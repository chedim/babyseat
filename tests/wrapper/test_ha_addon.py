"""The Home Assistant add-on (addon/, repository.yaml; #1143): the seat `cliffvm serve` run by the Supervisor on Home Assistant OS / Supervised.
No Home Assistant and no Supervisor here: these test the pure parts with real processes and fakes on PATH.
  addon/options.sh      /data/options.json -> the seat's launch line and the environment (secrets only as a read of the options file, never as text)
  addon/announce.sh     waits for the door with this start's admin token, then POSTs the Supervisor discovery {service: membrane, config: {host, port, token}}
  addon/install-dist.sh the release tarball of the add-on's version: sha256 checked BEFORE unpacking, then the folder's MANIFEST.sha256
  config.yaml / repository.yaml / translations: only documented keys (a small strict reader: the test environment has no YAML library)."""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "addon"
sys.path.insert(0, str(Path(__file__).parent))
from test_install_sh import VER, release  # noqa: E402

KEY = "sk-test-SECRET-0123456789"          # an API key: must never be printed
CLAUDE_KEY = "sk-ant-SECRET-abcdef"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.NEWTOKEN.sig"  # the admin token: must never be printed or put on a command line


# ---------------------------------------------------------------------------------------------------------------- options.sh
def plan(tmp_path, opts, raw=None):
    f = tmp_path / "options.json"
    f.write_text(raw if raw is not None else json.dumps(opts))
    return subprocess.run(["sh", str(ADDON / "options.sh"), str(f)], capture_output=True, text=True, env={"PATH": os.environ["PATH"]})


def applied(tmp_path, opts):
    """eval the plan the way run.sh does; return (argv, env, plan text)"""
    r = plan(tmp_path, opts)
    assert r.returncode == 0, r.stderr
    show = 'eval "$PLAN"; for a in "$@"; do printf "%s\\0" "$a"; done; printf "\\1"; env -0'
    s = subprocess.run(["sh", "-c", show], capture_output=True, env={"PATH": os.environ["PATH"], "PLAN": r.stdout})
    assert s.returncode == 0, s.stderr
    a, e = s.stdout.split(b"\x01", 1)
    argv = [x.decode() for x in a.split(b"\0") if x]
    env = dict(x.decode().split("=", 1) for x in e.split(b"\0") if x and b"=" in x)
    return argv, env, r


BASE = ["/opt/babyseat/bin/cliffvm", "serve", "--port", "4096", "--bind", "0.0.0.0", "--data-dir", "/data"]


def facts(argv):
    assert argv[: len(BASE)] == BASE
    rest = argv[len(BASE):]
    assert len(rest) % 2 == 0 and all(x == "--fact" for x in rest[::2]), rest
    return {k: json.loads(v) for k, v in (x.split("=", 1) for x in rest[1::2])}


def test_empty_options_are_the_seats_own_defaults(tmp_path):
    argv, env, _ = applied(tmp_path, {})
    assert facts(argv) == {}
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_BIN" not in env and "OPENAI_API_KEY" not in env


def test_the_name_is_a_json_string_fact(tmp_path):
    argv, _, _ = applied(tmp_path, {"seat_name": "Blurp 'the' \"seat\""})
    assert facts(argv) == {"seat_name": "Blurp 'the' \"seat\""}
    argv, _, _ = applied(tmp_path, {"seat_name": "42"})          # a name that looks like a number stays text
    assert facts(argv) == {"seat_name": "42"}


def test_openai_becomes_one_cloud_models_entry_and_the_key_an_environment_variable(tmp_path):
    argv, env, r = applied(tmp_path, {"provider": "openai", "base_url": "https://api.example.com/", "model": "gpt-x", "api_key": KEY})
    f = facts(argv)
    assert f["cloud_models"] == [{"name": "gpt-x", "backend": "openai", "base_url": "https://api.example.com/", "api_key_env": "OPENAI_API_KEY"}]
    assert f["minds"]["loops"] == "gpt-x" and f["minds"]["seat_default"] == "gpt-x"
    assert env["OPENAI_API_KEY"] == KEY
    assert KEY not in r.stdout + r.stderr and all(KEY not in a for a in argv)


def test_openai_without_a_key_sends_none(tmp_path):
    argv, env, _ = applied(tmp_path, {"provider": "openai", "base_url": "http://lmstudio.local:1234", "model": "m"})
    assert facts(argv)["cloud_models"] == [{"name": "m", "backend": "openai", "base_url": "http://lmstudio.local:1234"}]
    assert "OPENAI_API_KEY" not in env


