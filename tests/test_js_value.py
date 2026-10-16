"""THE JOB'S VALUE COMES BACK (issue #153): presence.exec ran the code in the
tab -- its effects showed on the page -- but the seat only ever heard
'=> undefined (the job returned no value)'. The tab wrapped the code as an
async function BODY, so a bare final expression ('1+1') was a statement
whose value fell on the floor. The tab now hands back what a console would:
the final expression's value, or an explicit return; a Promise is awaited;
a value JSON can't carry is still rendered readably.

The tab's compile + render are pure functions in panel.js (both copies: the
standalone UI and the HA integration's), driven here through node with the
same job shape the seat publishes."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PANELS = [ROOT / "membrane" / "ui" / "panel.js",
          ROOT / "integrations" / "homeassistant" / "custom_components" / "ai_terminal" / "www" / "panel.js"]


def _tab(panel: Path, code: str):
    """Run one job the way the tab does; the result body it would POST."""
    js = (f"const L = require({json.dumps(str(panel))});\n"
          f"(async () => {{\n"
          f"  let out;\n"
          f"  try {{ const fn = L.jsCompile({json.dumps(code)}, ['console', 'hass', 'panel', 'home']);\n"
          f"         const quiet = {{log() {{}}, info() {{}}, warn() {{}}, error() {{}}}};\n"
          f"         const v = await fn(quiet, {{states: {{}}}}, null, null);\n"
          f"         out = {{ok: true, value: v === undefined ? undefined : L.jsShow(v)}}; }}\n"
          f"  catch (e) {{ out = {{ok: false, error: String(e)}}; }}\n"
          f"  process.stdout.write(JSON.stringify(out));\n"
          f"}})();")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestTheJobsValueComesBack(unittest.TestCase):
    CASES = [
        ("1+1", "2"),                                              # the issue's repro: a bare expression
        ("return 1+1", "2"),                                       # an explicit return
        ("return 1+1;", "2"),
        ("const a = 2;\nconst b = 3;\na * b", "6"),                # the final expression of a script
        ("let s = 0; for (let i = 0; i < 4; i++) { s += i; }\ns;", "6"),
        ("if (true) { 'x' }\n'last'", "last"),
        ("Promise.resolve(41).then((x) => x + 1)", "42"),          # a Promise is awaited
        ("await new Promise((r) => setTimeout(() => r('late'), 10))", "late"),
        ("const v = await Promise.resolve([1, 2]);\nv.length", "2"),
        ("({a: 1, b: [2]})", '{"a":1,"b":[2]}'),                   # JSON-able: as JSON
        ("'hi'", "hi"),                                            # a string: as itself
        ("null", "null"),
        ("1+1 // a comment at the end", "2"),
    ]

    def test_each_panel_hands_back_the_value(self):
        for panel in PANELS:
            for code, want in self.CASES:
                with self.subTest(panel=panel.parent.name, code=code):
                    out = _tab(panel, code)
                    self.assertTrue(out.get("ok"), out)
                    self.assertEqual(out.get("value"), want)

    def test_no_value_is_still_undefined(self):
        for panel in PANELS:
            for code in ("let x = 1;", "console.log('only a log')", "return"):
                with self.subTest(panel=panel.parent.name, code=code):
                    out = _tab(panel, code)
                    self.assertTrue(out.get("ok"), out)
                    self.assertNotIn("value", out)

    def test_a_value_json_cannot_carry_is_rendered_readably(self):
        for panel in PANELS:
            for code, has in (("function add(a, b) { return a + b }\nadd", "add"),
                              ("10n ** 3n", "1000"),
                              ("Symbol('tag')", "Symbol(tag)"),
                              ("const o = {name: 'loop'}; o.self = o; o", "loop"),
                              ("new Map([['k', 1]])", "k"),
                              ("new Error('boom')", "boom")):
                with self.subTest(panel=panel.parent.name, code=code):
                    out = _tab(panel, code)
                    self.assertTrue(out.get("ok"), out)
                    self.assertIsInstance(out.get("value"), str)
                    self.assertIn(has, out["value"])

    def test_a_syntax_error_still_fails_honestly(self):
        for panel in PANELS:
            with self.subTest(panel=panel.parent.name):
                out = _tab(panel, "this is ( not js")
                self.assertFalse(out.get("ok"))
                self.assertIn("SyntaxError", out.get("error", ""))

    def test_the_tab_uses_the_compile(self):
        """The job runner in the element goes through jsCompile/jsShow, not a
        bare AsyncFunction body (the #153 break)."""
        for panel in PANELS:
            src = panel.read_text(encoding="utf-8")
            body = src[src.index("async _jsRun(job)"):]
            body = body[:body.index("\n    }\n")]
            self.assertIn("jsCompile(", body)
            self.assertIn("jsShow(", body)


class TestTheSeatRendersTheTabsValue(unittest.TestCase):
    """The seat side of the same contract: the body the tab POSTs ->
    the receipt."""
    def test_a_value_comes_back_in_the_receipt(self):
        import threading
        import types
        from membrane import jsexec as JS
        from membrane import night as N
        from membrane.entities import EntityStore, MEMBRANE_NS
        srv = types.SimpleNamespace(entity_store=EntityStore(path=None, shared=False))

        def publish(ev):
            if ev.get("type") == "membrane.jsexec":
                body = {"id": ev["job"]["id"], "ok": True, "value": "2", "logs": [], "person": "D"}
                threading.Timer(0.05, lambda: JS.result(srv, body)).start()
        srv._loop_runner = types.SimpleNamespace(hub=types.SimpleNamespace(publish=publish))
        JS.bind(srv)
        JS.register(srv, "tab_a", "ses_1", "D")
        r = N.method_call(srv, srv.entity_store, None, None, MEMBRANE_NS + "presence", "exec", {"code": "1+1"})
        self.assertEqual(r.get("rc"), 0, r)
        self.assertIn("=> 2", str(r.get("head")))
        self.assertNotIn("returned no value", str(r.get("head")))


if __name__ == "__main__":
    unittest.main()
