"""The seat the component runs inside Home Assistant (#1143, operator 2026-11-15): seat_runner.py. (a) the host -> the release's target; (b) the release
tarball of the component's own version, downloaded, its sha256 checked BEFORE unpacking, every file checked against MANIFEST.sha256, a failure leaves
nothing; (c) the process: started with the secrets in its environment only, ready when /global/health answers with the admin token, restarted with a
backoff, given up after too many quick failures, stopped with SIGTERM then a kill. Everything here is loopback and a FAKE cliffvm (tests/ha/fakedist.py):
never the real seat, never a download from the internet."""
import asyncio
import importlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest

from .fakedist import ReleaseServer, make_dist, make_release

VER = "9.8.7"
TARGET = "aarch64-apple-darwin"
CLAUDE_KEY = "sk-ant-SECRET-abcdef"


@pytest.fixture
def sr(ha):
    return importlib.import_module("cc_membrane.seat_runner")


def blocking(fn, *args):
    return asyncio.get_running_loop().run_in_executor(None, fn, *args)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                   # a zombie of this test process is not alive
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


# ---- (a) the platform ------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("system, machine, target", [
    ("Linux", "x86_64", "x86_64-unknown-linux-musl"), ("Linux", "amd64", "x86_64-unknown-linux-musl"),
    ("Linux", "aarch64", "aarch64-unknown-linux-musl"), ("Linux", "arm64", "aarch64-unknown-linux-musl"),
    ("Darwin", "arm64", "aarch64-apple-darwin"), ("Darwin", "x86_64", "x86_64-apple-darwin"),
])
def test_the_host_maps_to_the_static_musl_build_on_linux_and_the_apple_build_on_macos(sr, system, machine, target):
    assert sr.release_target(system, machine) == target


@pytest.mark.parametrize("system, machine", [("Linux", "armv7l"), ("Linux", "i686"), ("Windows", "AMD64"), ("FreeBSD", "amd64"), ("Linux", "riscv64")])
def test_an_unknown_host_is_a_clear_error_naming_it(sr, system, machine):
    with pytest.raises(sr.UnsupportedPlatform) as e:
        sr.release_target(system, machine)
    assert system in str(e.value) and machine in str(e.value) and "x86_64" in str(e.value) and "aarch64" in str(e.value)


def test_the_asset_name_and_the_release_base_are_the_ones_the_release_publishes(sr):
    assert sr.asset_name("0.7.61", "x86_64-unknown-linux-musl") == "babyseat-0.7.61-x86_64-unknown-linux-musl.tar.gz"
    assert sr.RELEASE_BASE.format(version="0.7.61") == "https://github.com/chedim/membrane/releases/download/v0.7.61"
    sh = (Path(__file__).resolve().parents[2] / "addon" / "install-dist.sh").read_text()
    assert "base=https://github.com/chedim/membrane/releases/download/v$version" in sh and "asset=babyseat-$version-$target.tar.gz" in sh


def test_the_component_version_is_the_manifests(sr):
    man = json.loads((Path(__file__).resolve().parents[2] / "custom_components" / "membrane" / "manifest.json").read_text())
    assert sr.component_version() == man["version"]


# ---- (b) the download and its checks ---------------------------------------------------------------------------------------------------------------

def _ensure(sr, run, rel, app, version=VER, target=TARGET):
    async def go():
        async with ReleaseServer(rel) as srv, aiohttp.ClientSession() as s:
            try:
                return await sr.ensure_dist(s, blocking, app, version, target, srv.url), srv.asked
            except sr.DistError as e:
                return e, srv.asked
    return run(go())


def _leftovers(app: Path):
    return sorted(p.name for p in app.iterdir()) if app.exists() else []


def test_a_good_release_is_unpacked_to_app_version_with_the_exec_bit_and_not_downloaded_again(sr, run, tmp_path):
    rel = make_release(tmp_path, VER, TARGET)
    app = tmp_path / "config" / "membrane" / "app"
    dist, asked = _ensure(sr, run, rel, app)
    assert dist == app / VER and (dist / "VERSION").read_text().strip() == VER
    assert os.access(dist / "bin" / "cliffvm", os.X_OK)
    assert asked == [f"/babyseat-{VER}-{TARGET}.tar.gz", f"/babyseat-{VER}-{TARGET}.tar.gz.sha256"]
    assert _leftovers(app) == [VER], "no staging folder is left"
    again, asked = _ensure(sr, run, rel, app)
    assert again == dist and asked == [], "a verified folder is used as it is: no download at a restart"


