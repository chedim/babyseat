#!/usr/bin/env python3 -I
"""Why do input tokens per turn grow? Read-only analysis of a COPY of a seat store.

usage:
  sqlite3 <live seat.sqlite> ".backup <scratch>/seat.sqlite"      # a fresh copy, never the live file
  python3 -I scripts/token_growth.py <scratch>/seat.sqlite [--repo <repo root>]

Reads (sqlite mode=ro): the Chat message `...:turn` entities that carry `usage`, the message
`text`, and the `...:turn:tool:<id>` entities (name, input, result). The CLI's `usage` is the SUM
over the turn's API calls of (input + cache_read + cache_creation); `usage.iterations[-1]` is the
LAST call only, so L (the last call's context) is exact. Labels used in the output:
  MEASURED  = read straight from the store (or fitted exactly, see the price fit)
  INFERRED  = modelled; the model and its calibration constants are at the top of this file
Needs only the stdlib. Prints markdown tables. Never writes, never starts a turn.
"""
import sqlite3, json, sys, re, ast, statistics as st, datetime, collections

BYTES_PER_TOK = 2.32      # MEASURED on 7 one-tool turns whose result is a 31-33 KB write reply: bytes / (L-B) tokens
TOK_PER_TOOL = 150        # MEASURED: one-tool turns with tiny results add 110-380 tokens (the tool_use block)
API_PER_TOOL = 0.51       # MEASURED on 16 early turns that kept raw events: (API calls - 1) / tool calls = 99/193 (parallel batches)

ECHO_PROPS = ("text", "banner_text", "banner_slot", "sys", "argv", "alert_lines", "keeps", "view", "stream_view",
              "history_view", "page_view")


def load(path):
    db = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    tools = collections.defaultdict(list)
    for k, b in db.execute("select k, body from c_field where k like 'membrane::Chat:message:%:turn:tool:%'"):
        p = json.loads(b)["props"]
        r = p.get("result")
        r = r if isinstance(r, str) else json.dumps(r)
        tools[k.split(":turn:tool:")[0]].append(dict(
            name=(p.get("name") or "").replace("mcp__membrane__", ""), inp=json.dumps(p.get("input")), res=r or ""))
    rows = []
    for k, b in db.execute("select k, body from c_field where k like 'membrane::Chat:message:%:turn'"):
        p = json.loads(b)["props"]
        u = p.get("usage")
        if not u:
            continue
        m = k[:-5]
        mb = db.execute("select body from c_field where k=?", (m,)).fetchone()
        text = (json.loads(mb[0])["props"].get("text") or "") if mb else ""
        it = (u.get("iterations") or [{}])[-1]
        L = sum(it.get(x, 0) for x in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        rows.append(dict(m=m, t=p["started"], text=text, inp=u["input_tokens"], cr=u["cache_read_input_tokens"],
                         cw=u["cache_creation_input_tokens"], out=u["output_tokens"], L=L,
                         Lcr=it.get("cache_read_input_tokens", 0), Lcw=it.get("cache_creation_input_tokens", 0),
                         cost=p.get("cost") or 0, calls=p.get("calls") or 0, tools=tools.get(m, [])))
    rows.sort(key=lambda r: r["t"])
    for r in rows:
        r["T"] = r["inp"] + r["cr"] + r["cw"]
        r["nt"] = len(r["tools"])
        r["resB"] = sum(len(t["res"]) for t in r["tools"])
    return rows


def q(xs, f):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(f * len(xs)))]


def hhmm(t):
    return datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")


def solve3(X, y):
    A = [[sum(x[i] * x[j] for x in X) for j in range(3)] for i in range(3)]
    b = [sum(x[i] * v for x, v in zip(X, y)) for i in range(3)]
    M = [A[i] + [b[i]] for i in range(3)]
    for i in range(3):
        p = max(range(i, 3), key=lambda j: abs(M[j][i])); M[i], M[p] = M[p], M[i]
        for j in range(3):
            if j != i:
                f = M[j][i] / M[i][i]; M[j] = [a - f * c for a, c in zip(M[j], M[i])]
    return [M[i][3] / M[i][i] for i in range(3)]


