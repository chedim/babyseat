"""#348: auto-chat goes; its ▶ button becomes the seat's pause (the nerve pause).

The button in membrane home's top bar drives `srv.paused` (lanes.nerve_paused),
the operator's stable point: ▶ while the seat runs, ⏸ while it is paused.
Pressing it pauses the acting lanes (the loops, the dreamer, the thinker's
ticks, the night pass, the autonode, the idle judges, speech, Blurp's own
cycle, the scripts' model calls) and pressing it again resumes them.

Pinned here:
  - GET / POST /membrane/seat/pause read and set the pause; the POST writes
    the same audit line as the typed `pause` / `resume` and notes the seat
    event; a body without a boolean `paused` is a 400, nothing changed;
  - the POST is behind the owner's door (an off-box caller needs the seat
    token), the GET is a read like its neighbours; HA's relay lets an admin's
    toggle through, stamped with the HA user;
  - the pause stays RAM-only: nothing is written but the audit line;
  - the typed `pause` / `resume` keep working;
  - the button: its icon, title, aria-label and aria-pressed from the seat's
    state, read on load and after every change, a refusal shown;
  - auto-chat is gone: the voice's machinery, the /session/<id>/auto route,
    the seat panel's `auto`, the boot's restore, the chat's ⟳ and its wiring.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

from membrane import lanes as L  # noqa: E402
from membrane import seat_panel as SP  # noqa: E402
from membrane import seatauth as SA  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import voice as V  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402
from tests.test_jwt_auth import _jwt_env  # noqa: E402
from tests.test_offbox_auth import _OffBox, _req  # noqa: E402

PAUSE = "/membrane/seat/pause"


def _seat(d):
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=None, shared=False)
    srv.paused = False
    srv.membrane = types.SimpleNamespace(audit_path=str(Path(d) / "audit.jsonl"))
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    return srv, r


def _audit(srv):
    p = Path(srv.membrane.audit_path)
    return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


def _events(srv):
    ent = srv.entity_store.docs.get(MEMBRANE_NS + "presence")
    return [e["kind"] for e in ((ent.properties or {}).get("events") or [])] if ent else []


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(self.tmp.name, "seat-token"),
                            MEMBRANE_DATA_DIR=self.tmp.name)
        self.env.start()
        os.environ.pop("MEMBRANE_NERVE_PAUSE", None)
        os.environ.pop("MEMBRANE_STANDALONE", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()


# ---- the seat side ----------------------------------------------------------------

class TestThePauseSwitch(_Env):
    def test_pause_and_resume_set_the_flag_audit_and_note_it(self):
        srv, _ = _seat(self.tmp.name)
        out = SP.set_pause(srv, True, by="operator")
        self.assertTrue(srv.paused)
        self.assertTrue(L.nerve_paused(srv))
        self.assertEqual(out["paused"], True)
        out = SP.set_pause(srv, False, by="ui:anna")
        self.assertFalse(srv.paused)
        self.assertEqual(out["paused"], False)
        ops = [(a["event"], a["op"]) for a in _audit(srv)]
        self.assertEqual(ops, [("nerve", "pause"), ("nerve", "resume")])
        self.assertEqual([a.get("by") for a in _audit(srv)], ["operator", "ui:anna"])
        self.assertEqual(_events(srv)[-2:], ["paused", "resumed"])

    def test_the_typed_pause_writes_the_same_line(self):
        srv, _ = _seat(self.tmp.name)
        L._pause_cmd(srv, "membrane: pause")
        self.assertTrue(L.nerve_paused(srv))
        SP.set_pause(srv, False)
        L._pause_cmd(srv, "resume")
        typed, button, typed2 = _audit(srv)
        self.assertEqual({k: typed[k] for k in ("event", "op")}, {"event": "nerve", "op": "pause"})
        self.assertEqual(set(button) - {"by"}, set(typed))           # the same line, plus who
        self.assertEqual(typed2["op"], "resume")
        self.assertFalse(L.nerve_paused(srv))

    def test_the_state_reads_the_seat(self):
        srv, _ = _seat(self.tmp.name)
        self.assertEqual(SP.pause_state(srv), {"paused": False, "enabled": True})
        srv.paused = True                                            # the typed pause, the battery
        self.assertEqual(SP.pause_state(srv)["paused"], True)
        os.environ["MEMBRANE_NERVE_PAUSE"] = "0"                     # the kill switch: a no-op pause
        try:
            self.assertEqual(SP.pause_state(srv), {"paused": False, "enabled": False})
        finally:
            os.environ.pop("MEMBRANE_NERVE_PAUSE", None)

    def test_it_stays_in_ram(self):
        srv, _ = _seat(self.tmp.name)
        before = set(os.listdir(self.tmp.name))
        SP.set_pause(srv, True)
        self.assertEqual(set(os.listdir(self.tmp.name)) - before, {"audit.jsonl"})
        fresh, _ = _seat(self.tmp.name)                              # a restart: a new seat object
        self.assertFalse(SP.pause_state(fresh)["paused"])

    def test_the_seat_control_pause_audits_too(self):
        srv, _ = _seat(self.tmp.name)
        SP.control(srv, {"paused": True}, by="operator")
        self.assertTrue(srv.paused)
        self.assertEqual([a["op"] for a in _audit(srv)], ["pause"])


# ---- the route ----------------------------------------------------------------------

def _serve(srv, r, offbox):
    handler = S.make_handler(hub=r.hub, sessions=r.sessions, messages=r.messages, config={}, runner=r)
    httpd = (_OffBox if offbox else ThreadingHTTPServer)(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class TestTheRoute(_Env):
    def setUp(self):
        super().setUp()
        self.srv, self.r = _seat(self.tmp.name)
        self.httpd = None

    def tearDown(self):
        if self.httpd is not None:
            self.httpd.shutdown()
        super().tearDown()

    def _go(self, method, body=None, offbox=False, headers=None):
        if self.httpd is None:
            self.httpd = _serve(self.srv, self.r, offbox)
        code, b, _ = _req(self.httpd.server_address[1], PAUSE, method, body, headers)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, b

    def test_get_reads_and_post_sets(self):
        self.assertEqual(self._go("GET"), (200, {"paused": False, "enabled": True}))
        code, out = self._go("POST", {"paused": True})
        self.assertEqual(code, 200)
        self.assertTrue(out["paused"])
        self.assertTrue(self.srv.paused)
        self.assertEqual(self._go("GET")[1]["paused"], True)
        self.assertEqual(_audit(self.srv)[-1]["op"], "pause")
        code, out = self._go("POST", {"paused": False})
        self.assertEqual((code, out["paused"], self.srv.paused), (200, False, False))
        self.assertEqual(_audit(self.srv)[-1]["op"], "resume")

    def test_a_bad_body_changes_nothing(self):
        for body in ({}, {"paused": "yes"}, {"paused": 1}, ["paused"]):
            code, out = self._go("POST", body)
            self.assertEqual(code, 400, body)
            self.assertIn("paused", str(out.get("error")))
        self.assertFalse(self.srv.paused)
        self.assertEqual(_audit(self.srv), [])

    def test_off_box_the_toggle_needs_the_seat_token(self):
        tok = SA.ensure()
        code, _ = self._go("POST", {"paused": True}, offbox=True)
        self.assertEqual(code, 401)
        self.assertFalse(self.srv.paused)
        self.assertEqual(self._go("GET", offbox=True)[0], 200)                 # a read stays open
        code, out = self._go("POST", {"paused": True}, offbox=True, headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual((code, out["paused"]), (200, True))
        self.assertTrue(self.srv.paused)

    def test_the_ha_relay_lets_an_admins_toggle_through(self):
        relay = (ROOT / "custom_components" / "membrane" / "relay.py").read_text()
        for name in ("ALLOWED_EXACT", "ADMIN_WRITE_EXACT", "PERSON_PATHS"):
            block = relay.split(name + " = (", 1)[1].split(")", 1)[0]
            self.assertIn('"membrane/seat/pause"', block, name)


# ---- auto-chat is gone ------------------------------------------------------------------

class TestAutoChatIsGone(_Env):
    def test_no_auto_chat_in_the_voice(self):
        for name in ("auto_set", "auto_state", "auto_on", "auto_next", "restore_state", "save_state",
                     "AUTO_MODES", "AUTO_PAUSE_S", "STATE_FILE", "_auto_after", "_last_words"):
            self.assertFalse(hasattr(V, name), name)

    def test_no_auto_in_the_seat_panel(self):
        srv, r = _seat(self.tmp.name)
        self.assertNotIn("auto", SP.CONTROLS)
        self.assertNotIn("auto", SP.state(srv))
        sid = r.sessions.create(title="t")["id"]
        out = SP.control(srv, {"session": sid, "auto": True})
        self.assertNotIn("auto", out["applied"])
        self.assertFalse(getattr(r, "_auto", None))

    def test_the_route_is_gone(self):
        srv, r = _seat(self.tmp.name)
        sid = r.sessions.create(title="t")["id"]
        httpd = _serve(srv, r, offbox=False)
        try:
            port = httpd.server_address[1]
            self.assertEqual(_req(port, f"/session/{sid}/auto", "POST", {"on": True})[0], 404)
            got = json.loads(_req(port, f"/session/{sid}/auto")[1] or b"{}")   # an unknown sub-path: the session row
            self.assertNotIn("rounds", got)
            self.assertNotIn("on", got)
        finally:
            httpd.shutdown()
        self.assertFalse(getattr(r, "_auto", None))

    def test_no_auto_chat_left_in_the_seat_code(self):
        server = (ROOT / "membrane" / "server.py").read_text()
        for s in ("auto_set", "auto_on", "auto_state", "_auto_aim", "autoTurn"):
            self.assertNotIn(s, server, s)
        self.assertNotIn("restore_state", (ROOT / "membrane" / "opencode.py").read_text())

    def test_no_auto_chat_left_in_the_ui(self):
        panel = (UI / "panel.js").read_text()
        for s in ("autobtn", "_toggleAuto", "_paintAuto", "autoSid", "membrane.auto", "/auto"):
            self.assertNotIn(s, panel, s)
        home = (UI / "home.js").read_text()
        for s in ("autobtn", "_toggleAuto", "_paintAuto", "autoTitle", "auto-chat"):
            self.assertNotIn(s, home, s)

    def test_membrane_only_stays_for_the_legacy_panel(self):
        self.assertIn("membraneOnly", (ROOT / "membrane" / "server.py").read_text())


# ---- the button (membrane home) ---------------------------------------------------------

def _js(body):
    js = "const M=require(%r); let out; %s; process.stdout.write(JSON.stringify(out))" % (str(UI / "home.js"), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


HARNESS = r"""
const H = require(%(home)r);
function el() {
  const attrs = {};
  return { textContent: "", title: "",
    setAttribute(k, v) { attrs[k] = String(v); }, getAttribute(k) { return k in attrs ? attrs[k] : null } };
}
const calls = [];
let seat = %(seat)s, refuse = %(refuse)s;
const get = async (p) => { calls.push(["GET", p]); return JSON.parse(JSON.stringify(seat)); };
const post = async (p, body) => {
  calls.push(["POST", p, body]);
  if (refuse) { const e = new Error(refuse); e.status = 401; throw e; }
  seat = Object.assign({}, seat, body); return JSON.parse(JSON.stringify(seat));
};
const btn = el();
const b = new H.PauseButton({ btn, get, post });
const look = () => ({ icon: btn.textContent, title: btn.title, label: btn.getAttribute("aria-label"),
                      pressed: btn.getAttribute("aria-pressed") });
