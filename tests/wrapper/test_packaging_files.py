"""The packaging files say what the operator decided (#1143, 2026-11-14): nothing publishes by itself, every wheel row has its binary, the image is small and not root, no secret is in a file.
Text-level checks (no YAML library needed): the workflows are linted separately (a yaml parse in CI, `ruby -ryaml` by hand)."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"


def wf(name):
    return (WF / name).read_text()


def triggers(text):
    """the top-level keys under `on:`"""
    m = re.search(r"(?ms)^on:\n(.*?)^(?=\S)", text)
    return re.findall(r"(?m)^  ([a-z_]+):", m.group(1))


def test_publish_is_a_manual_dispatch_only_with_a_confirmation_and_the_tag_check():
    t = wf("publish.yml")
    assert triggers(t) == ["workflow_dispatch"]
    assert re.search(r"(?m)^permissions:\n  contents: read$", t)
    assert '[ "$CONFIRM" = "publish $TAG" ]' in t
    assert re.search(r'\[ "\$\{tag#v\}" = "\$ver" \]', t)
    # the PyPI upload sits behind the wheels, the sdist and the shim, and PyPI comes before any GitHub release
    assert re.search(r"(?s)publish:\n    needs: \[wheels, shim\]", t) and re.search(r"(?s)release:\n    needs: \[detect, publish\]", t)
    assert "if: inputs.github_release" in t
    # the write permission is the release job's alone
    assert t.count("contents: write") == 1 and t.index("contents: write") > t.index("  release:")


def test_publish_ships_the_wheels_the_sdist_and_the_last_shim_and_the_shim_gets_only_the_launcher_files():
    t = wf("publish.yml")
    assert "uses: ./.github/workflows/wheels.yml" in t
    assert "{wheel-linux-*,wheel-macos-*,sdist,shim}" in t
    assert "cp membrane/__init__.py membrane/launcher.py membrane/__main__.py shim/membrane-seat/membrane/" in t
    assert "cp -R membrane " not in t


def test_the_image_lane_never_pushes_without_the_dispatch_the_ticked_box_and_the_confirmation():
    t = wf("docker.yml")
    assert set(triggers(t)) == {"workflow_dispatch", "pull_request"}
    assert '[ "$CONFIRM" = "push $TAG" ]' in t
    # every step that pushes or logs in is behind the confirmed step
    for m in re.finditer(r"(?m)^      - (?:uses|name|id)[^\n]*\n(?:        [^\n]*\n)*", t):
        step = m.group(0)
        if "push: true" in step or "docker/login-action" in step:
            assert "steps.go.conclusion == 'success'" in step, step
    assert t.count("push: true") == 1


def test_no_workflow_starts_a_publish_on_a_push_a_tag_or_a_release():
    for name in ("publish.yml", "docker.yml", "wheels.yml"):
        assert set(triggers(wf(name))) <= {"workflow_dispatch", "workflow_call", "pull_request"}, name


# the wheel rows: one per binary target of release-binaries.yml, with the tag the binary supports
EXPECT = {
    "aarch64-apple-darwin": "macosx_11_0_arm64",
    "x86_64-apple-darwin": "macosx_11_0_x86_64",
    "x86_64-unknown-linux-gnu": "manylinux_2_28_x86_64",
    "aarch64-unknown-linux-gnu": "manylinux_2_28_aarch64",
    "x86_64-unknown-linux-musl": "musllinux_1_2_x86_64",
    "aarch64-unknown-linux-musl": "musllinux_1_2_aarch64",
}


def test_every_binary_target_has_its_wheel_row_with_the_right_tag():
    targets = set(re.findall(r"target: ([a-z0-9_-]+)", wf("release-binaries.yml")))
    rows = dict(re.findall(r"target: ([a-z0-9_-]+),\s+tag: ([a-z0-9_]+)", wf("wheels.yml")))
    assert targets == set(EXPECT) and rows == EXPECT


def test_the_wheels_come_from_the_binaries_workflow_and_are_smoke_tested_and_the_sdist_must_fail():
    t = wf("wheels.yml")
    assert "uses: ./.github/workflows/release-binaries.yml" in t and "workflow_call:" in wf("release-binaries.yml")
    assert "wheel-payload-${{ matrix.target }}" in t and "scripts/dist/build_wheel.sh" in t and "scripts/dist/smoke_wheel.sh" in t
    assert "no binary for this platform" in t
    assert "cargo" not in t.lower().replace("no cargo here", "")


def test_the_release_tarballs_carry_their_sha256_for_install_sh():
    assert ".tar.gz.sha256" in wf("release-binaries.yml")


def test_the_image_is_small_not_root_and_has_no_claude_cli_python_or_rust():
    d = (ROOT / "Dockerfile").read_text()
    code = "\n".join(l for l in d.splitlines() if not l.lstrip().startswith("#"))
    assert re.search(r"(?m)^USER membrane$", code) and "useradd --uid 10001" in code
    assert re.search(r"(?m)^EXPOSE 4096$", code) and re.search(r"(?m)^VOLUME /data$", code)
    assert "ENTRYPOINT" in code and "HEALTHCHECK" in code and "/global/health" in code
    for w in ("pip ", "python", "cargo", "rustup", "npm", "torch", "claude.ai", "@anthropic"):
        assert w not in code, w
    assert "ANTHROPIC_API_KEY" in d and "CLAUDE_BIN" in d   # how to supply it is said


def test_the_entrypoint_execs_serve_on_4096_bound_for_the_bridge_with_the_data_volume():
    e = (ROOT / "docker" / "docker-entrypoint.sh").read_text()
    assert "exec /opt/babyseat/bin/cliffvm serve --port 4096 --bind 0.0.0.0 --data-dir /data" in e
    assert "set -eu" in e


def test_the_docker_context_is_an_allowlist():
    lines = [l for l in (ROOT / ".dockerignore").read_text().splitlines() if l and not l.startswith("#")]
    assert lines[0] == "*" and all(l.startswith("!") for l in lines[1:])
    assert "!docker-dist/*.tar.gz" in lines


@pytest.mark.parametrize("script", ["install.sh", "scripts/dist/build_wheel.sh", "scripts/dist/smoke_wheel.sh", "scripts/dist/smoke.sh", "docker/docker-entrypoint.sh"])
def test_the_shell_files_parse_and_stop_on_errors(script):
    p = ROOT / script
    assert subprocess.run(["sh", "-n", str(p)]).returncode == 0
    assert "set -eu" in p.read_text()


def test_no_secret_is_in_the_packaging_files():
    pat = re.compile(r"(ghp_[A-Za-z0-9]{20,}|pypi-AgE[A-Za-z0-9_-]{20,}|dckr_pat_[A-Za-z0-9_-]{10,}|sk-ant-[A-Za-z0-9_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)")
    files = [ROOT / "install.sh", ROOT / "Dockerfile", ROOT / "setup.py", ROOT / "pyproject.toml", *(ROOT / "docker").glob("*"), *WF.glob("*.yml"), *(ROOT / "scripts" / "dist").glob("*.sh")]
    for f in files:
        assert not pat.search(f.read_text()), f
    # a secret is only ever read from the repo's secret store, in the two lanes that publish
    for f in WF.glob("*.yml"):
        for m in re.findall(r"secrets\.([A-Z_]+)", f.read_text()):
            assert m in ("PYPI_TOKEN", "DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"), (f, m)


def test_publish_can_make_the_github_release_a_pre_release_so_the_stable_channel_does_not_move():
    # a pre-release is not the "latest" release: HACS and /releases/latest keep naming the stable one (a pre-release is an option of the publish line)
    t = wf("publish.yml")
    assert re.search(r"(?ms)^      prerelease:\n        description: .*?\n        type: boolean\n        default: false", t)
    assert "inputs.prerelease" in t and "--prerelease" in t


def test_the_image_lane_moves_latest_and_the_minor_tag_only_for_a_stable_release():
    # existing pulls of chedim/babyseat and chedim/membrane follow `latest` and X.Y: a pre-release (a pre-release, for example) must not move them; only X.Y.Z is pushed unless the dispatch says `stable`
    t = wf("docker.yml")
    assert re.search(r"(?ms)^      stable:\n        description: .*?\n        type: boolean\n        default: false", t)
    assert "type=raw,value=latest,enable=${{ inputs.stable == true }}" in t
    assert "pattern={{major}}.{{minor}},value=${{ steps.ver.outputs.version }},enable=${{ inputs.stable == true }}" in t
    assert "pattern={{version}},value=${{ steps.ver.outputs.version }}\n" in t
