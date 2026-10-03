"""THE AUTONODE (the trigger layer, the operator's 2026-10-01 design + the
mid-plan corrections): a GENERIC trigger bus -- watchers are ENTITIES
(model-authored via entity_edit), the engine stays
source-agnostic (file/mqtt/signal all land as the same Trigger), watch
scripts may BLOCK and run in parallel under a bounded pool (<=4 at
once; an overrun strikes the 3-strikes lane but can't be killed --
threads aren't), the queue drop-oldest, and the field hears ONE voice
per entity per 10s window."""

import tempfile
import threading
import time
import unittest
from pathlib import Path

import membrane.autonode as AN
from membrane.entities import (CLASS_TOOL, EntityStore, MEMBRANE_NS,
                               QuantumEntity)
from membrane.night import (_WATCH_TELL_TS, apply_entity_update,
                            run_entity_update_script)

from tests.test_entity_scripts import _Srv


def _watcher(est, eid="tool:w1", script="result = {'render': 'saw it'}",
             watch=None):
    ent = QuantumEntity(eid=f"{MEMBRANE_NS}{eid}", cls=CLASS_TOOL,
                        properties={"name": eid.split(":")[-1]})
    if script is not None:
        ent.properties["watch_script"] = script
    if watch is not None:
        ent.properties["watch"] = watch
    est.upsert(ent)
    return est.docs[f"{MEMBRANE_NS}{eid}"]


class _CapField:
    def __init__(self):
        self.calls = []

    def write_events(self, evs, session=None, **kw):
        self.calls.append((list(evs), session))
        return True

    def process(self, limit=8):
        return 0


# ---- 1. the file scanner ----------------------------------------------------

class TestFileScanner(unittest.TestCase):
    def test_detects_created_changed_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            sc = AN.FileTreeScanner(Path(d))
            self.assertEqual(sc.scan(), [])       # primed: no flood at boot
            f = Path(d) / "a.py"
            f.write_text("x = 1")
            ts = sc.scan()
            self.assertEqual([(t.kind, t.payload["event"],
                               t.payload["path"]) for t in ts],
                             [("file", "created", "a.py")])
            f.write_text("x = 2")
            ts = sc.scan()
            self.assertEqual([(t.kind, t.payload["event"]) for t in ts],
                             [("file", "changed")])
            f.unlink()
            ts = sc.scan()
            self.assertEqual([(t.kind, t.payload["event"]) for t in ts],
                             [("file", "deleted")])

    def test_skip_dirs_and_no_reemit(self):
        with tempfile.TemporaryDirectory() as d:
            sc = AN.FileTreeScanner(Path(d))
            sc.scan()
            (Path(d) / ".git").mkdir()
            (Path(d) / ".git" / "head").write_text("junk")
            (Path(d) / "keep.txt").write_text("k")
            ts = sc.scan()
            self.assertEqual([t.payload["path"] for t in ts], ["keep.txt"])
            self.assertEqual(sc.scan(), [])       # no change, no re-emit
            self.assertEqual(sc.scan(), [])

    def test_a_mtime_only_touch_still_fires(self):
        """(mtime_ns, size) fingerprints: a touch with identical size is
        a change -- the metadata WAS the ask."""
        with tempfile.TemporaryDirectory() as d:
            sc = AN.FileTreeScanner(Path(d))
            sc.scan()
            f = Path(d) / "t.py"
            f.write_text("x")
            sc.scan()
            time.sleep(0.02)
            f.write_text("x")                     # same size, new mtime
            got = sc.scan()
            self.assertEqual([g.payload["event"] for g in got], ["changed"])


# ---- 2. the watcher selector ------------------------------------------------

