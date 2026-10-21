"""#345 part 1: Blurp's energy, sleep and wake.

Energy runs 0..1 and lives with Blurp (<self dir>/energy.json): a restart
neither refills nor drains it. Awake, each loop step costs a basal share of
the seconds that passed plus a share of the CPU the cycle used; an error
costs ERROR_COST. The sleep pull is 1 - the lowest reserve (energy, disk,
awake time); when a reserve runs out Blurp falls asleep. Asleep, no cycle
runs and energy comes back at RESTORE_PER_S; it wakes at full energy or when
a person speaks. Every time here is injected: nothing sleeps.
"""
import json
import os
import shutil
import subprocess
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
UI = ROOT / "membrane" / "ui"

from membrane import energy as E  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

T0 = 1_760_000_000.0
FREE = 1_000_000_000          # bytes free on the volume, as the tests inject it
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


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
    SL.bind(srv)
    return srv, d


_ram = None


def setUpModule():
    """No test reads the machine's RAM: the available RAM is injected."""
    global _ram
    _ram = mock.patch.object(E, "_avail_ram", return_value=None)
    _ram.start()


def tearDownModule():
    _ram.stop()


def _lines(srv, kind):
    return [r for r in SL._read(srv) if r.get("kind") == kind]


def _asleep_at_zero(srv):
    """Spend it all on CPU in one step: asleep, energy 0."""
    E.tick(srv, T0, free=FREE)
    E.tick(srv, T0 + 1, cpu_s=1 / E.CPU_PER_S, free=FREE)
    assert E.asleep(srv)


class TestTheConstants(unittest.TestCase):
    def test_the_approved_values(self):
        self.assertEqual(E.ERROR_COST, 0.01)
        self.assertEqual(E.RESTORE_PER_S, 0.001)

    def test_the_picked_values(self):
        self.assertAlmostEqual(E.BASAL_PER_S, 1 / (16 * 3600))
        self.assertAlmostEqual(E.CPU_PER_S, 1 / (16 * 3600))
        self.assertEqual(E.AWAKE_LEN_S, 16 * 3600)


class TestEnergy(unittest.TestCase):
    def test_a_new_blurp_is_rested_and_awake(self):
        srv, _ = _seat()
        self.assertEqual(E.level(srv), 1.0)
        self.assertFalse(E.asleep(srv))

    def test_the_first_step_costs_nothing(self):
        srv, _ = _seat()
        E.tick(srv, T0, cpu_s=0.0, free=FREE)
        self.assertEqual(E.level(srv), 1.0)

    def test_awake_time_and_cpu_drain_it(self):
        srv, _ = _seat()
        E.tick(srv, T0, free=FREE)
        E.tick(srv, T0 + 3600, cpu_s=10.0, free=FREE)
        self.assertAlmostEqual(E.level(srv), 1 - 3600 * E.BASAL_PER_S - 10.0 * E.CPU_PER_S)
        self.assertFalse(E.asleep(srv))

    def test_a_restart_neither_refills_nor_drains_it(self):
        srv, d = _seat()
        E.tick(srv, T0, free=FREE)
        E.tick(srv, T0 + 7200, free=FREE)
        before = E.level(srv)
        SL._RT.pop(id(srv), None)
        srv2, _ = _seat(d)
        self.assertAlmostEqual(E.level(srv2), before)
        E.tick(srv2, T0 + 7200 + 86400, free=FREE)          # the downtime is not awake time
        self.assertAlmostEqual(E.level(srv2), before)
        self.assertTrue((d / "self" / E.FILE).exists())

    def test_a_paused_loop_is_not_awake_time(self):
        srv, _ = _seat()
        E.tick(srv, T0, free=FREE)
        E.pause(srv)                                         # off / paused / held: nothing ran
        E.tick(srv, T0 + 3 * 3600, free=FREE)
        self.assertEqual(E.level(srv), 1.0)

    def test_an_error_costs_its_share_and_is_recorded(self):
        srv, _ = _seat()
        E.error(srv, "nudged the ball on the person's side", now=T0)
        self.assertAlmostEqual(E.level(srv), 1.0 - E.ERROR_COST)
        rec = _lines(srv, "error")[-1]
        self.assertEqual(rec["why"], "nudged the ball on the person's side")
        self.assertEqual(rec["cost"], E.ERROR_COST)
        self.assertAlmostEqual(rec["energy"], 1.0 - E.ERROR_COST)

    def test_energy_never_goes_below_zero(self):
        srv, _ = _seat()
        for i in range(150):
            E.error(srv, "x", now=T0 + i)
        self.assertEqual(E.level(srv), 0.0)

    def test_share_is_the_throttle_times_energy(self):
        srv, _ = _seat()
        self.assertAlmostEqual(E.share(0.5, srv), 0.5)
        for i in range(25):
            E.error(srv, "x", now=T0 + i)
        self.assertAlmostEqual(E.share(0.5, srv), 0.5 * 0.75)