def test_a_wrong_sha256_installs_nothing(sr, run, tmp_path):
    rel = make_release(tmp_path, VER, TARGET, bad_sha=True)
    app = tmp_path / "app"
    err, _ = _ensure(sr, run, rel, app)
    assert isinstance(err, sr.DistError) and "sha256" in str(err) and "nothing installed" in str(err)
    assert _leftovers(app) == []


def test_a_file_changed_after_the_manifest_installs_nothing(sr, run, tmp_path):
    rel = make_release(tmp_path, VER, TARGET, tamper_inner=True)
    app = tmp_path / "app"
    err, _ = _ensure(sr, run, rel, app)
    assert isinstance(err, sr.DistError) and "MANIFEST.sha256" in str(err) and "packs/boot.c1f" in str(err)
    assert _leftovers(app) == []


def test_no_sidecar_or_no_tarball_for_the_target_installs_nothing(sr, run, tmp_path):
    rel = make_release(tmp_path, VER, TARGET, sidecar=False)
    app = tmp_path / "app"
    err, _ = _ensure(sr, run, rel, app)
    assert isinstance(err, sr.DistError) and ".sha256" in str(err) and "404" in str(err)
    err, asked = _ensure(sr, run, rel, app, target="x86_64-unknown-linux-musl")
    assert isinstance(err, sr.DistError) and "x86_64-unknown-linux-musl" in str(err) and asked == [f"/babyseat-{VER}-x86_64-unknown-linux-musl.tar.gz"]
    assert _leftovers(app) == []


def test_a_folder_of_another_version_installs_nothing(sr, run, tmp_path):
    rel = make_release(tmp_path, VER, TARGET, wrong_version="1.0.0")
    app = tmp_path / "app"
    err, _ = _ensure(sr, run, rel, app)
    assert isinstance(err, sr.DistError) and "1.0.0" in str(err)
    assert _leftovers(app) == []


def test_a_member_outside_the_folder_is_refused(sr, run, tmp_path):
    rel = make_release(tmp_path, VER, TARGET, extra_member=("../escaped.txt", b"x"))
    app = tmp_path / "cfg" / "app"
    err, _ = _ensure(sr, run, rel, app)
    assert isinstance(err, sr.DistError)
    assert not (tmp_path / "cfg" / "escaped.txt").exists() and not (app / "escaped.txt").exists()
    assert _leftovers(app) == []


def test_an_installed_folder_that_was_changed_is_downloaded_again(sr, run, tmp_path):
    rel = make_release(tmp_path, VER, TARGET)
    app = tmp_path / "app"
    dist, _ = _ensure(sr, run, rel, app)
    (dist / "packs" / "boot.c1f").write_text("changed on disk\n")
    again, asked = _ensure(sr, run, rel, app)
    assert again == dist and len(asked) == 2 and (dist / "packs" / "boot.c1f").read_text() == "PACK boot 1\n"


# ---- (c) the process -------------------------------------------------------------------------------------------------------------------------------

SETTINGS = {"provider": "anthropic", "anthropic_api_key": CLAUDE_KEY}


def _seat(sr, tmp_path, want=None, **kw):
    dist = make_dist(tmp_path / "dist" / VER, VER)
    data = tmp_path / "config" / "membrane"
    data.mkdir(parents=True, exist_ok=True)
    if want is not None:
        (data / "fake.json").write_text(json.dumps(want))
    return sr.SeatProcess(dist / "bin" / "cliffvm", data, sr.free_port(0), dict(SETTINGS), run_blocking=blocking, **kw), data