class TestWatcher(unittest.TestCase):
    def test_kind_is_always_required(self):
        t = AN.Trigger("signal", {"name": "SIGUSR1"})
        self.assertFalse(AN.watcher_matches({}, t))   # no watch -> default
        t2 = AN.Trigger("file", {"path": "a.py", "event": "created"})
        self.assertTrue(AN.watcher_matches({}, t2))   # file is the default
        # kind mismatch: the file-defaulted watcher ignores a signal:
        self.assertFalse(AN.watcher_matches(
            {"watch": {"kind": "file"}}, t))
        # the named-match form works:
        self.assertTrue(AN.watcher_matches(
            {"watch": {"kind": "signal", "name": "SIGUSR1"}}, t))

    def test_glob_filters_paths(self):
        t = AN.Trigger("file", {"path": "src/router.py", "event": "changed"})
        self.assertTrue(AN.watcher_matches(
            {"watch": {"kind": "file", "glob": "*.py"}}, t))
        self.assertFalse(AN.watcher_matches(
            {"watch": {"kind": "file", "glob": "*.md"}}, t))
        self.assertTrue(AN.watcher_matches({"watch": {"kind": "file"}}, t))

    def test_mqtt_kind_is_honestly_retired(self):
        """THE COVENANT (2026-10-03): mqtt is GONE — a watcher declaring
        the kind gets an HONEST refusal stamped (script_disabled + the
        reason on the doc), no subscription ever happens."""
        srv = _Srv()
        est = srv.entity_store
        _watcher(est, "tool:w1", watch={"kind": "mqtt", "topic": "boat/"})
        an = AN.Autonode(srv, est, root=None, bus=None)
        an.sync_subscriptions()
        p = est.docs[f"{MEMBRANE_NS}tool:w1"].properties or {}
        self.assertTrue(p.get("script_disabled"))
        self.assertIn("retired", str(p.get("script_last_error")))

    def test_signal_name_filter(self):
        self.assertTrue(AN.watcher_matches(
            {"watch": {"kind": "signal", "name": "SIGUSR1"}},
            AN.Trigger("signal", {"name": "SIGUSR1"})))
        self.assertFalse(AN.watcher_matches(
            {"watch": {"kind": "signal", "name": "SIGHUP"}},
            AN.Trigger("signal", {"name": "SIGUSR1"})))

    def test_disabled_watchers_never_match(self):
        self.assertFalse(AN.watcher_matches(
            {"watch": {"kind": "file"}, "script_disabled": True},
            AN.Trigger("file", {"path": "a.py", "event": "created"})))


# ---- 3. the bounded pool ------------------------------------------------

