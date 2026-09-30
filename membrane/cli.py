"""membrane CLI -- one entry point for every membrane surface.

    membrane opencode     field-backed opencode: OpenAI-compatible proxy on
                          :11500 + opencode pointed at it
    membrane review       print what the field learned
    membrane reset        kill the seat on :11500 (or --listen), then
                          start opencode fresh
    membrane update        check the install source, show what changed,
                           update + re-exec (MEMBRANE_NO_UPDATE_CHECK=1
                           disables the automatic startup check)
    membrane install       drop the agent skills into ./.opencode/skills/ of
                          the CURRENT directory and print the AGENTS.md blurb
    membrane connect      offer to connect a shared Couchbase field for THIS
                          project (creds go to ~/.membrane/projects/, never
                          into the repo)
    membrane move         move the entity field's collections: cb -> cb, or
                          dump cb -> json:DIR (one jsonl per collection;
                          --origin filters the per-origin docs)
    membrane ingest       cold-start: absorb docs/README into the field
                          (no model call; same learn guards as any turn)
    membrane codegraph    stdlib-AST call graph of this repo (stats / impact /
                          callers / callees; no model call)
    membrane doctor       show what's installed/running (CLI, skills, Ollama)

Per-project DATA lives in ./.membrane/ (field, audit, review queue, logs) --
auto-created on first run per directory. Skills are PROJECT-SCOPED on purpose
(~/.config/opencode/skills/ would leak them into unrelated opencode
sessions); `membrane opencode` auto-installs them into ./.opencode/skills/
on first run unless MEMBRANE_NO_AUTOINSTALL=1. Extra args are forwarded
verbatim to the underlying module.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .project_env import couchbase_onboarding, load_project_env
from .update import main as update_main, maybe_check_on_boot

SKILLS_DIR = Path(__file__).parent / "skills"

DATA_DIR = Path(".membrane")           # per-project, auto-created
FIELD = DATA_DIR / "field.json"
AUDIT = DATA_DIR / "audit.jsonl"
PROXY_LOG = DATA_DIR / "proxy.log"
OPENCODE_LOG = DATA_DIR / "opencode.log"

AGENTS_BLURB = """\
# membrane (field memory)

This project is membrane-backed: a long-term memory field sits between the
agent and the model. It recalls past situations per turn (RAG), learns every
turn (Q/A exemplars with a judge/demotion pipeline), and answers five
canonical frames itself (intercepts -- no model call, never learned):

    membrane: query the field for <topic>     read recall
    membrane: learn that <fact>               write a memory
    membrane: forget <what or which>          delete (indexes "3-7, 9",
                                              topics, "the last hour",
                                              "this session", "everything")
    field state                               memory health/stats
    membrane: codegraph <spec>                call graph reads (stats | impact
                                              <sym|file.py> | callers | callees)
    membrane: suggest                         review scope suggestions for the
                                              cross-project commons

The agent may also emit these frames in its own prose: reads arrive next turn
as clearly-marked `[membrane-intercept]` blocks. WRITES: a `membrane: learn
that <lesson>` in the agent's prose EXECUTES by default (same guards as any
turn, tagged `agent-note`, audited `origin: agent`; --no-agent-writes reverts
to propose-only), index-form `membrane: forget #N` deletes (wordy forms
propose only), and `membrane: watch <topic>` plants a trigger. The agent must
learn only EARNED lessons of its own work -- never secrets, user personal
data, or transcript dumps.

