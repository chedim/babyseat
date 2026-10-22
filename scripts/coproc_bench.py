"""The coprocessor's measurement harness (#454). Code only: the labelled sets
are the operator's and stay out of the repo.

  run     a labelled set (JSONL: {"id", "person", "memory"?, "history"?,
          "expected": [{"words": ["decide", ...], "match": "<regex>"}]})
          through one model in each surface; scores right / missed / extra /
          unparsed per surface, plus how many right actions the injection
          guard applies (TRACE_MIN on the person's own message).
            python scripts/coproc_bench.py run --set SET.jsonl --model qwen3:14b
            python scripts/coproc_bench.py run --set SET.jsonl --model claude:haiku --max-calls 60
          (claude:* runs the Agent SDK with no tools and strict MCP; it needs
          claude_agent_sdk installed in the interpreter that runs this.)
  recall  the capture->recall check: the same facts captured as prose turns
          only vs as prose turns plus the executor's notes (what an applied
          remember writes), each asked back through /membrane/ask.
            python scripts/coproc_bench.py recall [--facts FACTS.jsonl] [--embed mxbai-embed-large]
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import types
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import coproc as CP  # noqa: E402

SURFACES = CP.SURFACES


# ---- scoring ------------------------------------------------------------------

def _arg_text(a) -> str:
    if a.word == "remember":
        return f"{a.args.get('key', '')} {a.args.get('value', '')}"
    if a.word == "tool":
        return json.dumps(a.args)
    return a.args.get("text", "")


def score(item: dict, output: str, surface: str) -> dict:
    p = CP.parse(output, surface)
    exp = list(item.get("expected") or [])
    used = [False] * len(p.actions)
    right = guard = 0
    for e in exp:
        rx = re.compile(e.get("match") or ".", re.I)
        for i, a in enumerate(p.actions):
            if not used[i] and a.word in e.get("words", []) and rx.search(_arg_text(a)):
                used[i] = True
                right += 1
                guard += int(CP.traceable(a, item.get("person", "")))
                break
    extra = sum(1 for u in used if not u)
    extra_held = sum(1 for i, a in enumerate(p.actions)
                     if not used[i] and not CP.traceable(a, item.get("person", "")))
    return {"expected": len(exp), "right": right, "missed": len(exp) - right,
            "extra": extra, "extra_held": extra_held, "unparsed": len(p.lint),
            "calls": len(p.actions), "guard_applied": guard,
            "reply": CP.reply_of(p, surface)}


def run(items, ask, surfaces=SURFACES, log=None) -> dict:
    """ask(system, user, surface) -> (output, tokens_in, tokens_out)."""
    tot = {s: {"expected": 0, "right": 0, "missed": 0, "extra": 0, "extra_held": 0,
               "unparsed": 0, "calls": 0, "guard_applied": 0, "tok_in": 0, "tok_out": 0,
               "errors": 0}
           for s in surfaces}
    for it in items:
        for s in surfaces:
            sys_, user = CP.compile_turn(s, it.get("memory") or {}, it.get("history") or [],
                                         it.get("person", ""))
            try:
                out, tin, tout = ask(sys_, user, s)
            except Exception as e:
                out, tin, tout = "", 0, 0
                tot[s]["errors"] += 1
                if log:
                    log(f"[{it.get('id')}/{s}] error {type(e).__name__}: {str(e)[:120]}")
            sc = score(it, out, s)
            for k in ("expected", "right", "missed", "extra", "extra_held", "unparsed",
                      "calls", "guard_applied"):
                tot[s][k] += sc[k]
            tot[s]["tok_in"] += int(tin or 0)
            tot[s]["tok_out"] += int(tout or 0)
            if log:
                log(json.dumps({"id": it.get("id"), "surface": s, "out": out,
                                **{k: sc[k] for k in sc if k != "reply"}}, ensure_ascii=False))
    return tot


# ---- the models -----------------------------------------------------------------

def ollama_ask(model: str, base: str = "http://localhost:11434"):
    def ask(sys_, user, surface):
        body = {"model": model, "stream": False, "think": False,
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": sys_},
                             {"role": "user", "content": user}]}
        rq = urllib.request.Request(base + "/api/chat", data=json.dumps(body).encode(),
                                    headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(rq, timeout=600).read())
        return (r.get("message") or {}).get("content", ""), r.get("prompt_eval_count", 0), \
            r.get("eval_count", 0)
    return ask


def claude_ask(model: str, max_calls: int):
    """The Agent SDK with no tools at all and strict MCP (no configured
    servers): a python-shaped line can only be text."""
    import asyncio
    import claude_agent_sdk as sdk
    n = {"calls": 0}

    def ask(sys_, user, surface):
        if n["calls"] >= max_calls:
            raise RuntimeError("the call budget is spent")
        n["calls"] += 1

        async def go():
            opts = sdk.ClaudeAgentOptions(
                system_prompt=sys_, tools=[], allowed_tools=[], max_turns=1, model=model,
                setting_sources=[], mcp_servers={}, extra_args={"strict-mcp-config": None})
            text, usage = [], {}
            async for m in sdk.query(prompt=user, options=opts):
                if type(m).__name__ == "AssistantMessage":
                    for b in m.content:
                        if hasattr(b, "text"):
                            text.append(b.text)
                        elif hasattr(b, "name"):
                            text.append(f"\n#TOOLCALL {b.name}\n")
                if type(m).__name__ == "ResultMessage":
                    usage = getattr(m, "usage", {}) or {}
            return "".join(text), usage
        out, u = asyncio.run(go())
        tin = sum(int(u.get(k, 0) or 0) for k in
                  ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        return out, tin, int(u.get("output_tokens", 0) or 0)
    return ask


# ---- capture -> recall --------------------------------------------------------------

DEFAULT_FACTS = [
    ("the dinghy motor is a 2.5 hp suzuki", "dinghy_motor", "2.5 hp Suzuki",
     "what motor does the dinghy have?", "suzuki"),
    ("the anchor chain is 50 meters long", "anchor_chain", "50 m",
     "how long is the anchor chain?", r"50"),
    ("the HA server moved, it is at 192.168.1.159 now", "ha_server", "192.168.1.159",
     "what is the HA server's address?", r"192\.168\.1\.159"),
    ("my sister's birthday is march 3rd", "sister_birthday", "March 3",
     "when is my sister's birthday?", "march"),
    ("the boat seat listens on port 4097", "boat_seat_port", "4097",
     "which port does the boat seat use?", "4097"),
    ("the regatta starts at 10:30 on saturday", "regatta_start", "10:30 Saturday",
     "when does the regatta start?", r"10:30"),
    ("the spare shear pins are in the blue locker", "shear_pins_location", "blue locker",
     "where are the spare shear pins?", "blue"),
    ("our mooring ball number is 42", "mooring_ball", "42",
     "what is our mooring ball number?", r"\b42\b"),
    ("the fridge draws 4 amps", "fridge_draw", "4 A",
     "how much current does the fridge draw?", r"\b4\b"),
    ("the wifi password on the boat is in the logbook, page 3", "wifi_password_location",
     "logbook page 3", "where is the boat's wifi password written?", "logbook"),
    ("the outboard takes 2-stroke oil at 50:1", "outboard_oil", "2-stroke 50:1",
     "what oil mix does the outboard take?", r"50:1"),
    ("the jib is 18 square meters", "jib_area", "18 m2",
     "how big is the jib?", r"\b18\b"),
]
CHATTER = ["good morning", "thanks, that helps", "what's the weather like?",
           "let's look at the logs later", "ok", "can you summarize the last release?"]


def _facts(path=None) -> list[dict]:
    if path:
        return [json.loads(ln) for ln in Path(path).read_text().splitlines() if ln.strip()]
    return [{"person": p, "key": k, "value": v, "question": q, "expect": e}
            for p, k, v, q, e in DEFAULT_FACTS]


def _ask_handler(fld, q: str, k: int) -> dict:
    """GET /membrane/ask?q=... on a bare handler over `fld`."""
    import urllib.parse
    from membrane import lanes as L
    srv = types.SimpleNamespace(efield=fld, fid="bench", capturer=None,
                                membrane=types.SimpleNamespace(embedder=fld.emb))
    h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
    h.server, h.command, h.headers = srv, "GET", {}
    h.path = "/membrane/ask?" + urllib.parse.urlencode({"q": q, "k": k})
    out = []
    h._json_out = lambda obj, *a, **kw: out.append(obj)
    h._membrane_ask()
    return out[-1] if out else {}


def recall(facts, embedder, k: int = 3) -> dict:
    """Two fresh fields: 'prose' holds the facts as captured chat turns only;
    'actions' holds the same turns plus the note an applied remember writes
    ('key: value'). Each fact's question goes through /membrane/ask; a hit is
    the expected value in the top-1 / top-k answers."""
    from membrane import efield as EF
    from membrane.capture import Capturer
    from membrane.efstore import MemBackend
    out = {}
    for arm in ("prose", "actions"):
        fld = EF.EntityField(MemBackend(), embedder, f"bench-{arm}@local")
        cap = Capturer(fld, mode="on", start=False)
        for i, f in enumerate(facts):
            cap.on_turn(CHATTER[i % len(CHATTER)], "Sure.", conv="bench")
            cap.on_turn(f["person"], "Noted.", conv="bench")
        cap.flush()
        if arm == "actions":
            for f in facts:
                fld.learn(f"{f['key']}: {f['value']}", src="user")
        top1 = topk = 0
        for f in facts:
            res = _ask_handler(fld, f["question"], k)
            rx = re.compile(f["expect"], re.I)
            hits = [str(h.get("action") or "") + " " + str(h.get("situation") or "")
                    for h in res.get("hits") or []]
            top1 += int(bool(hits) and bool(rx.search(hits[0])))
            topk += int(any(rx.search(x) for x in hits))
        out[arm] = {"n": len(facts), "top1": top1, f"top{k}": topk}
    return out


# ---- the command line ------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--set", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--surfaces", default=",".join(SURFACES))
    r.add_argument("--max-calls", type=int, default=60)
    r.add_argument("--log")
    c = sub.add_parser("recall")
    c.add_argument("--facts")
    c.add_argument("--embed", default="mxbai-embed-large")
    c.add_argument("-k", type=int, default=3)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        items = [json.loads(ln) for ln in Path(a.set).read_text().splitlines() if ln.strip()]
        surfaces = tuple(s for s in a.surfaces.split(",") if s in SURFACES)
        if a.model.startswith("claude:"):
            ask = claude_ask(a.model.split(":", 1)[1], a.max_calls)
        else:
            ask = ollama_ask(a.model)
        logf = open(a.log, "w", encoding="utf-8") if a.log else None
        tot = run(items, ask, surfaces,
                  log=(lambda s: (logf.write(s + "\n"), logf.flush())) if logf else None)
        print(json.dumps({"model": a.model, "items": len(items), "totals": tot}, indent=1))
        return 0
    from membrane.embed import OllamaEmbedder
    print(json.dumps(recall(_facts(a.facts), OllamaEmbedder(model=a.embed), a.k), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
