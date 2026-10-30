"""LEARNING LIVES IN THE VM (#576 part C): Blurp's plasticity, as field physics.

GUESS. A write W (by anyone but a trigger) is explained by a cause C: one of the
last LOOK_BACK writes before it in the SAME chain. The guess is a trigger owned
by self:
    same entity:  WHEN changed(?x.c) WHERE isa(?x, <its class>): ?x.w = <value>
    another one:  WHEN changed(?x.c) WHERE isa(?x, <class>), is(?y, <eid>): ?y.w = <value>
The value is the simplest expression that would have produced W: a copy of the
cause (`x.c`) when the values match, else the literal (scalars only). A guess
already defined is not defined again.

SHADOW. A candidate is born in shadow: when it fires, it writes its prediction
onto ITSELF (`predicted` = {path, value, chain}), never onto the real path.

SCORE. A later write in the same chain to the predicted path, by anyone but the
candidate, with the predicted value is a hit; any other value, or no such
write, is not -- and needs no timer: only hits are refunded.

ENERGY. Every Blurp write costs WRITE_COST (one awake second, BASAL_PER_S):
defining a candidate, each shadow or live firing. A hit refunds REFUND x
WRITE_COST to Blurp and to the candidate's balance. The PURSE is Blurp's energy
(charge / refund / can_spend -- asleep, nothing is spent, so nothing is learned
or fired); part D2 hands in the real one.

STAY POSITIVE. A candidate whose balance is above zero is a proposal; on a
granted branch (granted(path), the skill tree's Accept -- D2) a proposal fires
live: it writes the real path. Below zero it is back in shadow. Sleep prunes
every candidate whose balance is negative (prune())."""
from __future__ import annotations

import collections
import hashlib
import json

from . import ch as CH
from .energy import BASAL_PER_S

WRITE_COST = BASAL_PER_S          # every Blurp write: one awake second (approved)
REFUND = 2.0                      # a hit pays back 2x its write cost (approved)
LOOK_BACK = 3                     # the causes a guess may look back at, in its chain (approved)
CHAINS_KEPT = 512                 # chains remembered for causes and predictions (a buffer bound)
GUESS_PER_CHAIN = 3               # new guesses one chain may define (operator, #582 review)
CANDIDATES_MAX = 256              # live candidates at most; at the cap a new guess replaces the most negative (operator)
REFUSED_KEPT = 4096               # guesses the VM refused, remembered so they are never retried (a buffer bound)


def _scalar(v) -> bool:
    return isinstance(v, (int, float, str, bool)) or v is None


def branch_of(path: str, value=None) -> str:
    """The kind + type a target belongs to (#576 D2's skills; the operator's grain):
    `membrane::Component:toy:die.b` -> `Component:toy`, `membrane::ha:light.x` ->
    `ha:light`; a learned call's branch is the called object's."""
    if isinstance(value, dict) and value.get("object"):
        path = str(value["object"])
    rest = str(path).split("::", 1)[1] if "::" in str(path) else str(path)
    parts = rest.split(":")
    if len(parts) == 1:
        return parts[0].split(".")[0]
    return f"{parts[0]}:{parts[1].split('.')[0]}"     # kind + type (operator): ha:light is not ha:lock


def _same(a, b) -> bool:
    """A copy, not a coincidence: equal AND the same type (True is not 1)."""
    return _scalar(a) and type(a) is type(b) and a == b


def _literal(v) -> str | None:
    if not _scalar(v):
        return None
    s = repr(v)
    return s if len(s) <= 200 else None


