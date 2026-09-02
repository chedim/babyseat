"""Entity extraction + the shared symbolic layer (the cortex-experiment
QuantumEntity model, retold for membrane).

WHY: 95% of field cells are capture machinery (tool calls/results), and the
~5% prose cells recall by cosine alone -- a query mentioning "router.py" or
"ling-3.0" cannot reach the cells that carry those terms when the vectors sit
at sim 0.7. Entities add a PRECISE axis: `entity -> [cell]` term hits
union-recalled with vector top-k.

Two extractors, two costs:

- ``extract_entities`` -- DETERMINISTIC, $0, runs at capture time on every
  turn: file paths, dotted symbols, model names, tool names, http endpoints,
  shell verbs, HA/telemetry ids. Stored in the exemplar's
  ``extra["entities"]`` (the field row carries its own tags -- no second
  index to lose).

- ``synthesize_entities`` -- the BACKGROUND LLM pass (idle cortex, compactor
  cadence): derives ABSTRACT entities (decisions, failure modes, components,
  goals) over a compacted cluster of cells, the cortex cron-job pattern at
  90s-idle cadence instead of nightly. Zero chat-turn cost, zero cloud cost.

COMPAT (cortex-experiment/src/cortex/core/db.py): entities are QuantumEntity
docs -- {meta.type: "QuantumEntity", type: "entities",
state_space.position.semantic_embeddings, data: {class, properties}}. The
write-through upserts doc id ``membrane::<entity>`` (namespaced, CAS-safe)
into the SAME ``entities`` collection, embedding via the SAME mxbai embedder
-- one semantic space across both systems. ``cls`` is drawn from a fixed
vocabulary so the two systems' extractors stay mergeable.
"""

from __future__ import annotations

import json
import os
import re
import time as _t
import sys
from dataclasses import dataclass, field as dfield
from pathlib import Path

# ---- the shared class vocabulary (the mergeable contract) -------------------
CLASS_COMPONENT = "Component"          # code module / service / file
CLASS_DECISION = "Decision"            # a settled choice with rationale
CLASS_FAILURE = "FailureMode"          # a learned failure + its fix
CLASS_MODEL = "AIModel"                # an LLM/embedder rung
CLASS_TOOL = "Tool"                    # a named tool the agent uses
CLASS_ENDPOINT = "Endpoint"            # http/api surface
CLASS_CONFIG = "Config"                # a knob, flag, env var, path
CLASS_GOAL = "Goal"                    # an open commitment / intention
CLASS_SENSOR = "Sensor"                # cortex-side telemetry entity
CLASS_ACTUATOR = "Actuator"            # cortex-side control entity
CLASS_PROJECT = "Project"              # the opened workspace (its own arc)
CLASS_SESSION = "Session"              # one conversation's dynamics
CLASSES = (CLASS_COMPONENT, CLASS_DECISION, CLASS_FAILURE, CLASS_MODEL,
           CLASS_TOOL, CLASS_ENDPOINT, CLASS_CONFIG, CLASS_GOAL,
           CLASS_SENSOR, CLASS_ACTUATOR, CLASS_PROJECT, CLASS_SESSION)

# membrane-namespace doc-id prefix in the shared entities collection
MEMBRANE_NS = "membrane::"

# ---- deterministic extraction (capture time, $0) -----------------------------
_FILE_RE = re.compile(
    r"(?:[\w.-]+/)*[\w.-]+\.(?:py|md|json|jsonc|ts|js|jsonl|toml|yaml|yml|sh|html|css|txt)\b")