(async () => {
  const out = await (async () => { %(script)s })();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ crash: String(e && e.stack || e) })); });
"""


def _run(script, seat=None, refuse=None):
    js = HARNESS % {"home": str(UI / "home.js"), "seat": json.dumps(seat or {"paused": False, "enabled": True}),
                    "refuse": json.dumps(refuse), "script": script}
    out = json.loads(subprocess.check_output(["node", "-e", js]))
    assert "crash" not in out, out.get("crash") if isinstance(out, dict) else out
    return out


@NODE
def test_the_view_running_and_paused():
    run, held = _js("out = [M.pauseView({paused: false}), M.pauseView({paused: true})]")
    assert run["icon"] == "▶" and held["icon"] == "⏸"
    assert run["pressed"] == "false" and held["pressed"] == "true"
    assert "running" in run["title"] and "click to pause" in run["title"]
    for lane in ("loops", "dreamer", "thinker", "night", "autonode", "judges", "speech", "restart"):
        assert lane in run["title"], lane
    assert "paused" in held["title"] and "click to resume" in held["title"]
    assert "pause" in run["label"] and "resume" in held["label"]
    for v in (run, held):
        assert "auto-chat" not in v["title"] and "LLM works on" not in v["title"]


@NODE
def test_the_view_before_the_seat_answers_and_with_the_switch_off():
    unknown, off = _js("out = [M.pauseView(null), M.pauseView({paused: false, enabled: false})]")
    assert unknown["icon"] == "▶" and "reading" in unknown["title"]
    assert "MEMBRANE_NERVE_PAUSE" in off["title"]


@NODE
def test_load_paints_the_seats_state():
    out = _run("await b.load(); return { look: look(), calls };", seat={"paused": True, "enabled": True})
    assert out["calls"] == [["GET", "/membrane/seat/pause"]]
    assert out["look"]["icon"] == "⏸" and out["look"]["pressed"] == "true"
    assert "click to resume" in out["look"]["title"] and out["look"]["label"]


@NODE
def test_a_click_toggles_and_repaints_from_the_reply():
    out = _run("""
      await b.load();
      await b.click(); const one = look();
      await b.click(); const two = look();
      return { one, two, calls };""")
    posts = [c for c in out["calls"] if c[0] == "POST"]
    assert posts == [["POST", "/membrane/seat/pause", {"paused": True}], ["POST", "/membrane/seat/pause", {"paused": False}]]
    assert out["one"]["icon"] == "⏸" and out["one"]["pressed"] == "true"
    assert out["two"]["icon"] == "▶" and out["two"]["pressed"] == "false"


@NODE
def test_a_click_before_the_state_is_known_reads_first():
    out = _run("await b.click(); return { look: look(), calls };", seat={"paused": True, "enabled": True})
    assert out["calls"][0] == ["GET", "/membrane/seat/pause"]
    assert out["calls"][1] == ["POST", "/membrane/seat/pause", {"paused": False}]   # resume, not a second pause
    assert out["look"]["icon"] == "▶"


@NODE
def test_a_refusal_keeps_the_state_and_says_why():
    out = _run("await b.load(); await b.click(); return { look: look() };",
               refuse="the seat token is needed to pause the seat")
    assert out["look"]["icon"] == "▶" and out["look"]["pressed"] == "false"
    assert "the seat token is needed" in out["look"]["title"]


def test_the_top_bar_button():
    src = (UI / "home.js").read_text()
    top = re.search(r'<div class="top glass">(.*?)</div>', src, re.S).group(1)
    m = re.search(r'<button class="pausebtn"[^>]*>', top)
    assert m, "the pause button sits in the top bar"
    assert 'aria-pressed="false"' in m.group(0) and "aria-label=" in m.group(0) and "title=" in m.group(0)
    assert "new PauseButton(" in src