class TestThePull(unittest.TestCase):
    def test_reserves_pull_and_the_limiting_one(self):
        srv, _ = _seat()
        E.tick(srv, T0, free=FREE)                           # the disk baseline: free space now
        E.tick(srv, T0 + 3600, free=FREE // 2)
        r = E.report(srv)
        self.assertAlmostEqual(r["reserves"]["disk"], 0.5)
        self.assertAlmostEqual(r["reserves"]["awake"], 1 - 3600 / E.AWAKE_LEN_S)
        self.assertAlmostEqual(r["reserves"]["energy"], E.level(srv))
        self.assertEqual(r["limiting"], "disk")
        self.assertAlmostEqual(r["pull"], 0.5)
        self.assertFalse(r["asleep"])
        json.dumps(r)

    def test_more_free_space_than_at_waking_is_a_full_reserve(self):
        srv, _ = _seat()
        E.tick(srv, T0, free=FREE)
        E.tick(srv, T0 + 1, free=FREE * 2)
        self.assertEqual(E.report(srv)["reserves"]["disk"], 1.0)


class TestTheRecordReserve(unittest.TestCase):
    """The operator's fourth reserve: the record Blurp holds in RAM against
    a cap of the person's CPU share x the RAM available when it woke."""

    def test_record_bytes_track_the_lines_held(self):
        srv, d = _seat()
        b0 = SL.record_bytes(srv)
        SL._append(srv, {"ts": T0, "kind": "note", "pad": "x" * 100})
        self.assertGreater(SL.record_bytes(srv), b0 + 100)
        SL._RT.pop(id(srv), None)                           # a restart reads the same size back from disk
        srv2, _ = _seat(d)
        self.assertEqual(SL.record_bytes(srv2), SL.record_bytes(srv))

    def test_the_cap_is_the_share_of_ram_at_wake(self):
        with _env():
            srv, _ = _seat()
            E.tick(srv, T0, free=FREE, avail=10_000_000)
            r = E.report(srv)
        self.assertEqual(r["record_cap"], 0.5 * 10_000_000)  # the default share, 0.5
        self.assertAlmostEqual(r["reserves"]["record"], 1 - SL.record_bytes(srv) / 5_000_000)

    def test_a_full_record_puts_it_to_sleep(self):
        with _env():
            srv, _ = _seat()
            E.tick(srv, T0, free=FREE, avail=4000)               # cap 2000 bytes
            SL._append(srv, {"ts": T0, "kind": "note", "pad": "x" * 2000})
            E.tick(srv, T0 + 10, free=FREE)
            self.assertTrue(E.asleep(srv))
            self.assertEqual(_lines(srv, "sleep")[-1]["why"], "record")
            self.assertEqual(E.report(srv)["limiting"], "record")

    def test_the_cap_is_recomputed_at_each_wake(self):
        with _env():
            srv, _ = _seat()
            E.tick(srv, T0, free=FREE, avail=10_000_000)
            E.tick(srv, T0 + 1, cpu_s=1 / E.CPU_PER_S, free=FREE)
            self.assertTrue(E.asleep(srv))
            E.person_spoke(srv, "alice", now=T0 + 2, free=FREE, avail=20_000_000)
            self.assertEqual(E.report(srv)["record_cap"], 0.5 * 20_000_000)

    def test_an_unknown_ram_reading_never_puts_it_to_sleep(self):
        srv, _ = _seat()
        E.tick(srv, T0, free=FREE)                                # _avail_ram -> None
        r = E.report(srv)
        self.assertIsNone(r["record_cap"])
        self.assertEqual(r["reserves"]["record"], 1.0)
        with _env():
            self.assertIsNone(SS.card(srv, now=T0)["record_cap"])     # the card: the cap is unknown

    def test_the_ram_reader_answers_bytes_or_none(self):
        with mock.patch.object(E, "_meminfo", return_value=None), \
                mock.patch.object(E, "_sysconf_avail", return_value=None), \
                mock.patch.object(E, "_vm_stat", return_value=None):
            self.assertIsNone(E._read_ram())
        with mock.patch.object(E, "_meminfo", return_value=123):
            self.assertEqual(E._read_ram(), 123)

    def test_vm_stat_parsing(self):
        out = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
               "Pages free:                               1000.\n"
               "Pages active:                             5000.\n"
               "Pages inactive:                           2000.\n"
               "Pages speculative:                         100.\n")
        self.assertEqual(E._parse_vm_stat(out), (1000 + 2000 + 100) * 16384)
        self.assertIsNone(E._parse_vm_stat("garbage"))

    def test_meminfo_parsing(self):
        self.assertEqual(E._parse_meminfo("MemTotal: 8000 kB\nMemAvailable:   4000 kB\n"), 4000 * 1024)
        self.assertIsNone(E._parse_meminfo("MemTotal: 8000 kB\n"))


