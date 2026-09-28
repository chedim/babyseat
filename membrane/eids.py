"""CANONICAL ENTITY IDS (pure): one id per thing, whatever the text calls
it, plus each entity's fixed random KEY.

Every word, recurring phrase and specific identifier is an entity. Its id
is `<kind>:<norm>`:

  w     word                 w:router            (lowercased, plural folded)
  p     phrase (2-3 words)   p:receipt lane
  f     file                 f:membrane/opencode.py (repo-relative when the
                             code graph knows it) | f:~opencode.py (bare/foreign)
  s     symbol               s:membrane.opencode._pick_listen_port (module-
                             qualified via the graph's defs) | s:?name (unresolved)
  c     command              c:pytest
  port  port                 port:11500
  m     model                m:glm-5.3-flash:cloud
  u     url                  u:<host/path>
  ha    home-assistant id    ha:sensor.x
  tool  tool name            tool:edit
  ctx   context              ctx:session:<sid>

The KEY is a hash-random bipolar hypervector derived from the id (never
stored, never semantic): it keeps an entity's contribution separable
when composites are bundled. Role vectors are hash-random too -- E25
showed roles taken from the semantic codebook are near-equal and cannot
encode order."""

from __future__ import annotations

import re
from pathlib import Path

from . import vsa
from .entities import extract_entities
from .slash import STOP

KINDS = ("w", "p", "f", "s", "c", "port", "m", "u", "ha", "tool", "ctx")
SPECIFIC = frozenset(("f", "s", "c", "port", "m", "u", "ha", "tool"))
MAX_MENTIONS = 16
_WORD = re.compile(r"[A-Za-z][A-Za-z_]+")
_LEGACY_KIND = {"files": "f", "symbols": "s", "models": "m", "shell": "c",
                "urls": "u", "ha": "ha"}


def norm_word(w: str) -> str:
    """Lowercase + the E19 plural fold ('tests' -> 'test', 'class' stays)."""
    w = (w or "").lower().strip()
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w


def _abs_root(graph: dict | None) -> str:
    """The repo root as an absolute path without its leading '/', lowered
    (extracted paths sometimes lose the slash: 'Users/x/repo/a.py')."""
    root = str((graph or {}).get("root") or ".")
    try:
        root = str(Path(root).resolve())
    except OSError:
        pass
    return root.strip("/").lower() + "/"


def _resolve_file(path: str, graph: dict | None) -> str:
    p = (path or "").strip().strip("`'\"")
    root = _abs_root(graph)
    if p.lstrip("/").lower().startswith(root):
        p = p.lstrip("/")[len(root):]            # an absolute path into this repo
    low = p.lower()
    if graph:
        files = graph.get("files") or {}
        if p in files:
            return "f:" + p
        hits = [f for f in files if f.lower() == low or f.lower().endswith("/" + low)]
        if len(hits) == 1:
            return "f:" + hits[0]
    if "/" in p and not p.startswith(("/", "~", "..")):
        return "f:" + p
    if p and not p.startswith(("/", "~")) and graph and graph.get("root") \
            and (Path(str(graph["root"])) / p).is_file():
        return "f:" + p                          # a top-level repo file (README.md)
    return "f:~" + p.rsplit("/", 1)[-1]


def _resolve_symbol(sym: str, graph: dict | None) -> str:
    s = (sym or "").strip().strip("`'\"()")
    if graph:
        defs = graph.get("defs") or {}
        if s in defs:
            return "s:" + s
        by_qual = [d for d, info in defs.items() if info.get("qual") == s]
        if len(by_qual) == 1:
            return "s:" + by_qual[0]
        short = (graph.get("short_index") or {}).get(s.rsplit(".", 1)[-1], [])
        if len(short) == 1:
            return "s:" + short[0]
    return "s:?" + s.lower()


def canon(kind: str, surface: str, graph: dict | None = None) -> str:
    """The canonical id for one mention."""
    if kind == "w":
        return "w:" + norm_word(surface)
    if kind == "p":
        return "p:" + " ".join(norm_word(w) for w in surface.split())
    if kind == "f":
        return _resolve_file(surface, graph)
    if kind == "s":
        return _resolve_symbol(surface, graph)
    if kind == "c":
        return "c:" + surface.rsplit("/", 1)[-1].lower()
    if kind in ("port", "tool", "ctx"):
        return f"{kind}:{surface.strip().lower()}"
    return f"{kind}:{surface.strip().lower()}"