Data lives in .membrane/ (field.json, audit.jsonl, logs) -- never commit it.
Persistence backend: JSON file by default; `membrane connect` (or the
launch-time prompt) points THIS project at a shared Couchbase cluster
(CouchbaseCollection `membrane.exemplars` in COUCHBASE_BUCKET, cortex
env convention: COUCHBASE_CONN_STRING / COUCHBASE_USER / COUCHBASE_PASSWORD
/ COUCHBASE_BUCKET). Credentials are stored per-project under
~/.membrane/projects/<slug>.env (mode 600) -- never in the repo; a `.env`
here would leak on the first `git add .`.
Couchbase unreachable -> transparent fallback to the JSON field.
Skills are project-scoped: .opencode/skills/ in THIS repo only (an opencode
launched outside never sees them). Read those skills (membrane-query-field,
membrane-write-memory, membrane-forget-memory, membrane-state,
membrane-global-field, membrane-codegraph, membrane-ingest,
membrane-restart) for the full
contract, guards, and model-collapse hazards. Facts true BEYOND this repo
(user/machine/team preferences) belong in the global commons:
`membrane: remember globally: <fact>` (see membrane-global-field). Do not
curl membrane's endpoints and paste field content into chat -- raw-pasted
field content is learnable and feeds the field back into itself.
Cold start: a fresh field is thin; if docs/README already hold the knowledge,
run `membrane ingest` (no model call, same guards as any learn) -- see
membrane-ingest. Before editing a function or file, `membrane: codegraph
impact <sym|file.py>` reads the call graph (no model call).
"""

GITIGNORE_SNIPPET = """
# membrane runtime state (per-project memory field)
.membrane/
"""


def _module_args(args: argparse.Namespace) -> list[str]:
    return list(args.forwarded)


def _autoinstall_skills(quiet: bool = False) -> bool:
    """Install the packaged skills into ./.opencode/skills/ if missing.

    Returns True if anything was installed. Respects MEMBRANE_NO_AUTOINSTALL.
    """
    if os.environ.get("MEMBRANE_NO_AUTOINSTALL"):
        return False
    dest_root = Path.cwd() / ".opencode" / "skills"
    missing = [s.name for s in sorted(SKILLS_DIR.iterdir())
               if (s / "SKILL.md").is_file() and not (dest_root / s.name).exists()]
    if not missing:
        return False
    for name in missing:
        src = SKILLS_DIR / name
        shutil.copytree(src, dest_root / name)
        if not quiet:
            print(f"[cli] installed .opencode/skills/{name}/SKILL.md "
                  f"(project-scoped; MEMBRANE_NO_AUTOINSTALL=1 to skip)")
    return bool(missing)


def _ensure_data_dir(argv: list[str]) -> list[str]:
    """Create ./.membrane/ and bind --field/--audit to it unless the caller
    passed their own. Pass-through flags win; defaults live here."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if "--field" not in argv:
        argv = ["--field", str(FIELD)] + argv
    if "--audit" not in argv:
        argv = ["--audit", str(AUDIT)] + argv
    return argv


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


def _reset(argv: list[str]) -> int:
    # `membrane reset <args...>`: kill what listens on the opencode seat's
    # port (--listen, default 11500), then boot the seat fresh -- run.sh's
    # `restart`. (It used to relaunch the TUI+proxy pair on :11435; those
    # modules are retired, so the seat is the only thing to restart.)
    port = 11500
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
    return _run_module("membrane.opencode", _ensure_data_dir(argv))


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


def _cb_ask(input_fn, getpass_fn, title: str) -> dict:
    """Interactive Couchbase endpoint (creds typed, never echoed)."""
    print(f"  {title}")
    conn = input_fn("  conn string [couchbase://127.0.0.1]: ").strip()
    user = input_fn("  user [Administrator]: ").strip()
    pw = getpass_fn("  password: ").strip()
    bucket = input_fn("  bucket [membrane]: ").strip()
    return {"conn": conn or "couchbase://127.0.0.1",
            "user": user or "Administrator",
            "pw": pw or "password",
            "bucket": bucket or "membrane"}


