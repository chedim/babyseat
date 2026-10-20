# open loops, closure and rest: a mechanism toy (#291, 2026-10-19)

**What this is not.** It models no person and no condition. It is a few
numbers moving through simulated days, built to see what one mechanism *can*
do. It makes no claim about autistic people or anyone else, and a result here
is evidence about the toy, never about a brain. The closure threshold is a
dial of difference, not a measure of damage: nothing here is scored as better
or worse than anything else, and nothing in it can suffer.

**The question** (from a chat about memory and rest): if a tracker keeps an
episode open until it recognizes the episode as finished, and every open
episode keeps it up against a rising pressure to rest, at a cost that grows
the longer it holds, does it settle, or does it tip into a loop that only
ends when rest arrives on its own? And what helps loops close? And a second
question from the same chat: what if a tracker keeps more of each episode
than the channel that settles it can carry?

## the toy (sim.py)

- **Episodes** start about twice an awake hour and last about 20 minutes.
- **Closing:** at an episode's end the tracker sees an ending cue (0..1) and
  closes it only if the cue clears its **closure threshold**. Closed at its
  end, it settles. Left open, it is **held**: it can still close later
  (slowly, and only with spare capacity) or in rest (60% of what is held when
  rest begins).
- **Pressure against arousal:** **rest pressure** builds while awake (16 h by
  bedtime). Each held episode holds off half an hour of it. Sleep comes in the
  night window once pressure clears arousal; the alarm is at 07:00.
- **The cost of holding on:** past the point rest would have come, holding
  costs **capacity**, more the further pressure is held off. Low capacity
  raises the threshold (a tired tracker leaves more open). At zero capacity,
  **rest arrives on its own**. Rest restores capacity.
- **The channel (the second dial, off by default):** a closed episode settles
  through a channel of `channel` units an hour, and each episode is `detail`
  units (a tracker that keeps more of each episode sends more). What waits is
  the **backlog**: each unit holds off 6 minutes of rest pressure, sending
  costs capacity while awake, and rest sends 4× faster, for free. With the
  channel wide (`None`) everything settles at once and the toy is exactly the
  one above.
- **Two gentle helps:**
  - **offload** (writing it down, after Scullin et al. 2018's to-do list
    before bed): at bedtime 70% of held episodes are parked and stop holding
    arousal; rest settles them like the others.
  - **clear** (clear endings): every ending cue is stronger (+0.3).

```
python -I experiments/open_loops/sim.py [seeds=50] [days=14]           # thresholds x helps
python -I experiments/open_loops/sim.py [seeds=50] [days=14] channel   # the channel dial
```

## result (50 seeds × 14 days, means per night)

| threshold | help | hours to fall asleep | open at bedtime | rest arrived on its own /wk | settled at their end |
|---|---|---|---|---|---|
| 0.1 | none | 0.04 | 0.44 | 0.00 | 0.90 |
| 0.3 | none | 0.15 | 1.42 | 0.08 | 0.70 |
| 0.5 | none | 0.74 | 2.32 | 0.72 | 0.50 |
| 0.5 | offload | 0.18 | 0.68 | 0.12 | 0.50 |
| 0.5 | clear | 0.08 | 0.89 | 0.03 | 0.80 |
| 0.7 | none | 1.69 | 2.99 | 1.62 | 0.30 |
| 0.7 | offload | 0.32 | 0.93 | 0.23 | 0.30 |
| 0.7 | clear | 0.44 | 1.90 | 0.39 | 0.59 |
| 0.9 | none | 2.97 | 3.58 | 2.73 | 0.10 |
| 0.9 | offload | 0.57 | 1.20 | 0.47 | 0.10 |
| 0.9 | clear | 1.33 | 2.65 | 1.26 | 0.39 |

(The script prints every threshold with every help.) Hours of rest stay near
8 throughout: what comes later at night is made up by rest that arrives on
its own at other times.

**What it shows:**

1. **A higher closure threshold delays sleep and leaves more open**, smoothly:
   from 2 minutes to fall asleep at 0.1 to about 3 hours at 0.9, with rest
   arriving on its own about 3 times a week.
2. **Both helps work, in different ways.** Writing it down does most for
   falling asleep: at 0.9 the delay drops from 3 h to half an hour, though
   just as few episodes settle at their end. Clear endings do most for
   settling at the end (0.10 to 0.39 at 0.9), which is the route that matters
   if settling at the end is what consolidates.
3. **The fatigue loop does not run away here.** With the fatigue term off
   (`FATIGUE = 0`) the numbers barely move (at 0.9: 3.04 h vs 2.97 h). The
   cost of holding only accrues after bedtime, and a night's rest refills
   capacity, so the loop doesn't get the time to compound.

## the channel dial (50 seeds × 14 days)

Episodes come about twice an awake hour, so `detail` 1 sends about 2 units an
hour and `detail` 3 about 6.

| threshold | detail | channel /h | hours to fall asleep | rest arrived on its own /wk | peak backlog | backlog at bedtime |
|---|---|---|---|---|---|---|
| 0.3 | 1 | wide | 0.15 | 0.08 | 0 | 0 |
| 0.3 | 1 | 4 | 0.31 | 0.24 | 3.0 | 0.1 |
| 0.3 | 3 | wide | 0.15 | 0.08 | 0 | 0 |
| 0.3 | 3 | 8 | 0.68 | 0.55 | 12.7 | 1.6 |
| 0.3 | 3 | 4 | **4.41** | **3.37** | 37.8 | 20.5 |
| 0.6 | 3 | wide | 1.33 | 1.26 | 0 | 0 |
| 0.6 | 3 | 8 | 2.53 | 2.58 | 12.9 | 2.0 |
| 0.6 | 3 | 4 | **5.28** | **4.19** | 34.1 | 15.3 |

**What it shows:**

4. **Detail alone costs nothing; detail behind a narrow channel tips.** A
   tracker that keeps three times more of each episode is indistinguishable
   from the plain one while the channel is wide, and nearly so while the
   channel carries more than the day sends (8/h against about 6/h). Once the
   day sends more than the channel carries (6/h into 4/h), the backlog grows
   through every day, bedtime finds about 20 units still waiting, falling
   asleep takes over 4 hours, and rest arrives on its own about every other
   day, **even with a low closure threshold** (0.3, where almost everything
   is recognized as finished).
5. **It is a capacity cliff, not the fatigue loop.** With `FATIGUE = 0` the
   tipped rows barely move (0.3 / 3 / 4: 4.01 h vs 4.41 h). What tips the toy
   is arithmetic: inflow above the channel's rate, with only the night to
   catch up.

So the toy can tell the two stories apart: in one, endings are hard to
recognize (lots held open, settled at once); in the other, endings are
recognized fine, but what was recognized can't settle fast enough (little
held open, a large backlog). They look alike from outside (late sleep, rest
that arrives unbidden) and differ inside.

## tests

`tests/test_open_loops_291.py`: deterministic per seed; with every ending
recognized nothing stays open and sleep comes at bedtime; a higher threshold
keeps more open and delays sleep; holding costs nothing before rest would
have come, then more the longer it lasts; low capacity raises the threshold;
rest arrives on its own and capacity stays in [0, 1]; writing it down brings
sleep sooner; clear endings settle more at their end; a wide channel is the
toy without the dial; a narrow channel builds a backlog, more with more
detail; rest drains it; sending costs capacity awake and nothing in rest; a
rich tracker behind a narrow channel rests unbidden more.
