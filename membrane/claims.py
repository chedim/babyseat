"""CLAIMS vs EVIDENCE: what was SAID, checked against what was OBSERVED.

Sources: the model's prose and the old compact notes (E17 found them wrong
at times). User text and reasoning are not claims.

  action  "I updated membrane/router.py"  -> an applied edit / write / file-
          writing shell command on that file within [-45 min, +5 min]:
          backed | contradicted (only failed edits) | unbacked
  tests   "524 tests pass"                -> the last full-suite run observed
          <= 60 min before: equal -> backed, different -> contradicted
  state   "<entity> cannot / always / only / never ..." -> pending_judge:
          a bounded, idle-only local-model judge reads the claim against the
          CODE and observation evidence of the entities it concerns (E25's
          port case: the note's contrary evidence existed only in code)

Extraction handles questions and conditionals (skipped), future/intent
('I'll update X' is a plan, not a claim), and negation ('I did not update
X'). A contradicted claim becomes a conflict on its entities."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from . import eids as E
from . import streams as ST
from .efstore import h16, o8

ACTION_VERBS = ("updated", "edited", "changed", "fixed", "added", "modified", "patched",
                "wrote", "created", "rewrote", "removed", "deleted", "renamed", "moved")
# base forms too: a negated claim uses them ('I did not update X'); an
# imperative ('update X') has no subject and never counts
BASE_VERBS = ("update", "edit", "change", "fix", "add", "modify", "patch", "write",
              "create", "rewrite", "remove", "delete", "rename", "move")
_ACTION = re.compile(r"\b(" + "|".join(ACTION_VERBS + BASE_VERBS) + r")\b", re.I)
_SUBJ = re.compile(r"\b(I|we|I've|we've|I have|we have)\b", re.I)
_PASSIVE = re.compile(r"\b(was|were|has been|have been|is now|got)\s+(" + "|".join(ACTION_VERBS) + r")\b", re.I)
_FUTURE = re.compile(r"\b(will|'ll|going to|let me|need to|should|could|would|plan to|next I|want to|about to)\b", re.I)
_NEG = re.compile(r"\b(not|n't|never|no longer|without|failed to|didn't|haven't|hasn't)\b", re.I)
_COND = re.compile(r"^\s*(if|when|once|unless|whether|suppose|what if)\b", re.I)
_TESTS = re.compile(r"\b(?:all\s+)?(\d{2,4})\s+(?:tests?\s+)?(?:pass|passed|passing|green)\b", re.I)
_TESTS_FAIL = re.compile(r"\b(\d{1,4})\s+(?:tests?\s+)?(?:fail|failed|failing|failures?)\b", re.I)
# a CAPABILITY / LIMITATION statement: the marker followed by a verb
# ('cannot select', 'never retries', 'doesn't bind') -- descriptive 'only',
# 'must', 'always' flood the judge with prose (789 hits on the real log)
_ABSOLUTE = re.compile(r"\b(cannot|can't|can not|unable to|doesn't|does not|won't|will not|"
                       r"never|is not able to|isn't able to)\s+(?:\w+\s+)?[a-z]+", re.I)
_PAST_AUX = re.compile(r"\b(did|didn't|haven't|hasn't|have|has|had|never)\b", re.I)
STATE_MAX_CHARS = 200
_WRITES = re.compile(r"sed -i|>>?\s*\S|tee\s|\bpatch\b|git apply|\bmv\b|\bcp\b")

ACTION_WINDOW = (-45 * 60, 5 * 60)
TESTS_WINDOW = 60 * 60


@dataclass
class Claim:
    kind: str                         # action | tests | state
    text: str
    role: str
    polarity: int = 1
    tense: str = "past"               # past | present | future
    subjects: list = field(default_factory=list)
    verb: str = ""
    count: int | None = None
    ts: float | None = None
    event: str = ""
    status: str = "pending"           # pending | backed | unbacked | contradicted |
    evidence: list = field(default_factory=list)   # pending_judge | supported | n/a
    judge: dict | None = None


def extract_claims(sentence: str, role: str, graph: dict | None = None,
                   ts: float | None = None, event: str = "") -> list[Claim]:
    s = " ".join((sentence or "").split())
    if not s or s.endswith("?") or _COND.match(s):
        return []
    ments = E.mentions_of(s, graph)
    specific = [e for e, _ in ments if E.is_specific(e)]
    out: list[Claim] = []
    m = _ACTION.search(s)
    if m and not (specific and (_SUBJ.search(s[:m.start()]) or _PASSIVE.search(s))):
        m = None                          # no subject (an imperative) or no object
    tense, pol = "past", 1
    if m:
        before = s[max(0, m.start() - 40):m.start()]
        tense = "future" if _FUTURE.search(before) else "past"
        pol = -1 if _NEG.search(before) else 1
        if tense == "past" and m.group(1).lower() in BASE_VERBS and not _PAST_AUX.search(before):
            m = None                      # 'I need to fix X' / 'we fix X' is no report
    if m:
        objs = [e for e in specific if e.startswith(("f:", "s:"))]
        if objs:
            out.append(Claim("action", s, role, pol, tense, objs, m.group(1).lower(), ts=ts,
                             event=event, status="n/a" if tense == "future" else "pending"))
    t = _TESTS.search(s)
    if t and not _FUTURE.search(s[:t.start()]):
        out.append(Claim("tests", s, role, 1, "present", [], "pass", int(t.group(1)),
                         ts=ts, event=event))
    if not out and specific and len(s) <= STATE_MAX_CHARS and _ABSOLUTE.search(s):
        out.append(Claim("state", s, role, -1 if _NEG.search(s) else 1, "present",
                         specific, _ABSOLUTE.search(s).group(1).lower(), ts=ts, event=event,
                         status="pending_judge"))
    if not out and len(s) <= STATE_MAX_CHARS and _ABSOLUTE.search(s) and role == "note":
        # a distilled note's absolute statement about a generic subject (the
        # E17 port note): judged against the nearest code/observation evidence
        out.append(Claim("state", s, role, -1 if _NEG.search(s) else 1, "present", [],
                         _ABSOLUTE.search(s).group(1).lower(), ts=ts, event=event,
                         status="pending_judge"))
    return out


def claim_key(origin: str, c: Claim) -> str:
    return f"c:{o8(origin)}:{h16(c.event + '|' + c.kind + '|' + c.text)}"


def ingest(fld, key: str, ev: dict) -> list[str]:
    """Extract and store the claims of one model-prose or distilled event."""
    if ev.get("role") not in ("model", "note"):
        return []
    graph = fld.graph_fn()
    keys = []
    for s in ST.sentences(ev.get("text") or ""):
        for c in extract_claims(s, ev["role"], graph, ev.get("ts"), key):
            ck = claim_key(fld.origin, c)
            if fld.b.insert_if_absent("claims", ck, {"_k": "claim", "origin": fld.origin,
                                                     **asdict(c)}):
                keys.append(ck)
    return keys


# ---- matching ----------------------------------------------------------------------------
def _obs(fld):
    return [d for _, d in fld.b.scan("events", {"origin": fld.origin, "stream": "observation"})]


def _touches(ev: dict, file_eid: str, graph) -> bool:
    a = ev.get("args") or {}
    fp = a.get("filePath") or a.get("path")
    if isinstance(fp, str) and E.canon("f", fp, graph) == file_eid:
        return True
    cmd = a.get("command")
    if isinstance(cmd, str) and _WRITES.search(cmd):
        base = file_eid.split(":", 1)[1].lstrip("~")
        return base in cmd or base.rsplit("/", 1)[-1] in cmd
    return False


def match_action(fld, c: dict, obs=None) -> tuple[str, list]:
    if c.get("ts") is None:
        return "n/a", []
    graph = fld.graph_fn()
    lo, hi = c["ts"] + ACTION_WINDOW[0], c["ts"] + ACTION_WINDOW[1]
    hits = []
    for ev in obs if obs is not None else _obs(fld):
        if ev.get("ts") is None or not (lo <= ev["ts"] <= hi):
            continue
        if ev.get("tool") not in ("edit", "write", "bash"):
            continue
        if any(_touches(ev, f, graph) for f in c["subjects"] if f.startswith("f:")):
            hits.append(ev)
    # a writing shell command that printed nothing succeeded ('empty')
    ok = [e for e in hits if e.get("outcome") in ("applied", "ok", "tests-ok", "empty")]
    refs = [{"event_seq": e.get("seq"), "tool": e.get("tool"), "outcome": e.get("outcome"),
             "dt": round(e["ts"] - c["ts"], 1)} for e in hits[:5]]
    if c.get("polarity", 1) < 0:
        return ("contradicted" if ok else "backed"), refs
    if ok:
        return "backed", refs
    return ("contradicted" if hits else "unbacked"), refs


def match_tests(fld, c: dict, obs=None) -> tuple[str, list]:
    if c.get("ts") is None:
        return "n/a", []
    runs = []
    for ev in obs if obs is not None else _obs(fld):
        cmd = (ev.get("args") or {}).get("command") or ""
        if ev.get("ts") is None or not (0 <= c["ts"] - ev["ts"] <= TESTS_WINDOW):
            continue
        n = ST.tests_ran(ev.get("result_head") or "", ev.get("result_tail") or "")
        if n is not None and ST.is_full_suite(cmd):
            runs.append((ev["ts"], n, ev))
    if not runs:
        return "unbacked", []
    ts, n, ev = max(runs, key=lambda r: r[0])
    ref = [{"event_seq": ev.get("seq"), "ran": n, "outcome": ev.get("outcome"),
            "dt": round(ts - c["ts"], 1)}]
    return ("backed" if n == c.get("count") and ev.get("outcome") != "tests-failed"
            else "contradicted"), ref


def evaluate(fld, now: float | None = None, limit: int = 500) -> int:
    """Settle pending action/test claims whose evidence window has closed."""
    now = now or time.time()
    obs = _obs(fld)
    n = 0
    for key, c in list(fld.b.scan("claims", {"origin": fld.origin, "status": "pending"},
                                  limit=limit)):
        if c.get("ts") is not None and c["ts"] + ACTION_WINDOW[1] > now:
            continue                                     # window still open
        if c["kind"] == "action":
            c["status"], c["evidence"] = match_action(fld, c, obs)
        elif c["kind"] == "tests":
            c["status"], c["evidence"] = match_tests(fld, c, obs)
        else:
            continue
        fld.b.upsert("claims", key, c)
        if c["status"] == "contradicted":
            _conflict(fld, key, c["subjects"])
        n += 1
    return n


def _conflict(fld, claim_key_: str, eids_: list) -> None:
    for eid in eids_:
        st = fld.get(eid)
        if st is not None and claim_key_ not in st.conflicts:
            st.conflicts.append(claim_key_)
            fld._dirty.add(eid)
    fld.flush()


# ---- the judge ------------------------------------------------------------------------------
def judge_evidence(fld, c: dict, k: int = 4) -> list[tuple[str, str]]:
    """(eid, sentence) evidence for a state claim: CODE and observation
    evidence of its specific subjects, else of the specific entities whose
    state is nearest the claim."""
    subjects = list(c.get("subjects") or [])
    if not subjects:
        v = fld.emb.embed([c["text"]])[0]
        subjects = [e for e, _ in fld.recall(v, k=k, kinds=["s", "f"])]
    out = []
    for eid in subjects:
        for ev in fld.evidence(eid, k=2, streams=("code", "observation")):
            out.append((eid, ev["text"]))
    return out[:k * 2]


class OllamaClaimJudge:
    """The idle local model as a judge: one line -- SUPPORTS / CONTRADICTS /
    UNRELATED + a short reason."""

    PROMPT = ("You check a claim about a software project against evidence from the "
              "project itself.\nClaim: {claim}\nEvidence:\n{evidence}\n"
              "Answer on one line: SUPPORTS, CONTRADICTS or UNRELATED, then a short reason.")

    def __init__(self, model: str, base_url: str = "http://localhost:11434", timeout: float = 60):
        self.model, self.base, self.timeout = model, base_url, timeout

    def judge(self, claim: str, evidence: list[str]) -> tuple[str, str]:
        import json
        import urllib.request
        body = json.dumps({"model": self.model, "stream": False, "options": {"temperature": 0},
                           "messages": [{"role": "user", "content": self.PROMPT.format(
                               claim=claim, evidence="\n".join(f"- {e}" for e in evidence))}]})
        rq = urllib.request.Request(self.base + "/api/chat", data=body.encode(),
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=self.timeout) as r:
            text = json.load(r)["message"]["content"].strip()
        word = (text.split() or ["UNRELATED"])[0].upper().strip(".,:")
        verdict = {"SUPPORTS": "supports", "CONTRADICTS": "contradicts"}.get(word, "unrelated")
        return verdict, text[:300]


class JudgeQueue:
    """Bounded and idle-only: at most `per_tick` claims per tick and
    `per_hour` per hour, only when `idle()` says the seat is quiet. Any
    judge error leaves the claim pending."""

    def __init__(self, fld, judge, per_tick: int = 3, per_hour: int = 60, idle=lambda: True):
        self.fld, self.judge, self.per_tick, self.per_hour, self.idle = (
            fld, judge, per_tick, per_hour, idle)
        self._spent: list[float] = []

    def tick(self, now: float | None = None) -> int:
        now = now or time.time()
        self._spent = [t for t in self._spent if now - t < 3600]
        if not self.idle():
            return 0
        budget = min(self.per_tick, self.per_hour - len(self._spent))
        if budget <= 0:
            return 0
        done = 0
        for key, c in list(self.fld.b.scan("claims", {"origin": self.fld.origin,
                                                      "status": "pending_judge"}, limit=budget)):
            ev = judge_evidence(self.fld, c)
            self._spent.append(now)
            if not ev:
                c["status"] = "unverified"
                self.fld.b.upsert("claims", key, c)
                continue
            try:
                verdict, reason = self.judge.judge(c["text"], [t for _, t in ev])
            except Exception:
                continue                                  # stays pending_judge
            c["judge"] = {"verdict": verdict, "reason": reason, "ts": now,
                          "evidence": [{"eid": e, "text": t} for e, t in ev]}
            c["status"] = {"supports": "supported", "contradicts": "contradicted"}.get(
                verdict, "unverified")
            self.fld.b.upsert("claims", key, c)
            if c["status"] == "contradicted":
                _conflict(self.fld, key, list(dict.fromkeys(list(c.get("subjects") or [])
                                                            + [e for e, _ in ev])))
            done += 1
        return done
