"""#1055 (Memo, 2026-11-10): a browser JS run's error comes back with its name and message, then the stack --
Firefox and Safari put no message in Error.stack (Chrome does), so a thrown 'boom' or the run's timeout came back
as a bare stack."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js"


def _fn() -> str:
    src = PANEL.read_text()
    m = re.search(r"function jsError\(err\) \{.*?\n\}\n", src, re.S)
    assert m, "panel.js has no jsError(err)"
    return m.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.parametrize("stack,want", [
    ("definePanel/_jsRun/v</<@http://localhost:4097/ui/www/panel.js:3753:84", "Error: timeout after 15000 ms"),  # Firefox
    ("Error: timeout after 15000 ms\n    at panel.js:3753:84", "Error: timeout after 15000 ms"),                  # Chrome
])
def test_the_message_rides_with_the_stack(stack, want):
    js = _fn() + f"""
const e = new Error("timeout after 15000 ms"); e.stack = {json.dumps(stack)};
const out = jsError(e);
console.log(JSON.stringify(out));"""
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out.startswith(want), out
    assert out.count("timeout after 15000 ms") == 1, out
    assert "panel.js:3753:84" in out


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_thrown_non_error_is_shown_as_is():
    js = _fn() + "\nconsole.log(JSON.stringify(jsError('boom')));"
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out == "boom"


def test_the_run_uses_it():
    assert "error: jsError(err)" in PANEL.read_text()