def _is_command_use(sentence: str, verb: str) -> bool:
    """A shell verb in prose is a COMMAND only when it is written as one: in
    backticks or after a '$ ' prompt (tool observations take their commands
    from the call's args instead)."""
    v = re.escape(verb)
    if re.search(rf"`[^`]*(?:^|[\s/]){v}\b[^`]*`", sentence, re.IGNORECASE):
        return True
    return re.search(rf"(?:^|\s)\$\s+{v}\b", sentence, re.IGNORECASE) is not None


def _backtick_commands(sentence: str) -> list[str]:
    """The shell verbs that open a backticked span (`git status`)."""
    from .entities import _SHELL_VERBS
    verbs = {v.lower() for v in _SHELL_VERBS}
    out = []
    for span in re.findall(r"`([^`\n]+)`", sentence or ""):
        head = span.strip().split()[0].rsplit("/", 1)[-1].lower() if span.strip() else ""
        if head in verbs:
            out.append(head)
    return out


def mentions_of(sentence: str, graph: dict | None = None,
                phrase_vocab: set | frozenset | None = None
                ) -> list[tuple[str, str]]:
    """(eid, surface) for every entity a sentence mentions: specific
    identifiers first, then maximal known phrases, then the remaining
    content words. Capped at MAX_MENTIONS."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    covered = set()

    def add(eid, surface):
        if eid not in seen and len(out) < MAX_MENTIONS:
            seen.add(eid)
            out.append((eid, surface))

    for kind, vals in extract_entities(sentence, "").items():
        k = _LEGACY_KIND.get(kind)
        if not k:
            continue
        for v in vals:
            if k == "c" and not _is_command_use(sentence, v):
                continue                      # 'membrane keeps picking...' is prose
            add(canon(k, v, graph), v)
            covered.update(w.lower() for w in _WORD.findall(v))
    for v in _backtick_commands(sentence):
        add(canon("c", v, graph), v)
        covered.add(v)
    words = [w for w in _WORD.findall(sentence or "")]
    low = [w.lower() for w in words]
    if phrase_vocab:
        taken = [False] * len(low)
        for n in (3, 2):
            for i in range(len(low) - n + 1):
                if any(taken[i:i + n]):
                    continue
                g = low[i:i + n]
                if any(t in STOP or len(t) < 3 for t in g):
                    continue
                ph = " ".join(g)
                if ph in phrase_vocab:
                    add(canon("p", ph), ph)
                    for j in range(i, i + n):
                        taken[j] = True
                    covered.update(g)
    for w in low:
        if len(w) >= 3 and w not in STOP and w not in covered:
            add(canon("w", w), w)
    return out


def legacy_to_canon(old: str, graph: dict | None = None) -> str | None:
    """Old entity-layer ids -> canonical ids: 'membrane::files:x',
    'membrane::Component:files:x', 'symbols:x', 'synth:<slug>',
    'models:x', 'shell:x'. Timelines, junk anchors and now-clocks have no
    canonical form (None)."""
    s = old or ""
    if s.startswith("membrane::"):
        s = s[len("membrane::"):]
    head, _, rest = s.partition(":")
    if not rest:
        return None
    if head in _LEGACY_KIND:
        return canon(_LEGACY_KIND[head], rest, graph)
    if head == "synth":
        words = rest.replace("-", " ").replace("_", " ").split()
        if not words:
            return None
        return canon("p" if len(words) > 1 else "w", " ".join(words))
    if head[:1].isupper():                      # 'Component:files:x' etc.
        return legacy_to_canon(rest, graph)
    return None


def entity_key(eid: str, dim: int = vsa.DEFAULT_DIM):
    """The entity's identity hypervector: hash-random from its id."""
    return vsa.hash_token_vec("ek|" + eid, dim)


def role_key(role: str, dim: int = vsa.DEFAULT_DIM):
    """A relation role vector (subj / rel / obj): hash-random, never the
    semantic codebook (E25)."""
    return vsa.hash_token_vec("role|" + role, dim)


def is_specific(eid: str) -> bool:
    return eid.split(":", 1)[0] in SPECIFIC
