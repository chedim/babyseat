"""THE PUBLIC IMAGE (#202): the Docker packaging is checked without Docker.

The entrypoint is run under /bin/sh with a stub `membrane` on PATH that
prints its argv, so the env -> flag mapping is asserted on the real script.
The .dockerignore and Dockerfile are read as text: the build context must be
an allowlist (a dev tree's root holds .membrane/, field dumps, logs and auth
files that must never reach a public image), and no ENV may bake in a
site-specific value.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "docker" / "docker-entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

SITE_ENV = ("MEMBRANE_BACKEND", "MEMBRANE_MODEL", "MEMBRANE_EMBED_MODEL")


def _run_entrypoint(tmp_path, args=(), env=None):
    """Run the entrypoint with a stub `membrane` that prints one argv item
    per line; returns the argv list the seat would have received."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "membrane"
    stub.write_text('#!/bin/sh\necho "PID=$$"\nfor a in "$@"; do echo "$a"; done\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    e = {k: v for k, v in os.environ.items() if k not in SITE_ENV}
    e["PATH"] = f"{bindir}:/usr/bin:/bin"
    e.update(env or {})
    # `exec sh -c` -> the entrypoint's own PID is the shell's PID; with exec
    # the stub inherits it
    out = subprocess.run(
        ["/bin/sh", "-c", 'echo "SHELL=$$"; exec /bin/sh "$0" "$@"',
         str(ENTRYPOINT), *args],
        env=e, capture_output=True, text=True, timeout=10, check=True)
    lines = out.stdout.splitlines()
    shell_pid = lines[0].split("=", 1)[1]
    stub_pid = lines[1].split("=", 1)[1]
    return shell_pid, stub_pid, lines[2:]


def _flag(argv, name):
    assert name in argv, f"{name} missing from {argv}"
    return argv[argv.index(name) + 1]


# ---- the entrypoint ----

def test_entrypoint_default_cmd_is_standalone_with_pinned_ports(tmp_path):
    _, _, argv = _run_entrypoint(tmp_path, ["standalone"])
    assert argv[0] == "standalone"
    assert _flag(argv, "--listen") == "11500"
    assert _flag(argv, "--serve-port") == "4096"
    assert _flag(argv, "--serve-host") == "0.0.0.0"
    # generic default backend: the Docker host, never a site address
    assert _flag(argv, "--backend") == "http://host.docker.internal:11434"
    assert "--model" not in argv
    assert "--embed-model" not in argv


def test_entrypoint_maps_each_env_to_its_flag(tmp_path):
    _, _, argv = _run_entrypoint(tmp_path, ["standalone"], env={
        "MEMBRANE_BACKEND": "http://ollama:11434",
        "MEMBRANE_MODEL": "qwen3:8b",
        "MEMBRANE_EMBED_MODEL": "mxbai-embed-large",
    })
    assert _flag(argv, "--backend") == "http://ollama:11434"
    assert _flag(argv, "--model") == "qwen3:8b"
    assert _flag(argv, "--embed-model") == "mxbai-embed-large"


def test_entrypoint_puts_seat_flags_before_passthrough(tmp_path):
    _, _, argv = _run_entrypoint(
        tmp_path, ["standalone", "--no-dreamer", "--", "-c"],
        env={"MEMBRANE_EMBED_MODEL": "mxbai-embed-large"})
    sep = argv.index("--")
    assert argv[sep + 1:] == ["-c"]
    for f in ("--backend", "--embed-model", "--listen", "--serve-port",
              "--serve-host", "--no-dreamer"):
        assert argv.index(f) < sep, f"{f} after -- in {argv}"


def test_entrypoint_honours_cmd_override(tmp_path):
    _, _, argv = _run_entrypoint(tmp_path, ["serve", "--no-thinker"])
    assert argv[0] == "serve"
    assert "--no-thinker" in argv
    assert "--standalone" not in argv and "standalone" not in argv


def test_entrypoint_leaves_non_seat_commands_bare(tmp_path):
    # doctor/stop/review take no seat flags -- `docker run IMG doctor` works
    _, _, argv = _run_entrypoint(tmp_path, ["doctor"],
                                 env={"MEMBRANE_MODEL": "qwen3:8b"})
    assert argv == ["doctor"]


def test_entrypoint_execs_the_seat(tmp_path):
    shell_pid, stub_pid, _ = _run_entrypoint(tmp_path, ["standalone"])
    assert shell_pid == stub_pid, "the seat must replace the shell (exec)"


def test_entrypoint_keeps_values_with_spaces_whole(tmp_path):
    _, _, argv = _run_entrypoint(tmp_path, ["standalone"],
                                 env={"MEMBRANE_MODEL": "a b"})
    assert _flag(argv, "--model") == "a b"


# ---- the build context ----

def _ignore_rules():
    return [ln.strip() for ln in DOCKERIGNORE.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def test_dockerignore_is_an_allowlist():
    rules = _ignore_rules()
    assert rules[0] == "*", "the first rule must exclude everything"
    includes = [r[1:] for r in rules if r.startswith("!")]
    assert includes, "the allowlist re-includes nothing"
    allowed = {"pyproject.toml", "README.md", "LICENSE", "membrane",
               "membrane/**", "docker", "docker/**"}
    assert set(includes) <= allowed, set(includes) - allowed
    for inc in includes:
        assert ".membrane" not in inc
        assert not inc.endswith(".json") and not inc.endswith(".jsonl")
        assert "*" not in inc.split("/")[0], f"root wildcard re-include {inc}"


# ---- the Dockerfile ----

def _dockerfile():
    # drop comment lines (Docker strips them, even inside a continuation),
    # then join continuations so a multi-line ENV/HEALTHCHECK reads as one
    lines = [ln for ln in DOCKERFILE.read_text().splitlines()
             if not ln.strip().startswith("#")]
    return re.sub(r"\\\n", " ", "\n".join(lines))


def _instructions(name):
    return [ln.strip()[len(name):].strip() for ln in _dockerfile().splitlines()
            if ln.strip().upper().startswith(name + " ")]


def test_dockerfile_home_is_the_volume():
    env = " ".join(_instructions("ENV"))
    assert re.search(r"\bHOME=/data\b", env)
    assert any("/data" in v for v in _instructions("VOLUME"))


def test_dockerfile_exposes_both_ports():
    ports = " ".join(_instructions("EXPOSE")).split()
    assert "4096" in ports and "11500" in ports


def test_dockerfile_healthcheck_on_state_with_start_period():
    hc = " ".join(_instructions("HEALTHCHECK"))
    assert "/membrane/state" in hc and "11500" in hc
    m = re.search(r"--start-period=(\d+)s", hc)
    assert m and int(m.group(1)) >= 90


def test_dockerfile_default_cmd_is_standalone():
    cmds = _instructions("CMD")
    assert cmds and cmds[-1] == '["standalone"]'


def test_dockerfile_non_interactive_env():
    env = " ".join(_instructions("ENV"))
    for k in ("MEMBRANE_NO_BROWSER", "MEMBRANE_NO_ASK",
              "MEMBRANE_NO_UPDATE_CHECK", "MEMBRANE_NO_AUTOINSTALL"):
        assert re.search(rf"\b{k}=1\b", env), k


_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_SECRETISH = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|PASS)\w*=", re.I)


def test_dockerfile_env_bakes_in_nothing_site_specific():
    for env in _instructions("ENV"):
        for kv in re.findall(r"(\w+)=(\S+)", env):
            name, value = kv
            assert not _IP.search(value) or value in ("0.0.0.0", "127.0.0.1"), kv
            assert not _SECRETISH.search(f"{name}="), kv
            assert "://" not in value, f"no baked-in URL: {kv}"
    text = DOCKERFILE.read_text()
    for ip in _IP.findall(text):
        assert ip in ("0.0.0.0", "127.0.0.1"), f"stray IP {ip}"


# ---- the release workflow ----

WORKFLOW = ROOT / ".github" / "workflows" / "docker.yml"


def _publish_job():
    text = WORKFLOW.read_text()
    return text[text.index("\n  publish:\n"):]


def test_publish_builds_the_tag_source_with_the_workflow_recipe():
    # a workflow_dispatch of an older tag (cut before the image existed)
    # has no Dockerfile in its tree: the SOURCE is the tag, the RECIPE
    # (Dockerfile, .dockerignore, docker/) is the workflow's own commit
    job = _publish_job()
    assert "inputs.tag || github.ref" in job
    m = re.search(r"uses: actions/checkout@v\d+\s+with:\s+"
                  r"ref: \$\{\{ github\.sha \}\}\s+path: _recipe\s+"
                  r"sparse-checkout: \|\s+(.*?)\n\s+sparse-checkout-cone-mode: false",
                  job, re.S)
    assert m, "no sparse checkout of the recipe at github.sha into _recipe"
    assert {"Dockerfile", ".dockerignore", "docker/"} <= set(m.group(1).split())
    copy = job.index("cp -R _recipe/")
    assert job.index("rm -rf _recipe", copy) < job.index("docker/build-push-action")
    # the version check reads the TAG's pyproject, before the recipe lands
    assert job.index("the tag is the package's version") < copy