def _runs(data: Path) -> list:
    f = data / "fake-runs.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def _fast(p, **kw):
    for k, v in {**dict(BOOT_TIMEOUT_S=10, HEALTH_POLL_S=0.05, STOP_GRACE_S=2, BACKOFF_FIRST_S=0.05, BACKOFF_MAX_S=0.2, STABLE_RUN_S=60, MAX_QUICK_FAILURES=3), **kw}.items():
        setattr(p, k, v)
    return p


def test_the_seat_starts_with_secrets_only_in_its_environment_and_stops_with_sigterm(sr, run, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "ha-supervisor-secret")          # Home Assistant OS gives HA one: the seat must not hold it
    monkeypatch.setenv("HASS_TOKEN_SOMETHING", "x")
    caplog.set_level(logging.DEBUG)

    async def go():
        async with aiohttp.ClientSession() as s:
            p, data = _seat(sr, tmp_path, session=s)
            _fast(p)
            await p.start()
            r = _runs(data)[-1]
            async with s.get(p.url + "/global/health", headers={"Authorization": f"Bearer {p.token}"}) as h:
                status = h.status
            pid = p.pid
            await p.stop()
            return p, data, r, status, pid
    p, data, r, status, pid = run(go())
    assert status == 200 and p.url == f"http://127.0.0.1:{p.port}" and p.token == f"tok-{pid}"
    argv = r["argv"]
    assert argv[1:8] == ["serve", "--port", str(p.port), "--bind", "127.0.0.1", "--data-dir", str(data)]
    assert all(CLAUDE_KEY not in a for a in argv), "a secret is never an argument"
    assert r["env"]["ANTHROPIC_API_KEY"] == CLAUDE_KEY
    assert "SUPERVISOR_TOKEN" not in r["env"] and "HASS_TOKEN_SOMETHING" not in r["env"]
    assert r["env"]["HOME"] == str(data / "home") and r["cwd"] == str(data)
    assert not alive(pid), "stopped"
    assert not (data / "seat.pid").exists()
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "cliffvm serve: data" in logged, "the seat's lines reach the logger"
    assert CLAUDE_KEY not in logged and "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9" not in logged and p.token not in logged, "no secret is logged"


def test_a_seat_that_dies_at_start_is_a_start_error_with_its_words_and_no_secret(sr, run, tmp_path):
    async def go():
        async with aiohttp.ClientSession() as s:
            p, data = _seat(sr, tmp_path, {"mode": "exit", "code": 3}, session=s)
            _fast(p)
            try:
                await p.start()
            except sr.SeatStartError as e:
                return str(e)
    err = run(go())
    assert err and "3" in err and "the fake was told to fail" in err
    assert CLAUDE_KEY not in err and "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9" not in err


def test_a_seat_that_never_answers_health_is_killed_after_the_boot_timeout(sr, run, tmp_path):
    async def go():
        async with aiohttp.ClientSession() as s:
            p, data = _seat(sr, tmp_path, {"mode": "nohealth"}, session=s)
            _fast(p, BOOT_TIMEOUT_S=1.5)
            t = time.monotonic()
            try:
                await p.start()
            except sr.SeatStartError as e:
                return str(e), time.monotonic() - t, _runs(data)[-1]["pid"]
    err, took, pid = run(go())
    assert "health" in err and took < 8
    assert not alive(pid)


def test_a_seat_that_ignores_sigterm_is_killed(sr, run, tmp_path):
    async def go():
        async with aiohttp.ClientSession() as s:
            p, data = _seat(sr, tmp_path, {"mode": "ignore_term"}, session=s)
            _fast(p, STOP_GRACE_S=0.5)
            await p.start()
            pid = p.pid
            t = time.monotonic()
            await p.stop()
            return pid, time.monotonic() - t
    pid, took = run(go())
    assert not alive(pid) and took < 5


