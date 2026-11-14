# Measurements of the cliffvm seat against the records (2026-11-14, #1143)

**Important first.** Measure-only; no behaviour changed. The cliffvm seat was run on a COPY of Throwy's store (2,100 rows, 94 MB), with a fake `claude`, on ephemeral ports. Old numbers come from the written records only (no old engine was run).
1. **Replies are 27-160x smaller** than the records: a Keep write is 472 B idle and 541 B with 5 running turns (records 12,613 B idle, 87,793 B with 3 running, 264,467 B with 10); a `mention` write is 3,271 B (record 62-97 KB). Better. (The records were taken before design B2.)
2. **Recall by edges is worse than the old engine on LoCoMo, and the gap is at depth.** conv 0-1, 230 questions: hit@8 0.548, hit@20 0.613; the same questions on the old doc arm (10-28): 0.648, 0.764. Multi-hop is the weak spot (hit@8 0.29 vs 0.56). Temporal is the strong one at @1 (0.59). No ranking above `related` exists in cliffvm (no reranker, no vote).
3. **Boot is about 2x the recorded 0.37 s and RSS about 2x**, on a 5x bigger store: door up in 0.76-0.78 s cold / 0.86-0.92 s warm, 79-99 MB at the door, 100-107 MB after 10 s, 184 MB idle after the whole run. Warm boot is not faster than cold here. Not comparable 1:1 (record: 470 rows, older pack set).
4. **Tokens per turn on Throwy's real turns are growing, not flat:** median 13.3k (first 55 turns) to 30.6k (last 55), p95 147k to 1.7M; calls per turn 3 to 4. Overall median 23.6k, not the ~18k in the brief (that figure has no source on disk).
5. **Abstention: nothing marks "I do not know".** A question about something never stated (dog, airline, pizza) still returns 15-17 rows; the top row shares 1-3 entities, real hits share 3-10, but no threshold exists.

## 1. Records found (read-only), each labelled

Label: M = MEASURED with a stated method, R = RECALLED (a note, no method), U = UNVERIFIED (named in the brief, no source on disk). "mem/" = `~/.claude/projects/-Users-dmitrii-chechetkin-projects-membrane/memory/`.

