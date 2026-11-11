"""The reranker installs itself at boot (operator 2026-11-11: users' seats get it), the way
living language installs spaCy -- Home Assistant on glibc too, under HA's own pins -- except
where it cannot or would hurt: Docker (MEMBRANE_NO_AUTOINSTALL), musl (no torch wheels), too little RAM.
On Linux without an NVIDIA driver torch comes from the CPU-only index (not GBs of CUDA)."""

import unittest
from unittest import mock

from membrane import recall_rerank as RR


def plain(**kw):
    """A plain desktop: glibc Linux x86_64, 32 GB, no flags."""
    env = kw.pop("env", {})
    m = {"platform": kw.pop("platform", "linux"), "libc": kw.pop("libc", ("glibc", "2.39")),
         "release": kw.pop("release", "6.8.0-generic"), "ram_gb": kw.pop("ram_gb", 32.0),
         "nvidia": kw.pop("nvidia", False)}
    return env, m


class Skip(unittest.TestCase):
    def check(self, env, m):
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(RR.sys, "platform", m["platform"]), \
                mock.patch.object(RR, "_libc", return_value=m["libc"]), \
                mock.patch.object(RR, "_ram_gb", return_value=m["ram_gb"]), \
                mock.patch.object(RR, "_ha_constraints", return_value=m.get("ha")):
            return RR.may_install()

    def test_a_plain_desktop_installs(self):
        ok, why = self.check(*plain())
        self.assertTrue(ok, why)

    def test_a_mac_installs(self):
        ok, why = self.check(*plain(platform="darwin", libc=("", "")))
        self.assertTrue(ok, why)

    def test_home_assistant_on_glibc_installs(self):
        env, m = plain(env={"MEMBRANE_NO_AUTOINSTALL": "1"})
        m["ha"] = "/ha/package_constraints.txt"
        ok, why = self.check(env, m)
        self.assertTrue(ok, why)

    def test_a_phone_on_glibc_installs(self):
        ok, why = self.check(*plain(ram_gb=12.0))
        self.assertTrue(ok, why)

    def test_docker_does_not(self):
        ok, why = self.check(*plain(env={"MEMBRANE_NO_AUTOINSTALL": "1"}))
        self.assertFalse(ok)
        self.assertIn("installs are off", why)

    def test_rerank_1_overrides_no_autoinstall(self):
        ok, _ = self.check(*plain(env={"MEMBRANE_NO_AUTOINSTALL": "1", "MEMBRANE_RERANK": "1"}))
        self.assertTrue(ok)

    def test_musl_does_not(self):
        ok, why = self.check(*plain(libc=("", "")))
        self.assertFalse(ok)
        self.assertIn("musl", why)

    def test_low_ram_does_not(self):
        ok, why = self.check(*plain(ram_gb=RR.MIN_RAM_GB - 1))
        self.assertFalse(ok)
        self.assertIn("RAM", why)


class Install(unittest.TestCase):
    def run_ensure(self, installed_after=True, nvidia=False, platform="linux", rc=0, ha=None):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=rc, stdout="", stderr="no wheel")
        seq = iter([False, installed_after])
        with mock.patch.object(RR, "_installed", side_effect=lambda: next(seq)), \
                mock.patch.object(RR, "may_install", return_value=(True, "")), \
                mock.patch.object(RR, "_nvidia", return_value=nvidia), \
                mock.patch.object(RR, "_ha_constraints", return_value=ha), \
                mock.patch.object(RR.sys, "platform", platform), \
                mock.patch("subprocess.run", side_effect=fake_run):
            ok = RR.ensure(log=lambda *_: None)
        return ok, calls

    def test_linux_without_nvidia_takes_cpu_torch_first(self):
        ok, calls = self.run_ensure()
        self.assertTrue(ok)
        self.assertIn("https://download.pytorch.org/whl/cpu", calls[0])
        self.assertIn("torch", calls[0])
        self.assertTrue(any("sentence-transformers" in c for c in calls[-1]))

    def test_nvidia_and_mac_take_the_default_torch(self):
        for kw in ({"nvidia": True}, {"platform": "darwin"}):
            ok, calls = self.run_ensure(**kw)
            self.assertTrue(ok)
            self.assertFalse(any("download.pytorch.org" in " ".join(c) for c in calls))

    def test_home_assistant_keeps_its_pins(self):
        _ok, calls = self.run_ensure(ha="/ha/package_constraints.txt")
        for c in calls:
            self.assertEqual(c[c.index("-c") + 1], "/ha/package_constraints.txt")

    def test_wheels_only(self):
        _ok, calls = self.run_ensure()
        self.assertTrue(all("--only-binary=:all:" in c for c in calls))

    def test_a_failed_install_leaves_it_off(self):
        ok, _ = self.run_ensure(rc=1)
        self.assertFalse(ok)


class Warm(unittest.TestCase):
    def setUp(self):
        self._s = (RR._state, RR._model)
        RR._state, RR._model = "idle", None

    def tearDown(self):
        RR._state, RR._model = self._s

    def test_not_installed_but_installable_starts_the_load(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(RR, "_installed", return_value=False), \
                mock.patch.object(RR, "may_install", return_value=(True, "")), \
                mock.patch.object(RR, "_start") as st:
            RR.warm()
        st.assert_called_once()

    def test_not_installable_stays_off(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(RR, "_installed", return_value=False), \
                mock.patch.object(RR, "may_install", return_value=(False, "musl")), \
                mock.patch.object(RR, "_start") as st:
            RR.warm()
        st.assert_not_called()

    def test_the_load_installs_first_and_goes_off_when_that_fails(self):
        with mock.patch.object(RR, "_find_peer", return_value=None), \
                mock.patch.object(RR, "_installed", return_value=False), \
                mock.patch.object(RR, "ensure", return_value=False) as en:
            RR._load()
        en.assert_called_once()
        self.assertEqual(RR._state, "off")


if __name__ == "__main__":
    unittest.main()
