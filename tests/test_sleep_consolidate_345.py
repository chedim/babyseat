"""#345 part 2: consolidation -- nothing raw stays through sleep.

At the moment Blurp falls asleep every raw line of the waking period moves,
unchanged, to its dated archive (self/archive/record-<UTC date of its own
ts>.jsonl), and the working record is emptied down to ONE summary line:
copies of the lines read whole (the birth, the accepts) and of the last
RECENT_MAX lines (the activity feed, the card's last activity). Readers see
summary + new lines as they saw the whole record (the equivalence tests below
run the status, the feed, the sheet, the dock and the card on a long
synthetic record before and after; #576 retired the old learner's readers).
A seat that dies mid-consolidation finishes it on load: no line lost, none
doubled. Every time here is injected: nothing sleeps.
"""
import json
import os
import random
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import energy as E  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

T0 = 1_760_000_000.0                  # 2025-10-09 08:53:20 UTC
FREE = 1_000_000_000
C = MEMBRANE_NS + "Component:"


class Crash(BaseException):
    """The seat dying mid-step (not an error the code may catch)."""


def _env():
    d = tempfile.mkdtemp()
    base = {k: v for k, v in os.environ.items() if k not in ("MEMBRANE_SELF_NAME", "MEMBRANE_DATA_DIR")}
    base["MEMBRANE_DATA_DIR"] = d
    return mock.patch.dict(os.environ, base, clear=True)


def _seat(d=None):
    d = Path(d or tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv, d


def _fresh(srv, d):
    """The same seat after a restart: a new runtime, the files as they are."""
    SL._RT.pop(id(srv), None)
    return _seat(d)


_ram = None


def setUpModule():
    global _ram
    _ram = mock.patch.object(E, "_avail_ram", return_value=None)
    _ram.start()


def tearDownModule():
    _ram.stop()


# ---- a long synthetic record ----------------------------------------------------------

class Life:
    """A seeded Blurp's record, written step by step: its birth, then lines of
    several kinds (a learner's, errors, the operator's accepts), with stalls
    and restarts in between."""

    KINDS = ("learn", "learn", "learn", "note", "error")

    def __init__(self, seed=345):
        self.rng = random.Random(seed)
        self.ts = T0
        self.n = 0
        self.lines = [{"ts": self.ts, "kind": "birth", "by": "operator"}]

    def step(self):
        r = self.rng
        self.n += 1
        self.ts += 2400.0 if self.n % 700 == 0 else 150.0 if self.n % 333 == 0 else 10.0
        if self.n % 500 == 0:
            self.lines.append({"ts": self.ts, "kind": "accept", "id": f"b{self.n}", "by": "operator"})
        self.lines.append({"ts": self.ts, "kind": r.choice(self.KINDS), "n": self.n, "x": round(r.random(), 4)})

    def take(self, steps):
        k = len(self.lines)
        for _ in range(steps):
            self.step()
        return self.lines[k:]


def _write(d, lines):
    p = Path(d) / "self" / "record.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")


STEPS = 6000


def _twin(steps=STEPS, seed=345):
    """Two seats with the same long record: A keeps it whole, B consolidates."""
    life = Life(seed)
    life.take(steps)
    a, da = _seat()
    b, db = _seat()
    _write(da, life.lines)
    _write(db, life.lines)
    SL._RT.pop(id(a), None)
    SL._RT.pop(id(b), None)
    a, _ = _seat(da)
    b, _ = _seat(db)
    from membrane import self_world as SW
    from membrane import toybox as TB
    for kind in ("die", "slow"):                       # toys on both seats (one toybox: the data dir)
        TB.add(a, kind)
        SW.join(b, [TB._put(b.entity_store, "toy:" + kind, kind, n=0)], "toy")
    # TB.add saves the store; the bare _put only arms its 10 s save debounce, so a
    # restart of B inside those 10 s read a store without its toys (#408): flush
    # it, as TB.add does, so the files are what the seat holds
    b.entity_store.flush()
    return life, (a, da), (b, db)


# ---- every reader, before and after ------------------------------------------------------