class TestBoundedPool(unittest.TestCase):
    def _anon(self, fire_timeout=2.0, backlog=64):
        srv = _Srv()
        return srv, srv.entity_store, AN.Autonode(
            srv, srv.entity_store, root=None, bus=None,
            fire_timeout=fire_timeout, backlog=backlog)

    def test_a_fire_applies_the_script_result_through_the_engine(self):
        srv, est, an = self._anon()
        _watcher(est, "tool:w1",
                 script="result = {'render': 'saw a file', "
                        "'patch': {'script_data': {'last_seen': 'now'}}}",
                 watch={"kind": "file"})
        an.dispatch(AN.Trigger("file", {"path": "a.py",
                                        "event": "created"}))
        an.pump()
        self.assertTrue(an.join_fires(timeout=5))
        ent = est.docs[f"{MEMBRANE_NS}tool:w1"]
        self.assertEqual(ent.properties.get("script_render"), "saw a file")
        self.assertEqual((ent.properties.get("script_data") or {})
                         .get("last_seen"), "now")   # the whitelist lane
        self.assertEqual(an.active, 0)   # the pool drained

    def test_blocking_scripts_dont_wedge_and_slots_are_respected(self):
        srv, est, an = self._anon()
        holds = [threading.Event() for _ in range(3)]
        for i in range(3):
            _watcher(est, f"tool:block{i}",
                     script="state['extra']['_hold'].wait(30)\nresult = {}",
                     watch={"kind": "file", "glob": f"b{i}.py"})
        fired = threading.Event()
        fast_hold = threading.Event()
        _watcher(est, "tool:fast",
                 script=("state['extra']['_fired'].set()\n"
                         "state['extra']['_hold'].wait(30)\n"
                         "result = {'render': 'fast ok'}"),
                 watch={"kind": "file", "glob": "fast*.py"})
        for i in range(3):
            an.dispatch(AN.Trigger("file", {"path": f"b{i}.py",
                                            "event": "created",
                                            "_hold": holds[i]}))
        an.pump()
        self.assertEqual(an.active, 3)   # three wedge-holding fires run
        an.dispatch(AN.Trigger("file", {"path": "fast.py",
                                        "event": "created",
                                        "_fired": fired,
                                        "_hold": fast_hold}))
        an.pump()
        self.assertTrue(fired.wait(5.0))    # dispatch PROGRESSED, not wedged
        self.assertEqual(an.active, 4)   # THE CAP: four scripts at once
        # beyond the cap the trigger waits in line, it is not lost
        an.dispatch(AN.Trigger("file", {"path": "b9.py", "event": "created",
                                        "_hold": threading.Event()}))
        an.pump()
        self.assertEqual(an.active, 4)
        self.assertEqual(an.backlog_size(), 1)
        for held in holds:
            held.set()
        fast_hold.set()
        self.assertTrue(an.join_fires(timeout=5))
        # the blocked scripts RAN CLEAN -- the wait was the script's own
        self.assertEqual(int(est.docs[f"{MEMBRANE_NS}tool:block0"]
                             .properties.get("script_failures") or 0), 0)

    def test_an_overrun_strikes_not_kills(self):
        srv, est, an = self._anon(fire_timeout=0.1)
        _watcher(est, "tool:slow",
                 script="import time as _t\n_t.sleep(0.6)\n"
                        "result = {'render': 'finally'}",
                 watch={"kind": "file"})
        _WATCH_TELL_TS.clear()             # fresh window for the tell cap
        an.dispatch(AN.Trigger("file", {"path": "s.py",
                                        "event": "created"}))
        an.pump()
        time.sleep(0.25)                   # past the budget, script alive
        an.pump()                          # the reap: one strike
        ent = est.docs[f"{MEMBRANE_NS}tool:slow"]
        self.assertEqual(int(ent.properties.get("script_failures") or 0), 1)
        self.assertEqual(ent.properties.get("script_last_error"),
                         "watch-fire overrun (budget 0.1s) -- the thread "
                         "cannot be killed, the result still applies")
        self.assertNotIn("script_disabled", ent.properties)
        self.assertTrue(an.join_fires(timeout=3))
        self.assertEqual(ent.properties.get("script_render"), "finally")

    def test_three_overruns_disable_the_watcher(self):
        srv, est, an = self._anon(fire_timeout=0.1)
        hold = threading.Event()
        _watcher(est, "tool:stub",
                 script="state['extra']['_hold'].wait(30)\nresult = {}",
                 watch={"kind": "file"})
        for _ in range(3):
            an.dispatch(AN.Trigger("file", {"path": "x.py",
                                            "event": "created",
                                            "_hold": hold}))
            ent = est.docs[f"{MEMBRANE_NS}tool:stub"]
            ent.properties.pop("script_cooldowns", None)
            an.pump()
            time.sleep(0.2)                # past the budget
            an.pump()                      # reap the strike
        ent = est.docs[f"{MEMBRANE_NS}tool:stub"]
        self.assertTrue(ent.properties.get("script_disabled"))
        hold.set()                         # don't wedge the executor
        self.assertTrue(an.join_fires(timeout=5))

    def test_backlog_drop_oldest(self):
        with tempfile.TemporaryDirectory() as d:
            srv = _Srv()
            an = AN.Autonode(srv, srv.entity_store, root=None, backlog=8)
            for i in range(100):
                an.dispatch(AN.Trigger("file", {"path": f"n{i}.py",
                                                "event": "created"}))
            self.assertEqual(an.q.qsize(), 8)
            got = an.q.drain()
            self.assertEqual([t.payload["path"] for t in got],
                             [f"n{i}.py" for i in range(92, 100)])

    def test_the_scanner_rides_pump(self):
        srv = _Srv()
        self.assertTrue(AN.seed_filetree_entity(srv.entity_store))
        with tempfile.TemporaryDirectory() as d:
            an = AN.Autonode(srv, srv.entity_store, root=Path(d))
            an.scanner.scan()              # prime the baseline
            (Path(d) / "x.py").write_text("x")
            an._last_scan = 0.0            # force the tick due
            an.pump()                      # scanner -> queue -> fire
            self.assertEqual(an.backlog_size(), 0)
            self.assertTrue(an.join_fires(timeout=5))
            # end to end: the watcher SAW the file, trigger to apply
            render = (srv.entity_store
                      .docs[f"{MEMBRANE_NS}tool:filetree"]
                      .properties.get("script_render") or "")
            self.assertIn("created", render)
            self.assertIn("x.py", render)


# ---- 4. the edit surface ------------------------------------------------