def test_ollama_becomes_seat_backend_and_no_key(tmp_path):
    argv, env, _ = applied(tmp_path, {"provider": "ollama", "ollama_url": "http://192.168.1.5:11434", "model": "llama3.2", "api_key": KEY})
    f = facts(argv)
    assert f["seat_backend"] == "http://192.168.1.5:11434" and f["minds"]["loops"] == "llama3.2"
    assert "cloud_models" not in f and "OPENAI_API_KEY" not in env


def test_claude_takes_the_users_executable_and_key_by_environment(tmp_path):
    exe = tmp_path / "claude"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    argv, env, r = applied(tmp_path, {"provider": "claude", "claude_path": str(exe), "anthropic_api_key": CLAUDE_KEY, "api_key": KEY})
    assert facts(argv) == {}
    assert env["CLAUDE_BIN"] == str(exe) and env["ANTHROPIC_API_KEY"] == CLAUDE_KEY and "OPENAI_API_KEY" not in env
    assert CLAUDE_KEY not in r.stdout + r.stderr and all(CLAUDE_KEY not in a for a in argv)


def test_claude_without_an_executable_still_starts_and_says_what_is_missing(tmp_path):
    argv, env, r = applied(tmp_path, {"provider": "claude"})
    assert facts(argv) == {} and "CLAUDE_BIN" not in env
    assert "claude_path" in r.stderr


@pytest.mark.parametrize("opts, says", [
    ({"provider": "gemini"}, "provider"),
    ({"provider": "openai", "model": "m"}, "base_url"),
    ({"provider": "openai", "base_url": "ftp://x", "model": "m"}, "base_url"),
    ({"provider": "openai", "base_url": "https://x"}, "model"),
    ({"provider": "ollama", "ollama_url": "", "model": "m"}, "ollama_url"),
    ({"provider": "ollama", "ollama_url": "http://h:11434"}, "model"),
    ({"provider": "claude", "claude_path": "claude"}, "claude_path"),
    ({"provider": "claude", "claude_path": "/nonexistent/claude"}, "claude_path"),
])
def test_bad_values_are_refused_with_the_option_named(tmp_path, opts, says):
    r = plan(tmp_path, opts)
    assert r.returncode != 0 and r.stdout == ""
    assert says in r.stderr and r.stderr.startswith("babyseat add-on:")


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", ""])
def test_an_unreadable_options_file_is_refused(tmp_path, raw):
    r = plan(tmp_path, None, raw=raw)
    assert r.returncode != 0 and r.stdout == "" and "options" in r.stderr


def test_a_secret_is_never_in_the_plan_even_on_a_refusal(tmp_path):
    r = plan(tmp_path, {"provider": "openai", "api_key": KEY, "anthropic_api_key": CLAUDE_KEY})
    assert r.returncode != 0 and KEY not in r.stdout + r.stderr and CLAUDE_KEY not in r.stdout + r.stderr


# ---------------------------------------------------------------------------------------------------------------- announce.sh
FAKE_CURL = r'''#!/usr/bin/env python3
"""a fake curl: logs its argv and the contents of every @file it is handed; answers health by FAKE_HEALTH (a code, or 'token' = 200 only with the right token)"""
import json, os, sys
a = sys.argv[1:]
url = [x for x in a if x.startswith("http")][-1]
files = {x[1:]: open(x[1:]).read() for x in a if x.startswith("@") and os.path.exists(x[1:])}
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"argv": a, "files": files}) + "\n")
if url.endswith("/global/health"):
    mode = os.environ.get("FAKE_HEALTH", "200")
    hdr = "".join(files.values())
    code = ("200" if os.environ["FAKE_TOKEN"] in hdr else "401") if mode == "token" else mode
    sys.stdout.write(code)
    sys.exit(0)
if url.endswith("/discovery"):
    if os.environ.get("FAKE_DISCOVERY") == "fail":
        sys.stdout.write("500"); sys.exit(0)
    sys.stdout.write("200"); sys.exit(0)
sys.exit(7)
'''


