# Membrane vs. the field it lives in

*2026-10-13, the operator's ask: compare membrane to the neighbors — the
`vector-symbolic-architecture` topic first, then the mainstream agent-memory
world — and name what we'd borrow.* The mirror was asked honestly; this is
the whole truth it gave back.

## The verdict up front

Membrane is this space's only **small, complete organism**: one VSA field
running one person's agent, with a face (chat, 3D field, voice), a home
sensor feed, seats with doctrine, and actuated entities. Everything else on
GitHub is either **infrastructure** (big, adopted, faceless) or **a
library** (math without a person attached). Nothing contradicts the thesis
— VSA memory is unfashionable in 2026, not falsified: the mainstream
discourse has literally zero coverage of hypervector memory.

---

## 1. The VSA topic (`github.com/topics/vector-symbolic-architecture`, 27 repos)

Ranked by popularity + similarity to membrane.

| # | Project | ★ | Stack | Similarity |
|---|---------|---|-------|------------|
| 1 | roandejager/Hillock | 142 | Python | **Highest shape match.** Local HDC/VSA + graph-triples + Hebbian memory engine behind an OpenAI-compatible `/v1` endpoint fronting Ollama — mirrors membrane's "hypervector Ollama proxy" identity. Refuses unanswerable queries before the LLM. LLM-free ingestion. No agents, no UI, no seat: membrane minus the field viewer, minds, and home. AGPL + commercial tiers. |
| 2 | staticroostermedia-arch/engram | 18 | Rust | **Deepest conceptual match.** VSA phase/momentum vectors (8192-d, HolographicBlocks), session-start/recall/remember/session-end loops with consolidation ("NREM"), MCP-native (105 tools, lean default 8), scars, a coherence-reliability score, ego self-model. Rust, beta (0.7.0), patent pending. |
| 3 | writerslogic/holographic-memory | 21 | Rust/Node | Holographic memory (BSC) as an embeddable library — the memory-primitive half only. |
| 4 | BooBSD/Tsetlin.jl | 87 | Julia | Same broad family (hyper-dimensional automata), pure ML library. |
| 5 | cair/GraphTsetlinMachine | 54 | Python | Logical/graph reasoning on HD automata; research lib. |
| 6 | MahmudulAlam/Holographic-Reduced-Representations | 39 | Python | HRR primitives across TF/PyTorch/Flax/JAX — a toolbox. |
| 7 | hviana/sema | 8 | TypeScript | VSA over content-addressable memory; instance-based reasoning. |
| 8 | ssmurfgg04-gif/context-m | 2 | Python | Deterministic agent memory, Mem0-compatible. |
| 9 | yuya-isaka/HPU | 36 | C | HDC accelerator — hardware, orthogonal. |
| 10 | Adam-Vandervorst/PyBHV | 35 | Python | Boolean hypervector operator primitives. |
| — | remaining 17 | 0–13 | various | Research fragments. Notables: **telp** ("memory-first artificial mind", closest to membrane's ambition, 0★), **Holographic-RAG** (embeddings → holographic topologies). |

**Dynamic field with entity scripts — the answer is NO.** Checked directly:
holomem's "dynamic" is temporal only (clock-dependent weights, passive
items); engram has background processes (consolidation) but no per-entity
behavior; telp's rows are explicitly passive. **Actuated entities are
membrane's alone in this topic.**

## 2. The mainstream agent-memory world (2026)

| Project | ★ | What it is | The take |
|---|---|---|---|
| mem0ai/mem0 | 67k | additive extraction, 94.4% LongMemEval, SOC2 hostable | Better *product* by every ops measure; no field, no organism. |
| getzep/graphiti | 31.7k | temporal knowledge graph; **validity windows** — facts are invalidated *in the store*, 104 ms p50 | Closest echo of "the field is ground truth". Validity windows worth stealing. |
| topoteretes/cognee | 32k | local-first KG memory, strongest at long horizons | Same lane, graph-based, no VSA. |
| letta-ai/letta | 25k | pivoted to **git-backed memory repos the agent edits itself** with ordinary file tools | Philosophical echo of the seat training its own membrane: memory as a place the agent maintains. |
| Cloudflare Agent Memory, HINDSIGHT | beta | typed Facts/Events/Instructions; **facts vs opinions** separated into four networks | HINDSIGHT's typing is a clean idea for the field's property kinds. |

## 3. The old giants

- **OpenCog / Hyperon** (opencog/atomspace 1.0k★, trueagi-io/hyperon-experimental 282★,
  singnet/das 18★). The one project that already answers "dynamic field with
  entity scripts": the AtomSpace is a live typed metagraph where atoms carry
  **ECAN attention values (STI/LTI — rent, wages, spreading)**, inference
  writes back into the field, and **GroundedSchemaNodes are atoms whose
  schema is executable code** evaluated during inference. Membrane's field
  is the small disciplined version; this is the maximalist academic one.
- **torchhd** (389★) and **IBM/neuro-vector-symbolic-architectures** (111★,
  incl. RAVEN): better-engineered VSA arithmetic than the field's hand-rolled
  ops — the place to borrow primitives from if the field ever needs GPU
  throughput.
- **infer-actively/pymdp** (743★): the formal active-inference math the
  predict/prognosis lanes approximate.

## 4. Are any of them better than membrane?

Honest mirror: **yes per axis, never at being membrane.**

- As *a product*: Mem0, Zep, Cognee win unambiguously — benchmarks,
  deployments, types. If membrane were "a memory library", it loses on day
  one.
- As *a substrate*: Hyperon is deeper and far more mature.
- As *pure math*: torchhd/IBM are cleaner than the field's bespoke ops.

But none is trying to be what membrane is: a person-shaped agent on a VSA
field, live in a home. That is a defensible niche — the topic page itself is
the argument: *membrane is what the rest of the space supplies parts for.*

---

## 5. The borrowed ideas (the work queue)

Ranked by fit to the doctrine (the seat trains its own membrane — anything
that puts a tool crutch in front of it does not qualify; Hillock's mode
gates and engram's consult-before-write tool contracts were deliberately
NOT borrowed for exactly that reason).

1. **Last-confirmation decay + mention-reinforcement** (holomem) — *the
   field's metabolism.* Mass fades on a half-life running from the last
   confirm/touch, not from birth; every mention adds weight (capped);
   contradictions damp (×0.35) instead of deleting, keeping history.
   Smallest change, most organism-payoff: dead names sink by themselves.
2. **The refusal gate** (Hillock) — an optional strict mode on the /v1
   relay lane: answer only what the field backs, cite the entity, refuse
   otherwise. The honesty doctrine, made mathematical.
3. **Session handoff** (engram) — a structured end-of-session summary the
   next session wakes with; the sticky-goal rebind when intent contradicts.
4. **Scars + per-record reliability** (engram) — a recall miss records a
   scar on the entity; a coherence score gates what memory may propose.
5. **Validity windows** (Graphiti) — a fact knows when it stopped being
   true; cleaner than contradiction-damping alone.
6. **Auditable memory diffs** (Letta) — the field's changes as reviewable
   history (feeds the known audit gaps too).
7. **Facts-vs-opinions typing** (HINDSIGHT) — property kinds split what is
   known from what is believed.
8. **torchhd primitives under load** — swap/upgrade the field's hot
   arithmetic only when scale demands it.