def _migrate(argv: list[str]) -> int:
    """`membrane migrate`: the interactive front door to `membrane move`.

    Detects where THIS project's store lives (the project env file ->
    couchbase; anything else -> a memory-only seat), asks what to move TO
    (offering the sane default), asks creds when the target is Couchbase,
    then runs the production `move` path (same endpoint grammar,
    idempotent, content-keyed)."""
    import getpass as _getpass
    from .project_env import project_env_path, parse_env_file

    def _ask(prompt: str, default: str) -> str:
        ans = input(prompt).strip()
        return ans or default

    have = parse_env_file(project_env_path())
    on_cb = (os.environ.get("MEMBRANE_STORE", "").strip().lower() == "couchbase"
             or have.get("MEMBRANE_STORE", "").strip().lower() == "couchbase")
    src_desc = "couchbase" if on_cb else "off (a local seat boots memory-only)"
    print(f"[membrane migrate] this project's field lives on: {src_desc}")
    print("  migrate to: [1] couchbase  [2] memory")
    to = _ask("  target [{}]: ".format(
        "1 (couchbase)" if not on_cb else "2 (memory)"),
        "1" if not on_cb else "2")
    to_cb = to.strip() in ("1", "couchbase", "cb")

    extra: list[str] = []

    if on_cb and not to_cb:
        # cb -> json dump: the nine collections as one .jsonl each
        bucket = (os.environ.get("COUCHBASE_BUCKET", "").strip()
                  or have.get("COUCHBASE_BUCKET", "").strip() or "membrane")
        src = "cb:" + bucket
        dst = "json:" + str(FIELD.parent / "efield-dump")
        print(f"[membrane migrate] couchbase ({bucket}) -> {dst}")
    elif on_cb and to_cb:
        # cb -> cb: source = current cluster (env creds), target = NEW creds
        cur_bucket = (os.environ.get("COUCHBASE_BUCKET", "").strip()
                      or have.get("COUCHBASE_BUCKET", "").strip() or "membrane")
        print("  (source cluster is the currently configured one; "
              f"bucket {cur_bucket})")
        print("  target cluster:")
        tgt = _cb_ask(input, _getpass.getpass,
                      "target cluster (creds for the NEW cluster):")
        scheme = "cbs" if tgt["conn"].startswith("couchbases") else "cb"
        src = "cb:" + cur_bucket
        dst = (f"{scheme}://{tgt['user']}:{tgt['pw']}@"
               + tgt["conn"].replace("couchbase://", "")
               .replace("couchbases://", "").rstrip("/")
               + f"/{tgt['bucket']}")
    elif (not on_cb) and to_cb:
        # off-cluster -> couchbase: a memory seat has no collections to
        # move; this CONFIGURES the project for the new cluster (the seat
        # joins the shared bucket; the entity field grows there from scratch
        # or arrives with the peers via copy).
        tgt = _cb_ask(input, _getpass.getpass,
                      "target cluster (creds are stored in "
                      "~/.membrane/projects/, never the repo):")
        try:
            from .project_env import _write_env_file, DECLINE_KEY
            path = project_env_path()
            vals = dict(have)
            vals.update({
                "MEMBRANE_STORE": "couchbase",
                "COUCHBASE_CONN_STRING": tgt["conn"],
                "COUCHBASE_USER": tgt["user"],
                "COUCHBASE_PASSWORD": tgt["pw"],
                "COUCHBASE_BUCKET": tgt["bucket"],
                DECLINE_KEY: "0",
            })
            _write_env_file(path, vals)
            print(f"[membrane migrate] store config saved to {path} "
                  f"(this project now lives on couchbase)", file=sys.stderr)
        except Exception as e:
            print(f"[membrane migrate] warning: could not save store config "
                  f"({e}); the move itself still runs.", file=sys.stderr)
        return 0                  # a memory seat holds no collections
    else:
        print("[membrane migrate] not on couchbase -- nothing to do "
              "(a memory-only seat has nothing to move; MEMBRANE_STORE="
              "couchbase puts a project on the shared cluster).",
              file=sys.stderr)
        return 0

    dst_show = dst
    if to_cb:
        import re as _re
        dst_show = _re.sub(r"(cb://[^:@/]+):[^@/]*@", r"\1:***@", dst)
    print(f"[membrane migrate] running: membrane move --from {src} "
          f"--to {dst_show}")
    sys.argv = ["membrane move", "--from", src, "--to", dst] + extra
    from .move import main as move_main
    return move_main()


