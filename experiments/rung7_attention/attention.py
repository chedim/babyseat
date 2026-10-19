"""#161: a joint-attention question for rung 7 -- is "which entity will the
operator touch next?" a reachable bar above "whatever they touch most"?

Replays read-only COPIES of a seat's record (never the live files):

    D=experiments/rung7_attention/data          # git-ignored
    cp <seat>/.membrane/serve-msgs.json <seat>/.membrane/self/record.jsonl $D/
    mkdir -p $D/toybox && cp <seat>/.membrane/toybox/*.log.jsonl $D/toybox/
    python experiments/rung7_attention/attention.py $D

THE SEQUENCE. One TURN is one operator chat message (a `[user: <name>]`
message from the operator's names) or one burst of toybox pokes tagged by
that person. A turn's touches are the entities the turn reached, deduped
within the turn, in first-touch order:

- the entity specs of the entity-family tool calls the agent made answering
  that message (until the session's next user message of any kind);
- known entity keys the operator NAMED in the message text (keys with a ':'
  and at least 5 characters, so `i` or `self` never match prose);
- the toy a person poked (the toybox log's `by` is the person's name).

Ids fold to one key (namespace and class prefix dropped, lower case); the
channel (chat:in/out, person:, voice:) is dropped: the operator speaking is
not what they attend to. Commands (exec) are out of the main sequence.

SCORING. Each predictor names a distribution q over candidate entities from
the turns before the one it predicts (every event in a turn sees the same
pre-turn state). A novel entity is unpredictable by any of them, so all
share one Witten-Bell "other" bucket, eps = D / (N + D) over the N events and
D distinct entities seen so far: p(e) = (1 - eps) q(e) when e is named, eps
otherwise. Log-loss skill vs popularity = 1 - LL / LL_pop; hit@k counts e
in the top k of q (a novel, unnamed e is a miss). CIs resample whole turns.
"""
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

OPERATOR_NAMES = ("Roomata Estorsky", "Dima")
CHANNEL = ("chat:", "person:", "voice:", "seat:", "e:", "word:", "session:")
ENTITY_TOOLS_ARGS = ("spec", "name", "entity", "eid", "key", "target")
POKE_GAP_S = 120.0          # pokes of one person closer than this are one turn
TOY_EID = {"toy_die": "toy:die", "toy_tower": "toy:tower", "w_body": "w:body",
           "w_burst": "w:burst", "w_drift": "w:drift", "w_slow": "w:slow", "w_fast": "w:fast"}


# ---------------------------------------------------------------- the ids

def norm(eid):
    """One key per entity: `membrane::Component:w:body` -> `w:body`; the
    channel the operator speaks through -> None."""
    if not eid or not isinstance(eid, str):
        return None
    s = eid.strip().strip("'\"`").lower()
    s = s.split("::", 1)[1] if "::" in s else s
    # the mirror / model views of an entity (m:, models:) are the entity
    for pre in ("tool://", "models:", "m:", "component:"):
        if s.startswith(pre):
            s = s[len(pre):]
    if s.startswith("files:"):
        s = "f:" + s[len("files:"):]
    s = s.strip()
    if not s or len(s) > 80 or " " in s or s.startswith(CHANNEL) or "chat:" in s:
        return None
    return s


# ---------------------------------------------------------------- predictors

class Popularity:
    """THE BASELINE: cumulative counts of what the operator touched."""

    def __init__(self):
        self.n = Counter()
        self.order = {}

    def observe(self, turn):
        for e in turn["ents"]:
            self.order.setdefault(e, len(self.order))
            self.n[e] += 1

    def pop(self):
        tot = sum(self.n.values())
        return {e: c / tot for e, c in self.n.items()} if tot else {}

    def predict(self, ts):
        return self.pop()


def _mix(a, b, lam):
    out = {e: (1 - lam) * v for e, v in b.items()}
    for e, v in a.items():
        out[e] = out.get(e, 0.0) + lam * v
    return out


def _normed(d):
    s = sum(d.values())
    return {e: v / s for e, v in d.items()} if s > 0 else {}