class TestEditScript(unittest.TestCase):
    def test_edit_stores_watch_script_and_watch(self):
        est = _Srv().entity_store
        _watcher(est, "tool:w0", script=None)
        res = est.edit("tool:w0", watch_script="result = {'render': 'x'}",
                       watch={"kind": "file", "glob": "*.py"})
        self.assertTrue(res["ok"])
        ent = est.docs[f"{MEMBRANE_NS}tool:w0"]
        self.assertIn("watch_script", ent.properties)
        self.assertEqual(ent.properties["watch"]["kind"], "file")

    def test_watch_rewrite_clears_the_strikes(self):
        est = _Srv().entity_store
        ent = _watcher(est, "tool:w0", script="result = {}")
        ent.properties["script_failures"] = 2
        ent.properties["script_disabled"] = True
        res = est.edit("tool:w0", watch_script="result = {'render': 'new'}")
        self.assertTrue(res["ok"])
        ent = est.docs[f"{MEMBRANE_NS}tool:w0"]
        self.assertNotIn("script_disabled", ent.properties)
        self.assertNotIn("script_failures", ent.properties)

    def test_publish_strips_the_watch_script(self):
        est = _Srv().entity_store
        _watcher(est, "tool:w0", script="result = {'render': 'watching'}",
                 watch={"kind": "file"})
        ent = est.docs[f"{MEMBRANE_NS}tool:w0"]
        ent.properties["script_render"] = "watching"   # a fire's render
        stripped = EntityStore._strip_code(dict(ent.properties))
        self.assertNotIn("watch_script", stripped)
        self.assertEqual(stripped.get("script_render"), "watching")
        self.assertIn("watch", stripped)


# ---- 5. the seeded filetree ------------------------------------------------

class TestSeedFiletree(unittest.TestCase):
    def test_idempotent_and_template(self):
        est = _Srv().entity_store
        self.assertTrue(AN.seed_filetree_entity(est))
        eid = f"{MEMBRANE_NS}tool:filetree"
        doc = est.docs[eid]
        self.assertEqual(doc.cls, CLASS_TOOL)
        self.assertEqual(doc.properties["watch"]["kind"], "file")
        self.assertFalse(AN.seed_filetree_entity(est))   # once only
        est.edit("tool:filetree", watch_script="result = {'render': 'mine'}")
        AN.seed_filetree_entity(est)
        self.assertEqual(est.docs[eid].properties.get("watch_script"),
                         "result = {'render': 'mine'}")   # rewrite survives

    def test_the_template_script_reports_a_file_trigger(self):
        est = _Srv().entity_store
        AN.seed_filetree_entity(est)
        from membrane.night import run_entity_script
        ent = est.docs[f"{MEMBRANE_NS}tool:filetree"]
        st = {"trigger": "watch", "properties": {}, "data": {},
              "now": time.time(),
              "extra": {"payload": {"path": "src/a.py",
                                    "event": "created"}}}
        out = run_entity_script(ent.properties["watch_script"], st)
        self.assertIn("created", out.get("render", ""))
        self.assertIn("src/a.py", out.get("render", ""))
        self.assertIn("last_change", out.get("patch", {}))

    def test_the_template_script_absorbs_other_kinds(self):
        est = _Srv().entity_store
        self.assertTrue(AN.seed_filetree_entity(est))
        from membrane.night import run_entity_script
        ent = est.docs[f"{MEMBRANE_NS}tool:filetree"]
        st = {"trigger": "watch", "properties": {}, "data": {},
              "now": time.time(),
              "extra": {"payload": {"topic": "boat/engines/rpm",
                                    "payload": {"rpm": 900}}}}
        out = run_entity_script(ent.properties["watch_script"], st)
        self.assertIn("boat/engines", out.get("render", ""))

    def test_the_template_script_tolerates_an_empty_trigger(self):
        est = _Srv().entity_store
        self.assertTrue(AN.seed_filetree_entity(est))
        from membrane.night import run_entity_script
        ent = est.docs[f"{MEMBRANE_NS}tool:filetree"]
        st = {"trigger": "watch", "properties": {}, "data": {},
              "now": time.time(), "extra": {}}
        out = run_entity_script(ent.properties["watch_script"], st)
        self.assertNotIn("error", out)


# ---- 6. the field voice ------------------------------------------------

