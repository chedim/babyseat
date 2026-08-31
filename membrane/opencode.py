"""opencode-on-membrane: `python -m membrane.opencode`.

Starts the RAG proxy (membrane field + background compactor, from
membrane/rag.py), extends it with the OpenAI-compatible surface opencode
speaks, and launches opencode pointed at it:

    you (opencode) --> membrane (field: RAG + state) --> Ollama

What is INTERNAL vs PROXIED (the split this entrypoint implements):
  * INTERNAL -- everything about the membrane's own math and state:
      /v1/models       built here (Ollama's tag list, embed models filtered)
      /v1/embeddings   served by the field's own embedder
      /membrane/state  the JSON instrument (thermo, gate, compactor, audit)
      chat turns that ask about the system (field size, effective rank,
      free energy, compactor, ...) are ANSWERED HERE from the live field --
      computed, deterministic, zero model calls.
  * PROXIED -- everything model-shaped: /v1/chat/completions (grounded by the
    field's RAG injection, learned back into the field every turn) and any
    other path.

The model selected in opencode is the CORTEX's model too: each chat request
stamps `srv.compact_model`, so the background compactor distills with the same
model the user picked (unless --compact-model pins one). Embeddings are the
one place the selected model may not qualify: the resolver tries
--embed-model > a known Ollama embed model > the selected model, and if
nothing can embed, every chat turn returns 503 (err and block -- the field
cannot run without vectors).

Run:
  PYTHONPATH=. .venv/bin/python -m membrane.opencode
  # -> the proxy listens on 127.0.0.1:11500 and opencode opens pointed at it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np

from .embed import OllamaEmbedder
from .rag import Compactor, RAGProxyHandler, _clip, _inject
from .runtime import Membrane, OllamaChatResponder, OllamaJudge, StubJudge
from .variety import thermo_state

# tag markers meaning "not a chat model" (kept out of /v1/models). Cloud models
# (e.g. glm-4.7:cloud, deepseek-v4-pro:cloud, gpt-oss:20b-cloud) never match these
# markers, so they always pass.
_NON_CHAT_MARKERS = ("embed", "minilm", "bge", "whisper", "nomic-embed")
# tag markers meaning "an embedding model" (the embed-role candidates)
_EMBED_MARKERS = ("embed", "minilm", "bge", "arctic", "gte")


def _is_non_chat(name: str) -> bool:
    low = (name or "").lower()
    return any(m in low for m in _NON_CHAT_MARKERS)


# ---- the state/math intercept (internal, no model) ---------------------------
# Phrases that only a question ABOUT the membrane's own math/state uses. No bare
# single words ("membrane" appears in nearly every coding turn in this repo).
_STATE_STRONG = ("field state", "field stats", "system state",
                 "state of the system", "state of the field",
                 "free energy", "effective rank", "eff rank", "eff_rank",
                 "rank ratio", "safe frac", "spectrum entropy",
                 "what do you remember", "memory stats", "audit stats",
                 "how many exemplars", "field size")
_STATE_QSTARTS = ("what", "how", "show", "tell", "give", "print", "report",
                  "dump", "status")
_STATE_EXACT = ("status", "state", "field state", "field stats", "stats",
                "memory", "membrane state", "membrane")
# whole-turn asks that are exactly about the membrane itself. "membrane" merely
# MENTIONED in a longer sentence is never enough -- in this repo nearly every
# turn says "membrane", so a substring rule would hijack normal questions.
_MEMBRANE_DIRECT = ("the membrane", "what is the membrane",
                    "what's the membrane", "whats the membrane",
                    "how is your memory", "how's your memory",
                    "hows your memory")


def _is_state_query(q: str) -> bool:
    """Conservative: the turn asks ABOUT the membrane itself (its math/state,
    not the codebase) -> answered internally from the live field. Guards:
    question-shaped (a '?' or a question/imperative opener) except for bare
    exact asks ("status", "field state"), no code fence, short (a state ask is
    short) -- so ordinary coding prose never trips it."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    bare = low.rstrip("?!").strip()
    if bare in _STATE_EXACT or bare in _MEMBRANE_DIRECT:
        return True
    if not ("?" in low or low.startswith(_STATE_QSTARTS)):
        return False
    return any(m in low for m in _STATE_STRONG)


def _state_report(srv) -> str:
    """The internal answer for a state/math query: live numbers straight off
    the field, the compactor, and the audit. No model call."""
    mem = srv.membrane
    exs = list(mem.store.exemplars)
    n = len(exs)
    safe = sum(1 for e in exs if e.safe)
    retired = sum(1 for e in exs
                  if (getattr(e, "outcome", "") or "").startswith("compacted@"))
    notes = sum(1 for e in exs if getattr(e, "outcome", "") == "compact-note")
    th = thermo_state(mem.store)
    comp = (srv.compactor.stats if srv.compactor
            else {"cycles": 0, "clusters": 0, "notes": 0})
    lines = [
        "Membrane state (computed live on this server, no model call):",
        f"- backend {srv.backend} | embed model {getattr(mem.embedder, 'model', None) or 'unresolved'}",
        f"- field: {n} exemplars ({safe} safe / {n - safe} unsafe; "
        f"{retired} compacted, {notes} compact notes)",
        f"- effective rank {th['eff_rank']:.2f} (rank ratio {th['rank_ratio']:.2f}); "
        f"free energy {th['free_energy']:.2f} (safe_frac {th['safe_frac']:.2f}, "
        f"spectrum entropy {th['spectrum_entropy']:.2f})",
        f"- gate: sim_min={mem.sim_min} dedup={mem.dedup_sim} "
        f"reask_window={mem.reask_window_s:.0f}s demote_threshold={mem.demote_threshold}",
        f"- retrieval: k={srv.rag_k}, sim>={srv.rag_sim_min}, "
        f"budget {srv.rag_ctx_chars} chars",
    ]
    if srv.compactor is not None:
        lines.append(f"- cortex/compactor: cycles={comp['cycles']} "
                     f"clusters={comp['clusters']} notes={comp['notes']}  "
                     f"model={srv.compact_model}  every={srv.compactor.idle:.0f}s")
    else:
        lines.append("- cortex/compactor: disabled")
    lines.append(f"- audit: {_audit_count(mem.audit_path)} records")
    return "\n".join(lines)


# ---- the recall intercept (internal, no model, never learned) ---------------
# Turns that ASK what the field remembers are served straight from the store:
# the topic is embedded, top-k SAFE exemplars come back formatted. The turn is
# intercepted BEFORE the gate -> it never learns, never demotes, never nudges:
# the field cannot eat its own readback (the collapse route). The two skills
# in .opencode/skills/ teach the agent the canonical phrasings; to the agent
# they look like ordinary skills, but the SKILL.md bodies say they are served
# by membrane itself.
_RECALL_CANON = ("membrane: query the field for ",
                 "membrane: search the field for ",
                 "membrane: what do you remember",
                 "membrane: dump your memory")
_RECALL_FRAMES = ("what do you remember about ", "what do you remember of ",
                  "what have you learned about ", "what have we learned about ",
                  "what did you learn about ", "what do you recall about ",
                  "search your memory for ", "search memory for ",
                  "search the field for ", "query the field for ",
                  "query your memory for ", "retrieve memories about ",
                  "retrieve memories of ", "what do you know about ")
_RECALL_EXACT = ("what do you remember", "what do you recall",
                 "what have you learned", "your memory", "memories",
                 "search your memory", "query the field", "search the field",
                 "dump your memory")
_RECALL_DUMP = ("what do you remember", "what do you recall",
                "dump your memory", "show your memory", "list your memory",
                "what's in your memory", "whats in your memory",
                "what is in your memory", "what have you learned")


def _recall_topic(q: str) -> str:
    """Strip the interrogative frame -> the topic text to embed.
    Returns "" when the ask is a plain dump (show everything recent)."""
    t = " ".join((q or "").split()).lower()
    for c in _RECALL_CANON:                    # canonical skill phrasings first
        if c.endswith("for ") and t.startswith(c):
            return t[len(c):].strip().rstrip("?.! ").strip()
    if t.startswith("membrane: "):
        t = t[len("membrane: "):].strip()
    t = t.rstrip("?.!").strip()
    if t in _RECALL_DUMP or t in _RECALL_EXACT:
        return ""
    for f in _RECALL_FRAMES:
        if t.startswith(f):
            t = t[len(f):].strip()
            for pre in ("about ", "of ", "for ", "on "):
                if t.startswith(pre):
                    t = t[len(pre):].strip()
            return t.rstrip("?.!").strip()
    return t