def announce(tmp_path, token=TOKEN, old=None, health="token", sup_token="SUPTOKEN", discovery="ok"):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    (b / "curl").write_text(FAKE_CURL)
    (b / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for x in ("curl", "sleep"):
        (b / x).chmod(0o755)
    tok = tmp_path / "admin.token"
    if token is not None:
        tok.write_text(token + "\n")
    log = tmp_path / "curl.log"
    env = {"PATH": f"{b}:{os.environ['PATH']}", "FAKE_LOG": str(log), "FAKE_HEALTH": health, "FAKE_TOKEN": token or "-", "FAKE_DISCOVERY": discovery}
    if sup_token:
        env["SUPERVISOR_TOKEN"] = sup_token
    args = ["sh", str(ADDON / "announce.sh"), str(tok)] + ([old] if old is not None else [])
    r = subprocess.run(args, capture_output=True, text=True, env=env)
    calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return r, calls


def test_the_discovery_payload_is_membrane_host_port_token(tmp_path):
    r, calls = announce(tmp_path)
    assert r.returncode == 0, r.stderr
    post = [c for c in calls if c["argv"][-1] == "http://supervisor/discovery"]
    assert len(post) == 1
    body = [v for v in post[0]["files"].values() if v.lstrip().startswith("{")]
    assert len(body) == 1
    host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()
    assert json.loads(body[0]) == {"service": "membrane", "config": {"host": host, "port": 4096, "token": TOKEN}}
    assert any("Authorization: Bearer SUPTOKEN" in v for v in post[0]["files"].values())


def test_no_token_on_a_command_line_or_in_the_output(tmp_path):
    r, calls = announce(tmp_path)
    assert r.returncode == 0
    assert TOKEN not in r.stdout + r.stderr and "SUPTOKEN" not in r.stdout + r.stderr
    assert all(TOKEN not in x and "SUPTOKEN" not in x for c in calls for x in c["argv"])
    # the health check presented this start's token (from a file, not argv)
    assert any(c["argv"][-1].endswith("/global/health") and any(TOKEN in v for v in c["files"].values()) for c in calls)


def test_the_temporary_files_are_gone_afterwards(tmp_path):
    r, calls = announce(tmp_path)
    assert r.returncode == 0
    for c in calls:
        for p in c["files"]:
            assert not Path(p).exists(), p


def test_it_waits_for_a_new_token_when_the_old_one_is_still_there(tmp_path):
    # the token file still holds the previous start's token: the fingerprint says so, nothing is announced, and it gives up (bounded)
    old = subprocess.run(["sh", "-c", f"printf '%s\\n' '{TOKEN}' | cksum"], capture_output=True, text=True).stdout.strip()
    r, calls = announce(tmp_path, old=old)
    assert r.returncode != 0
    assert not [c for c in calls if c["argv"][-1].endswith("/discovery")]
    assert "not announced" in r.stderr and TOKEN not in r.stderr


def test_a_door_that_never_answers_gives_up_bounded(tmp_path):
    r, calls = announce(tmp_path, health="000")
    assert r.returncode != 0 and "not announced" in r.stderr
    assert 0 < len(calls) <= 200 and not [c for c in calls if c["argv"][-1].endswith("/discovery")]


def test_without_the_supervisor_nothing_is_sent(tmp_path):
    r, calls = announce(tmp_path, sup_token=None)
    assert r.returncode == 0 and calls == [] and "Supervisor" in r.stderr


def test_a_refused_discovery_is_reported_without_the_token(tmp_path):
    r, _ = announce(tmp_path, discovery="fail")
    assert r.returncode != 0 and "500" in r.stderr and TOKEN not in r.stderr


# ---------------------------------------------------------------------------------------------------------------- install-dist.sh
def install(tmp_path, *args):
    dest = tmp_path / "opt" / "babyseat"
    r = subprocess.run(["sh", str(ADDON / "install-dist.sh"), "--version", VER, "--arch", "aarch64", "--dest", str(dest), *args],
                       capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
    return r, dest


def _retarget(rel):
    """the add-on takes the MUSL tarball: rename the fixture's tarball (and its sidecar) to the aarch64 musl name"""
    src = next(rel.glob("*.tar.gz"))
    dst = rel / f"babyseat-{VER}-aarch64-unknown-linux-musl.tar.gz"
    src.rename(dst)
    sc = rel / (src.name + ".sha256")
    if sc.exists():
        h = sc.read_text().split()[0]
        sc.unlink()
        (rel / (dst.name + ".sha256")).write_text(f"{h}  {dst.name}\n")
    return rel


def test_install_from_a_release_url_checks_and_unpacks(tmp_path):
    rel = _retarget(release(tmp_path))
    r, dest = install(tmp_path, "--base-url", f"file://{rel}")
    assert r.returncode == 0, r.stderr
    assert (dest / "VERSION").read_text().strip() == VER and os.access(dest / "bin" / "cliffvm", os.X_OK)


def test_install_from_a_local_dist_folder(tmp_path):
    rel = _retarget(release(tmp_path))
    r, dest = install(tmp_path, "--local", str(rel))
    assert r.returncode == 0, r.stderr
    assert (dest / "VERSION").read_text().strip() == VER


@pytest.mark.parametrize("kw", [{"bad_sidecar": True}, {"sidecar": False}, {"tamper_inner": True}])
def test_install_refuses_a_tarball_that_does_not_check(tmp_path, kw):
    rel = _retarget(release(tmp_path, **kw))
    r, dest = install(tmp_path, "--local", str(rel))
    assert r.returncode != 0 and not dest.exists()


def test_install_knows_only_the_two_architectures(tmp_path):
    r = subprocess.run(["sh", str(ADDON / "install-dist.sh"), "--version", VER, "--arch", "armv7", "--dest", str(tmp_path / "d")], capture_output=True, text=True)
    assert r.returncode != 0 and "armv7" in r.stderr


# ---------------------------------------------------------------------------------------------------------------- the files
def yaml_map(text):
    """a strict reader for the YAML subset these files use: `key: value` maps nested by two spaces, `- item` lists of scalars or of maps.
    Comments and blank lines are skipped. Anything else fails, so a file that grows past it is noticed here."""
    lines = []
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        ind = len(ln) - len(ln.lstrip(" "))
        assert ind % 2 == 0, f"odd indent: {ln!r}"
        lines.append((ind, ln.strip()))

    def scalar(v):
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            return v[1:-1]
        if v in ("true", "false"):
            return v == "true"
        if re.fullmatch(r"-?\d+", v):
            return int(v)
        return v

    def block(i, ind):
        if i < len(lines) and lines[i][1].startswith("- "):
            out = []
            while i < len(lines) and lines[i][0] == ind and lines[i][1].startswith("- "):
                item = lines[i][1][2:]
                if re.match(r"^[A-Za-z0-9_./-]+:( |$)", item):
                    lines[i] = (ind + 2, item)
                    m, i = block(i, ind + 2)
                    out.append(m)
                else:
                    out.append(scalar(item))
                    i += 1
            return out, i
        out = {}
        while i < len(lines) and lines[i][0] == ind:
            m = re.match(r'^("[^"]+"|[A-Za-z0-9_./-]+):(?: (.*))?$', lines[i][1])
            assert m, f"not a key line: {lines[i][1]!r}"
            k = m.group(1).strip('"')
            assert k not in out, f"duplicate key {k}"
            if m.group(2) not in (None, ""):
                out[k] = scalar(m.group(2))
                i += 1
            else:
                i += 1
                if i < len(lines) and (lines[i][0] > ind or (lines[i][0] == ind and lines[i][1].startswith("- "))):
                    out[k], i = block(i, lines[i][0])
                else:
                    out[k] = None
        return out, i

    m, i = block(0, 0)
    assert i == len(lines), f"unread line: {lines[i]}"
    return m


# config.yaml keys of https://contributors.home-assistant.io/apps/reference/configuration/ (2026-11-15); unknown keys are IGNORED by the Supervisor, so a typo is silent: refuse it here
DOCUMENTED = {
    "name", "version", "slug", "description", "arch", "url", "init", "startup", "boot", "stage", "timeout", "environment", "legacy", "stdin", "tmpfs",
    "breaking_versions", "homeassistant", "machine", "image", "options", "schema", "ports", "ports_description", "host_network", "webui", "watchdog",
    "ingress", "ingress_port", "ingress_entry", "ingress_stream", "panel_icon", "panel_title", "panel_admin", "map", "homeassistant_api", "hassio_api",
    "auth_api", "docker_api", "hassio_role", "services", "discovery", "backup", "backup_pre", "backup_post", "backup_exclude", "apparmor",
}
SCHEMA_TYPE = re.compile(r"^(str|password|bool|int|float|email|url|port|match\(.+\)|list\([^)]+\)|device(\(.+\))?)(\(\d*,\d*\))?\??$")


@pytest.fixture(scope="module")
def config():
    return yaml_map((ADDON / "config.yaml").read_text())


def test_config_has_the_required_keys_and_only_documented_ones(config):
    assert {"name", "version", "slug", "description", "arch"} <= set(config)
    assert set(config) <= DOCUMENTED, set(config) - DOCUMENTED
    assert config["arch"] == ["aarch64", "amd64"]
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", config["slug"])


def test_config_version_is_the_package_version(config):
    assert config["version"] == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert re.search(r'(?m)^version: "[0-9.]+"$', (ADDON / "config.yaml").read_text()), "the version is quoted (YAML would read 1.10 as a number)"


def test_config_announces_membrane_and_maps_the_ha_config_read_only_at_config(config):
    assert config["discovery"] == ["membrane"]
    assert config["map"] == [{"type": "homeassistant_config", "path": "/config"}]   # read_only defaults to true; /config is where the seat's cutover search looks
    assert "hassio_api" not in config and "homeassistant_api" not in config  # discovery needs neither
    # the add-on linter (frenck/action-app-linter src/lint.py) refuses `watchdog` (obsolete: the image's HEALTHCHECK) and any key set to its default value
    assert "watchdog" not in config
    assert re.search(r"(?m)^HEALTHCHECK .*\n    CMD .*/global/health", (ADDON / "Dockerfile").read_text())
    for k, v in {"startup": "application", "boot": "auto", "init": True, "hassio_api": False, "homeassistant_api": False, "host_network": False, "ingress": False,
                 "timeout": 10, "stage": "stable", "backup": "hot", "apparmor": True}.items():
        assert config.get(k, object()) != v, f"{k}: {v} is the default (the linter refuses it)"
    assert "4096/tcp" in config["ports"]
    assert "init" not in config   # true by default: the image's ENTRYPOINT is run.sh, not the base image's s6 /init: Docker's init forwards the stop signal


def test_options_and_schema_and_translations_agree(config):
    schema, options = config["schema"], config["options"]
    assert set(options) <= set(schema)
    for k, t in schema.items():
        assert SCHEMA_TYPE.match(str(t)), (k, t)
        if not str(t).endswith("?"):
            assert k in options, f"{k} is required and has no default"
    assert schema["provider"] == "list(claude|openai|ollama)"
    for k in ("api_key", "anthropic_api_key"):
        assert schema[k].startswith("password"), k
    tr = yaml_map((ADDON / "translations" / "en.yaml").read_text())
    assert set(tr) == {"configuration"} and set(tr["configuration"]) == set(schema)
    assert all(set(v) == {"name", "description"} for v in tr["configuration"].values())
    assert set(config["ports_description"]) == set(config["ports"])


def test_options_sh_reads_only_options_the_schema_declares(config):
    used = set(re.findall(r"\bget (\w+)", (ADDON / "options.sh").read_text())) | set(re.findall(r"has_value (\w+)", (ADDON / "options.sh").read_text()))
    assert used and used <= set(config["schema"]), used - set(config["schema"])


def test_repository_yaml_names_the_repository():
    repo = yaml_map((ROOT / "repository.yaml").read_text())
    assert set(repo) <= {"name", "url", "maintainer"} and repo["name"]
    assert repo["url"] == "https://github.com/chedim/membrane"


def test_the_add_on_folder_has_its_files():
    for f in ("config.yaml", "Dockerfile", "run.sh", "options.sh", "announce.sh", "install-dist.sh", "DOCS.md", "README.md", "icon.png", "translations/en.yaml"):
        assert (ADDON / f).is_file(), f
    assert not (ADDON / "build.yaml").exists(), "build.yaml is deprecated (the Dockerfile carries FROM and the ARGs)"
    for f in ("run.sh", "options.sh", "announce.sh", "install-dist.sh"):
        assert (ADDON / f).stat().st_mode & stat.S_IXUSR, f"{f} is executable"
    assert (ADDON / "icon.png").read_bytes() == (ROOT / "custom_components" / "membrane" / "brand" / "icon.png").read_bytes()


def test_the_dockerfile_builds_on_a_pinned_base_with_a_default_and_installs_no_claude():
    d = (ADDON / "Dockerfile").read_text()
    assert re.search(r"(?m)^ARG BUILD_FROM=ghcr\.io/home-assistant/base:\d+\.\d+$", d), "Supervisor 2026.04+ passes no BUILD_FROM: it needs a pinned default"
    assert re.search(r"(?m)^FROM \$\{BUILD_FROM\}", d)
    assert "install-dist.sh" in d and re.search(r"(?m)^ENTRYPOINT \[\"/opt/babyseat-addon/run.sh\"\]$", d)
    assert "claude" not in re.sub(r"(?m)^#.*$", "", d).lower(), "the claude CLI is never installed in the image"


def test_run_sh_execs_the_seat_and_announces_in_the_background():
    t = (ADDON / "run.sh").read_text()
    assert re.search(r'(?m)^exec "\$@"$', t)
    assert "announce.sh" in t and "options.sh" in t and "cksum" in t


def test_validate_shellchecks_the_add_on_scripts():
    wf = (ROOT / ".github" / "workflows" / "validate.yml").read_text()
    line = next(x for x in wf.splitlines() if "shellcheck -S warning" in x)
    assert "addon/*.sh" in line


def test_secrets_are_not_written_into_the_add_on_files():
    for p in ADDON.rglob("*"):
        if p.is_file() and p.suffix in (".sh", ".yaml", ".md", "") and p.name != "icon.png":
            t = p.read_text()
            assert not re.search(r"sk-[A-Za-z0-9-]{16,}", t), p