class TestFallingAsleep(unittest.TestCase):
    def test_out_of_energy(self):
        srv, _ = _seat()
        _asleep_at_zero(srv)
        self.assertEqual(E.level(srv), 0.0)
        rec = _lines(srv, "sleep")[-1]
        self.assertEqual(rec["why"], "energy")
        self.assertEqual(rec["energy"], 0.0)
        self.assertEqual(rec["ts"], T0 + 1)
        self.assertAlmostEqual(E.report(srv)["pull"], 1.0)

    def test_out_of_disk(self):
        srv, _ = _seat()
        E.tick(srv, T0, free=FREE)
        E.tick(srv, T0 + 10, free=0)
        self.assertTrue(E.asleep(srv))
        self.assertEqual(_lines(srv, "sleep")[-1]["why"], "disk")

    def test_out_of_awake_time(self):
        srv, _ = _seat()
        with mock.patch.object(E, "BASAL_PER_S", 0.0):        # isolate the clock from the energy it drains
            E.tick(srv, T0, free=FREE)
            E.tick(srv, T0 + E.AWAKE_LEN_S, free=FREE)
        self.assertTrue(E.asleep(srv))
        self.assertEqual(_lines(srv, "sleep")[-1]["why"], "awake")

    def test_asleep_is_not_off(self):
        srv, _ = _seat()
        SL.set_mode(srv, "on")
        _asleep_at_zero(srv)
        self.assertEqual(SL.mode(srv), "on")

    def test_a_sleeping_blurp_runs_no_cycle(self):
        srv, _ = _seat()
        SL.set_mode(srv, "on")
        _asleep_at_zero(srv)
        with mock.patch.object(SL, "_current", side_effect=AssertionError("a cycle ran")):
            self.assertIsNone(SL.cycle(srv, now=T0 + 100))

    def test_the_loop_step_restores_instead_of_cycling(self):
        srv, _ = _seat()
        SL.set_mode(srv, "on")
        _asleep_at_zero(srv)
        with mock.patch.object(SL, "cycle", side_effect=AssertionError("a cycle ran")), \
                mock.patch.object(E, "_free", return_value=FREE):
            SL.step(srv, now=T0 + 101)
        self.assertAlmostEqual(E.level(srv), 100 * E.RESTORE_PER_S)

    def test_the_loop_step_charges_the_cycle(self):
        srv, _ = _seat()
        SL.set_mode(srv, "on")
        with mock.patch.object(SL, "cycle", return_value=None) as cyc, \
                mock.patch.object(E, "_free", return_value=FREE):
            SL.step(srv, now=T0)
            SL.step(srv, now=T0 + 600)
        self.assertEqual(cyc.call_count, 2)
        self.assertAlmostEqual(E.level(srv), 1 - 600 * E.BASAL_PER_S, places=4)

    def test_an_off_step_runs_and_costs_nothing(self):
        srv, _ = _seat()                                     # born off
        with mock.patch.object(SL, "cycle", side_effect=AssertionError("a cycle ran")), \
                mock.patch.object(E, "_free", return_value=FREE):
            SL.step(srv, now=T0)
            SL.step(srv, now=T0 + 3600)
        self.assertEqual(E.level(srv), 1.0)


