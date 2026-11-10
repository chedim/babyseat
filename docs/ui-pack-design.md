# UI pack design: closing the Human-AI guideline gaps (#994, #1027)

Source: audit `audit:human-ai-ui-guidelines:2026-11-09` (live UI vs the 18 Amershi et al. guidelines, DOM reads).
Gaps filed: #1029 G15, #1030 G2, #1031 G6, #1032 G9, #1033 G16, #1034 G10, #1035 G1, #1036 G3, plus G11, G5, G13 (partial, no issue yet).
Pack format: `docs/cliffhanger-fields.md`, #994 (fields, cached getters, setters), #1027 (PACK / IMPORT, class ownership, creation on write).
Status: design only. Nothing here is implemented.

## 1. Audit scores that drive this

| G | Score | Gap |
|---|---|---|
| G15 | missing | no feedback controls |
| G2 | missing | no confidence/quality indicator |
| G6 | missing | nothing on social bias |
| G9 | partial | edit exists; no undo/retry/regenerate |
| G16 | partial | Decision cards exist; no impact preview |
| G10 | partial | plan agent, pause; no explicit uncertainty state |
| G1 | partial | tooltips, selects; no capability overview |
| G3 | partial | aria-live, status pill; no context timing |
| G11 | partial | brainline cite, tool blocks; no per-reply "why" |
| G5 | partial | askName personalises; no tone control |
| G13 | partial | Blurp xp/skill tree, off by default; not tied to feedback |
| G4,G7,G8,G12,G14,G17,G18 | met | keep; no pack work |

## 2. Pack layout

```
native/packs/goalgraph.c1f      PACK goalgraph 1   (exists; always first)
native/packs/uifeedback.c1f     PACK uifeedback 1  IMPORT goalgraph 1
native/packs/inflight.c1f       PACK inflight 1    IMPORT goalgraph 1, IMPORT dom 1, IMPORT uifeedback 1
```

