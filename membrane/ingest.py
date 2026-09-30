"""Cold-start ingest: turn a repo's existing text into field cells BEFORE any
turn happens (Tencent's cold-start, in membrane's idiom).

The field's weakness is the first week: nothing learned yet, so recall is
empty and the gate has nothing to replay. Yet the knowledge EXISTS -- in
README, docs/*.md, design notes, agent logs. This module reads those files,
chunks them on headings/size, and absorbs each chunk as a PRE-COMPLETED
(one DOC event per chunk -- the SAME guarded
pipeline as teed turns (vague/dedup/variety; an embedding per chunk, one at a
time, no model call, local mxbai only).

The pair form is document-shaped on purpose:
    situation = "<rel-path> :: <first line of the chunk>"   (what a future
                turn would look like: a question in this region)
    action    = the chunk body
so a later query near the topic retrieves the doc text as the action -- the
same shape total-capture turns already give the field.

What it deliberately ingests: *.md, *.txt, *.py, *.sh, *.toml, *.json
(source is memory too: the file's docstrings and structure ARE the project's
explanation of itself). What it refuses: anything under the runtime/secret
set (.git, .membrane, node_modules, venvs, build output), files over 512KB,
binary-looking files (NUL probe), and per-line JSONL state (audit logs) via
the .jsonl extension exclusion.

Ingest exemplars are tagged ``ingest`` via the audit trail (origin: ingest)
so they are FORENSICALLY separable: a ``membrane: forget <time-window>``,
the topic forget, or a future origin-filter can remove them wholesale
without touching lived turns.

Usage:
    python -m membrane.ingest [paths...] [--root .] [--max <n>] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "build", "dist",
              ".membrane", ".opencode", "__pycache__", "worlds", ".claude",
              ".idea", ".mypy_cache", ".pytest_cache"}

TEXT_EXT = {".md", ".txt", ".py", ".sh", ".toml", ".json"}
SKIP_EXT = {".jsonl"}          # machine state logs, never memory material

MAX_FILE_BYTES = 512 * 1024
CHUNK_CHARS = 1200             # ~300 tokens: a paragraph-cluster, not a paste
MIN_CHUNK_CHARS = 80           # tinier than this is noise (a heading alone)


def _candidate_files(paths: list[str], root: Path) -> list[Path]:
    """Files to consider: the named paths, or a default repo sweep (docs,
    README, top-level source) when called bare."""
    out: list[Path] = []
    seen: set = set()
    if paths:
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                for f in sorted(pp.rglob("*")):
                    if _want(f, root) and f not in seen:
                        seen.add(f)
                        out.append(f)
            elif _want(pp, root):
                if pp not in seen:
                    seen.add(pp)
                    out.append(pp)
        return out
    # bare call: docs + readme + top-level py/toml (a REPO is not a corpus;
    # deep src trees are the codegraph's job, not doc memory)
    for f in sorted(root.rglob("*")):
        if f.is_dir() or not _want(f, root):
            continue
        rel = f.relative_to(root).as_posix()
        top = rel.split("/")[0]
        depth = len(Path(rel).parts)
        keep = (rel.startswith("docs/") or top == "README.md"
                or (depth <= 2 and top in ("AGENTS.md", "INSTALL.md",
                                           "CHANGELOG.md")))
        if keep and f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _want(f: Path, root: Path) -> bool:
    if f.suffix.lower() in SKIP_EXT:
        return False
    if f.suffix.lower() not in TEXT_EXT:
        return False
    try:
        parts = f.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return False
    if any(p in _SKIP_DIRS for p in parts):
        return False
    try:
        st = f.stat()
    except OSError:
        return False
    return 0 < st.st_size <= MAX_FILE_BYTES


def _chunk_text(text: str, source: str) -> list[tuple[str, str]]:
    """Split one file into (situation, action) chunks. Markdown splits on
    ATX headings when present (a section IS the natural cell); otherwise on
    the CHUNK_CHARS budget, biasing breaks to paragraph boundaries."""
    text = text.strip()
    if not text:
        return []
    sections: list[str] = []
    if source.endswith(".md") and "\n#" in ("\n" + text):
        cur: list[str] = []
        for line in text.splitlines():
            if line.startswith("#") and cur:
                sections.append("\n".join(cur).strip())
                cur = [line]
            else:
                cur.append(line)
        sections.append("\n".join(cur).strip())
    else:
        # rolling budget with paragraph-bias breaks
        buf = ""
        for para in text.split("\n\n"):
            if len(buf) + len(para) > CHUNK_CHARS and buf.strip():
                sections.append(buf.strip())
                buf = para
            else:
                buf = (buf + "\n\n" + para).strip()
        if buf.strip():
            sections.append(buf.strip())
    out: list[tuple[str, str]] = []
    for sec in sections:
        if len(sec) < MIN_CHUNK_CHARS:
            # tiny section: fuse with the previous chunk if there is one,
            # else drop (a bare heading is noise, not memory)
            if out and len(out[-1][1]) + len(sec) < CHUNK_CHARS * 2:
                prev_src, prev = out[-1]
                out[-1] = (prev_src, prev + "\n\n" + sec)
            continue
        for i in range(0, len(sec), CHUNK_CHARS):
            piece = sec[i:i + CHUNK_CHARS].strip()
            if len(piece) < MIN_CHUNK_CHARS and out:
                out[-1] = (out[-1][0], out[-1][1] + "\n" + piece)
                continue
            if len(piece) < MIN_CHUNK_CHARS:
                continue
            first = next((ln.strip("# ").strip()
                          for ln in piece.splitlines() if ln.strip()), source)
            out.append((f"{source} :: {first[:120]}", piece))
    return out


def ingest_events(field, paths: list[str] | None = None, root: str | Path = ".",
                  max_chunks: int = 400, dry_run: bool = False) -> dict:
    """The ingest lane on the entity field: each chunk is a DOC event
    (discourse, role 'doc', src 'ingest:<file>'); content keys make a second
    ingest of the same text a no-op."""
    from .streams import Event
    rootp = Path(root)
    files = _candidate_files(list(paths or []), rootp)
    chunks, skipped_binary = [], 0
    for f in files:
        try:
            raw = f.read_bytes()
            text = raw.decode("utf-8") if b"\x00" not in raw[:4096] else None
        except (OSError, UnicodeDecodeError):
            text = None
        if text is None:
            skipped_binary += 1
            continue
        src = f.relative_to(rootp).as_posix() if f.is_relative_to(rootp) else f.as_posix()
        chunks += _chunk_text(text, src)
    absorbed = dups = 0
    for label, body in chunks[:max_chunks]:
        if dry_run:
            absorbed += 1
            continue
        before = int(field._ostats.get("ev_n") or 0)
        field.write_events([Event(stream="discourse", role="doc", text=body,
                                  src="ingest:" + label.split(" :: ", 1)[0])], session="ingest")
        if int(field._ostats.get("ev_n") or 0) > before:
            absorbed += 1
        else:
            dups += 1
    if not dry_run:
        field.process()
    return {"files": len(files), "chunks": len(chunks), "absorbed": absorbed, "dedup": dups,
            "skipped_binary": skipped_binary, "truncated": max(0, len(chunks) - max_chunks)}


def report(res: dict, dry_run: bool = False) -> str:
    head = ("dry run -- nothing written" if dry_run
            else "written as DOC events on the entity field")
    return (f"[membrane-ingest] {head}: {res['files']} files -> "
            f"{res['chunks']} chunks ({res['absorbed']} absorbed, "
            f"{res['dedup']} deduped, {res['skipped_binary']} binary skipped"
            + (f", {res['truncated']} over --max" if res['truncated'] else "")
            + ")")


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="membrane ingest",
        description="cold-start: absorb docs/notes into the field "
                    "(no model call)")
    ap.add_argument("paths", nargs="*", help="files/dirs to ingest "
                                             "(default: docs/ + README)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--field", default=".membrane/field.json")
    ap.add_argument("--audit", default=".membrane/audit.jsonl")
    ap.add_argument("--max", type=int, default=400, dest="max_chunks")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from .embed import OllamaEmbedder
    from .capture import mode_from_env
    if mode_from_env() == "on":
        from .efield import EntityField
        from .efstore import open_backend
        from .substrate import _origin_tag
        from .project_env import load_project_env
        load_project_env()
        fld = EntityField(open_backend(args.field), OllamaEmbedder(), _origin_tag(args.field))
        res = ingest_events(fld, args.paths or [], root=args.root,
                        max_chunks=args.max_chunks, dry_run=args.dry_run)
        print(report(res, args.dry_run))
        return
if __name__ == "__main__":
    main()