def _is_recall_query(q: str) -> bool:
    """Conservative: the turn asks the FIELD to recall something. Framed asks
    (incl. the canonical skill forms) match on the prefix; bare asks match
    exactly. Same length/fence guards as _is_state_query."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    if any(low.startswith(c) for c in _RECALL_CANON):
        return True
    if any(low.startswith(f) for f in _RECALL_FRAMES):
        return True
    bare = low.rstrip("?!").strip()
    return bare in _RECALL_DUMP


# ---- write/forget intercepts (operator-given memory, pre-gate) ---------------
# The same paradox as recall: these turns are intercepted BEFORE the gate, so
# the field never sees them -- no learn, no dedup, no nudge on the COMMAND
# itself. What is learned is the PAYLOAD (learn), and what is removed is from
# the store directly (forget) -- in both cases with an explicit audit trail so
# the training data records the operator's hand, not a model decision.

_LEARN_CANON = "membrane: learn that "
_LEARN_FRAMES = ("remember that ", "remember: ", "note that ",
                 "memorize that ", "memorise that ")
_FORGET_CANON = "membrane: forget "
_FORGET_FRAMES = ("forget that ", "forget about ", "forget everything about ",
                  "delete the memory of ", "delete your memory of ")
_FORGET_ALL = ("forget everything", "wipe your memory", "erase your memory",
               "delete all memories", "clear your memory")
_FORGET_WINDOW_HINTS = ("last ", "this session", "the session",
                        "that conversation", "this conversation")
_LEARN_TAG = "operator-note"
# soft frames: what a SEMANTICALLY-caught learn/forget usually reads like (the
# payload rides after the intent phrase). Used only for payload extraction --
# detection stays with the lexical matchers + the semantic gate.
_LEARN_SOFT_FRAMES = ("save this to your memory: ", "keep this in mind: ",
                      "note for later: ", "store a note that ",
                      "keep in mind that ", "you should remember that ")
_FORGET_SOFT_FRAMES = ("forget everything about ", "wipe that from your memory: ",
                       "wipe from your memory ", "erase what you learned about ",
                       "delete the memory of ")
_FORGET_LIMIT = 40          # max candidates a single forget may drop

# appended to every write/forget answer so the client can SEE the turn was
# intercepted pre-gate (never learned via the chat path, never demoted)
_INTERCEPT_MARKER = ("[membrane-intercept] operator write; handled internally, "
                     "the command itself was NOT learned. To force the model "
                     "path, repeat prefixed with 'model:'")


def _write_audit(mem, rec: dict) -> None:
    """One raw JSONL record on the audit log -- the operator's hand, outside the
    decision/outcome/contest schema. Never raises into the chat path."""
    try:
        with open(mem.audit_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:
        print(f"[opencode] operator-audit write failed: {_one_line(e)}",
              file=sys.stderr)


def _after_frame(q: str, frames: tuple[str, ...]) -> str:
    t = " ".join(q.split()).lower()
    for f in sorted(frames, key=len, reverse=True):
        if t.startswith(f):
            return t[len(f):].strip().rstrip("?.!").strip()
    return ""


def _is_forget_query(q: str) -> bool:
    """A turn commanded at the FIELD to delete memories (canonical form first).
    Same length/fence guards as the recall matcher. A canonical turn whose
    remainder parses as a time window ('the last hour', 'this session') is a
    forget too."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 400:
        return False
    if low.startswith(_FORGET_CANON):
        return True
    t = low.rstrip("?!").strip()
    if any(t.startswith(f) for f in _FORGET_FRAMES) and "you" in t:
        return True
    return t in _FORGET_ALL


def _is_learn_query(q: str) -> bool:
    """A turn GIVING the field a memory (membrane: learn that X), not a coding
    request. The canonical skill form is exact-prefix (bare 'membrane: learn
    that' with no payload also matches -- _learn_report then reports 'nothing
    to learn'); bare frames require the memory-flavored 'you/your' to stay
    conservative."""
    low = " ".join((q or "").split()).lower()
    if not low or "```" in low or len(low) > 2000:
        return False
    if low.startswith("membrane: learn that"):
        return True
    t = low.rstrip("?.!").strip()
    if any(t.startswith(f) for f in _LEARN_FRAMES) and "you" in t:
        return True
    return False


# ---- human-review surfacing (the judge ESCALATE loop -> the question window) -
# When the demotion judge cannot rule (verdict ESCALATE), the case is parked in
# <field>.review.jsonl (runtime._escalate_to_human). This block is what the
# operator actually SEES: the pending cases are serialized into the RAG system
# context with an instruction to ask the user through opencode's NATIVE
# question tool. The user's pick arrives as the next user turn; the pre-gate
# resolver below matches it (keep/drop/discuss) and closes the case.

_REVIEW_MATCHERS = (
    ("keep", "membrane: keep #"),
    ("drop", "membrane: drop #"),
    ("keep", "membrane: keep the memory"),
    ("drop", "membrane: drop the memory"),
    ("discuss", "membrane: discuss review"),
    ("discuss", "membrane: review discuss"),
)


def _review_request_block(srv) -> str | None:
    """The pending-review notice for the chat context, or None when the queue
    is empty. Capped: at most 3 cases and ~700 chars so the notice never
    crowds out real memories."""
    try:
        pending = srv.membrane.review_pending()
    except Exception:
        return None
    if not pending:
        return None
    lines = ["PENDING HUMAN REVIEWS (membrane's judge declined to rule; the "
             "human is the final authority):"]
    for rec in pending[:3]:
        lines.append(
            f"- review slot {rec['slot']} (exemplar #{rec.get('ex_idx')}): "
            f"Q: {_clip(rec.get('situation', ''), 110)} | judge: "
            f"{_clip(rec.get('judge_reason', ''), 90)}")
    lines.append(
        "Ask the user to rule on these NOW via your question tool (one "
        "question per pending case; use its slot number verbatim). Exact "
        "choices: `Keep this memory` / `Drop this memory` / `Let's discuss`. "
        "When the user answers, send the ruling as your NEXT chat turn "
        "(no tools) phrased exactly `membrane: keep #<slot>`, "
        "`membrane: drop #<slot>`, or `membrane: discuss review #<slot>`. "
        "Do not restate the case text back into chat; just relay the ruling.")
    return "\n".join(lines)


def _match_review_ruling(q: str) -> tuple[str, int] | None:
    """A user turn ruling on a pending review -> (keep|drop|discuss, slot).
    Canonical forms first; then free text the model might relay. Returns None
    when the turn is not a ruling."""
    low = " ".join((q or "").split()).lower().rstrip("?.!")
    for verdict, canon in _REVIEW_MATCHERS:
        if low.startswith(canon):
            tail = low.replace(canon, "", 1).strip().lstrip("# ")
            slot = _slot_of(low) or _slot_of(tail)
            if slot is not None:
                return verdict, slot
    m = _slot_of(low)
    if m is not None:
        # 'keep #N' / 'drop #N' / 'memory #N is right/wrong' freeforms
        if "keep" in low or "correct" in low or "right" in low or "restore" in low:
            return "keep", m
        if "drop" in low or "delete" in low or "remove" in low or "wrong" in low:
            return "drop", m
    return None


def _slot_of(low: str) -> int | None:
    import re
    ms = re.findall(r"#?(\d{5,})", low)      # slots are epoch-based (10 digits)
    return int(ms[0]) if ms else None


def _review_report(srv, verdict: str, slot: int) -> str:
    mem = srv.membrane
    if verdict == "discuss":
        cases = [r for r in mem.review_pending() if r["slot"] == slot]
        if not cases:
            return (f"Membrane review: no pending case with slot {slot}.\n\n"
                    + _INTERCEPT_MARKER)
        c = cases[0]
        return (f"Membrane review case {c['slot']} (exemplar "
                f"#{c.get('ex_idx')}) -- DETAILS FOR THE DISCUSSION:\n"
                f"  Q: {c.get('situation', '')[:400]}\n"
                f"  A: {c.get('action', '')[:400]}\n"
                f"  The user re-asked: {c.get('reask_query', '')[:200]}\n"
                f"  Judge declined because: {c.get('judge_reason', '')}\n"
                f"  Demotion signal: {c.get('signal')} (sim "
                f"{c.get('sim')})\n\n"
                f"Discuss this with the user freely; the case stays pending "
                f"until they rule with 'membrane: keep/drop #{slot}'."
                f"\n\n{_INTERCEPT_MARKER}")
    keep = verdict == "keep"
    rec = mem.resolve_review(slot, keep)
    if rec is None:
        return (f"Membrane review: no pending case with slot {slot} (already "
                f"resolved or unknown).\n\n" + _INTERCEPT_MARKER)
    what = ("KEPT -> contest filed; the memory re-promotes if the judge path "
            "agrees" if keep else
            "DROPPED -> the demotion is confirmed (human-dropped)")
    return (f"Membrane review: human verdict '{verdict}' on case {slot} "
            f"(exemplar #{rec.get('ex_idx')}) recorded. {what}. Case closed."
            f"\n\n{_INTERCEPT_MARKER}")


def _window_seconds(spec: str) -> float | None:
    """Parse a time-window forget spec -> seconds, or None when the spec is not
    a window. Forms: 'the last N minutes/hours/days', 'last 30m', 'this
    session', 'the session', 'that conversation', 'the last hour/session'."""
    t = " ".join(spec.split()).lower().strip()
    if t.startswith("the "):
        t = t[4:]
    if t.startswith("last "):
        t = t[5:]
    t = t.strip()
    if t in ("session", "this session", "whole session", "entire session",
             "conversation", "this conversation", "that conversation",
             "everything from this session"):
        return -1.0                      # sentinel: 'session' = since boot
    import re
    m = re.match(r"^(?:(\d+)\s*)?(seconds?|secs?|s|minutes?|mins?|m|hours?|h|"
                 r"days?|d)\b.*$", t)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1) else 1
    unit = m.group(2)[0]
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return float(n * mult)


