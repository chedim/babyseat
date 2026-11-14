# Why input tokens per turn grow on Throwy's real turns

Trace only, no behaviour change (#1143). Data: a fresh copy of Throwy's store, 111 turns that carry `usage`
(11-13 12:23 to 11-14 01:10, local time of the store). Re-run: `python3 -I scripts/token_growth.py <copy of seat.sqlite> --repo .`
(read-only, stdlib only). Labels: **MEASURED** = read from the store or an exact fit; **INFERRED** = modelled, the
constants are at the top of the script.

## Important first

1. **The per-turn base is nearly flat; the growth is the tool results.** The base of a turn (tools + doctrine +
   banner + message) is 3.15k tokens at the start and 4.63k at the last measured point (+47% in 12 h), and it is
   only 9% of the growth of the mean input. Tool results are 91% of it (mean input 33.7k to 183k). MEASURED anchors,
   INFERRED split (section 3).
2. **One thing makes the tool results big: a write reply echoed the banner.** `entity_write` replies are 74% of all tool-result bytes
   (2.79 MB of 3.74 MB); 97 replies are over 10 KB (up to 48 KB, 5 to 6 times the banner length). In those replies the
   copies of the banner and prompt getters (`Banner.text`, `banner_text`, `banner_slot`, `sys`, `argv`) are 66% of all result
   bytes. Replayed B2 shape on the old replies: -34% input tokens, **-48% input cost ($4.46 of $11.34)** (INFERRED).
3. **The median doubles mostly because the workload changed, not because the same turn got dearer.** No-tool turns are
   20 of the first 55 turns and 4 of the last 56; their input is flat (3,940 to 4,118, MEASURED). The cheap
   turns left the median; the long build sessions (10+ tools: 6 to 15 turns, median 124k to 341k) came in. The three p95 turns
   (1.70M to 2.18M, 00:12 to 00:26) are 48 to 61 calls of the nltk/WordNet/watchdog build, each re-sending 100 to 125k of context.
4. **The cache-read share rise (0.378 to 0.697 median turn) is more calls, not better caching.** Only 1,337 tokens
   (the 4 tool definitions) are ever read across turns; doctrine + banner + message are written fresh every turn at 40x the read
   price. Share = 1 - (L-P)/T, so it rises with calls per turn (MEASURED identity, median error 2 tokens).
5. **The sample cannot say whether B2 worked in practice:** only 2 turns (01:08, 01:10) ran after it; their results are 0.75 KB and 5.9 KB. Newest turns are
   missing, and the workload is one: tool-heavy build sessions by one operator.

## 1. What is in a turn's input (per call)

The CLI runs `-p --no-session-persistence --system-prompt <doctrine + banner> -- <message>`: no history, each call re-sends
the context of the turn. The turn's `usage` is the sum over its API calls (verified exactly on the turns that kept raw events);
`usage.iterations[-1]` is the last call. The prompt props (`sys`, `argv`, `banner_text`) are erased when a turn ends, so
the per-turn banner is **not** recoverable; the base is measured from turns whose first call is fully known (no tool: T; one tool: T-L; 41 turns).

| part | tokens | how known |
|---|---|---|
| prefix read across turns: 4 tool definitions (2,654 B JSON) + CLI text | 1,337 constant (952 with 3 tools before `run_cliff`) | MEASURED, 40 of 41 anchors; the 41st read 0 after >1h idle |
| doctrine, 236 chars | about 60 | INFERRED from chars |
| rest of the written base (CLI text, message) | about 600 | INFERRED, intercept of the fit |
| banner | 0.435 tokens per char (2.3 chars/token) | INFERRED fit on 29 turns, B = 1926 + 0.435 x banner chars |

Banner length after each write (the write reply carries it, 86 snapshots, MEASURED): 3.4k chars (14 Keeps) at 12:35, 5.3k (28) at 14:22,
**1.9k (7 Keeps) at 15:29** after the lazy-load restart (hydrated Keeps only), 5.8k (31) at 23:47, 6.1k (33) at 00:07. Keeps are 5.1 to 5.4k chars of it, as the banner's own TOKEN CHECK says.
At the last snapshot the banner is about 2.4k tokens, 56% of the base. Base B (MEASURED anchors): 3,153 (12:30), 4,280 (14:27), 2,925 (15:31), 4,494 (23:47), 4,632 (00:16).

## 2. Calls, base, tool results

| | first 55 | last 56 |
|---|---|---|
| input median / mean / p95 | 13.3k / 33.7k / 147k | 31.6k / 183k / 1.70M |
| tool calls median | 3 | 4 |
| API calls per turn, mean (INFERRED: 1 + 0.51 x tools) | 2.6 | 5.5 |
| base B median | 3.8k | 4.25k |
| last-call context L median (MEASURED) | 5.1k | 18.1k |
| tool-result bytes median | 1.9 KB | 31 KB |
| cost median / mean | $0.022 / $0.061 | $0.076 / $0.142 |

By kind of turn (median input, MEASURED): no tool 3.9k to 4.1k; 1-2 tools 21.9k to 15.2k; 3-9 tools 29k to 44k; 10+ tools 124k to 341k.
Growth of the **mean**: more calls +10.7k (7%), bigger base +2.1k (1%), tool part +136k (91%) (INFERRED, n and B modelled).

Tool results, all 111 turns (MEASURED): `entity_write` 226 calls 2.79 MB (74%), `entity_read` 193 calls 459 KB (12%), `run_cliff` 166 calls 303 KB (8%), `entity_query` 83 calls 192 KB (5%).

Ten largest turns (73% of all input tokens, 43% of cost). Calls 19 to 61, last context 38k to 124k; in each the big writes
are `Keep:*.title/kind/done`, `Plan:now.doing/next`, `Watch:main.snap`:

| time | input | calls | last ctx | echo bytes | saved without echo (INFERRED) | what |
|---|---|---|---|---|---|---|
| 00:12 | 2.18M | 50 | 107k | 134 KB | 59% | build the SemText watchdog in c1f |
| 00:21 | 1.80M | 61 | 117k | 91 KB | 35% | nltk plan |
| 00:26 | 1.70M | 48 | 124k | 64 KB | 26% | move the /tmp logic to cliff |
| 19:33 | 864k | 51 | 107k | 0 | 0% | test the landed features (SemText `mention` writes, 35-38 KB each: a fan-out, not the banner echo) |
| 14:16 | 587k | 32 | 80k | 64 KB | 20% | build "bounce" |
| 23:48 | 410k | 25 | 39k | 42 KB | 11% | test the new build |
| 13:56 | 375k | 26 | 71k | 115 KB | 61% | build the watchdog |
| 14:00 | 341k | 19 | 38k | 31 KB | 4% | continue |
| 13:47 | 305k | 21 | 50k | 33 KB | 42% | retest |
| 12:37 | 295k | 22 | 41k | 45 KB | 35% | docs lookup |

One raw example (11-13 21:50, no `usage` stored but raw events kept): 17 API calls, 41 tools, contexts 5.6k, 22.9k, 32k, 54k, 71k ... 110k, 127k, sum 1.23M. Call 2 jumps by 17k tokens: one write reply.

## 3. Time: cause or coincidence

| when (local) | event | effect on size |
|---|---|---|
| 12:18 (commit) | `run_cliff` added (4th tool) | prefix 952 (10:47) to 1,337 (12:30), +385: MEASURED, cause |
| 12:42 | write reply caps stdout/stderr at 8 KB | none on the echo: replies stay 22 to 48 KB after: MEASURED |
| 12:35 to 14:27, 23:47 to 00:12 | Keeps 12 to 28, then 31 to 33 | banner +1.9k chars, base +1.1k tokens (3.15k to 4.28k); 2.9k to 4.6k from 15:31 to 00:16: MEASURED, cause of the base growth |
| about 15:20 | lazy load restart: only hydrated Keeps in the banner | base 4.3k to 2.9k, write reply 33 KB to 10 KB: MEASURED step; cause INFERRED (the banner's own Keep note says "7 of 35") |
| 00:01 | doctrine nudge about the banner | about +35 tokens: negligible |
| 23:28 to 00:12 | Error class, limits | not separable in this data (base follows banner length): coincidence |
| 00:12 to 00:26 | semwatch / WordNet build | the three p95 turns; their size is the work (48 to 61 calls) times the echo, not a bigger base |
| 00:22 commit, about 00:30 restart | B2 reply shape | 2 turns after it only: cannot be judged |

The banner's TOKEN CHECK line (Keeps chars) did not stop the growth: 5,133 chars at 23:47, 5,375 at 00:07 (MEASURED).

## 4. Cache and cost

Prices fitted from the turns' own `cost` (max error 0.06%): cache read $0.10/M, cache write (1h) $4.00/M, output $10/M. Writes are 81%
of cost in the first half and 69% in the last, reads 3% and 11%, output 15% and 20%. Cache-read share of tokens 0.63 to 0.87 (sum), 0.378 to 0.697 (median turn).
Where the stable prefix ends (MEASURED): after the tool definitions. The banner changes every turn (the `NOW` clock line, the TOPIC CHECK line, doing/next, Keeps), and doctrine + banner go in one
`--system-prompt` string, so none of it is read from an earlier turn: a no-tool turn pays 2,768 written tokens = $0.0111 of its median $0.0146. Whether the CLI would cache a stable head of the string
is **not known** from this data (it needs a real `claude` turn, which this trace did not run).

Cost by component (INFERRED, total $11.34): tool results and the model's own calls re-sent 70% (of which the banner/prompt copies in write replies about 48 points), output 18%, system + banner + message 11%, tool definitions 1%.
On cheap turns the banner is the cost; on long turns the tool results are.

## Decisions (none chosen; savings replayed on these 111 turns, INFERRED, model within 9% of the measured cost)

1. **Keep the B2 reply shape and apply it to every fan-out reply** (the 19:33 turn shows `mention` reactions are a second kind of echo that the replay does not remove). Replayed: -34% input, -48% cost ($4.46). Operator decision: confirm the shape also covers `Note:body:*.mention` and `Watch:main.snap` reactions.
2. **Cap the door's result for `entity_read`, `entity_query` and `run_cliff` too, at the existing 8 KB per-stream number.** Alone: -40% input, -45% cost; with decision 1: -47% input, -57% cost ($5.29). Cost: a long Help page (`Help:c1f` is 17 KB) would be cut, so a way to read the rest is needed (not designed here).
3. **A banner budget with a cost line the model sees.** Holding the base at its smallest measured value (2,907 tokens) saves only 4% of input, 5% of cost ($0.49); it matters for cheap turns ($0.0111 of $0.0146). The TOKEN CHECK shows chars and was ignored; decide whether it should show tokens or dollars, and who sets the budget number (no number proposed).
4. **Cacheable prefix.** Test with ONE real turn whether a stable head of the system string (Keeps before the clock and TOPIC CHECK lines) is read from cache; until then no restructuring. Needs the operator's go (a real `claude` turn).
5. **Record per-call usage on the turn.** The store keeps only the sum and the last call, and erases the prompt props at the turn's end, so the base and the per-call profile had to be inferred. Keeping a short list (context size per call) is a data decision for the operator.

## What the sample cannot show

Newest turns (after B2) are 2; one operator, one workload (tool-heavy build sessions, long 50-call turns); the API calls per turn
and the base of tool turns are inferred from 16 early turns with raw events and from interpolation between 41 anchors; bytes per token (2.32) is calibrated on JSON write replies;
the 11-13 21:50 turn and the 27 earliest turns lack `usage` and are outside the 111; `calls` in the store is tools + 1, not API calls.
