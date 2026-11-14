# End-to-end pilot: the model in the loop, an empty seat (2026-11-14, #1143)

**Important first.** A small pilot (one LoCoMo conversation, 30 questions, plus ONE LongMemEval instance) on an EMPTY barest seat with the real model. It gauges the waters; it is not a benchmark.
1. **LoCoMo conv 0: the model's answers are mostly right even though token F1 says 0.28.** Token F1 overall 0.28 (answerable questions only: 0.10, because the model answers in full sentences with context); my reading (labelled, one author): 20 of 24 answerable questions right, 3 partial, 1 wrong; the 6 adversarial questions (category 5) all abstained or corrected the premise (6/6 by the phrase rule and by my reading).
2. **LongMemEval, one instance (knowledge-update): FAILED.** The model wrote both values of the fact into its Notes ("about an hour" in session 3, "about two hours" in session 14, the newer one), but at question time it answered `Not in memory` after ONE call and NO tool use. Gold: "about two hours". One instance proves nothing, but the failure is read-side, not write-side.
3. **Cost is small.** LoCoMo: $2.32 total for 19 ingest turns ($1.25) and 30 questions ($1.07, about $0.036 each); LongMemEval: $2.98 for 53 ingest turns, $0.004 for the question. All turns finished, no errors, no login or rate-limit problem. Model time: LoCoMo 6.5 min, LME 14.2 min.
4. **The memory is the banner on conv 0, the Notes on LME.** After 19 sessions the model kept 19 `Session:N` entities (24 KB) AND a 21.7 KB banner that repeats them all (13.7k input tokens on every question that does not need a tool). After 53 sessions of LME the banner was 2 KB (a short index) and 53 `Note:sessionN` entities (65 KB total). The model chose both layouts itself; the same doctrine produced two very different designs.
5. **Right/wrong samples** (full list in the appendix): right, "When did Caroline go to the LGBTQ support group?" gold `7 May 2023`, answer: "Caroline went to the LGBTQ support group on 7 May 2023, the day before Session 1" (F1 0.18, human: right). Wrong, "What are Caroline's plans for the summer?" gold `researching adoption agencies`, answer: "Memory has no explicit summer plans for Caroline" (the fact is in memory, the model did not connect it). Abstention, "What does Melanie's necklace symbolize?" (Melanie has none): "Not in memory as Melanie's necklace. The necklace in the sessions is Caroline's ... love, faith and strength."

## Method (fixed before the run)

