"""The time-skipper: let a world live offscreen before you arrive.

Warm-up populates a fresh field with PREMISE-GROUNDED situations the gate can
actually use -- not random filler. ONE model call reads the world's premise
(``--system``) and emits a palette of on-world ingredients (locations, actors,
verbs, moods, objects, times); the skipper then composes N plausible situations
from that palette and absorbs each as a pre-completed ``(situation, action)``
pair via ``Membrane.learn_pair`` -- NO per-event model calls, local mxbai
embeddings only. The field becomes the memory of the world *having lived while
you were away*, so when you arrive the cloud is populated and the gate has real
variety to act on.

Why premise-grounded, not random text: ``free_energy`` / ``effective_rank``
measures how SPREAD the situation embeddings are, not WHERE they spread. Random
text spreads them in a region of embedding space no real query ever visits ->
high rank the gate can never replay (low cosine to anything you'd actually say).
Premise-grounded situations spread them in the same region a real wanderer's
queries live -> a real message can land near a warm-up exemplar -> ``act``
(replay, no model call). The variety is USABLE, not just counted; the metric
alone does not show that distinction (it measures quantity of spread, not
relevance of spread).

The stub actions are deliberately lower-fidelity than real model turns -- and
that is a FEATURE: a stub-absorb can be re-asked -> demoted -> contested, which
exercises the full green->red->green gate loop on an already-populated field
(the demo the cold-start empty field could never show).

Degrades gracefully: if the one palette call fails (model not installed / backend
down / unparseable JSON), the skipper falls back to a minimal palette built from
the premise words themselves -- so warm-up never hard-fails and still produces
relevant variety. (That fallback is also the "strictly no model calls" path: a
caller can force it by passing ``model=None``.)
"""
from __future__ import annotations

import json
import random
import sys
import urllib.request

import numpy as np

# The one model call: ask for a structured world palette. Temperature 0.8 for
# ingredient variety; the premise constrains the domain. Output JSON only.
_PALETTE_SYSTEM = (
    "You are a world-building assistant. Given a world premise, produce a JSON "
    "object with six arrays of short noun/verb phrases (2-5 words each), each a "
    "concrete ingredient a visitor could encounter, meet, or do in THIS world: "
    "\"locations\", \"actors\" (who lives there), \"verbs\" (actions/events, base "
    "form), \"moods\" (atmospheres), \"objects\" (things to find), \"times\" (of "
    "day or weather). 10 items per array, all on-theme for the premise. "
    "Output ONLY the JSON object, no prose, no code fences."
)

# Keys we keep from the parsed palette (the categories the templates fill).
_KEYS = ("locations", "actors", "verbs", "moods", "objects", "times")

# Situation templates -- a wanderer's possible turns, grounded in the palette.
# Grammar is loose on purpose: the point is the EMBEDDING REGION (what part of
# the space the situation lands in), not prose quality. The stubs are meant to
# be re-asked and demoted, not read as literature.
_SIT_TEMPLATES = [
    "I head to {loc} {time}, {mood}.",
    "I look for {obj} at {loc}.",
    "I ask {actor} about {obj}.",
    "I wait at {loc} as {actor} {verb}.",
    "I follow {actor} to {loc}, {mood}.",
    "I watch {actor} {verb} {obj} at {loc}.",
    "{time}, I arrive at {loc}; {actor} is {verb}.",
    "I seek {obj} near {loc}, {mood}.",
    "I rest at {loc}; {actor} {verb} nearby, {mood}.",
    "I trace {obj} to {loc} {time}.",
]

# Stub actions -- the world's brief narration of what happens (lower-fidelity,
# replayable). On a near-repeat the gate ``act``s and replays one of these;
# re-asking it demotes the slot (the demo loop).
_ACT_TEMPLATES = [
    "{loc} settles into {mood}; {actor} {verb} {obj}.",
    "{actor} {verb}, {mood}, at {loc}.",
    "{time} at {loc}: {obj}, and {actor} {verb}.",
    "The {mood} deepens at {loc}; {actor} {verb}.",
    "{actor} {verb} {obj} and the {mood} holds.",
    "{time}; {loc} is {mood}; {actor} {verb}.",
]


