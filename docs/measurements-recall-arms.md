# Recall arms, full-detail comparison (internal)

Internal numbers: no public LoCoMo claim comes from this page. Prepared for 0.7.60 (#1143); no measurement has been run with it yet.

## The one command

```
S=<scratch dir>                      # outside the repo
python3 -I scripts/measure_recall_arms.py --repo $PWD --binary $PWD/native/target/release/cliffvm \
  --data <locomo10.json> --scratch $S --test-convs 0,1 --arms edges,cosine,fused --max-embed-calls <N> --dry-run
# read the estimate, then run it again without --dry-run
# <locomo10.json> is the LoCoMo data file; it is not in this repository. A real run needs an Ollama with the embed model (the `embed_model` fact, default mxbai-embed-large) at --ollama.
```

Everything lands in `$S`: `report.md` (also printed), `report.json`, `per_question.jsonl` (one row per arm and question, for recomputing), and a copy of the summary in `scripts/recall_runs/` (made by the first run; the next run reads it for "best so far").

## What it enforces

- `--max-embed-calls` has no default. A counting proxy on 127.0.0.1 sits between the seat and Ollama (the seat's `seat_backend` is pointed at it); at the limit it answers 429 and the run stops with a partial report, marked ABORTED.
- `--ollama` (default `http://127.0.0.1:11434`) must be loopback, or `--allow-remote` is given.
- One run at a time: an advisory lock (`--lock-file`); a second run is refused.
- `--dry-run` sends nothing, starts nothing, and prints conversations, turns, questions, an embedding-call estimate and a time estimate.
- Held out: `--test-convs` (required) and `--tune-convs` (default none) must not overlap, or the run is refused. Tune conversations are reported in their own section, marked never to quote.

## What the report holds

Header: data sha256, arm-configuration sha256 (arms, the read lines, k rows, conversations, Ollama address), conversation lists, wall time, embedding calls actually made. Per arm and per role: every category x @1/@3/@8/@20/@all for lenient, strict, ceiling and relative (definitions as in `docs/measurements-2026-11-14.md` section 3 (a2)), turns median, answered nothing. An arm the seat cannot serve prints `not available`, never zeros. Then "best so far" per cell (lenient and strict) with the variant that holds it, and every variant ever recorded (`scripts/recall_records.json`, copied from the 2026-11-14 page, plus earlier runs in `scripts/recall_runs/`). A record counts for a cell only when it was measured on the same conversations; the rest are listed as not comparable.

## Pointing an arm at the final entity

The query of each arm is the table `ARM_READS` near the top of `scripts/measure_recall_arms.py`: `(entity spec with {eid}, property to read, property written first)`. `edges` is today's `related` with `related_n`. `cosine` (`{eid}`, `cosine_related`) and `fused` (`Banner:recall`, `rows`) are still the first guesses and NO entity has those properties: the lanes now exist as getters of the `RecallPart` class in `membrane/apps/barest.c1f/recall.c1f` (`edge_rows`, `cos_rows`, `fused`, `ranked`) on the entity `membrane::Banner:recall`, but they work from the chat message the turn is answering (the "ask"), not from a question text written by the harness, so those two arms print `not available` until their lines are edited to a read that carries the question. The `edges` arm (`related`) works as it is. Rows may be dicts with `semtext`/`key`/`id` or plain strings; `Vector:of:<key>:s:<start>` rows are mapped back to their turn. A read that errors or returns no list marks the arm not available.

## Cost (estimates, not measurements)

For conversations 0 and 1: 788 turns and 230 questions. Embedding calls about 3,100 if the seat embeds one call per sentence and one per question per vector arm (it may batch; the dry-run prints the figure for the chosen conversations). Wall about 12 minutes by the doc's 449 ms per turn and a half second per question and arm, plus whatever the embedder costs. RAM: the harness is small; the seat and Ollama's model are the cost; run nothing else heavy beside it.

## Not measured / limits

Only what the harness was tested on: a three-conversation synthetic fixture, a fake embedder and a stub seat (`tests/test_measure_recall_arms.py`). The real seat path (`CliffvmSeat`) was not run, and the vector and fused reads are unconfirmed guesses (see above: they point at properties that do not exist). Questions stay in the field after being asked, as in the earlier runs. No LongMemEval, no latency numbers, no answer-quality score. Old recorded numbers are lenient and were recomputed from the 2026-11-14 page, not from per-question files (none exist for @1/@3).