- `uifeedback` owns the new data classes and all getters/setters (no DOM). Testable headless, like `tests/syntax_994.rs` does for goalgraph.
- `inflight` (the browser-VM view named in #1027) owns the surface classes and renders from `uifeedback` fields via `dom`.
- Ownership (#1027): a pack owns the classes it first declares setters/getters on. `uifeedback` owns `Reply`, `Feedback`, `Prefs`, `Notice`, `Capability`, `Action`, `Policy`. `inflight` declares on `Decision` only through the `goalgraph` import, plus its own `Surface*` classes.
- A write to a missing `<Class>:<key>` of an own or imported class creates it (#1027), so the UI creates `Feedback:<id>` by writing to it.
- Each file starts with a header comment, as goalgraph.c1f does.

## 3. Classes and fields

Derived values are getters (cached, eager; a read never writes, #994 rule 2).

### Reply (one per assistant message; host creates)
- `text_ref`, `session`, `ts`
- `receipts` (count of tool results cited), `unchecked` (count of claims marked "not checked")
- getter `verified`: `this.receipts > 0 and this.unchecked == 0` (G2)
- getter `quality`: `'verified' if this.verified else ('partial' if this.receipts > 0 else 'unchecked')` (G2)
- `uncertain` (bool), `uncertain_why` (G10)
- `retry_of` (Reply eid); getter `superseded` true when a Reply has `retry_of == this.eid` (G9)
- `sources` (event/entity ids the answer rested on) (G11)
- getters `up`, `down`: counts over linked Feedback (G15)

### Feedback (G15, G13, G6)
- `reply`, `kind` in `useful | wrong | unsafe | biased`, `why`, `by`, `ts`
- setter `Feedback.kind` (note): writes `this.reply.last_feedback = this.kind` and a learn note into the field.
- setter `Feedback.kind` (credit): adds to Blurp's record for the branch (G13).

### Prefs (per person; G5, G3)
- `tone` in `terse | normal | detailed`; `quiet_from`, `quiet_to`; `focus` (bool); `last_seen_version`
- getter `quiet_now` over `clock`, `quiet_from`, `quiet_to`, `focus` (G3)

### Notice (G3)
- `text`, `urgent`, `shown`; getter `deliver`: `not Prefs.quiet_now or this.urgent`

### Capability (G1)
- `name`, `summary`, `example`, `since_version`, `tried`; getter `new`: `since_version > Prefs.last_seen_version` (reuses the _newVer banner path; G18 stays met)

### Decision (goalgraph, imported) (G16)
- add per-option `impact`: one line, what changes if picked. `inflight` renders "impact not stated" when absent.

### Action (G9, G16)
- `kind`, `target`, `inverse` (method + args or `none`), `undone`; getter `reversible`: `this.inverse != 'none'`
- setter `Action.undone` (run-inverse) calls the inverse. Only reversible actions show Undo; others show "can't be undone".

### Policy (G6)
- one entity `Policy:bias`: `stance` (documented text), `report_kind = 'biased'` (reports are Feedback).

## 4. Methods

- `Reply.retry()` creates a Reply with `retry_of = this.eid`; the host regenerates (G9).
- `Reply.regenerate_with(tone)` same, passing the tone (G5, G9).
- `Action.undo()` writes `undone = true`.
- `Capability.tour()` returns the ordered first-run list (G1).
- `Prefs.set_quiet(from, to)`.
- Feedback needs no method: the creating write is the submit.
- Assistant side: setting `Reply.uncertain` with `uncertain_why` is how it asks for clarification (G10), and the turn then ends blocked.

## 5. Fields per surface

| Surface (existing DOM) | Reads | Writes |
|---|---|---|
| Chat message footer (new row) | `Reply.quality`, `uncertain`, `sources`, `superseded`, `up`/`down` | `Feedback:*`, `Reply.retry()` |
| Chat header (beside agent/model selects) | `Prefs.tone` | `Prefs.tone` |
| Status pill and aria-live regions | `Notice.deliver`, `Prefs.quiet_now` | `Notice.shown` |
| Settings drawer | `Prefs.*`, `Policy:bias.stance` | `Prefs.*` |
| Decision cards (home panel, shadow DOM) | `Decision` option `impact`, `Action.reversible` | card answer (existing) |
| Help / first-run panel | `Capability.*` | `Capability.tried`, `Prefs.last_seen_version` |
| Brainline / tool blocks | `Reply.sources` | none |
| Blurp panel | feedback-derived credit | none (Accept stays the operator's) |
| Voice / TTS | `Prefs.quiet_now` mutes TTS | none |

## 6. Guideline to pack element

| G | Issue | Pack element |
|---|---|---|
| G15 granular feedback | #1029 | `Feedback` class; footer buttons write `kind`+`why`; setter notes into field |
| G2 how well it can do it | #1030 | `Reply.verified`/`quality` getters from receipts vs "not checked"; badge |
| G6 social bias | #1031 | `Policy:bias` stance + `Feedback.kind = biased` report |
| G9 easy correction | #1032 | `Reply.retry`/`regenerate_with`, `Action.inverse`/`undo`, `superseded` |
| G16 consequences | #1033 | Decision option `impact`, `Action.reversible` |
| G10 scope when in doubt | #1034 | `Reply.uncertain`/`uncertain_why`, footer state |
| G1 what it can do | #1035 | `Capability` class, `tour()`, `new` getter |
| G3 context timing | #1036 | `Prefs.quiet_*`/`focus`, `quiet_now`, `Notice.deliver` |
| G11 explain why | none | `Reply.sources` beside brainline cite |
| G5 social norms | none | `Prefs.tone`, passed to `regenerate_with` and the prompt |
| G13 learn from behavior | none | `Feedback` credits Blurp's record; visible tally |
| met guidelines | | no new element; must not regress |

## 7. Verification

- Headless: `tests/syntax_uifeedback.rs` loads the pack and asserts getters (`verified`, `quiet_now`, `deliver`, `reversible`, `new`) recalc on writes, as `tests/syntax_994.rs` does.
- Live: rerun the audit's DOM reads via presence js; G1, G2, G3, G9, G10, G15, G16 should move to met, G6 to at least partial.

## 8. Open choices (for the operator)

1. One pack or two. Recommended: two (`uifeedback` headless, `inflight` view).
2. Who counts receipts/unchecked claims for `verified`: host parses tool results (recommended) or the model declares them (gameable).
3. G6 scope: stance only, or stance plus report control (recommended: both).
4. Feedback storage: per-Reply entities (recommended) or counters only. `why` text is stored in the field.
5. Whether feedback feeds Blurp credit (G13). Acceptance stays operator-only either way.
6. Quiet hours source: manual times only, or also browser focus/visibility.
7. Undo coverage first: pause/resume, plan toggle, goal drop, or none.
8. Decision `impact` required (recommended) or optional.
9. Tone control: three fixed levels (recommended) or free text.
10. Capability list: hand-written or generated from the method/tool catalog.