class Decayed(Popularity):
    """Recency: counts decayed by `half` turns, mixed lam with popularity."""

    def __init__(self, half=5.0, lam=0.5):
        super().__init__()
        self.half, self.lam = half, lam
        self.dec = {}

    def observe(self, turn):
        f = 0.5 ** (1.0 / self.half)
        self.dec = {e: v * f for e, v in self.dec.items()}
        for e in turn["ents"]:
            self.dec[e] = self.dec.get(e, 0.0) + 1.0
        super().observe(turn)

    def predict(self, ts):
        return _mix(_normed(self.dec), self.pop(), self.lam)


class Bigram(Popularity):
    """First order over turns: every entity of turn i-1 -> every entity of
    turn i, P(e | prev) = (c(prev, e) + beta pop(e)) / (c(prev) + beta),
    averaged over the previous turn's entities."""

    def __init__(self, beta=1.0):
        super().__init__()
        self.beta = beta
        self.tr = defaultdict(Counter)
        self.prev = []

    def observe(self, turn):
        for a in self.prev:
            for b in turn["ents"]:
                self.tr[a][b] += 1
        self.prev = list(turn["ents"])
        super().observe(turn)

    def predict(self, ts):
        pop = self.pop()
        if not self.prev:
            return pop
        out = Counter()
        for a in self.prev:
            row, tot = self.tr.get(a, {}), sum(self.tr.get(a, {}).values())
            for e, p in pop.items():
                out[e] += (row.get(e, 0) + self.beta * p) / (tot + self.beta) / len(self.prev)
        return dict(out)


class Repeat(Popularity):
    """The last turn again (lam), else popularity: the cheapest recency."""

    def __init__(self, lam=0.5):
        super().__init__()
        self.lam, self.prev = lam, []

    def observe(self, turn):
        self.prev = list(turn["ents"])
        super().observe(turn)

    def predict(self, ts):
        last = {e: 1.0 / len(self.prev) for e in self.prev} if self.prev else {}
        return _mix(last, self.pop(), self.lam) if last else self.pop()


class SelfLooks(Popularity):
    """What self looked at: self's looks (record `reach`) decayed by `half`
    seconds up to the turn's time, mixed lam with popularity. Names entities
    the operator never touched too (they leave the bucket)."""

    def __init__(self, looks, half=600.0, lam=0.5):
        super().__init__()
        self.looks = sorted(looks)            # [(ts, key)]
        self.half, self.lam = half, lam

    def trail(self, ts):
        w = Counter()
        for t, e in self.looks:
            if t >= ts:
                break
            age = ts - t
            if age < 8 * self.half:
                w[e] += 0.5 ** (age / self.half)
        return _normed(w)

    def predict(self, ts):
        tr = self.trail(ts)
        return _mix(tr, self.pop(), self.lam) if tr else self.pop()


class FieldNeighbours(Popularity):
    """The field's geometry: cosine neighbours of the last turn's entities
    (positions copied read-only), softmax(sim / temp) over the operator's
    seen entities, mixed lam with popularity."""

    def __init__(self, vecs, temp=0.1, lam=0.3):
        super().__init__()
        self.vecs, self.temp, self.lam, self.prev = vecs, temp, lam, []

    def observe(self, turn):
        self.prev = [e for e in turn["ents"] if e in self.vecs]
        super().observe(turn)

    def predict(self, ts):
        pop = self.pop()
        cand = [e for e in pop if e in self.vecs]
        if not self.prev or not cand:
            return pop
        nb = Counter()
        for a in self.prev:
            va = self.vecs[a]
            sims = {e: sum(x * y for x, y in zip(va, self.vecs[e])) for e in cand}
            m = max(sims.values())
            z = {e: math.exp((s - m) / self.temp) for e, s in sims.items()}
            tot = sum(z.values())
            for e, v in z.items():
                nb[e] += v / tot / len(self.prev)
        return _mix(dict(nb), pop, self.lam)


class Mix:
    """w * a + (1 - w) * b: a stacked predictor (each part sees every turn)."""

    def __init__(self, a, b, w=0.5):
        self.a, self.b, self.w = a, b, w

    @property
    def order(self):
        return self.a.order

    def observe(self, turn):
        self.a.observe(turn)
        self.b.observe(turn)

    def predict(self, ts):
        return _mix(_normed(self.a.predict(ts) or {}), _normed(self.b.predict(ts) or {}), self.w)