class TestFieldVoice(unittest.TestCase):
    def _anon(self):
        srv = _Srv()
        srv.efield = _CapField()
        return srv, srv.entity_store, AN.Autonode(srv, srv.entity_store,
                                                  root=None)

    def test_one_tell_per_fire_and_the_window(self):
        srv, est, an = self._anon()
        cap = srv.efield
        _WATCH_TELL_TS.clear()
        _watcher(est, "tool:w1", watch={"kind": "file"})
        an.dispatch(AN.Trigger("file", {"path": "a.py",
                                        "event": "created"}))
        an.pump()
        self.assertTrue(an.join_fires(timeout=5))
        self.assertEqual(len(cap.calls), 1)     # ONE voice per fire
        (evs, sess) = cap.calls[0]
        self.assertEqual(sess, "")
        self.assertEqual(evs[0].src, "script:watch")
        # window closed, cooldown cleared -> the applied tell repeats
        time.sleep(0.05)
        ent = est.docs[f"{MEMBRANE_NS}tool:w1"]
        ent.properties.pop("script_cooldowns", None)
        _WATCH_TELL_TS.pop(f"{MEMBRANE_NS}tool:w1", None)
        an.dispatch(AN.Trigger("file", {"path": "b.py",
                                        "event": "changed"}))
        an.pump()
        self.assertTrue(an.join_fires(timeout=5))
        self.assertEqual(len(cap.calls), 2)

    def test_a_second_fire_inside_the_window_is_silent_to_the_field(self):
        srv, est, an = self._anon()
        cap = srv.efield
        _WATCH_TELL_TS.clear()
        _watcher(est, "tool:w1", watch={"kind": "file"})
        an.dispatch(AN.Trigger("file", {"path": "a.py",
                                        "event": "created"}))
        an.pump()
        an.join_fires(timeout=5)
        n = len(cap.calls)
        ent = est.docs[f"{MEMBRANE_NS}tool:w1"]
        ent.properties.pop("script_cooldowns", None)   # script CAN run
        an.dispatch(AN.Trigger("file", {"path": "b.py",
                                        "event": "changed"}))
        an.pump()
        self.assertTrue(an.join_fires(timeout=5))
        self.assertEqual(ent.properties.get("script_render"), "saw it")
        self.assertEqual(len(cap.calls), n)     # but the field heard one

    def test_no_field_never_raises(self):
        srv = _Srv()
        est = srv.entity_store
        _watcher(est, "tool:w1", watch={"kind": "file"})
        an = AN.Autonode(srv, est, root=None)
        an.dispatch(AN.Trigger("file", {"path": "a.py",
                                        "event": "created"}))
        an.pump()
        self.assertTrue(an.join_fires(timeout=5))
        self.assertEqual(
            est.docs[f"{MEMBRANE_NS}tool:w1"].properties.get("script_render"),
            "saw it")


# ---- 7. the mqtt lane: RETIRED (2026-10-03) ------------

# ---- 8. the night dispatch ---------------------------------------------


class TestNightDispatch(unittest.TestCase):
    def test_watch_trigger_picks_the_watch_script(self):
        srv = _Srv()
        est = srv.entity_store
        ent = _watcher(est, "tool:w1",
                       script="result = {'render': 'wired: ' + str("
                              "state['extra'].get('payload'))}")
        ent.properties.pop("script_cooldowns", None)
        out = run_entity_update_script(
            srv, ent, "watch",
            extra_state={"payload": {"path": "a.py", "event": "changed"}})
        self.assertEqual(out.get("render"),
                         "wired: {'path': 'a.py', 'event': 'changed'}")

    def test_the_watch_error_rides_the_strikes_lane(self):
        srv = _Srv()
        est = srv.entity_store
        ent = _watcher(est, "tool:w1", script="raise ValueError('nope')")
        out = run_entity_update_script(srv, ent, "watch", extra_state={})
        self.assertIn("error", out)
        self.assertIn("ValueError: nope", out["error"])
        ent = est.docs[f"{MEMBRANE_NS}tool:w1"]
        self.assertEqual(int(ent.properties.get("script_failures") or 0), 1)
        self.assertEqual(ent.properties.get("script_last_error"),
                         "ValueError: nope")

    def test_the_apply_lands_and_tells(self):
        """THE WHITELIST (the covenant): a watcher's arbitrary prop
        ('n') is REFUSED (counted in patch_refused); the render
        applies. The apply lands 1 (the render), the refusal visible."""
        srv = _Srv()
        srv.membrane = None
        _srv_store = srv.entity_store
        ent = _watcher(_srv_store, "tool:w1",
                       script="result = {'render': 'r', 'patch': {'n': 1}}")
        ent.properties.pop("script_cooldowns", None)
        out = apply_entity_update(srv, ent,
                                  {"render": "r", "patch": {"n": 1}},
                                  "watch", silent=False)
        self.assertEqual(out["applied"], 1)
        self.assertEqual(out.get("patch_refused"), ["n"])
        self.assertIsNone(_srv_store.docs[f"{MEMBRANE_NS}tool:w1"]
                          .properties.get("n"))

    def test_dispatch_does_not_match_non_watchers(self):
        srv = _Srv()
        est = srv.entity_store
        # an entity with tick_script but no watch_script never fires
        ent = _watcher(est, "tool:w1", script=None)
        ent.properties["tick_script"] = "result = {'render': 'tick'}"
        est.upsert(ent)
        cap = _CapField()
        srv.efield = cap
        an = AN.Autonode(srv, est, root=None)
        an.dispatch(AN.Trigger("file", {"path": "a.py",
                                        "event": "created"}))
        an.pump()
        self.assertEqual(an.active, 0)
        self.assertEqual(
            est.docs[f"{MEMBRANE_NS}tool:w1"].properties.get("script_render"),
            None)