def _parse_palette(text: str) -> dict | None:
    """Robustly extract the JSON object from the model's reply.

    Strips ```json fences, finds the outermost {...}, and keeps only the six
    category arrays (coerced to non-empty string lists). Returns None if no
    usable object is found."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):                       # strip code fences
        for tok in s.split("```"):
            tok = tok.strip()
            if tok.lower().startswith("json"):
                tok = tok[4:].strip()
            if tok.startswith("{"):
                s = tok
                break
    lo, hi = s.find("{"), s.rfind("}")
    if lo < 0 or hi <= lo:
        return None
    try:
        obj = json.loads(s[lo:hi + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    out = {}
    for k in _KEYS:
        v = obj.get(k)
        if isinstance(v, list) and v:
            cleaned = [str(x).strip() for x in v if str(x).strip()]
            if cleaned:
                out[k] = cleaned[:20]
    return out or None


def generate_palette(backend: str, model: str | None, premise: str,
                     timeout: int = 120) -> dict | None:
    """ONE model call: read the premise -> a JSON palette of on-world ingredients.

    Returns a dict (categories -> phrase lists) or None on any failure (network
    error, unparseable JSON, model=None). The caller falls back to a
    premise-derived palette -- warm-up never hard-fails."""
    if not model or not backend or not premise:
        return None
    body = {"model": model, "stream": False, "temperature": 0.8,
            "messages": [{"role": "system", "content": _PALETTE_SYSTEM},
                         {"role": "user", "content": premise}]}
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(backend + "/api/chat", data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
    except Exception as e:                            # any failure -> fallback
        print(f"[warmup] palette call failed ({e}); using premise-words fallback",
              file=sys.stderr)
        return None
    txt = ""
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict):
            c = msg.get("content")
            if isinstance(c, str):
                txt = c
    return _parse_palette(txt)


def _fallback_palette(premise: str) -> dict:
    """Build a minimal palette from the premise words themselves (no model call).

    Reuses premise words across the noun categories so the templates still fill,
    and supplies a small fixed vocabulary for verbs/moods/times. This is the
    graceful-degradation path AND the strictly-no-model-calls path."""
    words = [w.strip(".,;:!?\"'()") for w in premise.split()
             if len(w.strip(".,;:!?\"'()")) > 3]
    if not words:
        words = ["the place", "someone", "something", "the road"]
    return {
        "locations": (words[:6] * 2)[:10],
        "actors": (words[1:5] * 2)[:10] or ["someone"],
        "verbs": ["waits", "moves", "watches", "gathers", "lingers",
                  "returns", "speaks", "looks up"],
        "moods": ["unease", "wonder", "stillness", "dread", "warmth",
                  "expectation", "silence", "awe"],
        "objects": (words[2:6] * 2)[:10] or ["something"],
        "times": ["at dawn", "at dusk", "by night", "in the rain",
                  "at noon", "before the storm", "as the light fades"],
    }


def build_palette(backend: str | None, model: str | None, premise: str):
    """ONE model call (or the premise-words fallback) -> a COMPLETE category->list
    palette: every ``_KEYS`` category backfilled so every template fills. Returns
    ``(palette, src)`` where src is ``'model'`` | ``'premise-fallback'``.

    Factored out of ``run_warmup`` so the proxy can build the palette ONCE (the one
    warm-up model call -- the only place the LLM is used) and cache it. The
    no-model ``/live`` loop then reuses the cached palette indefinitely: the world
    keeps living offscreen with zero LLM calls, the way the stripped (self-author)
    world requires. Warm-up never hard-fails -- a missing model/backend/premise or a
    network/parse error falls back to premise words (``src='premise-fallback'``)."""
    palette = generate_palette(backend, model, premise)
    if palette and len(palette) >= 3:
        src = "model"
    else:
        palette = _fallback_palette(premise)
        src = "premise-fallback"
    fb = _fallback_palette(premise)
    for k in _KEYS:
        if k not in palette or not palette.get(k):
            palette[k] = fb[k]
    return palette, src


def _fill(tmpl: str, p: dict, rng: random.Random) -> str | None:
    """Fill a template by sampling palette ingredients. Returns None if any
    needed category is missing (caller retries with another template)."""
    slots = {
        "loc": rng.choice(p["locations"]),
        "actor": rng.choice(p["actors"]),
        "verb": rng.choice(p["verbs"]),
        "mood": rng.choice(p["moods"]),
        "obj": rng.choice(p["objects"]),
        "time": rng.choice(p["times"]),
    }
    try:
        return tmpl.format(**slots)
    except (KeyError, IndexError):
        return None


def compose_pair(palette: dict, rng: random.Random) -> tuple[str | None, str | None]:
    """One ``(situation, action)`` pair from the palette, or ``(None, None)`` if
    the palette is too sparse for a given template (caller loops)."""
    sit = _fill(rng.choice(_SIT_TEMPLATES), palette, rng)
    act = _fill(rng.choice(_ACT_TEMPLATES), palette, rng)
    return sit, act


# ---- curriculum: low complexity -> high, building act-able clusters first --------
# The live worlds (Aethoria, told) LEARNED but never ACTED: every turn escalated,
# zero absorbs. The reason: the gate needs CONSENSUS (>= ``consensus_min`` of the
# top-k neighbors sharing the nearest's action) to act, and a uniform/diverse
# warm-up fills the field with unique exemplars -- no cluster -> no consensus ->
# no act. A curriculum fixes it by front-loading the one structure the gate needs:
# tight situation rings that share an action. Then it raises complexity to spread
# ``free_energy`` -- train the way you train anything, easy (clustered) -> hard
# (spread), not because it is easy.
_SLOT_NAMES = ("loc", "actor", "verb", "mood", "obj", "time")
# templates use the short slot names ({loc}, {actor}, ...); palettes store under
# the long category keys (locations, actors, ...). _fill maps long->short inline;
# _cluster_seed indexes the palette directly, so it needs the short->long map.
_SLOT_KEY = {"loc": "locations", "actor": "actors", "verb": "verbs",
             "mood": "moods", "obj": "objects", "time": "times"}


def _template_slots(tmpl: str) -> list[str]:
    """Which palette slots a template actually references."""
    return [s for s in _SLOT_NAMES if "{" + s + "}" in tmpl]


def _fill_slots(tmpl: str, slots: dict) -> str | None:
    """Fill a template from an explicit slot dict (vs ``_fill``'s rng-all)."""
    try:
        return tmpl.format(**slots)
    except (KeyError, IndexError):
        return None


def _narrow(palette: dict, breadth: float) -> dict:
    """Slice each palette category to a fraction (min 2). A narrow palette keeps
    warm-up situations premise-grounded AND tightly clustered."""
    return {k: v[: max(2, int(len(v) * breadth))] for k, v in palette.items()}


def _cos(a, b) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def _build_cluster(sit_tpl: str, palette: dict, rng: random.Random,
                   embedder, nudge, reps: int = 6,
                   lo: float = 0.85, hi: float = 0.948, tries: int = 4
                   ) -> list[tuple[str, str]]:
    """One act-able cluster, BUILT BY MEASUREMENT not guess.

    mxbai's per-slot similarity is too noisy to predict which variation lands in
    the act zone: ``verb`` collapses to >= 0.95 (deduped to nothing), ``time``
    spreads 0.73-0.91 (weak members get displaced by filler), and a given base
    may simply yield few in-zone candidates. So instead of guessing a slot, we
    POOL candidates across every non-verb slot, EMBED them (local mxbai, no chat
    model), and keep those whose cosine to the seed lands in the act zone
    ``[lo, hi]`` -- above ``sim_min`` (act-able) and below ``dedup_sim`` (``_learn``
    keeps each rather than collapsing them). We try several seed bases and keep
    the first ring that reaches a usable size; one unlucky base never sinks it.
    All members share ONE action -> on a near-repeat the top-k neighbors are this
    ring, unanimous -> consensus -> ``act`` (replay, no model call). This is the
    structure a uniform/diverse warm-up never builds -> the live worlds learned
    but never acted."""
    slots_needed = _template_slots(sit_tpl)
    if not slots_needed:
        return []
    vary_slots = [s for s in slots_needed if s != "verb"] or slots_needed
    best_ring = []
    for _ in range(tries):
        base = {s: rng.choice(palette[_SLOT_KEY[s]]) for s in slots_needed}
        seed = _fill_slots(sit_tpl, base)
        if not seed:
            continue
        action = _fill(rng.choice(_ACT_TEMPLATES), palette, rng)   # ONE action for the ring
        if not action:
            continue
        sv = nudge(embedder.embed([seed])[0])
        # pool in-zone candidates across every non-verb vary-slot
        cands = []
        for vary in vary_slots:
            for v in palette[_SLOT_KEY[vary]]:
                if v == base.get(vary):
                    continue
                s = dict(base)
                s[vary] = v
                t = _fill_slots(sit_tpl, s)
                if t and t != seed:
                    cands.append(t)
        if not cands:
            continue
        cvecs = [nudge(x) for x in embedder.embed(cands)]
        scored = [(c, t) for t, cv in zip(cands, cvecs)
                  if lo <= (c := _cos(sv, cv)) <= hi]
        scored.sort(reverse=True)                 # tightest (highest sim) first
        ring = [(seed, action)] + [(t, action) for _, t in scored[:max(0, reps - 1)]]
        if len(ring) >= min(reps, 4):
            return ring
        if len(ring) > len(best_ring):
            best_ring = ring
    return best_ring


def _clear_reask_ring(membrane) -> None:
    """Drop the re-ask ring after warm-up.

    The re-ask ring (``Membrane._recent``) models a LIVE conversation's recent
    turns -- "the user just sent this and is now re-sending it" -> the replay was
    wrong -> demote. Warm-up events are OFFSCREEN pre-arrival memory, not live
    turns: when a wanderer arrives they are in the past (outside the re-ask
    window), so a first message that lands near a warm-up cluster should ACT
    (replay), not be mistaken for a re-ask of an offscreen filler. Clearing the
    ring at the end of warm-up restores that -- the next live turn repopulates it.
    (Also fixes the self-inflicted case where phase-2/3 filler, drawn from the
    same palette as a phase-1 ring, lands >= reask_sim to it and would force a
    spurious re-ask on the first real message.)"""
    try:
        membrane._recent.clear()
    except AttributeError:
        pass


def run_warmup(membrane, backend: str, model: str | None, premise: str, n: int,
              seed: int = 0, curriculum: bool = True,
              palette: dict | None = None) -> dict:
    """Populate the field with ``n`` premise-grounded situations.

    ONE model call builds the palette (or the premise-words fallback), then
    ``n`` ``Membrane.learn_pair`` calls absorb ``(situation, stub-action)`` pairs
    -- no per-event model calls, local mxbai embeddings only. The gate path is
    untouched (``learn_pair`` is the existing teed-intake seam). Returns stats.

    ``palette`` (optional): a pre-built category->phrase-list dict. If given,
    the model palette call is SKIPPED entirely -- the caller owns the
    ingredients. This is how the random-text experiment runs: the field is seeded
    from a random-word palette (no narrative premise, no authorship, no model
    call), to see whether a "world" emerges inside the field unannounced. The
    warm-up's own docstring argues AGAINST random text (it spreads the field in
    a region no real query visits -> the gate never acts); this path lets the
    caller test that claim deliberately.

    ``curriculum`` (default) ramps low -> high complexity, the way you train:
    build act-able CLUSTERS first, then spread variety. Phase 1 seeds tight
    one-slot-varied situation rings that share an action -- the consensus
    structure the gate needs to ``act`` (which a uniform/diverse warm-up starves
    -> the live worlds learned but never acted). Phase 2 widens the palette and
    uses mid-complexity templates; phase 3 goes full-complexity on the whole
    palette to grow ``free_energy`` / effective rank. ``curriculum=False`` keeps
    the original uniform-complexity path."""
    rng = random.Random(seed)
    if palette:
        src = "provided"
        fb = _fallback_palette(premise or " ")
        for k in _KEYS:
            if k not in palette or not palette[k]:
                palette[k] = fb[k]
    else:
        palette, src = build_palette(backend, model, premise)
    cats = sorted(palette)
    sizes = ", ".join(f"{k}={len(palette[k])}" for k in cats)
    sched = "curriculum" if curriculum else "uniform"
    print(f"[warmup] palette source={src} categories={cats} sizes={{{sizes}}}"
          f"  -> {n} offscreen events ({sched})", file=sys.stderr)

    start = len(membrane.store)

    def _absorb(sit, act):
        if sit and act:
            membrane.learn_pair(sit, act)

    if not curriculum:
        for i in range(n):
            sit = act = None
            for _ in range(4):
                sit, act = compose_pair(palette, rng)
                if sit and act:
                    break
            _absorb(sit, act)
            if (i + 1) % 10 == 0:
                print(f"[warmup] {i + 1}/{n}  field={len(membrane.store)}",
                      file=sys.stderr)
        added = len(membrane.store) - start
        print(f"[warmup] done: added={added}/{n}  field_size={len(membrane.store)}",
              file=sys.stderr)
        _clear_reask_ring(membrane)
        return {"added": added, "requested": n, "palette_source": src,
                "field_size": len(membrane.store)}

    # ---- curriculum schedule --------------------------------------------------
    n1 = max(3, round(n * 0.25))          # phase 1: act-able clusters
    n2 = round(n * 0.35)                    # phase 2: widen palette + mid templates
    n3 = max(0, n - n1 - n2)               # phase 3: full spread
    simple = [t for t in _SIT_TEMPLATES if len(_template_slots(t)) <= 3]

    # phase 1 -- cluster seeds, BUILT BY MEASUREMENT: vary each non-verb slot,
    # embed candidates (local mxbai, no chat model), keep those in the tight
    # act-zone band [0.86, 0.94] to a seed -- dedup-safe yet act-able and tight
    # enough to dominate the top-k. All members share ONE action -> consensus.
    embedder, nudge = membrane.embedder, membrane._nudge
    n_seeds = min(4, max(1, n1 // 8))
    reps = max(5, n1 // max(1, n_seeds))
    ccount = 0
    rings = 0
    for _ in range(n_seeds):
        ring = _build_cluster(rng.choice(simple), palette, rng, embedder, nudge, reps)
        if len(ring) < 3:                            # too thin to reach consensus
            continue
        rings += 1
        for sit, act in ring:
            _absorb(sit, act)
            ccount += 1
            if ccount >= n1:
                break
        if ccount >= n1:
            break
    print(f"[warmup] phase1 clusters: {rings} rings x ~{reps}  absorbed={ccount}  "
          f"field={len(membrane.store)}", file=sys.stderr)

    # phase 2 -- ramp palette breadth 0.4->0.7, mid-complexity unique pairs
    for i in range(n2):
        p = _narrow(palette, 0.4 + 0.3 * (i / max(1, n2)))
        sit = _fill(rng.choice(simple), p, rng)
        act = _fill(rng.choice(_ACT_TEMPLATES), p, rng)
        _absorb(sit, act)
        if (n1 + i + 1) % 10 == 0:
            print(f"[warmup] phase2 {i + 1}/{n2}  field={len(membrane.store)}",
                  file=sys.stderr)

    # phase 3 -- full palette, all templates (incl 4-slot): maximize spread
    for i in range(n3):
        sit = _fill(rng.choice(_SIT_TEMPLATES), palette, rng)
        act = _fill(rng.choice(_ACT_TEMPLATES), palette, rng)
        _absorb(sit, act)
        if (n1 + n2 + i + 1) % 10 == 0:
            print(f"[warmup] phase3 {i + 1}/{n3}  field={len(membrane.store)}",
                  file=sys.stderr)

    added = len(membrane.store) - start
    print(f"[warmup] done: added={added}/{n}  field={len(membrane.store)}",
          file=sys.stderr)
    _clear_reask_ring(membrane)
    return {"added": added, "requested": n, "palette_source": src,
            "field_size": len(membrane.store), "curriculum": True}