class Learner:
    def __init__(self, vm, purse, granted=lambda path: False, suggest=None, adopted=None):
        self.vm = vm
        self.purse = purse
        self.granted = granted
        self.suggest = suggest           # (trigger, target path, value): a proposal's suggestion to the LLM
        self.adopted = adopted           # (write) -> the candidates whose suggestion it adopts (#583: a later turn)
        self._recent: collections.OrderedDict = collections.OrderedDict()   # chain -> its last writes
        self._pending: collections.OrderedDict = collections.OrderedDict()  # chain -> [(cand, path, value)]
        self._bal: dict[str, float] = {}
        self._hits: dict[str, int] = {}
        self._guessed: collections.OrderedDict = collections.OrderedDict()  # chain -> new guesses defined
        self._refused: collections.OrderedDict = collections.OrderedDict()  # guess eids the VM refused
        self.counts: collections.Counter = collections.Counter()   # what it saw and why it passed (the card shows it)

    # -- the account ------------------------------------------------------------------------
    def balance(self, cand: str) -> float:
        return self._bal.get(cand, 0.0)

    def _hit(self, cand: str) -> None:
        self.counts["hit"] += 1
        back = REFUND * WRITE_COST
        self.purse.refund(back, "hit")
        self._bal[cand] = self.balance(cand) + back
        self._hits[cand] = self.hits(cand) + 1

    def hits(self, cand: str) -> int:
        return self._hits.get(cand, 0)

    def state(self, cand: str) -> str:
        return "proposal" if self.balance(cand) > 0 else "shadow"

    def _spend(self, cand: str, why: str) -> bool:
        if not self.purse.can_spend():
            return False
        self.purse.charge(WRITE_COST, why)
        self._bal[cand] = self.balance(cand) - WRITE_COST
        return True

    # -- the VM's hooks ---------------------------------------------------------------------
    @staticmethod
    def _expected(target_path: str) -> str:
        """A learned call writes `<method>.invoke`; what it predicts is the call the LLM
        records, `<method>.last_call`."""
        return target_path[:-len(".invoke")] + ".last_call" if target_path.endswith(".invoke") else target_path

    def on_fire(self, tr, target_path: str, value) -> str:
        """A candidate fires: 'live' (write the real path), 'shadow' (write the
        prediction onto itself) or 'skip' (asleep)."""
        grant_path = (str(value["object"]) + ".invoke"
                      if target_path.endswith(".invoke") and isinstance(value, dict) and value.get("object")
                      else target_path)                  # a call acts on its object's branch
        suggests = self.state(tr.eid) == "proposal" and self.granted(grant_path)   # as it stood before this write
        if not self._spend(tr.eid, "fire"):
            self.counts["fire:skip"] += 1
            return "skip"
        self.counts["fire"] += 1
        ch = (CH.W.current() or {}).get("id")
        if ch:                                  # no chain, nothing can confirm it: no prediction kept
            self._remember(self._pending, ch, list).append((tr.eid, self._expected(target_path), value))
        if suggests and self.suggest is not None:
            # the operator (#576 H): Blurp never acts by itself while the LLM works -- a
            # proposal on an accepted branch is a SUGGESTION; the LLM adopting it is the hit
            try:
                self.suggest(tr, target_path, value)
            except Exception:
                pass
        return "shadow"

    @staticmethod
    def _remember(od, key, make):
        if key in od:
            od.move_to_end(key)
        else:
            od[key] = make()
            while len(od) > CHAINS_KEPT:
                od.popitem(last=False)
        return od[key]

    def observe(self, w: dict) -> None:
        """A write the VM drained: score the predictions on its path, then guess."""
        writer = str(w.get("writer") or "")
        path = str(w.get("path") or "")
        ch = (w.get("chain") or {}).get("id") or ""
        n = self.counts
        n["seen"] += 1
        why = ("own_write" if writer.startswith(("trigger:", "adapter:")) else "no_prop" if not w.get("prop")
               else "rel" if path.startswith("rel:") else "no_chain" if not ch else "")
        if why:
            n["pass:" + why] += 1
            if why == "no_chain":                       # #721: who writes outside any chain (the writer's kind)
                n["no_chain:" + (writer.split(":", 1)[0] or "?")] += 1
            return                                      # Blurp's own acts never confirm or teach themselves
        by_llm = writer == "llm" or writer.startswith("llm:")   # the LLM's actions: taught, and confirming
        n["by_llm" if by_llm else "by_other"] += 1
        paid = set()
        if by_llm and ch in self._pending:
            keep = []
            for cand, p, v in self._pending[ch]:
                if p != path:
                    keep.append((cand, p, v))
                    continue
                if v == w.get("new"):
                    self._hit(cand)
                    paid.add(cand)
            self._pending[ch] = keep
        if by_llm and self.adopted is not None:
            try:
                for cand in self.adopted(w):         # a suggestion shown, adopted in a later turn
                    if cand not in paid:
                        self._hit(cand)
                        paid.add(cand)
            except Exception:
                pass
        recent = self._remember(self._recent, ch, lambda: collections.deque(maxlen=LOOK_BACK))
        causes = list(recent)
        recent.append(w)
        if by_llm:
            for c in causes:
                self._guess(c, w, ch)

    # -- guessing ---------------------------------------------------------------------------
    def _guess(self, c: dict, w: dict, ch: str) -> None:
        n = self.counts
        if not self.purse.can_spend():
            n["guess:asleep"] += 1
            return
        if self._guessed.get(ch, 0) >= GUESS_PER_CHAIN:
            n["guess:chain_full"] += 1
            return
        est = self.vm.est
        c_eid, c_prop = str(c.get("eid") or ""), str(c.get("prop") or "")
        w_eid, w_prop = str(w.get("eid") or ""), str(w.get("prop") or "")
        ce, we = (est.docs or {}).get(c_eid), (est.docs or {}).get(w_eid)
        if ce is None or we is None or not c_prop or not w_prop:
            n["guess:no_entity"] += 1
            return
        if ce.cls == CH.CLASS or we.cls == CH.CLASS:
            n["guess:trigger"] += 1
            return
        if c_prop == "position" or w_prop == "position":
            n["guess:position"] += 1
            return                                     # vectors: the geometric watchers' business
        if not c_prop.isidentifier() or not w_prop.isidentifier():
            n["guess:odd_name"] += 1
            return                                     # x.odd-name would read as subtraction
        n["guess"] += 1
        same = c_eid == w_eid
        where = [["isa", "?x", ce.cls]] + ([] if same else [["is", "?y", w_eid]])
        if w_prop == "last_call" and isinstance(w.get("new"), dict):
            # a method call: the guess CALLS it (writes `<method>.invoke`), its object and
            # arguments copied from the cause where they match, literal otherwise
            target = "?x.invoke" if same else "?y.invoke"
            values = [self._call_expr(w["new"], c_eid, c_prop, c.get("new"))]
        else:
            target = f"?x.{w_prop}" if same else f"?y.{w_prop}"
            values = []
            if _same(w.get("new"), c.get("new")):
                values.append(f"x.{c_prop}")
            lit = _literal(w.get("new"))
            if lit is not None:
                values.append(lit)
        for expr in values:
            spec = {"when": [["changed", f"?x.{c_prop}"]], "where": where, "write": [target, expr]}
            name = "cand-" + hashlib.blake2b(json.dumps(spec, sort_keys=True).encode(),
                                             digest_size=6).hexdigest()
            eid = CH.MEMBRANE_NS + f"{CH.CLASS}:{name}"
            if eid in (est.docs or {}) or eid in self._refused:
                continue
            if self._guessed.get(ch, 0) >= GUESS_PER_CHAIN or not self.purse.can_spend() or not self._room():
                return
            try:
                self.vm.define(name, owner=CH.SELF, **spec)
            except CH.Refused:
                n["refused"] += 1
                self._remember(self._refused, eid, lambda: True)     # never retried, never charged
                while len(self._refused) > REFUSED_KEPT:
                    self._refused.popitem(last=False)
                continue
            self._spend(eid, "define")                  # charged only for what was defined
            n["defined"] += 1
            self._guessed[ch] = self._guessed.get(ch, 0) + 1
            while len(self._guessed) > CHAINS_KEPT:
                self._guessed.popitem(last=False)
            doc = est.docs[eid]
            doc.properties["shadow"] = True
            doc.properties["branch"] = branch_of(w_eid, w.get("new") if w_prop == "last_call" else None)
            est.upsert(doc)

    def _room(self) -> bool:
        """Under CANDIDATES_MAX, or room made by removing the most negative one."""
        mine = [t for t in self.vm.triggers() if (t.properties or {}).get("owner") == CH.SELF]
        if len(mine) < CANDIDATES_MAX:
            return True
        worst = min(mine, key=lambda t: self.balance(t.eid))
        if self.balance(worst.eid) >= 0:
            return False                                # every candidate pays for itself: no room
        self.vm.est.remove(worst.eid)
        self._bal.pop(worst.eid, None)
        return True

    @staticmethod
    def _call_expr(call: dict, c_eid: str, c_prop: str, c_new) -> str:
        obj = "x.eid" if call.get("object") == c_eid else repr(call.get("object"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        parts = [f"{k!r}: " + (f"x.{c_prop}" if _same(v, c_new) else repr(v)) for k, v in args.items()]
        return "{'object': " + obj + ", 'args': {" + ", ".join(parts) + "}}"

    # -- sleep ------------------------------------------------------------------------------
    def prune(self) -> list[str]:
        """Sleep: every candidate below zero goes (most draining first)."""
        est = self.vm.est
        bad = sorted((self.balance(t.eid), t.eid) for t in self.vm.triggers()
                     if (t.properties or {}).get("owner") == CH.SELF and self.balance(t.eid) < 0)
        gone = []
        for _b, eid in bad:
            if est.remove(eid):
                gone.append(eid)
            self._bal.pop(eid, None)
        return gone
