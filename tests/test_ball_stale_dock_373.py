"""#373: after Roll it back in the ball's toast (#331) the dock kept showing the
ball on the person's side although the roll had landed. A dock read already in
flight from before the poke landed afterwards and repainted the old side, and
the toast's path never read the toys again (only the dock popover's did).

The fix, driven here through the page's own methods in node (membrane/ui/home.js,
the custom element's prototype, with the chat, the DOM and the clock stubbed):
every successful poke re-reads the toys whichever button made it, and a dock
read that started before the latest poke never paints over the poke's result."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")

HARNESS = r"""
process.on("unhandledRejection", () => {});          // home.js's browser-only imports
let K;
global.HTMLElement = class {};
global.customElements = { define: (n, c) => { K = c; }, get: () => undefined };
global.document = { hidden: false };
const P = require(%(panel)r);
require(%(home)r);

const BALL = (side, n) => [{ name: "toy:ball", kind: "ball", reading: { side },
                            changes: { self: n, alice: 0 }, last: { by: "self" } }];
const reads = [];                                     // each dock read: {resolve}
const pokes = [];
const page = Object.create(K.prototype);
const painted = [];
page._paintDock = function (toys) { this._dockToys = toys; painted.push(toys[0].reading.side); };
page._paintSelfPanel = () => {};
page._ballNoteT = { textContent: "⚽ Blurp rolled you the ball" };
page._chat = {
  _toyState: () => new Promise((resolve) => reads.push({ resolve })),
  _toyPoke: () => new Promise((resolve) => pokes.push({ resolve })),
  _toyAfterPoke: P.toyAfterPoke,
};
const btn = (cls) => ({
  disabled: false, classList: { contains: (c) => c === cls },
  getAttribute: (a) => ({ "data-poke-toy": "toy:ball", "data-poke-action": "roll" })[a] || null,
  closest() { return this; },
});
const tick = () => new Promise((r) => setImmediate(r));

(async () => {
  page._dockTick(true);                               // a dock read, in flight from before the poke
  page._paintDock(BALL("other", 5));                  // the page shows self's roll to you
  const b = btn(%(cls)r);
  const done = %(click)s;
  await tick();
  pokes[0].resolve({ ok: true, reading: { side: "self" }, by: "alice" });   // the roll landed
  await done; await tick();
  reads[0].resolve(BALL("other", 5));                 // the stale read lands afterwards
  await tick(); await tick();
  const afterStale = page._dockToys[0].reading.side;
  const fresh = reads.length;
  if (reads[1]) { reads[1].resolve(BALL("self", 5)); await tick(); await tick(); }
  process.stdout.write(JSON.stringify({ afterStale, fresh, final: page._dockToys[0].reading.side, painted }));
  process.exit(0);
})();
"""


def _run(cls, click):
    js = HARNESS % {"panel": str(UI / "panel.js"), "home": str(UI / "home.js"), "cls": cls, "click": click}
    return json.loads(subprocess.check_output(["node", "-e", js], timeout=30))


TOAST = ("bn-roll", "page._onBallNote({ target: b })")
PANEL = ("", "page._onToyPoke({ target: b })")       # the 👶 panel's play buttons take the same door


@NODE
@pytest.mark.parametrize("cls,click", [TOAST, PANEL], ids=["toast", "panel"])
def test_a_read_from_before_the_poke_never_paints_over_its_result(cls, click):
    out = _run(cls, click)
    assert out["afterStale"] == "self", out


@NODE
@pytest.mark.parametrize("cls,click", [TOAST, PANEL], ids=["toast", "panel"])
def test_every_successful_poke_reads_the_toys_again(cls, click):
    out = _run(cls, click)
    assert out["fresh"] == 2, out
    assert out["final"] == "self", out
