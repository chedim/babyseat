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

KINDS = ("w", "p", "f", "s", "c", "port", "m", "u", "ha", "tool", "ctx", "person", "seat")
# person/seat: who said it (speakers.py) -- live at the first word they say
SPECIFIC = frozenset(("f", "s", "c", "port", "m", "u", "ha", "tool", "person", "seat"))
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


_NON_SRC_SEGMENTS = ("build", "dist", "__pycache__", ".git", ".venv", "node_modules")

def _non_src(path: str) -> bool:
    q = "/" + (path or "").lower().strip("/")
    return any(("/" + seg + "/") in q for seg in _NON_SRC_SEGMENTS)

def _resolve_file(path: str, graph: dict | None) -> str:
    p = (path or "").strip().strip("`'\"")
    root = _abs_root(graph)
    if p.lstrip("/").lower().startswith(root):
        p = p.lstrip("/")[len(root):]            # an absolute path into this repo
    low = p.lower()
    if graph:
        # THE SNAPSHOT (the 'dictionary changed size during iteration'
        # crash): a copy — the codegraph lane mutates files in place
        # from refresh threads while THIS resolve iterates it.
        files = dict(graph.get("files") or {})
        if p in files:
            return "f:" + p
        hits = [f for f in files if (f.lower() == low or f.lower().endswith("/" + low)) and not _non_src(f)]
        if len(hits) == 1:
            return "f:" + hits[0]
    if "/" in p and not p.startswith(("/", "~", "..")):
        return "f:" + p
    if p and not p.startswith(("/", "~")) and graph and graph.get("root") \
            and (Path(str(graph["root"])) / p).is_file():
        return "f:" + p                          # a top-level repo file (README.md)
    if p and not p.startswith(("/", "~")) and graph and graph.get("root"):
        _r = Path(str(graph["root"])).resolve()
        try:
            _hits = sorted(q for q in _r.glob("**/" + p) if q.is_file() and not _non_src(q.as_posix()))
        except OSError:
            _hits = []
        if len(_hits) == 1:
            return "f:" + _hits[0].relative_to(_r).as_posix()
    return "f:~" + p.rsplit("/", 1)[-1]


def _resolve_symbol(sym: str, graph: dict | None) -> str:
    s = (sym or "").strip().strip("`'\"()")
    if graph:
        # THE SNAPSHOT: same contract as _resolve_file's copy above --
        # defs mutates under rebuild_paths while this iterates.
        defs = dict(graph.get("defs") or {})
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


# ---- THE NURSERY GHOSTS (2026-10-11, Argus's lesson: "chopped turns can land
# mangled-fragment entities (m:m:...-style) that the graph layer reads but
# query and remove can never reach -- poking at them replicates them") -----
# The model-tag regex took ANY word:word token: 'a:b', a MAC's 'ba:c4:f5',
# 'adb:5565', 'c:-prefixed', and a 404 naming 'm:a:b' minted 'm:m:a:b'. A
# real m: id is a model tag (glm-5.3-flash:cloud, qwen3:8b) or a store
# entity's name (the field's mirror; efield's name_ok decides that one).
MODEL_TAG_WORDS = frozenset(("latest", "cloud", "instruct", "chat", "base", "text", "embed", "code"))
_HEX = re.compile(r"[0-9a-f]{1,4}")


def is_junk_tag(v: str) -> bool:
    """A fragment no real name has: nested mirror, a one-letter or dotted
    or dashed-lead segment, '...', a MAC-like hex run, a host:port."""
    s = str(v or "").strip().lower()
    segs = s.split(":")
    if len(segs) < 2 or segs[0] in ("m", "membrane", ""):
        return True
    for x in segs:
        if len(x) < 2 or x[0] in "-." or x[-1] in "-." or ".." in x:
            return True
    if all(_HEX.fullmatch(x) for x in segs) and len(segs) >= 2 and any(ch.isdigit() for ch in s):
        return True
    if len(segs) == 2 and segs[1].isdigit():
        return True
    return False


def is_model_tag(v: str) -> bool:
    """A real model's name:tag (one colon, a version digit or a tag word)."""
    s = str(v or "").strip().lower()
    if is_junk_tag(s):
        return False
    segs = s.split(":")
    if len(segs) != 2:
        return False
    name, tag = segs
    if name in KINDS or name in _LEGACY_KIND or len(name) < 3:
        return False
    return any(ch.isdigit() for ch in name + tag) or tag in MODEL_TAG_WORDS


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
            if k == "m" and is_junk_tag(v):
                continue                      # a fragment, not a name (the nursery ghosts)
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


