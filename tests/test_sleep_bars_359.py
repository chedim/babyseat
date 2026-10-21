"""#359: the sheet shows Blurp's four sleep reserves as bars, with the pull.

The film's sleep scene, made live. The sheet's `energy` section is
membrane/energy.py's report(): energy, asleep, reserves {energy, disk, awake,
record} (each 1 = full, 0 = empty), pull (1 - the lowest), limiting (which
one is lowest), since (when it fell asleep or woke, unix seconds),
record_bytes and record_cap (None when the RAM reading failed: the record
reserve then reads 1.0 by design).

Pure helpers in membrane/ui/panel.js: sleepBars(energy) is the model (rows,
the lowest marked, the pull, awake or asleep and since), sheetSleep(energy)
the HTML (each bar a meter with aria values and a text value).
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _run(body):
    js = "const P=require(%r); let out; %s; process.stdout.write(JSON.stringify(out))" % (str(UI / "panel.js"), body)
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _bars(e):
    return _run("out = P.sleepBars(%s)" % json.dumps(e))


def _html(e):
    return _run("out = P.sheetSleep(%s)" % json.dumps(e))


def _sheet(sheet):
    return _run("out = P.selfSheetHtml(%s, {}, 0)" % json.dumps(sheet))


SINCE = 1792929720.0          # 2026-10-25 12:02 UTC
AWAKE = {"energy": 0.734, "asleep": False, "reserves": {"energy": 0.734, "disk": 0.9, "awake": 0.41, "record": 0.88},
         "pull": 0.59, "limiting": "awake", "since": SINCE, "record_bytes": 1200, "record_cap": 10000.0}


@NODE
def test_four_bars_in_the_films_order_with_their_values():
    m = _bars(AWAKE)
    assert [r["key"] for r in m["rows"]] == ["energy", "disk", "awake", "record"]
    assert [r["label"] for r in m["rows"]] == ["energy", "disk", "awake time", "record room"]
    assert [r["pct"] for r in m["rows"]] == [73, 90, 41, 88]
    assert [r["lowest"] for r in m["rows"]] == [False, False, True, False]
    assert m["pull_pct"] == 59 and m["lowest"] == "awake"
    assert m["asleep"] is False


@NODE
def test_the_lowest_is_the_seats_limiting_one():
    e = dict(AWAKE, reserves={"energy": 0.2, "disk": 0.2, "awake": 0.5, "record": 0.9}, limiting="energy", pull=0.8)
    m = _bars(e)
    assert [r["lowest"] for r in m["rows"]] == [True, False, False, False]
    assert m["pull_pct"] == 80


@NODE
def test_without_limiting_or_pull_the_page_reads_the_lowest_itself():
    e = {"energy": 0.5, "asleep": False, "reserves": {"energy": 0.5, "disk": 0.3, "awake": 0.6, "record": 1.0}}
    m = _bars(e)
    assert m["lowest"] == "disk" and m["pull_pct"] == 70


@NODE
def test_values_are_clamped_to_0_100():
    e = dict(AWAKE, reserves={"energy": 1.4, "disk": -0.2, "awake": 0.5, "record": 0.5}, limiting="disk", pull=1.0)
    assert [r["pct"] for r in _bars(e)["rows"]] == [100, 0, 50, 50]


@NODE
def test_an_unknown_record_cap_says_so():
    e = dict(AWAKE, record_cap=None, reserves=dict(AWAKE["reserves"], record=1.0))
    rec = _bars(e)["rows"][3]
    assert rec["unknown"] is True and "cap unknown" in rec["text"]
    h = _html(e)
    assert "record room: cap unknown" in h


@NODE
def test_the_html_bars_are_meters_with_aria_values_and_text():
    h = _html(AWAKE)
    meters = re.findall(r'<div class="sbar[^"]*"[^>]*role="meter"[^>]*>', h)
    assert len(meters) == 4
    assert 'aria-valuenow="73"' in h and 'aria-valuemin="0"' in h and 'aria-valuemax="100"' in h
    assert 'aria-label="energy"' in h and 'aria-label="awake time"' in h
    assert "aria-valuetext=" in h
    assert "73%" in h and "41%" in h
    # the lowest is marked: its bar in the warning colour and a "lowest" tag
    assert re.search(r'class="sbar low"[^>]*aria-label="awake time"', h)
    assert h.count('class="sbar low"') == 1
    assert ">lowest<" in h
    # the energy bar is gold, the others the field colour
    assert 'class="sfill s-energy"' in h and 'class="sfill s-disk"' in h


@NODE
def test_the_pull_line_and_awake_since():
    h = _html(AWAKE)
    assert "pull = 1 − lowest = 59%" in h
    assert "awake" in h and "since" in h and "12:02" in h


@NODE
def test_asleep_shows_asleep():
    h = _html(dict(AWAKE, asleep=True))
    assert "asleep" in h


@NODE
def test_without_since_no_since():
    e = dict(AWAKE)
    e.pop("since")
    assert "since" not in _html(e)


@NODE
def test_an_older_seat_shows_nothing():
    assert _html(None) == "" and _html({}) == ""
    assert _bars(None) is None


@NODE
def test_a_seat_with_energy_but_no_reserves_shows_the_energy_bar_only():
    m = _bars({"energy": 0.5, "asleep": False})
    assert [r["key"] for r in m["rows"]] == ["energy"]


@NODE
def test_data_text_is_escaped():
    e = dict(AWAKE, limiting="<b>x</b>")
    h = _html(e)
    assert "<b>x</b>" not in h


@NODE
def test_the_sheet_carries_the_sleep_block():
    h = _sheet({"header": {"level": 1}, "energy": AWAKE})
    assert 'class="sleep"' in h and "Sleep" in h
    assert "pull = 1 − lowest = 59%" in h
    assert "energy <b>73%</b>" in h          # the nameplate still says it at a glance