def tok(nbytes):
    return nbytes / BYTES_PER_TOK


def result_without_echo(res):
    """B2 reply shape replayed on an old reply: keep the reactions of the written entity, drop the copies of the
    banner / prompt getters of the banner and the chat messages. Returns the new byte length (INFERRED: B2's real
    reply also adds a few `<Class>.<prop> xN` count lines)."""
    m = re.match(r"(wrote [^\n]*\nreactions: )(\[.*\])\s*$", res, re.S)
    if not m:
        return len(res)
    try:
        rx = json.loads(m.group(2))
    except Exception:
        return len(res)
    keep = [x for x in rx if not (x.get("prop") in ECHO_PROPS and (str(x.get("eid", "")).startswith("membrane::Banner")
                                                                  or str(x.get("eid", "")).startswith("membrane::Chat:message")))]
    return len(m.group(1)) + len(json.dumps(keep, separators=(",", ":"))) + 40


def seq_model(B, P, parts):
    """Sequential model of one turn: call i sees B plus everything earlier calls added; each tool is its own call.
    parts = bytes of (result + input) per tool. Returns (sum of contexts, cache reads, cache writes)."""
    ctxs = [B]
    for b in parts:
        ctxs.append(ctxs[-1] + tok(b) + TOK_PER_TOOL)
    return sum(ctxs), P + sum(ctxs[:-1]), (B - P) + (ctxs[-1] - B)