| # | record | where | label |
|---|---|---|---|
| R1 | door up 50,000 rows: 6.8 s / 388 MB before lazy load, 0.33 s / 26 MB after; real repro store (470 rows, 18 MB): 4.5 s / 184 MB before, 0.37 s / 40 MB at the door, 72 MB after warm boot | docs/cliffvm.md:175 | M (release build, synthetic Note rows, one machine) |
| R2 | empty store, all 33 packs: healthy in 0.84 s, RSS 13.9 MB | docs/release-parity.md:313 | M |
| R3 | burst of 2 parallel 1500-chunk messages + 1 MB output: 122 MB to about 2 GB, after the fix peak about 0.2 GB | docs/cliffvm.md:132 | M (synthetic; not re-run here) |
| R4 | idle RAM 90-211 MB; history route of 20 turns about 0.1 s | the brief only; nothing in docs/, mem/ or git messages | U |
| R5 | reply sizes (Keep idle 12,613 B; 3 running 87,793; 10 running 264,467; mention 61,992-96,656; others under 1.5 KB) | docs/reply-size.md:5, :11-:19 | M (private seat, copy of Throwy's store, fake claude, `242948c1`) |
| R6 | old Python seat, live, through 2026-10-15: 3,840 turns, 10.8K avg / 1.55K median tokens per turn, 4.9 calls per turn, 39.5% cache read | docs/token-economics.md:16-19 | M (`membrane stats --baseline`) |
| R7 | map-mode Memo about 18.9k input-equivalent per answer (was about 40k, long ones 111k) | mem/map-mode-breakthrough.md:20 | R |
| R8 | "Throwy median about 18k input, 76% cache reads", "recall 22/53" | the brief; mem/MEMORY.md:67 names "recall 22/53" only | U |
| R9 | membrane counts total prompt about 3,700/turn flat, cached_tokens 0 (opencode bench) | mem/benchmark-token-accounting.md:26 | R |
| R10 | LoCoMo, 10 conversations, 1,535 questions, held-out readers: vote @1 single 0.47 / multi 0.39 / temporal 0.57 / open 0.35 (after memspeak); fitted vote 0.48 / 0.40 / 0.62 / 0.28 | mem/recall-findings-2026-11-07.md:13, :15 | R (bench arms not on disk) |
| R11 | LoCoMo doc arm, old seat, all 10 convs, categories 1-4: hit@8 0.651, hit@20 0.769, ask mean 166-1153 ms by category; per question file lets conv 0-1 be recomputed (233 questions: hit@8 0.648, hit@20 0.764) | `~/projects/membrane-bench/locomo/results/doc-2026-10-28/{summary,per_question}.json`, method `locomo/README.md` | M |
| R12 | conv 0-1 hit@8: main 106 to 148 (of 230), seed scorer 169-173, bge rerank 179-180; warm ask 0.7-2.3 s live | mem/state-2026-10-28.md:32, :35, :36, :38, :41 | R |
| R13 | `Stats:eval.slow` flag = an evaluation over the former 10 ms allowance | docs/cliffvm.md:318 | M (definition) |

Rule from mem/state-2026-10-28.md:34 and :38: no public LoCoMo claims. This page is internal.

## 2. Measured now, next to the record

Machine: Mac, load average 3-6 during the runs (another session ran `cargo test` and a second cliffvm; Ollama idle), so the times are upper-ish. One job at a time from my side. Binary: `cargo build --release --features bin --bin cliffvm` at `78990411` (branch memo-hotfix-0753 head, docs-only changes on top).

| what | now | record | ratio | verdict |
|---|---|---|---|---|
| boot, cold, door up (3 fresh copies) | 0.76 / 0.76 / 0.78 s | 0.37 s (R1, 470 rows); 0.84 s empty (R2) | 2.1x R1; 0.9x R2 | worse than R1, same as R2; not comparable (store 4.5x the rows, more packs) |
| boot, warm (restart of same file) | 0.86-0.92 s | none | | not faster than cold |
| RSS at door / after 10 s | cold 79-88 / 101-108 MB; warm 94-99 / 102-107 MB | 40 / 72 MB (R1) | 2.1x / 1.5x | worse; same caveat |
| RSS idle after full run | 184 MB | 90-211 MB (R4) | in range | same (R4 is U) |
| history, 20 turns, 110 KB reply | median 74 ms, p95 165 | about 100 ms (R4, U) | 0.7x | same |
| `entity_query kind:Keep` (45 rows) / `kind:ChatMessage` | 123 ms / 74 ms median | none | | no record; Keep is slower than Chat |
| `entity_read` of the biggest entity (2.4 MB) whole / one prop | 57 ms / 24 ms | none | | no record |
| `related` on 20 real SemTexts, `related_n=5` write | median 27 ms, p95 53 ms; 2 of 20 answer null (text never mentioned) | none | | no record |
| `related` evaluation self time (Stats:eval, per ask) | median 7.6 ms; 12/20 under 10 ms, 6/20 had an evaluation over 10 ms | allowance 10 ms (R13) | | 60% inside the old allowance |
| `related` steps per ask | median 22,093, max 62,342 | step budget 100,000 (mem/ note, eval limits) | max 0.62 of budget | inside |
| `mention` write reply (21 words) | 3,271 B | 61,992-96,656 B (R5) | 0.03-0.05x | better (B2) |
| Keep write reply, idle | 472 B | 12,613 B (R5) | 0.04x | better |
| Keep write reply, 5 running turns (sleeping fake) | 541 B | 87,793 (3 running), 264,467 (10 running) | 0.006x vs 3 | better |
| idle CPU | 0.14 s CPU per minute (0.23%) | none | | no record |
| idle evaluations per minute (Stats:eval, window 1 s) | 395 | "an idle seat evaluates none" (git dc8c632b message) | | not zero; includes the 1 s clock and Mem sensors and the stats window itself, not separated here |

Tokens per turn, from the 110 turns in the copy that carry `usage` (turn entities `...:turn`: `usage`, `cost`, `calls`; input = uncached + cache read + cache write, summed over the turn's calls; `scripts/measure_usage.py`). Only 110 turns exist, not 200:

| | all 110 | first 55 | last 55 | record |
|---|---|---|---|---|
| input median | 23,574 | 13,340 | 30,552 | R8 18k (U); R7 18.9k (map mode, R); R6 1.55K median (old seat, in+out) |
| input p95 | 409,976 | 147,234 | 1,700,205 | R7: long answers 111k |
| cache read share, median turn / pooled | 0.59 / 0.83 | 0.38 / 0.63 | 0.69 / 0.87 | R8 76% (U); R6 39.5% |
| calls per turn, median | 3 | 3 | 4 | R6 4.9 |
| cost USD median / p95 / mean | 0.052 / 0.372 / 0.103 | 0.022 / 0.223 / 0.061 | 0.077 / 0.757 / 0.144 | none |
| output tokens median | 860 | 677 | 1,135 | |

Verdict: trend is growing (median x2.3, p95 x11), against a flat ~3,700 (R9) or a 40k to 18.9k improvement (R7). Input totals are not comparable with R6 (different seat, in+out, a different workload: Throwy's turns are tool-heavy build sessions).

## 3. Recall

**(a) LoCoMo conv 0 and 1 (R11/R12 subset).** Ingest: every dialogue turn (788) became one SemText `Loco:c<conv>:<dia id>` with the text `<speaker> (<session date>): <text> [shares <caption>]`, `mention = true`, by direct entity writes through the MCP door (the way the chat's body is made; no model; 449 ms median per turn, p95 966 ms). Every question of categories 1-4 became a SemText with `mention = true` and `related_n = 20`; hit@k = an evidence turn among the first k distinct texts of `related`. 230 questions (the old arm has 233; 3 have evidence strings the script does not parse).

| category | n | hit@1 | hit@3 | hit@8 | hit@20 | old hit@8 (R11, conv 0-1) | old hit@20 | verdict @8 / @20 |
|---|---|---|---|---|---|---|---|---|
| multi-hop (1) | 42 | 0.12 | 0.17 | 0.29 | 0.43 | 0.56 (n 43) | 0.72 | worse (0.5x / 0.6x) |
| temporal (2) | 63 | 0.59 | 0.78 | 0.84 | 0.87 | 0.84 (n 63) | 0.94 | same / worse |
| open-domain (3) | 11 | 0.18 | 0.18 | 0.18 | 0.36 | 0.46 (n 13) | 0.62 | worse (n tiny) |
| single-hop (4) | 114 | 0.32 | 0.46 | 0.52 | 0.56 | 0.60 (n 114) | 0.70 | worse (0.87x / 0.80x) |
| all | 230 | 0.35 | 0.48 | 0.55 | 0.61 | 0.65 | 0.76 | worse (0.85x / 0.80x) |

@1 and @3 have no like-for-like record: R10 (@1, 10 convs, vote, after memspeak: 0.47 / 0.39 / 0.57 / 0.35 for single / multi / temporal / open) is another subset and another algorithm; now single 0.32, multi 0.12, temporal 0.59, open 0.18 (the category-number-to-name mapping in R10 is assumed to be the LoCoMo paper's). Not comparable, shown for orientation only. Latency: related write+read median 51 ms, p95 83 ms (the old `ask_ms_mean` of 166-1153 ms is the whole ask with an embedder; not comparable, and the question's own mention, about 0.45 s, is not in my 51 ms).
Limits: the questions stay in the field after being asked (they shift word counts a little; question texts are skipped in the results); only conv 0-1; the old weights were fit on LoCoMo (R12), the new engine has none; speaker names and dates ride inside the turn text.

**(b) A small hand-built check on Throwy's own history (one author, 20 facts, NOT a benchmark).** Pool: the 154 messages of the copy plus the 45 Keep titles as SemTexts (the 35 existing assistant response bodies are in the field too). Facts picked from what the operator stated (Agatha, boat model, drive kit, prop, battery, Blurp, geography quiz, /tmp, controller, build, ...); each question is a plain sentence (`scripts/facts_throwy.json`). Rank of the source user message among distinct texts:

| | @1 | @3 | @8 | @20 |
|---|---|---|---|---|
| source message in top k | 7/20 | 11/20 | 12/20 | 15/20 |
| source message or its Keep title | 8/20 | 13/20 | 15/20 | 18/20 |

Misses are mostly paraphrases with no shared word (Agatha = "liveaboard", "what model of boat" vs "Catalina-Morgan 41"; "which computer" vs "macbook"), and a reply of the assistant outranking the source. No record exists for this check; verdict: not comparable. Abstention (dog, Lisbon airline, pizza topping; none stated anywhere): each returns 15-17 rows, the top ones share 1-3 entities with the question ("user's", "name", "what") and read as plausible, so nothing in the output says "not found".

## 4. Not measured, and what it needs

- The brief's R4 and R8 figures: no source; someone must say where they come from.
- Cold OS cache boot (the store is in the page cache after the first copy); a quiet machine (load was 3-6).
- Tokens of the most recent real turns (the copy has only 110 turns with usage and ends before the newest).
- Recall @1/3 like-for-like: needs the old per-question rank lists for @1/@3, or the same conversations through the 11-07 vote.
- All 10 conversations (this run: 2; about 15 min), LongMemEval, a reranker comparison (cliffvm has none).
- The 2 GB burst (R3) and the 50,000-row synthetic boot (R1): re-run with `native/tests` or `benchmark/` harnesses.
- Idle evaluations without the stats window's own cost.

## 5. Re-run (the operator will bench again after the paraphrase ladder)

```
cd <worktree> && (cd native && cargo build --release --features bin --bin cliffvm)
S=<scratch>; sqlite3 <live store> ".backup $S/seat.sqlite"          # a COPY; never the live store
python3 -I scripts/measure_speed.py  $PWD $PWD/native/target/release/cliffvm $S/seat.sqlite $S/speed   > $S/speed.json    # ~7 min
python3 -I scripts/measure_usage.py  $S/seat.sqlite                                                                        # seconds
python3 -I scripts/measure_locomo.py $PWD $PWD/native/target/release/cliffvm <locomo10.json> $S/loco 0,1 > $S/loco.json   # ~15 min
python3 -I scripts/measure_facts.py  $PWD $PWD/native/target/release/cliffvm $S/seat.sqlite scripts/facts_throwy.json $S/facts > $S/facts.json   # ~4 min
```

Each script starts its own seat (fake claude, `--port 0`, never 4096) and kills it. `scripts/measure_lib.py` holds the helpers. Run one at a time; check `vm_stat` and the load first.