class TestWatchCooldown(unittest.TestCase):
    """THE WATCH COOLDOWN (its own, gentler default): a 60s cooldown
    (the mention default) would make a watcher a per-minute organ --
    dead for the thing it exists for. The watch trigger defaults to 5s
    (downward-overridable per entity via script_cooldown_s, the same
    min() contract), and the autonode does NOT treat the gate's
    {'error': 'cooldown'} as a failure print (a normal gate outcome,
    not a malfunction)."""

    def _watch_ent(self, srv, script="result = {'render': 'saw it'}"):
        est = srv.entity_store
        _watcher(est, "tool:w1", script)
        return est.docs[f"{MEMBRANE_NS}tool:w1"]

    def test_watch_cooldown_default_is_5s(self):
        srv = _Srv()
        ent = self._watch_ent(srv)
        first = run_entity_update_script(srv, ent, "watch",
                                        extra_state={"payload": {}})
        self.assertNotIn("error", first)
        second = run_entity_update_script(srv, ent, "watch",
                                          extra_state={"payload": {}})
        self.assertEqual(second.get("error"), "cooldown")
        # past 5s the gate opens again
        ent.properties["script_cooldowns"]["watch"] = time.time() - 5.1
        third = run_entity_update_script(srv, ent, "watch",
                                         extra_state={"payload": {}})
        self.assertNotIn("error", third)

    def test_watch_cooldown_overridable_downward(self):
        srv = _Srv()
        ent = self._watch_ent(srv)
        ent.properties["script_cooldown_s"] = 1.0
        self.assertTrue(run_entity_update_script(
            srv, ent, "watch", extra_state={}))
        ent.properties["script_cooldowns"]["watch"] = time.time() - 1.5
        self.assertNotIn("error", run_entity_update_script(
            srv, ent, "watch", extra_state={}))

    def test_the_reap_lane_does_not_call_cooldown_a_failure(self):
        # the design pin: _reap's failure print skips gate refusals -- a
        # cooldown fire is a NORMAL outcome (the smoke showed it spamming
        # stderr as "fire ...: cooldown" once a scan tick)
        import inspect
        src = inspect.getsource(AN.Autonode._reap)
        self.assertIn('"cooldown"', src)


class TestOwnFileNoise(unittest.TestCase):
    """THE FEEDBACK GUARD (found in the boot smoke): the seat's own state
    files churn every apply (entities.json debounces, serve-*.json
    stamps) and the watcher watching them reports its own reporting --
    a 5s-cadence self-feedback hum. The scanner never emits the seat's
    own runtime files (the .membrane/ dot-dir is already skipped)."""

    def test_the_seats_own_state_files_never_fire(self):
        import tempfile as tf
        with tf.TemporaryDirectory() as td:
            sc = AN.FileTreeScanner(Path(td))
            Path(td, "real.py").write_text("x")
            self.assertEqual(sc.scan(), [])       # priming scan
            # the seat's own files churn AFTER priming (real churn shape)
            for n in ("entities.json", "field.json",
                      "serve-auth.json", "serve-msgs.json",
                      "field.json.seed.json", "serve-absorb.json"):
                Path(td, n).write_text("x")
            Path(td, "real.py").write_text("y")   # the ONLY real change
            ts = sc.scan()
            self.assertEqual([t.payload["path"] for t in ts], ["real.py"])
            self.assertEqual(ts[0].payload["event"], "changed")