def _forget_targets(srv, spec: str) -> list[tuple[int, float]]:
    """Resolve a forget spec against the field: a time window ('last hour',
    'this session'), by direct index ('#12', '12', '3-7'), else embed the text
    and take SAFE exemplars above sim_min. Returns [(index, sim)] sorted by
    index; emptiness = nothing matched. Window and index targets carry no
    similarity (sim 0.0).

    Window resolution is by FIELD SIZE AT BOOT (srv._boot_field_size, stamped
    in main()): the store is append-ordered, so 'this session' = every
    exemplar appended since the proxy booted. Wall-clock windows ('last hour')
    would need per-record timestamps, which the audit log does not carry -- so
    they resolve to the same boot-anchored approximation (conservative: it can
    only under-remove, never over-remove)."""
    exs = srv.membrane.store.exemplars
    spec_l = spec.strip().lower()
    win = _window_seconds(spec_l)
    if win is not None:
        booted = getattr(srv, "_boot_field_size", None)
        if booted is None:
            return []                    # probe/handler-only context: refuse
        return [(i, 0.0) for i in range(booted, len(exs))]
    if not spec_l or spec_l.startswith("everything") or spec_l in _FORGET_ALL:
        return [(i, 0.0) for i in range(len(exs))]
    # ranges/indices first: '#12', '12', '3-7' (comma lists allowed)
    toks = [t.strip().lstrip("#") for t in spec.replace(",", " ").split()]
    if toks and all(t.isdigit() or ("-" in t and
                                    all(p.isdigit() for p in t.split("-", 1)))
                    for t in toks):
        idx = set()
        for t in toks:
            if "-" in t:
                a, b = (int(p) for p in t.split("-", 1))
                idx.update(range(min(a, b), max(a, b) + 1))
            else:
                idx.add(int(t))
        return [(i, 0.0) for i in sorted(idx) if i < len(exs)]
    # text match: embed the spec, keep SAFE exemplars over the sim floor
    mem = srv.membrane
    try:
        vec = mem.embedder.embed([spec])[0]
    except Exception:
        return []
    qn = np.asarray(vec, dtype=float)
    n = np.linalg.norm(qn)
    if n <= 1e-12:
        return []
    qn = qn / n
    hits = []
    for ex, sim in mem.store.top_k(qn, k=_FORGET_LIMIT):
        if sim >= 0.55 and ex.safe:
            hits.append((mem.store.index_of(ex), float(sim)))
    return [(i, s) for i, s in sorted(hits) if i is not None]


def _forget_report(srv, spec: str) -> str:
    mem = srv.membrane
    is_window = _window_seconds(spec) is not None
    targets = _forget_targets(srv, spec)
    if not targets:
        if is_window:
            return ("Membrane forget (time window): nothing was appended since "
                    "boot; nothing removed.\n\n" + _INTERCEPT_MARKER)
        return ("Membrane forget (operator path, no model call): nothing in the "
                "field matches that; nothing removed.\n\n" + _INTERCEPT_MARKER)
    n0 = len(mem.store)
    # capture the situation texts BEFORE removal (indexes shift after)
    shown = [(i, sim, _clip(mem.store.exemplars[i].situation, 160))
             for i, sim in targets[:8] if i < n0]
    removed = mem.store.forget([i for i, _ in targets])
    mem.store.save(mem.field_path)
    _write_audit(mem, {"event": "forget", "removed": removed,
                       "window": is_window,
                       "field_size_before": n0,
                       "field_size_after": len(mem.store),
                       "spec": _clip(spec, 200)})
    head = ("Membrane forget (time window, operator path, no model call): "
            "removed" if is_window else
            "Membrane forget (operator path, no model call): removed")
    lines = [f"{head} {removed} of {n0} exemplars; field now {len(mem.store)}."]
    for i, sim, q in shown:
        lines.append(f"- #{i}{'' if sim == 0.0 else f' (sim {sim:.2f})'}: {q}")
    if len(targets) > 8:
        lines.append(f"- ... and {len(targets) - 8} more")
    return "\n".join(lines) + _INTERCEPT_MARKER


def _learn_report(srv, payload: str) -> str:
    mem = srv.membrane
    if not payload.strip() or not mem.embedder:
        return ("Membrane learn (operator path): nothing to learn or embedder "
                "unresolved -- nothing written.\n\n" + _INTERCEPT_MARKER)
    idx = mem.learn_pair(payload, payload)
    if idx is None:
        return ("Membrane learn (operator path): the field already holds that "
                "(dedup) or the variety gate skipped it -- nothing new written."
                "\n\n" + _INTERCEPT_MARKER)
    _write_audit(mem, {"event": "operator-learn", "ex_idx": idx,
                       "tag": _LEARN_TAG, "field_size": len(mem.store),
                       "payload": _clip(payload, 400)})
    ex = mem.store.exemplars[idx]
    return (f"Membrane learn (operator path, no model call): written as "
            f"exemplar #{idx}, safe=True. Field now "
            f"{len(mem.store)}.\n    Q: {_clip(ex.situation, 280)}\n"
            f"    A: {_clip(ex.action or '', 280)}\n\n" + _INTERCEPT_MARKER)


# ---- semantic fallback for the intercepts (the field grows its own detector) -
# Paradox (superseded, see the field-grown layer below): recall/state asks are
# intercepted BEFORE learning, so the field never holds examples of them -- no
# self-grown detector seemed possible. The FIXED seed is still external: a
# fixed phrase set, embedded once, cosine-matched when the LEXICAL matcher
# misses. High bar (it can steal a real coding turn); the exact-echo recovery
# hatch below fixes a false steal.

_INTERCEPT_SEEDS = [
    # recall family
    "what do you remember about our earlier discussion of retry handling",
    "what have you learned about the deployment pipeline",
    "what do you recall from previous sessions about my preferences",
    "search your memory for the name of that helper function",
    "query the field for anything about rate limiting",
    "what do you know about the flaky test we fixed last week",
    "do you remember why we chose optimistic locking",
    "remind me what we decided about the config format",
    "show me what you remember of the database migration plan",
    # state family
    "how full is your memory",
    "what state is your memory in",
    "tell me about your field statistics",
    "how is your memory health",
    "how many memories do you have right now",
    "are there any new memories since earlier",
    # learn family (the write skill, without the incantation) -- payload VARIED
    # so the family spans payload space (a topic-bound seed set misses real
    # writes whose payload differs from every seed's payload)
    "save this to your memory: the deploy script needs RUN_ENV=staging",
    "save this to your memory: we deploy on tuesdays",
    "remember that the api key rotates every month",
    "remember that the user prefers dark mode",
    "store a note in your field that the cron job moved to 3am",
    "store a note in your field that tests run with pnpm",
    "keep in mind for future sessions that we use pnpm here",
    "keep in mind for future sessions that staging uses port 8080",
    "you should remember that the staging password rotates weekly",
    "you should remember that the retry budget is three attempts",
    "note for later: the retry budget is three attempts",
    "note for later: the linter config lives in biome.json",
    # forget family (the delete skill, without the incantation) -- payload varied
    "forget everything about the old config format",
    "forget everything about the old retry policy",
    "delete the memory of that broken deploy workaround",
    "delete the memory of the deprecated v1 endpoints",
    "wipe that from your memory, the staging notes are stale",
    "erase what you learned about the deprecated api",
    "erase what you learned about the staging credentials",
    "remove from your memory the thing about the flaky windows runner",
]
# near-miss coding turns that must NOT trip the semantic gate
_INTERCEPT_ANTI_SEEDS = [
    "refactor the retry handling in client.py",
    "how do we handle retry logic in the request layer",
    "remember to bump the version before releasing",
    "remember to run the deploy script on tuesdays",
    "add a field to the user model for the last login time",
    "the tests are flaky, find out why and fix them",
    "remind me to review the migration plan before friday",
    "remind me what the api key rotation schedule is",
    "write a unit test for the rate limiter",
    "clean up the deprecated api endpoints this sprint",
    "note that the linter fails on generated files",
    "delete the old log files in /var/tmp",
]

# runtime caches (populated on the MembraneOpenAIHandler's server): the embedded
# seed matrix + threshold for the FIXED layer, and the same for the FIELD-GROWN
# layer (canon-frame exemplars harvested from the live store, cached on field
# size). Per-call embedding of the query costs one /api/embeddings round-trip
# (~ms local) and only fires when the lexical frames miss.
_SEED_CACHE = {"matrix": None, "thr": None, "tuned": False,
               "field": {"size": -1, "matrix": None, "thr": None,
                         "topics": []}}


def _tune_threshold(srv, Vn, tag: str) -> float | None:
    """Midpoint threshold for one seed layer (tag = 'fixed'|'field') vs the
    shared anti-seed set: halfway between the strongest anti-seed pull to any
    seed (false-steal pressure) and a perfect family match (1.0). None when
    the geometry cannot separate the sets -- 0 would intercept everything;
    None is the honest 'cannot separate' and that layer stays off (lexical
    frames carry it)."""
    mem = srv.membrane
    anti = np.asarray(mem.embedder.embed(_INTERCEPT_ANTI_SEEDS), dtype=float)
    norms = np.linalg.norm(anti, axis=1)
    norms[norms <= 1e-12] = 1.0
    anti = anti / norms[:, None]
    anti_best = float((anti @ Vn.T).max())
    thr = (1.0 + anti_best) / 2.0
    if thr > 0.99:
        # degenerate geometry (tiny/one-hot embedding space): anti-seeds
        # are indistinguishable from seeds -- the semantic gate would sit
        # unreachable at ~1.0. Disable; the lexical frames carry it.
        print(f"[opencode] intercept {tag} seeds inseparable "
              f"(anti_best={anti_best:.4f}); layer disabled", file=sys.stderr)
        return None
    print(f"[opencode] intercept {tag} layer tuned: thr={thr:.3f} "
          f"(anti_best={anti_best:.3f})", file=sys.stderr)
    return thr


# Fam = "recall" | "state" | "learn" | "forget" -- the four skill families
# the semantic gate routes into. Fixed-layer seeds are labeled lexically here;
# field-grown seeds are labeled by which canon frame they carry.
_FAMILIES = ("recall", "state", "learn", "forget")
_STATE_WORDS = ("state", "stats", "statistics", "health", "full", "many")
_LEARN_WORDS = ("save this to your memory", "remember that", "store a note",
                "keep in mind", "you should remember", "note for later")
