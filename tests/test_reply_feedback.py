"""Reply feedback (G15 #1029 / G9 #1032, docs/ui-pack-design.md): the chat footer's
👍/👎/retry and the Decision card's per-option impact line. The press itself is the door's
(membrane/packs/door_self.c1f; tests/specs/door_self.json): these are the page's halves."""
import pathlib
import subprocess
import shutil

import pytest

UI = pathlib.Path(__file__).resolve().parent.parent / "membrane" / "ui"


def test_the_ui_draws_the_footer_and_the_impact():
    panel = (UI / "panel.js").read_text()
    assert "_fbHtml(info.id)" in panel and 'data-fb="' in panel and "/membrane/feedback" in panel
    dec = (UI / "decisions.js").read_text()
    assert "impactHtml(card, o)" in dec and "impact not stated" in dec


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_impact_lines_render():
    src = (UI / "decisions.js").read_text()
    start = src.index("function impactOf(")
    end = src.index("/** How an open card's one variable is entered")
    js = ("const esc = (s) => String(s).replace(/[&<>\"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));\n"
          + src[start:end] +
          "console.log(impactHtml({var:{impact:{a:'restarts <the> seat'}}}, 'a'));"
          "console.log(impactHtml({var:{}}, 'b'));")
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0] == '<span class="imp">→ restarts &lt;the&gt; seat</span>'
    assert out[1] == '<span class="imp none">impact not stated</span>'