# ---------------------------------------------------------------- scoring

def run(turns, pred):
    """Prequential replay: one record per event {i (turn), e, lp, rank}."""
    out, n, seen = [], 0, set()
    for i, t in enumerate(turns):
        q = _normed(pred.predict(t["ts"]) or {})
        eps = 1.0 if n == 0 else len(seen) / (n + len(seen))
        ranked = sorted(q, key=lambda e: (-q[e], pred.order.get(e, 1 << 30), e))
        rank = {e: k + 1 for k, e in enumerate(ranked)}
        for e in t["ents"]:
            p = (1 - eps) * q[e] if q.get(e, 0) > 0 else eps
            out.append({"i": i, "e": e, "lp": math.log(p) if p > 0 else -50.0,
                        "rank": rank.get(e) if q.get(e, 0) > 0 else None,
                        "novel": e not in seen})
        for e in t["ents"]:
            n += 1
            seen.add(e)
        pred.observe(t)
    return out


def ll(res):
    return -sum(r["lp"] for r in res) / max(1, len(res))


def skill(res, base):
    b = sum(-r["lp"] for r in base)
    return 0.0 if b == 0 else 1.0 - sum(-r["lp"] for r in res) / b


def hit(res, k):
    return sum(1 for r in res if r["rank"] is not None and r["rank"] <= k) / max(1, len(res))


def boot_ci(res, base, stat, n=2000, seed=0, alpha=0.05):
    """Percentile CI of stat(res, base) resampling whole turns (paired)."""
    by_r, by_b = defaultdict(list), defaultdict(list)
    for r, b in zip(res, base):
        by_r[r["i"]].append(r)
        by_b[b["i"]].append(b)
    ids = sorted(by_r)
    rng = random.Random(seed)
    vals = []
    for _ in range(n):
        pick = [rng.choice(ids) for _ in ids]
        vals.append(stat([x for i in pick for x in by_r[i]], [x for i in pick for x in by_b[i]]))
    vals.sort()
    return vals[int(alpha / 2 * (n - 1))], vals[int((1 - alpha / 2) * (n - 1))]


def dhit(k):
    return lambda res, base: hit(res, k) - hit(base, k)


# ---------------------------------------------------------------- the data

def _text(m):
    return "".join(p.get("text", "") for p in m["parts"] if p.get("type") == "text")


def _tool_ents(p):
    st = p.get("state") or {}
    inp = st.get("input")
    if not isinstance(inp, dict):
        return []
    tool = str(p.get("tool") or "")
    if "entit" not in tool and tool not in ("entity",):
        return []
    out = []
    for k in ENTITY_TOOLS_ARGS:
        v = inp.get(k)
        if isinstance(v, str):
            out.append(v)
    return out


def chat_turns(msgs_path, known=(), tools=True):
    """Operator chat turns: [{ts, ents, src, sid}]; a turn whose touches are
    all channel is kept with ents=[] (dropped later). tools=False keeps only
    what the operator named themselves (the strict sequence)."""
    d = json.load(open(msgs_path))
    pat = re.compile(r"\[user: ([^\]]+)\]")
    known = sorted({k for k in known if ":" in k and len(k) >= 5}, key=len, reverse=True)
    turns = []
    for sid, msgs in d.items():
        cur = None
        for m in sorted(msgs, key=lambda m: m["info"]["time"]["created"]):
            if m["info"]["role"] == "user":
                t = _text(m)
                mm = pat.match(t)
                cur = None
                if mm and mm.group(1) in OPERATOR_NAMES:
                    cur = {"ts": m["info"]["time"]["created"] / 1000.0, "ents": [], "src": "chat",
                           "sid": sid, "text": t}
                    low = t.lower()
                    for k in known:
                        if re.search(r"(?<![\w:])" + re.escape(k) + r"(?![\w:-])", low):
                            cur["ents"].append(k)
                    turns.append(cur)
            elif cur is not None and tools:
                for p in m["parts"]:
                    if p.get("type") == "tool":
                        for raw in _tool_ents(p):
                            cur["ents"].append(raw)
    for t in turns:
        ks = []
        for raw in t["ents"]:
            k = norm(raw)
            if k and k not in ks:
                ks.append(k)
        t["ents"] = ks
    return turns