_FORGET_WORDS = ("forget", "delete the memory", "wipe", "erase",
                 "remove from your memory")


def _family_of(text: str) -> str:
    """Fixed-layer seed labeling: learn/forget keywords win (they read as
    commands), state words beat recall (a state ask is about the FIELD's own
    math), else recall."""
    low = text.lower()
    if any(w in low for w in _FORGET_WORDS):
        return "forget"
    if any(w in low for w in _LEARN_WORDS):
        return "learn"
    if any(w in low for w in _STATE_WORDS):
        return "state"
    return "recall"


def _seed_topics() -> list:
    """(text, family) for every fixed seed phrase."""
    return [(s, _family_of(s)) for s in _INTERCEPT_SEEDS]


def _embed_seed_matrix(srv) -> bool:
    """Embed every fixed seed once, cache (N, D) unit rows + the tuned
    threshold. False when the embedder is unavailable (the fallback then
    stays off -- lexical-only behavior)."""
    if _SEED_CACHE["matrix"] is not None:
        return True
    mem = srv.membrane
    if mem.embedder is None:
        return False
    topics = _seed_topics()
    try:
        V = np.asarray(mem.embedder.embed([t for t, _ in topics]), dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        thr = _tune_threshold(srv, Vn, "fixed")
    except Exception as e:
        print(f"[opencode] intercept seed embedding failed ({e}); "
              f"semantic fallback off", file=sys.stderr)
        return False
    _SEED_CACHE["matrix"] = (Vn, topics)
    _SEED_CACHE["thr"] = thr
    return True


# ---- field-grown seed layer -------------------------------------------------
# The 524-note paradox ("intercepted asks are never learned, so the field cannot
# grow its own detector") is broken by the CANON-FRAME PAYLOADS that DO enter
# the field: operator-note writes (the learn skill's payload), forget-command
# echoes, and any escaped (`model:`) skill ask that later settled. Those
# exemplars carry the canonical frames verbatim -- harvest them as a second
# seed layer so the detector grows from the operator's own hand, not from raw
# model traffic (which would let junk reasoning poison the semantic gate).
# Labeling: which canon frame the exemplar text carries -> its family.


def _field_seed_topics(srv) -> list:
    """(text, family, ex_idx) for every store exemplar carrying a canonical
    skill frame (learn-tag payloads, recall/learn/forget canon lines, escaped
    model: asks). Cached on field size -- a forget that removes the seed
    exemplars de-activates the field layer (a feature: forgetting also
    un-teaches the detector). Empty when --no-field-seeds."""
    if not getattr(srv, "field_seeds", True):
        return []
    mem = srv.membrane
    n = len(mem.store)
    c = _SEED_CACHE["field"]
    if c["size"] == n and c["matrix"] is not None:
        return c["topics"]
    topics: list = []
    for ex in mem.store.exemplars:
        text = " ".join((ex.situation + " " + (ex.action or "")).split())
        low = text.lower()
        if low.startswith("model:"):
            fam = "recall"
        elif any(f in low for f in _FORGET_FRAMES):
            fam = "forget"
        elif any(f in low for f in _LEARN_FRAMES):
            fam = "learn"
        elif any(f in low for f in _RECALL_FRAMES):
            fam = "recall"
        elif low.startswith("membrane:"):
            body = low[len("membrane:"):].strip()
            fam = ("learn" if body.startswith("learn") else
                   "forget" if body.startswith("forget") else
                   "state" if body.startswith(("field state", "state")) else
                   "recall")
        else:
            continue
        topics.append((text, fam, mem.store.index_of(ex)))
    c["topics"] = topics
    c["size"] = n
    c["matrix"] = None            # force re-embed on next use
    return topics


def _embed_field_seed_matrix(srv) -> bool:
    """Embed + threshold-tune the field-grown layer. False leaves the layer
    off (no seeds, embedder gone, --no-field-seeds, or inseparable)."""
    topics = _field_seed_topics(srv)
    if not topics:
        return False
    c = _SEED_CACHE["field"]
    if c["matrix"] is not None:
        return True
    mem = srv.membrane
    if mem.embedder is None:
        return False
    try:
        V = np.asarray(mem.embedder.embed([t for t, _, _ in topics]),
                       dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        thr = _tune_threshold(srv, Vn, "field")
        # (None -> inseparable -> the layer stays off, the honest 'cannot
        # separate')
    except Exception as e:
        print(f"[opencode] field seed embedding failed ({e}); "
              f"field layer off", file=sys.stderr)
        return False
    c["matrix"] = (Vn, thr)
    return thr is not None


def _semantic_intercept_hit(srv, q: str) -> tuple[str, float, str] | None:
    """4-family semantic verdict: (family, sim, source) for the best-matching
    family over BOTH seed layers (source = 'fixed' | 'field'), or None when
    nothing clears its layer's tuned threshold. A layer tuned to None
    (inseparable geometry) or failed embed simply contributes nothing --
    lexical frames carry on. Family margin: the winner must beat the best of
    every OTHER family by +0.02 (a state ask must not route to recall, a
    learn to a forget, etc.)."""
    if not _embed_seed_matrix(srv):
        return None
    mem = srv.membrane
    layers = []
    if _SEED_CACHE["thr"] is not None:
        layers.append((_SEED_CACHE["matrix"][0], _SEED_CACHE["thr"], "fixed"))
    if _embed_field_seed_matrix(srv):
        Vn, thr = _SEED_CACHE["field"]["matrix"]
        layers.append((np.asarray(Vn, dtype=float), thr, "field"))
    if not layers:
        return None
    try:
        qv = np.asarray(mem.embedder.embed([q]), dtype=float)[0]
        n = float(np.linalg.norm(qv))
        if n <= 1e-12:
            return None
        qv = qv / n
    except Exception:
        return None
    fam_best = {f: 0.0 for f in _FAMILIES}
    hit_src: dict = {}
    hit_txt: dict = {}
    for Vn, thr, src in layers:
        # field topics are (text, family, idx) triples; fixed are (text, family)
        topics = (_SEED_CACHE["matrix"][1] if src == "fixed"
                  else [(t, fam) for t, fam, _ in _SEED_CACHE["field"]["topics"]])
        sims = np.asarray(Vn, dtype=float) @ qv
        for s, (t, fam) in zip(sims, topics):
            if float(s) >= thr and float(s) > fam_best[fam]:
                fam_best[fam] = float(s)
                hit_src[fam] = src
                hit_txt[fam] = t
    fam = max(fam_best, key=lambda f: fam_best[f])
    if fam_best[fam] <= 0.0:
        return None
    others = max((v for f, v in fam_best.items() if f != fam), default=0.0)
    if fam_best[fam] < others + 0.02:
        return None
    print(f"[opencode] semantic intercept: family={fam} "
          f"sim={fam_best[fam]:.3f} source={hit_src[fam]} "
          f"seed={_clip(hit_txt[fam], 60)!r}", file=sys.stderr)
    return fam, fam_best[fam], hit_src[fam]


def _semantic_recall_hit(srv, q: str) -> tuple[bool, bool]:
    """Compat shim over the 4-family verdict (recall/state booleans)."""
    hit = _semantic_intercept_hit(srv, q)
    if hit is None:
        return False, False
    fam = hit[0]
    return fam == "recall", fam == "state"


def _recall_report(srv, topic: str) -> str:
    """The internal recall answer: top-k SAFE exemplars for the topic, or the
    newest slice of the field for a dump. Read-only: no learn, no audit write,
    no nudge -- the only embed call is of the TOPIC itself (an outbound
    query, never stored)."""
    mem = srv.membrane
    exs = mem.store.exemplars
    head = ("Field recall (computed live by membrane, no model call; "
            "this turn was intercepted and NOT learned):")
    if mem.embedder is None:
        return head + "\n- embedder unresolved; cannot search the field"
    lines = [head]
    if topic:
        vec = mem.embedder.embed([topic])[0]
        hits = 0
        for ex, sim in mem.store.top_k(vec, k=12):
            if sim < 0.25 or not ex.safe:
                continue
            q = _clip(ex.situation, 280)
            a = _clip(ex.action or "", 280)
            if not q or not a:
                continue
            tag = getattr(ex, "outcome", "") or "pending"
            lines.append(f"({sim:.2f} [{tag}]) Q: {q}\n    A: {a}")
            hits += 1
            if hits >= 8:
                break
        if hits == 0:
            lines.append(f"- nothing SAFE in the field about {topic!r} yet")
    else:
        lines.append(f"- field holds {len(exs)} exemplars; newest {min(12, len(exs))}:")
        for i, ex in enumerate(exs[-12:]):
            tag = getattr(ex, "outcome", "") or "pending"
            mark = "" if ex.safe else " [UNSAFE]"
            lines.append(f"#{len(exs) - len(exs[-12:]) + i} [{tag}]{mark} "
                         f"Q: {_clip(ex.situation, 200)}\n"
                         f"    A: {_clip(ex.action or '', 200)}")
    return "\n".join(lines)


def _last_user_text(body: dict) -> str:
    for m in reversed(body.get("messages") or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c
        if isinstance(c, list):
            parts = [b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text"]
            t = " ".join(p for p in parts if p)
            if t.strip():
                return t
    return ""


def n_ctx_mem(ctx: str | None) -> int:
    return 0 if not ctx else ctx.count("Q: ")


def _sse_line(obj) -> bytes:
    return ("data: " + json.dumps(obj) + "\n\n").encode()


def _openai_sse_chunks(model: str, content: str, piece: int = 128,
                       reasoning: bool = False):
    """One assistant text as OpenAI-style SSE chunks (the internal state
    answer, or the [membrane] gate monologue). ``reasoning=True`` emits the
    text as reasoning deltas (the thinking channel) instead of content."""
    key = "reasoning" if reasoning else "content"
    id_ = "chatcmpl-membrane-state"
    head = {"id": id_, "object": "chat.completion.chunk", "created": 0,
            "model": model}
    yield _sse_line({**head, "choices": [{"index": 0,
                                          "delta": {"role": "assistant"},
                                          "finish_reason": None}]})
    for i in range(0, len(content), piece):
        yield _sse_line({**head, "choices": [{"index": 0,
                                              "delta": {key: content[i:i + piece]},
                                              "finish_reason": None}]})
    yield _sse_line({**head, "choices": [{"index": 0, "delta": {},
                                          "finish_reason": "stop"}]})
    yield b"data: [DONE]\n\n"


def _known_embed_names(backend: str) -> list:
    """Ollama model names whose tag looks like an embedding model."""
    try:
        with urllib.request.urlopen(backend + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
    except Exception:
        return ["mxbai-embed-large", "nomic-embed-text"]
    return [m.get("name", "") for m in tags.get("models", [])
            if any(k in m.get("name", "").lower() for k in _EMBED_MARKERS)]


def _probe_embedder(backend: str, model: str):
    """An OllamaEmbedder for ``model`` iff a real embed call succeeds."""
    try:
        emb = OllamaEmbedder(base_url=backend, model=model, timeout=60)
        emb.embed(["probe"])
        return emb
    except Exception as e:
        print(f"[opencode] {model} cannot embed ({_one_line(e)})", file=sys.stderr)
        return None


def _one_line(e: Exception) -> str:
    import re
    return re.sub(r"\s+", " ", str(e))[:140]


def _audit_count(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return 0


# ---- total capture: plans, tool calls, tool results, reasoning ---------------
# Everything the model emits between your prompts is also memory-worthy: the
# tool plan (what it intended), each tool call (what it ran), the tool result
# (what actually came back -- it arrives on the NEXT request as a role:"tool"
# message), and the reasoning deltas (what it thought). Each becomes an
# ordinary Q/A exemplar (situation -> action) learned by the standard pipeline,
# so recall + compaction treat a tool trace exactly like a chat turn.

_THINK_KEYS = ("reasoning", "reasoning_content", "thinking")


def _msg_reasoning(msg: dict) -> str:
    for kk in _THINK_KEYS:
        v = msg.get(kk)
        if isinstance(v, str) and v.strip():
            return v.strip()
    c = msg.get("content")
    if isinstance(c, list):                      # anthropic-style thinking blocks
        return "\n".join(b.get("thinking", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "thinking"
                         and isinstance(b.get("thinking"), str)).strip()
    return ""


def _delta_reasoning(d: dict) -> str:
    for kk in _THINK_KEYS:
        v = d.get(kk)
        if isinstance(v, str):
            return v
    return ""


def _render_calls(calls: dict) -> str:
    """Accumulated tool-call deltas -> one compact line per call."""
    lines = []
    for idx in sorted(calls):
        c = calls[idx]
        fn = c.get("function") or {}
        lines.append(f"{fn.get('name') or '?'}({(fn.get('arguments') or '').strip()})")
    return "\n".join(lines)


def _tool_result_text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(b.get("text", "") for b in c
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _capture(mem, label: str, situation: str, action: str, min_len: int = 1) -> None:
    """Learn one observed artifact (plan / call / result / reasoning) as a plain
    Q/A exemplar. NEVER breaks the chat path: a capture error is logged and
    dropped. The standard pipeline (vague + dedup guards, repair path) applies,
    so a retry of the same tool call merges instead of piling up."""
    try:
        if not (situation.strip() and action.strip()) or len(action) < min_len:
            return
        idx = mem.learn_pair(situation, action)
        if idx is not None:
            print(f"[capture] {label} -> exemplar #{idx}", file=sys.stderr)
        else:
            print(f"[capture] {label} skipped (guard)", file=sys.stderr)
    except Exception as e:
        print(f"[capture] {label} error: {_one_line(e)}", file=sys.stderr)


def _capture_plan_and_calls(mem, query: str, content: str, calls: dict) -> None:
    """One response turn: learn the visible plan text and each tool call. Each
    call registers in mem._pending_tools so its RESULT (next request) captures
    the full situational loop: 'tool bash for: <query> / args' -> result."""
    if calls:
        for idx, c in sorted(calls.items()):
            fn = c.get("function") or {}
            name = fn.get("name") or "?"
            args = (fn.get("arguments") or "").strip()
            _capture(mem, f"call {name}",
                     f"tool call for: {_clip(query, 200)} -> {name}(args)",
                     f"{name}({_clip(args, 600)})")
            cid = c.get("id")
            if cid:
                mem._pending_tools[cid] = {"query": query, "name": name, "args": args}
        if content.strip():
            _capture(mem, "plan",
                     f"plan: {_clip(query, 200)}",
                     content)
    elif content.strip():
        # (text-only turns are learned by the main path; nothing extra here)
        pass


def _capture_tool_results(srv, body: dict) -> None:
    """Scan the incoming request for role:'tool' messages and learn each one
    paired with its pending call (query + args -> what the tool returned).
    Runs BEFORE the gate so it never disturbs the chat verdict."""
    mem = srv.membrane
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        cid = m.get("tool_call_id")
        if not cid or m.get("role") == "assistant":
            continue
        rec = mem._pending_tools.pop(cid, None)
        if rec is None:
            continue                      # pre-boot result or unknown id
        result = _tool_result_text(m)
        _capture(mem, f"result {rec['name']}",
                 f"tool {rec['name']} for: {_clip(rec['query'], 200)}\n"
                 f"args: {_clip(rec['args'], 300)}",
                 _clip(result, 2400))


def _capture_reasoning(mem, query: str, reasoning: str) -> None:
    _capture(mem, "reasoning", f"reasoning: {_clip(query, 200)}",
             reasoning, min_len=80)


def _chat_capable_models(backend: str) -> list:
    try:
        with urllib.request.urlopen(backend + "/api/tags", timeout=30) as r:
            tags = json.loads(r.read())
    except Exception:
        return []
    return [m.get("name", "") for m in tags.get("models", [])
            if m.get("name") and not _is_non_chat(m.get("name"))]


def _pick_default_model(models: list, preferred: str | None) -> str | None:
    if preferred:
        for m in models:
            if m == preferred or m.split(":")[0] == preferred:
                return m
    for want in ("glm", "qwen3", "qwen2.5-coder", "llama3.1", "mistral", "gemma"):
        for m in models:
            if m.startswith(want):
                return m
    return models[0] if models else None


class MembraneOpenAIHandler(RAGProxyHandler):
    """The RAG handler + the OpenAI-compatible surface: /v1/models,
    /v1/chat/completions (the RAG gate + the internal state/math intercept),
    /v1/embeddings (the field's own embedder), /membrane/state. Everything
    else forwards to Ollama."""

    # ---- routing ----
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/v1/models", "/v1/models/"):
            self._v1_models(); return
        if path in ("/membrane/state", "/state"):
            self._membrane_state(); return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/chat/completions":
            self._v1_chat(); return
        if path == "/v1/embeddings":
            self._v1_embeddings(); return
        if path.startswith("/membrane/"):
            self._json_out({"error": "read-only; GET /membrane/state"}); return
        super().do_POST()

    def _json_err_raw(self, code: int, msg: str) -> None:
        out = json.dumps({"error": {"message": msg, "type": "membrane_error"}}
                         ).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _json_out(self, obj) -> None:
        out = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    # ---- /v1/models: internal (Ollama's list, embed models filtered out) ----
    def _v1_models(self) -> None:
        ids = _chat_capable_models(self.server.backend)
        self._json_out({"object": "list",
                        "data": [{"id": m, "object": "model",
                                  "owned_by": "membrane->ollama"} for m in ids]})

    # ---- /v1/embeddings: the field's own embedder ----
    def _v1_embeddings(self) -> None:
        body = self._json_body()
        inp = body.get("input", "")
        texts = inp if isinstance(inp, list) else [inp]
        texts = [str(t) for t in texts if str(t).strip()]
        if not texts:
            self._json_err_raw(400, "input required"); return
        srv = self.server
        if srv.membrane.embedder is None and self._resolve_embedder(None) is None:
            self._json_err_raw(503, srv.pending_embed_error); return
        try:
            vecs = srv.membrane.embedder.embed(texts)
        except Exception as e:
            self._json_err_raw(503, f"embed failed: {_one_line(e)}"); return
        self._json_out({"object": "list",
                        "data": [{"object": "embedding", "index": i,
                                  "embedding": [float(x) for x in v]}
                                 for i, v in enumerate(vecs)],
                        "model": srv.membrane.embedder.model})

    # ---- the embedder resolution ladder (embed role) ----
    def _resolve_embedder(self, selected_model: str | None):
        """Order: explicit --embed-model; a known Ollama embed model from
        /api/tags; the opencode-selected chat model (only if it actually
        embeds). On total failure the error is parked on the server and every
        chat/embeddings turn returns it -- err and block the chat."""
        srv = self.server
        backend = srv.backend
        cands = []
        if srv.embed_model_arg:
            cands.append(srv.embed_model_arg)
        cands += [c for c in _known_embed_names(backend) if c not in cands]
        if selected_model and selected_model not in cands:
            cands.append(selected_model)
        for cand in cands:
            probe = _probe_embedder(backend, cand)
            if probe is not None:
                srv.membrane.embedder = probe
                srv.resolved_embed_name = cand
                srv.pending_embed_error = None
                print(f"[opencode] embedder resolved: {cand}", file=sys.stderr)
                return probe
        srv.pending_embed_error = (
            "no Ollama model can serve embeddings; the membrane field cannot "
            "run without one. Pull an embedding model, e.g. "
            "`ollama pull mxbai-embed-large`, then retry.")
        print(f"[opencode] {srv.pending_embed_error}", file=sys.stderr)
        return None

    # ---- /v1/chat/completions: gate + RAG + learn; state turns internal ----
    def _v1_chat(self) -> None:
        srv = self.server
        mem = srv.membrane
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._forward_raw(raw); return
        model = str(body.get("model", "membrane"))
        # the opencode-selected model is the cortex's AND the judge's model
        # (unless pinned): the follow-chat judge reads srv.compact_model live
        if srv.compact_follows_chat:
            srv.compact_model = model
        # ---- the embed role: resolve lazily (maybe ollama gained a model);
        # block the chat with a clear 503 when nothing can embed.
        if mem.embedder is None and self._resolve_embedder(model) is None:
            self._json_err_raw(503, srv.pending_embed_error); return
        # ---- total capture: results from the PREVIOUS turn's tool calls ----
        # before the intercepts: even a recall/state turn may carry the previous
        # turn's tool results; pairing must not be skipped. A capture error
        # must never break the chat.
        try:
            _capture_tool_results(srv, body)
        except Exception as cap_e:
            print(f"[opencode] result-capture error: {cap_e}", file=sys.stderr)
        # ---- INTERNAL interception (before the gate: these turns never touch
        # the field -- no learn, no demote, no nudge -- so introspection can't
        # feed the field back into itself). Served straight from the live store.
        # Detection: lexical frames first (deterministic fast path); SEMANTIC
        # fallback on INTENT when nothing matches: cosine to the fixed seed
        # set PLUS the field-grown seed layer (canon-frame exemplars harvested
        # from the live store -- the detector grows from the operator's own
        # writes; --no-field-seeds is the kill switch). A stolen turn echoes
        # the exact marker to let the client force the model path next turn.
        q_early = _last_user_text(body)
        model_escaped = q_early.strip().lower().startswith("model:")
        # ---- pre-gate detection: lexical frames first (deterministic fast
        # path); when nothing matches, the 4-family semantic gate routes on
        # INTENT (cosine to the fixed seeds + the field-grown seed layer).
        # Operator write/forget (the command turn itself must never be
        # learned/demoted; its PAYLOAD goes through learn_pair / store.forget
        # with an explicit audit record), recall/state answer-from-field, and
        # the human's ruling on a parked judge escalation (final authority).
        learn_hit = (not model_escaped) and _is_learn_query(q_early)
        forget_hit = (not model_escaped) and _is_forget_query(q_early)
        rec_hit = (not model_escaped) and _is_recall_query(q_early)
        state_hit = (not model_escaped) and _is_state_query(q_early)
        if not any((learn_hit, forget_hit, rec_hit, state_hit)) and srv.sem_intercept:
            # the semantic fallback embeds the query itself (an outbound read,
            # never learned). 4-family: recall/state answer from the field;
            # learn/forget are operator writes routed without the incantation.
            s_hit = _semantic_intercept_hit(srv, q_early)
            if s_hit:
                s_fam = s_hit[0]
                if s_fam == "recall":
                    rec_hit = True
                elif s_fam == "state":
                    state_hit = True
                elif s_fam == "learn":
                    learn_hit = True
                elif s_fam == "forget":
                    forget_hit = True
        ruling = (None if model_escaped else _match_review_ruling(q_early))
        if ruling is not None:
            v, slot = ruling
            text = _review_report(srv, v, slot)
            print(f"[opencode] intercept (review-{v} {slot}): "
                  f"{q_early[:60]!r}", file=sys.stderr)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        if learn_hit or forget_hit:
            if learn_hit:
                payload = (_after_frame(q_early, (_LEARN_CANON,)) or
                           _after_frame(q_early, _LEARN_FRAMES) or
                           _after_frame(q_early, _LEARN_SOFT_FRAMES))
                if not payload:            # bare "membrane: learn that" -> use the rest
                    payload = q_early[len(_LEARN_CANON):] if q_early.lower().startswith(
                        _LEARN_CANON) else ""
                text = _learn_report(srv, payload)
            else:
                spec = (_after_frame(q_early, (_FORGET_CANON,)) or
                        _after_frame(q_early, _FORGET_FRAMES) or
                        _after_frame(q_early, _FORGET_SOFT_FRAMES) or
                        q_early.rstrip("?!").strip())
                if not spec and _window_seconds(q_early) is not None:
                    spec = q_early          # bare 'forget the last hour' form
                text = _forget_report(srv, spec)
            print(f"[opencode] intercept ({'learn' if learn_hit else 'forget'}): "
                  f"{q_early[:60]!r}", file=sys.stderr)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        if rec_hit or state_hit:
            fam = "recall" if rec_hit else "state"
            marker = ("[membrane-intercept] internal " + fam +
                      " answer; it was NOT learned. To force the model path, "
                      "repeat the ask prefixed with 'model:'")
            text = (_recall_report(srv, _recall_topic(q_early)) if rec_hit
                    else _state_report(srv))
            text = text + "\n\n" + marker
            print(f"[opencode] intercept ({fam}, "
                  f"{'lexical' if (rec_hit and _is_recall_query(q_early)) or (state_hit and _is_state_query(q_early)) else 'semantic'}): "
                  f"{q_early[:60]!r}", file=sys.stderr)
            self._serve_intercept_answer(model, text,
                                         bool(body.get("stream", False)))
            return
        try:
            verdict, vec, query, tool_pair_q = mem.gate_turn(body)
            if vec is None:
                self._forward_raw(raw); return
        except Exception as e:
            print(f"[opencode] gate error, forwarding raw: {e}", file=sys.stderr)
            self._forward_raw(raw); return
        # ---- INTERNAL: the 'model:' escape hatch only (recall/state were both
        # fully handled above, pre-gate) ----
        q = _last_user_text(body)
        ctx = self._memory_block(vec)
        # pending human reviews ride along as a system-level notice: the model
        # is told to ASK the user via its native question tool. The user's
        # answer arrives as the next turn and is matched by the pre-gate
        # 'membrane: keep/drop' resolver below.
        review_block = _review_request_block(srv)
        if review_block:
            mono_review = review_block
        else:
            mono_review = ""
        if review_block:
            ctx = (ctx + "\n\n" + review_block) if ctx else review_block
        replay_sim = float(getattr(srv, "replay_sim", 0.0) or 0.0)
        mono = (self._gate_monologue(verdict, ctx, query)
                if getattr(srv, "gate_monologue", True) else "")
        if mono_review:
            mono = (mono + "\n[membrane] " + _one_line(mono_review)[:220]) if mono else "[membrane] " + _one_line(mono_review)[:220]
        if (replay_sim > 0 and verdict["verdict"] == "act"
                and (verdict.get("sim") or 0.0) >= replay_sim and not tool_pair_q):
            # FIELD REPLAY: the gate is sure (act, consensus >= 0.6) AND the
            # match clears the stricter replay line -> answer from the stored
            # action with NO model call. A safe memory that keeps proving
            # itself (later returns, not instant re-asks) becomes free. The
            # re-ask demotion loop still runs inside gate_turn, so a repeat
            # DURING the reask window still demotes instead of replaying.
            ex = mem.store.exemplars[verdict["ex_idx"]]
            answer = ex.action
            v = {"verdict": "act-replayed", "reason": verdict.get("reason", ""),
                 "sim": verdict.get("sim"), "nov": verdict.get("nov"),
                 "ex_idx": verdict["ex_idx"], "spectrum_entropy": verdict.get("spectrum_entropy"),
                 "free_energy": verdict.get("free_energy"), "rank_ratio": verdict.get("rank_ratio"),
                 "safe_frac": verdict.get("safe_frac"), "sim_min_eff": verdict.get("sim_min_eff")}
            self._serve_intercept_answer(model, answer,
                                         bool(body.get("stream", False)),
                                         thinking=mono)
            mem.record_act(query, vec, v, model, answer)
            self._log_turn(v, ctx, None, len(mem.store),
                           replayed=verdict["ex_idx"])
            return
        up = dict(body)
        if ctx:
            up["messages"] = _inject(body.get("messages") or [], ctx)
        try:
            self._v1_deliver(up, raw, query, vec, verdict, ctx)
        except Exception as e:
            print(f"[opencode] backend error, no field write: {e}", file=sys.stderr)
            self._forward_raw(raw)

    def _v1_deliver(self, up: dict, raw: bytes, query: str, vec, verdict: dict,
                    ctx: str | None) -> None:
        """Forward to Ollama's /v1/chat/completions (stream or buffered), tee
        to the client, then learn the exchange into the field."""
        srv = self.server
        mem = srv.membrane
        data = json.dumps(up).encode()
        req = urllib.request.Request(srv.backend + "/v1/chat/completions",
                                     data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        streaming = bool(up.get("stream", False))
        parts: list[str] = []
        think: list[str] = []
        calls: dict = {}          # index -> accumulated tool-call delta
        tool_calls = False
        if streaming:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            # the [membrane] gate monologue: streamed as reasoning deltas so
            # opencode shows the proxy's routing/recall/judge machinery in the
            # thinking channel, separated from the model's own reasoning.
            if getattr(srv, "gate_monologue", True):
                mono = self._gate_monologue(verdict, ctx, query)
                if mono:
                    for chunk in _openai_sse_chunks(
                            up.get("model", "membrane"), mono + "\n\n",
                            reasoning=True):
                        self.wfile.write(chunk)
                    self.wfile.flush()
            with urllib.request.urlopen(req, timeout=300) as r:
                for line in r:                      # SSE, tee verbatim
                    if not line:
                        continue
                    self.wfile.write(line)
                    self.wfile.flush()
                    s = line.strip()
                    if not s.startswith(b"data:"):
                        continue
                    payload = s[len(b"data:"):].strip()
                    if not payload or payload == b"[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for ch in obj.get("choices", []) or []:
                        d = ch.get("delta") or {}
                        t = d.get("content")
                        if isinstance(t, str):
                            parts.append(t)
                        think.append(_delta_reasoning(d))
                        for tc in d.get("tool_calls") or []:
                            tool_calls = True
                            i = int(tc.get("index", 0) or 0)
                            c = calls.setdefault(i, {"function": {}})
                            if tc.get("id"):
                                c["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            tgt = c["function"]
                            if fn.get("name"):
                                tgt["name"] = (tgt.get("name") or "") + fn["name"]
                            if fn.get("arguments"):
                                tgt["arguments"] = (tgt.get("arguments") or "") + fn["arguments"]
            self.wfile.flush()
            resp_obj = None
        else:
            resp_bytes = urllib.request.urlopen(req, timeout=300).read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            self.wfile.flush()
            try:
                resp_obj = json.loads(resp_bytes)
            except json.JSONDecodeError:
                resp_obj = None
            if resp_obj:
                ch = (resp_obj.get("choices") or [{}])[0]
                msg = ch.get("message") or {}
                c = msg.get("content")
                if isinstance(c, str):
                    parts.append(c)
                if msg.get("tool_calls"):
                    tool_calls = True
                    for j, tc in enumerate(msg["tool_calls"]):
                        if isinstance(tc, dict):
                            calls[int(tc.get("index", j) or 0)] = tc
                think.append(_msg_reasoning(msg))
        content = "".join(parts)
        thought = "".join(think).strip()
        ex_idx = None
        # learn text turns; a pure tool-call turn has no human prose to ground
        # future turns on and the gate may have already learned from its user
        # text -- so only text-bearing answers are learned here.
        if content.strip() and not tool_calls:
            try:
                ex_idx = mem.record_escalation(query, vec, verdict, content,
                                               up.get("model", "membrane"),
                                               False)
            except Exception as e:
                print(f"[opencode] learn error (response already served): {e}",
                      file=sys.stderr)
        # total capture: the model's intent + machinery + thought, as plain Q/A.
        try:
            if calls:
                _capture_plan_and_calls(mem, query, content, calls)
            if thought:
                _capture_reasoning(mem, query, thought)
        except Exception as e:
            print(f"[opencode] capture error (response already served): {e}",
                  file=sys.stderr)
        self._log_turn(verdict, ctx, ex_idx, len(mem.store))

    # ---- the [membrane] monologue (thinking channel) ----------------------
    def _gate_monologue(self, verdict: dict, ctx: str | None, query: str) -> str:
        """One [membrane] block: routing decision, what was recalled, and the
        gate notes (re-ask votes, demotions, judge rulings). Streamed into
        opencode's thinking channel BEFORE the model's own reasoning so the
        wall of text is visibly separated: [membrane] lines are the proxy's
        machinery, everything after is the model."""
        mem = self.server.membrane
        v = verdict.get("verdict", "?")
        sim = verdict.get("sim")
        sim_s = f"{sim:.2f}" if isinstance(sim, (int, float)) else "-"
        n = len(mem.store)
        notes = getattr(mem, "gate_notes", None) or []
        lines = [f"[membrane] route={v} sim={sim_s} field={n}"]
        v_verb = {"act": "would-absorb", "act-replayed": "REPLAYED (no model call)",
                  "escalate": "forwarding to model"}.get(v)
        if v == "escalate-with-hint":
            v_verb = "forwarding with demoted memory as hint"
        if v_verb:
            lines.append(f"[membrane] {v_verb}")
        if notes:
            lines.append(f"[membrane] gate: " + "; ".join(notes))
        th_fe = verdict.get("free_energy")
        if isinstance(th_fe, (int, float)):
            lines.append(f"[membrane] field free_energy={th_fe:.1f}")
        if ctx:
            lines.append(f"[membrane] injected {n_ctx_mem(ctx)} memory slice(s)")
        else:
            lines.append("[membrane] no memory injected (nothing close enough)")
        return "\n".join(lines)

    # ---- the internal intercept answer (no model call, never learned) ----
    # One unified server for both families: a fully-buffered recall/state
    # report, delivered buffered or as SSE depending on the request.
    def _serve_intercept_answer(self, model: str, text: str,
                                streaming: bool, thinking: str | None = None) -> None:
        if not streaming:
            msg = {"role": "assistant", "content": text}
            if thinking:
                msg["reasoning_content"] = thinking
            self._json_out({"object": "chat.completion", "created": 0,
                            "model": model,
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": msg}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        try:
            if thinking:
                for chunk in _openai_sse_chunks(model, thinking,
                                                reasoning=True):
                    self.wfile.write(chunk)
            for chunk in _openai_sse_chunks(model, text):
                self.wfile.write(chunk)
            self.wfile.flush()
        except Exception as e:
            print(f"[opencode] intercept stream error: {e}", file=sys.stderr)

    # ---- /membrane/state: the JSON instrument ----
    def _membrane_state(self) -> None:
        srv = self.server
        mem = srv.membrane
        exs = mem.store.exemplars
        out = {
            "backend": srv.backend,
            "embed_model": getattr(mem.embedder, "model", None),
            "field_size": len(exs),
            "safe": sum(1 for e in exs if e.safe),
            "retired": sum(1 for e in exs
                           if (getattr(e, "outcome", "") or "").startswith("compacted@")),
            "notes": sum(1 for e in exs if getattr(e, "outcome", "") == "compact-note"),
            "thermo": thermo_state(mem.store),
            "gate": {"sim_min": mem.sim_min, "dedup_sim": mem.dedup_sim,
                     "demote_threshold": mem.demote_threshold,
                     "reask_window_s": mem.reask_window_s,
                     "vague_filter": mem.vague_filter},
            "retrieval": {"k": srv.rag_k, "rag_sim_min": srv.rag_sim_min,
                          "ctx_chars": srv.rag_ctx_chars},
            "compact": dict(srv.compactor.stats) if srv.compactor else None,
            "compact_model": srv.compact_model,
            "compact_follows_chat": srv.compact_follows_chat,
            "state_intercept": srv.state_intercept,
            "audit_records": _audit_count(mem.audit_path),
            "review_pending": len(mem.review_pending()),
        }
        self._json_out(out)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Membrane field + opencode: a local field-backed coding "
                    "agent (math/state answered internally; model work proxied "
                    "to Ollama).")
    ap.add_argument("--listen", type=int, default=11500,
                    help="port for the membrane OpenAI-compatible proxy")
    ap.add_argument("--backend", default="http://localhost:11434",
                    help="upstream Ollama URL")
    ap.add_argument("--model", default=None, metavar="MODEL",
                    help="the model opencode opens with (default: best "
                         "available from Ollama's tags)")
    ap.add_argument("--field", default="rag-field.json")
    ap.add_argument("--audit", default="rag-audit.jsonl")
    ap.add_argument("--embed-model", default=None, metavar="MODEL",
                    help="the field's embedding model (default: auto -- known "
                         "Ollama embed models, then the selected model; chat "
                         "BLOCKS when nothing can embed)")
    ap.add_argument("-k", dest="k", type=int, default=4,
                    help="memories retrieved per turn")
    ap.add_argument("--rag-sim-min", type=float, default=0.45)
    ap.add_argument("--mem-chars", type=int, default=400)
    ap.add_argument("--ctx-chars", type=int, default=2400)
    ap.add_argument("--rag-mmr-sim", type=float, default=0.90,
                    help="recall-time dedup: a memory this similar to one "
                         "already injected is skipped")
    ap.add_argument("--sim-min", type=float, default=0.85,
                    help="gate absorb threshold (audit-only in RAG mode)")
    ap.add_argument("--demote-threshold", type=int, default=1)
    ap.add_argument("--dedup-sim", type=float, default=0.95)
    ap.add_argument("--no-vague-filter", dest="vague_filter", action="store_false")
    ap.add_argument("--reask-window", type=float, default=300.0)
    ap.add_argument("--no-judge", dest="no_judge", action="store_true",
                    help="disable the demotion judge (default ON: the "
                         "selected chat model adjudicates demotions)")
    ap.add_argument("--judge-model", default=None, metavar="MODEL",
                    help="pin the demotion judge model (default: follows the "
                         "model selected in opencode)")
    ap.add_argument("--compact-every", type=float, default=90.0, metavar="S",
                    help="seconds between cortex compaction cycles (0 disables)")
    ap.add_argument("--compact-min-field", type=int, default=12)
    ap.add_argument("--compact-cluster-min", type=int, default=4)
    ap.add_argument("--compact-cluster-max", type=int, default=8)
    ap.add_argument("--compact-cluster-sim", type=float, default=0.60)
    ap.add_argument("--compact-mem-chars", type=int, default=400)
    ap.add_argument("--compact-model", default=None, metavar="MODEL",
                    help="pin the cortex/distillation model (default: follows "
                         "the model selected in opencode)")
    ap.add_argument("--replay-sim", type=float, default=0.97, metavar="SIM",
                    help="cosine above which an ACT verdict replays the stored "
                         "answer with NO model call (the token saver). "
                         "An instant repeat still takes the re-ask demotion "
                         "path; a settled later repeat gets the free replay. "
                         "0 disables replay (pure observer).")
    ap.add_argument("--no-state-intercept", dest="state_intercept",
                    action="store_false",
                    help="proxy every turn to the model, even membrane math/"
                         "state questions")
    ap.add_argument("--no-sem-intercept", dest="sem_intercept",
                    action="store_false",
                    help="disable the semantic fallback for recall/state "
                         "interception (lexical frames only)")
    ap.add_argument("--no-field-seeds", dest="field_seeds",
                    action="store_false",
                    help="disable the FIELD-GROWN seed layer of the semantic "
                         "intercept gate (fixed canonical seeds only) -- the "
                         "kill switch if the self-grown layer ever misroutes")
    ap.add_argument("--no-gate-monologue", dest="gate_monologue",
                    action="store_false",
                    help="do not stream the [membrane] gate monologue into "
                         "opencode's thinking channel")
    ap.add_argument("--no-launch", dest="launch", action="store_false",
                    help="run only the proxy (don't start opencode)")
    ap.add_argument("passthrough", nargs="*",
                    help="extra args passed to opencode (use -- to separate)")
    args = ap.parse_args()
    passthrough = args.passthrough
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    # In launch mode opencode owns the terminal, so membrane's own stderr
    # (the [opencode]/[rag]/[compact] debug lines) would corrupt the TUI.
    # Redirect it to a log file; the terminal stays clean for opencode.
    _log_fh = None
    if args.launch:
        _log_fh = open("membrane-opencode.log", "a", buffering=1)
        sys.stderr = _log_fh

    backend = args.backend.rstrip("/")
    chat_models = _chat_capable_models(backend)
    if not chat_models:
        print(f"[opencode] WARNING: no chat-capable Ollama models at {backend} "
              f"(is Ollama running?)", file=sys.stderr)
    model = _pick_default_model(chat_models, args.model)
    if model is None and args.model:
        model = args.model          # respect an explicit choice even if unlisted

    if args.no_judge:
        judge = StubJudge()          # demotions always stand
    else:
        # "you = the selected model" for the judge too: the demotion judge is
        # an OllamaJudge whose .model is read LIVE at each call -- it follows
        # srv.compact_model (which follows the opencode selection) unless
        # --judge-model pins it. Errors degrade to ACCEPT, so a broken judge
        # never silences the environment's vote.
        _judge_impl = OllamaJudge(backend=backend,
                                  model=args.judge_model or model or "granite3.3:8b",
                                  timeout=120)

        class _FollowChatJudge:
            """OllamaJudge whose model tracks the live selected model (read
            from the server at call time -- the handler stamps
            srv.compact_model on every chat turn)."""

            def contest(self, situation, action, query, reask_count):
                _judge_impl.model = srv.compact_model
                return _judge_impl.contest(situation, action, query,
                                           reask_count)

        judge = _FollowChatJudge() if not args.judge_model else _judge_impl
    mem = Membrane(
        responder=OllamaChatResponder(backend),
        field_path=args.field,
        audit_path=args.audit,
        embedder=None,          # resolved by the ladder (below)
        sim_min=args.sim_min,
        demote_threshold=args.demote_threshold,
        reask_window_s=args.reask_window,
        judge=judge,
        observer=True,          # never replay: always forward, always learn
        vague_filter=bool(args.vague_filter),
    )
    # Membrane defaults embedder to OllamaEmbedder() when None; we want the
    # lazy resolution ladder instead, so clear it after construction.
    mem.embedder = None

    srv = ThreadingHTTPServer(("127.0.0.1", args.listen), MembraneOpenAIHandler)
    srv.membrane = mem
    srv.backend = backend
    srv.rag_k = args.k
    srv.rag_sim_min = max(0.0, min(1.0, float(args.rag_sim_min)))
    srv.rag_ctx_chars = max(200, int(args.ctx_chars))
    srv.rag_mem_chars = max(64, int(args.mem_chars))
    srv.rag_mmr_sim = max(0.0, min(1.0, float(args.rag_mmr_sim)))
    srv.compact_min_field = max(6, int(args.compact_min_field))
    srv.compact_min_cluster = max(2, int(args.compact_cluster_min))
    srv.compact_max_cluster = max(srv.compact_min_cluster,
                                  int(args.compact_cluster_max))
    srv.compact_cluster_sim = max(0.0, min(1.0, float(args.compact_cluster_sim)))
    srv.compact_mem_chars = max(64, int(args.compact_mem_chars))
    srv.compact_follows_chat = args.compact_model is None
    srv.compact_model = args.compact_model or model or "granite3.3:8b"
    srv.judge_follows_chat = (not args.no_judge) and args.judge_model is None
    srv.pending_embed_error = None
    srv.state_intercept = bool(args.state_intercept)
    srv.sem_intercept = bool(args.sem_intercept)
    srv.field_seeds = bool(args.field_seeds)
    srv.gate_monologue = bool(args.gate_monologue)
    srv.replay_sim = max(0.0, min(1.0, float(args.replay_sim)))
    srv.embed_model_arg = args.embed_model
    srv.resolved_embed_name = args.embed_model
    # the append-ordered store means 'this session' / 'the last hour' (the
    # time-window forget) resolves to everything appended after boot
    srv._boot_field_size = len(mem.store)
    if args.compact_every > 0:
        srv.compactor = Compactor(srv, idle=args.compact_every)
        srv.compactor.start()
    else:
        srv.compactor = None

    # resolve the embedder AT BOOT: the field must be able to embed before a
    # single turn (a fresh field also needs nothing, but the resolver proves
    # the embed role early rather than on the first chat).
    if srv.embed_model_arg:
        emb = _probe_embedder(backend, srv.embed_model_arg)
        if emb is not None:
            mem.embedder = emb
            srv.pending_embed_error = None
    if mem.embedder is None:
        # ladder: known embed models, then the selected chat model
        for cand in _known_embed_names(backend) + ([model] if model else []):
            if cand in [srv.embed_model_arg]:
                continue
            probe = _probe_embedder(backend, cand)
            if probe is not None:
                mem.embedder = probe
                srv.resolved_embed_name = cand
                print(f"[opencode] embedder resolved: {cand}", file=sys.stderr)
                break

    # ---- the opencode config + launch ----
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "provider": {
            "membrane": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Membrane (field)",
                "options": {"baseURL": f"http://127.0.0.1:{args.listen}/v1"},
                "models": {m: {"name": m} for m in chat_models},
            },
        },
    }
    if model:
        cfg["model"] = f"membrane/{model}"
        cfg["small_model"] = f"membrane/{model}"
    fd, cfg_path = tempfile.mkstemp(prefix="membrane-opencode-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
    env = dict(os.environ)
    env["OPENCODE_CONFIG"] = cfg_path

    judge_s = ("off (demotions stand)" if args.no_judge else
               args.judge_model or
               srv.compact_model + (" (follows opencode selection)"
                                    if srv.judge_follows_chat else ""))
    print(f"membrane opencode  http://127.0.0.1:{args.listen}/v1  ->  {backend}\n"
          f"  field={args.field}  audit={args.audit}  field_size={len(mem.store)}\n"
          f"  chain: opencode -> membrane (RAG + learning) -> Ollama\n"
          f"  default model: {model or '?'}  |  cortex model: {srv.compact_model}"
          f"{' (follows opencode selection)' if srv.compact_follows_chat else ' (pinned)'}\n"
          f"  embedder: {getattr(mem.embedder, 'model', None) or 'UNRESOLVED -- chat blocked until an embed model is available'}\n"
          f"  state/math: {'answered internally from the live field' if srv.state_intercept else 'off'}\n"
          f"  replay: {'sim>=' + format(srv.replay_sim, '.2f') + ' -> stored answer, no model call' if srv.replay_sim > 0 else 'off (pure observer)'}\n"
          f"  judge: {judge_s}\n"
          f"  opencode config: {cfg_path}",
          file=sys.stderr)

    # ---- signal + child lifecycle -------------------------------------------
    # In launch mode opencode owns the terminal: ctrl+c must interrupt opencode
    # (clear the current message), NOT kill membrane. So membrane IGNORES SIGINT
    # and instead shuts itself down when the opencode child exits (polled below)
    # or on SIGTERM. In proxy-only mode there is no child, so SIGINT is the
    # normal "stop the server" signal and is handled as before.
    def _term(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    if args.launch:
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    child = None
    try:
        if args.launch:
            opencode = shutil.which("opencode")
            if opencode is None:
                print("[opencode] `opencode` not on PATH; proxy-only "
                      "(point opencode at the /v1 URL above)", file=sys.stderr)
            else:
                extra = []
                if model and not any(a.startswith("--model") or a.startswith("-m")
                                     for a in passthrough):
                    extra = ["--model", f"membrane/{model}"]
                cmd = [opencode] + extra + list(passthrough)
                print(f"[opencode] launching: {' '.join(cmd)}", file=sys.stderr)
                child = subprocess.Popen(cmd, env=env)
        if child is None:
            # proxy-only: block forever, SIGINT/SIGTERM raise KeyboardInterrupt
            srv.serve_forever()
        else:
            # launch mode: serve until the opencode child exits (or a signal).
            # serve_forever() blocks, so poll the child on a short timeout loop.
            srv.timeout = 0.25
            while child.poll() is None:
                srv.handle_request()
    except KeyboardInterrupt:
        print("\n[membrane-opencode] bye", file=sys.stderr)
    finally:
        if child is not None:
            try:
                child.terminate(); child.wait(timeout=10)
            except Exception:
                pass
        if srv.compactor is not None:
            srv.compactor.stop()
        for p in (cfg_path,):
            try:
                os.unlink(p)
            except OSError:
                pass
        srv.server_close()
        if _log_fh is not None:
            try:
                _log_fh.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()