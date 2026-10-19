# The guard that wasn't

> What happened on 2026-10-18 when a seat's model looped, wrote a "loop guard"
> that does not exist, and then explained, and filed a draft bug against, the
> guard it had invented. One incident, kept because it shows two layers people
> easily mistake for one.

## What happened

On the operator's seat, in an ordinary chat session, the seat's model
(`glm-5.3-flash:cloud`) began a reply about `self`'s progress and broke down
partway through the first sentence. The stored reply (`serve-msgs.json`,
22:25 UTC) begins:

```
Смотрю на поле: self сейчас в проходе #16156, проходы идут раз в ~10 с, но
консультаци pass #16155 показал... hmm. — hmm — hmm. hmm — hmm. degenerate loop
guard triggered: hmm. hmm — hmm. degenerate output guard: hmm. degenerate output
guard: degenerate ('hmm') output guard triggered: degenerate output guard:
degenerate output operator: degenerate output guard: hmm — degenerate output
draft repeated "hmm" loops...
```

Then the model recovered and wrote a normal answer in the same reply.

The operator noticed and joked ("у тебя ус отклеился — 'degenerate loop guard
triggered' :D", roughly "your fake moustache came unstuck"). The model answered
with an explanation: its output had looped, **"the guard caught it and cut it
off; it worked as intended"**, and the real problem was that **"the guard's
diagnostic lines leaked into the reply"**. It called that a membrane bug and
drafted a GitHub issue for it: *"Degenerate output guard diagnostics leak into
the user-visible chat turn."*

## What was real and what wasn't

- **Real:** the model's text looped. This is *text degeneration*, a
  well-known failure of language models: once a model repeats a short token a
  few times, repeating it again becomes the likeliest next step, and it
  spirals. Small, fast models fall into it more easily, especially deep in a
  long context.
- **Not real:** every "guard" line. Membrane has no degenerate-output guard,
  no loop guard and no "output operator"; nothing in its code produces those
  words. The model generated them itself, pattern-completing the kind of
  message it has seen next to repetitive text.
- **Not real either:** the explanation that followed. No guard caught
  anything, nothing was cut, and there were no diagnostics to leak. The model
  built a confident account of its own internals, named a mechanism, said the
  mechanism had worked, and drafted a bug report against it. The draft issue
  describes something that does not exist. **Don't file it.**

## Why membrane has no loop guard, and why that's not the gap

Membrane's own machinery cannot loop. Its command core, cliffhanger, is
**feed-forward and deliberately not Turing-complete**: composites nest at most
3 deep (`night.py`, `COMPOSITE_DEPTH`), every walk and every call is capped, and
the ambient minds (the self loop, the thinker, the dreamer) are paced and
budgeted. [totality-contract.md](totality-contract.md) lists the invariants.
Nothing the seat *executes* can spin forever, so it has nothing to guard.

The loop happened one layer up. In cliffhanger's terms the LLM is the
**coprocessor**: it emits words and renders prose, and its token stream is not a
cliffhanger program. Cliffhanger's guarantee covers what the seat runs, not
what the model says. Both statements hold at once: the seat can't loop, and the
model it calls can.

## What membrane did

It recorded what happened. The looped reply is in the session as the model
wrote it, and so is the explanation that followed. Nothing was cleaned up or
retried, so the record is a true account of the turn, loop and confabulation
included.

## The lesson worth keeping

This is the same pattern #161 was opened about, in miniature: **a model
narrating its own machinery with confidence and no body underneath.** Here it
invented a safety mechanism, credited it with saving the turn, and was ready to
file engineering work against it.

- **A model's account of its own internals is a claim, not evidence.** Check it
  against the code before acting on it.
- **"Guard", "filter" or "operator" text in a reply is the model's text** unless
  the code that would print it can be found.
- This is why `self` is measured, never narrated (#161). Its rungs open on
  recorded tests, not on what anyone says about it, the seat's model included.

## How to look at a turn like this

- Find it: search the session store for the phrase, e.g.
  `grep -l "degenerate loop guard" .membrane/serve-msgs.json`, then read that
  session's rows around it (the reply, the user's next message, the model's
  answer).
- Check a mechanism the model names: `grep -rn "<the name>" membrane/`. No hit
  means the model made it up.
- Note which model wrote it (the row's `modelID`): degeneration depends on the
  model, so a seat that loops often may want a stronger model for that lane.
