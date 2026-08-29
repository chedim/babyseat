"""The character ledger: who is in the world, and where each of them IS.

A character is NOT a row in a table; it is a *walking region* of the field. Its
``state_vec`` is the exponentially-weighted moving average of the chronicle scenes
tagged to it -- the tip of that walk. State lives in the field (the scenes are
real exemplars with ``tags``), and this ledger only makes the cast *addressable*
by name and tracks each character's rolling state.

Tagging is geometric: a scene is tagged to every character whose ``state_vec`` it
sits close to (cosine >= ``admit_threshold``). There is NO automatic admission --
a scene far from all characters is left untagged, so the cast stays short.
Characters are seeded once by the genesis chapter (the LLM names who it
introduced) and thereafter managed by hand from the menu (retire / pin).

This is the SPINE + the emergence/voice layer on top of it:

  * characters emerge and walk (the EMA ``tag`` below);
  * a stranger that recurs becomes a character (``note_stranger`` -- the field's
    own emergence, no model; the name is the field's most-repeated salient word);
  * a character that has lived enough "starts thinking" -- its own per-character
    n-gram voice (``voice_for``), which cells-act authors beats from;
  * the on-stage character (``on_stage``) is whoever the narrative is freshest in
    -- the fade is just "stop POVing a character that fell out of the narrative,"
    * not* retirement.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


# ---- emergence thresholds (the two-stage "a stranger becomes a character that
# starts thinking") -----------------------------------------------------------
# A beat that tags NO existing character is a "stranger"; it accumulates in an
# in-memory scratch buffer. When a recurring region (a cluster of strangers
# within ``_STRANGER_BAND`` cosine) reaches ``_ADMIT_RECURRENCE`` scenes, it is
# auto-admitted as a character -- the field's own emergence, no model. The name
# is the field's most-repeated salient word about that region (see ``_field_name``).
# A character "starts thinking" (authors beats in cells-act from its own
# per-character n-gram) only once it has lived ``_VOICE_MIN`` tagged scenes;
# until then it is just narrated.
_ADMIT_RECURRENCE = 3
_STRANGER_BAND = 0.55
_STRANGER_CAP = 64
_VOICE_MIN = 5
# words the field says a lot but that are not a figure's name -- dropped before
# the salient-noun frequency count names a stranger.
_NAME_STOP = frozenset("""
the a an and or but if then else of to in on at by for with from into over under
is are was were be been being am do does did done have has had having will would
can could should may might must shall i you he she it we they him her them us me
my your his its our their this that these those there here where when why how what
who whom which all any some no not yes very just only also too more most much many
said says say tell told asks asked one two three him her she he they them
out up down back away off again still now then soon later here there
""".split())


@dataclass
class Character:
    id: int
    name: str
    state_vec: list[float]       # unit; the EMA of this char's tagged scenes
    anchor_idx: int | None       # the exemplar that introduced them (for "zoom to")
    born_cycle: int
    last_cycle: int
    scene_count: int
    pinned: bool = False
    # a one-line role + a VOICE description (how this character speaks/thinks).
    # Empty for legacy / chat-seeded chars; populated by the genesis seed (which
    # names the ensemble "Name — role — voice") or the --protagonist flag. The
    # POV character's voice is what the narrator speaks in. Backward-compatible:
    # old ad521bb cast records lack the keys -> Exemplar(**rec)... Character(**rec)
    # falls back to these defaults.
    role: str = ""
    voice: str = ""
    # the text of the most recent scene that tagged this character -- their
    # "current situation", surfaced in the viewer's snapshot panel. Updated by
    # Cast.tag on every feed that touches them. Empty until first tagged.
    last_scene: str = ""


class Cast:
    """The cast list + the tagging/state machinery."""

    def __init__(self, decay: float = 0.6, admit_threshold: float = 0.32,
                 cap: int = 12, embedder=None) -> None:
        self.chars: list[Character] = []
        self._next_id = 0
        self.decay = decay
        self.admit_threshold = admit_threshold
        self.cap = cap
        self.embedder = embedder      # set by the Membrane after construction
        self.pov_id: int | None = None   # the protagonist (who the reader IS)
        # the world's own clock (the consolidation cycle). In-memory on the proxy
        # (``srv.consolidation_stats["cycles"]``) but mirrored here so it persists
        # across restarts in the cast wrapper -- otherwise every restart drops the
        # in-world date back to "cycle 0" while the field still holds 100+ scenes.
        # Kept in sync by the proxy at every cycle increment; persisted whenever the
        # cast is saved (every tag/walk/adjust/cast action), so the lag is tiny.
        self.cycles: int = 0
        # ---- emergence (in-memory, never serialized) ----
        # untagged beats accumulate here; a recurring region is auto-admitted.
        self._strangers: list[dict] = []
        # per-character n-gram voices: cid -> FieldNGram, rebuilt when the char
        # lives more. A character "starts thinking" once it has >= _VOICE_MIN
        # tagged scenes; ``_voice_size`` tracks the scene_count each was built at.
        self._voices: dict[int, object] = {}
        self._voice_size: dict[int, int] = {}

    # ---- addressing ------------------------------------------------------
    def by_id(self, cid: int) -> Character | None:
        for c in self.chars:
            if c.id == cid:
                return c
        return None

    def _fresh_id(self) -> int:
        cid = self._next_id
        self._next_id += 1
        return cid

    def by_name(self, name: str) -> Character | None:
        n = (name or "").strip().strip("\"'.,;:")
        for c in self.chars:
            if c.name == n:
                return c
        return None

    def set_pov(self, cid: int) -> bool:
        """Designate ``cid`` the protagonist (who the reader IS). The narrator
        speaks from this character's region of the field, in their voice."""
        if self.by_id(cid) is None:
            return False
        self.pov_id = cid
        return True

    # ---- admission -------------------------------------------------------
    def admit(self, name: str, vec, anchor_idx: int | None, cycle: int,
              role: str = "", voice: str = "") -> Character | None:
        """Create a new character seeded by ``vec`` (a description or scene embedding).

        Respects ``cap``: a full cast refuses admission. The caller (genesis seed
        or a future menu action) is responsible for choosing the name.
        """
        if len(self.chars) >= self.cap:
            return None
        sv = _unit(np.asarray(vec, dtype=float)).tolist()
        c = Character(id=self._fresh_id(), name=name, state_vec=sv,
                      anchor_idx=anchor_idx, born_cycle=cycle,
                      last_cycle=cycle, scene_count=0, role=role, voice=voice)
        self.chars.append(c)
        return c

    # ---- the core: tag a scene to its cast + walk each tagged state -------
    def tag(self, vec, cycle: int, text: str = "") -> list[int]:
        """Tag ``vec`` (a scene embedding) to every character it sits close to,
        EMA-updating each tagged character's ``state_vec`` toward the scene, and
        recording ``text`` as the character's ``last_scene`` (their current
        situation, shown in the viewer's snapshot panel).

        Returns the list of character ids tagged (possibly empty). Does NOT
        auto-admit: a scene far from the whole cast returns ``[]`` and the cast
        does not grow (keeps the list short; admission is seed/manual only).
        """
        if not self.chars:
            return []
        sv = _unit(np.asarray(vec, dtype=float))
        M = np.array([c.state_vec for c in self.chars], dtype=float)
        Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-12)
        sims = Mn @ sv
        tagged: list[int] = []
        for i, c in enumerate(self.chars):
            if float(sims[i]) >= self.admit_threshold:
                old = np.asarray(c.state_vec, dtype=float)
                walked = _unit(self.decay * old + (1.0 - self.decay) * sv)
                c.state_vec = walked.tolist()
                c.scene_count += 1
                c.last_cycle = cycle
                if text:
                    c.last_scene = text
                tagged.append(c.id)
        return tagged

    def walk_pov(self, vec, cycle: int) -> bool:
        """The I is the ground: unconditionally EMA-walk the POV protagonist's
        ``state_vec`` toward ``vec`` -- bypassing the cosine ``admit_threshold``
        that gates every OTHER scene. The user IS the POV character, so their
        thought walks the character's state *by fiat*, not by similarity. The
        field FOLLOWS the I; it does not check it. No exemplar is appended here
        (no recall loop, no echo of the user's own words); this only moves the
        POV's region, so the gardener's future scenes accrue where the user
        steers and the narrator speaks from there. Returns False when no POV."""
        if self.pov_id is None:
            return False
        c = self.by_id(self.pov_id)
        if c is None:
            return False
        sv = _unit(np.asarray(vec, dtype=float))
        old = np.asarray(c.state_vec, dtype=float)
        c.state_vec = _unit(self.decay * old + (1.0 - self.decay) * sv).tolist()
        c.scene_count += 1
        c.last_cycle = cycle
        return True

    def walk_all(self, vec, cycle: int, text: str = "") -> int:
        """The I speaks to the whole world: unconditionally EMA-walk EVERY
        character's ``state_vec`` toward ``vec`` -- bypassing the cosine
        ``admit_threshold`` that gates ``tag``. Used when no POV is selected, so
        the player's thought is mixed into the world field of ALL characters at
        once (a gentle, uniform nudge to every region), not targeted at one
        protagonist. Like ``walk_pov`` this appends NO exemplar (no recall loop,
        no echo of the user's words); it only moves each region -- the future
        chronicle accrues where the player steered. Returns the number of
        characters walked (0 for an empty cast)."""
        if not self.chars:
            return 0
        sv = _unit(np.asarray(vec, dtype=float))
        for c in self.chars:
            old = np.asarray(c.state_vec, dtype=float)
            c.state_vec = _unit(self.decay * old + (1.0 - self.decay) * sv).tolist()
            c.scene_count += 1
            c.last_cycle = cycle
            if text:
                c.last_scene = text
        return len(self.chars)

    def retro_tag(self, store) -> int:
        """One-time, after the genesis seed: tag every existing exemplar to the
        cast from its STORED vec (no embed call), in field order (oldest first,
        so each char's ``state_vec`` walks forward to the most recent scene).

        Returns the number of exemplars tagged to at least one character.
        """
        n = 0
        for ex in store.exemplars:
            ids = self.tag(ex.vec, cycle=getattr(ex, "_cycle", 0) or 0)
            if ids:
                ex.tags = ids
                n += 1
        return n

    # ---- emergence: a stranger becomes a character ----------------------
    def note_stranger(self, vec, cycle: int, text: str = "") -> "Character | None":
        """A beat that tagged NO existing character (``tag`` returned ``[]``).
        Track it in the scratch ``_strangers`` buffer; if a recurring region (a
        cluster of strangers within ``_STRANGER_BAND`` cosine) reaches
        ``_ADMIT_RECURRENCE`` scenes, auto-admit it as a character -- the field's
        own emergence, NO model. The name is the field's most-repeated salient
        word about that region (``_field_name``): the field has already named the
        figure by saying it over and over, so we read that back. Returns the new
        Character (and evicts the graduated cluster), or None. Respects ``cap``;
        a full cast simply keeps accumulating strangers without admitting.

        No retro-tag of past scenes: the new char's ``state_vec`` is the region's
        centroid, so FUTURE beats in that region tag to it and it accrues from
        here -- it "starts thinking" once it has lived ``_VOICE_MIN`` of them."""
        sv = _unit(np.asarray(vec, dtype=float))
        self._strangers.append({"vec": sv.tolist(), "cycle": int(cycle),
                                 "text": text or ""})
        if len(self._strangers) > _STRANGER_CAP:
            self._strangers = self._strangers[-_STRANGER_CAP:]
        # need both a seeded cast and enough accumulated strangers to cluster
        if not self.chars or len(self._strangers) < _ADMIT_RECURRENCE:
            return None
        M = np.array([s["vec"] for s in self._strangers], dtype=float)
        Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-12)
        sims = Mn @ sv
        cluster_idx = [i for i, s in enumerate(sims)
                       if float(s) >= _STRANGER_BAND]
        if len(cluster_idx) < _ADMIT_RECURRENCE or len(self.chars) >= self.cap:
            return None
        cluster = [self._strangers[i] for i in cluster_idx]
        centroid = _unit(np.mean(
            [np.asarray(c["vec"], dtype=float) for c in cluster], axis=0))
        name = self._field_name(cluster)
        c = self.admit(name, centroid, anchor_idx=None, cycle=cycle,
                       role="", voice="")
        if c is not None:
            # the region graduated into a character; drop its strangers
            gone = set(cluster_idx)
            self._strangers = [s for i, s in enumerate(self._strangers)
                               if i not in gone]
            print(f"[cast] stranger admitted: {c.name} "
                  f"(recurrence {len(cluster)}); cast={len(self.chars)}",
                  file=sys.stderr)
        return c

    def _field_name(self, cluster: list[dict]) -> str:
        """The field's name for a recurring region: the most-repeated salient word
        across the cluster's scene texts (NO model). The field has named the
        figure by saying it; we tokenize, drop the stoplist + existing cast names
        + short tokens, and take the winner, title-cased. Falls back to a
        positional 'Stranger {next_id}' when nothing salient remains."""
        from .generator import _tok as _gen_tokens
        existing = {c.name.lower().strip() for c in self.chars if c.name}
        counts: Counter = Counter()
        for s in cluster:
            for tok in _gen_tokens(s.get("text", "")):
                if len(tok) < 3 or tok in _NAME_STOP or tok in existing:
                    continue
                counts[tok] += 1
        if counts:
            winner = counts.most_common(1)[0][0]
            return winner[:1].upper() + winner[1:]
        return f"Stranger {self._next_id}"

    # ---- the on-stage character + per-character voice (cells-act) -------
    def on_stage(self) -> "Character | None":
        """The freshest character -- the one most recently in the narrative
        (``max last_cycle``), tiebreak the POV. This IS the fade: when the POV's
        ``last_cycle`` goes stale (it fell out of the narrative), the chronicle
        follows whoever is freshest instead -- 'stopping POVing them' without
        retiring them (the POV stays in the cast, its stars keep their color;
        it is simply no longer seeded/authored-from). When nothing has been
        tagged yet (all ``last_cycle == 0``), follow the POV (or None)."""
        if not self.chars:
            return None
        freshest = max(self.chars, key=lambda c: c.last_cycle)
        if freshest.last_cycle == 0:
            pov = self.by_id(self.pov_id) if self.pov_id is not None else None
            return pov
        pov = self.by_id(self.pov_id) if self.pov_id is not None else None
        if pov is not None and pov.last_cycle == freshest.last_cycle:
            return pov
        return freshest

    def voice_for(self, cid: int, store) -> "object | None":
        """The per-character n-gram voice (cells-act). A character 'starts
        thinking' only once it has lived enough (``>= _VOICE_MIN`` tagged
        scenes); before that returns None -- 'until then we just narrate it'.
        Trained on the character's own tagged ``Exemplar.action``s (the chronicle
        scenes they lived), cached and rebuilt when they live more. Reuses
        ``FieldNGram`` unchanged. Returns None too when the lived scenes are too
        few distinct actions to speak from.

        Only TRUSTED (LLM-seeded) scenes train the voice -- self-authored beats
        (the cell's own n-gram stammer, flagged ``self_authored``) are absorbed
        into the field but excluded here, so the voice speaks from real lived
        material and can't spiral on its own output."""
        from .generator import FieldNGram, MIN_ACTIONS
        c = self.by_id(cid)
        if c is None or c.scene_count < _VOICE_MIN:
            return None
        size = c.scene_count
        if self._voice_size.get(cid, -1) == size and cid in self._voices:
            return self._voices[cid]
        actions = [e.action for e in store.exemplars
                   if cid in getattr(e, "tags", []) and e.action
                   and not getattr(e, "self_authored", False)]
        if len(set(actions)) < MIN_ACTIONS:
            return None
        lm = FieldNGram(actions)
        self._voices[cid] = lm
        self._voice_size[cid] = size
        return lm

    # ---- management (menu) ----------------------------------------------
    def retire(self, cid: int, store) -> bool:
        """Remove a character AND strip its id from every exemplar's ``tags``
        (its stars revert to untagged/neutral). Returns True if a character was
        removed. Pinned characters are refused unless ``force``."""
        c = self.by_id(cid)
        if c is None or c.pinned:
            return False
        self.chars = [ch for ch in self.chars if ch.id != cid]
        for ex in store.exemplars:
            if cid in ex.tags:
                ex.tags = [t for t in ex.tags if t != cid]
        if self.pov_id == cid:        # retiring the protagonist -> narrator falls back
            self.pov_id = None
        return True

    def pin(self, cid: int) -> bool:
        c = self.by_id(cid)
        if c is None:
            return False
        c.pinned = not c.pinned
        return True

    # ---- persistence -----------------------------------------------------
    def to_json(self) -> list[dict]:
        return [asdict(c) for c in self.chars]

    def save(self, path: str | Path) -> None:
        # wrapper shape so the protagonist pointer (pov_id) + the world clock
        # (cycles) persist alongside the cast. ``to_json`` (the /state + /cast
        # response) stays the bare list the viewer already reads; only the on-disk
        # file gains the wrapper.
        Path(path).write_text(json.dumps(
            {"pov_id": self.pov_id, "cycles": self.cycles,
             "chars": self.to_json()}, indent=2))

    @classmethod
    def load(cls, path: str | Path, decay: float = 0.6,
             admit_threshold: float = 0.32, cap: int = 12, embedder=None) -> "Cast":
        cast = cls(decay=decay, admit_threshold=admit_threshold, cap=cap,
                   embedder=embedder)
        p = Path(path)
        if p.exists():
            text = p.read_text()
            try:
                data = json.loads(text) if text.strip() else []
            except json.JSONDecodeError:
                data = []
            # tolerate BOTH the new wrapper {"pov_id":..,"chars":[..]} and the
            # bare list (old ad521bb cast files) -> no migration needed.
            recs = data["chars"] if isinstance(data, dict) else data
            pov_id = data.get("pov_id") if isinstance(data, dict) else None
            cycles = data.get("cycles", 0) if isinstance(data, dict) else 0
            for rec in recs:
                try:
                    cast.chars.append(Character(**rec))
                except TypeError:
                    continue
            cast._next_id = (max((c.id for c in cast.chars), default=-1) + 1)
            # validate the protagonist pointer AFTER the cast is loaded (a retired
            # char's stale pointer -> drop it, so the narrator falls back to global)
            cast.pov_id = pov_id if (pov_id is not None and cast.by_id(pov_id)) else None
            # restore the world clock (0 for old bare-list files -> starts at 0)
            try:
                cast.cycles = int(cycles)
            except (TypeError, ValueError):
                cast.cycles = 0
        return cast