def poke_turns(toybox_dir):
    pokes = []
    for f in Path(toybox_dir).glob("*.log.jsonl"):
        key = TOY_EID.get(f.name.split(".")[0], f.name.split(".")[0].replace("_", ":"))
        for ln in open(f):
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r.get("by") in OPERATOR_NAMES:
                pokes.append((float(r["ts"]), key))
    pokes.sort()
    turns = []
    for ts, key in pokes:
        if turns and ts - turns[-1]["last"] < POKE_GAP_S:
            turns[-1]["last"] = ts
            if key not in turns[-1]["ents"]:
                turns[-1]["ents"].append(key)
        else:
            turns.append({"ts": ts, "last": ts, "ents": [key], "src": "poke"})
    return turns


def self_looks(record_path):
    looks, birth = [], None
    for ln in open(record_path):
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("kind") == "birth" and birth is None:
            birth = float(r["ts"])
        if r.get("kind") == "reach" and not r.get("audit"):
            k = norm(r.get("eid"))
            if k:
                looks.append((float(r["ts"]), k))
    return looks, birth


def build(data_dir, known=(), tools=True):
    """(turns, self's looks, self's birth ts). The keys recognised in the
    operator's text: every key the agent's tools reached anywhere in the
    record, self's looks and the toys (recognition only, never a forecast)."""
    data_dir = Path(data_dir)
    looks, birth = self_looks(data_dir / "record.jsonl")
    reached = {e for t in chat_turns(data_dir / "serve-msgs.json") for e in t["ents"]}
    known = set(known) | reached | {k for _, k in looks} | set(TOY_EID.values())
    turns = chat_turns(data_dir / "serve-msgs.json", known, tools=tools)
    turns += poke_turns(data_dir / "toybox")
    turns.sort(key=lambda t: t["ts"])
    return turns, looks, birth


# ---------------------------------------------------------------- the replay

def tune(turns, cut, grid):
    """The params with the lowest head log-loss (events of turns < cut)."""
    best = None
    for params, mk in grid:
        res = [r for r in run(turns, mk()) if r["i"] < cut]
        v = ll(res)
        if best is None or v < best[0]:
            best = (v, params, mk)
    return best[1], best[2]


def tail(res, cut):
    return [r for r in res if r["i"] >= cut]


def fmt_ci(ci):
    return f"[{ci[0]:+.3f}, {ci[1]:+.3f}]"


def describe(label, all_turns):
    turns = [t for t in all_turns if t["ents"]]
    ev = [e for t in turns for e in t["ents"]]
    cnt = Counter(ev)
    print(f"\n## {label}\n")
    print(f"operator turns: {len(all_turns)} ({sum(t['src'] == 'chat' for t in all_turns)} chat, "
          f"{sum(t['src'] == 'poke' for t in all_turns)} poke bursts); with a touch: {len(turns)}; "
          f"events: {len(ev)}; distinct: {len(cnt)}")
    if not ev:
        return turns
    tot = len(ev)
    top = cnt.most_common()
    sh = [c / tot for _, c in top]
    print(f"concentration: top1 {sh[0]:.2f}, top5 {sum(sh[:5]):.2f}, top10 {sum(sh[:10]):.2f}; "
          f"singletons {sum(1 for _, c in top if c == 1)}; entropy "
          f"{-sum(p * math.log2(p) for p in sh):.2f} bits (uniform over {len(cnt)}: {math.log2(len(cnt)):.2f})")
    print("top 12:", ", ".join(f"{e} {c}" for e, c in top[:12]))
    seen, novel = set(), 0
    for e in ev:
        novel += e not in seen
        seen.add(e)
    print(f"novel events (first touch of an entity): {novel} of {tot} ({novel / tot:.0%}); "
          f"mean touches per turn {tot / len(turns):.1f}")
    return turns


