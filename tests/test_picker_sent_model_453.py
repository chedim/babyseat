"""#453: after a seat restart or a page reload the chat's model picker showed
the session's model (claude-agent:haiku) but the turn went to the seat default
(qwen3) until the person picked the model again; and when the model list could
not be read at load (the seat restarting) the picker stayed empty.

Driven here through the chat element's own methods in node (membrane/ui/panel.js,
the custom element's prototype, with the DOM, the API and the clock stubbed):
the model a turn carries is the model the picker shows, and a failed list read
is tried again."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

HARNESS = r"""
let K;
global.HTMLElement = class {};
global.customElements = { define: (n, c) => { K = c; }, get: () => undefined };
global.document = { hidden: false };
const timers = [];
global.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
require(%(panel)r);

const ROWS = [{ id: "qwen3", owned_by: "membrane->ollama" },
              { id: "claude-agent:haiku", owned_by: "membrane->claude-agent" }];
let listFails = %(list_fails)d;
const sent = [];
const page = Object.create(K.prototype);
page._api = async (url, opts) => {
  if (url.endsWith("/config")) return { ok: true, data: { model: "membrane/qwen3" } };
  if (url.endsWith("/v1/models")) {
    if (listFails > 0) { listFails--; return { ok: false, status: 502, data: null }; }
    return { ok: true, data: { data: ROWS } };
  }
  if (url.endsWith("/session/s1")) return { ok: true, data: { id: "s1", model: { id: "claude-agent:haiku" } } };
  if (url.endsWith("/prompt_async")) { sent.push(JSON.parse(opts.body)); return { ok: true, data: {} }; }
  return { ok: false, data: null };
};
const sel = { innerHTML: "", value: "", _l: null,
  insertAdjacentHTML(_w, h) { this.innerHTML += h; },
  addEventListener(_e, fn) { this._l = fn; } };
page._modelSel = sel;
page._foot = { textContent: "" };
page._ta = { value: "" };
page._grow = () => {}; page._slash = async () => false; page._err = () => {};
page.dispatchEvent = () => {}; page._busy = () => true; page._resync = async () => {};
page._live = true; page._agent = "build"; page._chat = { rows: [] };
page._sid = "s1";
const send = async (text) => { page._ta.value = text; await page._submit(); return sent[sent.length - 1]; };
const model = (body) => (body && body.model ? body.model.modelID : "");

(async () => {
  const out = {};
  await page._loadModel();
  for (let i = 0; i < 20 && listFails >= 0 && timers.length; i++) {
    const t = timers.shift(); out.retryMs = t.ms; await t.fn(); }
  out.options = (page._models || []).slice();
  await page._loadSessionModel("s1");               // the reload's / restart's session read
  out.shown = sel.value;
  out.sentAfterReload = model(await send("hello"));
  sel.value = "";                                    // the person picks the seat default
  page._modelSel.value = "";
  page._pick = "";
  out.shownAfterDefault = sel.value;
  out.sentAfterDefault = model(await send("again"));
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
"""


def _run(list_fails=0):
    js = HARNESS % {"panel": str(UI / "panel.js"), "list_fails": list_fails}
    return json.loads(subprocess.check_output(["node", "-e", js], timeout=30))


@NODE
def test_after_a_reload_the_turn_goes_to_the_model_the_picker_shows():
    out = _run()
    assert out["shown"] == "claude-agent:haiku"
    assert out["sentAfterReload"] == out["shown"]


@NODE
def test_picking_the_seat_default_still_sends_the_seat_model():
    out = _run()
    assert out["shownAfterDefault"] == ""
    assert out["sentAfterDefault"] == "qwen3"


@NODE
def test_a_failed_model_list_read_at_load_is_tried_again():
    out = _run(list_fails=2)
    assert out["options"] == ["qwen3", "claude-agent:haiku"]
    assert out["retryMs"] == 5000