def _strip(x):
    """A value fit for == (numpy scalars to floats, tuples to lists)."""
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_strip(v) for v in x]
    if hasattr(x, "tolist"):
        return x.tolist()
    return x


def _readers(srv, now):
    """What the body's readers say over this seat's record."""
    recs = SL._read(srv)
    out = {}
    out["births"] = [r for r in recs if r.get("kind") == "birth"]
    out["accepts"] = [r for r in recs if r.get("kind") in SL.KEEP and r.get("kind") != "birth"]
    out["recent"] = SL.recent(srv, SL.RECENT_MAX)
    out["last_line"] = next((r for r in reversed(recs) if r.get("kind") not in SL.OPERATOR_KINDS), None)
    with mock.patch("time.time", return_value=now):
        st = SL.status(srv)
    out["status"] = st
    return _strip(out)


def _panel(srv, now):
    """The sheet, the dock and the card (energy aside: the record's size is
    the one thing consolidation changes on purpose)."""
    with mock.patch("time.time", return_value=now):
        sh = dict(SS.compute(srv, now))
        dock = dict(SS.dock(srv, now, fresh=True))
        card = dict(SS.card(srv, now))
    sh.pop("energy", None)
    for k in ("energy", "asleep", "pull", "limiting", "record_cap"):
        card.pop(k, None)
    return _strip({"sheet": sh, "dock": dock, "card": card})


class TestEquivalence(unittest.TestCase):
    """Every reader returns the same before and after consolidation."""

    @classmethod
    def setUpClass(cls):
        cls.env = _env()
        cls.env.start()
        cls.life, (cls.a, cls.da), (cls.b, cls.db) = _twin()
        cls.now = cls.life.ts + 30
        cls.out = SL.consolidate(cls.b, cls.now)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()

    def assertSame(self, x, y):
        self.assertEqual(sorted(x), sorted(y))
        for k in x:
            self.assertEqual(x[k], y[k], k)

    def test_it_consolidated(self):
        self.assertIsNotNone(self.out)
        self.assertEqual(self.out["archived"], len(self.life.lines))
        raw = (self.db / "self" / "record.jsonl").read_text().splitlines()
        self.assertEqual(len(raw), 1)                            # one summary line, nothing raw
        self.assertEqual(json.loads(raw[0])["kind"], "summary")
        self.assertLess(SL.record_bytes(self.b), SL.record_bytes(self.a) / 2)

    def test_every_reader_is_the_same(self):
        self.assertSame(_readers(self.a, self.now), _readers(self.b, self.now))

    def test_the_sheet_dock_and_card_are_the_same(self):
        self.assertSame(_panel(self.a, self.now), _panel(self.b, self.now))

    def test_the_same_as_it_goes_on_and_sleeps_again(self):
        """New lines after the summary, then a second consolidation, then more."""
        life = Life(345)
        life.take(STEPS)
        more = life.take(500)
        for srv in (self.a, self.b):
            for rec in more:
                SL._append(srv, rec)
        now = life.ts + 30
        self.assertSame(_readers(self.a, now), _readers(self.b, now))
        SL.consolidate(self.b, now)                              # it sleeps again: only the new lines go
        more = life.take(300)
        for srv in (self.a, self.b):
            for rec in more:
                SL._append(srv, rec)
        now = life.ts + 30
        want = _readers(self.a, now)
        self.assertSame(want, _readers(self.b, now))
        self.assertSame(_panel(self.a, now), _panel(self.b, now))
        b2, _ = _fresh(self.b, self.db)                          # a restart reads the same summary back
        self.assertSame(want, _readers(b2, now))


# ---- the archive -----------------------------------------------------------------------

def _archive_text(d):
    """Every archived line in the manifest's order (the order they were written)."""
    arch = Path(d) / "self" / "archive"
    cur = {}
    out = []
    for ln in (arch / "manifest.jsonl").read_text().splitlines():
        for name, count in json.loads(ln)["runs"]:
            fh = cur.setdefault(name, iter((arch / name).read_text().splitlines()))
            out.extend(next(fh) for _ in range(count))
    return out


