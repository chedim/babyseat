"""The two commands `membrane` and `babyseat`: start the seat. This is ALL the Python the seat has: it finds the `cliffvm` binary the wheel
carries and replaces itself with it (`os.execv`), so a SIGTERM from `docker stop` or ctrl+c lands on the Rust seat, not on a wrapper.
No flags are parsed, no defaults chosen, no secret or store path is known here: `cliffvm serve` owns them (docs/python-retirement.md)."""
import os
import sys
from pathlib import Path

from . import __version__

HERE = Path(__file__).resolve().parent
# the subcommands of the Python seat that are not in this release: each says so and exits 2 (a command must not vanish silently)
GONE = ("opencode", "review", "ingest", "backfill", "move", "toybox", "self", "shadow", "store", "connect", "auth", "doctor",
        "bench", "codegraph", "erase-field", "install")
LAST_PYTHON = "babyseat==0.7.53"


def binary() -> str | None:
    """The cliffvm binary: $CLIFFVM, else the wheel's (dist/bin/cliffvm next to this file), else a development checkout's release build."""
    for c in (os.environ.get("CLIFFVM"), HERE / "dist" / "bin" / "cliffvm", HERE.parent / "native" / "target" / "release" / "cliffvm"):
        if c and os.access(c, os.X_OK) and Path(c).is_file():
            return str(c)
    return None


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--version", "-V"):
        print(f"babyseat {__version__}")
        raise SystemExit(0)
    if args and args[0] in GONE:
        print(f"`{args[0]}` is not in this release (the Python seat is retired); `pip install {LAST_PYTHON}` has it.", file=sys.stderr)
        raise SystemExit(2)
    exe = binary()
    if exe is None:
        print("no binary for this platform: this install carries no cliffvm (set CLIFFVM to one, or build native/ with "
              "`cargo build --release --no-default-features --features bin`).", file=sys.stderr)
        raise SystemExit(1)
    os.execv(exe, [exe, "serve", *args])


if __name__ == "__main__":
    main()
