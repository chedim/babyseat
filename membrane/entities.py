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
import threading
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
CLASS_TIMELINE = "Timeline"            # a dreamed branch (the dreamer's output)
CLASS_SEAT = "Seat"                    # a seat's presence/identity (the auto-published organ)
CLASSES = (CLASS_COMPONENT, CLASS_DECISION, CLASS_FAILURE, CLASS_MODEL,
           CLASS_TOOL, CLASS_ENDPOINT, CLASS_CONFIG, CLASS_GOAL,
           CLASS_SENSOR, CLASS_ACTUATOR, CLASS_PROJECT, CLASS_SESSION,
           CLASS_TIMELINE, CLASS_SEAT)

# membrane-namespace doc-id prefix in the shared entities collection
MEMBRANE_NS = "membrane::"

# ---- deterministic extraction (capture time, $0) -----------------------------
_FILE_RE = re.compile(
    r"(?:[\w.-]+/)*[\w.-]+\.(?:py|md|json|jsonc|ts|js|jsx|tsx|mjs|cjs|go|rs|"
    r"toml|yaml|yml|sh|bash|html|css|txt|d\.ts)\b")
_SYMBOL_RE = re.compile(r"\b[a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*){1,3}\b"
                        r"|\b[a-zA-Z][a-z0-9]*(?:_[a-z0-9]+)+\b"
                        r"|\b_[a-z0-9]+(?:_[a-z0-9]+)+\b")
_MODEL_RE = re.compile(r"\b[a-zA-Z][\w.-]*(?::[\w.-]+){1,3}\b")
_URL_RE = re.compile(r"https?://[^\s\"'`)\]}]+")
# (?<![\w-]): a name INSIDE another one is not an HA id -- 'ha-light.status'
# (the method status on the class ha-light) read as light.status, the phantom
# HA entity that echoed to the top of the banner (2026-10-10)
_HA_RE = re.compile(r"(?<![\w-])(?:sensor|binary_sensor|light|switch|cover|fan|pump|battery)\.[\w]+")
_SHELL_VERBS = ("git", "docker", "kubectl", "brew", "pip", "npm", "pnpm",
                "cargo", "make", "curl", "ssh", "ollama", "pytest",
                "membrane", "python")
# prose dots that LOOK like symbols but are sentences ("we.think", "it.does")
_PRONOUN_HEADS = ("it", "we", "i", "they", "he", "she", "you", "this",
                  "that", "there", "here", "one", "something", "anything")
# URL-encoded harness artifacts that LOOK like symbols but are chat-template
# tokens or escaped markup leaking through capture ("u003cstart_of_turn" =
# URL-encoded "<start_of_turn"; "u003c" prefix = the encoded "<")
_CAPTURE_TOKEN_JUNK = re.compile(r"u003[cde][\w]*", re.I)

# ---- THE JSON-SAFE SCREEN (the 251-save-failures exhibit) -------------
# A script's patch/script_data value can be ANYTHING (a function, a
# module, a filehandle); one bad value poisons its doc and the
# side-index's whole-file json.dumps dies -- every save failing (251
# in one boot), the mirror starving in RAM forever. The screen:
# json-safe scalars pass; bounded str for keys; lists/dicts recurse
# (bounded depth); EVERYTHING ELSE wraps to a str -- the value stays
# inspectable, the file keeps writing.
_JSON_SAFE = (str, int, float, bool, type(None))


def json_safe(value, depth: int = 0, max_depth: int = 6):
    """One value's json-safe form: pass scalars, recurse containers,
    str() anything stranger (the poison becomes a readable string)."""
    if isinstance(value, _JSON_SAFE):
        return value
    if depth > max_depth:
        return str(value)[:400]
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:64]:
            out[str(k)[:120]] = json_safe(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [json_safe(v, depth + 1) for v in value[:128]]
    try:
        return str(value)[:400]
    except Exception:
        return "<unprintable>"  # type: ignore[return-value]


def sanitize_properties(props: dict) -> dict:
    """A properties dict's json-safe copy (the patch lane's contract:
    entity properties survive json.dumps -- ALWAYS)."""
    return {str(k)[:120]: json_safe(v)
            for k, v in list((props or {}).items())[:64]}


# THE LOCK'S CONTRACT (the timing fence): the store lock's hold is the
# FLAT snapshot -- for 10k plain docs, well under this bound (the old
# form's to_doc-under-lock took 20-45s; this is the regression tripwire).
_STORE_LOCK_HOLD_MAX_S = 1.0


def _stamp_guest_flavor(eid: str, props: dict) -> None:
    """THE GUEST FLAVOR (the two-universes datum, stamped at both
    ends): an eid in a FOREIGN namespace (entity::<x>::, episodic::)
    is a guest of this seat's store -- the flag lands whatever door
    the doc came through (load, upsert); the pull lane's imports
    carry it explicitly already."""
    if eid.startswith("entity::") or eid.startswith("episodic::"):
        props["guest"] = True

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
                "tool entity_add for:",
                "tool entity_edit for:",
                "tool entity_remove for:"):
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
    # the entity key 'files:membrane/entities.py'), not a model. A match
    # whose tag part contains '.py'/'\.js' etc. is a file:line ERROR
    # FRAGMENT ('gate.py:handle', 'file:line'), not a model.
    _CODE_EXTS = ("py", "js", "ts", "go", "rs", "rb", "java", "sh")
    models = []
    for m in _MODEL_RE.finditer(text):
        mv = m.group(0)
        if mv.startswith("http"):
            continue
        if m.end() < len(text) and text[m.end()] == "/":
            continue
        # a file:line ERROR FRAGMENT anywhere in the match ('gate.py:handle',
        # 'file:line') is not a model -- the code-extension check runs on
        # the WHOLE match
        if re.search(r"\.(?:%s)\b" % "|".join(_CODE_EXTS), mv):
            continue
        models.append(mv)
    add("models", models)
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
        # THE CAPTURE-TOKEN SCREEN (the u003cstart_of_turn exhibit):
        # URL-encoded harness artifacts die before the symbol signal check
        if _CAPTURE_TOKEN_JUNK.match(c):
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

def query_entities(text: str) -> list[str]:
    """Entity keys a QUERY mentions (same extractor, query side)."""
    return [f"{kind}:{v.lower()}"
            for kind, values in extract_entities(text).items()
            for v in values]