def table(turns, vecs, nulls=50, frac=0.7):
    cut = int(len(turns) * frac)
    grids = {
        "recency (decayed)": [((h, l), (lambda h=h, l=l: Decayed(h, l)))
                              for h in (1, 2, 3, 5, 10, 20) for l in (0.2, 0.4, 0.6, 0.8)],
        "repeat last turn": [((l,), (lambda l=l: Repeat(l))) for l in (0.1, 0.2, 0.3, 0.5, 0.7)],
        "bigram": [((b,), (lambda b=b: Bigram(b))) for b in (0.1, 0.3, 1, 3, 10)],
    }
    if vecs:
        grids["field neighbours"] = [((tp, l), (lambda tp=tp, l=l: FieldNeighbours(vecs, tp, l)))
                                     for tp in (0.02, 0.05, 0.1, 0.2) for l in (0.1, 0.2, 0.4, 0.6)]
    chosen = {"popularity": ((), Popularity)}
    for name, grid in grids.items():
        chosen[name] = tune(turns, cut, grid)
    base = run(turns, Popularity())
    bt = tail(base, cut)
    print(f"\nheld-out tail: turns {cut}..{len(turns) - 1}, {len(bt)} events "
          f"({sum(r['novel'] for r in bt)} novel); params tuned on the first {cut} turns' log-loss\n")
    print("| predictor | params | LL (nats) | skill vs pop [95% CI] | hit@1 | hit@5 "
          "| d hit@1 [CI] | d hit@5 [CI] | whole-seq skill | shuffled-order null skill |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for name, (params, mk) in chosen.items():
        res = run(turns, mk())
        rt = tail(res, cut)
        nul = "--"
        if name != "popularity":
            sk = []
            for s in range(nulls):
                perm = turns[:]
                random.Random(s).shuffle(perm)
                sk.append(skill(tail(run(perm, mk()), cut), tail(run(perm, Popularity()), cut)))
            sk.sort()
            nul = f"{sum(sk) / len(sk):+.3f} [{sk[int(0.025 * (nulls - 1))]:+.3f}, {sk[int(0.975 * (nulls - 1))]:+.3f}]"
        print(f"| {name} | {params} | {ll(rt):.3f} | {skill(rt, bt):+.3f} {fmt_ci(boot_ci(rt, bt, skill))} | "
              f"{hit(rt, 1):.2f} | {hit(rt, 5):.2f} | {fmt_ci(boot_ci(rt, bt, dhit(1)))} | "
              f"{fmt_ci(boot_ci(rt, bt, dhit(5)))} | {skill(res, base):+.3f} | {nul} |")
    return cut, chosen


def vs_recency(turns, vecs, cut, chosen, nulls=50):
    """The harder bar: does anything beat the RECENCY cache (the best cheap
    predictor) when stacked on it? w tuned on the head like everything else."""
    _, rec = chosen["recency (decayed)"]
    parts = {"recency + bigram": chosen["bigram"][1]}
    if "field neighbours" in chosen:
        parts["recency + field neighbours"] = chosen["field neighbours"][1]
    base = run(turns, rec())
    bt = tail(base, cut)
    print(f"\nagainst RECENCY as the baseline (tail {len(bt)} events)\n")
    print("| predictor | w (recency) | skill vs recency [95% CI] | hit@1 | hit@5 | d hit@1 [CI] | shuffled-order null |")
    print("|---|---|---|---|---|---|---|")
    print(f"| recency (decayed) | 1 | +0.000 | {hit(bt, 1):.2f} | {hit(bt, 5):.2f} | -- | -- |")
    for name, mk in parts.items():
        (w,), mkm = tune(turns, cut, [((w,), (lambda w=w: Mix(rec(), mk(), w))) for w in (0.3, 0.5, 0.7, 0.85)])
        rt = tail(run(turns, mkm()), cut)
        sk = []
        for s in range(nulls):
            perm = turns[:]
            random.Random(s).shuffle(perm)
            sk.append(skill(tail(run(perm, mkm()), cut), tail(run(perm, rec()), cut)))
        sk.sort()
        print(f"| {name} | {w} | {skill(rt, bt):+.3f} {fmt_ci(boot_ci(rt, bt, skill))} | {hit(rt, 1):.2f} | "
              f"{hit(rt, 5):.2f} | {fmt_ci(boot_ci(rt, bt, dhit(1)))} | {sum(sk) / len(sk):+.3f} |")


def self_check(turns, looks, birth):
    """Proxy for "does the current self already pass?": what self's own look
    trail says about the operator's next touch, on the turns since its birth."""
    lt = [i for i, t in enumerate(turns) if t["ts"] >= birth]
    if not lt:
        return
    lcut = lt[0]
    base = run(turns, Popularity())
    alive = [r for r in base if r["i"] >= lcut]
    lkeys = {k for _, k in looks}
    print(f"\n## self's look trail\n\nturns since self's birth: {len(turns) - lcut}, events {len(alive)}; "
          f"self looked at {len(lkeys)} distinct entities in {len(looks)} looks")
    print(f"operator events on an entity self EVER looked at: {sum(1 for r in alive if r['e'] in lkeys)} of {len(alive)}")
    for half in (120.0, 600.0, 3600.0):
        sl = SelfLooks(looks, half, 0.0)
        h1 = h5 = 0
        for i in range(lcut, len(turns)):
            tr = sl.trail(turns[i]["ts"])
            rk = sorted(tr, key=lambda e: -tr[e])
            for e in turns[i]["ents"]:
                h1 += e in rk[:1]
                h5 += e in rk[:5]
        print(f"- trail alone (half-life {half:.0f}s): hit@1 {h1 / len(alive):.2f}, hit@5 {h5 / len(alive):.2f} "
              f"(popularity on the same events: hit@1 {hit(alive, 1):.2f}, hit@5 {hit(alive, 5):.2f})")
    best = None
    for half in (120.0, 600.0, 3600.0):
        for lam in (0.1, 0.3, 0.5, 0.8):
            res = [r for r in run(turns, SelfLooks(looks, half, lam)) if r["i"] >= lcut]
            v = skill(res, alive)
            if best is None or v > best[0]:
                best = (v, half, lam, res)
    v, half, lam, res = best
    print(f"- trail + popularity, the BEST of 12 settings picked ON these events (optimistic): "
          f"half-life {half:.0f}s, lam {lam}: skill {v:+.3f} {fmt_ci(boot_ci(res, alive, skill))}, "
          f"hit@1 {hit(res, 1):.2f}, hit@5 {hit(res, 5):.2f}")
    for lab, lo, hi in (("10 min BEFORE (self leads)", -600, 0), ("10 min AFTER (self follows)", 0, 600)):
        got = []
        for t in turns[lcut:]:
            win = {k for ts, k in looks if t["ts"] + lo <= ts < t["ts"] + hi}
            if win:
                got.append(bool(win & set(t["ents"])))
        print(f"- turns where self's looks in the {lab} include a touched entity: {sum(got)} of {len(got)}")


def main(argv):
    data_dir = Path(argv[1]) if len(argv) > 1 else Path(__file__).parent / "data"
    vec_path = data_dir / "field-vecs.json"
    vecs = json.load(open(vec_path)) if vec_path.exists() else {}
    all_turns, looks, birth = build(data_dir)
    turns = describe("the sequence: what each operator turn reached (agent tool calls + named + pokes)", all_turns)
    if vecs:
        cov = sum(1 for t in turns for e in t["ents"] if e in vecs)
        print(f"field positions (end-of-record copy): {cov} of {sum(len(t['ents']) for t in turns)} events have one")
    cut, chosen = table(turns, vecs)
    vs_recency(turns, vecs, cut, chosen)
    print("\n### sensitivity: a 50/50 head/tail split")
    table(turns, vecs, nulls=20, frac=0.5)
    if looks and birth:
        self_check(turns, looks, birth)
    strict, _, _ = build(data_dir, tools=False)
    st = describe("strict: only what the operator named or poked (no agent tool calls)", strict)
    if len(st) >= 20:
        table(st, vecs)


if __name__ == "__main__":
    main(sys.argv)
