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


def _scalar(v) -> bool:
    return isinstance(v, (int, float, str, bool)) or v is None


def _literal(v) -> str | None:
    if not _scalar(v):
        return None
    s = repr(v)
    return s if len(s) <= 200 else None


class Learner:
    def __init__(self, vm, purse, granted=lambda path: False):
        self.vm = vm
        self.purse = purse
        self.granted = granted
        self._recent: collections.OrderedDict = collections.OrderedDict()   # chain -> its last writes
        self._pending: collections.OrderedDict = collections.OrderedDict()  # chain -> [(cand, path, value)]
        self._bal: dict[str, float] = {}

    # -- the account ------------------------------------------------------------------------
    def balance(self, cand: str) -> float:
        return self._bal.get(cand, 0.0)

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
        """A learned call writes `<method>.call`; what it predicts is the call the LLM
        records, `<method>.last_call`."""
        return target_path[:-len(".call")] + ".last_call" if target_path.endswith(".call") else target_path

    def on_fire(self, tr, target_path: str, value) -> str:
        """A candidate fires: 'live' (write the real path), 'shadow' (write the
        prediction onto itself) or 'skip' (asleep)."""
        live = self.state(tr.eid) == "proposal" and self.granted(target_path)   # as it stood before this write
        if not self._spend(tr.eid, "fire"):
            return "skip"
        ch = CH.W.current() or {}
        self._remember(self._pending, ch.get("id") or "", list).append((tr.eid, self._expected(target_path), value))
        return "live" if live else "shadow"     # a live write is confirmed the same way as a shadow one

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
        if writer.startswith(("trigger:", "adapter:")) or not w.get("prop") or path.startswith("rel:"):
            return                                      # Blurp's own acts never confirm or teach themselves
        keep = []
        for cand, p, v in self._pending.get(ch, []):
            if p != path:
                keep.append((cand, p, v))
                continue
            if v == w.get("new"):
                back = REFUND * WRITE_COST
                self.purse.refund(back, "hit")
                self._bal[cand] = self.balance(cand) + back
        if ch:
            if ch in self._pending:
                self._pending[ch] = keep
            recent = self._remember(self._recent, ch, lambda: collections.deque(maxlen=LOOK_BACK))
            causes = list(recent)
            recent.append(w)
            if writer == "llm" or writer.startswith("llm:"):   # the target: the LLM's actions (operator, 10-26)
                for c in causes:
                    self._guess(c, w)

    # -- guessing ---------------------------------------------------------------------------
    def _guess(self, c: dict, w: dict) -> None:
        if not self.purse.can_spend():
            return
        est = self.vm.est
        c_eid, c_prop = str(c.get("eid") or ""), str(c.get("prop") or "")
        w_eid, w_prop = str(w.get("eid") or ""), str(w.get("prop") or "")
        ce, we = (est.docs or {}).get(c_eid), (est.docs or {}).get(w_eid)
        if ce is None or we is None or not c_prop or not w_prop or ce.cls == CH.CLASS or we.cls == CH.CLASS:
            return
        if c_prop == "position" or w_prop == "position":
            return                                     # vectors: the geometric watchers' business
        same = c_eid == w_eid
        where = [["isa", "?x", ce.cls]] + ([] if same else [["is", "?y", w_eid]])
        if w_prop == "last_call" and isinstance(w.get("new"), dict):
            # a method call: the guess CALLS it (writes `<method>.call`), its object and
            # arguments copied from the cause where they match, literal otherwise
            target = f"?x.call" if same else f"?y.call"
            values = [self._call_expr(w["new"], c_eid, c_prop, c.get("new"))]
        else:
            target = f"?x.{w_prop}" if same else f"?y.{w_prop}"
            values = []
            if w.get("new") == c.get("new") and _scalar(w.get("new")):
                values.append(f"x.{c_prop}")
            lit = _literal(w.get("new"))
            if lit is not None:
                values.append(lit)
        for expr in values:
            spec = {"when": [["changed", f"?x.{c_prop}"]], "where": where, "write": [target, expr]}
            name = "cand-" + hashlib.blake2b(json.dumps(spec, sort_keys=True).encode(),
                                             digest_size=6).hexdigest()
            eid = CH.MEMBRANE_NS + f"{CH.CLASS}:{name}"
            if eid in (est.docs or {}):
                continue
            if not self._spend(eid, "define"):
                return
            try:
                self.vm.define(name, owner=CH.SELF, **spec)
            except CH.Refused:
                continue
            doc = est.docs[eid]
            doc.properties = {**(doc.properties or {}), "shadow": True}
            est.upsert(doc)

    @staticmethod
    def _call_expr(call: dict, c_eid: str, c_prop: str, c_new) -> str:
        obj = "x.eid" if call.get("object") == c_eid else repr(call.get("object"))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        parts = [f"{k!r}: " + (f"x.{c_prop}" if (v == c_new and _scalar(v)) else repr(v)) for k, v in args.items()]
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