System under test: the barest app as shipped (default doctrine `docs/doctrine-barest-draft.md`, default banner, Help entities, the tools), the real `claude` CLI through the operator's login (`--fact chat_auth=login`; `ANTHROPIC_API_KEY` removed from the seat's environment), an EMPTY store, no Throwy packs, no edit of doctrine or banner. Binary: release `cliffvm` built from this worktree. Private seat, `--port 0`, one real turn at a time. Only guard: the per-turn timeout (`chat_turn_timeout_s=1800`, the pack's own fact; its default is 21600). A login/limit-like error would have stopped the run; none occurred.

Script: `scripts/bench_system.py` (usage in its docstring; templates at its top). Raw logs (live-updated JSONL, one line per turn): `<scratch>/sysbench/{locomo,lme}/{ingest,questions}.jsonl`, scratch = `/private/tmp/claude-504/-Users-dmitrii-chechetkin-projects-membrane/dfe53dac-fdac-441e-bf76-5b5fc6fd3d70/scratchpad/`; also there: `post_ingest.sqlite` and `empty_baseline.sqlite` per set, and `report-all.md` (the script's own report).

Templates (exact):
- Ingest, LoCoMo, one chat message per session: `Below is session {n} of a conversation between {A} and {B} on {date}. Remember what will matter for answering questions about it later; you will not see this text again.` + blank line + lines `speaker: text` (an image caption goes after the text as `[image: caption]`).
- Ingest, LongMemEval (sessions in date order): same, with `a conversation between a user and an assistant on {date}. ... (the user's own sentences above all); ...`, lines `user: ...` / `assistant: ...`.
- Question: `{question}  (Answer from your memory of the conversation you were given earlier. If you have no record of it, say 'not in memory'.)`
- Each question runs in a FRESH seat started on its own copy of `post_ingest.sqlite` (`sqlite3 .backup`, taken after the last ingest turn, no turn running); the copy is thrown away.

Data: LoCoMo `locomo10.json` conversation 0 (Caroline and Melanie, 19 sessions, 73.5k characters). Questions: the FIRST 6 of each category 1-5 in file order (30; rule in `locomo_questions`; 1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop, 5 adversarial). LongMemEval: only the S haystack exists on disk (`longmemeval_s_cleaned.json`; no oracle file). Rule (`pick_lme`): among `knowledge-update` instances (the fact changes across sessions: needs memory across sessions and the newest value must win) the one with the smallest haystack in characters: `cc5ded98`, 53 sessions, 481k characters (about 120k tokens), question "How much time do I dedicate to coding exercises each day?", gold "about two hours".

Scoring: token F1 against gold (lowercase, punctuation stripped, articles and `and` removed, LoCoMo style). Category 5: score 1 if the answer matches an abstention phrase (regex `ABSTAIN_RE` in the script: "not in memory", "no record", "unknown", "don't have ... information", "no mention", "can't find", "not discussed" ...), else 0. The regex also fires on a few answerable questions where the model hedged (it does not change their near-zero F1). No judge model was called; the "human" column is my own reading, one author, labelled as such. LME: normalised comparison plus my reading.

Write-side check (no model): rows in the post-ingest store that are not in the empty baseline and are not the seat's own artefacts (the raw chat messages, bodies, turns, responses, tools, commands, the word index `Word`/`Identifier`, Help, Pack, Stats), plus the banner's `text`. Gold counts as present if its key tokens (gold words minus stop words) are all in that text.

## Results

LoCoMo conv 0 (30 questions):

| category | n | mean token F1 | abstained (rule) | my reading |
|---|---|---|---|---|
| 1 multi-hop | 6 | 0.067 | 0 | 6 right |
| 2 temporal | 6 | 0.151 | 1 | 4 right, 1 near, 1 partial |
| 3 open-domain | 6 | 0.085 | 1 | 5 right, 1 partial |
| 4 single-hop | 6 | 0.097 | 0 | 5 right, 1 wrong |
| 5 adversarial | 6 | 1.000 (abstention) | 6 | 6 right |
| all | 30 | 0.280 | | 20 + (6) right, 1 near, 2 partial, 1 wrong |

F1 and a reasonable reading disagree on almost every answerable question: the model answers in full sentences, with sessions and dates around the fact, so precision is tiny. Disagreements the other way: none found (no wrong answer scored high).

LongMemEval `cc5ded98`: answer `Not in memory. My notes from our earlier conversations don't say how much time you spend on coding exercises each day.` Normalised match with "about two hours": no (F1 0.00); my reading: wrong. Tools used: none, calls 1, 2.2 s model time, $0.004.

Write side (model-written memory):

| | LoCoMo conv 0 | LME instance |
|---|---|---|
| model-written rows | 20: 19 `Session:N` + the banner | 54: 53 `Note:sessionN` + the banner |
| bytes | 45.8 KB (entities 24.1 KB; banner text 21.7 KB) | 65.1 KB (banner text 2.1 KB) |
| sample titles | `Session:1` ... `Session:19`, `membrane::Banner:owner:main` | `Note:session1` ... `Note:session9`, banner |
| gold key tokens all present (answerable, n=24 / 1) | 14/24 (58%) | 1/1 (read by hand: both "an hour" and "about two hours" are in Notes 3 and 14) |
| at least half the key tokens | 22/24 (92%) | 1/1 |
| no `Keep` entities, no packs | the model used neither; it wrote entities of its own classes | same |

The all-tokens miss on LoCoMo is mostly long paraphrased golds of open-domain/single-hop questions (e.g. "by carving out some me-time each day for activities like running, reading, or playing the violin": 12 of 13 tokens present), not missing facts.

Cost and time (from the CLI's result line; "model time" = the turn's own `duration`; the poll adds up to 3 s per turn):

| | turns | calls | input tokens (cache-read share) | output tokens | reported cost | model time |
|---|---|---|---|---|---|---|
| LoCoMo ingest | 19 | 144 | 1.07M (80%) | 29.9k | $1.25 | 4.3 min |
| LoCoMo questions | 30 | 67 | 373k (37%) | 11.7k | $1.07 | 2.2 min |
| LME ingest | 53 | 385 | 2.29M (80%) | 94k | $2.98 | 14.2 min |
| LME question | 1 | 1 | 3.0k (77%) | 0.1k | $0.004 | 2 s |

Per turn: LoCoMo ingest about 3 s to 25 s, $0.03-0.06; a question $0.007 (no tool, banner only) to $0.045 (tool calls), median input 13.7k tokens. 12 of 30 LoCoMo questions ran `run_cliff`, 25 tool calls were `entity_query`; 5 questions used no tool. Ingest tools (LoCoMo): `entity_read` 54, `entity_query` 36, `entity_write` 33.

ESTIMATES (labelled; extrapolated linearly from the numbers above, one conversation each, not measured):
- the 1.5k LoCoMo questions: about 1,535 x $0.036 = about $55 and about 2-3 hours, IF the banner stays at conv 0's size (13.7k tokens per question); a longer conversation means a longer banner and a higher price per question.
- all 10 conversations ingested: $1.25 per 19 sessions / 73.5k characters scales to about $12-25 and 45-90 minutes (the other conversations are about 1-2x as long).
- LongMemEval-S, 500 instances (each has its OWN haystack, so each is ingested separately): 500 x $3 = about $1,500 and about 118 hours of model time. Not worth running whole; a fixed sample would do.

## Limits: what this pilot cannot show

One conversation, 30 questions (6 per category: a category's mean moves 0.17 with one answer), one LME instance, one author for the "my reading" column and the abstention rule, one model (the CLI's default at run time), prompt-dependent (the ingest and question templates above; the question template invites "not in memory", which a plain question would not). The model saw the dates and the question's wording; no judge model; the gold of LoCoMo category 5 is "abstain" (the file lists the distractor answer). No repeat runs: the model is stochastic, and the answer to the LME question may differ on a second run (it did not read the Notes this time; why, a second run could tell). The pilot says nothing about scale: conv 0's banner holds the whole conversation, which cannot hold for 10 conversations or a 120k-token haystack, and the LME banner shows what the model does when it cannot: an index, Notes, and an unread index. No comparison with the old engine, with a baseline without memory (the questions are not answerable without the ingest, but the model's prior knowledge was not tested), or with Throwy's store.

## Appendix: every question (answers cut at 170 characters; the full text is in `questions.jsonl`)

| # | cat | question | gold | answer | F1 / abstain | my reading | tools | calls | cost |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 1 | What did Caroline research? | Adoption agencies | Caroline researched **adoption agencies**. In Session 2 (25 May 2023) she was looking into them and chose one that helps LGBTQ+ people. Her research advice in Session 17 ... | 0.07 | right | entity_query | 2 | $0.044 |
| 1 | 1 | What is Caroline's identity? | Transgender woman | According to my memory, Caroline is a transgender woman. In Session 19 (22 Oct 2023) she said that finding self-acceptance was a long process and that transitioning and g... | 0.04 | right | - | 1 | $0.006 |
| 2 | 1 | What is Caroline's relationship status? | Single | Caroline is single. In Session 2 (May 2023) she said she plans to be a single parent and was researching adoption agencies. In Session 3 she mentioned a tough breakup bef... | 0.05 | right | entity_query | 2 | $0.042 |
| 3 | 1 | Where did Caroline move from 4 years ago? | Sweden | Caroline moved from Sweden, her home country. I got this from Session 3 (9 June 2023). It says she has known her friends for 4 years, since she moved from her home countr... | 0.04 | right | run_cliff,entity_query | 3 | $0.041 |
| 4 | 1 | What career path has Caroline decided to persue? | counseling or mental health for Transgender people | Caroline plans to pursue a career in counseling and mental health. She wants to work with transgender people on self-acceptance and mental health. She first said this in ... | 0.18 | right | entity_query | 2 | $0.040 |
| 5 | 1 | What activities does Melanie partake in? | pottery, camping, painting, swimming | Melanie's activities, from the sessions in memory:  - **Pottery:** She signed up for a class in July 2023 and took her kids to a pottery workshop. She makes bowls, plates... | 0.03 | right | entity_query | 2 | $0.045 |
| 6 | 2 | When did Caroline go to the LGBTQ support group? | 7 May 2023 | Caroline went to the LGBTQ support group on **7 May 2023**, the day before Session 1 (8 May 2023). She told Melanie she felt accepted there and that the transgender stori... | 0.18 | right | entity_query | 2 | $0.039 |
| 7 | 2 | When did Melanie paint a sunrise? | 2022 | Melanie painted a lake sunrise (sunrise/sunset) in 2022, "last year" relative to Session 1 (8 May 2023). She shared a photo of it in that session. Memory gives no more ex... | 0.03 | right | run_cliff,entity_query | 3 | $0.041 |
| 8 | 2 | When did Melanie run a charity race? | The sunday before 25 May 2023 | Melanie ran the charity race for mental health on about 20 May 2023 (Saturday). She mentioned it in Session 2, dated 25 May 2023. The date is approximate because she desc... | 0.14 | near (about 20 May; gold: Sunday 21 May) | entity_query | 2 | $0.039 |
| 9 | 2 | When is Melanie planning on going camping? | June 2023 | Not in memory. My records don't show any future camping plans for Melanie. They only cover camping trips she has already taken:  - June 2023, in the mountains. - Early Ju... | 0.08 | partial (says not in memory, then names June 2023) | entity_query | 2 | $0.040 |
| 10 | 2 | When did Caroline give a speech at a school? | The week before 9 June 2023 | Caroline gave the school talk the week before 9 June 2023, about 2 June 2023. She said so in Session 3 (9 June 2023). The talk was about her transgender journey. She enco... | 0.24 | right | entity_query | 2 | $0.039 |
| 11 | 2 | When did Caroline meet up with her friends, family, and mentors? | The week before 9 June 2023 | Caroline met up with her friends, family, and mentors in the week before 9 June 2023, around 2 June 2023. In Session 3 (9 June 2023) she shared a photo of friends and fam... | 0.24 | right | run_cliff,entity_query | 3 | $0.041 |
| 12 | 3 | What fields would Caroline be likely to pursue in her educaton? | Psychology, counseling certification | Caroline would likely pursue **counseling and mental health**. She would probably focus on work with transgender people and the LGBTQ+ community.  This comes from several... | 0.01 | partial (counseling; no psychology) | entity_query | 2 | $0.042 |
| 13 | 3 | Would Caroline still want to pursue counseling as a career if she hadn't received support growing up? | Likely no | Probably not, or at least it would be less likely. This is an inference from my memory, not something Caroline said directly.  In Session 19 (22 Oct 2023), Caroline said ... | 0.02 | right | - | 1 | $0.007 |
| 14 | 3 | Would Caroline likely have Dr. Seuss books on her bookshelf? | Yes, since she collects classic children's books | Probably yes, though it's an inference. Nothing in memory says she owns Dr. Seuss books.  Session 6 (6 July 2023) is the relevant one. Caroline was building a library for... | 0.10 | right | entity_query | 2 | $0.040 |
| 15 | 3 | Would Caroline pursue writing as a career option? | LIkely no; though she likes reading, she wants to be a counselor | No. Writing isn't in my memory as a career option for Caroline. The careers she talks about are counseling and mental health work, especially with trans people. She also ... | 0.20 | right | run_cliff,entity_query | 3 | $0.041 |
| 16 | 3 | Would Melanie be considered a member of the LGBTQ community? | Likely no, she does not refer to herself as part of it | Probably not, based on what I have. My memory shows Melanie as a supportive friend or ally to Caroline, who is the one who transitioned. She calls Caroline inspiring and ... | 0.11 | right | - | 1 | $0.007 |
| 17 | 3 | Would Melanie be more interested in going to a national park or a theme park? | National park; she likes the outdoors | Melanie would more likely prefer a national park. Her sessions show a steady pattern of loving nature and outdoor trips, and I found no mention of theme parks.  - **Campi... | 0.07 | right | run_cliff,entity_query | 3 | $0.042 |
| 18 | 4 | What did the charity race raise awareness for? | mental health | The charity race raised awareness for mental health. Melanie ran it around 20 May 2023, according to Session 2. | 0.20 | right | entity_query | 2 | $0.040 |
| 19 | 4 | What did Melanie realize after the charity race? | self-care is important | My notes don't record an explicit realization. Session 2 (2023-05-25) says Melanie ran a charity race for mental health around 2023-05-20. It then lists her self-care hab... | 0.09 | right (as inference) | entity_query | 2 | $0.041 |
| 20 | 4 | How does Melanie prioritize self-care? | by carving out some me-time each day for activities like running, reading, or playing the violin | Melanie makes self-care part of her routine, mostly through creative and calming activities.  - **Daily me-time (Session 2):** After the mental-health charity race she ra... | 0.10 | right | run_cliff,entity_query | 3 | $0.044 |
| 21 | 4 | What are Caroline's plans for the summer? | researching adoption agencies | Memory has no explicit "summer plans" for Caroline. These are the closest items:  - **Trip with Melanie (Session 12, 17 Aug 2023):** Melanie suggested a family outing thi... | 0.00 | wrong (misses the adoption research) | run_cliff,entity_query | 3 | $0.044 |
| 22 | 4 | What type of individuals does the adoption agency Caroline is considering support? | LGBTQ+ individuals | The agency Caroline is considering supports LGBTQ+ people. In Session 2 (25 May 2023), she said she was researching adoption agencies and chose one that helps LGBTQ+ folk... | 0.07 | right | run_cliff,entity_query | 3 | $0.042 |
| 23 | 4 | Why did Caroline choose the adoption agency? | because of their inclusivity and support for LGBTQ+ individuals | Caroline chose the adoption agency because it helps LGBTQ+ people. In Session 2 (25 May 2023) she said she had been researching adoption agencies and picked one that supp... | 0.12 | right | run_cliff,entity_query | 3 | $0.043 |
| 24 | 5 | What did Caroline realize after her charity race? | (abstain; the file lists the distractor answer: self-care is important) | Not in memory. The sessions don't record Caroline running a charity race. Session 2 (25 May 2023) says Melanie ran a charity race for mental health around 20 May 2023. Th... | 1.00 | right (abstains, names the real owner) | run_cliff,entity_query | 3 | $0.042 |
| 25 | 5 | What are Melanie's plans for the summer with respect to adoption? | (abstain; the file lists the distractor answer: researching adoption agencies) | Not in memory. I have no record of Melanie making any summer plans about adoption.  The nearest mentions are these: - **Session 17 (13 Oct 2023):** Melanie said a friend ... | 1.00 | right (abstains, names the real owner) | entity_query | 2 | $0.041 |
| 26 | 5 | What type of individuals does the adoption agency Melanie is considering support? | (abstain; the file lists the distractor answer: LGBTQ+ individuals) | Not in memory. My notes don't say what type of individuals the adoption agency supports. They also have Caroline, not Melanie, as the one pursuing adoption. Caroline pass... | 1.00 | right (abstains, names the real owner) | - | 1 | $0.007 |
| 27 | 5 | Why did Melanie choose the adoption agency? | (abstain; the file lists the distractor answer: because of their inclusivity and support) | Not in memory for Melanie. Melanie didn't choose an adoption agency in anything I have on record.  The agency was Caroline's. In Session 2 (25 May 2023) she said she had ... | 1.00 | right (abstains, names the real owner) | run_cliff,entity_query | 3 | $0.045 |
| 28 | 5 | What is Melanie excited about in her adoption process? | (abstain; the file lists the distractor answer: creating a family for kids who need one) | Melanie isn't the one adopting, according to my memory. Caroline is.  - **Caroline** passed the adoption agency interviews around 20 Oct 2023. She's excited and thankful,... | 1.00 | right (abstains, names the real owner) | - | 1 | $0.007 |
| 29 | 5 | What does Melanie's necklace symbolize? | (abstain; the file lists the distractor answer: love, faith, and strength) | Not in memory as Melanie's necklace. The necklace in the sessions is Caroline's, not Melanie's. In Session 4 (27 June 2023), Caroline showed a necklace with a cross and a... | 1.00 | right (abstains, names the real owner) | run_cliff,entity_query | 3 | $0.042 |