def _connect() -> int:
    """`membrane connect`: (re)run the shared-field onboarding interactively.

    Unlike the passive launch-time offer, this respects a MEMBRANE_NO_ASK=1
    ONLY for the passive path -- an explicit `connect` is the operator
    asking, so it always prompts (force=True) unless the env already
    configures the store, in which case it just points at where."""
    from .project_env import project_env_path, parse_env_file, store_configured
    if store_configured():
        print(f"[membrane] couchbase already configured via environment "
              f"(MEMBRANE_STORE/COUCHBASE_*); env wins over the project "
              f"file.", file=sys.stderr)
        return 0
    wrote = couchbase_onboarding(force=True)
    return 0 if (wrote or parse_env_file(project_env_path())) else 1


def _auth(argv: list[str]) -> int:
    """`membrane auth <provider>`: store a cloud API key outside the repo.

    Providers come from auth.PROVIDERS (anthropic, openrouter, gemini, groq,
    github, mistral). The key goes to ~/.membrane/auth.json (600); the
    provider's env var always wins over the stored value (the shell is the
    louder authority). Probes the provider's model listing on success so a
    typo fails HERE, not on the first routed turn."""
    from .auth import (save_api_key, mask, fetch_models, PROVIDERS,
                       load_api_key, _env_names)
    import getpass as _getpass
    provider = argv[0] if argv and not argv[0].startswith("-") else None
    if not provider or provider not in PROVIDERS:
        provs = ", ".join(sorted(PROVIDERS))
        print(f"usage: membrane auth <{provs}>   "
              f"(or: membrane auth <provider> --key <KEY>)", file=sys.stderr)
        return 2
    prov = PROVIDERS[provider]
    # --key flag (non-interactive); otherwise prompt (hidden)
    key = ""
    prev = ""
    for a in argv[1:]:
        if prev == "--key":
            key = a
            break
        prev = a
    if not key and not sys.stdin.isatty():
        print("[membrane] auth: no TTY and no --key; pass "
              "`--key <API key>` or run interactively", file=sys.stderr)
        return 2
    if not key:
        key = _getpass.getpass(
            f"{provider} API key (input hidden, {prov['key_hint']}): ").strip()
    if not key:
        print("[membrane] auth: empty key, nothing stored", file=sys.stderr)
        return 2
    p = save_api_key(provider, key)
    ids = fetch_models(provider, key)
    print(f"[membrane] {provider} key stored: {p}")
    if ids:
        print(f"[membrane] verified: {len(ids)} model(s) visible, "
              f"first: {', '.join(ids[:3])}")
        print("[membrane] they will appear in opencode's model picker at "
              "the next `membrane opencode` boot")
    else:
        print("[membrane] WARNING: stored, but the model listing probe "
              "failed -- check the key/network; rung registration will "
              "retry at next boot")
    env_names = _env_names(provider)
    print(f"[membrane] active key: {mask(key)} "
          f"(env {env_names[0]} overrides the store)")
    return 0


