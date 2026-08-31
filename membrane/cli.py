"""membrane CLI -- one entry point for every membrane surface.

    membrane tui          TUI + proxy on :11435 (tears down the proxy it
                          started when the TUI exits)
    membrane proxy        just the proxy, foreground
    membrane opencode     field-backed opencode: OpenAI-compatible proxy on
                          :11500 + opencode pointed at it
    membrane anthropic    TUI+proxy in --anthropic mode (Claude Code front door)
    membrane review       print what the field learned
    membrane demo         in-process end-to-end loop (no chat model needed)
    membrane install      drop .opencode/skills/* + print an AGENTS.md blurb
                          into the CURRENT directory

Extra args are forwarded verbatim to the underlying module (proxy flags to the
proxy, etc.). In tui mode flags are routed by name exactly like ./run.sh did.
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"

AGENTS_BLURB = """\
# membrane (field memory)

This project is membrane-backed: a long-term memory field sits between the
agent and the model. It recalls past situations per turn (RAG), learns every
turn (Q/A exemplars with a judge/demotion pipeline), and answers four
canonical frames itself (intercepts -- no model call, never learned):

    membrane: query the field for <topic>     read recall
    membrane: learn that <fact>               write a memory
    membrane: forget <what or which>          delete (indexes "3-7, 9",
                                              topics, "the last hour",
                                              "this session", "everything")
    field state                               memory health/stats

The agent may also emit these frames in its own prose: reads arrive next turn
as clearly-marked `[membrane-intercept]` blocks; writes/forgets from model
prose are report-only (the field is only written by operator turns).