class TestTheArchive(unittest.TestCase):
    def setUp(self):
        self.env = _env()
        self.env.start()
        self.addCleanup(self.env.stop)

    def _seat_with(self, lines):
        srv, d = _seat()
        _write(d, lines)
        return _fresh(srv, d)

    def test_every_raw_line_moves_unchanged_to_its_own_day(self):
        life = Life(7)
        life.take(5000)                                      # ~19 h from 08:53 UTC: past midnight
        srv, d = self._seat_with(life.lines)
        before = (d / "self" / "record.jsonl").read_text().splitlines()
        SL.consolidate(srv, life.ts + 5)
        arch = d / "self" / "archive"
        days = sorted(p.name for p in arch.glob("record-*.jsonl"))
        self.assertGreater(len(days), 1)                     # the synthetic life spans days
        for name in days:
            for ln in (arch / name).read_text().splitlines():
                day = SL._day(json.loads(ln)["ts"])
                self.assertEqual(name, f"record-{day}.jsonl")
        self.assertEqual(_archive_text(d), before)           # byte for byte, in order, once

    def test_history_is_the_whole_record(self):
        life = Life(8)
        life.take(1200)
        srv, d = self._seat_with(life.lines)
        full = list(SL._read(srv))
        SL.consolidate(srv, life.ts + 5)
        more = life.take(100)
        for rec in more:
            SL._append(srv, rec)
        self.assertEqual(SL.history(srv), full + more)
        SL.consolidate(srv, life.ts + 5)                      # a second sleep archives only the new lines
        self.assertEqual(SL.history(srv), full + more)
        self.assertEqual(len(_archive_text(d)), len(full) + len(more))

    def test_the_activity_feed_never_shows_the_summary(self):
        life = Life(9)
        life.take(30)
        srv, d = self._seat_with(life.lines)
        want = SL.recent(srv, SL.RECENT_MAX)
        SL.consolidate(srv, life.ts + 5)
        self.assertEqual(SL.recent(srv, SL.RECENT_MAX), want)
        self.assertTrue(all(r.get("kind") != "summary" for r in SL.recent(srv, SL.RECENT_MAX)))

    def test_nothing_new_nothing_to_do(self):
        life = Life(10)
        life.take(200)
        srv, d = self._seat_with(life.lines)
        self.assertIsNotNone(SL.consolidate(srv, life.ts + 5))
        self.assertIsNone(SL.consolidate(srv, life.ts + 6))  # only the summary: nothing new to archive
        self.assertEqual(len(_archive_text(d)), len(life.lines))

    def test_nothing_on_the_step_path_reads_the_archive(self):
        life = Life(11)
        life.take(1500)
        srv, d = self._seat_with(life.lines)
        SL.consolidate(srv, life.ts + 5)
        SL._RT.pop(id(srv), None)
        srv, _ = _seat(d)
        with mock.patch.object(SL, "_archive_lines", side_effect=AssertionError("the archive was read")), \
                mock.patch.object(SL, "history", side_effect=AssertionError("the archive was read")), \
                mock.patch.object(E, "_free", return_value=FREE):
            SL.set_mode(srv, "on")
            SL.status(srv)
            for k in range(3):
                SL.step(srv, now=life.ts + 60 + 20 * k)


# ---- falling asleep consolidates; the record reserve recovers --------------------------