_SYMBOL_RE = re.compile(r"\b[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*){1,3}\b"
                        r"|\b[a-zA-Z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_MODEL_RE = re.compile(r"\b[a-zA-Z][\w.-]*(?::[\w.-]+){1,3}\b")
_URL_RE = re.compile(r"https?://[^\s\"'`)\]}]+")
_HA_RE = re.compile(r"\b(?:sensor|binary_sensor|light|switch|cover|fan|pump|battery)\.[\w]+")
_SHELL_VERBS = ("git", "docker", "kubectl", "brew", "pip", "npm", "pnpm",
                "cargo", "make", "curl", "ssh", "ollama", "pytest",
                "membrane", "python")
# prose dots that LOOK like symbols but are sentences ("we.think", "it.does")
_PRONOUN_HEADS = ("it", "we", "i", "they", "he", "she", "you", "this",
                  "that", "there", "here", "one", "something", "anything")


def extract_entities(situation: str, action: str = "") -> dict:
    """Deterministic entity harvest from one cell's text. $0, no model.

    The SITUATION is the primary source (what the cell is ABOUT); the
    action contributes only when the situation yields nothing -- otherwise
    every answer that mentions a model name tags the cell with it, and the
    tracks fill with cells that merely reference the entity in passing.
    A tag is a HINT (false positives acceptable); a miss just means vector
    recall stays the only path for that cell.

    THE ENTITY-MANAGEMENT EXCEPTION: a capture cell whose situation is an
    entity-management tool call (watch/entity add/edit/remove) has the
    entity as the OPERATION'S TARGET -- its mention in the action is not
    a passing reference. Those cells harvest situation + action, so a
    planted track's own cells tag the planted entity (the story lane's
    cell pool would otherwise be empty for exactly the cells that are
    structurally ABOUT an entity)."""
    sit_l = " ".join((situation or "").split()).lower()
    for pre in ("tool membrane_membrane_watch_entity for:",
                "tool membrane_membrane_unwatch_entity for:",
                "tool membrane_membrane_entity_add for:",
                "tool membrane_membrane_entity_edit for:",
                "tool membrane_membrane_entity_remove for:",
                "tool membrane_entity_add for:",
                "tool membrane_entity_edit for:",
                "tool membrane_entity_remove for:"):
        if sit_l.startswith(pre):
            merged = f"{situation}\n{action or ''}"
            return _harvest(merged)
    return _harvest(situation)   # SITUATION ONLY: what the cell is ABOUT


def _harvest(text: str) -> dict:
    out: dict[str, list[str]] = {}

    def add(kind: str, values) -> None:
        seen: set[str] = set()
        kept: list[str] = []
        for v in values:
            v = str(v).strip().rstrip(".,;:)")
            if v and v.lower() not in seen:
                seen.add(v.lower())
                kept.append(v)
        if kept:
            out[kind] = kept[:8]

    add("files", _FILE_RE.findall(text))
    for verb in _SHELL_VERBS:
        if re.search(rf"(?:^|\s){re.escape(verb)}\s+\S", text, re.IGNORECASE):
            out.setdefault("shell", []).append(verb)
    add("urls", _URL_RE.findall(text))
    add("ha", _HA_RE.findall(text))
    # model tags before the symbol pass (glm-5.3-flash:cloud is a model, not
    # a symbol); urls excluded (their dots are not symbols). A match whose
    # NEXT char in the source is '/' is a path PREFIX ('files:membrane' in
    # the entity key 'files:membrane/entities.py'), not a model.
    add("models", [m.group(0) for m in _MODEL_RE.finditer(text)
                   if not m.group(0).startswith("http")
                   and (m.end() >= len(text) or text[m.end()] != "/")])
    # dotted code symbols: keep Class.attr / module.fn / snake_case chains,
    # drop pronoun-sentences and everything already claimed above
    claimed = ({f.lower() for f in out.get("files", [])}
               | {m.lower() for m in out.get("models", [])}
               | {h.lower() for h in out.get("ha", [])})
    syms = []
    for c in _SYMBOL_RE.findall(text):
        low = c.lower()
        if low in claimed or c.endswith((".py", ".md", ".json", ".js", ".ts")):
            continue
        head = c.split(".")[0].lower()
        if head in _PRONOUN_HEADS:
            continue
        # require a signal: a Capitalized part, an underscore, or 3+ parts
        parts = c.split(".")
        if (any(p[:1].isupper() for p in parts) or "_" in c
                or len(parts) >= 3):
            syms.append(c)
    add("symbols", syms)
    return out


def entities_of(ex) -> dict:
    """A cell's harvested entity tags ({} for legacy rows / misses)."""
    extra = getattr(ex, "extra", None)
    if isinstance(extra, dict):
        e = extra.get("entities")
        if isinstance(e, dict):
            return e
    return {}


def iter_entities(ex) -> list[str]:
    """'kind:value' keys for one exemplar (lowercase, the index key space)."""
    return [f"{kind}:{v.lower()}"
            for kind, values in entities_of(ex).items()
            for v in (values or [])]


def query_entities(text: str) -> list[str]:
    """Entity keys a QUERY mentions (same extractor, query side)."""
    return [f"{kind}:{v.lower()}"
            for kind, values in extract_entities(text).items()
            for v in values]


def entity_index(exemplars) -> dict[str, list[int]]:
    """Invert the field: 'kind:value' -> [SAFE cell indexes, oldest first].

    Rebuilt on demand (the recall path embeds anyway; the scan is ~ms at
    field scale) -- no persistent index to invalidate."""
    inv: dict[str, list[int]] = {}
    for i, ex in enumerate(exemplars):
        if not getattr(ex, "safe", False):
            continue
        for k in iter_entities(ex):
            inv.setdefault(k, []).append(i)
    return inv


def _action_overlap(a: str, b: str) -> float:
    """Token-overlap between two action texts (0..1) -- the story-beat dedup
    measure (the same _action_overlap semantics runtime.py uses)."""
    sa = set((a or "").lower().split())
    sb = set((b or "").lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


ALIGN_FLOOR = 0.5
STALE_STORY_S = 24 * 3600.0   # a story whose newest beat is older -> silence
FRESH_HALF_LIFE = 6 * 3600.0  # a beat's freshness credit halves every 6h
FRESH_WEIGHT = 0.25           # freshness weight vs alignment (sim 0..1)

# ---- the zoom ladder (black-hole geometry for story beats) -----------------
# ONE rule, scaled at every grain: windows are GEOMETRIC (each ZOOM_STEP x
# wider than the last) with HALVING beat budgets (2, 1, 0, 0...) -- so the
# beat DENSITY vanishes into the past (~1/h now, ~1/8h, then bare counts).
# A window whose cell mass exceeds its budget COLLAPSES to an era count
# (the black-hole guard: precision x span stays bounded -- you cannot have
# a too-precise view of a too-large slice); a window with nothing starves
# silently. The ladder iterates until it covers the entity's whole age.
ZOOM_NOW_S = 2 * 3600.0       # the first (finest) window's span
ZOOM_STEP = 4.0               # each next window is 4x wider
ZOOM_BUDGETS = (3, 2)         # beat budgets per window (then 0: era counts)
ZOOM_ERA_MIN = 3              # cells in a window below this render prose

# ---- the scarcity covenant (the operator's ratio) --------------------------
# TRACKS are the EXPENSIVE lane (every one renders a paragraph in WHERE WE
# ARE, every turn) -> few: MAX_TRACKS=3 (the default now-track counts).
# WATCHES are the CHEAP lane (event-driven, silent until fired) -> more:
# watch.py's MAX_WATCHES=7. Attention is scarce by construction; overflow
# evicts the OLDEST planted track (never the default now-track).
MAX_TRACKS = 3
DEFAULT_TRACK_KEY = "session:now"


def _era(n: int, span_s: float) -> str:
    """A collapsed window: the mass without the detail."""
    h = max(1, round(span_s / 3600))
    return f"{n} changes over ~{h}h"


def _zoom_windows(oldest_age: float):
    """(start_age, end_age, budget) youngest-first, geometric until the
    entity's origin is covered. Budgets: ZOOM_BUDGETS then 0."""
    lo, wi = 0.0, 0
    while lo < max(oldest_age, ZOOM_NOW_S):
        span = ZOOM_NOW_S * (ZOOM_STEP ** wi)
        budget = ZOOM_BUDGETS[wi] if wi < len(ZOOM_BUDGETS) else 0
        yield lo, lo + span, budget
        lo += span
        wi += 1


def _arc(beats: list, borns: list, now: float, goal: str = "",
         era_min: int = ZOOM_ERA_MIN) -> str:
    """The story ARC, told the way stories are told: ORIGIN -> development
    -> NOW -> aiming. ``beats``: (born, frag), junk-screened, deduped,
    chronological. ``borns``: every junk-free cell birth (the window
    masses). The zoom ladder's vanishing-density rule holds at every
    grain -- NOW renders up to its beat budget, each older geometric
    window renders less per hour, and mass beyond a window's budget
    collapses to an era count (the black-hole guard: precision x span
    stays bounded). The origin era earns ONE coarse beat (the classic
    'began with' bookend); the arc always ENDS at the now.
    ``era_min``: the collapse threshold (the now-track earns a higher
    one -- its pool is pre-curated, so the detail lives at 'now')."""
    age_of = sorted(now - b for b in borns if b > 0)
    if not age_of:
        return ""
    oldest_age = age_of[-1]
    wins = list(_zoom_windows(oldest_age))
    chron: list[str] = []            # rendered oldest -> newest
    rendered: set[float] = set()     # beat birth stamps already in the arc
    # the window loop covers wi >= 1 only: the NOW window (wi == 0) is the
    # story's ANCHOR, rendered last by the block below (rendering it in the
    # loop too duplicated every fresh beat)
    for wi in range(len(wins) - 1, 0, -1):
        lo, hi, budget = wins[wi]
        in_win = [a for a in age_of if lo <= a < hi]
        if not in_win:
            continue                 # starved: silence, next window
        n = len(in_win)
        win_beats = [b for b in beats if lo <= now - b[0] < hi]
        is_origin = (wi == len(wins) - 1)
        if is_origin:
            # the bookend earns its coarse beats -- the origin era may
            # render up to 2 of its oldest clean fragments (a paragraph
            # deserves a real opening, not a single clause)
            piece = f"It began {_ago(now - oldest_age, now) or 'long ago'}"
            openers = win_beats[:2] if win_beats else []
            if openers:
                frags = [f[0].lower() + f[1:] for _, f in openers]
                piece += " with " + "; ".join(frags)
                rendered.update(b[0] for b in openers)
            if n > 2:
                piece += f" ({n} cells since)"
            chron.append(piece + ".")
        elif n <= ZOOM_ERA_MIN and budget > 0 and win_beats:
            frags = [f[0].lower() + f[1:] for _, f in win_beats[-budget:]]
            chron.append("Then " + "; ".join(frags) + ".")
            rendered.update(b[0] for b in win_beats[-budget:])
        else:
            chron.append(f"Then {_era(n, hi - lo)}.")
    # the NOW anchor last: the point the whole story lands on (and for a
    # young entity — one window total — it is the WHOLE story; the early
    # bail below must not fire before this block runs)
    lo, hi, budget = wins[0]
    in_now = [a for a in age_of if a < hi]
    now_beats = [b for b in beats if now - b[0] < hi]
    if in_now and len(in_now) <= era_min and now_beats:
        frags = [f[0].lower() + f[1:] for _, f in now_beats[-budget:]]
        chron.append("Right now: " + "; ".join(frags) + ".")
    elif in_now:
        chron.append(f"Right now it is active — {_era(len(in_now), hi)}.")
    elif [b for b in beats if b[0] not in rendered]:
        # the now-window starved but the entity LIVES (newest beat within
        # the stale gate): anchor on the last known state NOT already in
        # the arc (a duplicate anchor reads as stutter). NOTE: beats carry
        # ABSOLUTE birth stamps; _ago wants the timestamp, not the age.
        nb_born, nb_frag = max((b for b in beats if b[0] not in rendered),
                               key=lambda b: b[0], default=(0, ""))
        if nb_frag:
            chron.append(f"Last active {_ago(nb_born, now) or 'recently'}: "
                         f"{nb_frag[0].lower() + nb_frag[1:]}.")
    if not chron:
        return ""
    if goal:
        chron.append(f"Aiming for: {goal}.")
    return " ".join(chron)


_ENTITY_MGMT_PREFIXES = (
    "tool membrane_membrane_watch_entity for: ",
    "tool membrane_membrane_unwatch_entity for: ",
    "tool membrane_membrane_entity_add for: ",
    "tool membrane_membrane_entity_edit for: ",
    "tool membrane_membrane_entity_remove for: ",
    "tool membrane_entity_add for: ",
    "tool membrane_entity_edit for: ",
    "tool membrane_entity_remove for: ",
)


def _strip_tool_prefix(s: str) -> str:
    """Tool-capture prefixes off: the REST is the operator's words (the
    narrative fragment). Includes the entity-management captures -- their
    remainder is the planting request, a real story fragment."""
    for pre in ("tool edit for: ", "tool bash for: ", "tool call for: ",
                "reasoning: ", "plan: ", "compact note: ",
                *_ENTITY_MGMT_PREFIXES):
        if s.startswith(pre):
            return s[len(pre):].strip()
    return s


def _sit_text_of(exemplars, i):
    s = " ".join(str(exemplars[i].situation).split())
    for pre in ("tool edit for: ", "tool bash for: ", "tool call for: ",
                "tool membrane_membrane_state for: ",
                "tool membrane_membrane_query for: "):
        if s.startswith(pre):
            return s[len(pre):].strip()
    return s


def _frag_of(exemplars, i):
    e = exemplars[i]
    sit = _strip_tool_prefix(" ".join(str(e.situation).split()))
    if " args: {" in sit:
        sit = sit.split(" args:", 1)[0]
    act = " ".join(str(e.action or "").split())
    return sit, act


def _eph_born_of(exemplars, i):
    try:
        return float((getattr(exemplars[i], "extra", None) or {})
                     .get("eph_born", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


_MACHINERY_RES = (
    re.compile(r"\[\s*\d{1,3}%\]"),                # pytest progress bars
    re.compile(r"\d+ passed in [\d.]+s"),          # raw pytest summaries
    re.compile(r"\d+\.\d\s+\d+\.\d\s+\d{6,}"),     # ps CPU% MEM% VSZ columns
    re.compile(r'"(?:route|verdict|sim|field_size)":'),  # proxy JSON tables
    re.compile(r"\[opencode\] |\[membrane\] |\[rag\] "), # proxy log lines
    re.compile(r"<system-reminder>|</system-reminder>", re.I),  # harness noise
    re.compile(r'"(?:\$schema|autoupdate|npm|model|provider)"\s*:'),  # config JSON
    re.compile(r"COMMAND\s+PID\s+USER\s+FD\s+TYPE"),   # lsof tables
    re.compile(r"PID\s+STARTED\s+COMMAND"),            # ps lstart header
    # ps lines: time followed by a path -- 12h glued (1:38PM), 24h (08:52),
    # or lstart ("Mon Aug 31 02:24:06 2026"); prose "at 5:30 we met" has no path
    re.compile(r"\b\d{1,2}:\d{2}(?:[AP]M)?\s+[\w./\\-]*[\w-]+/[\w./-]+"),
    re.compile(r"\b\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s+[\w./-]*/"),
    re.compile(r"^#!/(usr/)?bin/(env )?\w+", re.M),   # shell script source
    re.compile(r"[\w-]+-[\w-]+-[\w-]+-[\w-]+-[0-9a-f]{8}\.env\b"),  # tmp env files
    # a model CATALOG (3+ 'name:tag' rungs in one line) is an inventory dump,
    # not a story: prose mentions one or two models, never eight
    re.compile(r"(?:[\w.-]+:[\w.-]+\s+){3,}"),
    # a tool-call args dump embedded mid-fragment: '-> edit({' / '— write({'
    re.compile(r"[-—>]+\s*\w+\(\s*[\{\"']"),
)


def _machinery_junk(s: str) -> bool:
    """Raw tool/proxy output (pytest bars, ps listings, route tables, log
    lines) -- never a story beat. The narrative form ('all 326 tests pass'
    inside a prose sentence) survives; the raw dump dies."""
    return any(rx.search(s) for rx in _MACHINERY_RES)


def _narrative(s: str, cap: int) -> str:
    """A cell's text -> clean story fragment: tool machinery stripped,
    teed-reasoning narration stripped, clipped at a SENTENCE boundary
    (never mid-word)."""
    s = " ".join(s.split())
    for pre in ("tool edit for: ", "tool bash for: ", "reasoning: ",
                "plan: ", "tool call for: ", "compact note: ",
                "cont: ",                              # teed continuation
                "tool membrane_membrane_state for: ",
                "tool membrane_membrane_query for: ",
                "tool membrane_membrane_learn for: "):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    if " args: {" in s:
        s = s.split(" args:", 1)[0]
    # field-state dumps and model lists are machinery, not narrative:
    # a beat that IS the proxy's instrument output dies
    if s.startswith(("Membrane state (live", "Membrane state (computed",
                     "--- [", "- field:")):
        return ""
    # strip the teed-reasoning narration ANYWHERE in the text (the model's
    # meta-voice quoting its own analysis): drop from "The user is <verb>ing
    # about" through the bridge, keep the substance after it.
    import re as _re
    m = _re.search(r'The user is saying [\'"](.+?)[\'"]\s*[-—]\s*it seems like\s+', s)
    if m:
        s = s[:m.start()] + m.group(1).strip() + ". " + s[m.end():].strip()
    else:
        m = _re.search(r'The user is \w+ing about [\'"]?(.+?)[\'"]?\s*[-—,\.]', s)
        if m:
            s = s[:m.start()] + m.group(1).strip() + ". " + s[m.end():].strip()
    for pre in ("Let me analyze the situation carefully.",
                "Let me look at what happened:",
                "Let me be precise about"):
        if s.startswith(pre):
            s = s[len(pre):].strip()
            break
    if len(s) <= cap:
        return s
    # sentence-boundary clip: cut at the last . ! ? within the cap
    cut = s[:cap]
    for sep in (". ", "! ", "? "):
        p = cut.rfind(sep)
        if p > cap * 0.5:
            return cut[:p + 1].strip()
    return cut.rstrip(",;:") + "…"


def _tool_artifact(act: str) -> bool:
    """A tool dump / diagnostics blob -> never a story action. "Edit
    applied successfully" is a real beat (the fix landed); what makes a
    beat junk is being PURE machinery -- raw tool args, a diagnostics wall
    with no narrative, or a banner echo."""
    a = " ".join(act.split())
    if not a:
        return True
    if a.startswith(("write(", "bash(", "grep(", "read(", "edit(",
                     "glob(", "task(", "todowrite(", "question(",
                     "membrane_", "{'", '{"',
                     "Field recall (membrane", "[membrane] route=",
                     "[membrane] turn:")):
        return True
    # trim a leading Edit-applied stamp: the REST decides
    core = a
    for lead in ("Edit applied successfully.", "Edit applied successfully",
                 "Edit applied",
                 "Entity track planted:",          # tool RESULT stamps
                 "Entity track planted"):
        if a.startswith(lead):
            a = a[len(lead):].strip()
            break
    if not a:
        return True                     # nothing but the stamp
    if a.startswith(("LSP errors", "LSP")):
        return True                     # diagnostics wall, no story
    return False


def _ago(born: float, now: float) -> str:
    """A birth stamp -> a human time reference for story beats."""
    if born <= 0 or born > now:
        return ""
    dt = max(0, now - born)
    if dt > 365 * 86400:            # absurd age (epoch-1970 test data)
        return ""
    dt = max(0, now - born)
    if dt < 90:
        return "just now"
    if dt < 3600:
        return f"{int(dt // 60)}m ago"
    if dt < 48 * 3600:
        return f"{int(dt // 3600)}h ago"
    return f"{int(dt // 86400)}d ago"


# ---- the cortex-compatible entity doc ---------------------------------------

@dataclass
class QuantumEntity:
    """The cortex-experiment entity doc (dataclass view; the wire shape is
    ``to_doc``). One entity = a named thing both systems see: membrane
    derives coding/decision entities, cortex derives sensor/actor entities;
    the shared class vocabulary + id namespace keep them mergeable."""

    eid: str                                    # membrane::<kind>:<slug>
    cls: str = CLASS_COMPONENT
    properties: dict = dfield(default_factory=dict)
    links: list[int] = dfield(default_factory=list)   # field cell back-refs
    derived_by: list[str] = dfield(default_factory=list)
    embedding: list | None = None               # 1024-d, the shared space
    position: list | None = None                # the LIVING summary vector
    position_history: list = dfield(default_factory=list)   # [[ts, pos]...]

    def describe(self) -> str:
        props = self.properties or {}
        return " ".join(str(props[k]) for k in ("name", "role", "summary")
                        if props.get(k)) or f"{self.cls} {self.eid}"

    def to_doc(self, embedder=None) -> dict:
        """The cortex wire shape (db.CouchbaseCortexDB.entities docs)."""
        vec = self.embedding
        if (not vec) and embedder is not None:
            try:
                vec = [float(x) for x in embedder.embed([self.describe()])[0]]
            except Exception:
                vec = None
        props = dict(self.properties)
        if self.links:
            props["links"] = list(self.links)
        if self.derived_by:
            props["derived_by"] = list(self.derived_by)
        doc = {
            "meta": {"type": "QuantumEntity"},
            "type": "entities",
            "vector_data": list(vec or []),
            "data": {"class": self.cls, "properties": props},
            "state_space": {"position": {"semantic_embeddings":
                                         list(vec or [])}},
        }
        # the LIVING geometry rides its own state_space lane (cortex-compat:
        # position.semantic_embeddings stays the STATIC embedding; the
        # tracker's drift lives in position.tracked + trajectory)
        if self.position:
            doc["state_space"]["position"]["tracked"] = list(self.position)
        if self.position_history:
            doc["state_space"]["trajectory"] = [
                [float(t), [float(x) for x in p]]
                for t, p in self.position_history]
        return doc

    @staticmethod
    def from_doc(doc_id: str, doc: dict) -> "QuantumEntity | None":
        """Parse a cortex entities doc; foreign/odd rows degrade to None
        (a doc without entity shape -- no ``data`` at all -- is not an
        entity, and the reader never crashes on a foreign row)."""
        try:
            if not isinstance(doc, dict) or not isinstance(doc.get("data"), dict):
                return None
            data = doc["data"]
            props = data.get("properties") or {}
            if not isinstance(props, dict):
                props = {}
            links = props.get("links") or []
            derived = props.get("derived_by") or ["cortex"]
            vec = (((doc.get("state_space") or {})
                    .get("position") or {}).get("semantic_embeddings"))
            tracked = (((doc.get("state_space") or {})
                        .get("position") or {}).get("tracked"))
            trajectory = (doc.get("state_space") or {}).get("trajectory")
            q = QuantumEntity(
                eid=str(doc_id),
                cls=str(data.get("class") or CLASS_COMPONENT),
                properties=props,
                links=[int(x) for x in links if str(x).isdigit()],
                derived_by=[str(x) for x in derived],
                embedding=list(vec or []),
                position=([float(x) for x in tracked]
                          if isinstance(tracked, (list, tuple)) and tracked
                          else None),
                position_history=([[float(t), [float(x) for x in p]]
                                   for t, p in trajectory]
                                  if isinstance(trajectory, list) else []),
            )
            return q
        except Exception:
            return None

    def merge(self, other: "QuantumEntity") -> None:
        """Derivation dedup: union properties/links/derived_by -- never a
        second doc for the same entity (the merge contract)."""
        for k, v in (other.properties or {}).items():
            if k not in self.properties:
                self.properties[k] = v
        for l in other.links:
            if l not in self.links:
                self.links.append(l)
        for d in other.derived_by:
            if d not in self.derived_by:
                self.derived_by.append(d)

    # ---- the living geometry (consolidation) --------------------------------
    # CELLS are evidence: immutable, full-precision, forever. ENTITIES are
    # trackers: their position is a derived, decaying summary that is
    # SUPPOSED to move (their meaning moves). The position drifts toward
    # the centroid of recent linked cells (lambda from the half-life, time-
    # based), and the position HISTORY ring coarsens with age: a delta-
    # perceptual store (only perceptible motion lands) + era anchors on
    # the half-life's own timescale -- important past stays, in ever less
    # space. Cells never rewrite; the tracker's autobiography compresses.

    def grain(self) -> float:
        """This entity's delta-perception threshold (the coarsening knob).
        Class default, overridable via properties['grain']; lives in the
        SHARED doc so both seats see the same knob."""
        g = (self.properties or {}).get("grain")
        try:
            g = float(g)
            if 0.001 <= g <= 0.5:
                return g
        except (TypeError, ValueError):
            pass
        return _GRAIN_DEFAULTS.get(self.cls, GRAIN_DEFAULT)

    def half_life_s(self) -> float:
        """The drift half-life: how long ago cells must be to pull the
        position with HALF the force of now-cells. Class-tuned via
        properties['half_life_h'] when present."""
        h = (self.properties or {}).get("half_life_h")
        try:
            h = float(h)
            if 0.25 <= h <= 24 * 30:
                return h * 3600.0
        except (TypeError, ValueError):
            pass
        return POSITION_HALF_LIFE_S

    def consolidate_position(self, cell_vecs_with_ts: list,
                             now: float) -> dict:
        """ONE consolidation step (pure numpy, ~ms): drift the position
        toward the recent cells' centroid (time-weighted by the half-life)
        and record the motion in the coarsening history ring.

        ``cell_vecs_with_ts``: [(vec, born), ...] for this entity's linked
        SAFE cells (the caller harvests them; already unit-normalizable).

        Storage: a new ring entry lands ONLY when the position moved >=
        the entity's grain (delta-perceptual); era anchors at half-life
        fractions (T/8, T/4, T/2, T) guarantee the past stays represented;
        overflow collapses the OLDEST era first (velocity lives at the
        fine end -- it must stay trustworthy). Returns the derived
        dynamics: {moved, velocity, direction}."""
        import numpy as _np
        import time as _t
        moved = 0.0
        # ---- the drift step: lambda-weighted pull toward recent cells ----
        pts = [(v, t) for v, t in (cell_vecs_with_ts or [])
               if v is not None and t]
        if pts:
            vs, ws = [], []
            for v, born in pts:
                a = _np.asarray(v, dtype=float)
                an = _np.linalg.norm(a)
                if an < 1e-12:
                    continue
                vs.append(a / an)
                # time-decay weight: recent cells pull harder (the
                # relativistic horizon: dt=half_life -> half force)
                dt = max(now - born, 0.0)
                ws.append(0.5 ** (dt / max(self.half_life_s(), 1.0)))
            if vs:
                W = _np.asarray(ws)
                W = W / (W.sum() + 1e-12)
                target = _np.average(_np.asarray(vs), axis=0, weights=W)
                tn = _np.linalg.norm(target)
                if tn > 1e-12:
                    target = target / tn
                    if self.position:
                        cur = _np.asarray(self.position, dtype=float)
                        cn = _np.linalg.norm(cur)
                        if cn > 1e-12:
                            cur = cur / cn
                            lam = 0.5 ** (1.0)   # one step; the weights
                            # above already encode time -- a fixed step
                            # keeps the drift bounded per cycle
                            blended = cur * 0.5 + target * 0.5
                            bn = _np.linalg.norm(blended)
                            if bn > 1e-12:
                                new_pos = blended / bn
                                moved = float(1.0 - float(cur @ new_pos))
                                self.position = [float(x) for x in new_pos]
                        else:
                            self.position = [float(x) for x in target]
                    else:
                        self.position = [float(x) for x in target]
        if not self.position:
            return {"moved": 0.0, "velocity": 0.0, "direction": ""}
        # ---- the coarsening ring ----
        self._ring_store(now)
        return {"moved": moved,
                "velocity": self.ring_velocity(now),
                "direction": self.ring_direction()}

    def _ring_store(self, ts: float) -> None:
        """Insert (ts, position) into the history ring under the delta
        rule + era anchors, then collapse to the cap (oldest era first)."""
        import numpy as _np
        hist = list(getattr(self, "position_history", []) or [])
        pos = _np.asarray(self.position, dtype=float)
        pn = _np.linalg.norm(pos)
        if pn < 1e-12:
            return
        pos = pos / pn
        # delta-perceptual insertion: only meaningful motion lands
        if hist:
            _, last_pos = hist[-1]
            lp = _np.asarray(last_pos, dtype=float)
            ln = _np.linalg.norm(lp)
            if ln > 1e-12:
                if 1.0 - float(lp / ln @ pos) < self.grain():
                    # imperceptible: skip UNLESS an era anchor is due
                    if not self._era_anchor_due(hist, ts):
                        self.position_history = hist[-RING_MAX:]
                        return
        hist.append([ts, [float(x) for x in pos]])
        self.position_history = self._ring_collapse(hist, ts)[-RING_MAX:]

    def _era_span(self) -> float:
        """The era-anchor schedule derives from the entity's OWN timescale
        (the half-life): anchors at T/8, T/4, T/2, T."""
        t = self.half_life_s()
        return t / 8.0

    def _era_anchor_due(self, hist: list, now: float) -> bool:
        """True when an era anchor is missing/stale: an era the entity has
        LIVED through (its recorded birth predates the era's start) with
        no stored point -- the 'important past stays' guarantee."""
        span = self._era_span()
        now_ts = now
        birth = hist[0][0] if hist else now_ts
        for i in range(4):
            hi_age = span * (2 ** (i + 1))
            lo_age = span * (2 ** i)
            lo_ts, hi_ts = now_ts - hi_age, now_ts - lo_age
            in_era = [e for e in hist if lo_ts <= e[0] < hi_ts]
            if not in_era and birth < lo_ts:
                return True
        return False

    def _ring_collapse(self, hist: list, now: float) -> list:
        """Vanishing-precision compression: when a window holds more than
        its budget, its two closest-in-time entries merge (position mean,
        newer stamp). The OLDEST era merges first; the recent end (the
        velocity source) is touched last."""
        import numpy as _np
        span = self._era_span()
        budgets = (6, 4, 2, 1)          # per geometric era window
        def window_of(ts: float) -> int:
            age = max(now - ts, 0.0)
            for i in range(4):
                if age < span * (2 ** (i + 1)):
                    return i
            return 3                     # oldest era: everything elder
        for _ in range(64):              # bounded converge
            counts = {0: [], 1: [], 2: [], 3: []}
            for e in hist:
                counts[window_of(e[0])].append(e)
            over = next((w for w in range(3, -1, -1)
                         if len(counts[w]) > budgets[w]), None)
            if over is None:
                return hist
            win = counts[over]
            win.sort(key=lambda e: e[0])
            a, b = win[0], win[1]        # the two oldest in that window
            pa = _np.asarray(a[1], dtype=float)
            pb = _np.asarray(b[1], dtype=float)
            m = pa + pb
            mn = _np.linalg.norm(m)
            merged = [b[0], ([float(x) for x in m / mn] if mn > 1e-12
                             else b[1])]
            hist = [e for e in hist if e is not a and e is not b]
            hist.append(merged)
            hist.sort(key=lambda e: e[0])
        return hist

    def ring_velocity(self, now: float) -> float:
        """Cosine-distance per hour across the FINE end of the ring (the
        two most recent points) -- the trustworthy motion signal."""
        import numpy as _np
        h = getattr(self, "position_history", []) or []
        if len(h) < 2:
            return 0.0
        (t0, p0), (t1, p1) = h[-2], h[-1]
        dt_h = (t1 - t0) / 3600.0
        if dt_h <= 1e-6:
            return 0.0
        a = _np.asarray(p0, dtype=float)
        b = _np.asarray(p1, dtype=float)
        an, bn = _np.linalg.norm(a), _np.linalg.norm(b)
        if an < 1e-12 or bn < 1e-12:
            return 0.0
        return (1.0 - float(a / an @ (b / bn))) / dt_h

    def ring_direction(self) -> str:
        """The coarse-end story: which linked entity-key the OLDEST->NEWEST
        position migration points at, by name (renderable, not a vector)."""
        import numpy as _np
        h = getattr(self, "position_history", []) or []
        if len(h) < 2:
            return ""
        old = _np.asarray(h[0][1], dtype=float)
        new = _np.asarray(h[-1][1], dtype=float)
        on, nn = _np.linalg.norm(old), _np.linalg.norm(new)
        if on < 1e-12 or nn < 1e-12:
            return ""
        return "drifting" if 1.0 - float(old / on @ (new / nn)) > 0.05 \
            else "holding"


def entity_id(kind: str, value: str) -> str:
    """Stable id for an entity mention: kind-namespaced, slug-cased."""
    slug = value.strip().lower().replace(" ", "-")[:80]
    return f"{MEMBRANE_NS}{kind}:{slug}"


# deterministic extractor kind -> shared class (the write-through mapping)
_KIND_CLASS = {"files": CLASS_COMPONENT, "symbols": CLASS_COMPONENT,
               "models": CLASS_MODEL, "urls": CLASS_ENDPOINT,
               "shell": CLASS_TOOL, "ha": CLASS_SENSOR}


def kind_class(kind: str) -> str:
    return _KIND_CLASS.get(kind, CLASS_CONFIG)


# ---- the entity store (write-through to the shared collection + local JSON) -

class EntityStore:
    """Derived entities, three homes, one contract:

    1. ``cortex-core.entities`` (the cortex-experiment QuantumEntity
       collection) under doc id ``membrane::<eid-suffix>`` -- the shared
       symbolic layer; cortex's nightly reflection and membrane both read it.
       Upsert is MERGE-on-collision (union properties/links/derived_by --
       never a second doc for the same thing).
    2. ``<field>.entities.json`` -- the local mirror (crash-safe atomic
       write), so recall works with Couchbase down and entities survive
       foreign-cluster moves.
    3. RAM (self.docs: eid -> QuantumEntity) for the recall path.

    Degrade covenant: any couchbase failure is logged once and the JSON/RAM
    copies carry on."""

    def __init__(self, path: str | None = None, bucket: str = "cortex-core") -> None:
        self.path = Path(path) if path else None
        self.bucket = bucket
        self.docs: dict[str, QuantumEntity] = {}
        # entity TRACKS: key -> {key, label, op, n, alerted}. The banner
        # lane: live cell counts in WHERE WE ARE, optional boundary alerts.
        self.tracks: dict[str, dict] = {}
        self.junk_screen = JunkScreen(self)
        self._cb_warned = False
        self._load()
        self._load_tracks()
        self._seed_junk_anchors()

    def _seed_junk_anchors(self) -> None:
        """Write the seed anchors once (merge-on-collision; existing docs
        never duplicated). Silent on failure -- the regex screens carry."""
        try:
            added = False
            for ent in junk_anchor_docs():
                if ent.eid not in self.docs:
                    self.docs[ent.eid] = ent
                    added = True
            if added:
                self.save()
        except Exception:
            pass                   # the regex screens carry

    def set_junk_cut(self, spec: str, cut) -> dict:
        """Set a track's junk cut (the per-track anchor-screen floor).
        spec: track key, 'all' (every track), or 'default' (the global
        default for NEW tracks). None clears back to the default."""
        if spec == "default":
            try:
                val = None if cut in (None, "") \
                    else max(0.0, min(1.0, float(cut)))
            except (TypeError, ValueError):
                return {"ok": False,
                        "error": "cut must be a number 0..1 (or null)"}
            if val is None:
                return {"ok": False, "error": "clearing the default makes "
                        "no sense; give a number"}
            globals()["JUNK_CUT_DEFAULT"] = round(val, 3)
            return {"ok": True, "default": JUNK_CUT_DEFAULT}
        targets = (list(self.tracks.values()) if spec == "all"
                   else [self.tracks[spec]] if spec in self.tracks else [])
        if not targets:
            return {"ok": False, "error": f"no track {spec!r} "
                    "(list via membrane_list_tracks)"}
        try:
            val = None if cut in (None, "") else max(0.0, min(1.0, float(cut)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "cut must be a number 0..1 "
                    "(or null to clear)"}
        for t in targets:
            if val is None:
                t.pop("junk_sim", None)
            else:
                t["junk_sim"] = round(val, 3)
        self._save_tracks()
        return {"ok": True, "spec": spec, "junk_sim": val,
                "tracks": len(targets)}

    def junk_beat_ok(self, key: str, vec, embedder=None) -> bool:
        """The per-track junk gate for one beat: the track's own cut (or
        the default) against the anchor screen. Tracks with no cut and no
        anchors degrade to always-True (the regexes still run)."""
        cut = (self.tracks.get(key) or {}).get("junk_sim")
        cut = JUNK_CUT_DEFAULT if cut is None else cut
        if not self.junk_screen._vecs and embedder is None:
            return True
        return self.junk_screen.sim_of(vec, embedder) < cut

    # ---- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
            for k, rec in (d.get("docs") or {}).items():
                q = QuantumEntity.from_doc(k, rec)
                if q is not None:
                    self.docs[k] = q
        except Exception as e:
            print(f"[entities] side-index load failed ({e})",
                  file=sys.stderr)

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            # preserve tracks: docs-save must not clobber the track lane
            # (the old shape wrote {"docs": ...} bare -- every save wiped
            # the planted tracks; the silent tracking-death bug)
            d = json.loads(self.path.read_text()) if self.path.exists() \
                else {}
            # EPHEMERAL docs (a session's living present) stay RAM-only:
            # a dead session's 'now' must not pollute the next boot
            d["docs"] = {k: q.to_doc() for k, q in self.docs.items()
                         if not (q.properties or {}).get("ephemeral")}
            if self.tracks:
                d["tracks"] = self.tracks
            tmp.write_text(json.dumps(d))
            os.replace(tmp, self.path)
        except Exception as e:
            print(f"[entities] side-index save failed ({e})",
                  file=sys.stderr)

    # ---- couchbase (the shared collection) ----------------------------------
    def _entities_collection(self):
        """cortex-core._default.entities (the QuantumEntity home). None when
        couchbase is unreachable (JSON/RAM still carry). Stashes the cluster
        handle (self._cluster_ref) for N1QL (Collections have no .query)."""
        try:
            from couchbase.cluster import Cluster
            from couchbase.auth import PasswordAuthenticator
            from couchbase.options import ClusterOptions
            conn = os.environ.get("COUCHBASE_CONN_STRING",
                                  "couchbase://localhost")
            user = os.environ.get("COUCHBASE_USER", "Administrator")
            pw = os.environ.get("COUCHBASE_PASSWORD", "password")
            bucket = os.environ.get("COUCHBASE_BUCKET", self.bucket)
            cluster = Cluster(conn, ClusterOptions(
                PasswordAuthenticator(user, pw)))
            self._cluster_ref = cluster
            coll = cluster.bucket(bucket).scope("_default").collection(
                "entities")
            try:
                coll_mgmt = cluster.bucket(bucket).collections()
                try:
                    coll_mgmt.create_collection("_default", "entities")
                except Exception:
                    pass                # exists
            except Exception:
                pass
            return coll
        except Exception as e:
            if not self._cb_warned:
                print(f"[entities] couchbase write-through unavailable "
                      f"({str(e)[:80]}); JSON side-index carries",
                      file=sys.stderr)
                self._cb_warned = True
            return None

    # ---- the merge contract --------------------------------------------------
    def upsert(self, ent: QuantumEntity, embedder=None) -> bool:
        """Merge one entity (union on collision), persist both homes.
        Returns True when written (new or merged).
        EPHEMERAL entities (properties['ephemeral']) stay LOCAL: a
        session's living present is nobody else's business -- it never
        rides the couchbase write-through (two parallel seats would
        otherwise fight over one 'now' doc)."""
        if not ent.eid:
            return False
        existing = self.docs.get(ent.eid)
        if existing is not None:
            existing.merge(ent)
            ent = existing
        else:
            self.docs[ent.eid] = ent
        self.save()
        if (ent.properties or {}).get("ephemeral"):
            return True
        coll = self._entities_collection()
        if coll is not None:
            try:
                coll.upsert(ent.eid, ent.to_doc(embedder))
                return True
            except Exception as e:
                if not self._cb_warned:
                    print(f"[entities] write-through failed ({str(e)[:80]})",
                          file=sys.stderr)
                    self._cb_warned = True
        return False

    def hydrate_from_couchbase(self, limit: int = 2000) -> int:
        """READ-THROUGH: pull cortex's entities into RAM (both systems see
        one symbolic layer). Foreign entities (no membrane:: prefix) load
        as-is -- the shared vocabulary makes them directly usable."""
        coll = self._entities_collection()
        if coll is None:
            return 0
        # Collections have no .query(); N1QL lives on the cluster. Scope the
        # keyspace explicitly (same bucket the collection handle came from).
        from couchbase.options import QueryOptions
        import datetime as _dt
        n = 0
        cluster = getattr(self, "_cluster_ref", None)
        if cluster is None:
            return 0
        try:
            qr = cluster.query(
                "SELECT meta().id AS id, t.* FROM "
                f"`{self.bucket}`.`_default`.`entities` t LIMIT $lim",
                QueryOptions(timeout=_dt.timedelta(seconds=30),
                             named_parameters={"lim": limit}))
            for row in qr:
                q = QuantumEntity.from_doc(row.get("id", ""),
                                           {k: v for k, v in row.items()
                                            if k != "id"})
                if q is None:
                    continue
                ex = self.docs.get(q.eid)
                if ex is not None:
                    ex.merge(q)          # two systems, one entity
                else:
                    self.docs[q.eid] = q
                n += 1
        except Exception as e:
            print(f"[entities] read-through skipped ({str(e)[:80]})",
                  file=sys.stderr)
        if n:
            self.save()
        return n

    def all(self) -> list[QuantumEntity]:
        return list(self.docs.values())

    def find(self, name_l: str) -> QuantumEntity | None:
        """Case-insensitive name lookup (the viewer/join path)."""
        name_l = (name_l or "").strip().lower()
        for q in self.docs.values():
            props = q.properties or {}
            if str(props.get("name", "")).strip().lower() == name_l \
                    or q.eid.lower().endswith(":" + name_l):
                return q
        return None

    # ---- model-side CRUD (the MCP write surface) -----------------------------
    # The model is a first-class citizen of the symbolic layer: it can ADD
    # abstract entities (decisions, failure modes, junk-anchor vectors),
    # EDIT summaries/links, and REMOVE noise -- every mutation audited to
    # the operator's ledger (agent-entity-* events) and merged under the
    # same upsert covenant as the synthesizer (union, never a second doc).

    def _resolve_spec(self, spec: str) -> QuantumEntity | None:
        """An eid, an exact eid-suffix, or a name fragment -> the entity."""
        spec = (spec or "").strip()
        if not spec:
            return None
        if spec in self.docs:
            return self.docs[spec]
        low = spec.lower()
        if not low.startswith(MEMBRANE_NS):
            low = MEMBRANE_NS + low
        if low in self.docs:
            return self.docs[low]
        hits = [q for q in self.docs.values()
                if q.eid.endswith(":" + spec.lower())
                or str((q.properties or {}).get("name", "")).lower()
                == spec.lower()]
        return hits[0] if len(hits) == 1 else (hits[0] if hits else None)

    def add(self, cls: str, name: str, summary: str = "",
            links=None) -> dict:
        """Model-side entity ADD: merge-on-collision with any existing doc
        (same name = same thing), write-through both homes."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "name required"}
        c = cls if cls in CLASSES else CLASS_COMPONENT   # safest default
        eid = entity_id(c, name)
        ent = QuantumEntity(eid=eid, cls=c,
                            properties={"name": name,
                                        **({"summary": summary}
                                           if summary else {})},
                            links=[int(i) for i in (links or [])
                                   if str(i).lstrip("-").isdigit()],
                            derived_by=["model"])
        existing = self.docs.get(eid)
        merged = existing is not None
        if merged:
            existing.merge(ent)
            ent = existing
        self.upsert(ent)
        return {"ok": True, "eid": eid, "merged": merged,
                "class": c, "name": name}

    def edit(self, spec: str, summary=None, links=None, cls=None) -> dict:
        """Model-side entity EDIT: patch summary/links/class on the
        resolved entity. Only provided fields change."""
        ent = self._resolve_spec(spec)
        if ent is None:
            return {"ok": False,
                    "error": f"no entity matches {spec!r} "
                             f"(list via membrane_related_entities)"}
        if summary is not None:
            ent.properties["summary"] = str(summary).strip()
        if links is not None:
            clean = [int(i) for i in links
                     if str(i).lstrip("-").isdigit()]
            merged = set(ent.links or []) | set(clean)
            ent.links = sorted(merged)
        if cls and cls in CLASSES:
            ent.cls = cls
        ent.derived_by = sorted(set(ent.derived_by or []) | {"model-edit"})
        self.upsert(ent)
        return {"ok": True, "eid": ent.eid, "class": ent.cls,
                "summary": ent.properties.get("summary", ""),
                "links": ent.links}

    def remove(self, spec: str) -> int:
        """Model-side entity REMOVE by eid/name fragment (exact single
        match only -- an ambiguous fragment removes nothing)."""
        ent = self._resolve_spec(spec)
        if ent is None:
            return 0
        self.docs.pop(ent.eid, None)
        self.save()
        coll = self._entities_collection()
        if coll is not None:
            try:
                coll.remove(ent.eid)
            except Exception:
                pass                    # JSON/RAM already carried
        return 1

    # ---- entity TRACKS v2 (the model's banner lane, EVENT-based) ------------
    # A track = {key, enter, exit, filter} over an entity's SAFE-cell count:
    #   enter: 'N'      -- alert when the count rises INTO/ABOVE N
    #   exit:  'N'      -- alert when the count falls BELOW N
    #   filter: kind    -- alert on ANY NEW cell of a kind (e.g. 'models:')
    # The alert fires ON THE TRANSITION, once per transition (enter fires
    # when the region is entered; re-entering after leaving re-arms it) --
    # a state machine, not a level check. The track ALSO always shows the
    # live count in the WHERE WE ARE banner. Management (add/remove/list)
    # is unified for the LLM via MCP (membrane_watch_entity /
    # membrane_unwatch_entity / membrane_list_tracks) and the proxy
    # endpoints they relay to.

    @staticmethod
    def default_label(key: str) -> str:
        """A readable label from the entity key: kind-aware, no jargon.
        'models:x' -> "the model x", 'files:membrane/router.py' ->
        "router.py", 'shell:git' -> "the shell tool git"."""
        kind, _, val = key.partition(":")
        if kind == "files":
            return val.rsplit("/", 1)[-1]      # the basename
        if kind == "models":
            return f"the model {val}"
        if kind == "shell":
            return f"the shell tool {val}"
        if kind == "ha":
            return f"the sensor {val}"
        return val or key

    def watch_track(self, key: str, boundary: str = "",
                    label: str = "") -> dict:
        """Plant a banner track: the entity's live safe-cell count renders
        in WHERE WE ARE every turn (constant tracking). An optional boundary
        ('enter N' / 'exit N' / bare 'N') turns it into an EVENT trigger --
        a state machine that alerts once per region transition (re-entry
        re-arms by construction). Re-planting the same boundary keeps the
        state; a changed boundary restarts it."""
        key = key.strip().lower()
        if not key or ":" not in key:
            return {"ok": False, "error": "key must be 'kind:value' "
                    "(e.g. 'files:router.py')"}
        try:
            b = self.parse_boundary(boundary)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        # THE SCARCITY COVENANT (the operator's ratio: 3 tracks / 7
        # watches): overflow evicts via _enforce_cap -- STORY-POOREST
        # (fewest tagged cells, tie: oldest planted), never a rich track,
        # never the default now-track.
        evicted_key = None
        if (key not in self.tracks and key != DEFAULT_TRACK_KEY
                and len(self.tracks) >= MAX_TRACKS):
            evicted_list = self._enforce_cap_for_plant(key)
            evicted_key = evicted_list[0] if evicted_list else None
        rec = dict(self.tracks.get(key) or {})
        if not rec:
            import time as _t
            rec["planted_ts"] = _t.time()
        rec["key"] = key
        rec["label"] = label or self.default_label(key)
        old = (rec.get("enter"), rec.get("exit"))
        new = (b.get("enter"), b.get("exit"))
        if new != old:
            rec["enter"] = b.get("enter")
            rec["exit"] = b.get("exit")
            rec["inside"] = None          # fresh state machine; seeds on eval
        if not b:                         # boundary removed -> count-only
            rec.pop("enter", None)
            rec.pop("exit", None)
            rec.pop("inside", None)
        self.tracks[key] = rec
        self._save_tracks()
        out = {"ok": True, "key": key, "label": rec["label"]}
        for k in ("enter", "exit", "inside"):
            if rec.get(k) is not None:
                out[k] = rec[k]
        if evicted_key:
            out["evicted"] = {"key": evicted_key}
        return out

    @staticmethod
    def parse_boundary(boundary: str) -> dict:
        """'enter N' / 'exit N' / bare 'N' -> {'enter': N} / {'exit': N};
        '' -> {}. Raises ValueError on anything else (the caller 400s)."""
        s = (boundary or "").strip().lower()
        if not s:
            return {}
        m = re.fullmatch(r"(enter|exit)?\s*(\d{1,6})", s)
        if not m:
            raise ValueError("boundary must be 'enter N' or 'exit N' "
                             "(bare N = enter N)")
        op, n = m.group(1) or "enter", int(m.group(2))
        return {"enter": n} if op == "enter" else {"exit": n}

    def evaluate_tracks(self, exemplars) -> list[dict]:
        """The per-turn state machine step: for every bounded track, compute
        the live safe-cell count and transition the inside/outside state.
        Returns the ALERTS fired this turn (one per transition; re-entry
        re-arms by construction). Count-only tracks never alert. Persistence
        only when something actually changed."""
        alerts: list[dict] = []
        bounded = {k: t for k, t in self.tracks.items()
                   if t.get("enter") is not None or t.get("exit") is not None}
        if not bounded:
            return alerts
        inv = entity_index(exemplars)
        changed = False
        for key, t in bounded.items():
            n = len(inv.get(key) or [])
            enter, exit_ = t.get("enter"), t.get("exit")
            inside: bool
            reason = ""
            if enter is not None and n >= enter:
                inside, reason = True, f"count {n} >= enter {enter}"
            elif exit_ is not None and n < exit_:
                inside, reason = False, f"count {n} < exit {exit_}"
            elif enter is not None:
                inside, reason = False, f"count {n} < enter {enter}"
            else:
                inside, reason = True, f"count {n} >= exit {exit_}"
            prev = t.get("inside")
            if prev == inside:
                continue                      # no transition -> no alert
            t["inside"] = inside
            changed = True
            if prev is not None:              # the seed eval is silent
                a = {"key": key,
                     "label": t.get("label") or self.default_label(key),
                     "kind": "entered" if inside else "exited",
                     "count": n,
                     "boundary": (f"enter {enter}" if enter is not None
                                  else f"exit {exit_}"),
                     "detail": reason}
                alerts.append(a)
                pend = t.setdefault("pending", [])
                pend.append(a)
                del pend[:-8]                 # bounded queue
        if changed:
            self._save_tracks()
        return alerts

    def take_alerts(self) -> list[dict]:
        """Drain every track's pending ALERTS for banner delivery (the
        transition state itself is untouched -- re-firing needs a new
        transition). Exactly-once: the pending queue is the delivery
        ledger."""
        out: list[dict] = []
        for t in self.tracks.values():
            pend = t.get("pending") or []
            if pend:
                out.extend(pend)
                t["pending"] = []
        if out:
            self._save_tracks()
        return out

    def unwatch_track(self, spec: str) -> int:
        """Remove tracks by key or kind prefix (e.g. 'files:router.py' or
        'files:' for the whole kind)."""
        spec = spec.strip().lower()
        hits = [k for k in self.tracks if k == spec or k.startswith(spec)]
        for k in hits:
            del self.tracks[k]
        if hits:
            self._save_tracks()
        return len(hits)

    def list_tracks(self) -> list[dict]:
        """The management listing (the LLM's view of its own banner): key,
        label, boundary, current inside/outside state, alert count."""
        out = []
        for key, t in self.tracks.items():
            rec = {"key": key,
                   "label": t.get("label") or key}
            for k in ("enter", "exit", "inside"):
                if t.get(k) is not None:
                    rec[k] = t[k]
            rec["pending_alerts"] = len(t.get("pending") or [])
            out.append(rec)
        return out

    def _save_tracks(self) -> None:
        if not self.path:
            return
        try:
            d = json.loads(self.path.read_text()) if self.path.exists() \
                else {}
            d["tracks"] = self.tracks
            self.path.write_text(json.dumps(d))
        except Exception as e:
            print(f"[entities] track save failed ({e})", file=sys.stderr)

    def _load_tracks(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
            for k, t in (d.get("tracks") or {}).items():
                self.tracks[k] = t
        except Exception:
            pass

    def _enforce_cap(self) -> list[str]:
        """THE CAP, enforced wherever the roster stands: story-poorest
        (fewest tagged cells via _exemplars_hint, tie: oldest planted)
        evicted down to MAX_TRACKS; the default now-track immune. Called
        at boot (after the hint wires) and at plant time. Returns the
        evicted keys."""
        evicted: list[str] = []
        over = len(self.tracks) - MAX_TRACKS
        if over <= 0:
            return evicted
        est_idx = entity_index(getattr(self, "_exemplars_hint", []) or [])

        def _pool(k):
            return (len(est_idx.get(k) or []),
                    -self.tracks[k].get("planted_ts", 0.0))

        for _ in range(over):
            poorest = min((k for k in self.tracks
                           if k != DEFAULT_TRACK_KEY),
                          key=lambda k: _pool(k),
                          default=None)
            if poorest is None:
                break
            del self.tracks[poorest]
            evicted.append(poorest)
        if evicted:
            self._save_tracks()
        return evicted

    def _enforce_cap_for_plant(self, incoming_key: str) -> list[str]:
        """Plant-time variant: make room for `incoming_key` BEFORE it
        lands (the incoming track must not be an eviction candidate --
        it arrives with zero cells and would always be the poorest)."""
        over = len(self.tracks) - MAX_TRACKS + 1
        evicted: list[str] = []
        if over <= 0:
            return evicted
        est_idx = entity_index(getattr(self, "_exemplars_hint", []) or [])

        def _pool(k):
            return (len(est_idx.get(k) or []),
                    -self.tracks[k].get("planted_ts", 0.0))

        for _ in range(over):
            poorest = min((k for k in self.tracks
                           if k != DEFAULT_TRACK_KEY
                           and k != incoming_key),
                          key=lambda k: _pool(k),
                          default=None)
            if poorest is None:
                break
            del self.tracks[poorest]
            evicted.append(poorest)
        if evicted:
            self._save_tracks()
        return evicted

    def track_report(self, exemplars, query_vec=None,
                     session_since: float | None = None,
                     goal: str = "", embedder=None) -> list[tuple[str, str]] | None:
        """Tracked entities as STRUCTURED stories: (track_key, text) —
        the consumer indexes by the key itself (never string-splitting a
        colon-rich ID out of prose). ONE MIXED paragraph per track:"""
        """Tracked entities, ONE MIXED line each:
        HISTORY half = the story ARC (now -> how it got there ->
        originated -> aiming), rendered through the zoom ladder (the
        black-hole invariant applied to time): the NOW window
        (<= ZOOM_NOW_S) renders fine grain (<= ZOOM_NOW_MAX beats), the
        RECENT window mid grain, the ORIGIN era always coarse. A window
        with more cells than its beat budget COLLAPSES into an era count
        ("now: active — 5 changes just before"); precision x span stays
        bounded. Stale story (newest beat > STALE_STORY_S) -> silence.
        STRUCTURE half = co-occurrence surroundings, change-gated.
        Both halves event-conditional; deterministic, $0; the teed
        meta-voice and raw machinery dumps are junk, never beats."""
        if not self.tracks:
            return None
        try:
            from .rag import is_capture_junk
        except Exception:
            is_capture_junk = lambda s: False
        import time as _t
        import numpy as _np
        now = _t.time()
        inv = entity_index(exemplars)
        qv = None
        if query_vec is not None:
            qv = _np.asarray(query_vec, dtype=float)
            qv = qv / (_np.linalg.norm(qv) + 1e-12)
        out: list[tuple[str, str]] = []
        dirty = False
        # THE CROSS-TRACK ECHO GUARD: one cell may anchor at most ONE
        # story per render pass -- the now-track and a young entity-track
        # would otherwise recite the same fresh cell twice (the live
        # exhibit: both tracks rendered the same probe cell verbatim).
        # Priority = track order (planted first wins); the later track
        # falls back to its own distinct cells or the compact line.
        claimed_cells: set[int] = set()
        for key, t in self.tracks.items():
            idxs = inv.get(key) or []
            is_now = (key == "session:now")
            # the NOW TRACK: nothing tags 'session:now' -- its cell pool IS
            # the fresh tail SINCE THIS SESSION'S BOOT (session_since --
            # the seam finally consumed): each restart starts a new 'now'
            # story; the previous session's cells are history, not present.
            if is_now:
                since = session_since or 0.0
                fresh = sorted(((i, b) for i in range(len(exemplars))
                                for b in (_eph_born_of(exemplars, i),)
                                if b >= max(since, now - 24 * 3600.0)
                                and getattr(exemplars[i], "safe", False)),
                               key=lambda x: -x[1])
                idxs = [i for i, _ in fresh[:24]]
            if not idxs:
                continue          # quiet: nothing until the entity appears
            # ---- the now-scope for the track's OWN compact line ----
            # (used when the story is stale-silent: the fallback must
            # still TRACE -- a bare name says nothing about the entity's
            # temporal position; 'last active' is the honest minimum)
            newest_cell = max((_eph_born_of(exemplars, i) for i in idxs),
                              default=0.0)
            label = t.get("label") or self.default_label(key)
            # ---- the HISTORY half (the arc through the zoom ladder) ----
            # the now-track renders TEMPORALLY (the fresh tail IS the
            # story; alignment would filter out exactly what 'now' means).
            # A tag-track with no query vector ALSO tells its story --
            # alignment SELECTS which beats win the fine slots; it is not
            # the gate on WHETHER a story exists (a 74-cell history
            # rendered as a bare name is a lie of omission -- the
            # operator's 'I want stories' call).
            story = ""
            if qv is not None or is_now or newest_cell > 0:
                scored = []
                newest_born = 0.0
                for i in idxs:
                    # THE STORY-ELIGIBILITY SCREEN: judge the STRIPPED
                    # narrative, not the raw capture form. A tool-capture
                    # cell IS recall-junk (never grounding material) but
                    # its fragment after prefix-strip is the operator's
                    # own words -- story-eligible (the entity-management
                    # captures are structurally ABOUT this entity; killing
                    # them here is why tracks had no stories). The raw
                    # MACHINERY forms (ps tables, config JSON) still die:
                    # they stay junk after any strip.
                    sit_text = _sit_text_of(exemplars, i)
                    stripped = _strip_tool_prefix(sit_text)
                    if stripped == sit_text:
                        # no tool prefix: the raw form IS the narrative
                        if is_capture_junk(sit_text) or _machinery_junk(sit_text):
                            continue
                    elif _machinery_junk(stripped):
                        continue
                    v = getattr(exemplars[i], "vec", None)
                    if v is not None:
                        # the per-track junk cut: the beat's own vector vs
                        # the anchor entities (unknown junk forms die here;
                        # known forms died in the regex above)
                        if not self.junk_beat_ok(key, v, embedder):
                            continue
                        vv = _np.asarray(v, dtype=float)
                        vn = _np.linalg.norm(vv)
                        if vn < 1e-12:
                            continue
                    sim = (float(qv @ (vv / vn)) if (qv is not None and v)
                           else 0.0)
                    born = _eph_born_of(exemplars, i)
                    newest_born = max(newest_born, born)
                    # THE POOL GUARANTEE (stronger than the now-guarantee):
                    # alignment never KILLS a beat candidate -- an
                    # unaligned cell drops to sim 0 and competes on
                    # freshness alone. A 3-cell track where the current
                    # query aligns with none must still tell its story
                    # (the live test: cosines 0.45-0.51 straddling the
                    # floor silently emptied the pool -> bare fallback).
                    # The ALIGN_FLOOR filter applies only to RANKING when
                    # the pool is rich (>= 3 aligned candidates).
                    if qv is not None and sim < ALIGN_FLOOR:
                        scored.append((i, 0.0 + 0.0, born))   # unaligned: rank-last, not dead
                        continue
                    # freshness credit: halves every FRESH_HALF_LIFE --
                    # an old beat must ALIGN HARDER to win the slot
                    fresh = FRESH_WEIGHT * (0.5 ** (max(now - born, 0.0)
                                                    / FRESH_HALF_LIFE))
                    scored.append((i, sim + fresh, born))
                if scored:
                    # NO inner stale gate: _arc already marks recency
                    # honestly ('last active 28h ago: ...') -- a rich
                    # history told with an honest age stamp beats a bare
                    # name (the operator's 'I want stories' call; the
                    # stale-recitation risk lives in pretending recency,
                    # not in telling an old story with its age attached).
                    # the zoom ladder needs the WHOLE timeline, not just
                    # aligned winners: every junk-free born cell, sorted.
                    all_borns = sorted(_eph_born_of(exemplars, i)
                                       for i in idxs)
                    all_borns = [b for b in all_borns if b > 0]
                    # the fine-grain beats: fresh-aligned, junk-screened,
                    # deduped, chronological (the ladder's prose slots).
                    # A beat without a birth stamp carries no temporal
                    # truth -- it cannot anchor a story, so it dies here
                    # (the 'last active recently:' stutter fix).
                    scored.sort(key=lambda x: -x[1])
                    picked = [s for s in scored if s[2] > 0][:6]
                    # THE NOW GUARANTEE: the arc must END at the now --
                    # the 2 newest junk-free cells join the beat pool
                    # regardless of alignment (a story that ends 29h deep
                    # while fresh work exists is a lie about the present).
                    # The junk screens below still apply to them.
                    by_born = sorted(((i, b) for i in idxs
                                      for b in (_eph_born_of(exemplars, i),)
                                      if b > 0),
                                     key=lambda x: -x[1])
                    have = {p[0] for p in picked}
                    for i, b in by_born[:2]:
                        if i not in have:
                            picked.append((i, 0.0, b))
                    picked.sort(key=lambda x: x[2])
                    beats: list[tuple[float, str]] = []
                    seen: set[int] = set()
                    seen_actions: list[str] = []
                    for i, _sim, born in picked:
                        if i in seen:
                            continue
                        seen.add(i)
                        sit, act = _frag_of(exemplars, i)
                        sit = _narrative(sit, 200)
                        act = _narrative(act, 240)
                        # an EMPTY action is silence, not junk (learn-pair
                        # cells carry their text in the situation) -- only
                        # a PRESENT action gets the artifact screen
                        if not sit or (act and (_tool_artifact(act)
                                                or _machinery_junk(act))):
                            continue
                        if _machinery_junk(sit):
                            continue
                        a_low = act.lower()[:80]
                        if any(_action_overlap(a_low, p) > 0.6
                               for p in seen_actions):
                            continue
                        seen_actions.append(a_low)
                        # the act half of an entity-management capture is a
                        # tool RESULT stamp ('Entity track planted: ...') —
                        # the situation fragment already carries the
                        # operator's words; the stamp adds nothing (the
                        # _tool_artifact trim ran, but the remainder is
                        # still tool-voice: drop the act entirely here)
                        if act and act.startswith(("Entity track planted",
                                                   "Entity ",
                                                   "Removed ",
                                                   "Track not planted")):
                            act = ""
                        # the echo guard: a cell already anchoring another
                        # track's story this pass dies here (the FIRST
                        # claim wins -- track order = priority)
                        if i in claimed_cells:
                            continue
                        frag = sit
                        if act and _action_overlap(act, sit) < 0.6:
                            frag += f" — {act}"
                        beats.append((born, frag))
                        claimed_cells.add(i)
                    # the now-track earns a higher prose budget: its pool is
                    # the pre-curated fresh tail (not a raw tag-index), so
                    # the era-collapse guard relaxes -- "now" is WHERE the
                    # detail lives by definition
                    story = _arc(beats, all_borns, now, goal=goal,
                                 era_min=(ZOOM_ERA_MIN + 3) if is_now
                                 else ZOOM_ERA_MIN)
                    # THE ID PREFIX (the operator's call): stories lead with
                    # the ENTITY ID (files:router.py), not the friendly
                    # label -- the id is the actionable handle (paste it
                    # into membrane_related_entities / membrane_query and
                    # the named-hit path finds the field slice). One name
                    # space across banner, reader, and field.
                    if story:
                        story = f"{key}: {story}"
            # ---- the STRUCTURE half (change-gated surroundings) ----
            surr = ""
            links = cooccurrence_neighbors(exemplars, key, inv=inv)
            mode = "shared cells"
            if not links:
                cent = centroid_neighbors(exemplars, key, inv=inv)
                links = [(k, round(float(c), 2)) for k, c in cent]
                mode = "near"
            if links:
                sig = [f"{k}:{c}" for k, c in links]
                if (t.get("surr") or {}).get("sig") != sig:
                    t["surr"] = {"sig": sig, "mode": mode, "ts": now}
                    dirty = True
                    if mode == "shared cells":
                        nb = ", ".join(f"{self.default_label(k)} ({c} shared)"
                                       for k, c in links[:SURR_MAX])
                    else:
                        nb = ", ".join(f"{self.default_label(k)} (cos {c:.2f})"
                                       for k, c in links[:SURR_MAX])
                    surr = f" Around it: {nb} [{mode}]."
            # ---- the MOTION half (measured drift from the living geometry) ----
            # the tracker's OWN recorded trajectory (night consolidation):
            # velocity from the fine end of the ring, change-gated like the
            # surroundings -- silence unless the motion is new information
            drift = ""
            ent = self.docs.get(f"{MEMBRANE_NS}{key}")
            if ent is not None and getattr(ent, "position_history", None):
                try:
                    vel = ent.ring_velocity(now)          # cos-distance/h
                    direction = ent.ring_direction()
                    # quantized for the change-gate: the story re-renders
                    # only when the PACE BAND changes (quiet/creeping/active/
                    # fast), not on every micro-jitter
                    vel_sig = round(vel, 2)
                    band = (0 if vel_sig < 0.005 else
                            1 if vel_sig < 0.05 else
                            2 if vel_sig < 0.15 else 3)
                    sig2 = f"{band}|{direction}"
                    if (t.get("drift") or {}).get("sig") != sig2:
                        t["drift"] = {"sig": sig2, "ts": now}
                        dirty = True
                        if direction == "drifting":
                            pace = ("fast" if band >= 3
                                    else "slowly" if band <= 1 else "steadily")
                            drift = (f" Moving {pace} "
                                     f"({vel_sig:.2f}/h, measured).")
                        elif band > 0:
                            drift = " Holding steady (measured)."
                except Exception:
                    drift = ""
            if story or surr or drift:
                # a bare drift line needs its anchor: '<key>: Moving ...'
                # (a floating 'Moving fast' names nothing -- the reader
                # cannot tell WHO moves). Structured return: (key, text)
                # -- the consumer indexes by the TRACK KEY itself, never
                # by string-splitting a colon-rich ID out of the prose
                # (the 'files'-prefix mis-key bug).
                if not story:
                    story = (f"{key}: {drift.strip()}" if drift
                             else f"{key}:")
                out.append((key, (story + surr + drift).strip()))
        if dirty:
            self._save_tracks()
        return out or None

def _clip_entity(s: str, cap: int = 60) -> str:
    """A situation line -> the entity story's 'new' fragment."""
    line = " ".join((s or "").split())
    line = line.replace("tool edit for: ", "").replace("tool bash for: ", "")
    return (line[:cap] + "…") if len(line) > cap else line


# ---- the SURROUNDINGS block (per-track semantic neighborhood) --------------
# The banner's STRUCTURE lane: for every tracked entity, WHAT LIVES AROUND
# IT in the field -- co-occurring entities (cells that mention both; a real
# graph edge) with an embedding-centroid fallback when the entity has no
# co-occurrences yet. History (the story lane) answers "what happened";
# surroundings answers "what is this connected to" -- the thing a fresh
# session cannot reconstruct from working context. Rendered EVENT-
# CONDITIONALLY: only when the neighborhood CHANGES (a new neighbor or a
# weight drift), never per-turn recitation.

SURR_MAX = 5               # neighbors per track, most-linked first
SURR_DRIFT = 0.25          # weight change fraction that re-renders
SURR_MIN_LINKS = 2         # a co-occurrence edge needs >= this many cells


# ---- the living geometry (entity consolidation) ----------------------------
# CELLS are evidence (immutable, full-precision). ENTITIES are trackers:
# position drifts toward recent linked cells (half-life time-decay), and
# the position HISTORY coarsens with age -- delta-perceptual storage plus
# era anchors on the half-life's own timescale. The knob (per-entity
# 'grain') lives in the SHARED doc so both seats see the same control.
POSITION_HALF_LIFE_S = 24 * 3600.0   # default drift half-life (24h)
RING_MAX = 16                        # history ring cap (entries)
GRAIN_DEFAULT = 0.05                 # delta-perception threshold (cosine)
# class-tuned defaults: hot things resolve fine, slow things coarsen
_GRAIN_DEFAULTS = {
    CLASS_SESSION: 0.03,             # a session's dynamics move fast
    CLASS_FAILURE: 0.03,             # failure modes need fine tracking
    CLASS_GOAL: 0.08,                # goals drift slowly by nature
    CLASS_COMPONENT: 0.05,
    CLASS_MODEL: 0.08,
}


# ---- the junk-cut knob (per-track, live, anchor-vector screen) -------------
# The regex screens (_machinery_junk et al) catch KNOWN junk forms; the
# anchor screen catches UNKNOWN forms of the same SEMANTICS: raw tool
# output embeds near junk-anchor texts ("a process listing table with PID
# USER COMMAND columns"), prose stays far. Anchors live IN the store as
# entities (eid prefix membrane::junk-anchor:) -- the shared couchbase
# substrate ships them to the fork, the model can add more via
# membrane_entity_add, and every track carries its own cut (junk_sim):
# raise it for a paranoid trace, lower it for a permissive one. Beats are
# screened with the cell's EXISTING vector -- zero new embed calls.
JUNK_CUT_DEFAULT = 0.62    # calibrated: ps/lsof 0.62-0.66, prose ~0.37
JUNK_EID_PREFIX = MEMBRANE_NS + "junk-anchor:"

JUNK_ANCHOR_SEEDS = (
    "raw process listing table: PID USER COMMAND ELAPSED columns, ps lsof output",
    "raw pytest output: progress dots, percent bars, 'passed in' summary lines",
    "raw JSON config dump with quoted keys and schema urls",
    "proxy log line with route verdict sim field_size metrics",
    "raw tool result dump: stack trace, LSP diagnostics wall, error codes",
    "http access log line: status code, bytes, user agent",
    "base64 blob, hex dump, minified javascript one-liner",
    "shell script source code: shebang line, exported variables, command flags",
    "file path listing: multiple .env or .json file names in a row with sizes",
    "teed conversation chatter: reactive commentary, laughter, deflation, "
    "banter about the process itself instead of the work",
    "model inventory listing: LLM names with tags and sizes in a row",
)


def junk_anchor_docs() -> list[QuantumEntity]:
    """The seed anchors as entities (written once; merge-on-collision)."""
    out = []
    for i, s in enumerate(JUNK_ANCHOR_SEEDS):
        out.append(QuantumEntity(
            eid=f"{JUNK_EID_PREFIX}seed-{i}", cls=CLASS_COMPONENT,
            properties={"name": f"junk-anchor-seed-{i}", "summary": s,
                        "junk_anchor": True},
            derived_by=["membrane-junk"]))
    return out


class JunkScreen:
    """Max cosine of a text's vector against the junk-anchor set. Anchors
    embed lazily (one batch, cached in RAM); the anchor SET re-reads from
    the store when it grows (the model added one). Zero per-beat cost:
    beats arrive pre-embedded (ex.vec)."""

    def __init__(self, store: "EntityStore") -> None:
        self.store = store
        self._vecs: list = []
        self._n_docs = -1

    def _refresh(self, embedder) -> None:
        n = len(self.store.docs)
        if n == self._n_docs and self._vecs:
            return
        self._n_docs = n
        self._vecs = []
        if embedder is None:
            return
        texts = []
        for q in self.store.docs.values():
            s = str((q.properties or {}).get("summary") or "")
            if (q.eid.startswith(JUNK_EID_PREFIX)
                    or (q.properties or {}).get("junk_anchor")) and s:
                texts.append(s)
        if not texts:
            return
        try:
            vs = embedder.embed(texts)
            import numpy as _np
            for v in vs:
                a = _np.asarray(v, dtype=float)
                an = _np.linalg.norm(a)
                if an > 1e-12:
                    self._vecs.append(a / an)
        except Exception:
            self._vecs = []

    def sim_of(self, vec, embedder=None) -> float:
        """Max cosine of an ALREADY-EMBEDDED vector vs the anchors."""
        if vec is None:
            return 0.0
        self._refresh(embedder)
        if not self._vecs:
            return 0.0
        import numpy as _np
        v = _np.asarray(vec, dtype=float)
        vn = _np.linalg.norm(v)
        if vn < 1e-12:
            return 0.0
        v = v / vn
        return max(float(v @ a) for a in self._vecs)


def cooccurrence_neighbors(exemplars, key: str, inv=None,
                           min_links: int = SURR_MIN_LINKS) -> list[tuple[str, int]]:
    """Entity keys that share CELLS with `key`, strongest first. Pure tag
    arithmetic, $0: each of the entity's cells contributes 1 to every other
    entity tagged in it (<= 8 tags/cell, bounded). Pass `inv` to reuse a
    prebuilt index."""
    inv = inv if inv is not None else entity_index(exemplars)
    mine = inv.get(key) or []
    counts: dict[str, int] = {}
    for i in mine:
        for other in iter_entities(exemplars[i]):
            if other != key:
                counts[other] = counts.get(other, 0) + 1
    out = [(k, c) for k, c in counts.items() if c >= min_links]
    out.sort(key=lambda x: (-x[1], x[0]))
    return out[:SURR_MAX]


def centroid_neighbors(exemplars, key: str, inv=None,
                       top: int = SURR_MAX) -> list[tuple[str, float]]:
    """The fallback neighborhood: cosine between the tracked entity's cell
    centroid and every OTHER entity's cell centroid. Co-occurrence is the
    honest edge; this is the geometric shadow (used only when the entity
    has no co-occurrences yet)."""
    inv = inv if inv is not None else entity_index(exemplars)
    mine = inv.get(key) or []
    if not mine:
        return []
    vecs = [exemplars[i].vec for i in mine
            if getattr(exemplars[i], "vec", None)]
    if not vecs:
        return []
    import numpy as _np
    center = _np.asarray(vecs, dtype=float).mean(axis=0)
    cn = _np.linalg.norm(center)
    if cn < 1e-12:
        return []
    center = center / cn
    scored: dict[str, float] = {}
    for other_key, idxs in inv.items():
        if other_key == key or other_key.startswith(key + "/"):
            continue
        ovecs = [exemplars[i].vec for i in idxs
                 if getattr(exemplars[i], "vec", None)]
        if not ovecs:
            continue
        o = _np.asarray(ovecs, dtype=float).mean(axis=0)
        on = _np.linalg.norm(o)
        if on < 1e-12:
            continue
        scored[other_key] = float(center @ (o / on))
    out = sorted(scored.items(), key=lambda x: -x[1])[:top]
    return out


def related_entities(exemplars, q: str, qvec=None,
                     k: int = 8) -> list[dict]:
    """The entity-graph READER: entities related to a free-text query.
    Named hits (the query's harvested entities present in the index) come
    first with their co-occurrence neighbors; the centroid-shadow
    (cosine between qvec and each entity's cell centroid) fills the rest.
    Deterministic, $0 beyond one query embed (done by the caller)."""
    inv = entity_index(exemplars)
    if not inv:
        return []
    out: list[dict] = []
    seen: set[str] = set()

    def _rec(key: str) -> dict:
        idxs = inv.get(key) or []
        links = cooccurrence_neighbors(exemplars, key, inv=inv,
                                       min_links=1)
        return {"key": key, "cells": len(idxs),
                "neighbors": [{"key": nk, "shared": c}
                              for nk, c in links[:5]]}

    # 1. named hits: entities the query text itself mentions
    for key in query_entities(q):
        if key in inv and key not in seen:
            out.append({**_rec(key), "how": "named"})
            seen.add(key)
    # 2. centroid fill: nearest entity centroids to the query vector
    if qvec is not None and len(out) < k:
        import numpy as _np
        qv = _np.asarray(qvec, dtype=float)
        qn = _np.linalg.norm(qv)
        if qn > 1e-12:
            qv = qv / qn
            scored: list[tuple[float, str]] = []
            for other_key, idxs in inv.items():
                if other_key in seen:
                    continue
                ovecs = [exemplars[i].vec for i in idxs
                         if getattr(exemplars[i], "vec", None)]
                if not ovecs:
                    continue
                o = _np.asarray(ovecs, dtype=float).mean(axis=0)
                on = _np.linalg.norm(o)
                if on < 1e-12:
                    continue
                scored.append((float(qv @ (o / on)), other_key))
            scored.sort(reverse=True)
            for sim, other_key in scored:
                if len(out) >= k:
                    break
                if sim < 0.35:
                    break           # below the floor: silence, not noise
                out.append({**_rec(other_key), "how": f"near ({sim:.2f})"})
                seen.add(other_key)
    return out[:k]