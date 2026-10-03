"""ENTITIES WHOSE SCRIPTS ERRORED speak to the field (2026-10-01, the
owner's ask): the script engine's runs -- success AND error -- become
field events (src starts 'script'), the entity doc persists
script_last_error, the banner renders the script-error announcement
kind-aware (it used to fall into the generic 'new entity:' lane), and
the subject entity's STATE moves toward the event sentence so errored
scripts are findable by similarity. Deterministic fakes only (the lab
covenant): sha256-seeded embedder, RAM MemBackend, isolated EntityStore.
"""
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder
from membrane.efield import EntityField
from membrane.efstore import MemBackend
from membrane.entities import EntityStore, QuantumEntity, CLASS_COMPONENT
from membrane.night import run_scripted_entities, run_entity_update_script
from membrane import entity_block as EB
from membrane import streams as ST
from membrane.capture import LazyEmbedder


NS = "membrane::"


def _world():
    """A real field (FakeEmbedder) + an isolated overlay store."""
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    est = EntityStore(path=None, shared=False)
    return f, est


def _seed_entity(est, eid, script="result = 1/0"):
    ent = QuantumEntity(eid=eid, cls=CLASS_COMPONENT,
                        properties={"nudge_script": script})
    est.upsert(ent)
    return ent


class TestScriptEvents(unittest.TestCase):
    def test_error_writes_field_event(self):
        """A failing nudge script: the field gains a session='' event with
        src starting 'script' naming the subject -- news-lane material
        (state_diff renders it, not this conversation's watermark)."""
        f, est = _world()
        _seed_entity(est, NS + "files:router.py")
        r = run_scripted_entities(f, est, force=True)
        self.assertEqual(r["errors"], 1)
        ev = next(d for _, d in f.b.scan("events") if d["stream"] == "discourse" and
                  (d.get("src") or "").startswith("script"))
        self.assertEqual(ev["session"], "")
        self.assertIn("errored", ev["text"])
        self.assertEqual(ev.get("subject"), "f:router.py")

    def test_error_moves_the_entity_state(self):
        """THE SIMILARITY HOOK: the subject entity's state exists after the
        error event -- the entity drifts toward its error sentence, so an
        ask near that text finds it."""
        f, est = _world()
        _seed_entity(est, NS + "files:router.py")
        run_scripted_entities(f, est, force=True)
        f.process()
        self.assertIsNotNone(f.get("f:router.py"))

    def test_state_diff_quotes_the_script_lane(self):
        """The read side state_diff was built for src='script'... events;
        now a writer exists -- the diff must quote the error, labeled by
        its script src, not the stream name."""
        f, est = _world()
        _seed_entity(est, NS + "files:router.py")
        run_scripted_entities(f, est, force=True)
        f.process()
        out = EB.state_diff(f, {"seq": 0, "conflicts": 0},
                            own_session_prefix="s:")
        self.assertIsNotNone(out)
        self.assertIn("script", out.lower())
        self.assertIn("errored", out)

    def test_last_error_persisted_then_cleared(self):
        """script_last_error lands on the doc at failure; a fixed script
        (edit clears it) leaves the doc clean."""
        f, est = _world()
        ent = _seed_entity(est, NS + "files:router.py")
        run_scripted_entities(f, est, force=True)
        self.assertTrue((est.docs[NS + "files:router.py"].properties or {})
                        .get("script_last_error"))
        ent.properties["nudge_script"] = "result = {'data': {'ok': True}}"
        est.upsert(ent)
        run_scripted_entities(f, est, force=True)
        p = est.docs[NS + "files:router.py"].properties or {}
        self.assertNotIn("script_last_error", p)
        self.assertIsNone(p.get("script_disabled"))

    def test_success_writes_an_event_citing_the_render(self):
        """A working nudge also speaks (its render is field news now --
        script_render finally has a consumer: the state diff)."""
        f, est = _world()
        _seed_entity(est, NS + "files:router.py",
                     script="result = {'render': 'healthy; 3 nodes'}")
        r = run_scripted_entities(f, est, force=True)
        self.assertEqual(r["errors"], 0)
        ev = next(d for _, d in f.b.scan("events") if
                  (d.get("src") or "").startswith("script"))
        self.assertIn("healthy; 3 nodes", ev["text"][:400])

    def test_trigger_path_reports_errors_too(self):
        """run_entity_update_script's error lane (the mention hook) tells
        the field the same way -- srv.efield is the writer, degrade-safe."""
        f, est = _world()
        ent = _seed_entity(est, NS + "files:router.py", script="")
        # the sandbox is lifted (test_run_lane pins it), so an os import
        # is NOT an error anymore -- pin the trigger-error lane with a
        # genuine failure instead
        ent.properties["mention_script"] = "result = 1/0"
        est.upsert(ent)
        srv = types.SimpleNamespace(efield=f, entity_store=est,
                                    membrane=None)
        run_entity_update_script(srv, ent, "mention")
        ev = next(d for _, d in f.b.scan("events") if
                  (d.get("src") or "").startswith("script"))
        self.assertIn("mention", ev["text"])

    def test_disabled_announces_once_still(self):
        """3 strikes -> script_disabled; THE COVENANT: the disable
        surfaces as an error-bubble ENTITY (a birth on the graph),
        not a banner push."""
        f, est = _world()
        _seed_entity(est, NS + "files:router.py")
        for _ in range(3):
            run_scripted_entities(f, est, force=True)
        p = est.docs[NS + "files:router.py"].properties or {}
        self.assertTrue(p.get("script_disabled"))
        kinds = [a["kind"] for a in est.take_announcements()]
        self.assertIn("new", kinds)          # the bubble's birth
        bubbles = [e for e in est.docs.values()
                   if str(e.eid).startswith("membrane::error:")]
        self.assertTrue(bubbles)

    def test_unresolved_embedder_degrades(self):
        """The seat's embedder still unresolved: the event is WRITTEN and
        stays pending; nothing raises (the 0616bb0 covenant)."""
        f, est = _world()
        _seed_entity(est, NS + "files:router.py")
        f.emb = LazyEmbedder(types.SimpleNamespace(embedder=None))
        try:
            run_scripted_entities(f, est, force=True)
        except Exception as e:
            self.fail(f"unresolved embedder must degrade, not raise: {e}")


class TestBannerKinds(unittest.TestCase):
    def test_script_error_renders_kind_aware(self):
        """The announcement's kind-aware line: 'script: <eid> <kind> ->'
        instead of the old mislabeled 'new entity: ... [Component]'."""
        line = OC_ANNOUNCE({"kind": "script-error", "eid": "membrane::f:x",
                            "name": "nudge", "summary": "ZeroDivision: 1/0",
                            "cls": "Component", "extra": ""})
        self.assertTrue(line.startswith("script:"))
        self.assertIn("nudge", line)
        self.assertIn("ZeroDivision", line)
        self.assertNotIn("new entity", line)

    def test_other_kinds_keep_their_lines(self):
        self.assertTrue(OC_ANNOUNCE(
            {"kind": "merged", "eid": "a", "extra": "into b",
             "name": "", "cls": "", "summary": ""}).startswith("new entity"))
        self.assertTrue(OC_ANNOUNCE(
            {"kind": "new", "eid": "b", "name": "n", "cls": "Component",
             "summary": "s", "extra": ""}).startswith("new entity"))


def OC_ANNOUNCE(a):
    """The factored banner renderer (opencode.announce_line) under its
    test-facing import."""
    from membrane.opencode import announce_line
    return announce_line(a)


if __name__ == "__main__":
    unittest.main()