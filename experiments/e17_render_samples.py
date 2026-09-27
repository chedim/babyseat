"""E17 -- SAMPLE RECALL BLOCKS: the same recalled memories rendered four
ways (membrane/render.py), plus what a model answers from each.

Pool: .membrane/e17-pool.json (the recallable memories of this project's
live field + the old snapshot -- built once, see the session notes; the
same recall hygiene the seat applies). For each question: the top
candidates by cosine (the seat's --no-context budget: 7200 chars, up to
24 picks), rendered as today / spans / spans+tags / clustered; each block
is handed to a local model with the question, and its answer recorded.
One e16 free-edit rendering of the top memory rides along as the
readability contrast.

    PYTHONPATH=. .venv/bin/python experiments/e17_render_samples.py

Writes .membrane/e17-samples.json (gitignored: it holds field text).
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

from membrane.embed import OllamaEmbedder
from membrane.fidelity import fit, region_words
from membrane.render import (_unit, clean, clip, doc_freq, render_clustered,
                             render_spans, render_today)
from membrane.rag import strip_machinery_text
from membrane.slash import SlashVocab
from membrane.token_timeline import tokenize

OLLAMA = "http://localhost:11434"
QUESTIONS = [
    "why does membrane keep picking the dumbest models, and how did we fix "
    "the routing?",
    "how long do membrane restarts take?",
    "opencode does not launch, can it select another port?",
]
BUDGET = 7200          # --no-context: --ctx-chars 2400 x3
FLOOR = 0.55           # candidates below this never enter the pool
ANSWER_MODEL = "llama3.1:8b"
SYSTEM = ("You are a coding assistant working on the 'membrane' project. "
          "The block below is recalled from the project's long-term memory. "
          "Answer the question using it; if it does not contain the answer, "
          "say so plainly. Be brief (<= 6 sentences).\n\n")

_SECRET = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)(\S+)"
    r"|sk-[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{20,}|gh[pous]_[A-Za-z0-9]{20,}")


def redact(s: str) -> str:
    return _SECRET.sub(lambda m: (m.group(1) + m.group(2) + "<redacted>")
                       if m.group(1) else "<redacted>", s)


def ask(block: str, q: str) -> str:
    body = json.dumps({"model": ANSWER_MODEL, "stream": False,
                       "options": {"temperature": 0.0},
                       "messages": [{"role": "system", "content": SYSTEM + block},
                                    {"role": "user", "content": q}]})
    rq = urllib.request.Request(OLLAMA + "/api/chat", data=body.encode(),
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=600) as r:
        return json.load(r)["message"]["content"].strip()


def main() -> None:
    pool_all = json.loads(Path(".membrane/e17-pool.json").read_text())
    emb = OllamaEmbedder(model="mxbai-embed-large")
    M = _unit([p["vec"] for p in pool_all])
    df, n = doc_freq(p["situation"] + " " + p["action"] for p in pool_all)
    out = {"budget": BUDGET, "answer_model": ANSWER_MODEL,
           "pool_size": len(pool_all), "questions": []}
    for q in QUESTIONS:
        t0 = time.time()
        s = M @ _unit(emb.embed([q])[0])
        order = [i for i in np.argsort(-s) if s[i] >= FLOOR][:24]
        # the seat's surface hygiene (rag._memory_block): machinery spans
        # (<system-reminder> ...) are stripped before anything renders
        pool = [dict(pool_all[i], sim=float(s[i]),
                     situation=strip_machinery_text(pool_all[i]["situation"]),
                     action=strip_machinery_text(pool_all[i]["action"]))
                for i in order]
        today, t_used = render_today(pool, BUDGET)
        spans_b, s_used, s_fid = render_spans(pool, emb, BUDGET, df, n,
                                              tags=False)
        tags_b, g_used, _ = render_spans(pool, emb, BUDGET, df, n, tags=True)
        clus_b, c_used = render_clustered(pool, emb, BUDGET, df, n)
        # fidelity of today's clipped situations, same measure as spans'
        t_fid = [float(_unit(emb.embed([clip(pool[i]["situation"], 220)])[0])
                       @ _unit(pool[i]["vec"])) for i in t_used]
        blocks = {"today": (today, t_used, t_fid),
                  "spans": (spans_b, s_used, s_fid),
                  "spans+tags": (tags_b, g_used, None),
                  "clustered": (clus_b, c_used, None)}
        rec = {"question": q, "top_sim": float(s.max()),
               "pool": [{"sim": p["sim"], "situation": redact(clean(p["situation"]))[:300]}
                        for p in pool], "renderings": {}}
        for name, (b, used, fid) in blocks.items():
            print(f"  [{q[:30]}] answering from {name}", file=sys.stderr)
            rec["renderings"][name] = {
                "block": redact(b), "chars": len(b),
                "memories": len(set(used)),
                "fidelity": (float(np.mean(fid)) if fid else None),
                "answer": redact(ask(b, q))}
        # the readability contrast: e16's free-edit search on the top memory,
        # starting from today's clipped situation
        top = pool[0]
        start = clip(top["situation"], 220)
        nb = [j for j in np.argsort(-(M @ _unit(top["vec"])))[1:40]][:10]
        region = region_words([pool_all[j]["situation"] for j in nb],
                              exclude=set(tokenize(start)))
        vocab = getattr(main, "_vocab", None) or SlashVocab(
            emb, [p["situation"] for p in pool_all])
        main._vocab = vocab
        fitted, trace = fit(start, np.asarray(top["vec"]), emb, vocab,
                            region, [], rounds=5)
        rec["contrast"] = {"start": redact(start), "fitted": redact(fitted),
                           "cos_start": trace[0]["cos"],
                           "cos_fitted": trace[-1]["cos"],
                           "ops": [t["op"] for t in trace[1:]]}
        rec["seconds"] = round(time.time() - t0, 1)
        out["questions"].append(rec)
        Path(".membrane/e17-samples.json").write_text(json.dumps(out, indent=1))
    print("wrote .membrane/e17-samples.json", file=sys.stderr)


if __name__ == "__main__":
    main()