class TestWaking(unittest.TestCase):
    def test_rested_at_full_energy(self):
        srv, _ = _seat()
        _asleep_at_zero(srv)
        E.tick(srv, T0 + 1 + 500, free=FREE)
        self.assertTrue(E.asleep(srv))
        self.assertAlmostEqual(E.level(srv), 0.5)
        E.tick(srv, T0 + 1 + 1000, free=FREE // 4)
        self.assertFalse(E.asleep(srv))
        self.assertEqual(E.level(srv), 1.0)
        rec = _lines(srv, "wake")[-1]
        self.assertEqual((rec["why"], rec["energy"]), ("rested", 1.0))
        r = E.report(srv)                                    # a fresh day: the disk now, the clock at zero
        self.assertEqual(r["reserves"]["disk"], 1.0)
        self.assertEqual(r["reserves"]["awake"], 1.0)

    def test_a_person_speaking(self):
        srv, _ = _seat()
        _asleep_at_zero(srv)
        E.tick(srv, T0 + 201, free=FREE)
        self.assertTrue(E.person_spoke(srv, "alice", now=T0 + 202, free=FREE))
        self.assertFalse(E.asleep(srv))
        rec = _lines(srv, "wake")[-1]
        self.assertEqual((rec["why"], rec["who"]), ("spoke", "alice"))
        self.assertAlmostEqual(rec["energy"], 200 * E.RESTORE_PER_S, places=6)

    def test_speaking_to_an_awake_blurp_writes_nothing(self):
        srv, _ = _seat()
        self.assertFalse(E.person_spoke(srv, "alice", now=T0, free=FREE))
        self.assertEqual(_lines(srv, "wake"), [])

    def test_a_chat_message_wakes_it(self):
        from membrane import chat_io as CIO
        with _env():
            srv, _ = _seat()
            _asleep_at_zero(srv)
            with mock.patch.object(E, "_free", return_value=FREE):
                CIO.heard(srv, "alice", "hello", "ses_1")
            self.assertFalse(E.asleep(srv))
            self.assertEqual(_lines(srv, "wake")[-1]["who"], "alice")

    def test_a_spoken_message_wakes_it(self):
        from membrane import voice_io as VIO
        with _env():
            srv, _ = _seat()
            _asleep_at_zero(srv)
            with mock.patch.object(E, "_free", return_value=FREE):
                VIO.heard(srv, "alice", "hello", "ses_1")
            self.assertFalse(E.asleep(srv))

    def test_asleep_survives_a_restart(self):
        srv, d = _seat()
        _asleep_at_zero(srv)
        SL._RT.pop(id(srv), None)
        srv2, _ = _seat(d)
        self.assertTrue(E.asleep(srv2))


class TestVisible(unittest.TestCase):
    def test_the_card(self):
        with _env():
            srv, _ = _seat()
            SL.set_mode(srv, "on")
            c = SS.card(srv, now=T0)
            self.assertEqual(c["energy"], 1.0)
            self.assertFalse(c["asleep"])
            self.assertTrue(c["awake"])
            self.assertIn("pull", c)
            self.assertIn(c["limiting"], ("energy", "disk", "awake", "record"))
            _asleep_at_zero(srv)
            c = SS.card(srv, now=T0 + 2)
            self.assertTrue(c["asleep"])
            self.assertFalse(c["awake"])                     # the zoo shows it asleep
            self.assertEqual(c["mode"], "on")
            self.assertEqual(c["limiting"], "energy")

    def test_a_seat_without_self(self):
        c = SS.card(None)
        self.assertIsNone(c["energy"])
        self.assertFalse(c["asleep"])

    def test_the_sheet(self):
        with _env():
            srv, _ = _seat()
            _asleep_at_zero(srv)
            s = SS.sheet(srv, now=T0 + 2, fresh=True)
        self.assertEqual(s["energy"]["energy"], 0.0)
        self.assertTrue(s["energy"]["asleep"])
        self.assertEqual(s["energy"]["limiting"], "energy")
        self.assertIn("energy", SS.empty())


@NODE
class TestThePanelShowsIt(unittest.TestCase):
    def _html(self, sheet):
        js = ("const L=require(%r); process.stdout.write(JSON.stringify(L.selfSheetHtml(%s, {}, 0)))"
              % (str(UI / "panel.js"), json.dumps(sheet)))
        return json.loads(subprocess.check_output(["node", "-e", js]))

    def test_energy_and_sleep_in_the_header(self):
        html = self._html({"header": {"level": 1}, "energy": {"energy": 0.734, "asleep": True, "pull": 1.0,
                                                               "limiting": "disk", "record_cap": 5e9}})
        self.assertIn("energy <b>73%</b>", html)
        self.assertIn("asleep", html)
        self.assertIn("disk", html)
        self.assertNotIn("record cap unknown", html)

    def test_an_unknown_record_cap(self):
        html = self._html({"header": {"level": 1}, "energy": {"energy": 1.0, "asleep": False, "pull": 0.0,
                                                               "limiting": "energy", "record_cap": None,
                                                               "reserves": {"energy": 1.0, "disk": 1.0, "awake": 1.0,
                                                                            "record": 1.0}}})
        self.assertIn("record room: cap unknown", html)       # #359: the Sleep block says it

    def test_an_older_seat_without_energy(self):
        self.assertNotIn("energy", self._html({"header": {"level": 1}}))


if __name__ == "__main__":
    unittest.main()
