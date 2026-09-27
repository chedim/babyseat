"""E24 -- TWO STREAMS: discourse (what people SAY about the project) and
observations (what the project SHOWS: tool calls and their results),
split from the audit log, each read the way it is written.

  discourse     your messages (plain turns, and the message inside every
                tool wrapper -- the intent the tool served) + the model's
                prose answers. Read as language (E23's test 1).
  observations  one event per tool use: tool, parsed args, result text,
                an outcome class, the message it served. Read as
                STRUCTURE: files from filePath/paths, commands from shell
                heads, ports, grep patterns; facts = (entity, tool,
                outcome).
  distilled     compact notes -- the compactor's summaries of the other
                two (E17 found them wrong at times): kept apart, counted.

Then the join: which discourse entities have OBSERVED evidence (the same
file/command/symbol appears in both streams)?

    PYTHONPATH=. .venv/bin/python experiments/e24_two_streams.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e18_entity_field import embed_cached  # noqa: E402
from e23_word_field import PROBES, sentences_of, word_field  # noqa: E402
from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.entities import extract_entities  # noqa: E402
from membrane.rag import strip_machinery_text  # noqa: E402

_TOOL = re.compile(r"^(compact note:\s*)?tool (\w+) for:\s*(.*?)(?:\nargs:\s*(\{.*))?$", re.S)
_CALL = re.compile(r"^(compact note:\s*)?tool call for:\s*(.*?)\s*->\s*(\w+)\(args\)\s*$", re.S)
_UNCLOSED = re.compile(r"<(system-reminder|system-warning)>.*$", re.S | re.I)
_PORT = re.compile(r"(?<![\w.\"]):(\d{4,5})\b|(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{4,5})\b")
_SHELL_HEADS = re.compile(r"(?:^|;|&&|\|\||\||\$\()\s*([a-z][\w./-]*)")
_INLINE = re.compile(r"\b(\w+)\((\{.*)$", re.S)     # 'pls continue bash({...})'
_ARGS_TAIL = re.compile(r"\s*args:\s*\{.*$", re.S)
_ARROW = re.compile(r"\s*->\s*\w+\(args\)")
_ARGS_ANY = re.compile(r"\s*\bargs:\s*\{[^\n]*")
_INLINE_ANY = re.compile(r"\b\w+\(\{[^\n]*")


def discourse_clean(text: str) -> str:
    """Every capture shape of tool text, removed wherever it sits: the
    '-> bash(args)' marker, an 'args: {...}' run, an inline 'name({...'
    call -- each to the end of its line (the tool text never shares a
    line with prose after it in these captures)."""
    t = _ARROW.sub("", text)
    t = _ARGS_ANY.sub("", t)
    t = _INLINE_ANY.sub("", t)
    return t.strip()


def clean(text: str) -> str:
    t = strip_machinery_text(text or "")
    return " ".join(_UNCLOSED.sub("", t).split())


def outcome(tool: str, result: str) -> str:
    r = (result or "").lower()
    if tool == "edit" or tool == "write":
        return "applied" if "successfully" in r else ("failed" if r else "unknown")
    if "not found" in r or "no files found" in r or "no such file" in r:
        return "not-found"
    if "traceback" in r or "error" in r[:400] or "failed" in r[:400]:
        return "error"
    m = re.search(r"(\d+) passed", r) or re.search(r"ran (\d+) tests", r)
    if m:
        return "tests-ok" if "fail" not in r else "tests-failed"
    return "ok" if r.strip() else "empty"


def unescape(v):
    """Captures sometimes double-escape: a literal backslash-n where the
    command had a newline ('\\nsys.path.insert' -> a fake symbol)."""
    if isinstance(v, str) and "\\n" in v:
        return v.replace("\\n", "\n").replace("\\t", " ").replace('\\"', '"')
    return v


def parse_args(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return {k: unescape(x) for k, x in v.items()} if isinstance(v, dict) else {}
    except ValueError:
        out = {}
        for k in ("filePath", "command", "pattern", "path"):
            m = re.search(rf'"{k}"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
            if m:
                out[k] = unescape(m.group(1))
        return out


_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\s*\1\b", re.S)
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def shell_skeleton(cmd: str) -> str:
    """The command with heredoc bodies and quoted strings removed: what is
    left is shell structure (command names, flags, paths) -- the python
    run INSIDE a heredoc or `-c "..."` is not a command."""
    t = _HEREDOC.sub(" ", cmd)
    return _QUOTED.sub(" ", t)


def obs_entities(tool: str, args: dict, result: str) -> dict:
    """Entities an observation touches, read from its structure."""
    ents: dict[str, str] = {}
    result = unescape(result or "")
    if tool == "todowrite":                  # a plan, not an observation
        return {"tool:" + tool: tool}
    fp = args.get("filePath") or args.get("path")
    if isinstance(fp, str) and fp:
        ents["files:" + fp.rsplit("/", 1)[-1].lower()] = fp
    cmd = args.get("command")
    if isinstance(cmd, str):
        for h in _SHELL_HEADS.findall(shell_skeleton(cmd)):
            if h not in ("cd", "echo", "then", "do", "done", "fi", "if", "for", "true"):
                ents["cmd:" + h.rsplit("/", 1)[-1]] = h
        for p in (a or b for a, b in _PORT.findall(cmd)):
            ents["port:" + p] = p
    pat = args.get("pattern")
    if isinstance(pat, str) and pat and tool == "grep":
        ents["pattern:" + pat.lower()[:40]] = pat
    arg_text = " ".join(v for v in args.values() if isinstance(v, str))[:2000]
    for kind, vals in extract_entities(arg_text, "").items():
        for v in vals:
            if kind == "files":
                ents["files:" + v.rsplit("/", 1)[-1].lower()] = v
            elif kind in ("symbols", "models"):
                ents[f"{kind}:{v.lower()}"] = v
    for kind, vals in extract_entities((result or "")[:3000], "").items():
        if kind in ("symbols", "files"):
            for v in vals[:8]:
                key = "files:" + v.rsplit("/", 1)[-1].lower() if kind == "files" else f"symbols:{v.lower()}"
                ents.setdefault(key, v)
    for p in [a or b for a, b in _PORT.findall(result or "")][:4]:
        ents.setdefault("port:" + p, p)
    ents["tool:" + tool] = tool
    return ents


def split_audit(path: Path):
    discourse, observations, distilled = [], [], []
    seen_d, seen_o = set(), set()
    rows = []
    for line in path.open():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "decision" and r.get("query") and r.get("ts"):
            rows.append(r)
    rows.sort(key=lambda r: r["ts"])
    for r in rows:
        q, resp, ts = r["query"], r.get("response") or "", r["ts"]
        if q.startswith("compact note"):
            distilled.append({"ts": ts, "text": clean(q)[:600] + " => " + clean(resp)[:600]})
            continue
        m, c = _TOOL.match(q), _CALL.match(q)
        if m:
            tool, msg, raw = m.group(2), clean(m.group(3)), m.group(4)
            args = parse_args(raw)
            key = (tool, json.dumps(args, sort_keys=True)[:300], resp[:200])
            if key not in seen_o:
                seen_o.add(key)
                observations.append({"ts": ts, "tool": tool, "args": args,
                                     "result": resp[:4000], "served": msg,
                                     "outcome": outcome(tool, resp)})
            text, model_said = msg, ""       # the intent joins discourse
        elif c:
            text, model_said = clean(c.group(2)), ""   # the call is not discourse
        else:
            qc = _ARGS_TAIL.sub("", clean(q))
            mi = _INLINE.search(qc)
            if mi:
                args = parse_args(mi.group(2).rstrip(")"))
                key = (mi.group(1), json.dumps(args, sort_keys=True)[:300], "")
                if key not in seen_o:
                    seen_o.add(key)
                    observations.append({"ts": ts, "tool": mi.group(1), "args": args,
                                         "result": "", "served": qc[:mi.start()].strip(),
                                         "outcome": "call-only"})
                qc = qc[:mi.start()].strip()
            ra = _ARGS_TAIL.sub("", clean(resp))
            mr = _INLINE.search(ra)
            ra = ra[:mr.start()].strip() if mr else ra
            text = (qc + "\n" + ra[:600]).strip()
            model_said = discourse_clean(ra)
        text = discourse_clean(text)
        if len(text) >= 12 and text.lower() not in seen_d:
            seen_d.add(text.lower())
            discourse.append({"ts": ts, "q": clean(q)[:300] if not (m or c) else text,
                              "text": text, "model": model_said})
    return discourse, observations, distilled


def main() -> None:
    emb = OllamaEmbedder(model="mxbai-embed-large")
    discourse, obs, distilled = split_audit(Path(".membrane/audit.jsonl"))
    print(f"SPLIT: {len(discourse)} discourse messages, {len(obs)} observations "
          f"(distinct tool uses), {len(distilled)} compact notes kept apart")
    print("   tools:", dict(Counter(o["tool"] for o in obs).most_common(10)))
    print("   outcomes:", dict(Counter(o["outcome"] for o in obs).most_common()))

    # ---- observations: entities + facts ------------------------------------
    ent_obs = defaultdict(list)
    for i, o in enumerate(obs):
        o["ents"] = obs_entities(o["tool"], o["args"], o["result"])
        for e in o["ents"]:
            if not e.startswith("tool:"):
                ent_obs[e].append(i)
    kinds = Counter(e.split(":", 1)[0] for e in ent_obs)
    print(f"\nOBSERVED ENTITIES: {len(ent_obs)} ({dict(kinds)})")
    for kind in ("files", "cmd", "symbols", "port"):
        top = sorted((e for e in ent_obs if e.startswith(kind + ":")),
                     key=lambda e: -len(ent_obs[e]))[:12]
        print(f"   {kind:<8}", ", ".join(f"{e.split(':', 1)[1]} ({len(ent_obs[e])})" for e in top))
    print("\n   facts, for a few entities (tool -> outcome counts):")
    for e in ("files:opencode.py", "files:field.py", "cmd:pytest", "cmd:git", "port:11500"):
        if e in ent_obs:
            f = Counter(f"{obs[i]['tool']}->{obs[i]['outcome']}" for i in ent_obs[e])
            print(f"     {e:<20} {dict(f.most_common(6))}")

    # ---- discourse: E23's test 1, on the clean stream ------------------------
    split = int(0.6 * len(discourse))
    S = sentences_of(discourse, split)
    X = embed_cached(emb, [x["s"] for x in S], "dsent")
    print(f"\nDISCOURSE: {len(S)} sentences")
    rows, prior, c, _ = word_field(discourse, split, S, X, emb,
                                   label="TEST 1 on the discourse stream only")
    shell_like = {"head", "dev", "null", "dev null", "users", "dmitrii", "chechetkin",
                  "echo", "grep", "bash", "command", "filepath", "projects"}
    rset = {r["term"]: r for r in rows}
    left = [t for t in shell_like if t in rset]
    print(f"\n   shell/path tokens still frequent in discourse: {len(left)} of "
          f"{len(shell_like)} ({', '.join(left)})")

    # ---- the join: discourse entities with observed evidence -----------------
    disc_specific = Counter()
    for m in discourse:
        for kind, vals in extract_entities(m["text"], "").items():
            for v in vals:
                if kind == "files":
                    disc_specific["files:" + v.rsplit("/", 1)[-1].lower()] += 1
                elif kind in ("symbols", "models"):
                    disc_specific[f"{kind}:{v.lower()}"] += 1
    both = [e for e in disc_specific if e in ent_obs]
    print(f"\nJOIN: {len(disc_specific)} specific entities named in discourse; "
          f"{len(both)} ({len(both) / max(len(disc_specific), 1):.0%}) also appear in "
          f"observations")
    for e in sorted(both, key=lambda e: -disc_specific[e])[:14]:
        f = Counter(obs[i]["outcome"] for i in ent_obs[e])
        print(f"     {e:<34} said {disc_specific[e]:>3}x | observed {len(ent_obs[e]):>3}x "
              f"{dict(f.most_common(3))}")
    said_only = sorted((e for e in disc_specific if e not in ent_obs),
                       key=lambda e: -disc_specific[e])[:10]
    print("   named but never observed:", ", ".join(e.split(":", 1)[1] for e in said_only))
    Path(".membrane/e24-results.json").write_text(json.dumps({
        "n_discourse": len(discourse), "n_obs": len(obs), "n_distilled": len(distilled),
        "join": {"named": len(disc_specific), "observed": len(both)}}, indent=1))


if __name__ == "__main__":
    main()