def main():
    path = sys.argv[1]
    repo = sys.argv[sys.argv.index("--repo") + 1] if "--repo" in sys.argv else None
    rows = load(path)
    n = len(rows)
    print("# token growth: %d turns with usage, %s .. %s\n" % (n, hhmm(rows[0]["t"]), hhmm(rows[-1]["t"])))

    # ---- price fit (MEASURED)
    pr, pw, po = solve3([[r["cr"], r["cw"], r["out"]] for r in rows], [r["cost"] for r in rows])
    err = max(abs(pr * r["cr"] + pw * r["cw"] + po * r["out"] - r["cost"]) / r["cost"] for r in rows)
    print("price fit (least squares on the turns' own cost field): cache_read $%.3f/M, cache_write(1h) $%.3f/M, output $%.3f/M; max relative error %.4f\n" % (pr * 1e6, pw * 1e6, po * 1e6, err))

    # ---- the base B: a turn with 1 API call has T = B; with 1 tool (2 calls) T - L = B  (MEASURED anchors)
    anchors = []
    for r in rows:
        if r["nt"] == 0:
            anchors.append((r["t"], r["T"], r["cr"], r["cw"]))
        elif r["nt"] == 1 and r["calls"] == 2:
            anchors.append((r["t"], r["T"] - r["L"], r["cr"] - r["Lcr"], r["cw"] - r["Lcw"]))
    P = st.mode([a[2] for a in anchors if a[2] > 0])
    print("## 1. Base of a turn (first call: tools + system prompt + banner + user message)\n")
    print("MEASURED anchors = turns whose first call is fully known (no tool: T; one tool: T-L): %d anchors." % len(anchors))
    print("Prefix the first call READS: %d tokens in %d of %d anchors, constant over the whole window (the others read 0: cache cold after >1h idle)." % (
        P, sum(1 for a in anchors if a[2] == P), len(anchors)))
    if repo:
        s = open(repo + "/membrane/packs/door_tools.c1f").read()
        tl = ast.literal_eval(re.search(r"^Request\.mc_tools getter: (\[.*\])$", s, re.M).group(1))
        cs_ = open(repo + "/membrane/packs/chat_turn.c1f").read()
        note = re.search(r"ChatMessage\.note getter:\n  WHEN this.over: None\n  ELSE: '(.*)'", cs_).group(1)
        print("Tool definitions in the pack: %d tools, %d bytes JSON; doctrine (ChatMessage.note): %d chars." % (
            len(tl), len(json.dumps(tl, separators=(",", ":"))), len(note)))
    print("\n| time | base B | read (prefix) | written (system+banner+message) |\n|---|---|---|---|")
    step = max(1, len(anchors) // 14)
    for t, B, cr, cw in anchors[::step]:
        print("| %s | %d | %d | %d |" % (hhmm(t), B, cr, cw))
    bs = [a[1] for a in anchors]
    print("\nB min %d, median %d, max %d; first anchor %d (%s), last anchor %d (%s) (MEASURED).\n" % (
        min(bs), st.median(bs), max(bs), anchors[0][1], hhmm(anchors[0][0]), anchors[-1][1], hhmm(anchors[-1][0])))

    # banner text after each write that recomputed it: the write reply echoes the new Banner.text (MEASURED)
    snaps = []
    for r in rows:
        for t in r["tools"]:
            m = re.match(r"(wrote [^\n]*\nreactions: )(\[.*\])\s*$", t["res"], re.S)
            if not m:
                continue
            try:
                rx = json.loads(m.group(2))
            except Exception:
                continue
            for x in rx:
                if x.get("eid") == "membrane::Banner:owner:main" and x.get("prop") == "text" and isinstance(x.get("new"), str):
                    k = re.search(r"Keeps are (\d+) chars", x["new"])
                    snaps.append((r["t"], len(x["new"]), x["new"].count("\n- ["), int(k.group(1)) if k else None))
    print("Banner text length right after a write (the write reply carries it; %d snapshots, MEASURED), median per stretch of 10 snapshots:\n" % len(snaps))
    print("| time | banner chars | Keep lines | Keeps chars (TOKEN CHECK) |\n|---|---|---|---|")
    for i in range(0, len(snaps), 10):
        g = snaps[i:i + 10]
        kc = [x[3] for x in g if x[3]]
        print("| %s | %d | %d | %s |" % (hhmm(g[0][0]), st.median(x[1] for x in g), st.median(x[2] for x in g), "%d" % st.median(kc) if kc else "-"))
    pairs = []
    for t, B, _, _ in anchors:
        prev = [x for x in snaps if x[0] <= t and t - x[0] < 600]
        if prev:
            pairs.append((prev[-1][1], B))
    def fit(ps):
        mx = st.mean(p[0] for p in ps); my = st.mean(p[1] for p in ps)
        b = sum((x - mx) * (y - my) for x, y in ps) / sum((x - mx) ** 2 for x, y in ps)
        return b, my - b * mx
    if len(pairs) > 5:
        b, a = fit(pairs)
        ps2 = [p for p in pairs if abs(p[1] - (a + b * p[0])) < 600]       # one outlier pass: a turn right after a restart
        b, a = fit(ps2)
        print("\nB against the banner length of the turn before it (%d of %d anchor turns): B = %.0f + %.3f x banner chars, i.e. %.1f chars per token (INFERRED fit). The %.0f is the tool prefix %d plus doctrine, CLI text and message." % (len(ps2), len(pairs), a, b, 1 / b, a, P))
        print("At the last snapshot (%d chars) the banner is about %d tokens = %.0f%% of a base of %d.\n" % (snaps[-1][1], b * snaps[-1][1], 100 * b * snaps[-1][1] / (a + b * snaps[-1][1]), a + b * snaps[-1][1]))

    def B_at(t):    # linear interpolation between neighbouring anchors (INFERRED for turns that are not anchors)
        if t <= anchors[0][0]:
            return anchors[0][1]
        for (t0, b0, _, _), (t1, b1, _, _) in zip(anchors, anchors[1:]):
            if t0 <= t <= t1:
                return b0 + (b1 - b0) * ((t - t0) / (t1 - t0) if t1 > t0 else 0)
        return anchors[-1][1]
    for r in rows:
        r["B"] = B_at(r["t"])
        r["n"] = 1 + API_PER_TOOL * r["nt"]            # INFERRED number of API calls
        r["G"] = r["T"] - r["n"] * r["B"]               # the part of T that is not calls x base

    # ---- 2. halves
    h = n // 2
    A, Z = rows[:h], rows[h:]
    print("## 2. First half vs last half (the report's split, %d turns each; boundary %s)\n" % (h, hhmm(Z[0]["t"])))
    print("| | first %d | last %d |\n|---|---|---|" % (h, n - h))
    spec = [("input T median", lambda rs: st.median(r["T"] for r in rs), "%d"),
            ("input T mean", lambda rs: st.mean(r["T"] for r in rs), "%d"),
            ("input T p95", lambda rs: q([r["T"] for r in rs], .95), "%d"),
            ("calls (reported) median", lambda rs: st.median(r["calls"] for r in rs), "%.1f"),
            ("API calls (est.) median", lambda rs: st.median(r["n"] for r in rs), "%.1f"),
            ("turns with no tool", lambda rs: sum(1 for r in rs if r["nt"] == 0), "%d"),
            ("base B median (anchors + interpolation)", lambda rs: st.median(r["B"] for r in rs), "%d"),
            ("last-call context L median", lambda rs: st.median(r["L"] for r in rs), "%d"),
            ("tool result bytes median", lambda rs: st.median(r["resB"] for r in rs), "%d"),
            ("tool result bytes mean", lambda rs: st.mean(r["resB"] for r in rs), "%d"),
            ("cache-read share (sum)", lambda rs: sum(r["cr"] for r in rs) / sum(r["T"] for r in rs), "%.3f"),
            ("cache-read share (median turn)", lambda rs: st.median(r["cr"] / r["T"] for r in rs), "%.3f"),
            ("cost median $", lambda rs: st.median(r["cost"] for r in rs), "%.4f"),
            ("cost mean $", lambda rs: st.mean(r["cost"] for r in rs), "%.4f")]
    for name, f, fmt in spec:
        print("| %s | %s | %s |" % (name, fmt % f(A), fmt % f(Z)))
    print("\nSame kind of turn in both halves (MEASURED median T):\n\n| kind | first half: n / T | last half: n / T |\n|---|---|---|")
    for lab, pred in [("no tool", lambda r: r["nt"] == 0), ("1-2 tools", lambda r: 1 <= r["nt"] <= 2),
                      ("3-9 tools", lambda r: 3 <= r["nt"] <= 9), ("10+ tools", lambda r: r["nt"] >= 10)]:
        cells = []
        for rs in (A, Z):
            sel = [r["T"] for r in rs if pred(r)]
            cells.append("%d / %s" % (len(sel), "%d" % st.median(sel) if sel else "-"))
        print("| %s | %s | %s |" % (lab, cells[0], cells[1]))
    print()

    # ---- 3. attribution of the mean growth (INFERRED)
    print("## 3. Where the growth of the MEAN input comes from (INFERRED: n = 1 + %.2f x tools; B interpolated)\n" % API_PER_TOOL)
    mT = [st.mean(r["T"] for r in rs) for rs in (A, Z)]
    mn = [st.mean(r["n"] for r in rs) for rs in (A, Z)]
    mB = [st.mean(r["B"] for r in rs) for rs in (A, Z)]
    mnB = [st.mean(r["n"] * r["B"] for r in rs) for rs in (A, Z)]
    mG = [st.mean(r["G"] for r in rs) for rs in (A, Z)]
    d = mT[1] - mT[0]
    calls_eff = (mn[1] - mn[0]) * mB[0]
    base_eff = mn[1] * (mB[1] - mB[0])
    print("| term | first | last | change | share of the change |\n|---|---|---|---|---|")
    print("| mean input T | %d | %d | %+d | 100%% |" % (mT[0], mT[1], d))
    print("| calls x base (n x B) | %d | %d | %+d | %.0f%% |" % (mnB[0], mnB[1], mnB[1] - mnB[0], 100 * (mnB[1] - mnB[0]) / d))
    print("|  of which more calls | %.1f -> %.1f | | %+d | %.0f%% |" % (mn[0], mn[1], calls_eff, 100 * calls_eff / d))
    print("|  of which bigger base | %d -> %d | | %+d | %.0f%% |" % (mB[0], mB[1], base_eff, 100 * base_eff / d))
    print("| tool part G = T - n x B (results and own calls re-sent) | %d | %d | %+d | %.0f%% |" % (mG[0], mG[1], mG[1] - mG[0], 100 * (mG[1] - mG[0]) / d))

    tot = collections.defaultdict(lambda: [0, 0, 0])
    echo_b = 0
    for r in rows:
        for t in r["tools"]:
            a = tot[t["name"]]; a[0] += 1; a[1] += len(t["res"]); a[2] = max(a[2], len(t["res"]))
            nb = result_without_echo(t["res"]) if t["name"] in ("entity_write", "run_cliff") else len(t["res"])
            t["res_noecho"] = nb
            echo_b += len(t["res"]) - nb
    allb = sum(a[1] for a in tot.values())
    print("\nTool results over all %d turns: %d bytes (MEASURED).\n" % (n, allb))
    print("| tool | calls | result bytes | share | largest |\n|---|---|---|---|---|")
    for k, a in sorted(tot.items(), key=lambda kv: -kv[1][1]):
        print("| %s | %d | %d | %.0f%% | %d |" % (k, a[0], a[1], 100 * a[1] / allb, a[2]))
    bigw = [len(t["res"]) for r in rows for t in r["tools"] if t["name"] in ("entity_write", "run_cliff") and len(t["res"]) > 10000]
    print("\nWrite/run_cliff replies over 10 KB: %d replies, %d bytes = %.0f%% of all result bytes. Bytes that are copies of the banner/prompt getters (what B2 stops): %d = %.0f%% of all result bytes.\n" % (
        len(bigw), sum(bigw), 100 * sum(bigw) / allb, echo_b, 100 * echo_b / allb))

    # ---- 4. top 10
    print("## 4. The ten largest turns by input\n")
    print("| time | input T | calls | last ctx L | result bytes | write replies >10KB: n (bytes) | echo bytes | input saved without the echo | user message |\n|---|---|---|---|---|---|---|---|---|")
    top = sorted(rows, key=lambda r: -r["T"])[:10]
    for r in top:
        big = [len(t["res"]) for t in r["tools"] if t["name"] in ("entity_write", "run_cliff") and len(t["res"]) > 10000]
        eb = sum(len(t["res"]) - t.get("res_noecho", len(t["res"])) for t in r["tools"])
        s0 = seq_model(r["B"], P, [len(t["res"]) + len(t["inp"]) for t in r["tools"]])[0]
        s1 = seq_model(r["B"], P, [t.get("res_noecho", len(t["res"])) + len(t["inp"]) for t in r["tools"]])[0]
        print("| %s | %d | %d | %d | %d | %d (%d) | %d | %.0f%% | %s |" % (hhmm(r["t"]), r["T"], r["calls"], r["L"], r["resB"], len(big), sum(big), eb, 100 * (1 - s1 / s0), r["text"][:50].replace("\n", " ").replace("|", "/")))
    print("\nThe top 10 are %.0f%% of all input tokens of the %d turns and %.0f%% of the cost.\n" % (
        100 * sum(r["T"] for r in top) / sum(r["T"] for r in rows), n, 100 * sum(r["cost"] for r in top) / sum(r["cost"] for r in rows)))

    # ---- 5. cache and cost
    print("## 5. Cache and cost\n")
    print("| | first %d | last %d |\n|---|---|---|" % (h, n - h))
    def cs(rs):
        return sum(r["cr"] for r in rs), sum(r["cw"] for r in rs), sum(r["T"] for r in rs), sum(r["out"] for r in rs)
    for lab, f in [("cache-read share of tokens", lambda x: x[0] / x[2]), ("cache-write share of tokens", lambda x: x[1] / x[2]),
                   ("cost of cache writes $", lambda x: x[1] * pw), ("cost of cache reads $", lambda x: x[0] * pr),
                   ("cost of output $", lambda x: x[3] * po)]:
        v = [f(cs(rs)) for rs in (A, Z)]
        print("| %s | %.3f | %.3f |" % (lab, v[0], v[1]))
    for lab, rs in (("first", A), ("last", Z)):
        x = cs(rs); tc = x[0] * pr + x[1] * pw + x[3] * po
        print("\n(%s half cost: writes %.0f%%, reads %.0f%%, output %.0f%%)" % (lab, 100 * x[1] * pw / tc, 100 * x[0] * pr / tc, 100 * x[3] * po / tc), end="")
    ident = [abs(r["cw"] - (r["L"] - P)) for r in rows if r["cr"] > 0]
    print("\n\nIdentity MEASURED: tokens written to the cache in a turn = L - P (last context minus the read prefix); median |error| %d tokens. So cache-read share = 1 - (L-P)/T, and rises with calls per turn." % st.median(ident))
    print("A cache write costs %.0fx a cache read (MEASURED price fit).\n" % (pw / pr))

    comp = collections.defaultdict(float)
    for r in rows:
        Bt, nn = r["B"], r["n"]
        comp["tool definitions prefix (read every call)"] += nn * P * pr
        comp["system prompt + banner + message (written once, read on later calls)"] += (Bt - P) * pw + (nn - 1) * (Bt - P) * pr
        comp["output tokens"] += r["out"] * po
    tc = sum(r["cost"] for r in rows)
    comp["tool results and own calls (the rest)"] = tc - sum(comp.values())
    print("Cost by component, all %d turns, total $%.2f (INFERRED: n and B as in section 3):\n\n| component | $ | share |\n|---|---|---|" % (n, tc))
    for k, v in sorted(comp.items(), key=lambda kv: -kv[1]):
        print("| %s | %.2f | %.0f%% |" % (k, v, 100 * v / tc))
    nt0 = [r["cost"] for r in rows if r["nt"] == 0]
    print("\nA no-tool turn: base %d tokens written at $%.3f/M = $%.4f of a median $%.4f (MEASURED).\n" % (
        st.median(bs) - P, pw * 1e6, (st.median(bs) - P) * pw, st.median(nt0) if nt0 else 0))

    # ---- 6. counterfactuals
    print("## 6. What each change would have saved on these %d turns (INFERRED)\n" % n)
    ident_b = lambda r: r["B"]
    ident_r = lambda t: len(t["res"])
    def run(fb, fr):
        Tm = c_ = 0
        for r in rows:
            Ts, rd, wr = seq_model(fb(r), P, [fr(t) + len(t["inp"]) for t in r["tools"]])
            Tm += Ts; c_ += rd * pr + wr * pw
        return Tm, c_
    T0, c0 = run(ident_b, ident_r)
    meas_noout = sum(r["cost"] - r["out"] * po for r in rows)
    print("Model check: the sequential model (each tool its own API call) gives $%.2f against a measured $%.2f without output (ratio %.2f; it over-counts calls). Savings below are the model's percentage applied to the measured cost.\n" % (c0, meas_noout, c0 / meas_noout))
    print("| change | input tokens saved | cost saved | $ saved of $%.2f |\n|---|---|---|---|" % tc)
    floor = min(bs)
    for lab, fb, fr in [
            ("(a) write/run_cliff replies without the copies of the banner/prompt getters (B2 shape replayed)", ident_b, lambda t: t.get("res_noecho", len(t["res"]))),
            ("(b) every tool result capped at 8 KB (the existing per-stream cap of the write reply)", ident_b, lambda t: min(len(t["res"]), 8192)),
            ("(c) a and b together", ident_b, lambda t: min(t.get("res_noecho", len(t["res"])), 8192)),
            ("(d) base never above its smallest measured value (%d tokens)" % floor, lambda r: min(r["B"], floor), ident_r),
            ("(e) a and d together", lambda r: min(r["B"], floor), lambda t: t.get("res_noecho", len(t["res"])))]:
        T1, c1 = run(fb, fr)
        print("| %s | %.0f%% | %.0f%% | $%.2f |" % (lab, 100 * (1 - T1 / T0), 100 * (1 - c1 / c0), (c0 - c1) * meas_noout / c0))
    Ta, ca = run(ident_b, lambda t: t.get("res_noecho", len(t["res"])))
    print("\nSo the copies of the banner/prompt getters in write replies are about %.0f%% of the whole input cost of the %d turns (the model's c0 - c(a), INFERRED)." % (100 * (c0 - ca) / c0, n))
    print("\nNewest turns in the sample (MEASURED):")
    for r in rows[-2:]:
        print("  %s: calls %d, tool result bytes %d, input %d, cost $%.4f" % (hhmm(r["t"]), r["calls"], r["resB"], r["T"], r["cost"]))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main()