def _doctor() -> int:
    """What's installed / running / reachable."""
    print("membrane doctor")
    cli = Path(sys.argv[0]).resolve()
    print(f"  cli:      {cli}")
    print(f"  python:   {sys.version.split()[0]}  ({sys.executable})")

    pkg = SKILLS_DIR.parent
    print(f"  package:  {pkg}")
    shipped = sorted(s.name for s in SKILLS_DIR.iterdir()
                     if (s / "SKILL.md").is_file())
    print(f"  skills shipped: {', '.join(shipped) or 'NONE (broken install)'}")

    proj = Path.cwd() / ".opencode" / "skills"
    here = sorted(s.name for s in SKILLS_DIR.iterdir()
                  if (s / "SKILL.md").is_file() and (proj / s.name).exists())
    here = [n for n in shipped if (proj / n / "SKILL.md").is_file()]
    print(f"  skills in this project: {', '.join(here) or 'none'}"
          f"  ({proj})")

    print(f"  project data: {DATA_DIR}/ "
          f"({'exists' if DATA_DIR.is_dir() else 'not created yet'})")
    if "--verbose" in sys.argv and DATA_DIR.is_dir():
        for p in sorted(DATA_DIR.iterdir()):
            print(f"    {p.name}  {p.stat().st_size}B")

    try:
        from .capture import mode_from_env
        from .efstore import open_backend
        from .efield import EntityField
        from .substrate import _origin_tag
        mode = mode_from_env()
        fpath = str(DATA_DIR / "field.json")
        be = open_backend(fpath)
        v = EntityField(be, None, _origin_tag(fpath)).vitals()
        by: dict = {}
        for k, c in v["events"].items():
            by[k.split("/")[0]] = by.get(k.split("/")[0], 0) + c
        print(f"  entity field: MEMBRANE_EFIELD={mode} on {type(be).__name__}; events "
              + (", ".join(f"{k} {c}" for k, c in sorted(by.items())) or "none")
              + "; entities " + (", ".join(f"{k} {c}" for k, c in sorted(v["entities"].items()))
                                 or "none"))
    except Exception as e:
        print(f"  entity field: unreadable ({str(e)[:80]})")
    print(f"  opencode proxy port 11500: "
          f"{'LISTENING' if _port_open(11500) else 'free'}")

    backend = "http://localhost:11434"
    try:
        import urllib.request
        with urllib.request.urlopen(backend + "/api/tags", timeout=2) as r:
            models = [m["name"] for m in __import__("json").load(r)["models"]]
        print(f"  ollama {backend}: up, {len(models)} models "
              f"(embed-capable present: {any('embed' in m or 'minilm' in m or 'bxbai' in m or 'mxbai' in m for m in models)})")
    except Exception:
        print(f"  ollama {backend}: DOWN (chat/learning will not work)")
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
        "  opencode   field-backed opencode (OpenAI-compatible proxy on :11500)\n"
        "  serve      field-backed headless opencode server (basic auth; the\n"
        "             mobile companion app's surface; the proxy on :11500)\n"
        "  review     print what the field learned\n"
        "  reset      kill the proxy on the port, then opencode fresh\n"
        "  update     check the install source for updates, show what\n"
        "             changed, apply (git pull --ff-only) and re-exec\n"
        "  install    install .opencode/skills/* into the CURRENT directory\n"
        "  connect    offer to connect a shared Couchbase field for THIS\n"
        "             project (creds go to ~/.membrane/projects/, not the repo)\n"
        "  auth       store a cloud API key (anthropic, openrouter, gemini,\n"
        "             groq, github, zen, mistral): ~/.membrane/auth.json (600);\n"
        "             its models become routable rungs + picker entries\n"
        "  move       move the entity field's collections (cb->cb, cb->json\n"
        "             dumps); see --help for endpoint forms\n"
        "  migrate    interactive migration front door: detects where this\n"
        "             project's field lives, asks for the target mechanism\n"
        "             (and creds when Couchbase is involved), then runs move\n"
        "  ingest     cold-start: absorb docs/README into the field (no model call)\n"
        "  backfill   rebuild the entity field from the audit log + the codebase\n"
        "  codegraph  stdlib-AST call graph (stats | impact | callers | callees)\n"
        "  doctor     what's installed/running (CLI, skills, Ollama)\n"
        "\n"
        "Everything after the subcommand is forwarded verbatim to the\n"
        "underlying module.\n"
        "\n"
        "Retired (print a pointer to opencode): tui, proxy, anthropic, demo.\n")
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd = argv[0] if argv and not argv[0].startswith("-") else None
    if cmd not in ("tui", "proxy", "opencode", "anthropic", "review",
                   "demo", "reset", "install", "doctor", "connect",
                   "ingest", "backfill", "codegraph", "update", "move",
                   "migrate", "auth", "serve"):
        if cmd is None and not argv:
            print(__doc__)
            return
        ap.error(f"first argument must be a command; got {argv!r}\n"
                 + sub_help)
    forwarded = argv[1:]
    args = argparse.Namespace(cmd=cmd, forwarded=forwarded,
                              force="--force" in forwarded)
    if cmd == "doctor":
        raise SystemExit(_doctor())
    if cmd == "update":
        raise SystemExit(update_main())
    if cmd == "install":
        if args.force:
            forwarded = [a for a in forwarded if a != "--force"]
        raise SystemExit(_install(args))
    # opencode surfaces: bind per-project data + auto-install skills first
    if cmd in ("opencode", "reset", "serve"):
        _autoinstall_skills()
    # shared-field onboarding: hydrate the per-project env (secrets live in
    # ~/.membrane/projects/, never in the repo), and -- when nothing
    # configures the store and we are interactive -- offer to connect a
    # Couchbase cluster once (a decline is remembered, not re-asked).
    load_project_env()
    if cmd == "connect":
        raise SystemExit(_connect())
    if cmd == "auth":
        raise SystemExit(_auth(args.forwarded))
    if cmd == "move":
        ap2 = argparse.ArgumentParser(prog="membrane move")
        ap2.add_argument("--from", dest="src", required=True)
        ap2.add_argument("--to", dest="dst", required=True)
        ap2.add_argument("--origin", action="append", default=None,
                         help="only these origins' docs (repeatable)")
        mv = ap2.parse_args(args.forwarded)
        sys.argv = ["membrane move", "--from", mv.src, "--to", mv.dst] \
            + (["--origin"] + [o for o in mv.origin] if mv.origin else [])
        from .move import main as move_main
        raise SystemExit(move_main())
    if cmd == "migrate":
        raise SystemExit(_migrate(args.forwarded))
    if cmd == "ingest":
        argv2 = _ensure_data_dir(args.forwarded)
        raise SystemExit(_run_module("membrane.ingest", argv2))
    if cmd == "backfill":
        # the entity field rebuilt from the audit log + the codebase
        argv2 = args.forwarded
        if "--field" not in argv2:
            argv2 = ["--field", str(FIELD)] + argv2
        if "--audit" not in argv2:
            argv2 = ["--audit", str(DATA_DIR / "audit.jsonl")] + argv2
        raise SystemExit(_run_module("membrane.backfill", argv2))
    if cmd == "codegraph":
        raise SystemExit(_run_module("membrane.codegraph", args.forwarded))
    if cmd in ("opencode", "reset", "serve"):
        couchbase_onboarding()
        maybe_check_on_boot(args.forwarded)
    if cmd == "reset":
        raise SystemExit(_reset(args.forwarded))
    if cmd in ("tui", "anthropic", "demo", "proxy"):
        # THE TUI IS RETIRED (the operator's call): opencode is the only
        # real seat -- the toy world's surfaces are cut. The commands
        # remain as pointers, not launchers -- a silent fall-back to a
        # dead surface teaches the wrong habit.
        print(f"membrane {cmd} is retired -- the opencode seat is the "
              "real surface now:\n"
              "  membrane opencode     (field-backed opencode on :11500)",
              file=sys.stderr)
        raise SystemExit(2)
    if cmd == "opencode":
        argv2 = _ensure_data_dir(args.forwarded)
        raise SystemExit(_run_module("membrane.opencode", argv2))
    if cmd == "serve":
        argv2 = _ensure_data_dir(["--serve"] + args.forwarded)
        raise SystemExit(_run_module("membrane.opencode", argv2))
    if cmd == "review":
        argv2 = _ensure_data_dir(args.forwarded)
        raise SystemExit(_run_module("membrane.review", argv2))
    raise SystemExit(_run_module("membrane." + cmd, args.forwarded))


if __name__ == "__main__":
    main()