# ---- THE NAME LINT (2026-10-08, the seat's own review) -----------------------
# "m:m:c:agdroid:doorbell (doubled prefix) ... c:{'cmd': -- a dict-literal as a
# command name ... doorbell under two nodes (m:m:c: and m:component:c:) --
# split-brain." `m:` / `membrane::` is the field's MIRROR of membrane's own
# entities -- fine to READ (a track key), a bug inside a NAME a model creates.
NAME_MAX = 120
KEY_MAX = 240
_BAD_CHARS = set("{}[]\"'`<>\\")
_CLASS_HEADS = {"component", "decision", "failuremode", "aimodel", "endpoint",
                "config", "goal", "sensor", "actuator", "project", "session",
                "timeline", "seat", "concept"}


def _plain(s: str, cap: int) -> str | None:
    if any(ord(ch) < 32 for ch in s):
        return "names are plain one-line identifiers (no newlines/tabs)"
    if _BAD_CHARS & set(s):
        return ("no brace/bracket/quote characters in a name (a dict or list "
                "literal is not a name)")
    if len(s) > cap:
        return f"too long ({len(s)} > {cap}): a name, not a sentence"
    return None


def lint_name(name: str) -> str | None:
    """None = a clean name a model may CREATE; else why not (+ the clean
    suggestion where one exists)."""
    s = str(name or "").strip()
    if not s:
        return "a name is required"
    bad = _plain(s, NAME_MAX)
    if bad:
        return bad
    if s.startswith("membrane::") or s.split(":", 1)[0] == "m":
        clean = s
        while True:
            if clean.startswith("membrane::"):
                clean = clean[len("membrane::"):]
            elif clean.split(":", 1)[0] == "m" and ":" in clean:
                clean = clean.split(":", 1)[1]
            else:
                break
        return (f"'m:' / 'membrane::' is the field's MIRROR prefix, not part "
                f"of a name (it would land twice) -- name it '{clean}'")
    segs = s.split(":")
    if len(segs) > 1 and segs[0].lower() in _CLASS_HEADS:
        rest = ":".join(segs[1:])
        return (f"the class rides cls=, not the name (one referent, one node) "
                f"-- name it '{rest}'")
    if len(segs) > 2 and segs[0] in KINDS and segs[1] in KINDS:
        rest = ":".join(segs[1:])
        return f"stacked kind prefixes '{segs[0]}:{segs[1]}:' -- name it '{rest}'"
    return None


def lint_key(key: str) -> str | None:
    """A track / watch KEY: may name the mirror (it reads an existing
    entity); only the shape is checked."""
    s = str(key or "").strip()
    if not s:
        return "a key is required"
    return _plain(s, KEY_MAX)


# ---- THE ONE SPELLING (2026-10-11 sweep: one stored entity lived as up to four
# field states -- 'm:component:decision' from prose, bare 'decision' from
# readings, nothing from script tells, 'membrane::tool:clock' from ticks -- and
# every cross-store lookup missed somewhere). The store side resolves any
# spelling with EntityStore.resolve; this is the other direction.
_NS_PREFIX = "membrane::"


def field_id(eid: str) -> str:
    """A store eid (or any spelling of one) -> its canonical field id: the
    long heads shorten (files:x -> f:x, shell:x -> c:x), a class-embedded
    name whose inner part is a kind is that kind (Component:ha:light.x ->
    ha:light.x), a kind keeps itself (tool:clock), everything else is the
    mirror m:<name> -- the same id prose about it gets ('Component:decision'
    -> m:component:decision)."""
    s = str(eid or "").strip()
    if s.lower().startswith(_NS_PREFIX):
        s = s[len(_NS_PREFIX):]
    for _ in range(4):
        head, _, rest = s.partition(":")
        h = head.lower()
        if not rest:
            return "m:" + s.lower() if s else ""
        if h in _LEGACY_KIND:
            return _LEGACY_KIND[h] + ":" + rest
        if h == "m":
            return "m:" + rest.lower()
        if h in KINDS:
            return h + ":" + rest
        rh = rest.partition(":")[0].lower()
        if rh in KINDS or rh in _LEGACY_KIND:
            s = rest                      # Component:ha:x -> ha:x
            continue
        return "m:" + s.lower()
    return "m:" + s.lower()