def test_a_crashing_seat_is_restarted_with_a_growing_backoff_then_given_up(sr, run, tmp_path):
    gave = []

    async def go():
        async with aiohttp.ClientSession() as s:
            p, data = _seat(sr, tmp_path, {"mode": "ok", "exit_after": 0.8, "code": 5}, session=s, on_give_up=gave.append)
            _fast(p, BACKOFF_FIRST_S=0.1, BACKOFF_MAX_S=0.4, MAX_QUICK_FAILURES=3)
            await p.start()
            t = time.monotonic()
            while not gave and time.monotonic() - t < 30:
                await asyncio.sleep(0.1)
            delays = list(p.delays)
            await p.stop()
            return p, data, delays
    p, data, delays = run(go())
    runs = _runs(data)
    assert gave and "5" in gave[0] and CLAUDE_KEY not in gave[0], gave
    assert len(runs) == 3, "the first start and two restarts, then it gives up (3 quick failures)"
    assert delays == [0.1, 0.2], delays
    assert p.gave_up and all(not alive(r["pid"]) for r in runs)


def test_backoff_doubles_up_to_its_cap(sr):
    p = sr.SeatProcess("/x", "/d", 1, {}, run_blocking=blocking)
    p.BACKOFF_FIRST_S, p.BACKOFF_MAX_S = 5, 60
    assert [p.backoff(n) for n in (1, 2, 3, 4, 5, 6)] == [5, 10, 20, 40, 60, 60]


def test_a_seat_that_ran_long_enough_is_restarted_without_counting_toward_giving_up(sr, run, tmp_path):
    gave = []

    async def go():
        async with aiohttp.ClientSession() as s:
            p, data = _seat(sr, tmp_path, {"mode": "ok", "exit_after": 0.8, "code": 0}, session=s, on_give_up=gave.append)
            _fast(p, STABLE_RUN_S=0.5, MAX_QUICK_FAILURES=2, BACKOFF_FIRST_S=0.05)
            await p.start()
            t = time.monotonic()
            while len(_runs(data)) < 4 and time.monotonic() - t < 30:
                await asyncio.sleep(0.1)
            await p.stop()
            return data
    data = run(go())
    assert len(_runs(data)) >= 4 and not gave


def test_the_token_is_refreshed_with_a_minted_one(sr, run, tmp_path):
    got = []

    async def go():
        async with aiohttp.ClientSession() as s:
            p, data = _seat(sr, tmp_path, session=s, on_token=got.append)
            _fast(p, TOKEN_REFRESH_S=0.3)
            await p.start()
            t = time.monotonic()
            while len(got) < 2 and time.monotonic() - t < 15:
                await asyncio.sleep(0.1)
            await p.stop()
            return p
    p = run(go())
    assert got[:2] == ["minted-1", "minted-2"] and p.token == got[-1]


def test_an_orphan_seat_of_this_data_folder_is_stopped_before_a_start_and_another_process_is_left_alone(sr, run, tmp_path):
    p, data = _seat(sr, tmp_path)
    _fast(p, STOP_GRACE_S=1)            # the orphan here is this test's child: it stays a zombie until the test reaps it
    (data / "fake.json").write_text(json.dumps({"mode": "ok"}))
    port = sr.free_port(0)
    orphan = subprocess.Popen([str(p.binary), "serve", "--port", str(port), "--bind", "127.0.0.1", "--data-dir", str(data)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        t = time.time()
        while not (data / "admin.token").exists() and time.time() - t < 10:
            time.sleep(0.05)
        (data / "seat.pid").write_text(f"{orphan.pid}\n")
        run(p.reap_orphan())
        assert orphan.wait(10) is not None
        (data / "seat.pid").write_text(f"{other.pid}\n")
        run(p.reap_orphan())
        assert other.poll() is None, "a process that is not this folder's seat is never touched"
        assert not (data / "seat.pid").exists()
    finally:
        for c in (orphan, other):
            if c.poll() is None:
                c.kill()
                c.wait()


def test_the_port_is_kept_when_free_and_another_is_chosen_when_taken(sr):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    taken = s.getsockname()[1]
    try:
        assert not sr.port_free(taken)
        other = sr.free_port(taken)
        assert other != taken and sr.port_free(other)
    finally:
        s.close()
    assert sr.free_port(taken) == taken


def test_redact_hides_the_secrets_and_anything_token_shaped(sr):
    line = "key sk-ant-SECRET-abcdef tok eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.c2ln and tok-123 ok"
    out = sr.redact(line, ["tok-123", ""])
    assert "SECRET" not in out and "eyJhbGci" not in out and "tok-123" not in out and out.endswith("ok")
