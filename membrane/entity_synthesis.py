"""Background entity synthesis: the idle cortex derives ABSTRACT entities
(decisions, failure modes, components, goals) from compacted clusters -- the
cortex-experiment cron-job pattern (extract_autonomous_entities) at the
compactor's 90s-idle cadence instead of nightly.

DESIGN:
- rides the Compactor's cycle: right after a distillation commits, the SAME
  cluster (members + the fresh note) goes to the entity synthesizer -- one
  idle-cortex call covers a whole topic group, zero chat-turn cost.
- the LLM returns a JSON array of {class, name, summary}; we VALIDATE against
  the shared class vocabulary (entities.CLASSES) and clip -- a malformed
  reply degrades to no entities (never breaks the compaction).
- derived entities are merged into the deterministic tags' side-index via
  QuantumEntity docs (write-through, see opencode.py's wiring) with
  derived_by: ["membrane-idle"] so operator/manual entities stay distinct.
"""

from __future__ import annotations

import json

from .entities import CLASSES, CLASS_COMPONENT, QuantumEntity

_ENTITY_SYSTEM = (
    "You derive ENTITIES from compacted memory exchanges. The user message "
    "holds a topic plus Q/A exchanges. Return a JSON array (max 5 items) of "
    "discrete durable entities: reusable components, settled decisions, "
    "failure modes, tools, models, goals. Each item: {\"class\": one of "
    f"{', '.join(CLASSES)}, \"name\": short id (a file, symbol, model name, "
    "or a kebab-case name), \"summary\": one sentence of the durable fact}. "
    "Only entities the exchanges actually EVIDENCE; no guesses, no persons, "
    "no transient state. Write only the JSON array.")

# validated class set for fast membership
_CLASS_SET = frozenset(CLASSES)


def parse_entity_reply(text: str) -> list[dict]:
    """The LLM's JSON array -> validated {class, name, summary} dicts.

    Tolerant: a reply with prose around the JSON still parses (first [
    .. last ] slice); items missing name/class are dropped; unknown classes
    map to Component (the safest general class). Bounded to 5."""
    if not text:
        return []
    try:
        lo = text.index("[")
        hi = text.rindex("]")
        items = json.loads(text[lo:hi + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    if isinstance(items, dict):
        items = [items]
    out: list[dict] = []
    for it in (items or [])[:8]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not (1 < len(name) <= 80):
            continue
        cls = str(it.get("class") or CLASS_COMPONENT).strip()
        if cls not in _CLASS_SET:
            cls = CLASS_COMPONENT
        summary = str(it.get("summary") or "").strip()[:400]
        out.append({"class": cls, "name": name, "summary": summary})
    return out[:5]


def synthesize_from_cluster(backend: str, model: str,
                            members_situations: list[str],
                            note: str, topic: str,
                            chat, members_born: list | None = None
                            ) -> list[QuantumEntity]:
    """One idle-cortex call over a compacted cluster -> QuantumEntities.

    ``chat`` is the module-level _ollama_chat (injected so this module stays
    test-friendly); members_situations are the raw situation texts of the
    cluster (already clipped by the caller), ``note`` the compact note just
    committed. ``members_born``: the source cells' birth timestamps (the
    speech acts' times -- the symbol-birth rule: born = the earliest
    utterance that crystallized the symbol, not the synthesis time).
    Returns [] on any failure (degrade, never raise)."""
    import time as _t
    born = None
    for b in (members_born or []):
        try:
            b = float(b)
        except (TypeError, ValueError):
            continue
        if b > 0 and (born is None or b < born):
            born = b
    if born is None:
        born = _t.time()
    parts = "\n----\n".join(s[:200] for s in members_situations[:8])
    user = (f"Topic: {topic[:120]}\n\nExchanges:\n\n{parts}\n\n"
            f"Compact note: {note[:600]}\n\nDerive entities as a JSON array.")
    try:
        reply = chat(backend, model, [
            {"role": "system", "content": _ENTITY_SYSTEM},
            {"role": "user", "content": user}])
    except Exception:
        return []
    items = parse_entity_reply(reply)
    ents: list[QuantumEntity] = []
    for it in items:
        cls = it["class"]
        if cls not in _CLASS_SET:
            cls = CLASS_COMPONENT
        # THE SYMBOL-BIRTH RULE (the operator's thesis: symbols come into
        # being through the sounds we make -- born is the speech act's
        # timestamp): the synth entity traces to the utterances that
        # crystallized it -- born = the EARLIEST source situation's cell
        # birth (the speech acts' time, not the synthesis time), and the
        # crystallized-from texts ride the properties (the symbol's
        # grounding: which lived turns earned this symbol).
        e = QuantumEntity(
            eid=f"membrane::synth:{it['name'].strip().lower().replace(' ', '-')[:80]}",
            cls=cls,
            properties={"name": it["name"], "summary": it["summary"],
                        "topic": topic[:80],
                        "crystallized_from": [s[:160] for s in
                                              members_situations[:8]],
                        "born": born,
                        },
            derived_by=["membrane-idle"],
        )
        ents.append(e)
    return ents