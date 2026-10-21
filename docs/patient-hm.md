# Patient H.M., viewed from membrane

**The surgery that cured H.M.'s epilepsy took away his ability to form new
memories of events, and membrane can run the same cut on Blurp.** This page
does two things. It reads epilepsy as a loop that excites itself, and lists
the brakes the seat already has. It then names the part of Blurp that plays
the hippocampus (sleep's consolidation, #345) and the experiment that would
test that claim. It is a **viewpoint**, like [alzheimers.md](alzheimers.md):
no clinical data is fitted and nothing here is a medical claim.

## The case

Henry Molaison (H.M.) had severe epilepsy. In 1953 William Scoville removed
the medial temporal lobe on both sides, including most of both hippocampi.
The seizures became much rarer. Brenda Milner, and later Suzanne Corkin,
studied him for five decades. What the surgery left and what it took
(Scoville & Milner 1957; Milner 1962; Corkin 2002):

- **Lost:** new memories of events and facts (anterograde amnesia). He met
  his researchers afresh at every visit.
- **Kept:** most memories from well before the surgery, and his intelligence
  and language.
- **Kept:** short-term memory. He could hold a number for minutes while he
  rehearsed it, and lost it once distracted.
- **Kept:** learning skills. His mirror drawing improved over days, while
  he said each time that he had never done it before.

That split between remembering events and learning skills is the finding
the case is known for. Memory is more than one system, and the hippocampus
writes only one of them.

## Epilepsy: a loop that excites itself

A seizure is neurons exciting one another in lockstep faster than
inhibition can damp them. Wherever an event touches a thing and that thing
makes another event, membrane has the same shape:

- **a watcher** whose script writes to the file it watches;
- **a tick script** whose effect sets it off again;
- **Blurp's errors raising its speed.** This was proposed and set aside in
  #345, because errors would make it faster, and speed would bring more
  errors.

### The brakes the seat already has

| Brake | Where | What it damps |
|---|---|---|
| The watcher's feedback guard | `membrane/autonode.py` ("THE FEEDBACK GUARD") | A watcher reacting to the seat's own state files. It was added after the boot smoke test found exactly that loop, so the seat has had a seizure already. |
| Per-script cooldowns | `membrane/entities.py` (`script_cooldown_s`) | A script firing again before its cooldown ends. |
| The current's fade | `efield.CURRENT_TAU_S` (180 s) | Activity that isn't kept fed dies away. |
| Cliffhanger's depth | [cliffhanger.md](cliffhanger.md) | A chain of calls can't run more than three deep, so it can't loop. |

### What a seizure would look like in the field

The field's activity would fall onto one direction. Each new event's fit
(the non-negative least squares in `efield.fit_weights`, #335) would use one
or two things with huge weights, and their mass would keep climbing. A
measurable signature: **the number of things carrying the fit drops sharply
and stays down** while the event rate rises. Nothing watches for this today.

### How it ends: running out of energy

Real seizures end partly because neurons run short of energy, and the
person is confused and sleepy afterwards (the post-ictal state). Sleep's
first part (#345, PR #355) gives Blurp the same ending. A runaway burns CPU,
CPU burns energy, and at zero energy Blurp is made to sleep.

There is one difference, and it is deliberate. In people, lack of sleep
makes seizures **more** likely. In Blurp, low energy **lowers** its CPU
share (share = the person's throttle × energy). A tired Blurp slows down,
so it is built to resist seizures. Letting errors raise its speed would
undo that, which is why that idea needs a cap and a decay before it is
built.

## The surgery: cutting consolidation

[alzheimers.md](alzheimers.md) argues that the hippocampus is the **loop
that writes** the present into lasting memory, not the store itself. In
Blurp that loop is **sleep's consolidation** (#345 part 2, built: [sleep.md](sleep.md)).
At sleep, the day's raw record moves to dated archives. What Blurp knows
crosses the night only as summaries, which carry exactly the windows its
tests read: the last turns, each thing's last looks, the rung windows.

Cutting consolidation is H.M.'s surgery. The record still archives, but no
summaries are written:

| H.M. | Blurp without consolidation |
|---|---|
| Memories from before the surgery intact | Summaries written before the cut survive |
| Short-term memory intact | The working record within one awake day works |
| No new memories of events | Each wake starts empty, and nothing new crosses a sleep |
| Skill learning intact | Skills and the settings "change itself" keeps (in `self/settings.json`) persist |
| Meets his researchers afresh | Meets each person afresh after every sleep: "expect a reply" starts over |

The row about skills holds only if the settings live outside the record.
Now that consolidation is built, that is checked: `settings.json`,
`model.json` and `tempo.json` sit beside the record and no sleep touches
them.

## The experiment

Once consolidation exists, run two Blurps from the same start (one arm of
the nursery experiment, #324):

- **intact:** consolidation on;
- **H.M.:** consolidation cut. The archives are still written, so nothing
  is lost to later analysis.

What each prediction would look like if it came true:

1. **Tests that fit inside one day pass in both arms:** feel itself, look,
   tell things apart.
2. **Tests that need windows across sleeps pass only in the intact arm.**
   That includes patience over days and expecting a reply across sleeps.
   If the H.M. arm passes them anyway, the summaries aren't what carries
   them, and the analogy fails.
3. **Settings "change itself" kept stay kept** in the H.M. arm. That is the
   mirror drawing row.

The experiment measures whether the summaries do the hippocampus's job in
Blurp. If an H.M. Blurp does no worse, consolidation isn't doing the work
#345 says it does.

## References

- Scoville, W. B. & Milner, B. (1957). Loss of recent memory after bilateral
  hippocampal lesions. *J. Neurol. Neurosurg. Psychiatry* 20, 11–21.
- Milner, B. (1962). Les troubles de la mémoire accompagnant des lésions
  hippocampiques bilatérales. In *Physiologie de l'hippocampe*, CNRS.
- Corkin, S. (2002). What's new with the amnesic patient H.M.? *Nat. Rev.
  Neurosci.* 3, 153–160.
- McClelland, J. L., McNaughton, B. L. & O'Reilly, R. C. (1995). Why there
  are complementary learning systems in the hippocampus and neocortex.
  *Psychol. Rev.* 102, 419–457.