Read the skills in .opencode/skills/ (membrane-query-field,
membrane-write-memory, membrane-forget-memory, membrane-state) for the full
contract, guards, and model-collapse hazards. Do not curl membrane's
endpoints and paste field content into chat -- raw-pasted field content is
learnable and feeds the field back into itself.
"""

GITIGNORE_SNIPPET = """
# membrane runtime state (per-project memory field)
rag-field.json
rag-audit.jsonl
rag-field.json.review.jsonl
membrane-opencode.log
membrane-proxy.log
"""


def _module_args(args: argparse.Namespace) -> list[str]:
    return list(args.forwarded)


def _run_module(module: str, argv: list[str]) -> int:
    cmd = [sys.executable, "-m", module] + argv
    return subprocess.call(cmd)


def _port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", int(port)))
    except OSError:
        return False
    finally:
        s.close()
    return True


def _tui(argv: list[str]) -> int:
    # Route flags between proxy and TUI exactly like ./run.sh did.
    proxy_flags = ("--judge", "--no-judge", "--no-neg-followup", "--anthropic",
                   "--observer", "--absorb", "--no-vague-filter",
                   "--variety-gate", "--variety-temp")
    proxy_kv = ("--listen", "--backend", "--sim-min", "--consensus-min",
                "--dedup-sim", "--demote-threshold", "--judge-model",
                "--judge-timeout", "--judge-backend", "--vague-min-tokens",
                "--temp-driver", "--temp-tau", "--sim-min-floor", "--nudge-sigma",
                "--nudge-seed", "--system", "--warmup", "--warmup-model",
                "--warmup-seed", "--tag", "--cells")
    tui_kv = ("--refresh",)
    both_kv = ("--field", "--audit")

    proxy_args: list[str] = []
    tui_args: list[str] = []
    it, n = 0, len(argv)
    while it < n:
        a = argv[it]
        def _kv(dest: list[str]) -> None:
            nonlocal it
            dest.append(argv[it]); it += 1
            if it < n:
                dest.append(argv[it]); it += 1
        if any(a == f for f in proxy_flags):
            proxy_args.append(a); it += 1
        elif any(a == f for f in tui_kv):
            _kv(tui_args)
        elif any(a == f for f in both_kv):
            _kv(proxy_args); _kv(tui_args)
        elif any(a == f for f in proxy_kv):
            _kv(proxy_args)
        else:
            proxy_args.append(a); tui_args.append(a); it += 1

    # Which port will the proxy bind? (default 11435)
    port = 11435
    for i, a in enumerate(proxy_args):
        if a == "--listen" and i + 1 < len(proxy_args):
            port = int(proxy_args[i + 1]); break

    started: subprocess.Popen | None = None
    log = open("membrane-proxy.log", "a")
    if not _port_open(port):
        started = subprocess.Popen(
            [sys.executable, "-m", "membrane.proxy"] + proxy_args,
            stdout=log, stderr=subprocess.STDOUT)
        for _ in range(40):
            if _port_open(port):
                break
            if started.poll() is not None:
                started = None
                break
            time.sleep(0.25)
        if started is not None and not _port_open(port):
            print("warning: proxy did not come up on :%d -- see "
                  "membrane-proxy.log" % port, file=sys.stderr)
            print("         (the TUI still works; it shows the last field "
                  "state)", file=sys.stderr)

    try:
        return subprocess.call(
            [sys.executable, "-m", "membrane.tui"] + tui_args)
    finally:
        if started is not None:
            started.terminate()
            try:
                started.wait(timeout=5)
            except Exception:
                pass
            log.close()


def _anthropic(argv: list[str]) -> int:
    # TUI+proxy in --anthropic mode: prepend --anthropic if absent.
    if "--anthropic" not in argv:
        argv = ["--anthropic"] + argv
    return _tui(argv)


def _reset(argv: list[str]) -> int:
    # `membrane reset <args...>`: kill what's on the proxy port, then start
    # fresh (TUI+proxy) -- run.sh's `restart`.
    port = 11435
    prev = ""
    for a in argv:
        if prev == "--listen":
            port = int(a); break
        prev = a
    pids = subprocess.run(["lsof", "-nP", "-iTCP:%d" % port,
                           "-sTCP:LISTEN", "-t"],
                          capture_output=True, text=True).stdout.split()
    if pids:
        print("[cli] reset: killing proxy on :%d (pids %s)" % (port, pids),
              file=sys.stderr)
        for pid in pids:
            subprocess.run(["kill", pid], capture_output=True)
        time.sleep(0.5)
    else:
        print("[cli] reset: nothing on :%d, starting fresh" % port,
              file=sys.stderr)
    return _tui(argv)


def _install(args: argparse.Namespace) -> int:
    """Drop the skills into $PWD/.opencode/skills/ and print the blurb."""
    cwd = Path.cwd()
    dest_root = cwd / ".opencode" / "skills"
    installed, refreshed = [], []
    for skill in sorted(SKILLS_DIR.iterdir()):
        if not (skill / "SKILL.md").is_file():
            continue
        dest = dest_root / skill.name
        if dest.exists():
            if not args.force:
                refreshed.append(skill.name)
                continue
            shutil.rmtree(dest)
        shutil.copytree(skill, dest)
        installed.append(skill.name)

    for name in installed:
        print(f"installed .opencode/skills/{name}/SKILL.md")
    for name in refreshed:
        print(f"kept existing .opencode/skills/{name}/ (--force to refresh)")
    if not args.force:
        # Always print the blurb; also append the gitignore snippet once.
        gi = cwd / ".gitignore"
        marker = "# membrane runtime state"
        if gi.is_file() and marker in gi.read_text():
            pass
        elif gi.is_file():
            gi.write_text(gi.read_text().rstrip("\n") + "\n" + GITIGNORE_SNIPPET)
            print("appended membrane entries to .gitignore")
        else:
            gi.write_text(GITIGNORE_SNIPPET.lstrip("\n"))
            print("created .gitignore with membrane entries")
    print()
    print(AGENTS_BLURB)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="membrane", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # Manual parse (not subparsers): everything after the subcommand is
    # FORWARDED VERBATIM to the underlying module -- argparse's positional
    # nargs="*" refuses to absorb leading flag-like tokens ("--listen 11599"),
    # and those are exactly what we must pass through untouched.
    sub_help = (
        "\n"
        "  tui        TUI + proxy (default surface)\n"
        "  proxy      proxy only, foreground\n"
        "  opencode   field-backed opencode (OpenAI-compatible proxy on :11500)\n"
        "  anthropic  TUI+proxy --anthropic mode (Claude Code front door)\n"
        "  review     print what the field learned\n"
        "  demo       in-process end-to-end loop (no chat model needed)\n"
        "  reset      kill the proxy on the port, then TUI+proxy fresh\n"
        "  install    install .opencode/skills/* into the CURRENT directory\n"
        "\n"
        "Everything after the subcommand is forwarded verbatim to the\n"
        "underlying module (proxy/TUI flags by name).\n")
    argv = sys.argv[1:]
    cmd = argv[0] if argv and not argv[0].startswith("-") else None
    if cmd not in ("tui", "proxy", "opencode", "anthropic", "review",
                   "demo", "reset", "install"):
        if cmd is None and not argv:
            print(__doc__)
            return
        ap.error(f"first argument must be a command; got {argv!r}\n"
                 + sub_help)
    forwarded = argv[1:]
    args = argparse.Namespace(cmd=cmd, forwarded=forwarded,
                              force="--force" in forwarded)
    if cmd == "install":
        if args.force:
            forwarded = [a for a in forwarded if a != "--force"]
        raise SystemExit(_install(args))
    if args.cmd == "reset":
        raise SystemExit(_reset(args.forwarded))
    if args.cmd == "tui":
        raise SystemExit(_tui(args.forwarded))
    if args.cmd == "anthropic":
        raise SystemExit(_anthropic(args.forwarded))
    if args.cmd == "demo":
        raise SystemExit(_run_module("membrane.demo", args.forwarded))
    raise SystemExit(_run_module("membrane." + args.cmd, args.forwarded))


if __name__ == "__main__":
    main()