# ---- the scarcity covenant (the operator's ratio, revised: 6 tracks) ------
# TRACKS are the EXPENSIVE lane (every one renders a paragraph in WHERE WE
# ARE, every turn) -> MAX_TRACKS=6 user tracks + the default now-track
# (the clock, permanent, never counted). WATCHES are the CHEAP lane
# (event-driven, silent until fired) -> watch.py's MAX_WATCHES=7.
# Attention is scarce by construction; overflow evicts the STORY-POOREST
# track (never the default now-track).
MAX_TRACKS = 6
DEFAULT_TRACK_KEY = "session:now"

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
                for t, p in self.position_history[-RING_MAX:]]
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
            # THE POSITION FALLBACK (the goal-selection fix, the operator's
            # catch: 225 Goals hydrated with position=None -- the synth
            # goals carry semantic_embeddings / top-level vector_data but
            # no LIVING tracked ring yet, and from_doc read only tracked):
            # a young entity's position is its semantic embedding -- the
            # meaning IS the location until the entity has lived enough to
            # grow its own drift ring. The top-level vector_data (cortex's
            # own wire key) is the last resort.
            position: list | None = (
                [float(x) for x in tracked]
                if isinstance(tracked, (list, tuple)) and tracked else None)
            if not position and isinstance(vec, list) and vec:
                position = [float(x) for x in vec]
            if not position and isinstance(
                    doc.get("vector_data"), list) and doc["vector_data"]:
                position = [float(x) for x in doc["vector_data"]]
                if not vec:
                    vec = doc["vector_data"]
            q = QuantumEntity(
                eid=str(doc_id),
                cls=str(data.get("class") or CLASS_COMPONENT),
                properties=props,
                links=[int(x) for x in links if str(x).isdigit()],
                derived_by=[str(x) for x in derived],
                embedding=list(vec or []),
                position=position,
                # THE HYDRATION TRIM (the announcer-seat jetsam fix): foreign
                # docs can carry unbounded trajectories (the fork's
                # recordAlways pulse appends without a collapse); a hydrate
                # that swallowed them multiplied the side-index to 58MB and
                # RSS to 3.7GB. Trim at the door: the newest RING_MAX points
                # are the living geometry -- the rest was era history.
                position_history=([[float(t), [float(x) for x in p]]
                                   for t, p in trajectory][-RING_MAX:]
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
                             now: float, drift_gain: float = 0.5,
                             record_always: bool = False) -> dict:
        """ONE consolidation step (pure numpy, ~ms): drift the position
        toward the recent cells' centroid (time-weighted by the half-life)
        and record the motion in the coarsening history ring.

        ``cell_vecs_with_ts``: [(vec, born), ...] for this entity's linked
        SAFE cells (the caller harvests them; already unit-normalizable).
        ``drift_gain``: the blend step toward the weighted target
        (0.5 = the balanced default; the momentum pulse uses 0.9 -- the
        EMA already smooths across turns, the tracker must not re-smooth:
        a near-identical target at gain 0.5 moves the position ~1e-5,
        forever below grain, and the ring never grows).
        ``record_always``: the PULSE mode -- every step lands in the ring
        regardless of grain (the session:now momentum fold: a turn
        HAPPENED is itself the event; velocity needs its two points).

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
                            lam = drift_gain    # one step; the time weights
                            # above already encode recency -- the gain is
                            # just how decisively one step moves (0.5 the
                            # balanced default; 0.9 the momentum pulse)
                            blended = cur * (1.0 - drift_gain) \
                                + target * drift_gain
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
        self._ring_store(now, record_always=record_always)
        return {"moved": moved,
                "velocity": self.ring_velocity(now),
                "direction": self.ring_direction()}

    def _ring_store(self, ts: float, record_always: bool = False) -> None:
        """Insert (ts, position) into the history ring under the delta
        rule + era anchors, then collapse to the cap (oldest era first).
        ``record_always``: the PULSE mode -- skip the delta check (every
        step lands; the session:now momentum fold needs its two points)."""
        import numpy as _np
        hist = list(getattr(self, "position_history", []) or [])
        pos = _np.asarray(self.position, dtype=float)
        pn = _np.linalg.norm(pos)
        if pn < 1e-12:
            return
        pos = pos / pn
        # delta-perceptual insertion: only meaningful motion lands
        # (UNLESS pulse mode: every step records)
        if hist and not record_always:
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

    def project(self, horizon_h: float = 6.0, damped: bool = True
                ) -> list[list] | None:
        """THE TRAJECTORY PROJECTION (the operator's question: 'what
        happens if it continues that way?'): extrapolate the entity's
        future positions from its ring.

        Physics: constant-velocity along the FINE end's direction, with
        optional exponential damping -- a session's drift never holds a
        straight line (topics turn), so the honest model is v(t) = v0 *
        exp(-t/tau) with tau from the ring's own coarse/fine velocity
        ratio (the more the entity decelerated historically, the harder
        the projection damps). Pure numpy, ~us.

        Returns the projected polyline [[t, [x...]], ...] at hourly steps
        to the horizon (None when the ring is too short to have motion --
        a holding entity projects nowhere). NOT stored, NOT persisted: a
        projection is a question about the future, never a claim about
        it -- the banner renders it as 'toward X (projected)', the
        reasoner may use it as ONE weak vote, and the next real
        consolidation step overwrites any of it."""
        import numpy as _np
        h = getattr(self, "position_history", []) or []
        if len(h) < 2:
            return None
        (t0, p0), (t1, p1) = h[-2], h[-1]
        dt_h = (t1 - t0) / 3600.0
        if dt_h <= 1e-6:
            return None
        a = unit_or_none(p0)
        b = unit_or_none(p1)
        if a is None or b is None:
            return None
        v = (b - a)                          # the fine-end velocity direction
        speed = (1.0 - float(a @ b)) / dt_h  # the current pace (cos/h)
        if speed < 1e-4:
            return None                      # holding: nothing to project
        # the damping constant from the entity's OWN history: how much
        # slower the fine end moves than the coarse end (a decelerating
        # entity keeps decelerating -- the one lesson the ring holds)
        if len(h) >= 3 and damped:
            coarse = unit_or_none(h[0][1])
            coarse_speed = ((1.0 - float(coarse @ b))
                            / max((t1 - h[0][0]) / 3600.0, 1e-6)
                            if coarse is not None else 0.0)
            ratio = speed / max(coarse_speed, 1e-6)
            tau = max(1.0, min(24.0, horizon_h * max(ratio, 0.25)))
        else:
            tau = horizon_h
        cur = _np.asarray(b, dtype=float)
        out = []
        steps = max(1, int(horizon_h))
        for k in range(1, steps + 1):
            t = t1 + k * 3600.0
            damped_speed = speed * float(_np.exp(-k / tau)) if damped else speed
            # the position walks the velocity direction, decaying in step
            # length: pos_k = b + v_hat * (integral of speed) -- the damped
            # sum converges, so a 24h projection saturates instead of
            # flying off the sphere
            total = speed * tau * (1.0 - float(_np.exp(-k / tau)))
            nxt = cur + v * total
            nn = _np.linalg.norm(nxt)
            if nn < 1e-12:
                break
            nxt = nxt / nn
            out.append([t, [float(x) for x in nxt]])
        return out if out else None

def unit_or_none(vec):
    import numpy as _np
    v = _np.asarray(vec, dtype=float)
    n = _np.linalg.norm(v)
    return v / n if n > 1e-12 else None

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

class MultiParentOps:
    """THE MULTI-PARENT UPGRADE (the operator's questions: 'what about
    multiple inheritance? how do we calculate offset then?'):

    - SET: add one or more parents (the ISA links), cycle-guarded
    - UNSET: remove one or more parents (un-inherit = drop the pointer)
    - OFFSETS: the per-relation offset, calculated live from the child's
      position vs each parent's -- 'what makes this child distinct from
      each parent separately'
    - The child's position is ALWAYS its own lived centroid (its bound
      contexts); the parents CLASSIFY it, they don't position it.

    Constraint propagation across multiple branches:
      living  -- OR across all branches (any living ancestor inherits
                 the constraint; you can't escape it by finding a
                 non-living branch)
      latch   -- the MOST RESTRICTIVE (lowest) latch across all branches
                 (multiple parents can't be used to escape a constraint
                 by finding a lax branch)
      fn      -- the UNION of all branches' fn schemas (the child is
                 callable through any parent's interface)
    """

    PARENTS_KEY = "parents"        # the property key (the list of eids)
    MAX_PARENTS = 8                # per entity (bounded, anti-flood)
    MAX_CHAIN = 16                 # the transitive walk depth

    @staticmethod
    def _parents_of(props: dict) -> list[str]:
        """The parents list from the entity's properties: the new
        `parents` key (list) or the legacy `parent` key (a single eid,
        migrated on read)."""
        v = props.get("parents")
        if isinstance(v, list):
            return [str(x) for x in v if x]
        legacy = props.get("parent")
        return [str(legacy)] if legacy else []

    @classmethod
    def register(cls, store):
        """Mount the multi-parent operations onto an EntityStore."""
        store.set_parents = cls.set_parents.__get__(store)
        store.unset_parent = cls.unset_parents.__get__(store)
        store.parent_offsets = cls.parent_offsets.__get__(store)
        store.all_parents = cls.all_parents.__get__(store)
        store.inherited_multi = cls.inherited_multi.__get__(store)

    @staticmethod
    def set_parents(store, spec: str, parent_specs: list[str]) -> dict:
        """Add multiple parents to one child, cycle-guarded per parent."""
        child = store._resolve_spec(spec)
        if child is None:
            return {"ok": False, "error": f"child '{spec}' not found"}
        props = child.properties or {}
        current = MultiParentOps._parents_of(props)
        added, rejected = [], []
        for ps in parent_specs:
            parent = store._resolve_spec(ps)
            if parent is None:
                rejected.append({"parent": ps, "error": "not found"})
                continue
            if parent.eid == child.eid:
                rejected.append({"parent": ps, "error": "self-parent"})
                continue
            # THE CYCLE GUARD: walk the full ancestor DAG transitively
            stack, seen = [parent], set()
            cycle = False
            while stack:
                w = stack.pop()
                if w.eid in seen:
                    continue
                seen.add(w.eid)
                if w.eid == child.eid:
                    cycle = True
                    break
                for pp in MultiParentOps._parents_of(
                        w.properties or {}):
                    pw = store.docs.get(pp)
                    if pw is not None:
                        stack.append(pw)
            if cycle:
                rejected.append({"parent": ps,
                                 "error": "cycle (the child is an "
                                          "ancestor)"})
                continue
            if parent.eid in current:
                continue                    # already linked
            current.append(parent.eid)
            added.append(parent.eid)
        if current != MultiParentOps._parents_of(props):
            props["parents"] = current[:MultiParentOps.MAX_PARENTS]
            child.properties = props
            store.upsert(child)
            store._announce("merged", child,
                            f"parents {added}" if added else "refreshed")
        return {"ok": True, "child": child.eid, "added": added,
                "rejected": rejected,
                "parents": current[:MultiParentOps.MAX_PARENTS]}

    @staticmethod
    def unset_parents(store, spec: str, parent_specs: list[str]) -> dict:
        """UN-INHERIT: remove one or more parents from the child. The
        parent pointer is dropped; the child's own position (from its
        lived instances) is unchanged."""
        child = store._resolve_spec(spec)
        if child is None:
            return {"ok": False, "error": f"child '{spec}' not found"}
        props = child.properties or {}
        current = MultiParentOps._parents_of(props)
        removed, not_found = [], []
        for ps in parent_specs:
            p = store._resolve_spec(ps)
            peid = p.eid if p is not None else ps
            if peid in current:
                current.remove(peid)
                removed.append(peid)
            else:
                not_found.append(peid)
        if removed:
            props["parents"] = current
            child.properties = props
            store.upsert(child)
        return {"ok": True, "child": child.eid, "removed": removed,
                "not_found": not_found,
                "remaining": current}

    @staticmethod
    def all_parents(store, spec: str) -> list["QuantumEntity"]:
        """The full ancestor DAG (multi-parent aware): every parent,
        transitively, across all branches, deduped, nearest first."""
        child = store._resolve_spec(spec)
        if child is None:
            return []
        out, seen, queue = [], {child.eid}, [
            store.docs.get(pp) for pp in
            MultiParentOps._parents_of(child.properties or {})]
        queue = [q for q in queue if q is not None]
        while queue:
            w = queue.pop(0)
            if w.eid in seen:
                continue
            seen.add(w.eid)
            out.append(w)
            for pp in MultiParentOps._parents_of(w.properties or {}):
                pw = store.docs.get(pp)
                if pw is not None and pw.eid not in seen:
                    queue.append(pw)
        return out

    @staticmethod
    def inherited_multi(store, spec: str) -> dict:
        """The constraint inheritance, multi-parent aware:
          latch  -- the MOST RESTRICTIVE (lowest) across all branches
          living -- OR across all branches (any living ancestor inherits)
          fns    -- the union of all branches' fn schemas"""
        child = store._resolve_spec(spec)
        if child is None:
            return {"latch": None, "living": False, "fns": []}
        ancestors = MultiParentOps.all_parents(store, spec)
        latch = None
        living = False
        fns = []
        for a in ancestors:
            props = a.properties or {}
            lv = props.get("latch")
            if lv is not None:
                try:
                    lv = max(0, int(lv))
                except (TypeError, ValueError):
                    lv = None
                if lv is not None:
                    latch = lv if latch is None else min(latch, lv)
            if props.get("living"):
                living = True
            fn = props.get("fn")
            if fn:
                fns.append({"parent": a.eid, "fn": fn})
        own_latch = (child.properties or {}).get("latch")
        if own_latch is not None:
            try:
                own = max(0, int(own_latch))
                latch = own if latch is None else min(latch, own)
            except (TypeError, ValueError):
                pass
        if (child.properties or {}).get("living"):
            living = True
        return {"latch": latch, "living": living, "fns": fns}

    @staticmethod
    def parent_offsets(store, spec: str) -> dict:
        """The per-relation offset: the child's position vs each DIRECT
        parent's position -- 'what makes this child distinct from each
        parent separately'. Returns {parent_eid: cosine-difference}:
        0 = the child sits ON the parent (no distinguishing direction),
        approaching 1 = fully distinct (the offset is the meaning)."""
        child = store._resolve_spec(spec)
        if child is None or not getattr(child, "position", None):
            return {}
        import numpy as _np
        cv = _np.asarray(child.position, dtype=float)
        cn = _np.linalg.norm(cv)
        if cn < 1e-12:
            return {}
        cv = cv / cn
        out: dict[str, float] = {}
        props = child.properties or {}
        for pp in MultiParentOps._parents_of(props):
            pw = store.docs.get(pp)
            if pw is None or not getattr(pw, "position", None):
                continue
            pv = _np.asarray(pw.position, dtype=float)
            pn = _np.linalg.norm(pv)
            if pn < 1e-12:
                continue
            pv = pv / pn
            # the offset magnitude: cosine distance between the child's
            # position and the parent's (0 = the child IS the parent's
            # centroid, 2 = fully opposite); normalized to [0, 1]
            out[pp] = round((1.0 - float(cv @ pv)) / 2.0, 4)
        return out

class TimelineOps:
    """The alternative-timeline organ (the dreamer's substrate): dreamed
    branches as ENTITIES, each a child of the true timeline.

    THE OPERATOR'S DESIGN (the timeline-dreaming arc): solution generation
    IS timeline generation. session:now is the only TRUE timeline (the
    lived present); every dreamed branch is a Timeline entity whose ISA
    parent is session:now -- it inherits the present's latch/living
    covenant, and its branch_sim (the parent-offset cosine) IS the
    how-far-fetched measure. Status is reality's vote, not self-declaration:
    quarantined (proposed) -> confirmed (a real turn served its direction)
    / refuted (a near turn it could not serve) / impossible (the vet found
    it violating an inherited latch or a FailureMode parent -- KEPT: the
    field's learned physics). Nothing here ever authors a real turn; the
    timeline renders as a suggestion only.
    """

    TL_STATUS_QUARANTINED = "quarantined"
    TL_STATUS_CONFIRMED = "confirmed"
    TL_STATUS_REFUTED = "refuted"
    TL_STATUS_IMPOSSIBLE = "impossible"

    @staticmethod
    def _tl_eid(text: str) -> str:
        """A stable eid for one dreamed branch: tl:<slug of the terminal
        text> -- collision-merged (the union merge contract)."""
        import hashlib as _hashlib
        slug = _hashlib.sha256((text or "").strip().lower().encode()) \
            .hexdigest()[:12]
        return f"{MEMBRANE_NS}tl:{slug}"

    def make_timeline(store, terminal: str, vec: list | None = None,
                      seed_intent: str | None = None,
                      steps: list | None = None,
                      parent: str = "membrane::session:now",
                      branch_sim: float | None = None,
                      status: str = "quarantined",
                      links: list | None = None) -> "QuantumEntity":
        """Commit one dreamed branch as a Timeline entity. Merged on
        collision (a re-proposed branch STRENGTHENS the existing one --
        derived_by/links union, never a duplicate). The parent ISA link
        is set on the entity doc directly (the dreamer's commit path is
        hot: no MCP roundtrip)."""
        ent = QuantumEntity(
            eid=TimelineOps._tl_eid(terminal),
            cls=CLASS_TIMELINE,
            properties={
                "name": (terminal or "")[:80],
                "status": status,
                "origin": "dream",
                "seed_intent": seed_intent or "",
                "terminal": (terminal or "")[:2000],
                "steps": [str(s)[:300] for s in (steps or [])][:12],
                "born": _t.time(),
                "branch_sim": (float(branch_sim) if branch_sim is not None
                               else None),
                "curiosity": 0.7,
            },
            links=[int(x) for x in (links or []) if isinstance(x, int)],
        )
        if vec:
            ent.embedding = [float(x) for x in vec]
            ent.position = [float(x) for x in vec]
        # THE SEAT-LOCAL QUARANTINE (the operator's ruling: "shouldn't
        # that seat have its own session:now and tracked entities?"):
        # quarantined + impossible branches stay RAM-only (ephemeral --
        # the upsert's shared write-through skips ephemeral docs). A
        # seat's unconfirmed dreams are nobody else's business -- the
        # parallel seat's banner should render ITS OWN dreams, not this
        # seat's speculation. CONFIRMED branches (reality voted) become
        # part of the shared record: the elect's upsert publishes them.
        # THE SEAT-LOCAL QUARANTINE (the operator's ruling: "shouldn't
        # that seat have its own session:now and tracked entities?"):
        # quarantined + impossible branches stay RAM-only (ephemeral --
        # the upsert's shared write-through skips ephemeral docs). A
        # seat's unconfirmed dreams are nobody else's business -- the
        # parallel seat's banner should render ITS OWN dreams, not this
        # seat's speculation. CONFIRMED branches (reality voted) become
        # part of the shared record: the elect's upsert publishes them.
        if status in (TimelineOps.TL_STATUS_QUARANTINED,
                      TimelineOps.TL_STATUS_IMPOSSIBLE):
            ent.properties["ephemeral"] = True
        store.upsert(ent)
        # the ISA link (the branch hangs off the true timeline); best-effort
        # -- the parent may not exist (no session:now yet): the branch stays
        # parentless rather than failing the commit.
        try:
            if store._resolve_spec(parent) is not None:
                MultiParentOps.set_parents(store, ent.eid, [parent])   # the static form (the bound one never mounted)
        except Exception:
            pass
        return store.docs.get(ent.eid, ent)

    def elect_timeline(store, turn_vec: list, intent_sims: dict,
                       confirm_sim: float = 0.72,
                       turn_state: list | None = None,
                       lane=None,
                       confirm_shape_sim: float = 0.69,
                       confirm_sim_shape_ok: float = 0.60) -> dict:
        """REALITY'S VOTE, timeline-shaped: for every quarantined branch
        near this REAL turn's vec -- the branch whose seed_intent the turn
        actually serves (intent_sim >= STEER line) is CONFIRMED (status
        flips in place; the branch leaves quarantine); a near branch that
        serves nothing open is REFUTED (removed -- the dream was noise).
        Returns the counts for the audit.

        THE ORDER-SHAPED BALLOT (the VSA lane's #7 consumer): ``turn_state``
        (the real turn's hypervector state) + ``lane`` (the seat's token
        lane) arm the second sense -- a branch whose TERMINAL TEXT encodes
        to a state near the turn's (Hamming-sim >= ``confirm_shape_sim``,
        computed lazily and cached on the branch properties) confirms at a
        RELAXED cosine bar (``confirm_sim_shape_ok``): reality voted by
        SHAPE where the vec alone was shy. Still one ballot, two readings;
        the shape lane never confirms a branch the cosine vetoes outright
        (a far vec AND far shape stays refuted/missed). Lane failure is
        silent: today's vec-only ballot runs."""
        import numpy as _np
        tv = _np.asarray(turn_vec, dtype=float)
        tn = _np.linalg.norm(tv)
        if tn < 1e-12:
            return {"confirmed": 0, "refuted": 0}
        tv = tv / tn
        confirmed = refuted = 0
        confirmed_eids: list[str] = []
        turn_state_arr = (_np.asarray(turn_state, dtype=_np.uint8)
                          if turn_state is not None and lane is not None
                          else None)
        for ent in list(store.docs.values()):
            if ent.cls != CLASS_TIMELINE:
                continue
            props = ent.properties or {}
            if props.get("status") != TimelineOps.TL_STATUS_QUARANTINED:
                continue
            pv = getattr(ent, "position", None) or getattr(ent, "embedding", None)
            if not pv:
                continue
            pvv = _np.asarray(pv, dtype=float)
            pn = _np.linalg.norm(pvv)
            if pn < 1e-12:
                continue
            sim = float(tv @ (pvv / pn))
            # the shape reading (lazy, cached): the branch terminal's
            # token state vs the real turn's
            shape_sim = None
            if turn_state_arr is not None:
                try:
                    terminal = str(props.get("terminal") or "")
                    if terminal:
                        cached = (props.get("vsa_shape") or {})
                        if cached.get("cb") == lane.codebook.id:
                            from .vsa import unpack_state as _unpack
                            from .vsa import hamming as _ham
                            shape_sim = 1.0 - _ham(
                                turn_state_arr, _unpack(cached["s"]))
                        else:
                            bs = lane.encode(terminal)
                            if bs is not None:
                                from .vsa import hamming as _ham, \
                                    pack_state as _pack
                                shape_sim = 1.0 - _ham(turn_state_arr, bs)
                                props["vsa_shape"] = {
                                    "cb": lane.codebook.id,
                                    "s": _pack(bs)}
                except Exception:
                    shape_sim = None
            _bar = confirm_sim
            if shape_sim is not None and shape_sim >= confirm_shape_sim:
                _bar = min(confirm_sim, confirm_sim_shape_ok)
            if sim < _bar:
                continue
            served = (props.get("seed_intent") or "") in (intent_sims or {})
            if served:
                props["status"] = TimelineOps.TL_STATUS_CONFIRMED
                props["confirmed_ts"] = _t.time()
                # THE CURIOSITY DECAY (self-extinguishing): a confirmed
                # branch's question is served -- relax the hunger (floor
                # 0.05: never fully dead, a confirmed branch can still
                # teach). The dreamer's proposal weighting reads this.
                try:
                    props["curiosity"] = round(
                        max(0.05, float(props.get("curiosity", 0.7)) * 0.4),
                        3)
                except (TypeError, ValueError):
                    pass
                confirmed += 1
                confirmed_eids.append(ent.eid)
                # THE PERSISTENCE (the elect's write): the status flip +
                # the curiosity decay land on the shared doc too (the
                # parallel seats see the confirmation) -- best-effort,
                # degraded mode keeps the RAM layer as the truth.
                try:
                    store.upsert(ent)
                except Exception:
                    pass
            else:
                store.remove(ent.eid)
                refuted += 1
        return {"confirmed": confirmed, "refuted": refuted,
                "confirmed_eids": confirmed_eids}

    def fire_watch(self, eid: str, event: dict) -> None:
        """THE WATCH-FIRE STAMP: the fired event lands on the entity doc
        (watch_last_fire: kind, entity, ts + the event head). The loop
        layer reads the queue; the ENTITY keeps its last fire's
        provenance (the react/ignore verdict lands on the doc later)."""
        ent = self.docs.get(eid) or self.resolve(eid, exact=True)
        if ent is None:
            return
        props = ent.properties or {}
        props["watch_last_fire"] = {"kind": event.get("kind"),
                                    "ts": event.get("ts"),
                                    "distance": event.get("distance"),
                                    "event_id": event.get("event_id")}
        self.upsert(ent, embedder=None)

    def stamp_watch_decision(self, eid: str, verdict: dict) -> None:
        """THE LOOP'S RETURN PATH: the thinking loop's decision
        (reacted/ignored + what it did) stamps the entity
        (watch_last_decision) -- the loop's verdict lives beside the
        entity's watch provenance."""
        ent = self.docs.get(eid) or self.resolve(eid, exact=True)
        if ent is None:
            return
        props = ent.properties or {}
        props["watch_last_decision"] = dict(verdict or {})
        props["watch_decisions"] = (list(props.get("watch_decisions") or []) + [dict(verdict or {})])[-3:]
        self.upsert(ent, embedder=None)

    def open_timelines(store, parent: str = "membrane::session:now",
                       limit: int = 8) -> list:
        """The render surface: quarantined + confirmed branches under the
        true timeline, newest first, bounded."""
        out = []
        for ent in list(store.docs.values()):
            if ent.cls != CLASS_TIMELINE:
                continue
            props = ent.properties or {}
            if props.get("status") in (TimelineOps.TL_STATUS_QUARANTINED,
                                       TimelineOps.TL_STATUS_CONFIRMED):
                parents = MultiParentOps._parents_of(props)
                if parent and parents and parent not in parents:
                    continue
                out.append(ent)
        out.sort(key=lambda e: float((e.properties or {}).get("born") or 0),
                 reverse=True)
        return out[:max(1, int(limit))]

    # ---- timeline navigation (the scripts' joints) --------------------------
    def timelines_of(store, spec: str, open_only: bool = True
                     ) -> list:
        """The branches hanging off THIS entity (Timeline class via ISA or
        link-adjacent). The dreamer's outputs, navigable from the parent
        -- the thinker's extension path reads this."""
        ent = store._resolve_spec(spec)
        if ent is None:
            return []
        out = []
        for e in list(store.docs.values()):
            if e.cls != CLASS_TIMELINE:
                continue
            props = e.properties or {}
            if open_only and props.get("status") not in (
                    TimelineOps.TL_STATUS_QUARANTINED,
                    TimelineOps.TL_STATUS_CONFIRMED):
                continue
            if spec in MultiParentOps._parents_of(props) \
                    or ent.eid in (e.links or []):
                out.append(e)
        out.sort(key=lambda e: float((e.properties or {}).get("born") or 0),
                 reverse=True)
        return out

    def related_by_class(store, spec: str, cls: str, limit: int = 8
                         ) -> list:
        """The neighborhood by class: entities sharing a tag with spec
        (the co-tag relation -- the soft edges), the caller's filter
        applied. Read-only navigation for scripts."""
        ent = store._resolve_spec(spec)
        if ent is None:
            return []
        tags = set((ent.properties or {}).get("entities") or [])
        if not tags:
            return []
        out = []
        for e in list(store.docs.values()):
            if e.eid == ent.eid or e.cls != cls:
                continue
            if tags & set((e.properties or {}).get("entities") or []):
                out.append(e)
                if len(out) >= max(1, int(limit)):
                    break
        return out

class EntityStore(MultiParentOps, TimelineOps):
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

    def __init__(self, path: str | None = None, bucket: str = "cortex-core",
                 shared: bool = True, origin: str | None = None) -> None:
        self.path = Path(path) if path else None
        self.bucket = bucket
        # THE TEST-ISOLATION GUARD (the 'doomed.py in the fork's banner'
        # bug): test stores were created with shared=True default, so
        # when couchbase was UP the test fixtures WROTE THROUGH to the
        # shared collection -- my test entities materialized in the
        # fork's banner via its read-through. Tests pass shared=False:
        # RAM/JSON only, the shared substrate never touched.
        self.shared = shared
        # THE SEAT-FIRST FIELD (the operator's 2026-10-01 design): tier 1
        # is local and authoritative; tier 2 (couchbase) holds ONLY
        # published entities. The origin is the pub-namespace identity
        # (the persisted <data-dir>/origin tag, same as the field's).
        self.origin = origin or "seat@local"
        # THE SEAT'S ROOT (the stale-route retire's reference): the
        # start folder (the field path's parent's parent for the
        # .membrane home; the cwd for the bare boot) — the boot sets
        # it explicitly; the derived-from-origin tag is the fallback.
        self.root = os.getcwd()
        self.docs: dict[str, QuantumEntity] = {}
        # entity TRACKS: key -> {key, label, op, n, alerted}. The banner
        # lane: live cell counts in WHERE WE ARE, optional boundary alerts.
        self.tracks: dict[str, dict] = {}
        # THE ANNOUNCEMENT QUEUE (in-memory, exactly-once): entity BIRTHS
        # and MERGES announce once to the model (banner ALERT-style
        # lines); the queue is the delivery ledger (a restart losing
        # undelivered ones is the same covenant as the ALERT queue --
        # notifications, not state). Only GRADUATIONS announce: a new
        # STORE DOC (night synthesis, model add, gray-band growth,
        # materialization). Nursery activity (side-index tags, first-
        # sightings, read-through, reharvest) stays silent.
        self.announcements: list[dict] = []
        self.junk_screen = JunkScreen(self)
        self._cb_warned = False
        self._dirty = False
        self._save_timer = None
        self._mirror = None                   # the parsed file, kept warm
        # THE STORE LOCK (the '[entities] side-index save failed'
        # flood + the announcer seat's 37MB TORN entities.json): the
        # debounced save timer thread serializes self.docs WHILE the
        # chat/night/thinker threads upsert-merge -> the json.dumps read
        # a dict mutating mid-walk. Every docs mutation goes through
        # upsert/remove/merge; the save snapshots UNDER this lock.
        self._store_lock = threading.RLock()
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
                    "(list via tracks)"}
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

    # ---- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
            self._mirror = d
            for k, rec in (d.get("docs") or {}).items():
                q = QuantumEntity.from_doc(k, rec)
                if q is None:
                    continue
                q.properties = sanitize_properties(q.properties)
                _stamp_guest_flavor(k, q.properties)
                self.docs[k] = q
        except Exception as e:
            print(f"[entities] side-index load failed ({e})",
                  file=sys.stderr)

    def save(self) -> None:
        """THE SIDE-INDEX SAVE, rebuilt (the 45s-PENDING exhibit):
        the old form built EVERY entity's full to_doc() dict (~10KB of
        embedding copy per doc, 9.5k docs = ~95MB of dict-building)
        UNDER _store_lock -- every reader (entity_edit/related/state)
        queued behind a 20-45s save, debounced to repeat every 10s.
        NOW: the lock holds one FLAT snapshot (shallow-copied property
        dicts, milliseconds); to_doc + json.dumps run OUTSIDE it. A
        doc that still can't serialize is skipped AND NAMED (the eid
        prints -- the poison is visble, not silent); the file always
        writes."""
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            # THE FLAT SNAPSHOT (the lock's ms contract): shallow-copy
            # each entity's scalar state + properties dict -- no
            # to_doc, no embedding copies, under the lock.
            with self._store_lock:
                # list(): the docs are added to by threads that don't take
                # this lock (every module's _entity()) -- iterate a copy
                flat = [(eid, dict(ent.properties or {}), ent.cls,
                         list(ent.links), list(ent.derived_by))
                        for eid, ent in list(self.docs.items())
                        if not ((ent.properties or {}).get("ephemeral"))]
                tracks = dict(self.tracks) if self.tracks else None
            snapshot: dict = {}
            for eid, props, cls, links, derived_by in flat:
                try:
                    # THE SCREEN at the save's other end: any poison
                    # that made it into properties BEFORE the screen
                    # existed wraps to str here -- the file always
                    # writes, the value stays inspectable.
                    props = sanitize_properties(props)
                    ent = self._snap_to_entity(eid, props, cls, links,
                                               derived_by)
                    snapshot[eid] = ent.to_doc()   # the heavy build HERE
                except Exception as e:
                    # THE POISON IS NAMED: a doc that can't serialize
                    # skips (never kills the file) and prints its eid
                    # -- the operator sees WHO, the queue keeps flowing
                    print(f"[entities] side-index: doc {eid} skipped "
                          f"({str(e)[:80]})", file=sys.stderr)
            # preserve tracks: docs-save must not clobber the track lane
            # (the old shape wrote {"docs": ...} bare -- every save wiped
            # the planted tracks; the silent tracking-death bug)
            try:
                d = json.loads(self.path.read_text()) \
                    if self.path.exists() else {}
            except Exception:
                d = {}
            self._mirror = d
            d["docs"] = snapshot
            if tracks:
                d["tracks"] = tracks
            elif "tracks" in d and not self.tracks:
                d.pop("tracks", None)     # tracks dropped while down
            tmp.write_text(json.dumps(d))
            os.replace(tmp, self.path)
            self._side_saved = True       # the retry flag: clean
        except Exception as e:
            self._side_saved = False
            print(f"[entities] side-index save failed ({e})",
                  file=sys.stderr)

    def _snap_to_entity(self, eid: str, props: dict, cls: str,
                        links: list, derived_by: list):
        """The flat snapshot's doc builder: a QuantumEntity from the
        copied scalars (the embedding/position references ride -- they
        are read-only at save time)."""
        ent = QuantumEntity.__new__(QuantumEntity)
        ent.eid = eid
        ent.cls = cls
        ent.properties = props
        ent.links = links
        ent.derived_by = derived_by
        ent.embedding = None              # re-embedded on load (cheap)
        ent.position = None
        ent.position_history = []
        return ent

    SAVE_DEBOUNCE_S = 10.0

    def save_soon(self) -> None:
        """Mark dirty and coalesce: ONE whole-file write per interval. The
        mirror is large (each doc carries vectors); rewriting it per
        upsert wedged every bulk pass (the boot consolidation included)
        behind multi-second writes."""
        self._dirty = True
        if self._save_timer is None:
            th = threading.Timer(self.SAVE_DEBOUNCE_S, self._flush_save)
            th.daemon = True
            self._save_timer = th
            th.start()

    def _flush_save(self) -> None:
        self._save_timer = None
        if not self._dirty:
            return
        try:
            # THE LOST MARK (2026-10-11 sweep): clearing _dirty AFTER the save
            # erased the mark of a write that landed DURING it -- that write
            # waited for some unrelated next upsert. Cleared BEFORE: a write
            # during the save re-marks it and arms the next debounce.
            self._dirty = False
            self.save()
        finally:
            # THE RETRY (the silent-starve exhibit): _dirty stays up
            # when the save failed -- the next debounce tries again
            # (the file never starves because poison exists)
            if not getattr(self, "_side_saved", True):
                self._dirty = True
                if self._save_timer is None:
                    self.save_soon()

    def flush(self) -> None:
        """Make pending store writes durable NOW (tests, shutdown)."""
        self._cancel_save_timer()
        if self._dirty:
            self._dirty = False
            self.save()

    def _cancel_save_timer(self) -> None:
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None

    # ---- couchbase (the shared collection) ----------------------------------
    def close(self) -> None:
        """Shutdown: close the couchbase cluster so its IO threads stop
        BEFORE interpreter finalization (a live asio thread touching
        CPython during Py_Finalize is a SIGSEGV, seen in the wild)."""
        self._cancel_save_timer()
        if self._dirty:
            self._dirty = False
            try:
                self.save()
            except Exception as e:
                print(f"[entities] save on close failed ({e})", file=sys.stderr)
        cluster = getattr(self, "_cluster_ref", None)
        if cluster is None:
            return
        self._cluster_ref = None
        try:
            cluster.close()
        except Exception:
            pass

    def _entities_collection(self):
        """cortex-core._default.entities (the QuantumEntity home). None when
        couchbase is unreachable (JSON/RAM still carry) OR when the store
        is unshared (the test-isolation guard: tests never touch the
        shared substrate). Stashes the cluster
        handle (self._cluster_ref) for N1QL (Collections have no .query)."""
        # THE CLUSTER-HANDLE CACHE (the memory-leak fix): this used to
        # construct a FRESH couchbase Cluster on EVERY call -- and the
        # write-through calls it per entity upsert. Each Cluster spawns
        # its own SDK io workers + connection buffers and is never
        # closed; over a session the leaked workers piled into GBs (the
        # operator's 30GB Terminal tree with two seats). One handle per
        # store: construct once, reuse for every read and write.
        cached = getattr(self, "_cb_coll_cache", None)
        if cached is not None:
            return cached
        if not self.shared:
            return None
        # THE FAST DEGRADE (the door-visibility CI hang): a probe-miss
        # skips the SDK entirely -- the boot used to sit in a multi-second
        # connect timeout here too when no cluster exists.
        from .efstore import probe_local_couchbase
        if not probe_local_couchbase():
            return None
        try:
            from couchbase.cluster import Cluster
            from couchbase.auth import PasswordAuthenticator
            from couchbase.options import ClusterOptions
            conn = os.environ.get("COUCHBASE_CONN_STRING",
                                  "couchbase://127.0.0.1")
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
            self._cb_coll_cache = coll
            return coll
        except Exception as e:
            if not self._cb_warned:
                print(f"[entities] couchbase write-through unavailable "
                      f"({str(e)[:80]}); JSON side-index carries",
                      file=sys.stderr)
                self._cb_warned = True
            return None

    # ---- the merge contract --------------------------------------------------
    def upsert(self, ent: QuantumEntity, embedder=None,
               sync: bool = False) -> bool:
        """Merge one entity (union on collision), persist its LOCAL home.
        Returns True when written (new or merged).
        THE SEAT-FIRST FIELD (the operator's 2026-10-01 design): the
        auto write-through is RETIRED -- upsert never touches the cloud
        (couchbase down changes nothing; tier 1 is authoritative). The
        ``sync`` parameter is kept for the call sites/tick gate and is
        now inert (no caller can share by accident; only publish() puts
        anything on the wire).
        ``sync=False`` (the tick-pace applies: the clock's silent
        upsert): the tick's truth is seat-local liveliness, and the
        fork read-through bonus never needs it.
        BIRTHS announce: a NEW store doc (any creation path -- night
        synthesis, model add, materialization, gray-band growth) queues
        an announcement; merges-into-existing stay silent here (the
        merge itself announces via entity_edit's merged_into)."""
        if not ent.eid:
            return False
        _stamp_guest_flavor(ent.eid, ent.properties or {})
        existing = self.docs.get(ent.eid)
        is_new = existing is None
        if existing is not None:
            existing.merge(ent)
            ent = existing
        else:
            self.docs[ent.eid] = ent
        self.save_soon()
        if is_new and not (ent.properties or {}).get("ephemeral"):
            self._announce("new", ent)
        # THE RETURN SEMANTICS fix (the shared=False test failures): the
        # RAM/JSON write IS the success.
        return True

    # ---- tier 2: PUBLISH (the model's explicit exports) ----------------------
    @staticmethod
    def _strip_code(props: dict) -> dict:
        """THE NO-INFECTION RULE (the operator's 2026-10-01 call): an
        exported doc carries the entity's STATE, never the foreign
        seat's executable scripts (a leaked tick_script would RUN
        inside the importing seat -- the seat-first rule: every seat's
        code is local, only data travels). Strips the trigger scripts
        AND the engine's script-* state; ``script_render`` (the last
        rendered line, inert text) survives."""
        return {k: v for k, v in props.items()
                if not k.endswith("_script")
                and k not in ("script_disabled", "script_failures",
                              "script_every_n")}

    def publish(self, spec, embedder=None) -> dict:
        """EXPORT an entity to the global namespace (tier 2): a snapshot
        doc under a PER-ORIGIN key (``pub:<origin>:<eid>`` -- two seats
        publishing the same eid never contend, there is no other seat's
        doc to fight) stamped {by, at, ver, eid}. Versioned: republishing
        bumps ``published_ver`` (the import organ upgrades only on newer
        versions). The model's decision -- the store NEVER publishes
        alone (only the presence entity is seat-published, by the
        organ's design). Degrades when couchbase is down."""
        ent = self._resolve_spec(spec) if isinstance(spec, str) else spec
        if ent is None:
            return {"ok": False, "error": f"no entity matches {spec!r}"}
        coll = self._entities_collection()
        if coll is None:
            return {"ok": False, "error": "couchbase unavailable; "
                    "the local tier carries (publish again when up)"}
        props = ent.properties or {}
        ver = int(props.get("published_ver") or 0) + 1
        props["published_ver"] = ver
        props["published_at"] = _t.time()
        key = f"pub:{self.origin}:{ent.eid}"
        doc = ent.to_doc(embedder)
        doc["data"]["properties"] = self._strip_code(
            doc["data"].get("properties") or {})
        doc["published"] = {"by": self.origin, "at": props["published_at"],
                            "ver": ver, "eid": ent.eid}
        try:
            coll.upsert(key, doc)
        except Exception as e:
            return {"ok": False, "error": f"publish failed ({str(e)[:80]})"}
        self.save_soon()                 # the local stamps, persisted
        self._announce("published", ent)
        return {"ok": True, "key": key, "ver": ver, "eid": ent.eid}

    def unpublish(self, spec) -> dict:
        """RETRACT: remove THIS seat's pub doc for the entity (an original
        author's retraction; a foreign seat's pub doc is never touched).
        The import stays local -- a retraction freezes the imported copy
        at its last version (the local graph is the authority)."""
        ent = self._resolve_spec(spec) if isinstance(spec, str) else spec
        if ent is None:
            return {"ok": False, "error": f"no entity matches {spec!r}"}
        coll = self._entities_collection()
        if coll is None:
            return {"ok": False,
                    "error": "couchbase unavailable; nothing to retract"}
        key = f"pub:{self.origin}:{ent.eid}"
        try:
            coll.remove(key)
        except Exception as e:
            return {"ok": False, "error": f"unpublish failed ({str(e)[:80]})"}
        props = ent.properties or {}
        props.pop("published_ver", None)
        props.pop("published_at", None)
        self.save_soon()
        return {"ok": True, "key": key, "eid": ent.eid}

    def _pub_query(self, key_prefix: str = "pub:%", limit: int = 2000):
        """The pull: N1QL over the shared entities collection for
        published docs (per-origin ``pub:`` keys only -- the raw
        per-origin state write-throughs are retired; the shared layer is
        the UNION of published entities). Yields (doc_id, doc)."""
        coll = self._entities_collection()
        if coll is None:
            return
        cluster = getattr(self, "_cluster_ref", None)
        if cluster is None:
            # THE FAKE/DEGRADED PATH (tests): a stub collection that can
            # answer queries itself. Real couchbase always hands the
            # store a cluster (the N1QL home); collections alone have
            # no .query -- so this fallback never runs on real seats.
            qr = coll.query("published", limit=limit)
            for row in qr:
                yield row.get("id", ""), {k: v for k, v in row.items()
                                          if k != "id"}
            return
        from couchbase.options import QueryOptions
        import datetime as _dt
        qr = cluster.query(
            "SELECT meta().id AS id, t.* FROM "
            f"`{self.bucket}`.`_default`.`entities` t "
            f"WHERE meta().id LIKE '{key_prefix}' "
            "LIMIT $lim",
            QueryOptions(timeout=_dt.timedelta(seconds=30),
                         named_parameters={"lim": limit}))
        for row in qr:
            yield row.get("id", ""), {k: v for k, v in row.items()
                                      if k != "id"}

    def import_published(self, limit: int = 2000, skip_own: bool = True) -> int:
        """The MAPPING organ (tier 2 -> tier 1): pull every published
        entity, FOREIGN origins first-class -- each maps onto the local
        graph under its shared-vocabulary eid, stamped ``imported_by``
        (the published owner), and from then on it RUNS LOCALLY (drift,
        tracks, its own scripts). The local doc is ALWAYS the authority:
        an existing local eid is never clobbered by a foreign snapshot
        (the imported copy only lands when the entity doesn't live
        here). Idempotent; returns the count imported this pull."""
        n = 0
        try:
            rows = list(self._pub_query(limit=limit))
        except Exception as e:
            print(f"[entities] published pull skipped ({str(e)[:80]})",
                  file=sys.stderr)
            return 0
        for doc_id, doc in rows:
            meta = doc.get("published") or {}
            owner = meta.get("by")
            eid = meta.get("eid") or doc_id
            if not eid or (skip_own and owner == self.origin):
                continue
            if eid in self.docs:
                continue           # the local copy is the authority
            q = QuantumEntity.from_doc(eid, doc)
            if q is None:
                continue
            # DEFENSE IN DEPTH: old publishers (or a hostile row) may
            # carry code -- an imported doc never runs foreign scripts.
            props = q.properties or {}
            props.update(self._strip_code(props))
            props["imported_by"] = owner
            # THE GUEST GATE (the two-universes datum): an imported eid
            # is a GUEST -- flagged explicitly so every query surface
            # can partition the universes (the default scan hides
            # guests; guest:true asks for them). The eid's own
            # namespace (entity::<x>::, episodic::) flavors it too --
            # the pull stamps ALL imports.
            props["guest"] = True
            # THE STALE-ROUTE RETIRE (the seat-root-move datum): an
            # eid whose body names an absolute path OUTSIDE this
            # seat's root is a dead route -- retired, not a ghost.
            # The body's LEGACY HEAD strips first ('files:/abs' is an
            # absolute body under its family's head, not a relative one).
            try:
                body = eid[len(MEMBRANE_NS):] \
                    if eid.startswith(MEMBRANE_NS) else eid
                _head, _sep, _rest = body.partition(":")
                if _head in ("files", "symbols", "models", "shell",
                             "urls") and _sep:
                    body = _rest
                _root = getattr(self, "root", "") or ""
                if body.startswith("/") and _root \
                        and not body.startswith(_root):
                    props["retired"] = True
                    props["retired_ts"] = _t.time()
            except Exception:
                pass
            if meta.get("at"):
                props["imported_at"] = meta["at"]
            self.docs[eid] = q
            n += 1
        if n:
            self.save_soon()
        return n

    def hydrate_from_couchbase(self, limit: int = 2000) -> int:
        """The read-through (SEAT-FIRST: the pull is pub-only now) --
        foreign PUBLISHED entities map onto the local graph and run
        locally. The raw per-origin state write-throughs are retired;
        the old membrane::/cortex:: read is delegated to import's
        contract (same organ, tier-2 semantics)."""
        return self.import_published(limit=limit)

    def update_presence(self, extra: dict | None = None) -> dict:
        """THE PRESENCE ORGAN (the operator's 2026-10-01 design): the
        seat's own ``Seat`` entity describing THIS seat and its NOW,
        published to the global tier on the SLOW cadence (the thinker's
        every-60-beats pulse -- a lot slower than the 1Hz clock, which
        stays the tick primitive). The ONE seat-auto publish lane: the
        model never has to remember to announce itself, and every OTHER
        seat reads it as the roster (peers). Properties: ``seat`` (the
        origin) + the caller's facts (branch, head, uptime, docs,
        ports...) + ``presence_ts``."""
        eid = f"{MEMBRANE_NS}presence"
        ent = self.docs.get(eid)
        if ent is None:
            ent = QuantumEntity(eid=eid, cls=CLASS_SEAT, properties={})
        props = ent.properties or {}
        props["seat"] = self.origin
        if extra:
            props.update(extra)
        props["presence_ts"] = _t.time()
        ent.properties = props
        self.upsert(ent)
        return self.publish(ent)

    def peers(self, limit: int = 200) -> list:
        """THE ROSTER (the thinker's anchor): the OTHER seats' presence
        entities off the global tier -- who is alive, what they run, how
        long up. A seat never introduces itself to itself (own-origin
        rows are skipped). Degrades to [] couchbase-down."""
        rows = self._pub_query(key_prefix=f"pub:%:{MEMBRANE_NS}presence",
                               limit=limit)
        out = []
        try:
            listed = list(rows)
        except Exception as e:
            print(f"[entities] peers pull skipped ({str(e)[:80]})",
                  file=sys.stderr)
            return []
        for doc_id, doc in listed:
            meta = doc.get("published") or {}
            owner = meta.get("by")
            if not owner or owner == self.origin:
                continue
            data = doc.get("data") or {}
            out.append({"origin": owner,
                        "eid": meta.get("eid") or doc_id,
                        "ver": meta.get("ver"),
                        "properties": data.get("properties") or {}})
        return out

    def all(self) -> list[QuantumEntity]:
        return list(self.docs.values())

    def find(self, name_l: str) -> QuantumEntity | None:
        """Case-insensitive name lookup (the viewer/join path)."""
        name_l = (name_l or "").strip().lower()
        for q in list(self.docs.values()):
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
        """An eid, an exact eid-suffix, or a name fragment -> the entity.
        MERGE POINTER-FOLLOWING: a merged id resolves to its survivor
        (the mark-don't-delete covenant -- the merged doc stays, the
        graph stays connected).
        THE MIRROR COLLAPSE (the seven-boot re-mint exhibit): a DOUBLED
        legacy key resolves to its canonical first (canon_eid's peel) --
        the mirror doc only answers when the canonical is ABSENT."""
        from .night import canon_eid as _canon
        canon = _canon(spec)
        if canon and canon != (spec or "").strip().removeprefix(MEMBRANE_NS):
            _canon_ent = self.docs.get(canon if canon.startswith(MEMBRANE_NS)
                                       else MEMBRANE_NS + canon)
            if _canon_ent is not None:
                return self._follow_merges(_canon_ent)
        ent = self._resolve_spec_raw(spec)
        return self._follow_merges(ent) if ent is not None else None

    def _follow_merges(self, ent):
        seen: set[str] = set()
        while ent is not None and (ent.properties or {}).get("merged_into"):
            dst = str(ent.properties["merged_into"])
            if dst in seen:
                break                  # merge cycle: stop pointing
            seen.add(dst)
            ent = self.docs.get(dst)
        return ent

    # the field's short kinds -> the store's long heads (eids._LEGACY_KIND
    # inverted; 'm' is the field's MIRROR of membrane:: first, models second)
    _SHORT_TO_LONG = {"s": "symbols", "f": "files", "c": "shell", "u": "urls"}
    _FIELD_ONLY = ("w", "p")

    def _spec_candidates(self, spec: str) -> list[str]:
        s = spec.strip()
        bare = s[len(MEMBRANE_NS):] if s.lower().startswith(MEMBRANE_NS) else s
        out = [s, MEMBRANE_NS + bare]
        head, _, rest = bare.partition(":")
        h = head.lower()
        if rest and h == "m":
            out += [MEMBRANE_NS + rest, MEMBRANE_NS + "models:" + rest]
            rh, _, rrest = rest.partition(":")
            if rrest and rh.lower() in self._SHORT_TO_LONG:
                out.append(MEMBRANE_NS + self._SHORT_TO_LONG[rh.lower()] + ":" + rrest)
        elif rest and h in self._SHORT_TO_LONG:
            out.append(MEMBRANE_NS + self._SHORT_TO_LONG[h] + ":" + rest)
        return out

    def _lower_index(self) -> dict:
        """lower(eid) -> eid, rebuilt when the store's size moves (the
        resolver's case-blind lookup; the review's circular 'nearest')."""
        n = len(self.docs)
        idx = getattr(self, "_lc_idx", None)
        if idx is None or getattr(self, "_lc_n", -1) != n:
            idx = {}
            for eid in list(self.docs.keys()):
                idx.setdefault(eid.lower(), eid)
            self._lc_idx, self._lc_n = idx, n
        return idx

    def resolve(self, spec: str, exact: bool = False) -> "QuantumEntity | None":
        """THE ONE RESOLVER (2026-10-11 sweep): any spelling of a stored
        entity -> its doc (the namespace, the m: mirror, the field's short
        kinds, case, merges). exact=True drops the last-resort suffix/name
        guess -- for write paths (stamps, consequences) where a wrong hit is
        worse than none."""
        if not spec:
            return None
        if exact:
            from .night import canon_eid as _canon
            c = _canon(spec)
            if c:
                hit = self.docs.get(MEMBRANE_NS + c.removeprefix(MEMBRANE_NS))
                if hit is not None:
                    return self._follow_merges(hit)
            ent = self._resolve_spec_raw(spec, fuzzy=False)
            return self._follow_merges(ent) if ent is not None else None
        return self._resolve_spec(spec)

    def junk_field_ids(self) -> set:
        """JUNK BY MARKING, NOT DELETING (2026-10-11, the operator: "instead of
        deleting junk, you could just link it to a 'junk' node"): the field ids
        the engine stops amplifying -- every stored entity whose ISA chain
        holds a hub named 'junk', plus the field-only ids (a field word or an
        unresolved symbol can't take a parent) listed on a hub's `junk`."""
        from .eids import field_id as _fid
        hubs = [d for d in list(self.docs.values())
                if str((d.properties or {}).get("name") or "").lower() == "junk"
                or d.eid.lower().endswith(":junk") or d.eid.lower() == (MEMBRANE_NS + "junk")]
        if not hubs:
            return set()
        sig = (len(self.docs), tuple(sorted((h.eid, len((h.properties or {}).get("junk") or [])) for h in hubs)))
        if getattr(self, "_junk_sig", None) == sig:
            return self._junk_ids
        hub_ids = {h.eid for h in hubs}
        out = set()
        for h in hubs:
            for x in (h.properties or {}).get("junk") or []:
                out.add(str(x))
                out.add(_fid(str(x)))
        for d in list(self.docs.values()):
            ps = (d.properties or {}).get("parents") or []
            ps = ps if isinstance(ps, list) else [ps]
            if any(p in hub_ids or str(p).lower().endswith(":junk") or str(p).lower() == "junk" for p in ps):
                out.add(_fid(d.eid))
                out.add(d.eid)
        self._junk_sig, self._junk_ids = sig, out
        return out

    def by_field_id(self, fid: str) -> "QuantumEntity | None":
        """A FIELD id -> the stored entity it is about (the flush's intake,
        the field's subjects): field_id(eid) -> eid, rebuilt as the store moves."""
        from .eids import field_id as _fid
        n = len(self.docs)
        idx = getattr(self, "_fid_idx", None)
        if idx is None or getattr(self, "_fid_n", -1) != n:
            idx = {}
            for eid in list(self.docs.keys()):
                idx.setdefault(_fid(eid), eid)
            self._fid_idx, self._fid_n = idx, n
        hit = idx.get(fid) or idx.get(str(fid or "").lower())
        return self.docs.get(hit) if hit else None

    def _resolve_spec_raw(self, spec: str, fuzzy: bool = True) -> QuantumEntity | None:
        """The direct resolution (no merge-pointer following): exact, then
        the spec's spellings (membrane:: added, the m: mirror peeled, the
        field's short kinds mapped), each case-blind; then an eid-suffix
        or name match."""
        spec = (spec or "").strip()
        if not spec:
            return None
        if spec in self.docs:
            return self.docs[spec]
        idx = self._lower_index()
        for c in self._spec_candidates(spec):
            if c in self.docs:
                return self.docs[c]
            hit = idx.get(c.lower())
            if hit is not None and hit in self.docs:
                return self.docs[hit]
        if not fuzzy:
            return None
        low = spec.lower()
        hits = [q for q in list(self.docs.values())
                if q.eid.lower().endswith(":" + low)
                or str((q.properties or {}).get("name", "")).lower() == low]
        if len(hits) > 1:
            # THE DELIBERATE ONE WINS (2026-10-10: a night-minted twin
            # 'models:mqtt:bridge' shadowed Argus's 'Component:mqtt:bridge'):
            # an entity someone made beats one the night materialized from text
            def _made(q):
                pp = q.properties or {}
                night = pp.get("materialized_by") == "night" or "membrane-night" in (q.derived_by or [])
                return 0 if not night else 1
            hits.sort(key=_made)                     # stable: same-rank order kept
        return hits[0] if hits else None

    def miss_hint(self, spec: str) -> str:
        """A miss that is not a typo: field-only keys explain themselves."""
        head = (spec or "").strip().removeprefix(MEMBRANE_NS).partition(":")[0].lower()
        if head in self._FIELD_ONLY:
            return (f"'{spec}' is a field-only {'word' if head == 'w' else 'phrase'} "
                    "(no stored entity to read): look at it with "
                    "entities_related or query, not entity_read")
        return ""

    def near_specs(self, spec: str, k: int = 3) -> list[str]:
        """The MIS'S nearest candidates (the self-correct-in-one-call
        aid, 2026-10-06): the store's existing eids that share the
        spec's head/tail or name-substring -- the model's next call
        carries them in the refusal instead of a dead end. ''-ish specs
        return []. Each is a REAL eid (its own case, namespace peeled) --
        it resolves as written and is never the failed spec itself."""
        s = (spec or "").strip().lower().removeprefix(MEMBRANE_NS)
        if len(s) < 3:
            return []
        frag = s.rsplit(":", 1)[-1]
        scored: list[tuple[int, str]] = []
        for eid in list(self.docs.keys())[:4000]:
            bare = eid[len(MEMBRANE_NS):] if eid.startswith(MEMBRANE_NS) else eid
            low = bare.lower()
            if low == s:
                continue
            tail = low.rsplit(":", 1)[-1]
            score = 0
            if frag and frag in tail:
                score = 3 if tail.startswith(frag) else 2
            elif frag and tail and (tail.startswith(frag)
                                    or frag.startswith(tail)
                                    or frag in low):
                # BOTH directions: the spec may carry MORE letters than
                # the doc's tail (the model's invented suffix) or fewer
                # ('gitx' vs 'git' shares the stem)
                score = 1
            if score:
                scored.append((-score, bare))
        scored.sort()
        return [e for _, e in scored[:max(1, k)]]

    def add(self, cls: str, name: str, summary: str = "",
            links=None) -> dict:
        """Model-side entity ADD: merge-on-collision with any existing doc
        (same name = same thing), write-through both homes."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "name required"}
        # THE NAME LINT (2026-10-08): a created name never carries the
        # field's mirror prefix, the class, stacked kinds or a literal
        from .eids import lint_name as _lint_name
        _bad = _lint_name(name)
        if _bad:
            return {"ok": False, "error": f"name {name[:60]!r} refused: {_bad}"}
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

    def set_parent(self, spec: str, parent_spec: str) -> dict:
        """ENTITY INHERITANCE (the operator's ISA design): the child
        entity gains a parent concept -- 'a letter points to letters'.
        The relation is an OFFSET: the child's position sits at the
        parent's position plus the child's distinguishing direction
        (learned from its own instances). The parent's CONSTRAINTS
        propagate down the chain (latch level, the `living` flag) and
        the parent's fn schema is the child's interface superset.
        Resolution follows the merged_into pointer pattern: a parent
        id that itself has a parent resolves transitively."""
        child = self._resolve_spec(spec)
        if child is None:
            return {"ok": False, "error": f"child '{spec}' not found"}
        parent = self._resolve_spec(parent_spec)
        if parent is None:
            return {"ok": False, "error": f"parent '{parent_spec}' not found"}
        if child.eid == parent.eid:
            return {"ok": False, "error": "self-parent is a cycle"}
        # THE CYCLE GUARD: follow the parent chain transitively; a cycle
        # (the child IS its own ancestor) is rejected
        walker = parent
        hops = 0
        while walker is not None and hops < 16:
            pp = (walker.properties or {}).get("parent")
            if pp == child.eid:
                return {"ok": False,
                        "error": f"cycle: {child.eid} is an ancestor of "
                                 f"{parent_spec}"}
            walker = self.docs.get(pp) if pp else None
            hops += 1
        props = child.properties or {}
        props["parent"] = parent.eid
        self.upsert(child)
        self._announce("merged", child, f"ISA {parent.eid}")
        return {"ok": True, "child": child.eid, "parent": parent.eid}

    def parent_chain(self, spec: str) -> list["QuantumEntity"]:
        """The ISA walk: the entity's full ancestor chain, nearest parent
        first (the transitive resolution for constraint inheritance and
        fn-schema lookup)."""
        out = []
        ent = self._resolve_spec(spec)
        hops = 0
        while ent is not None and hops < 16:
            pp = (ent.properties or {}).get("parent")
            if not pp:
                break
            parent = self.docs.get(pp)
            if parent is None:
                break
            out.append(parent)
            ent = parent
            hops += 1
        return out

    def inherited_latch(self, spec: str) -> int | None:
        """The latch level, inherited down the ISA chain: the child's own
        latch if set, else the nearest ancestor's, else None."""
        ent = self._resolve_spec(spec)
        hops = 0
        while ent is not None and hops < 16:
            lv = (ent.properties or {}).get("latch")
            if lv is not None:
                try:
                    return max(0, int(lv))
                except (TypeError, ValueError):
                    pass
            pp = (ent.properties or {}).get("parent")
            ent = self.docs.get(pp) if pp else None
            hops += 1
        return None

    def inherited_living(self, spec: str) -> bool:
        """The covenant's operational hook: `living` propagates down the
        ISA chain -- if ANY ancestor is marked living, the child inherits
        the constraint (all life held of highest value)."""
        ent = self._resolve_spec(spec)
        hops = 0
        while ent is not None and hops < 16:
            if (ent.properties or {}).get("living"):
                return True
            pp = (ent.properties or {}).get("parent")
            ent = self.docs.get(pp) if pp else None
            hops += 1
        return False

    def children_of(self, spec: str) -> list["QuantumEntity"]:
        """The ISA walk reversed: entities whose parent chain passes
        through this one."""
        ent = self._resolve_spec(spec)
        if ent is None:
            return []
        eid = ent.eid
        out = []
        for q in list(self.docs.values()):
            if q is ent:
                continue
            walker, hops = q, 0
            while walker is not None and hops < 16:
                pp = (walker.properties or {}).get("parent")
                if pp == eid:
                    out.append(q)
                    break
                walker = self.docs.get(pp) if pp else None
                hops += 1
        return out

    def edit(self, spec: str, summary=None, links=None, cls=None,
             merged_into=None, nudge_script=None,
             delete_script=None, mention_script=None,
             thought_script=None, dreamed_script=None,
             tick_script=None,
             watch_script=None, watch: dict | None = None,
             getter_script=None, watch_ops=None, watch_fields=None,
             properties: dict | None = None,
             publish: bool = False, unpublish: bool = False) -> dict:
        """Model-side entity EDIT: patch summary/links/class on the
        resolved entity. Only provided fields change.
        ``publish=True`` exports the entity to the global namespace
        (tier 2 -- the model's decision: a versioned, per-origin-key
        snapshot; republishing bumps the version). ``unpublish=True``
        retracts this seat's export (the imported copy stays local).
        ``properties``: a bounded property patch (the model's write path
        for goal status -- fulfill/drop via the edit surface; the same
        guards as the script-patch contract: <=16 keys, 2000-char values,
        script_*/status-engine keys untouchable except the goal's own
        status/summary).
        ``merged_into``: MARK-don't-delete subsumption (the operator's
        covenant) -- the source doc STAYS, marked
        properties['merged_into'] = <dst eid>, its links union into the
        destination, and the merge ANNOUNCES (an event the model should
        see, same as a birth). The graph reader follows the pointer:
        a merged id transparently resolves to its survivor.
        ``nudge_script`` / ``delete_script``: PROGRAMMABLE ENTITIES (the
        operator's design) -- small lifecycle scripts the model writes;
        they run at consolidation time (delete first, then nudge), see
        the entity's geometry as the ``state`` variable, and return
        {render?, data?, delete?} as ``result``. Size-capped, audited,
        3-strike auto-disable on failures. Empty string clears."""
        ent = self._resolve_spec(spec)
        if ent is None:
            near = self.near_specs(spec)
            hint = (f"; nearest: {', '.join(near)} -- resolve by eid or "
                    f"the exact name" if near else
                    " (list via related)")
            return {"ok": False,
                    "error": f"no entity matches {spec!r}{hint}"}
        if summary is not None:
            ent.properties["summary"] = str(summary).strip()
        if links is not None:
            clean = [int(i) for i in links
                     if str(i).lstrip("-").isdigit()]
            merged = set(ent.links or []) | set(clean)
            ent.links = sorted(merged)
        if cls and cls in CLASSES:
            ent.cls = cls
        # THE SCRIPT FIELDS (programmable entities, size-capped; ''
        # clears the script and its failure state). Setting a NEW
        # nudge script also clears the stale `script_render` (the
        # operator's catch: the old fixture line rode the banner
        # until the next consolidation pass otherwise) -- the new
        # script's render lands at the next pass.
        # THE TRIGGER SCRIPTS (model-authored policy): the getter/setter
        # organ's per-trigger bodies -- mention (0.25s, read-shape),
        # thought (1s), dreamed (2s). The engine (night.py's
        # run_entity_update_script) dispatches by trigger; the budgets
        # narrow as the trigger approaches the live chat. Same
        # size-caps/3-strikes contract as nudge/delete.
        for field, val, cap in (("nudge_script", nudge_script,
                                 SCRIPT_MAX_NUDGE),
                                ("delete_script", delete_script,
                                 SCRIPT_MAX_DELETE),
                                ("mention_script", mention_script,
                                 SCRIPT_MAX_NUDGE),
                                ("thought_script", thought_script,
                                 SCRIPT_MAX_NUDGE),
                                ("dreamed_script", dreamed_script,
                                 SCRIPT_MAX_NUDGE),
                                # the hum's 1s-heartbeat trigger (the
                                # clock organ; cooldown-free, paced by
                                # the entity's own `tick_every` prop)
                                ("tick_script", tick_script,
                                 SCRIPT_MAX_NUDGE),
                                # the autonode trigger (the operator's
                                # 2026-10-01 ask: the model authors its
                                # own watchers) -- fires on external
                                # events (file/mqtt/signal) through the
                                # bounded pool
                                ("watch_script", watch_script,
                                 SCRIPT_MAX_NUDGE),
                                # the READ-BACK trigger (2026-10-02): a
                                # dynamic entity's getter runs ON A READ
                                # (the dict return = the state facts)
                                ("getter_script", getter_script,
                                 SCRIPT_MAX_NUDGE)):
            if val is None:
                continue
            sval = str(val)
            if len(sval) > cap:
                return {"ok": False,
                        "error": f"{field} too long ({len(sval)} > {cap}) "
                                 f"-- split: keep the entity's script under "
                                 f"{cap} (drop comments, fold helpers into "
                                 f"the body's own functions) or move the "
                                 f"extra logic into a second entity + a "
                                 f"watch/field lane"}
            if sval.strip():
                # THE SYNTAX AT THE DOOR (jobs.py, 2026-10-12): a define returns
                # before its first fire -- a script that can't even compile is
                # refused now, with the reason ('return' outside function: the
                # engine runs scripts as module code, set result = {...})
                try:
                    compile(sval, f"<{field}>", "exec")
                except SyntaxError as e:
                    hint = (" -- scripts run as module code: set a top-level result = {...}, "
                            "no return outside a function") if "return" in str(e) else ""
                    return {"ok": False, "error": f"{field} doesn't compile: {e.msg} (line {e.lineno}){hint}"}
            if not sval.strip():
                ent.properties.pop(field, None)
                ent.properties.pop("script_disabled", None)
                ent.properties.pop("script_failures", None)
            else:
                ent.properties[field] = sval
                # a rewrite is a fresh start (clear the disable state,
                # the cooldowns -- the new script runs on the next try --
                # AND the stale render from the previous script)
                ent.properties.pop("script_disabled", None)
                ent.properties.pop("script_failures", None)
                ent.properties.pop("script_cooldowns", None)
                if field == "nudge_script":
                    ent.properties.pop("script_render", None)
                # SETTING IS ARMING (2026-10-11): thought/dreamed scripts set
                # before their triggers were wired wait dormant until set again
                if field in ("thought_script", "dreamed_script"):
                    import time as _t
                    ent.properties[field.replace("_script", "_armed_ts")] = _t.time()
        # THE SPATIAL OPS (the reactive layer's CRUD; the same lane's
        # events fire -- the covenants live in watch_agents)
        if watch_ops is not None:
            try:
                from . import watch_agents as _wa
                # the REAL seat when wired (the attention budget counts
                # the board's watches too); the bare store otherwise
                srv = getattr(self, "_srv", None) or type("S", (), {
                    "entity_store": self,
                    "events_pool": getattr(self, "_events_pool", None)})()
                target = self.docs.get(ent.eid) or ent
                res_ops = _wa.edit_watch_ops(
                    srv, target,
                    watch_ops if isinstance(watch_ops, list) else [],
                    replace=watch_ops is not None and
                    isinstance(watch_ops, list) and not watch_ops)
            except Exception as e:
                return {"ok": False, "error": f"watch_ops: {e}"[:120]}
            # THE REFUSAL REACHES THE MODEL (2026-10-08): the cap/budget
            # answer was dropped here -- the edit 'succeeded', nothing
            # was planted, the model never learned it was full
            if isinstance(res_ops, dict) and not res_ops.get("ok"):
                return res_ops
        if watch_fields is not None:
            res_wf = self.edit_watch_fields(ent.eid, watch_fields)
            if isinstance(res_wf, dict) and not res_wf.get("ok"):
                return res_wf
        # THE WATCH SELECTOR (the autonode): what external events fire
        # this entity's watch_script -- {kind: file|signal, plus
        # the optional glob/topic/name narrowing}. This is the CHEAP
        # pre-filter; the script does the real filtering. Empty dict
        # clears it.
        if watch is not None:
            if isinstance(watch, dict) and watch:
                ent.properties["watch"] = {
                    str(k)[:40]: (v if k == "glob" else str(v)[:120])
                    for k, v in list(watch.items())[:8]}
                # THE SELECTOR ATTACH EVENT (the watch lane's covenant:
                # the autonode's sensor armed = the loops see it)
                try:
                    from . import watch_agents as _wa
                    pool = getattr(self, "_events_pool", None)
                    if pool is not None:
                        _wa.notify_watch_attached(self, pool, eid,
                                                  lane="selector",
                                                  detail=dict(ent
                                                              .properties["watch"]))
                except Exception:
                    pass
            else:
                ent.properties.pop("watch", None)
                try:
                    from . import watch_agents as _wa
                    pool = getattr(self, "_events_pool", None)
                    if pool is not None:
                        _wa.notify_watch_detached(self, pool, [eid],
                                                  lane="selector")
                except Exception:
                    pass
        # THE PROPERTY PATCH (the model's write path for goal state):
        # bounded like the script-patch contract -- <=16 keys, 2000-char
        # values, the engine's script_* keys and another entity's keys
        # untouchable. The goal's own status IS touchable (fulfill/drop
        # via the edit surface; audited).
        parents_patch = None
        if isinstance(properties, dict) and properties:
            for k, v in list(properties.items())[:16]:
                kk = str(k)[:80]
                if kk in ("script_every_n", "script_cooldown_s"):
                    # the two documented knobs (2026-10-11 sweep: every script_*
                    # key was skipped, so neither could ever be set) -- bounded
                    try:
                        num = float(v)
                        ent.properties[kk] = (int(min(96, max(1, num))) if kk == "script_every_n"
                                              else round(min(3600.0, max(0.0, num)), 1))
                    except (TypeError, ValueError):
                        pass
                    continue
                if kk.startswith("script_"):
                    continue
                if kk in ("parents", "parent"):
                    # ISA goes through set_parents (names resolved, cycles
                    # refused) -- a raw write was silently inert (2026-10-10)
                    if isinstance(v, str):
                        try:
                            v = json.loads(v) if v.strip().startswith("[") else [v]
                        except ValueError:
                            v = [v]
                    parents_patch = [str(x) for x in (v or [])]
                    continue
                props2 = ent.properties or {}
                props2[kk] = (v if not isinstance(v, str) else v[:2000])
                ent.properties = props2
        if parents_patch is not None:
            # a parents patch REPLACES the list, in exactly the given order --
            # the dispatch order is the author's (inherit only ADDS; the probe
            # suite could not swap an order, 2026-10-10)
            resolved = []
            for spec in parents_patch:
                d = self._resolve_spec(spec)
                if d is None:
                    return {"ok": False, "error": f"parents: no entity matches {spec!r}"}
                if d.eid == ent.eid:
                    return {"ok": False, "error": "parents: an entity cannot be its own parent"}
                if d.eid not in resolved:
                    resolved.append(d.eid)
            ent.properties = ent.properties or {}
            ent.properties["parents"] = []
            pr = MultiParentOps.set_parents(self, ent.eid, resolved) if resolved else {"ok": True}
            if not pr.get("ok"):
                return {"ok": False, "error": f"parents: {pr.get('error')}"}
            ent.properties["parents"] = [x for x in resolved if x in (ent.properties.get("parents") or resolved)]
        if merged_into:
            dst = self._resolve_spec(str(merged_into))
            if dst is None or dst.eid == ent.eid:
                return {"ok": False,
                        "error": f"merge target {merged_into!r} not found "
                                 f"(or same entity)"}
            # the mark: the source STAYS, pointing at its survivor
            ent.properties["merged_into"] = dst.eid
            # the union: the destination inherits the source's links
            for l in (ent.links or []):
                if l not in (dst.links or []):
                    dst.links.append(l)
            dst.derived_by = sorted(set(dst.derived_by or [])
                                    | {"model-merge"})
            ent.derived_by = sorted(set(ent.derived_by or [])
                                    | {"model-merge"})
            self.upsert(dst)
            self.upsert(ent)
            self._announce("merged", ent, extra=f"into {dst.eid}")
            return {"ok": True, "eid": ent.eid,
                    "merged_into": dst.eid}
        ent.derived_by = sorted(set(ent.derived_by or []) | {"model-edit"})
        self.upsert(ent)
        res = {"ok": True, "eid": ent.eid, "class": ent.cls,
               "summary": ent.properties.get("summary", ""),
               "links": ent.links}
        # THE PUBLISH VEHICLE (the seat-first field, the model's decision):
        # edit(spec, publish=True) exports to the global namespace;
        # edit(spec, unpublish=True) retracts this seat's export. A
        # republish bumps the version (imports upgrade only on newer).
        if publish:
            res["published"] = self.publish(ent)
        if unpublish:
            res["unpublished"] = self.unpublish(ent)
        return res

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
    # is unified for the LLM via MCP (watch /
    # unwatch / tracks) and the proxy
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

    def edit_watch_fields(self, eid: str, filters) -> dict:
        """THE FIELD-WATCH PLANT (the model's cap-3 filters over the
        entity's own field state -- the grammar lives in night.py;
        the verbs are pos/align/vel, crossing semantics)."""
        from .night import edit_watch_fields as _ewf
        try:
            return _ewf(self, eid, filters)
        except Exception as e:
            return {"ok": False, "error": str(e)[:120]}

    def watch_track(self, key: str, boundary: str = "",
                    label: str = "", owner: str = "") -> dict:
        """Plant a banner track: the entity's live safe-cell count renders
        in WHERE WE ARE every turn (constant tracking). An optional boundary
        ('enter N' / 'exit N' / bare 'N') turns it into an EVENT trigger --
        a state machine that alerts once per region transition (re-entry
        re-arms by construction). Re-planting the same boundary keeps the
        state; a changed boundary restarts it.

        THE OWNER (the lane gate's stamp, 2026-10-06): 'operator' (the
        agent-less/banner lanes) delivers the crossing as the banner's
        ALERT line (as always); a LISTENER's name ('watch:<eid>') routes
        the firing into the EVENTS POOL -- the listener receives its own
        watch's crossing as its next turn's message (the thinker's
        event-input flow), never the banner."""
        key = key.strip().lower()
        from .eids import lint_key as _lint_key
        _bad = _lint_key(key)
        if _bad and key:
            return {"ok": False, "error": f"track key refused: {_bad}"}
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
        # THE OWNER's stamp: set on plant; an ownerless RE-PLANT keeps
        # the lane (a re-arm never jumps the delivery lane; an explicit
        # owner='' claim moves it back deliberately).
        rec["owner"] = (owner or rec.get("owner") or "operator").strip()
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
        # THE ATTACH EVENT (the watch covenant: a sensor armed is a
        # decision the loops see) -- the pool rides the store when the
        # proxy wires it
        try:
            from . import watch_agents as _wa
            pool = getattr(self, "_events_pool", None)
            if pool is not None:
                _wa.notify_watch_attached(self, pool, key, lane="track",
                                          detail={"boundary": boundary})
        except Exception:
            pass
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
        # the forms the models write: 'enter: 3', '>= 5', 'above 5 cells',
        # '< 2', 'exit below 2' (25 refusals, 2026-10-08)
        s = re.sub(r"\b(cells?|count|safe)\b", " ", s)
        m = re.fullmatch(r"(enter|exit)?\s*[:=]?\s*(>=|<=|>|<|above|below|over|under)?"
                         r"\s*(\d{1,6})\s*", s)
        if not m:
            raise ValueError("boundary must be 'enter N' or 'exit N' "
                             "(bare N or '>= N' = enter N, '< N' = exit N)")
        op, cmp_, n = m.group(1), m.group(2), int(m.group(3))
        if op is None:
            op = "exit" if cmp_ in ("<", "<=", "below", "under") else "enter"
        return {"enter": n} if op == "enter" else {"exit": n}

    def evaluate_tracks(self, count_fn=None) -> list[dict]:
        """The per-turn state machine step: for every bounded track, compute
        the live safe-cell count and transition the inside/outside state.
        Returns the ALERTS fired this turn for OPERATOR-owned tracks (one
        per transition; re-entry re-arms by construction).

        THE OWNER SPLIT (the lane gate's delivery, 2026-10-06): a
        LISTENER-owned track's firing goes into the EVENTS POOL instead --
        the listener receives its own watch's crossing as its next turn's
        message (the thinker's event-input flow); the pool's drop-oldest
        cap is the bound. The banner ALERTs never carry a listener's
        firing.

        ``count_fn(key)`` = the entity's live
        mention count (the entity field passes its own; None counts 0 --
        which un-arms every bounded track until a count source is wired).
        Count-only tracks never alert. Persistence only when something
        actually changed."""
        alerts: list[dict] = []
        bounded = {k: t for k, t in list(self.tracks.items())
                   if t.get("enter") is not None or t.get("exit") is not None}
        if not bounded:
            return alerts
        changed = False
        pool = getattr(self, "_events_pool", None)
        for key, t in bounded.items():
            n = count_fn(key) if count_fn is not None else 0
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
                if str(t.get("owner") or "operator") != "operator" \
                        and pool is not None:
                    # THE LISTENER'S MESSAGE: the crossing IS the event
                    pool.put({"kind": "boundary", "entity": key,
                              "owner": str(t.get("owner")),
                              "entered": inside, "count": n,
                              "boundary": a["boundary"],
                              "detail": reason, "ts": _t.time()})
                else:
                    alerts.append(a)
                    pend = t.setdefault("pending", [])
                    pend.append(a)
                    del pend[:-8]             # bounded queue
        if changed:
            self._save_tracks()
        return alerts

    def take_alerts(self) -> list[dict]:
        """Drain every track's pending ALERTS for banner delivery (the
        transition state itself is untouched -- re-firing needs a new
        transition). Exactly-once: the pending queue is the delivery
        ledger."""
        out: list[dict] = []
        for t in list(self.tracks.values()):
            pend = t.get("pending") or []
            if pend:
                out.extend(pend)
                t["pending"] = []
        if out:
            self._save_tracks()
        return out

    def unwatch_track(self, spec: str) -> int:
        """Remove tracks by key or kind prefix (e.g. 'files:router.py' or
        'files:' removes all file tracks)."""
        spec = spec.strip().lower()
        hits = [k for k in list(self.tracks) if k == spec or k.startswith(spec)]
        for k in hits:
            del self.tracks[k]
        if hits:
            # THE DETACH EVENT (the watch covenant)
            try:
                from . import watch_agents as _wa
                pool = getattr(self, "_events_pool", None)
                if pool is not None:
                    _wa.notify_watch_detached(self, pool, hits, lane="track")
            except Exception:
                pass
            self._save_tracks()
        return len(hits)

    def list_tracks(self, owner: str = "") -> list[dict]:
        """The management listing (the LLM's view of its own banner): key,
        label, boundary, current inside/outside state, alert count.
        LANE-AWARE (2026-10-06): `owner='watch:<eid>'` returns that
        listener's own tracks; '' = all (the operator's view, now with
        each track's owner)."""
        out = []
        for key, t in self.tracks.items():
            t_owner = str(t.get("owner") or "operator")
            if owner and t_owner != owner:
                continue
            rec = {"key": key,
                   "label": t.get("label") or key,
                   "owner": t_owner}
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
            d = self._mirror
            if d is None:
                d = json.loads(self.path.read_text()) if self.path.exists() \
                    else {}
                self._mirror = d
            d["tracks"] = self.tracks
            self.path.write_text(json.dumps(d))
        except Exception as e:
            print(f"[entities] track save failed ({e})", file=sys.stderr)

    def _load_tracks(self) -> None:
        if self._mirror is not None:
            for k, t in (self._mirror.get("tracks") or {}).items():
                self.tracks[k] = t
            return
        if not self.path or not self.path.exists():
            return
        try:
            d = json.loads(self.path.read_text())
            for k, t in (d.get("tracks") or {}).items():
                self.tracks[k] = t
        except Exception:
            pass

    def _announce(self, kind: str, ent: QuantumEntity,
                  extra: str = "") -> None:
        """Queue one entity event for the banner (bounded, in-memory).
        Only GRADUATIONS announce: new store docs and merges.
        properties['silent_birth'] suppresses the birth announcement
        (the materialization quality bar: generic symbols graduate
        silently -- the doc exists, the banner isn't told)."""
        if kind == "new" and (ent.properties or {}).get("silent_birth"):
            return
        a = {"kind": kind, "eid": ent.eid,
             "cls": ent.cls,
             "name": str((ent.properties or {}).get("name")
                         or ent.eid.rsplit(":", 1)[-1]),
             "summary": str((ent.properties or {}).get("summary")
                            or "")[:140]}
        if extra:
            a["extra"] = extra
        self.announcements.append(a)
        del self.announcements[:-8]           # bounded

    def take_announcements(self, max_per_pass: int = 3) -> list[dict]:
        """Drain up to `max_per_pass` pending entity announcements,
        exactly-once (the queue is the delivery ledger). The per-pass
        cap smooths a night-synthesis flood into a trickle across turns
        (the 8-at-once exhibit: 8 lines drowned the tracks in one
        header)."""
        out = self.announcements[:max_per_pass]
        del self.announcements[:max_per_pass]
        return out

    def _poorest_track(self, incoming_key: str | None = None) -> str | None:
        """The eviction candidate: the OLDEST PLANTED non-default track.
        (The exemplar-count pool is gone with the exemplar field; the
        age-based default is the story-poorest proxy that costs nothing.)"""
        return min((k for k in self.tracks
                    if k != DEFAULT_TRACK_KEY and k != incoming_key),
                   key=lambda k: self.tracks[k].get("planted_ts", 0.0),
                   default=None)

    def _enforce_cap(self) -> list[str]:
        """THE CAP, enforced wherever the roster stands: the
        oldest-planted track evicted down to MAX_TRACKS; the default
        now-track immune. Called at boot and at plant time. Returns the
        evicted keys."""
        evicted: list[str] = []
        over = len(self.tracks) - MAX_TRACKS
        if over <= 0:
            return evicted
        for _ in range(over):
            poorest = self._poorest_track()
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
        evicted: list[str] = []
        over = len(self.tracks) - MAX_TRACKS + 1
        if over <= 0:
            return evicted
        for _ in range(over):
            poorest = self._poorest_track(incoming_key=incoming_key)
            if poorest is None:
                break
            del self.tracks[poorest]
            evicted.append(poorest)
        return evicted

    def ensure_track_entity(self, key: str) -> QuantumEntity:
        """THE TRACK-ENTITY GUARANTEE (the 'entities.py has no doc' fix):
        every planted track gets a store doc immediately -- created on
        demand if neither materialization nor entity_add made one (the
        bootstrap passes materialize=0, so doc-less tracks were skipped
        forever). Idempotent: an existing doc (either eid form) is
        returned untouched. The bootstrap then consolidates the key to
        seed the ring (this method does NOT consolidate -- it has no
        field access; the caller does). The birth is SILENT: the model
        planted the track; the doc is a mechanical consequence."""
        ent = self._entity_for_track(key)
        if ent is not None:
            return ent
        kind, _, val = key.partition(":")
        ent = QuantumEntity(
            eid=f"{MEMBRANE_NS}{key}",
            cls=_KIND_CLASS.get(kind, CLASS_COMPONENT),
            properties={"name": val, "tracked_by": "track-plant",
                        "silent_birth": True},
            derived_by=["track-bootstrap"])
        self.upsert(ent)
        return self.docs.get(ent.eid) or ent

    def _entity_for_track(self, key: str) -> QuantumEntity | None:
        """THE TRACK-KEY LOOKUP (the dual-eid fix): entity docs exist in
        TWO eid forms -- materialization gives 'membrane::files:x.py'
        (kind:key) while model-side entity_add gives
        'membrane::Component:files:x.py' (class-embedded). The track's
        geometry lookup must find BOTH, or scripted/geometry tracks
        silently fall back to cell recitation. Exact first, then
        suffix match (the class-prefixed form ends with ':<key>')."""
        direct = self.docs.get(f"{MEMBRANE_NS}{key}")
        if direct is not None:
            return direct
        # any spelling of a stored entity (m:component:decision, f:x for
        # files:x): the boot minted twins like membrane::m:component:decision
        hit = self.resolve(key, exact=True) or self.by_field_id(key)
        if hit is not None:
            return hit
        suffix = ":" + key.lower()
        for q in list(self.docs.values()):
            if q.eid.lower().endswith(suffix):
                return q
        return None

POSITION_HALF_LIFE_S = 24 * 3600.0   # default drift half-life (24h)
RING_MAX = 16                        # history ring cap (entries)
GRAIN_DEFAULT = 0.05                 # delta-perception threshold (cosine)
# the now-subject's kind weights (the 'subject reads tools, not topics'
# fix): the session's subject lives in files/symbols; shell verbs and
# model names tag aggressively and dominate recent mass
_GRAIN_DEFAULTS = {
    CLASS_SESSION: 0.03,             # a session's dynamics move fast
    CLASS_FAILURE: 0.03,             # failure modes need fine tracking
    CLASS_GOAL: 0.08,                # goals drift slowly by nature
    CLASS_COMPONENT: 0.05,
    CLASS_MODEL: 0.08,
}

# THE FILE GRAIN MARGIN: a symbol must OUTSCORE the best file by this
# much to lead the subject at all when a file is near (the near-tie
# cede). 0.03 = one grain step: inside it, same-grain mush.

# ---- programmable entities (the script constants live HERE -- entities.py
# owns the entity contract; night.py imports them, no circular import) ----
SCRIPT_MAX_NUDGE = 2048        # nudge script size cap (chars)
SCRIPT_MAX_DELETE = 1024       # delete script size cap

# ---- the junk-cut knob (per-track, live, anchor-vector screen) -------------
# The regex screens (_machinery_junk et al) catch KNOWN junk forms; the
# anchor screen catches UNKNOWN forms of the same SEMANTICS: raw tool
# output embeds near junk-anchor texts ("a process listing table with PID
# USER COMMAND columns"), prose stays far. Anchors live IN the store as
# entities (eid prefix membrane::junk-anchor:) -- the shared couchbase
# substrate ships them to the fork, the model can add more via
# entity_add, and every track carries its own cut (junk_sim):
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