class TestSleepConsolidates(unittest.TestCase):
    def setUp(self):
        self.env = _env()
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_falling_asleep_archives_the_day(self):
        life = Life(12)
        life.take(800)
        srv, d = _seat()
        _write(d, life.lines)
        srv, _ = _fresh(srv, d)
        now = life.ts + 10
        E.tick(srv, now, free=FREE)
        E.tick(srv, now + 1, cpu_s=1 / E.CPU_PER_S, free=FREE)          # out of energy
        self.assertTrue(E.asleep(srv))
        raw = (d / "self" / "record.jsonl").read_text().splitlines()
        self.assertEqual(len(raw), 1)
        self.assertEqual(json.loads(raw[0])["kind"], "summary")
        sleep = [r for r in SL._read(srv) if r.get("kind") == "sleep"][-1]
        self.assertEqual(sleep["why"], "energy")
        self.assertEqual(SL.history(srv)[-1], sleep)             # the sleep line itself is archived too

    def test_the_record_reserve_recovers_and_it_stays_awake(self):
        """Part 1's gap: a full record put it to sleep, it woke rested and fell
        asleep again at once. Now sleep empties the record."""
        life = Life(13)
        life.take(6000)
        srv, d = _seat()
        _write(d, life.lines)
        srv, _ = _fresh(srv, d)
        full = SL.record_bytes(srv)
        now = life.ts + 10
        E.tick(srv, now, free=FREE, avail=2 * full)               # the cap: the record as it is (half the RAM)
        self.assertTrue(E.asleep(srv))
        self.assertEqual([r for r in SL._read(srv) if r.get("kind") == "sleep"][-1]["why"], "record")
        self.assertLess(SL.record_bytes(srv), full / 2)
        E.tick(srv, now + 10, free=FREE, avail=2 * full)          # energy was full: it wakes rested
        self.assertFalse(E.asleep(srv))
        E.tick(srv, now + 20, free=FREE)
        self.assertFalse(E.asleep(srv))                           # ...and stays up: the record has room again
        self.assertGreater(E.report(srv)["reserves"]["record"], 0.5)


# ---- anything open at sleep is dropped --------------------------------------------------


# ---- restart safety ----------------------------------------------------------------------

class TestRestartSafety(unittest.TestCase):
    """The seat dies at each step of a consolidation; on load it finishes it.
    No line is lost or doubled, in the working record or the archive."""

    def setUp(self):
        self.env = _env()
        self.env.start()
        self.addCleanup(self.env.stop)
        self.life = Life(15)
        self.life.take(1200)
        srv, self.d = _seat()
        _write(self.d, self.life.lines)
        self.srv, _ = _fresh(srv, self.d)
        self.full = list(SL._read(self.srv))
        self.raw = (self.d / "self" / "record.jsonl").read_text().splitlines()
        self.now = self.life.ts + 5

    def _check(self):
        srv, _ = _fresh(self.srv, self.d)
        self.assertFalse((self.d / "self" / SL.CONSOLIDATING).exists())
        lines = (self.d / "self" / "record.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["kind"], "summary")
        self.assertEqual(_archive_text(self.d), self.raw)          # each line archived exactly once
        self.assertEqual(SL.history(srv), self.full)
        return srv

    def _die_at(self, name):
        with mock.patch.object(SL, name, side_effect=Crash()):
            with self.assertRaises(Crash):
                SL.consolidate(self.srv, self.now)

    def test_dies_while_archiving(self):
        real = SL._c_append

        def half(path, lines):                                 # the first day's lines land, then it dies
            real(path, lines)
            raise Crash()
        with mock.patch.object(SL, "_c_append", side_effect=half):
            with self.assertRaises(Crash):
                SL.consolidate(self.srv, self.now)
        self._check()

    def test_dies_before_the_manifest(self):
        self._die_at("_c_manifest")
        self._check()

    def test_dies_before_the_swap(self):
        self._die_at("_c_swap")
        self._check()

    def test_dies_after_the_swap(self):
        self._die_at("_c_done")
        self._check()

    def test_an_error_leaves_the_record_whole(self):
        with mock.patch.object(SL, "_c_manifest", side_effect=OSError("disk full")):
            self.assertIsNone(SL.consolidate(self.srv, self.now))
        self.assertEqual((self.d / "self" / "record.jsonl").read_text().splitlines(), self.raw)
        self.assertFalse((self.d / "self" / SL.CONSOLIDATING).exists())
        self.assertEqual(SL._read(self.srv), self.full)
        arch = self.d / "self" / "archive"
        self.assertEqual(sum(len(p.read_text().splitlines()) for p in arch.glob("record-*.jsonl")), 0)
        SL.consolidate(self.srv, self.now)                        # the next sleep does it
        self._check()


if __name__ == "__main__":
    unittest.main()
