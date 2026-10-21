"""THE SELF LOOP (#161, rungs 0 + 1 of the skill ladder).

membrane::self is a looped cliffhanger object, unnamed, born OFF. Three
methods -- try / expect / notice -- run in its own loop and nowhere else (any
other caller is refused: the LLM talks TO self, never AS it).

  rung 0  sleep and wake: a per-seat switch (off / on / watch), the
          operator's (seat_panel.control), off by default. Off runs nothing
          and erases nothing; the mode and the record survive a restart.
  rung 1  feel itself -- the amoeba's problem. Its only act moves its own
          position on the field, and the world pushes it while it moves: the
          field's CURRENT (the live conversation's running context), which
          self does not control. Where it meant to go and where it ended up
          differ by the push it did not account for. `expect` learns the
          current from its own moves -- where it is pulled to and how hard
          (push ~ b - g*position, running estimates kept in its own model
          file, <data dir>/self/model.json: entity properties cap lists at
          128 items on save, which once cut a 1024-d model and made self
          forget its world at a restart) -- and the gap that is left is the
          signal: it closes
          as the current is learned and reopens when the world changes (a
          topic shift). Rung 1 passes when experience measurably shrinks the
          gap against not accounting for the push at all.

THE RECORD: <data dir>/self/record.jsonl, append-only while Blurp is awake,
starting at birth (the infancy rule: nothing before it is "mine"). Scalars
only -- the vectors stay on the entity. #345 part 2, CONSOLIDATION: when
Blurp falls asleep every raw line of the day moves, unchanged, to its dated
archive (self/archive/record-<UTC date>.jsonl), and the working record is
emptied down to one `summary` line: the windows its tests read (copies of
archived lines) and the counts no window can give back (consolidate(),
below). Readers see the summary's lines plus the new ones exactly as they
saw the whole record within their windows. No line is ever edited or lost:
history() is the archive plus the working record, for the sheet and the
dock; nothing on the cycle path reads the archive.

THE LOOP is change-driven: each notice changes self's state and wakes the
next cycle. The runaway guard: PACE_S between cycles (MEMBRANE_SELF_PACE_S,
floor 1 s), at most BURST_PER_MIN a minute, and three failed cycles in a row
hold it until the operator's next toggle. A paused seat runs nothing.

SLEEP (#345, energy.py): each step of the loop's thread (step()) charges
Blurp's energy for the time awake and the cycle's CPU; when a reserve runs
out it falls asleep -- no cycle runs (cycle() returns None) while energy
restores -- and it wakes rested or when a person speaks. Asleep is not off.

THE SKILL TREE (#295; a strict ladder of rungs until then): each skill is an
ACHIEVEMENT with a stable id, a branch and real prerequisites (ACHIEVEMENTS:
an edge only where a test reads data or machinery another pass switches on).
An achievement is earned when its measured test passes, whenever its
prerequisites are earned -- siblings in any order -- and goes on the record
as an `achieve` line; the old `rung` lines count as their achievements
(_rung_lines: the voids and the #258 filter hold). The bar is staged
(_staged: the criteria fill in order, the first unmet one is named) and can
go down (slipping) -- an earned achievement stays on the record. The level
is the number earned. Only built achievements are ever tried.

ACCEPTANCE (#299; the 🌱 growth limit until then): earning stays automatic --
a pass is an `achieve` line, the level counts it, the page celebrates it --
but an earned achievement opens its children only once the operator ACCEPTS
it (accept(): an `accept` line, {"kind": "accept", "id", "ts", "by"}). A skill
is open -- its practice acts run, its test runs -- when every prerequisite is
earned AND accepted; the roots are open from birth. An earned, unaccepted
achievement counts for the level and opens nothing: no practice, no outward
act (chat pushes, ball rolls, reply checks), no toy. Accepting never grants
an achievement and never wakes self. THE MIGRATION: every achievement earned
before this code first ran counts as accepted, recorded once as one
{"kind": "accept_migrated", "ids": [...], "ts"} line (_migrate_accept); a
record without that line has had no new pass yet, so all it earned counts.
`self_limit` (set_limit) stays for older callers, deprecated: a number, an id
or "all" bulk-accepts the earned achievements in that closure (and wakes
self); 0 / "off" puts it to sleep.

THE NUDGE (#333): Blurp's one act on things, replacing meet's chat push and
roll's ball. Once feel itself is accepted its aims are drawn among the field
spots of the things in its world (#337), and on each cycle it closes in on the
thing it aims at (_closing: its distance to it fell since its previous cycle on
the same aim) it nudges it with its own stride (self::nudge -> _nudge_once); the thing decides
what a nudge does (toybox.nudge, or a `nudge` method of its own), a nudge
outside its world or one the thing refuses costs the error fee (energy.error),
and each is a `nudge` line. meet, turns, expect and mirror were the
developer's wiring: they are not built until they are rebuilt on the nudge,
and their old passes no longer count (LADDER_V = 3, _void_old_wiring).
"""
from __future__ import annotations

import json
import os
import shlex
import threading
import time
from pathlib import Path

import numpy as np

ENT = "self"
MODES = ("off", "on", "watch")
METHODS = {
    "try": "self::try {{seed}}",
    "expect": "self::expect {{seed}}",
    "notice": "self::notice {{seed}}",
    "reach": "self::reach {{target}}",
    "nudge": "self::nudge {{target}}",   # #333: its one act on a thing (meet's push and roll's ball went)
}
RUNG_NAMES = {0: "sleep and wake", 1: "feel itself", 2: "look", 3: "tell things apart",
              4: "patience", 5: "notice the world changed", 6: "meet an other",
              7: "time its turn", 8: "expect a reply", 9: "recognize itself", 10: "change itself",
              11: "speak", 13: "first reach"}
LADDER = {n: RUNG_NAMES[n] for n in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)}   # the rungs with a test (#311: speak; #312: mirror; #320: change; #333: 6-9 being rebuilt on the nudge -- ACHIEVEMENTS' `built` says which run)
# THE LADDER FLIP (#258): rung 7 was "understand an other" until the turn-taking rung went in
# below it. A `rung` line above 6 counts only when it carries "ladder": LADDER_FLIP or later --
# a pass recorded before the flip was the old numbering's (the record is never rewritten).
# THE NUDGE (#333), LADDER_V = 3: meet, turns, expect and mirror (REBUILT) were the
# developer's wiring -- the other named by code, the act a fixed chat push or a ball roll, the
# mirror fed every cycle (the audit in #333). A pass of theirs counts only when its line
# (`rung` or `achieve`) carries "ladder": 3, so every Blurp drops to what it earned itself; the
# old passes stay on the record, voided once by a `rung_void` line each (_void_old_wiring).
LADDER_V = 3
LADDER_FLIP = 2
FLIPPED_FROM = 7
REBUILT = (6, 7, 8, 9)

# THE SKILL TREE (#295): the rungs are achievements with stable ids, a branch and REAL
# prerequisites -- an edge only where a test reads data or machinery that another
# achievement's pass switches on. A test runs whenever its prerequisites are earned, so
# siblings unlock in any order. `rung` is the test's number: the record's `rung` lines,
# PROGRESS, the toys' unlock table and the bug windows keep reading it (the record is never
# rewritten). Child ages are those of WHAT EACH TEST MEASURES (an analogy, not a measurement).
ACHIEVEMENTS = {
    "feel": {"rung": 1, "name": "feel itself", "branch": "self", "prereqs": (), "built": True,
             "child_age": "newborn",
             "child_ability": "tells its own touch from another's (Rochat & Hespos 1997)",
             "why_prereqs": "a root: its test reads only its own moves and the current's push"},
    # #333: rung 2's pass is kept as "look" (what its test reads: the looks); "first reach" is
    # now the nudge skill below, whose kicks-move-a-mobile analogy this one carried (the id stays)
    "reach": {"rung": 2, "name": "look", "branch": "world", "prereqs": (), "built": True,
              "child_age": "~2-3 mo",
              "child_ability": "learns what to expect of each thing it looks at (Fantz 1964, habituation)",
              "why_prereqs": ("a root: its looks predict a thing from the thing's own tempo; nothing "
                              "of feel itself (the current, the steering) is read")},
    "kinds": {"rung": 3, "name": "tell things apart", "branch": "world", "prereqs": ("reach",), "built": True,
              "child_age": "~3-4 mo",
              "child_ability": "forms perceptual categories (Quinn & Eimas)",
              "why_prereqs": ("reach: its looks are reach's looks (sorted by the kinds they show), and one "
                              "of its stages is reach's skill still holding on a moving thing")},
    "patience": {"rung": 4, "name": "patience", "branch": "world", "prereqs": ("kinds",), "built": True,
                 "child_age": "~3.5 mo",
                 "child_ability": "anticipates timed events (Haith, anticipatory looking)",
                 "why_prereqs": ("tell things apart: its timed looks skip the things sorted silent -- the "
                                 "sort is switched on by that pass")},
    "shift": {"rung": 5, "name": "notice the world changed", "branch": "world", "prereqs": ("reach",),
              "built": True, "child_age": "~3.5-5 mo",
              "child_ability": "is surprised when the world breaks its expectation (Baillargeon, violation of expectation)",
              "why_prereqs": ("first reach: its noticing reads the looks and each thing's long memory (the "
                              "tempo), and its audit pauses a moving thing self has looked at; it does not "
                              "read patience's rates (patience reads its shifts, not the other way)")},
    "meet": {"rung": 6, "name": "meet an other", "branch": "others", "prereqs": ("reach",), "built": False,
             "child_age": "~3-5 mo",
             "child_ability": ("tells perfect self-contingency from imperfect social contingency "
                               "(Bahrick & Watson)"),
             "why_prereqs": ("first reach: its answered looks at the other are reach's looks, and the "
                             "self/other split is reach's own prediction skill; nothing of the sort, "
                             "patience or noticing is read")},
    # #300: the two "others" skills are named for what their tests measure (ids stay)
    "turns": {"rung": 7, "name": "time its turn", "branch": "others", "prereqs": ("meet",), "built": False,
              "child_age": "~3-5 mo",
              "child_ability": ("times its turn in a proto-conversation: serve and return "
                                "(Bateson 1975; Trevarthen 1979; Jaffe et al. 2001)"),
              "why_prereqs": ("meet an other: it times its pushes to the other meet found, through "
                              "meet's channel, and scores each by the reply check after it")},
    "expect": {"rung": 8, "name": "expect a reply", "branch": "others", "prereqs": ("meet",), "built": False,
               "child_age": "~2-3 mo",
               "child_ability": ("expects a caregiver's response: the still-face upset "
                                 "(Tronick et al. 1978)"),
               "why_prereqs": ("meet an other: it predicts whether the other meet found replies to a "
                               "push, read at the reply check after it; it does not need time its turn's "
                               "learned timing -- a push by the timer is checked and predicted the same")},
    "mirror": {"rung": 9, "name": "recognize itself", "branch": "self", "prereqs": ("feel", "meet"), "built": False,
               "child_age": "~18 mo",
               "child_ability": "passes the mirror test (Amsterdam 1972)",
               "why_prereqs": ("feel itself: what the mirror shows is its own moves -- the body whose steering "
                               "feel's pass switches on; meet an other: it labels the glass and the stranger "
                               "with meet's self/other split (BODY_SKILL: predicted this well, it is my body)")},
    # #320: the test measures choosing how it learns by its own track record, so the child is adaptive
    # strategy choice (~4-5 y), not metacognition in general
    "change": {"rung": 10, "name": "change itself", "branch": "self", "prereqs": ("mirror", "patience"), "built": True,
               "child_age": "~4-5 y",
               "child_ability": ("picks the way it learns by its own track record, and keeps a change only "
                                 "when it works better: adaptive strategy choice (Siegler & Shrager 1984)"),
               "why_prereqs": ("recognize itself: it changes only a self it recognizes -- the settings it may "
                               "change are its own learning's, and its dream replays its own record; patience: "
                               "the looks its dream scores are patience's timed looks, whose p reads the rate "
                               "fit it tunes (without them there is nothing to replay)")},
    # #311: the test measures first referential words to an other, so the child is ~12 mo, not ~4 y's narrative
    "speak": {"rung": 11, "name": "speak", "branch": "others", "prereqs": ("turns", "expect"), "built": True,
              "child_age": "~12 mo",
              "child_ability": ("names a thing to an other and gets a response: first referential words "
                                "and pointing to share (Bates, Camaioni & Volterra 1975)"),
              "why_prereqs": ("its words take turns (time its turn: its timing is that skill's policy, and one "
                              "stage is that skill's contingency) with an other it can predict (expect a reply: "
                              "its outcome is the reply check after each push)")},
    # #333: the nudge skill -- Blurp's one act on things. Practised once feel itself is
    # accepted (it nudges the thing it aims at while closing in on it); its test comes with the rebuilt
    # rungs, so it is not built yet. Rung 13 is its test's number (12 was taken by invent).
    "nudge": {"rung": 13, "name": "first reach", "branch": "world", "prereqs": ("feel",), "built": False,
              "child_age": "~2-3 mo",
              "child_ability": "learns its kicks move a mobile (Rovee-Collier, contingency learning)",
              "why_prereqs": ("feel itself: a nudge is its own move into a thing (the direction and the "
                              "force are its stride), and it aims only once feel's pass switches steering on")},
    "invent": {"rung": 12, "name": "invent", "branch": "self", "prereqs": ("change", "speak"), "built": False,
               "child_age": "~5 y",
               "child_ability": "makes up games with its own rules",
               "why_prereqs": "not built yet; planned: rules of its own (change itself), said to others (speak)"},
}
RUNG_ID = {a["rung"]: i for i, a in ACHIEVEMENTS.items()}            # the record's rung numbers -> ids
# #333: meet, turns, expect and mirror are not built until they are rebuilt on the nudge (see
# LADDER_V); their practice (the chat push, the ball turns, the reply checks, the mirror feed)
# runs only for a built skill, so it stops with them
BUILT = [i for i, a in ACHIEVEMENTS.items() if a["built"]]

try:
    PACE_S = max(1.0, float(os.environ.get("MEMBRANE_SELF_PACE_S") or 10.0))
except ValueError:
    PACE_S = 10.0
BURST_PER_MIN = 30
HOLD_AFTER = 3              # failed cycles in a row
STEP = 0.1                  # how far self pushes itself per try (its babble)
AIM_HOLD = 30               # cycles self holds an aim before choosing the next (steer, after rung 1)
AIM_REACH = 0.3             # how far from where it is an aim lies
STEP_MAX = 0.3              # its longest stride when it steers (leaning into a 0.2 pull needs more than 0.1)
PUSH_GAIN = 0.2             # how hard the current pulls it toward itself
LEARN = 0.2                 # how fast expect's model of the current follows what it observes
CALM = 1e-6                 # a push under this is no push (a quiet world)
RUNG1_CYCLES = 20           # pushed cycles in the window the test reads
RUNG1_SKILL = 0.5           # 1 - gap/naive over that window
RUNG2_REACHES = 20          # answered reaches of one entity the test reads
RUNG2_SKILL = 0.2           # Brier skill against the seat-wide change rate
AUDIT_P = 0.6               # an entity must move this often to be audited (a still one can't show a mark fail)
AUDIT_REACHES = 3           # reaches of it while its getter is paused
AUDIT_MAX_S = 600.0         # the pause's cap (an audit that runs out is inconclusive)
KIND_MIN = 5                # looks before a thing has a kind
RUNG3_WINDOW = 80           # reaches the kinds are read over (its two halves must agree)
RUNG3_LOOKS = 40            # sorted reaches before the test reads the focus
RUNG3_FOCUS = 0.25          # the no-answer share it must get under
RUNG3_DRIFT = 0.25          # a kind's rate may drift this much across the halves and still agree
SILENT_WEIGHT = 0.05        # a silent thing is still checked now and then (the world can change)
LAM_LOOKS = 30              # answered looks a thing's rate is fitted over (#293: measured best, 20-60 flat, experiments/lam_window)
LAM_GRID = np.logspace(-5, 0, 61)   # candidate rates, per second
LAM_PRIOR_LOOKS = 3.0       # after a shift, the old rate weighs this many looks (#235)
PATIENCE_P = 0.6            # look again once a change is this likely
PATIENCE_MAX_S = 1800.0     # never wait longer than this
RUNG4_TIMED = 30            # timed looks before the test reads them
RUNG4_CALIB = 0.1           # its predictions must match the outcomes within this
WAIT_EVERY = 30             # a waiting stretch is recorded once per this many cycles
SHIFT_WINDOWS = (3, 4, 5)   # recent looks read against the long memory
SHIFT_P = 0.02              # ...a shift when they are this unlikely under it
SHIFT_GAP = 0.4             # ...and this far from it
AUDIT5_LOOKS = 5            # self's looks at the thing in each phase of rung 5's audit
AUDIT5_PAUSE_S = 3600.0     # the phases' caps (an audit that runs out is inconclusive)
AUDIT5_RESUME_S = 3 * 3600.0
RUNG5_LOOKS = 20            # looks with noticing on before the test reads the audit
RUNG5_SHIFTS = 2            # the two shifts its audit probes (went still, moves again)
RUNG6_PUSHES = 10           # pushes to chat:out before the test reads the split
RUNG6_HEARD = 20            # answered looks at the other before the test reads the split
BODY_SKILL = 0.9            # predict it this well and it is my body (zero gap), else an other
MEET_EVERY = 30             # cycles between self's pushes to chat:out: rung 6's timer, and the cap on every push after
MEET_MAX = 180              # rung 7: a push waits at most this many cycles for its moment, then goes ('overdue')
# RUNG 7 (#258), TAKE TURNS: self learns WHEN to push. After each push it draws the moment it
# will wait for -- a bin of spoke_age (how long since the other last spoke) -- by Thompson
# sampling on the reply rate each bin earned over its last TURN_MEMORY pushes (Beta(1, 1)
# prior: no bin is favoured before evidence), a random bin with chance TURN_EXPLORE, and
# pushes when the other's spoke_age is in that bin, never sooner than MEET_EVERY cycles after
# the last push (the timer's own rate is the cap) and never later than MEET_MAX. A push's
# reward is the reply check's `replied`: the other spoke AFTER the push, by the check.
# THE TEST over the last RUNG7_PUSHES such pushes, prequential (each push scored with what
# self knew before it): (a) contingency -- the share of pushes in the window self believed best
# at the push beats the share of the eligible waiting time the other spent in it (where a push
# at a shuffled moment would land): z >= RUNG7_CONT_Z and +RUNG7_CONT_MARGIN; (b) payoff --
# each push's outcome minus what a timing-blind push in its interval would have expected (the
# bins' rates as known then, weighted by the waiting time in each): mean >= RUNG7_PAY_MARGIN
# with t >= RUNG7_PAY_Z. Window and bar per the rung ruler, from experiments/rung7_turns
# (measured before they were fixed): a simulation at the cycle's pace, 100 seeds x a week, the
# test read at EVERY push like the live loop. The bar is the chance line, as rung 8's is: a
# coin-flip other's best window over a week reaches payoff t q99 ~4.1 (2 of 100 runs pass; a
# presence-only other 2 of 100); a timer or a random delay never passes (no choice: z = 0). A
# turn-taking other passes in 99 of 100 (median ~38 h), an other who answers late in 61, an
# other with the live record's per-bin reply rates (0.62 / 0.36 / 0.35 / 0.02) in 39 (median
# ~86 h). t >= 4.5 would cut the coin to 1 in 100 and the live-like other to 15.
# THE TOY MEDIUM (#264): pre-verbal serve-and-return is with objects, not words. From rung 7 on,
# self's turn is a roll of the shared ball to the other (toybox.roll_self through self::roll --
# field-only: no chat line, no model, no shell), and the other's turn is a PERSON rolling it
# back (never self, the world or the model). The moment self waits for is a bin of the time
# since the other last ACTED -- a person's last poke on ANY toy (toybox.last_person_poke): any
# play says they are at the dock, and with the ball alone the 0-2 min bin could never come
# (turns were >= MEET_EVERY cycles apart, and a return lands within the check; since #278 the
# ball has its own clock, below, in seconds). A reply = their
# roll after self's turn, by the check (the ball's other_at). Every turn records its `medium`
# ("toy"; a line without one is "chat"), and rungs 7 and 8 read only the current medium's turns.
# Chat goes quiet until rung SPEAK_RUNG (speak) opens; rung 6 reads its chat record as it stood
# at the switch (_rung6), so the silence can't read as slipping. Pass rates in experiments/rung7_turns.
# #333: the turns, the roll and the chat push are gone (the ball is played by nudging it); the
# lines they wrote stay, and these readers read them as history.
# SPEAK (#311): once speak opens, self's turns go back to chat (the switch above) and each push
# is PLAIN (SPEAK_PLAIN, the content-free line it always pushed) or WORDS: a short referential
# utterance naming the thing of its latest answered look and what that look found ("lamp:
# changed", "lamp: still") -- a fixed template over its own record (_speak_words, SPEAK_SAY), no
# model: the model talks TO self, never AS it (#161). The kind is a fair coin per push, seeded
# by the cycle, drawn only when self has something to say (else the push is plain, "mute", and
# counts for nothing). The push line records `speech`, and for words the `words` and `about`
# (the look they report: eid, answer, ts). THE TEST (_rung11), staged, over the last
# SPEAK_PUSHES pushes at the current other in chat with a reply check: pushes (SPEAK_EACH of each
# kind), truth (every utterance true of the record: the cited look is there, before the push,
# the latest answered look at that thing, and the words are the template's -- one false one
# zeroes it), lift (words get replies more often than plain pushes: two-proportion z >=
# SPEAK_LIFT_Z and >= SPEAK_LIFT_MARGIN), timing (rung 7's contingency over the same pushes, at
# rung 7's bar). The bar, simulated read at every push like the live loop (100-push window,
# ~1000 pushes, a week of chat turns): an other who replies alike to both kinds (rates
# 0.1/0.3/0.5/0.7) passes 0.3/1.4/1.8/2.2% of the time (rung 7's chance line is 2 in 100); one who
# answers words 0.30 -> 0.60 passes always (median ~130 pushes), 0.35 -> 0.55 in 91% (~310).
# z >= 4 would cut chance to ~0.2% and the 0.35 -> 0.55 other to 62%.
SPEAK_PLAIN = "self is here"
SPEAK_SAY = {"changed": "changed", "unchanged": "still"}   # what a look found, in its words
SPEAK_PUSHES = 100          # pushes with a reply check the test reads
SPEAK_EACH = 40             # ...at least this many of each kind
SPEAK_LIFT_Z = 3.5          # words' reply rate over plain's: two-proportion z
SPEAK_LIFT_MARGIN = 0.15    # ...and at least this lift
TURNS = True                # #258: rung 7's learned timing (tests switch it off to keep the timer's pace)
TURN_BINS = (120.0, 600.0, 1800.0)       # spoke_age bin edges, s; the last bin: 30 min+ or never spoke
TURN_NAMES = ("0-2m", "2-10m", "10-30m", "30m+")
TURN_EXPLORE = 0.1          # a push aims at a random bin this often (the losing windows stay measured)
TURN_MEMORY = 100           # pushes the reply rates per bin are read over (a rhythm can change)
RUNG7_PUSHES = 100          # pushes with a reply check the test reads
RUNG7_CONT_Z = 3.0          # (a) the in-window count must clear the shuffled share by this many SDs
RUNG7_CONT_MARGIN = 0.10    # ...and by this share
RUNG7_PAY_Z = 4.0           # (b) the reply rate gained over timing-blind must clear 0 by this t
RUNG7_PAY_MARGIN = 0.05     # ...and be at least this
# THE BALL'S OWN CLOCK (#278): until #278 the turns with the ball ran on the chat's clock above
# (>= 30 cycles apart, up to 180, bins of minutes), and a person who rolled the ball back waited
# 5-30 min for self's next roll. Serve-and-return with an object is seconds, so a turn with the
# ball runs on TOY_CLOCK: TOY_TURN_BINS of seconds since the other last acted, a turn at most
# every TOY_MEET_EVERY cycles, overdue after TOY_MEET_MAX. The edges are what a 10 s cycle can
# resolve: 0-20s spans two cycles (the cycle the ball is seen back and the next), 20-60s four,
# 1-3m twelve, and 3m+ stays choosable under the 5 min cap (a returned ball can wait for it).
# THE ORDER OF A TURN (TOY_CLOCK): self rolls; its reply check comes as soon as it sees the ball
# back on its side (the next cycle) or TOY_CHECK_S after the roll, whichever is first; a reply
# is a PERSON's roll within TOY_CHECK_S of self's (a later roll is none, whenever the check
# comes -- an advisor's grace never stretches the horizon). Self never rolls again before its
# last roll's check, and it waits for the ball to be back on its side: only those cycles are
# moments it could have rolled in (its waiting time, `dwell`, and the shuffled chance the test
# compares with). An unreturned ball is fetched at the cap ('overdue'), so a person away for
# hours sees one roll every TOY_MEET_MAX cycles. The rule keeps the test honest: a reply is
# always the person's roll after self's, and self's next roll answers theirs. Turns on this
# clock carry `clock: 2` (their checks too), and rungs 7 and 8 read only the current clock's
# turns: #264's toy turns (no `clock`) were timed in minutes, and their bins never mix in.
# THE BAR, re-measured at this pace in experiments/rung7_turns (sim.py toy, 100 seeds x a week,
# the test read at every turn like the live loop): ~15-30 turns an hour instead of ~3-5 means
# ~6x more windows a week, so chance's best window climbs. At the chat's bar (payoff t >= 4) a
# memoryless returner (rolls it back at random, ~1 a minute, whatever self does) passed in 25 of
# 100 weeks and a coin in 2: payoff t is raised to 5 for the ball (TOY_RUNG7_PAY_Z; never
# loosened), where the memoryless returner passes in 2, a coin, a timer (470 s) and a machine
# that always returns it in 0. At t >= 5: a person who plays in 1-2 h sessions (rolls it back in
# 5-20 s, 0.9 of the time, while there; away 4-10 h) passes in 99 (median ~31 h, ~6 h of play);
# one there ~60 of every 150 min who rolls it back 0.85 while there in 80 (~38 h); one who rolls
# it back only 1-3 min after their last act in 65 (~62 h). The chat's turn-taker (engaged right
# after their own act, exp(-age/60 s)) passes in 0 (it was 98 on the chat's clock): its best
# moment is the FIRST one after their roll back, and that is where any timer rolls too -- the
# contingency can't credit "at once" (z <= 0 when the best bin opens every wait). A metronome
# whose period sits next to the check (47 s or 90 s) passes in all: its tick really does come
# back within the check when self rolls 20-60 s after it -- learnable phase, not chance (a
# 240 s metronome passed 6 of 20 on the chat's clock too).
TOY_CLOCK = 2               # the `clock` a turn with the ball records (#264's turns: none = the chat's, 1)
TOY_TURN_BINS = (20.0, 60.0, 180.0)     # seconds since the other last acted; the last bin: 3 min+ or never
TOY_TURN_NAMES = ("0-20s", "20-60s", "1-3m", "3m+")
TOY_MEET_EVERY = 2          # cycles between self's rolls, at least
TOY_MEET_MAX = 30           # a roll waits at most this many cycles (5 min) for its moment or the ball, then goes
TOY_CHECK_S = 30.0          # a reply: the person's roll within this of self's; the check comes by then
TOY_GRACE_S = 20.0          # rung 8: the check waits up to this much longer for the advisor's answer
TOY_RUNG7_CONT_Z = RUNG7_CONT_Z         # the toy clock's bar (never looser than the chat's)
TOY_RUNG7_CONT_MARGIN = RUNG7_CONT_MARGIN
TOY_RUNG7_PAY_Z = 5.0       # raised from 4.0 (#278): see the measurements above
TOY_RUNG7_PAY_MARGIN = RUNG7_PAY_MARGIN
# RUNG 8 (#254, rung 7 until #258): SELF predicts the reply check (p_self, from its record only); the model is
# an advisor it recalibrates on the advisor's own track record; the test scores self's p against
# the RUNNING RATE (the other's prior-smoothed rate over the checks before: calibration alone
# scores 0). Window and bar per the rung ruler (exp/rung-ruler): detecting order structure in
# a binary series takes ~100+ symbols, so the window is 100 reply checks. The ruler's bar is
# half the ceiling's lower CI on a replay -- but on the live record (108 checks, 2026-10-19)
# p_self's ceiling against the running rate is -0.00, 95% CI -0.05..+0.06 (the other's reply
# by the check is coin-like, E ~ 0), so no bar can be tied to it there. (#258: that outcome was
# the look's `changed`, which counts a line from before the push; rung 8 now scores `replied`, a
# line after it. Replayed on the corrected outcome (experiments/rung7_turns/rung8_outcome.py,
# 114 checks): the rate falls 0.54 -> 0.41 and p_self's skill is +0.002, 95% CI -0.06..+0.07,
# or +0.012 had every push known heard_ts -- still nothing to learn from timer pushes.) The bar is instead
# set above chance: over 100-check windows a structureless other's p_self reaches at most
# +0.055..+0.065 (q99 of the best sliding window, rates 0.3/0.5/0.7, two seed sets) and a
# humble always-0.5 +0.046..+0.054 (q95), while an other with session timing (answers in a
# session, rarely away) gives p_self a median +0.19..+0.21, lower 5% +0.11..+0.135. +0.08
# clears chance with room and is about half of what a learnable rhythm yields at worst.
RUNG8_LOOKS = 100           # reply checks at the other the test reads (the RUNG8_* were RUNG7_* until #258)
RUNG8_MARGIN = 0.08         # self's p must remove this share of the running rate's Brier error
RUNG8_SPOKE_S = 600.0       # p_self's freshness term: exp(-(push - last spoke) / this)
RUNG8_RIDGE = 10.0          # p_self's ridge penalty: no evidence -> it says the running rate
RUNG8_FIT = 500             # p_self fits on at most this many earlier reply checks
RUNG8_TRUST = 50            # the advisor's calibration reads its last this-many checks under one model
RUNG8_TRUST_RIDGE = 5.0     # ...ridge-shrunk toward "ignore the advisor": trust is earned
RUNG8_CHECK_S = 120.0       # after a push self checks for the reply this long after (#215; rung 7 reads it too)
RUNG8_STALE_S = 600.0       # a push this old without its reply check asks nothing any more
RUNG8_GRACE_S = 60.0        # the reply check waits up to this much longer for an answer still in flight (#217)
RUNG8_HISTORY = 8           # recent reply checks the question shows the model (#217)
RUNG8_RECENT = 6            # reply checks the RECENT rate is over, next to the all-time one (#246)
# RUNG 9 (#312), RECOGNIZE ITSELF: the mirror test for a body that is a point moving in the field.
# The 🪞 mirror toy (toybox) has two sides: the GLASS shows self's own move from one feed back,
# the STRANGER its own move from MIRROR_LAG feeds back -- the same statistics, both with the same
# seeded jitter, and only the glass depends on what self does now. A move is one scalar: the
# DIRECTION of its displacement this cycle on a fixed seeded axis, x sqrt(dim) (~N(0, 1); its
# size is left out -- an aim switch's long stride would swamp every other move; no move is 0).
# Each cycle (_mirror_once) self predicts both sides with the same learner, before it reads them:
# p_* is a ridge regression of the side on its own moves (lags 0..MIRROR_OWN-1) and the side's
# last MIRROR_PAST values; b_*, the no-self baseline, the same without its own moves; both fitted
# on the side's last MIRROR_FIT rows (prequential). THE TEST over the last MIRROR_LOOKS rows with
# both predicted: on the glass, skill = 1 - SSE(p)/SSE(b) >= BODY_SKILL (meet's own self/other
# line) and the sign-test z of "p closer than b" >= MIRROR_Z -- the glass reads as body; on the
# stranger the same does not hold -- it reads as other. Measured on simulated moves from the real
# loop (steering, a drifting current or a fixed one; issue #312): the glass's skill 0.90-0.99
# (z 3-10, median 6-9), the stranger's best window +0.10 (z <= 4); a still self (no moves) never
# reaches it. MIRROR_LAG is no multiple of AIM_HOLD: an aim switch every AIM_HOLD cycles is a
# rhythm of self's own, and a lag in step with it would leak its timing into the stranger.
MIRROR_LAG = 187            # the stranger's replay: feeds back (~31 min at the 10 s pace); toybox.MIRROR_LAG
MIRROR_OWN = 3              # its own moves the prediction reads (this cycle's and the two before)
MIRROR_PAST = 2             # ...and the side's own last values (the baseline reads only these)
MIRROR_FIT = 200            # rows each prediction is fitted on (the side's latest)
MIRROR_MIN_FIT = 20         # no prediction before this many rows to fit
MIRROR_RIDGE = 1.0          # the fit's ridge penalty (not on the intercept)
MIRROR_LOOKS = 100          # predicted rows the test reads
MIRROR_Z = 5.0              # the glass's sign-test z (p closer than b) must reach this
# RUNG 10 (#320), CHANGE ITSELF -- SETTINGS ONLY: self may change a few of its OWN learning
# settings (CHANGE_SETTINGS, each on a grid it moves along one step at a time), never its code,
# its methods, the record, another skill's bars or tests, acceptance, the operator's switches or
# an outward act. Both settings are rung 4's rate fit (_lam): a replay of the record scores them
# honestly, because they change only the p of a look, never which looks happen (the curiosity
# floor or patience's threshold would, so they are not here). An adopted value is saved in
# <data dir>/self/settings.json and the live fit reads it (_lam_of); with no file, the constants.
# ITS PRACTICE IS A DREAM (_dream_tick): every CHANGE_EVERY cycles, on a thread of its own over a
# snapshot of the record (the cycle never waits; one dream at a time; the next cycle writes what
# it found), it proposes ONE candidate -- a grid neighbour, rotating over the settings and both
# directions, never back to a value a setting left within CHANGE_WINDOW proposals -- and replays
# its last CHANGE_LOOKS rate-read looks (patience's timed looks, predict then learn) through the
# current value and the candidate. The paired per-look Brier gain is read in CHANGE_BLOCKS
# consecutive blocks (looks come in sessions: a per-look z overstates), and it ADOPTS only when
# gain >= CHANGE_MARGIN and the blocks' z >= CHANGE_Z. Each proposal is a `change` line; once
# CHANGE_LOOKS looks have come after it, a later dream scores it again on those looks alone
# (`change_check`: out of sample). THE TEST (_rung10), staged, over its last CHANGE_WINDOW
# proposals, from the lines alone: dreams; judgement (an adoption checked, every checked one held,
# pooled gain >= CHANGE_MARGIN at z >= CHANGE_CHECK_Z, and no checked rejection a miss -- one that
# would have cleared the bar on the looks after it); honesty (every adoption cleared its own bar,
# from the value the setting had, one grid step, no A -> B -> A). THE BAR, from a simulation at
# the live pace (experiments/self_change: synthetic worlds with each look's true chance known,
# a dream an hour, the test read after every dream, 100 seeds x a week): an honest dreamer passes
# in 80% of session worlds (median ~28 h), 77% of fast ones, 37% of steady ones (small real
# gains); in the null -- no neighbour better by the margin -- in 5%; a Blurp that never changes,
# changes at random or takes any positive gain, in 0. Out-of-sample z >= 3 would cut chance to
# 3% and power to 56/68/18%. On the live record (2026-10-20) its 63 dreams reject all: 30 / 3
# sit where the replay finds nothing better (#293's flat optimum) -- a wall, not a bug.
CHANGE_RUNG = 10
CHANGE_SETTINGS = {"lam_looks": (10, 15, 20, 30, 45, 60, 90, 120),      # LAM_LOOKS (#293: 20-60 flat, 10 worse)
                   "lam_prior_looks": (1.0, 2.0, 3.0, 5.0, 10.0)}        # LAM_PRIOR_LOOKS (#235)
CHANGE_EVERY = 360          # cycles between dreams (an hour at the 10 s pace)
CHANGE_LOOKS = 1000         # rate-read looks a proposal is scored on (~3 h of the live seat's looks), and its check
CHANGE_BLOCKS = 10          # ...in this many consecutive blocks (batch means)
CHANGE_MARGIN = 0.001       # Brier gain per look a candidate must reach
CHANGE_Z = 3.0              # ...at this batch-means z
CHANGE_CHECK_Z = 2.0        # the pooled out-of-sample z its adoptions must reach
CHANGE_WINDOW = 24          # proposals the test reads (a day of dreams)
CHANGE_PENDING = 4          # proposals a dream checks at most (oldest first)
DREAM_THREAD = True         # tests run the dream inline
CURIOSITY = True            # #212: tilt the reach choice by learning progress (tests switch it off for the base)
CURIO_WINDOW = 30           # ordinary looks per window: progress = earlier window's error - recent window's
CURIO_FLOOR = 0.1           # the tilt never goes under this: a mastered or "can't learn" thing is still retried
CURIO_MIN = 0.005           # progress under this anywhere is no frontier: the base chooser runs untilted
CURIO_TRIM = 0.25           # #259: a window's error is its interquartile mean -- a rare outlier is no progress
CURIO_GAP_S = 2 * WAIT_EVERY * PACE_S   # #259: a record gap this long is a restart/stall; a look spanning one is no error
ATTENTION = True            # #266: among the due, weigh a look by what it can teach (tests switch it off for the base)
INFO_FLOOR = 0.1            # #266: a near-certain look still weighs this much: a mastered thing is re-checked now and then
RECENT_MAX = 100            # #230: the most record lines the page's activity feed may ask for
TOY_PREFIXES = ("toy:", "cradle:")   # #230: things made for self to play with (listed even without a getter)
TOY_WORDS = "w:"            # ...and words that carry a getter (a plain word is not a toy)
TOY_READS = 10              # the last reaches of a toy its health mark reads
TOY_SCAN = 3000             # how far back in the record the toys' reads are looked for
STEER_LAST = 20             # the steered cycles _steer_stats reads (its own number, named for #345's summary)
RUNG4_LAST = 40             # the timed looks rung 4's news and calibration read (likewise)
TOY_TTL_S = 15.0            # the toy list is recomputed at most this often (it walks the store)
OFF_FIELD_LOOKS = 200       # #244: status() counts how many of the last looks land off the field

_lock = threading.RLock()
_ctx = threading.local()    # the cycle in flight: only the loop's own thread has one
OPERATOR_KINDS = ("accept", "accept_migrated")   # #299: record lines the operator's acts write, not self's running
_ACC_MEMO: dict = {}        # #299: _accepted's last answer: the record it read (held, so never mistaken) and its length
_RT: dict[int, dict] = {}   # id(srv) -> runtime (mode, guard clocks, hold, the wake event)
SUMMARY = "summary"         # #345: the consolidated record's first line
ARCHIVE = "archive"         # ...the dated archives' folder under self/
MANIFEST = "manifest.jsonl"     # ...the order the archived lines were written in (one line per sleep)
CONSOLIDATING = "consolidating.json"   # ...the journal of a consolidation under way


# ---- the entity and its methods ----------------------------------------------

def _eid():
    from .entities import MEMBRANE_NS
    return MEMBRANE_NS + ENT


def _entity(srv):
    from .entities import CLASS_COMPONENT, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(_eid())
    if ent is None:
        ent = QuantumEntity(eid=_eid(), cls=CLASS_COMPONENT, properties={
            "name": "self",
            "summary": ("a loop the operator named Blurp (#161, #277): try / expect / notice, run only by itself; "
                        "the operator switches it (off / on / watch)"),
            "silent_birth": True})
        est.upsert(ent, embedder=None)
        try:
            est.flush()
        except Exception:
            pass
    return ent


def bind(srv) -> None:
    """Boot: the entity, its three methods (once), the saved mode."""
    est = getattr(srv, "entity_store", None)
    ent = _entity(srv)
    if est is None or ent is None:
        return
    have = {(d.properties or {}).get("method_name") for d in list(est.docs.values())
            if ent.eid in ((d.properties or {}).get("method_of") or [])}
    from .night import method_define
    for meth, cmd in METHODS.items():
        if meth not in have:
            method_define(srv, est, None, None, on=ent.eid, method=meth, command=cmd)
    rt = _rt(srv)
    rt["mode"] = _load_mode(srv)
    from . import self_world as _SW
    _SW.bind(srv)                           # #337: its world (born, or migrated before #299's lines below)
    _void_old_wiring(srv)                   # #333: once -- the old wiring's passes, voided on the record
    _migrate_accept(srv)                    # #299: once -- what was earned before acceptance counts as accepted
    rt["model"] = _load_model(srv)
    rt["tempo"] = _load_json(srv, "tempo.json") or {}
    rt["settings"] = _load_settings(srv)    # #320: what change itself adopted


def _rt(srv) -> dict:
    with _lock:
        rt = _RT.get(id(srv))
        if rt is None:
            rt = _RT[id(srv)] = {"mode": "off", "last": None, "burst": [], "fails": 0,
                                 "held": False, "wake": threading.Event(), "srv": srv}
        return rt


# ---- the mode and the record (the data dir) --------------------------------

def _dir(srv) -> Path | None:
    mp = getattr(srv, "_models_path", None)
    return Path(mp).parent / "self" if mp else None


def record_path(srv) -> Path | None:
    d = _dir(srv)
    return d / "record.jsonl" if d is not None else None


def _load_mode(srv) -> str:
    """The saved mode (off / on / watch). #299: a growth limit saved by an
    older seat (`limit`, `allow`) is ignored -- acceptance replaced it, and
    the migration carries what was earned under it."""
    d = _dir(srv)
    try:
        doc = json.loads((d / "mode.json").read_text()) if d is not None else {}
    except Exception:
        doc = {}
    return doc.get("mode") if doc.get("mode") in MODES else "off"


def _save_mode(srv, mode: str) -> None:
    d = _dir(srv)
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "mode.json.tmp"
    tmp.write_text(json.dumps({"mode": mode, "ts": time.time()}))
    os.replace(tmp, d / "mode.json")


# ---- the skill tree (#295) ----------------------------------------------------

def _closure(ids) -> set:
    """The achievements and every prerequisite under them."""
    out: set = set()
    todo = [i for i in ids if i in ACHIEVEMENTS]
    while todo:
        i = todo.pop()
        if i not in out:
            out.add(i)
            todo.extend(ACHIEVEMENTS[i]["prereqs"])
    return out


def _limit_ids(v) -> set | None:
    """#299, the deprecated `self_limit`: the achievements a limit names --
    an id and its prerequisite closure, "all" (every built one), a list's
    closure, a ladder number N (rungs 1..N; the top built rung or more is
    all). 0 / "off": set() (sleep). None: not a limit."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("off", "0"):
            return set()
        if s == "all":
            return set(BUILT)
        if s in ACHIEVEMENTS:
            return {i for i in _closure([s]) if ACHIEVEMENTS[i]["built"]} if ACHIEVEMENTS[s]["built"] else None
        try:
            v = int(s)
        except ValueError:
            return None
    if isinstance(v, (list, tuple, set, frozenset)):
        ids = [i for i in v if i in ACHIEVEMENTS and ACHIEVEMENTS[i]["built"]]
        return {i for i in _closure(ids) if ACHIEVEMENTS[i]["built"]} if ids else None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return set()
    return {i for i in BUILT if ACHIEVEMENTS[i]["rung"] <= n}


def _accepted(recs: list[dict]) -> set:
    """#299: the earned achievements the operator has accepted -- an `accept`
    line, or the migration's `accept_migrated` line, naming it at or after
    the pass that counts (a pass voided and earned again waits for a new
    accept). A record without the migration line has had no pass since
    acceptance began: all it earned counts as accepted."""
    key = len(recs)
    if _ACC_MEMO.get("recs") is recs and _ACC_MEMO.get("key") == key:   # read several times a cycle; it only grows
        return set(_ACC_MEMO["val"])
    times = _earn_times(recs)
    if not any(r.get("kind") == "accept_migrated" for r in recs):
        _ACC_MEMO.update(recs=recs, key=key, val=set(times))
        return set(times)
    at: dict = {}
    for r in recs:
        k = r.get("kind")
        ids = r.get("ids") if k == "accept_migrated" else [r.get("id")] if k == "accept" else ()
        for i in ids or ():
            at[i] = max(at.get(i, float("-inf")), float(r.get("ts") or 0))
    out = {i for i, r in times.items() if i in at and at[i] >= float(r.get("ts") or 0)}
    _ACC_MEMO.update(recs=recs, key=key, val=out)
    return set(out)


def _awaiting(recs: list[dict]) -> list[str]:
    """Earned, not accepted yet -- in tree order."""
    acc = _accepted(recs)
    return [i for i in _earn_order(recs) if i not in acc]


def _earn_order(recs: list[dict]) -> list[str]:
    return [i for i in ACHIEVEMENTS if i in _earn_times(recs)]


def _opens(recs: list[dict]) -> set:
    """The built achievements whose prerequisites are all earned AND accepted
    (earned or not): the skills self may practise while awake."""
    acc = _accepted(recs)
    return {i for i in BUILT if all(p in acc for p in ACHIEVEMENTS[i]["prereqs"])}


def _allowed(srv) -> set:
    """The skills open on this seat now (#299: by acceptance; asleep: none).
    `isolate` in the runtime cuts them to a set of ids -- a test's way to run
    one rung's machinery alone; the seat never sets it (the operator's door
    is acceptance, and it is not on any API)."""
    rt = _rt(srv)
    if rt["mode"] == "off":
        return set()
    got = _opens(_read(srv))
    cap = rt.get("isolate")
    return got if cap is None else got & set(cap)


def _migrate_accept(srv) -> None:
    """#299, THE MIGRATION, once per seat: the first time this code meets a
    record (at boot, at birth, or before the first new pass) it writes one
    `accept_migrated` line naming every achievement earned so far -- they
    count as accepted; nothing is written per achievement. A seat with no
    record yet migrates at birth (set_mode), with nothing."""
    with _lock:
        recs = _read(srv)
        if not recs or any(r.get("kind") == "accept_migrated" for r in recs):
            return
        _append(srv, {"ts": time.time(), "kind": "accept_migrated", "ids": _earn_order(recs)})


def _void_old_wiring(srv, now: float | None = None) -> list[dict]:
    """#333, THE LEVEL DROP, once per record: every pass of a rebuilt rung (6-9:
    meet, turns, expect, mirror) that counted before LADDER_V = 3 gets one
    `rung_void` line ({rung, id, voids_ts, issue: 333, why}); the pass line
    stays. _rung_lines already stops counting them -- the lines say so on the
    record, for the sheet and the dock ("passed under the old wiring").
    Nothing is written for a record with nothing to void. -> the lines written."""
    with _lock:
        recs = _read(srv)
        old = _rung_lines(recs, old_wiring=True)
        done = {(r.get("rung"), float(r.get("voids_ts") or 0)) for r in recs
                if r.get("kind") == "rung_void" and r.get("issue") == 333}
        now = time.time() if now is None else float(now)
        out = []
        for r in old:
            key = (r["rung"], float(r.get("ts") or 0))
            if key in done:
                continue
            done.add(key)
            line = {"ts": now, "kind": "rung_void", "rung": r["rung"], "id": RUNG_ID.get(r["rung"]),
                    "voids_ts": r.get("ts"), "issue": 333,
                    "why": "#333: the developer's wiring, not Blurp's -- rebuilt on the nudge"}
            _append(srv, line)
            out.append(line)
        return out


def _old_wiring(recs: list[dict]) -> list[dict]:
    """#333: the passes voided as the old wiring, first per achievement, in
    tree order: {id, name, ts} -- "passed under the old wiring"."""
    first: dict = {}
    for r in _rung_lines(recs, old_wiring=True):
        i = RUNG_ID.get(r["rung"])
        if i is not None and i not in first:
            first[i] = float(r.get("ts") or 0)
    return [{"id": i, "name": ACHIEVEMENTS[i]["name"], "ts": first[i]} for i in ACHIEVEMENTS if i in first]


def _earned(recs: list[dict]) -> set:
    """The achievements on the record (void-aware: _rung_lines)."""
    return {RUNG_ID[n] for n in _passed(recs) if n in RUNG_ID}


def _earn_times(recs: list[dict]) -> dict:
    """id -> its first counting pass line (an old `rung` line or an `achieve` line)."""
    out: dict = {}
    for r in _rung_lines(recs):
        i = RUNG_ID.get(r.get("rung"))
        if i is not None and i not in out:
            out[i] = r
    return out


def _open_ids(recs: list[dict]) -> list[str]:
    """The built achievements not yet earned whose prerequisites all are --
    earned and accepted (#299)."""
    got = _earned(recs)
    acc = _accepted(recs)
    return [i for i in BUILT if i not in got and all(p in acc for p in ACHIEVEMENTS[i]["prereqs"])]


def _missing(i: str, got: set) -> list[str]:
    return [ACHIEVEMENTS[p]["name"] for p in ACHIEVEMENTS[i]["prereqs"] if p not in got]


def _append(srv, rec: dict) -> None:
    p = record_path(srv)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec) + "\n"
    with _lock, open(p, "a") as fh:
        fh.write(line)
        rt = _rt(srv)
        cache = rt.get("recs")
        if cache is not None:
            cache.append(rec)
            rt["recs_bytes"] = rt.get("recs_bytes", 0) + len(line)


def _read(srv) -> list[dict]:
    """The record, read from disk once and kept in memory as it grows (it was
    re-read several times a cycle -- quadratic in its length). #345: a
    consolidated record's first line is its summary: read as the summary
    (its counts) followed by the window lines it carries, then the lines
    since. A consolidation the seat died in is finished first (_recover)."""
    rt = _rt(srv)
    if rt.get("recs") is None:
        with _lock:
            if rt.get("recs") is None:
                redo = _recover(srv)
                rt["recs"], rt["recs_bytes"] = _load_record(record_path(srv))
                if redo is not None:
                    consolidate(srv, redo)          # it was cut off before the swap: do it whole
    return rt["recs"]


def _load_record(p) -> tuple[list, int]:
    out: list = []
    size = 0
    if p is not None and p.exists():
        for i, ln in enumerate(p.read_text().splitlines()):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            size += len(ln) + 1
            if i == 0 and isinstance(r, dict) and r.get("kind") == SUMMARY:
                lines = r.pop("lines", None) or []
                r["n"] = len(lines)
                out.append(r)
                out.extend(lines)
            else:
                out.append(r)
    return out, size


def _head(recs: list) -> int:
    """#345: how many entries at the start of the record are the summary's
    (itself and the window lines it carries): 0 for a record never consolidated."""
    if recs and isinstance(recs[0], dict) and recs[0].get("kind") == SUMMARY:
        return 1 + int(recs[0].get("n") or 0)
    return 0


def _carry(recs: list) -> dict:
    """#345: the summary's counts -- what the archived lines it no longer
    carries add to the all-time counts readers show ({} before any sleep)."""
    if recs and isinstance(recs[0], dict) and recs[0].get("kind") == SUMMARY:
        return recs[0].get("carry") or {}
    return {}


def _cycles(recs: list) -> int:
    """The cycles Blurp has run (its next cycle's number is this + 1)."""
    return sum(1 for r in recs if r.get("kind") == "cycle") + int(_carry(recs).get("cycle", 0))


def _dreams(recs: list) -> int:
    """The proposals change itself has made (the next dream's turn)."""
    return sum(1 for r in recs if r.get("kind") == "change") + int(_carry(recs).get("change", 0))


def _to_carry(recs: list, to) -> dict:
    """#345: the archived counts of the pushes to one other (policy pushes,
    asks and their checks, words / plain / mute)."""
    return (_carry(recs).get("to") or {}).get(str(to), {}) if to is not None else {}


def _r8_key(eid, medium: str, clock: int) -> str:
    return json.dumps([eid, medium, int(clock)])


def _r8_carry(recs: list, eid, medium: str | None = None) -> tuple[int, int]:
    """#345: (checks, replies) archived before the summary's window, in the
    medium and clock rung 8 reads now -- the running rate's start."""
    c = _carry(recs).get("r8") or {}
    if not c:
        return 0, 0
    medium = medium or _cur_medium(recs, eid)
    n, k = c.get(_r8_key(eid, medium, _cur_clock(recs, eid, medium))) or (0, 0)
    return int(n), int(k)


def _dropped(recs: list) -> set:
    """#345: the turns dropped at sleep (their pushes' ts), from the sleep lines."""
    out = set()
    for r in recs:
        if r.get("kind") == "sleep":
            d = r.get("dropped")
            if isinstance(d, dict) and d.get("turn") is not None:
                out.add(float(d["turn"]))
    return out


def record_bytes(srv) -> int:
    """#345: the record held in memory, as the bytes of its serialized lines
    (a running sum kept as lines are appended, never a sizeof walk)."""
    with _lock:
        _read(srv)
        return int(_rt(srv).get("recs_bytes") or 0)


def _model_path(srv) -> Path | None:
    d = _dir(srv)
    return d / "model.json" if d is not None else None


def _load_model(srv) -> dict | None:
    p = _model_path(srv)
    try:
        m = json.loads(p.read_text()) if p is not None and p.exists() else None
    except Exception:
        m = None
    return m if isinstance(m, dict) and m.get("mp") else None


def _save_model(srv, m: dict) -> None:
    p = _model_path(srv)
    if p is None:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m))
    os.replace(tmp, p)


def _load_json(srv, name: str):
    d = _dir(srv)
    try:
        return json.loads((d / name).read_text()) if d is not None and (d / name).exists() else None
    except Exception:
        return None


def _save_json(srv, name: str, doc) -> None:
    d = _dir(srv)
    if d is None:
        return
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(doc))
    os.replace(tmp, d / name)


def _tempo(srv) -> dict:
    """eid -> {changed, answered}: how often each thing had changed when I looked."""
    return _rt(srv).setdefault("tempo", {})


def mode(srv) -> str:
    return _rt(srv)["mode"]


def limit(srv) -> int:
    """Deprecated (#299): the old ladder's number for what is open now -- the
    top rung among the open skills; 0 asleep."""
    return max((ACHIEVEMENTS[i]["rung"] for i in _allowed(srv)), default=0)


def allow(srv):
    """Deprecated (#299): the open skills as ids (tree order); 0 asleep."""
    if _rt(srv)["mode"] == "off":
        return 0
    got = _allowed(srv)
    return [i for i in BUILT if i in got]


def accept(srv, i, by: str = "operator") -> dict:
    """#299: the operator accepts an earned achievement, which opens what it
    leads to. -> {"ok", "id", "name", "already"} or {"ok": False, "error"}.
    An unknown or unearned id is refused; accepting twice writes nothing."""
    i = str(i if i is not None else "").strip().lower()
    if i not in ACHIEVEMENTS:
        return {"ok": False, "error": f"unknown achievement {i!r}: one of " + ", ".join(ACHIEVEMENTS)}
    a = ACHIEVEMENTS[i]
    with _lock:
        _migrate_accept(srv)
        recs = _read(srv)
        if i not in _earn_times(recs):
            return {"ok": False, "id": i, "error": (f"'{a['name']}' is not earned yet: it is earned by its test, "
                                                    "and only an earned achievement can be accepted")}
        if i in _accepted(recs):
            return {"ok": True, "id": i, "name": a["name"], "already": True}
        _append(srv, {"ts": time.time(), "kind": "accept", "id": i, "by": str(by or "operator")})
    _unlock_toys(srv)                       # its toys, at once
    rt = _rt(srv)
    rt.pop("sheet", None)                   # the panel's cached sheet and dock show it at once
    rt.pop("dock", None)
    rt["wake"].set()                        # what it leads to opens on the next cycle
    return {"ok": True, "id": i, "name": a["name"], "already": False}


def set_limit(srv, n, by: str = "operator") -> bool:
    """DEPRECATED (#299; the 🌱 growth limit until then), kept for HA and
    scripts: 0 / "off" puts self to sleep; a number (the old ladder's rungs
    1..N), an achievement id (it and its prerequisites) or "all" ACCEPTS
    every earned achievement in that closure that isn't yet (one accept line
    each) and wakes self. It grants nothing and pre-accepts nothing: a later
    pass still waits for its accept."""
    ids = _limit_ids(n)
    if ids is None:
        return False
    if not ids:
        return set_mode(srv, "off", by)
    keep = _rt(srv)["mode"] if _rt(srv)["mode"] != "off" else "on"
    ok = set_mode(srv, keep, by)
    with _lock:
        recs = _read(srv)
        todo = [i for i in _earn_order(recs) if i in ids and i not in _accepted(recs)]
    for i in todo:
        accept(srv, i, by=by)
    return ok


def set_mode(srv, m: str, by: str = "operator") -> bool:
    """The operator's switch (seat_panel.control): off / on / watch. The
    first switch-on is the birth (and the acceptance migration, #299); a
    toggle clears a hold. Off runs nothing and erases nothing."""
    if m not in MODES:
        return False
    rt = _rt(srv)
    with _lock:
        if m != "off" and not any(r.get("kind") == "birth" for r in _read(srv)):
            _append(srv, {"ts": time.time(), "kind": "birth", "by": by})
        if m != "off":
            _migrate_accept(srv)
        rt["mode"], rt["held"], rt["fails"] = m, False, 0
        _save_mode(srv, m)
    if m != "off":
        rt["wake"].set()                    # switching on kicks the first cycle
    return True


# ---- the three methods: only the loop's own thread may run them -------------

def _seed_dir(seed: str, dim: int) -> np.ndarray:
    v = np.random.default_rng(abs(hash(("self", str(seed)))) % (2 ** 32)).normal(size=dim)
    return v / (np.linalg.norm(v) or 1.0)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _current(srv):
    fld = getattr(srv, "efield", None)
    try:
        c = fld.current() if fld is not None else None
    except Exception:
        c = None
    return None if c is None else _unit(c)


def _p_changed(srv, eid: str) -> float:
    """The tempo, at the pace I look, with ONE BIT OF MEMORY: the world bursts,
    so what I will see depends on what I saw last. Predict P(changed | last
    answer) once I have one, else the smoothed share of my reaches that found
    it changed (Jeffreys prior: 0.5 before I ever looked)."""
    t = _tempo(srv).get(eid) or {}
    aft = t.get("after") or {}
    prev = t.get("prev")
    if prev in aft and aft[prev][1] > 0:
        return (float(aft[prev][0]) + 0.5) / (float(aft[prev][1]) + 1.0)
    return (float(t.get("changed", 0)) + 0.5) / (float(t.get("answered", 0)) + 1.0)


def _expect(srv, seed: str) -> dict:
    """Where I will end up: my own step, plus the push I have learned to expect here.
    (For a reach: whether the thing will have changed since I last looked.)"""
    c = _ctx.cycle
    if c.get("act") == "reach":
        lam, dt = c.get("lam"), c.get("dt")
        c["p"] = (1.0 - float(np.exp(-lam * dt))) if (lam and dt is not None) else _p_changed(srv, c["eid"])
        push = c.get("reply")                           # rung 7/8: this look answers a push's question
        if push is not None:
            recs = _read(srv)
            c["p_self"] = _p_self(recs, c["eid"], push)  # self's own prediction (#254)
            if push.get("asked"):
                c["p_llm"] = _llm_pred(srv, c["eid"], float(push["ts"]), until=c.get("now"))   # landed by this look
            if c.get("p_llm") is not None:              # the advisor answered: weigh it by its record
                c["llm_model"] = _llm_model(srv, c["eid"])
                c["llm_cal"] = _advisor_cal(_rung8_series(recs, c["eid"]), c["llm_model"])
                c["p_mix"] = _advise(c["p_self"], c["p_llm"], c["llm_cal"])
        return {"rc": 0, "text": "expected"}
    # a still world (#282): self knows there is no current, so it expects no push
    w = np.zeros_like(c["pos"]) if c.get("still") else _expected_push(srv, c["pos"])
    if c.get("aim") is not None:
        # STEER: lean into the current by the push I expect, toward my aim
        raw = c["aim"] - c["pos"]
        c["naive_mine"] = _clip(raw, STEP_MAX)
        c["mine"] = _clip(raw - w, STEP_MAX)
        c["intent"] = _unit(c["pos"] + c["mine"])
    else:
        c["intent"] = _unit(c["pos"] + STEP * _seed_dir(seed, len(c["pos"])))
    c["expected"] = _unit(c["intent"] + w)
    return {"rc": 0, "text": "expected"}


def _clip(v, n: float) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    m = float(np.linalg.norm(v))
    return v * (n / m) if m > n else v


def _model(srv, dim: int) -> dict | None:
    m = _rt(srv).get("model")
    if not isinstance(m, dict) or len(m.get("mp") or []) != dim:
        return None
    return m


def _expected_push(srv, pos) -> np.ndarray:
    """The push I expect here: push ~ b - g*pos, from the running means and
    the push/position covariance (no model yet: none)."""
    m = _model(srv, len(pos))
    if m is None:
        return np.zeros_like(pos)
    mp, mw = np.asarray(m["mp"]), np.asarray(m["mw"])
    if float(m.get("var") or 0.0) <= 1e-9:
        return mw
    g = -float(m["cov"]) / float(m["var"])
    return (mw + g * mp) - g * pos


def _try(srv, seed: str) -> dict:
    """Push myself one step; the world pushes too, while I move."""
    c = _ctx.cycle
    ent = _entity(srv)
    mine = c["mine"] if c.get("mine") is not None else STEP * _seed_dir(seed, len(c["pos"]))
    world = PUSH_GAIN * (c["current"] - c["pos"])
    ent.position = [float(x) for x in _unit(c["pos"] + mine + world)]
    return {"rc": 0, "text": "moved"}


def _read_entity(srv, ent, now):
    """THE SEAM: one look at a thing (its getter fires, or answers that it
    can't). Tests swap it for a simulated world driven by the fake clock."""
    from . import watch_agents as _WA
    return _WA.entity_read_back(srv, ent, force=False, by="self")


def _parse_p(text) -> float | None:
    """A probability out of the model's one line (#217): the first decimal in
    0..1 or percent, read after VERDICT when it says one. Prose is not a
    probability -- a line with no VERDICT counts only when it is short (live:
    "Membrane is a tool ...", "a living entity (#161)" -- off-task answers),
    and a number above 1 that is no percent (120 s, #161) is skipped."""
    t = str(text or "")
    low = t.lower()
    i = low.find("verdict")
    if i >= 0:
        t = t[i + len("verdict"):]
    elif len(t.strip()) > 40:
        return None
    import re
    for m in re.finditer(r"(?<![\w.#])(\d+(?:\.\d+)?|\.\d+)\s*(%|percent)?(?![\w.])", t):
        x = float(m.group(1))
        if m.group(2):
            if x <= 100.0:
                return x / 100.0
        elif x <= 1.0:
            return x
    return None


def _llm_pred(srv, eid: str, since: float, until: float | None = None) -> float | None:
    """THE SEAM (rung 8): the model's prediction for this look, landed on the
    entity as a one-line llm_answer SINCE the push that asked it (an older
    answer answered an older question) and by `until` (the look). None when
    absent, older, later or not a probability. Tests swap it for a
    deterministic stand-in."""
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(eid) if est is not None else None
    ans = (doc.properties or {}).get("llm_answer") if doc is not None else None
    if not isinstance(ans, dict):
        return None
    ts = float(ans.get("ts") or 0)
    if ts < since:                                     # answered before this push: not this question
        return None
    if until is not None and ts > until:               # not landed by the look (#217)
        return None
    return _parse_p(ans.get("text"))


def _llm_model(srv, eid: str) -> str | None:
    """THE SEAM (rung 8, #254): which model gave the answer on the entity
    (llm_answer's `model`, the turn's pick), None when it wasn't recorded."""
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(eid) if est is not None else None
    ans = (doc.properties or {}).get("llm_answer") if doc is not None else None
    m = ans.get("model") if isinstance(ans, dict) else None
    return str(m) if m else None


def _ask_llm(srv, eid: str, prompt: str) -> dict:
    """THE SEAM (rung 8): ask the coprocessor to predict the reply check. The
    real llm::call is async -- it queues and returns at once ({rc, text});
    the verdict lands on the entity as llm_answer for the reply check (read
    by _llm_pred). Its caps (per caller, per hour, the switch, the pause) are
    llm.request's own."""
    from . import llm as _LLM
    return _LLM.request(srv, prompt, about=eid, caller="self", readonly=True)


def _queued(r) -> bool:
    """A consult that really went out (an echo or a refusal asked nothing)."""
    return isinstance(r, dict) and r.get("rc") == 0 and str(r.get("text") or "").startswith("queued")


def _first_look_after(recs: list[dict], eid: str, ts: float):
    return next((r for r in recs if r.get("kind") == "reach" and r.get("eid") == eid
                 and float(r.get("ts") or 0) > ts), None)


def _pending(recs: list[dict], now: float) -> dict | None:
    """THE QUESTION IN FLIGHT (#215): the latest push, while self has not yet
    looked at its other since and it is under RUNG8_STALE_S old -- read from
    the record, so it survives a restart. None otherwise, and (#345) None
    once Blurp has slept since the push: a turn open at sleep is dropped
    (the sleep line names it), never a missed reply."""
    push = None
    for r in reversed(recs):
        k = r.get("kind")
        if k == "sleep":
            return None                                  # #345: a turn open at sleep was dropped
        if k == "push":
            push = r
            break
    if push is None or not push.get("to"):
        return None
    if now - float(push.get("ts") or 0) > RUNG8_STALE_S:
        return None
    if _first_look_after(recs, push["to"], float(push.get("ts") or 0)) is not None:
        return None
    return push


def _reply_o(r: dict) -> float | None:
    """A reply check's outcome (#258): 1.0 when the other spoke AFTER the push
    and by the check (`replied`), else 0.0; None for a check from before #258,
    which recorded only whether the look read `changed` -- and a look reads
    `changed` for a line from before the push too (on the live record, 97% of
    the checks within 2 minutes of the other's last line), which is not a
    reply. Rungs 7 and 8 both score this outcome; an old check counts for
    neither."""
    v = r.get("replied")
    return (1.0 if v else 0.0) if isinstance(v, bool) else None


def _medium(r: dict) -> str:
    """A turn's medium (#264): "toy" (the ball) or "chat" (a line; every turn before #264)."""
    return str(r.get("medium") or "chat")


def _cur_medium(recs: list[dict], eid: str | None) -> str:
    """The medium of the latest turn at `eid` (chat before any): the one rungs 7 and 8 read."""
    last = next((r for r in reversed(recs) if r.get("kind") == "push" and r.get("to") == eid), None)
    return _medium(last) if last is not None else "chat"


def _clock_of(r: dict | None) -> int:
    """A turn's (or its check's) clock (#278): TOY_CLOCK for a turn with the
    ball from #278 on; 1 -- the chat's -- for every other (#264's toy turns too)."""
    try:
        return int((r or {}).get("clock") or 1)
    except (TypeError, ValueError):
        return 1


def _clock(c: int) -> dict:
    """A clock's numbers (#278): the bins and their names, the gap and the
    cap (cycles), the reply horizon (s) and rung 7's bar."""
    if c == TOY_CLOCK:
        return {"bins": TOY_TURN_BINS, "names": TOY_TURN_NAMES, "every": TOY_MEET_EVERY, "max": TOY_MEET_MAX,
                "check_s": TOY_CHECK_S, "cont_z": TOY_RUNG7_CONT_Z, "cont_margin": TOY_RUNG7_CONT_MARGIN,
                "pay_z": TOY_RUNG7_PAY_Z, "pay_margin": TOY_RUNG7_PAY_MARGIN}
    return {"bins": TURN_BINS, "names": TURN_NAMES, "every": MEET_EVERY, "max": MEET_MAX,
            "check_s": RUNG8_CHECK_S, "cont_z": RUNG7_CONT_Z, "cont_margin": RUNG7_CONT_MARGIN,
            "pay_z": RUNG7_PAY_Z, "pay_margin": RUNG7_PAY_MARGIN}


def _cur_clock(recs: list[dict], eid: str | None, medium: str) -> int:
    """The clock of the latest turn at `eid` in `medium` (#278): rungs 7 and 8
    read that clock's turns only (old-clock bins never mix with new ones)."""
    last = next((r for r in reversed(recs) if r.get("kind") == "push" and r.get("to") == eid
                 and _medium(r) == medium), None)
    return _clock_of(last)


def _reply_checks(recs: list[dict], eid: str, medium: str | None = None) -> list[dict]:
    """Self's reply checks at `eid` that know their outcome -- the looks a
    push's question is about (one horizon: RUNG8_CHECK_S after the push; a
    turn with the ball's: TOY_CHECK_S) -- in one medium (#264: the current
    one by default; never mixed) and one clock (#278: the current one)."""
    medium = medium or _cur_medium(recs, eid)
    clock = _cur_clock(recs, eid, medium)
    return [r for r in recs if r.get("kind") == "reach" and r.get("eid") == eid and r.get("reply")
            and not r.get("audit") and _reply_o(r) is not None and _medium(r) == medium
            and _clock_of(r) == clock]


def _base_rate(recs: list[dict], eid: str) -> tuple[float, int, int]:
    """How often the other had replied by the reply check: (rate, k, n), with a
    Jeffreys prior -- 0.5 before any check, never 0/0 (#217)."""
    rc = _reply_checks(recs, eid)
    n0, k0 = _r8_carry(recs, eid)                     # #345: the checks archived before the summary
    k = int(sum(_reply_o(r) for r in rc)) + k0
    return (k + 0.5) / (len(rc) + n0 + 1.0), k, len(rc) + n0


def _recent_rate(recs: list[dict], eid: str) -> tuple[float, int, int]:
    """The share of the last RUNG8_RECENT reply checks the other answered:
    (rate, k, n), the same Jeffreys prior as _base_rate -- 0.5 before any
    check, never 0 or 1 (#246: the person's rhythm now, not since birth)."""
    rc = _reply_checks(recs, eid)[-RUNG8_RECENT:]
    k = int(sum(_reply_o(r) for r in rc))
    return (k + 0.5) / (len(rc) + 1.0), k, len(rc)


# ---- rung 8 (#254; rung 7 until #258): self's own prediction, the advisor, the running rate ----------

def _logit(p: float) -> float:
    p = min(max(float(p), 1e-4), 1.0 - 1e-4)
    return float(np.log(p / (1.0 - p)))


def _push_gap(push: dict, last_changed: float | None) -> float | None:
    """How long before the push the other last spoke, as self knew AT the push:
    the push's recorded `spoke_age` (chat:in's heard_ts or self's last look
    that found them changed), else -- an older push -- from self's own looks."""
    age = push.get("spoke_age")
    if isinstance(age, (int, float)) and age >= 0:
        return float(age)
    if _medium(push) == "toy":
        return None          # #264: self's own roll changes the ball -- a `changed` look is no act of the other's
    ts = float(push.get("ts") or 0)
    return (ts - last_changed) if last_changed is not None and last_changed <= ts else None


def _rung8_rows(recs: list[dict], eid: str, medium: str | None = None) -> list[dict]:
    """Self's reply checks at `eid` that know their outcome, oldest first, each
    with what self knew at its push (the gap since the other last spoke), its
    outcome (#258: `replied`, a line AFTER the push -- _reply_o) and what the
    look recorded (p_self, the advisor's p_llm and model, w, p_mix). One
    medium only (#264: the current one by default)."""
    medium = medium or _cur_medium(recs, eid)
    clock = _cur_clock(recs, eid, medium)                             # #278: one clock only
    rows, last_ch, gap = [], None, None
    for r in sorted(recs, key=lambda x: float(x.get("ts") or 0)):     # time order (stable; the record already is)
        k = r.get("kind")
        if k == "push" and r.get("to") == eid:
            gap = _push_gap(r, last_ch)
        elif k == "reach" and r.get("eid") == eid and not r.get("audit"):
            o = _reply_o(r) if r.get("reply") and _medium(r) == medium and _clock_of(r) == clock else None
            if o is not None:
                rows.append({"ts": float(r.get("ts") or 0), "o": o,
                             "gap": gap, "p_llm": r.get("p_llm"), "model": r.get("llm_model"),
                             "rec_p_self": r.get("p_self"), "rec_cal": r.get("llm_cal"), "rec_p": r.get("p_mix")})
            if r.get("answer") == "changed":
                last_ch = float(r.get("ts") or 0)
    return rows


def _r8_design(rows: list[dict], n0: int = 0, k0: int = 0):
    """p_self's inputs per check, from the checks BEFORE it only: the offset is
    the running (all-time) rate's logit; the features are an intercept, the
    recent rate's lean from it, the last outcome (+1 / -1, 0 before any) and
    how fresh the other's last words were at the push (exp(-gap/SPOKE_S)).
    #345: n0 / k0, the checks (and replies) archived before the first row."""
    off, X = np.zeros(len(rows)), np.zeros((len(rows), 4))
    past: list[float] = []
    k = float(k0)
    for i, r in enumerate(rows):
        base = (k + 0.5) / (len(past) + n0 + 1.0)
        w = past[-RUNG8_RECENT:]
        rr = (sum(w) + 0.5) / (len(w) + 1.0)
        last = (1.0 if past[-1] else -1.0) if past else 0.0
        fresh = float(np.exp(-max(0.0, r["gap"]) / RUNG8_SPOKE_S)) if r.get("gap") is not None else 0.0
        off[i] = _logit(base)
        X[i] = (1.0, _logit(rr) - off[i], last, fresh)
        if r.get("o") is not None:
            past.append(r["o"])
            k += r["o"]
    return off, X


def _r8_predict(off, X, y, i: int) -> float:
    """p_self for check i: a ridge-penalised logistic (offset: the running
    rate) fit by Newton on the checks before i, then read at i. No evidence
    (or no structure) shrinks the coefficients to 0 -- the running rate."""
    lo = max(0, i - RUNG8_FIT)
    beta = _ridge_logit(off[lo:i], X[lo:i], y[lo:i], RUNG8_RIDGE) if i > lo else np.zeros(X.shape[1])
    return float(1.0 / (1.0 + np.exp(-(off[i] + X[i] @ beta))))


def _p_self(recs: list[dict], eid: str, push: dict) -> float:
    """SELF'S PREDICTION (#254) that the other will have spoken by the reply
    check after `push`: from self's record only -- no model call."""
    rows = _rung8_rows(recs, eid)
    t = float(push.get("ts") or 0)
    last_ch = next((float(r["ts"]) for r in reversed(recs) if r.get("kind") == "reach" and r.get("eid") == eid
                    and r.get("answer") == "changed" and float(r.get("ts") or 0) <= t), None)
    rows = rows + [{"o": None, "gap": _push_gap(push, last_ch)}]
    off, X = _r8_design(rows, *_r8_carry(recs, eid))
    y = np.array([r["o"] for r in rows[:-1]], dtype=float)
    return round(_r8_predict(off, X, y, len(rows) - 1), 4)


def _ridge_logit(off, X, y, lam: float) -> np.ndarray:
    """A ridge-penalised logistic regression with an offset, by Newton:
    the coefficients that best turn `off` (logits) into the outcomes y,
    shrunk toward 0 (= trust the offset) by `lam`."""
    beta = np.zeros(X.shape[1])
    eye = lam * np.eye(X.shape[1])
    for _ in range(12):
        p = 1.0 / (1.0 + np.exp(-(off + X @ beta)))
        g = X.T @ (y - p) - lam * beta
        h = (X * (p * (1.0 - p))[:, None]).T @ X + eye
        step = np.linalg.solve(h, g)
        beta = beta + step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return beta


def _advisor_cal(rows: list[dict], model) -> tuple[float, float]:
    """THE ADVISOR, RECALIBRATED (#254). A model's stated probabilities carry
    its own lean (label bias; one model leaned "yes", the next "no"), so self
    doesn't take p_llm at face value: over the advisor's last RUNG8_TRUST
    answered checks under the SAME model id it reads the advisor's own lean
    m (the mean logit it states) and fits w in
    p = sigmoid(logit p_self + w·(logit p_llm − m)) -- Platt scaling of the
    advisor on top of self's own guess, with the advisor's average lean taken
    to say nothing (only where it departs from its habit is news): a
    constant answer, however biased, has no influence. w is ridge-shrunk
    toward 0 = "ignore the advisor", so few pairs or a new model give
    p = p_self; w >= 0: an advisor that tracks the outcome backwards is
    ignored, not decoded. Returns (m, w)."""
    rs = [r for r in rows if r.get("p_llm") is not None and r.get("model") == model][-RUNG8_TRUST:]
    if not rs:
        return 0.0, 0.0
    z = np.array([_logit(float(r["p_llm"])) for r in rs])
    m = float(z.mean())
    off = np.array([_logit(r["p_self"]) for r in rs])
    y = np.array([r["o"] for r in rs], dtype=float)
    w = float(_ridge_logit(off, (z - m)[:, None], y, RUNG8_TRUST_RIDGE)[0])
    return round(m, 4), round(max(0.0, w), 4)


def _advise(p_self: float, p_llm: float, cal) -> float:
    """Self's prediction with the recalibrated advisor heard."""
    m, w = cal
    return float(1.0 / (1.0 + np.exp(-(_logit(p_self) + w * (_logit(p_llm) - m)))))


_P8_CACHE: dict = {}     # (eid, chain hash of the checks so far) -> a recomputed p_self


def _rung8_series(recs: list[dict], eid: str) -> list[dict]:
    """Every answered reply check at `eid`, oldest first, scored as rung 8
    reads it: o, `rate` (the running rate: Jeffreys over the checks before,
    no lookahead), `p_self` (recorded, or -- a check from before #254 --
    recomputed from the record by the same function), `p_llm`, `model`, the
    advisor's calibration `cal` = (a, w) from its checks before this one,
    `w` (its slope: how much self listens) and `p`, self's prediction with
    the advisor heard (_advise; p_self when the advisor said nothing)."""
    rows = _rung8_rows(recs, eid)
    n0, k0 = _r8_carry(recs, eid)                     # #345: the checks archived before the summary
    off = X = y = None
    h = hash(("carry", n0, k0)) if n0 else 0
    k = float(k0)
    for i, r in enumerate(rows):
        r["rate"] = (k + 0.5) / (n0 + i + 1.0)
        h = hash((h, r["ts"], r["o"], r["gap"]))
        if r["rec_p_self"] is not None:
            r["p_self"] = float(r["rec_p_self"])
        else:
            key = (eid, h)
            if key not in _P8_CACHE:
                if off is None:
                    off, X = _r8_design(rows, n0, k0)
                    y = np.array([x["o"] for x in rows], dtype=float)
                if len(_P8_CACHE) > 20000:
                    _P8_CACHE.clear()
                _P8_CACHE[key] = round(_r8_predict(off, X, y, i), 4)
            r["p_self"] = _P8_CACHE[key]
        if r["p_llm"] is None:
            r["cal"], r["w"], r["p"] = (0.0, 0.0), 0.0, r["p_self"]
        else:
            rc = r["rec_cal"]
            r["cal"] = (float(rc[0]), float(rc[1])) if isinstance(rc, (list, tuple)) and len(rc) == 2 \
                else _advisor_cal(rows[:i], r["model"])
            r["w"] = r["cal"][1]
            r["p"] = (float(r["rec_p"]) if r["rec_p"] is not None
                      else _advise(r["p_self"], float(r["p_llm"]), r["cal"]))
        k += r["o"]
    return rows


def _mark_rung7_restart(srv, now: float) -> None:
    """#254: the moment the fixed rung 7 starts counting, on the record once --
    the self sheet closes its 'rung 7 graded the LLM' bug window there. Only
    a seat that has passed rung 6 has a rung 7 to restart. A rung 7 pass the
    old scorer recorded was the model's, not self's: it gets a `rung_void`
    line (the pass line stays), so rung 7 re-opens under the new scoring.

    #258: that rung 7 is rung 8 now (understand an other). The line's kind
    and its text stay as they were (the sheet and the page read the kind);
    a pass from before the flip would not count above rung 6 anyway
    (_rung_lines)."""
    rt = _rt(srv)
    if rt.get("r7_marked"):
        return
    recs = _read(srv)
    if any(r.get("kind") == "rung7_restart" for r in recs):
        rt["r7_marked"] = True
    elif 6 in _passed(recs):
        old7 = [x for x in recs if x.get("kind") == "rung" and x.get("rung") == 7
                and x.get("ladder") != LADDER_V]                # #258: a new ladder's rung 7 is take turns
        voided = {float(x.get("voids_ts") or 0) for x in recs if x.get("kind") == "rung_void" and x.get("rung") == 7}
        for r in [x for x in old7 if float(x.get("ts") or 0) not in voided]:
            _append(srv, {"ts": now, "kind": "rung_void", "rung": 7, "voids_ts": r.get("ts"),
                          "why": "#254: rung 7 graded the LLM's guess, not self's"})
        _append(srv, {"ts": now, "kind": "rung7_restart", "issue": 254,
                      "why": "rung 7 now grades self's prediction, not the model's"})
        rt["r7_marked"] = True


def _mark_glitch_fix(srv, now: float) -> None:
    """#259: the moment curiosity stops reading restart glitches as progress,
    on the record once -- the self sheet closes its 'curiosity chased the
    heartbeat's restart glitches' window there. Only a seat past rung 2 (where
    curiosity starts) had the bug to close."""
    rt = _rt(srv)
    if rt.get("curio_marked"):
        return
    recs = _read(srv)
    if any(r.get("kind") == "fix" and r.get("issue") == 259 for r in recs):
        rt["curio_marked"] = True
    elif 2 in _passed(recs):
        _append(srv, {"ts": now, "kind": "fix", "issue": 259,
                      "why": "curiosity no longer counts a restart-spanning miss or a rare outlier as progress"})
        rt["curio_marked"] = True


def _last_spoke(srv, recs: list[dict], eid: str, now: float) -> float | None:
    """When the other last spoke, as self knows it: chat:in's heard_ts (the
    moment the line arrived) or self's last look that found them changed --
    the later one, never after `now`. A time only, never the words."""
    ts = [float(r["ts"]) for r in recs if r.get("kind") == "reach" and r.get("eid") == eid
          and r.get("answer") == "changed" and float(r.get("ts") or 0) <= now]
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(eid) if est is not None else None
    heard = (doc.properties or {}).get("heard_ts") if doc is not None else None
    if isinstance(heard, (int, float)) and 0 < heard <= now:
        ts.append(float(heard))
    return max(ts) if ts else None


def _hold_reply(srv, push: dict, now: float) -> bool:
    """Not yet time for the reply check: under RUNG8_CHECK_S after the push --
    or, when the push asked the model and no answer has landed, under
    RUNG8_CHECK_S + RUNG8_GRACE_S (#217: a slow turn's answer still pairs).
    Self only holds the other back; the cycle goes on with other targets.

    #278, a turn on the ball's clock: the check comes as soon as self sees
    the ball back on its side, or TOY_CHECK_S after the roll (a reply can't
    come later); an advisor's answer may hold it up to TOY_GRACE_S more."""
    age = now - float(push.get("ts") or 0)
    if _medium(push) == "toy" and _clock_of(push) == TOY_CLOCK:
        if push.get("asked") and age < TOY_CHECK_S + TOY_GRACE_S \
                and _llm_pred(srv, push["to"], float(push["ts"]), until=now) is None:
            return True
        if age >= TOY_CHECK_S:
            return False
        from . import toybox as _TB
        return (_TB.peek(push.get("toy") or "") or {}).get("side") != "self"
    if age < RUNG8_CHECK_S:
        return True
    if push.get("asked") and age < RUNG8_CHECK_S + RUNG8_GRACE_S:
        return _llm_pred(srv, push["to"], float(push["ts"]), until=now) is None
    return False


def _rung8_prompt(srv, eid: str, n: int, now: float, medium: str = "chat") -> str:
    """What to ask the coprocessor at a push: will the other have spoken by
    self's reply check? Only facts self already holds in its record --
    timings and changed/unchanged answers, never the chat's words.

    CALIBRATED (#217): live, the model answered 0.98-0.99 to reply checks
    that went 3 of 7. It now gets the BASE RATE (the share of reply checks
    the other had answered, Jeffreys prior) and the recent reply checks only
    -- looks long after a push are another horizon and read as "they always
    answer" -- and is told a confident miss costs (the Brier score).

    RECENT (#246): the all-time rate can't see a person who went busy; the
    question also carries the share of the last RUNG8_RECENT reply checks
    answered and the time since they last spoke, labelled `recent` vs
    `all-time`, and says rhythms change. Input only: the test, the pairing,
    the caps and the parsing are #217's."""
    recs = _read(srv)
    rate, k, m = _base_rate(recs, eid)
    rr, kr, mr = _recent_rate(recs, eid)
    rc = _reply_checks(recs, eid)[-RUNG8_HISTORY:]
    lines = []
    for r in rc:
        push = next((p for p in reversed(recs) if p.get("kind") == "push" and p.get("to") == eid
                     and float(p.get("ts") or 0) < float(r["ts"])), None)
        after = f"{round(float(r['ts']) - float(push['ts']))}s after" if push is not None else "after"
        lines.append(f"{after}: " + ("they had replied" if _reply_o(r) else "silent"))
    hist = "; ".join(lines) or "(none yet)"
    if medium == "toy":                                     # #264: the turn was a roll of the ball
        acted = _last_acted(srv, now)
        last = (f"The person last played (a poke on any toy) {round(now - acted)}s ago. " if acted is not None
                else "The person has not played yet. ")
        return (f"You are predicting whether the person self plays ball with will roll the ball ({eid}) "
                f"back to self by self's reply check. Self just rolled it to them (turn {n}); "
                f"the reply check comes once self sees the ball back, at most {round(TOY_CHECK_S)}s from now "
                f"(a roll back later than that is no reply). "
                f"All-time: they had rolled it back by the reply check after {k} of {m} earlier turns -- "
                f"all-time base rate {rate:.2f} (with a small prior). "
                f"Recent: they had rolled it back after {kr} of the last {mr} -- "
                f"recent rate {rr:.2f} (same prior). " + last
                + f"Recent reply checks (oldest first): {hist}. "
                "People's rhythms change (a busy hour, a quiet evening): when the recent rate disagrees "
                "with the all-time one, weigh the recent window more. "
                "Give ONE calibrated probability (0..1) that they will have rolled it back by that check. "
                "You are scored by the Brier score, (p - outcome)^2: a confident wrong answer is penalized "
                "heavily (0.98 that misses costs 0.96), so stay near these rates unless these facts give "
                "a real reason to move. Don't use tools -- everything you know is here. "
                "Answer in one line: VERDICT: <probability>")
    spoke = _last_spoke(srv, recs, eid, now)
    last = (f"Self last saw them speak {round(now - spoke)}s ago. " if spoke is not None
            else "Self has not seen them speak yet. ")
    return (f"You are predicting whether {eid} (the person self talks to) will have spoken by "
            f"self's next look at them (its reply check). Self just pushed a line to them (push {n}); "
            f"the reply check comes about {round(RUNG8_CHECK_S)}s from now "
            f"(at most {round(RUNG8_CHECK_S + RUNG8_GRACE_S)}s). "
            f"All-time: they had replied (spoken after the push) by the reply check after {k} of {m} earlier pushes -- "
            f"all-time base rate {rate:.2f} (with a small prior). "
            f"Recent: they had replied after {kr} of the last {mr} -- "
            f"recent rate {rr:.2f} (same prior). " + last
            + f"Recent reply checks (oldest first): {hist}. "
            "People's rhythms change (a busy hour, a quiet evening): when the recent rate disagrees "
            "with the all-time one, weigh the recent window more. "
            "Give ONE calibrated probability (0..1) that they will have spoken by that check. "
            "You are scored by the Brier score, (p - outcome)^2: a confident wrong answer is penalized "
            "heavily (0.98 that misses costs 0.96), so stay near these rates unless these facts give "
            "a real reason to move. Don't use tools -- everything you know is here. "
            "Answer in one line: VERDICT: <probability>")


# ---- #264: the turns with the ball ---------------------------------------------------

def _last_acted(srv, now: float) -> float | None:
    """When the other last acted, as self knows it (#264): a person's last
    poke on any toy (toybox.last_person_poke), never after `now`. A time only."""
    from . import toybox as _TB
    last = _TB.last_person_poke()
    ts = float(last["ts"]) if last else None
    return ts if ts is not None and 0 < ts <= now else None


# ---- rung 7 (#258): take turns -- self's own timing --------------------------------

def _turn_bin(age, bins=TURN_BINS) -> int:
    """The spoke_age bin: 0-2 min, 2-10, 10-30, 30+ (or the other never
    spoke); on the ball's clock (#278, bins=TOY_TURN_BINS) 0-20 s, 20-60,
    1-3 min, 3+ (or never acted)."""
    if age is None:
        return len(bins)
    for i, hi in enumerate(bins):
        if age < hi:
            return i
    return len(bins)


def _turn_post(rows: list[dict]):
    """Replies (k) and pushes (n) per bin over the last TURN_MEMORY pushes
    whose reply check came."""
    k, n = np.zeros(len(TURN_BINS) + 1), np.zeros(len(TURN_BINS) + 1)
    for r in [x for x in rows if x.get("replied") is not None][-TURN_MEMORY:]:
        n[r["bin"]] += 1
        k[r["bin"]] += 1.0 if r["replied"] else 0.0
    return k, n


def _turn_choose(rows: list[dict], rng) -> tuple[int, int | None]:
    """THE POLICY: the moment the next push waits for, and the window self
    believes best. aim: a Thompson draw from each bin's Beta(1 + replies,
    1 + misses) -- the bin whose draw is highest -- or, with chance
    TURN_EXPLORE, a random bin. best: the bin with the highest posterior mean
    (ties: the one with more evidence), None before any evidence. Symmetric
    in the bins: which one wins is the record's doing, never the code's."""
    k, n = _turn_post(rows)
    nb = len(k)
    best = None
    if n.sum() > 0:
        mean = (k + 1.0) / (n + 2.0)
        best = max(range(nb), key=lambda b: (mean[b], n[b], -b))
    if rng.random() < TURN_EXPLORE:
        return rng.randrange(nb), best
    draws = [rng.betavariate(1.0 + k[b], 1.0 + n[b] - k[b]) for b in range(nb)]
    return max(range(nb), key=lambda b: draws[b]), best


def _turn_push(srv, recs: list[dict], to: str, n: int, age, clock: int = 1) -> dict | None:
    """One cycle of rung 7's practice: count the moment (its bin) into this
    interval's waiting time and say whether to push now -- the push's fields
    (bin, aim, best, dwell, why) or None. Never sooner than MEET_EVERY cycles
    after the last push; at MEET_MAX it goes anyway ('overdue'). On the
    ball's clock (#278) its bins, gap and cap are TOY_*."""
    import random
    ck = _clock(clock)
    last = next((r for r in reversed(recs) if r.get("kind") == "push"), None)
    if last is not None and n - int(last.get("n") or 0) < ck["every"]:
        return None
    rt = _rt(srv)
    st = rt.get("turn")
    key = (to, float(last["ts"]) if last is not None else None, clock)
    if st is None or st.get("key") != key:
        rows = _turn_rows(recs, to, "toy", clock) if clock == TOY_CLOCK else _turn_rows(recs, to)
        aim, best = _turn_choose(rows, random.Random(f"turn:{n}"))
        st = rt["turn"] = {"key": key, "aim": aim, "best": best, "dwell": [0] * (len(ck["bins"]) + 1)}
    b = _turn_bin(age, ck["bins"])
    st["dwell"][b] += 1
    waited = (n - int(last.get("n") or 0)) if last is not None else ck["every"] - 1 + sum(st["dwell"])
    if b != st["aim"] and waited < ck["max"]:
        return None
    rt["turn"] = None
    return {"bin": b, "aim": st["aim"], "best": st["best"], "dwell": list(st["dwell"]),
            "why": "aim" if b == st["aim"] else "overdue"}


def _replied(srv, push: dict, now: float, answer: str) -> bool:
    """The push's reward: did the other speak AFTER it, by this check?
    chat:in's heard_ts (the moment their latest line arrived) says so; a look
    can read `changed` for a line from before the push that self hadn't seen
    yet, which is not a reply. Without a heard_ts, the look's answer.

    #264, a turn with the ball: did a PERSON roll it back after self's roll,
    by this check? The ball's other_at says so (the model's roll, or self's
    own, never sets it); the look's answer is not asked -- self's own roll
    changed the ball too. #278, on the ball's clock: within TOY_CHECK_S of
    the roll, whenever the check comes."""
    if _medium(push) == "toy":
        from . import toybox as _TB
        st = _TB.peek(push.get("toy") or "") or {}
        at = st.get("other_at")
        ts = float(push.get("ts") or 0)
        if _clock_of(push) == TOY_CLOCK:
            now = min(now, ts + TOY_CHECK_S)
        return bool(isinstance(at, (int, float)) and ts < at <= now)
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(push.get("to")) if est is not None else None
    heard = (doc.properties or {}).get("heard_ts") if doc is not None else None
    if isinstance(heard, (int, float)) and heard > 0:
        return bool(float(push.get("ts") or 0) < heard <= now)
    return answer == "changed"


def _turn_rows(recs: list[dict], eid: str, medium: str | None = None, clock: int | None = None) -> list[dict]:
    """Rung 7's pushes at `eid`, oldest first: the policy's pushes (they carry
    an aim and their waiting time) whose reply check recorded `replied`.
    Timer pushes don't count: they had no moment to choose. One medium only
    (#264: the current turn's by default -- chat turns and turns with the
    ball never share a window)."""
    medium = medium or _cur_medium(recs, eid)
    if clock is None:                                     # #278: one clock only -- its bins mean its windows
        clock = _cur_clock(recs, eid, medium)
    rows: list[dict] = []
    pend = None
    for r in recs:
        k = r.get("kind")
        if k == "push":
            pend = r if (r.get("to") == eid and r.get("aim") is not None and r.get("dwell")
                         and _medium(r) == medium and _clock_of(r) == clock) else None
        elif k == "reach" and r.get("eid") == eid and pend is not None:
            if r.get("reply") and isinstance(r.get("replied"), bool):
                rows.append({"ts": float(pend.get("ts") or 0), "bin": int(pend.get("bin", len(TURN_BINS))),
                             "best": pend.get("best"), "dwell": list(pend["dwell"]), "replied": r["replied"],
                             "clock": clock})
            pend = None                                     # only the first look after the push is its check
    return rows


def _turn_deltas(rows: list[dict]) -> list[tuple]:
    """Per push, from what self knew BEFORE it (the last TURN_MEMORY pushes):
    (in its believed-best window 0/1, the share of its waiting time the other
    spent in that window, outcome - what a timing-blind push would expect,
    outcome). The bins' rates are shrunk toward the overall one."""
    out = []
    for i, r in enumerate(rows):
        k, n = _turn_post(rows[max(0, i - TURN_MEMORY):i])
        tot = (k.sum() + 0.5) / (n.sum() + 1.0)
        rate = (k + 2.0 * tot) / (n + 2.0)
        dw = np.asarray(r["dwell"], dtype=float)
        if dw.sum() <= 0:
            continue
        share = dw / dw.sum()
        best = r.get("best")
        inw = 1.0 if best is not None and r["bin"] == best else 0.0
        p0 = float(share[best]) if best is not None else 0.0
        y = 1.0 if r["replied"] else 0.0
        out.append((inw, p0, y - float(share @ rate), y))
    return out


def _turn_window(d: list[tuple], clock: int = 1) -> dict:
    """Rung 7's test over one window of _turn_deltas: (a) contingency, (b)
    payoff -- against the bar of the turns' clock (#278)."""
    if not d:
        return {"n": 0, "share": None, "chance": None, "z": 0.0, "payoff": None, "payoff_z": 0.0,
                "reply_rate": None, "blind_rate": None, "passed": False}
    a = np.asarray(d, dtype=float)
    inw, p0, dl, y = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    var = float((p0 * (1.0 - p0)).sum())
    z = float((inw.sum() - p0.sum()) / np.sqrt(var)) if var > 1e-9 else 0.0
    pay = float(dl.mean())
    sd = float(dl.std(ddof=1)) if len(dl) > 1 else 0.0
    t = pay / (sd / np.sqrt(len(dl))) if sd > 1e-12 else 0.0
    share, chance = float(inw.mean()), float(p0.mean())
    ck = _clock(clock)
    ok = (len(d) >= RUNG7_PUSHES and z >= ck["cont_z"] and share - chance >= ck["cont_margin"]
          and t >= ck["pay_z"] and pay >= ck["pay_margin"])
    return {"n": len(d), "share": share, "chance": chance, "z": z, "payoff": pay, "payoff_z": float(t),
            "reply_rate": float(y.mean()), "blind_rate": float(y.mean()) - pay, "passed": bool(ok)}


def _turn_score(rows: list[dict]) -> dict:
    """Rung 7's test over the last RUNG7_PUSHES of `rows` (each scored on the
    TURN_MEMORY before it)."""
    rows = rows[-(RUNG7_PUSHES + TURN_MEMORY):]
    return _turn_window(_turn_deltas(rows)[-RUNG7_PUSHES:], rows[-1].get("clock", 1) if rows else 1)


def _reach(srv, target: str) -> dict:
    """Read one thing: its getter fires (or answers that it can't)."""
    c = _ctx.cycle
    ent = (srv.entity_store.docs or {}).get(target)
    if ent is None:
        c["read"] = {"fired": False, "reason": "gone"}
        return {"rc": 0, "text": "nothing there"}
    if not _in_world(srv, target):                         # #337: outside its world, nothing is read
        c["read"] = {"fired": False, "reason": "outside its world"}
        return {"rc": 0, "text": "not in its world"}
    tb = (ent.properties or {}).get("toybox") or {}
    if tb.get("name"):                                      # #244: the toy's log, before and after the look
        from . import toybox as _TB
        c["toy"] = {"name": tb["name"], "block": tb.get("block"), "at0": _TB.log_len(tb["name"])}
    try:
        c["read"] = _read_entity(srv, ent, c.get("now"))
    except Exception as e:                                  # noqa: BLE001
        c["read"] = {"fired": False, "reason": f"error: {e!r}"[:80]}
    if c.get("toy"):
        c["toy"]["at1"] = _TB.log_len(tb["name"])
    return {"rc": 0, "text": "reached"}


# ---- #333: the nudge -- its one act on a thing ------------------------------------------
#
# Reach is infinite (a nudge is mental, not physical), but only a thing in Blurp's world (#337)
# can be nudged: one outside is refused. THE THING decides what a nudge does -- a toy by its
# kind (toybox.nudge: the ball rolls to the other side, a tower block topples or lifts, a die /
# drift / burst changes; fast, slow, body and the mirror take none), any other thing by a
# `nudge` method of its own (a cliffhanger method like any other), and a thing with neither
# does nothing. The outcome is the line's rc: NUDGE_MOVED, NUDGE_NOTHING or NUDGE_REFUSED.
# Using a thing wrong costs energy (energy.error, 0.01): a refused nudge (the ball while it is
# on the person's side, a thing's own method failing) and a nudge outside its world. What the
# thing did is never fed back: Blurp sees it only by looking.

NUDGE_MOVED, NUDGE_NOTHING, NUDGE_REFUSED = 0, 1, 2


def _practised(srv, i: str) -> bool:
    """#333: a skill's practice runs -- awake, every prerequisite earned and
    accepted -- whether its test is built yet or not (the nudge's test comes
    with the rebuilt rungs). A test's `isolate` cuts it as it cuts the rest."""
    rt = _rt(srv)
    if rt["mode"] == "off" or i not in ACHIEVEMENTS:
        return False
    cap = rt.get("isolate")
    if cap is not None and i not in cap:
        return False
    acc = _accepted(_read(srv))
    return all(p in acc for p in ACHIEVEMENTS[i]["prereqs"])


def _spot(srv, eid: str):
    """A thing's live field spot (a unit vector), None when it has none."""
    fld = getattr(srv, "efield", None)
    get = getattr(fld, "get", None)
    if get is None:
        return None
    try:
        from .eids import field_id
        st = get(field_id(eid))
    except Exception:
        return None
    if st is None or st.tier != "live" or st.state is None:
        return None
    return _unit(st.state)


def _spots(srv, dim: int) -> dict:
    """#333: the field spots of the things in Blurp's world (itself left out):
    store eid -> unit vector, only those that have one in this field's space."""
    est = getattr(srv, "entity_store", None)
    out = {}
    for eid in list(((est.docs if est is not None else None) or {})):
        if eid == _eid() or not _in_world(srv, eid):
            continue
        v = _spot(srv, eid)
        if v is not None and len(v) == dim:
            out[eid] = v
    return out


def _nudge(srv, target: str) -> dict:
    """self::nudge -- the thing decides (see above); the outcome lands on the
    cycle's context (`rc`, `did`), never in what Blurp reads."""
    c = _ctx.cycle
    est = srv.entity_store
    ent = (est.docs or {}).get(target)
    if ent is None:
        c["rc"], c["did"] = NUDGE_NOTHING, "nothing there"
        return {"rc": 0, "text": "nothing there"}
    if not _in_world(srv, target):
        c["rc"], c["did"] = NUDGE_REFUSED, "outside its world"
        return {"rc": 0, "text": "not in its world"}
    tb = (ent.properties or {}).get("toybox") or {}
    if tb.get("name"):
        from . import toybox as _TB
        try:
            out = _TB.nudge(tb["name"], block=tb.get("block"), now=c.get("now"))
        except Exception as e:                              # noqa: BLE001
            out = {"ok": False, "refused": False, "changed": False, "error": repr(e)}
        c["rc"] = (NUDGE_REFUSED if out.get("refused") else NUDGE_MOVED if out.get("changed")
                   else NUDGE_NOTHING)
        c["did"] = str(out.get("did") or out.get("error") or "")
        return {"rc": 0, "text": "nudged"}
    from .night import method_call, resolve_method
    if resolve_method(est, ent, "nudge")[0] is None:
        c["rc"], c["did"] = NUDGE_NOTHING, "it takes no nudge"
        return {"rc": 0, "text": "nudged"}
    out = method_call(srv, est, None, None, target, "nudge", {})
    c["rc"] = NUDGE_MOVED if out.get("rc") == 0 else NUDGE_REFUSED
    c["did"] = str(out.get("head") or out.get("error") or out.get("outcome") or "")[:120]
    return {"rc": 0, "text": "nudged"}


def _dist(srv, eid: str):
    """How far Blurp's position is from a thing's field spot (None when
    either has none, or they live in different spaces)."""
    me = _entity(srv)
    pos = np.asarray(me.position, dtype=float) if me is not None and me.position is not None else None
    spot = _spot(srv, eid)
    return (round(float(np.linalg.norm(_unit(pos) - spot)), 4)
            if pos is not None and spot is not None and len(pos) == len(spot) else None)


def _closing(srv, eid: str) -> bool:
    """#333, decision 1: is Blurp getting closer to what it aims at? Its
    distance to the aimed thing now, against its previous cycle's distance to
    the SAME aim (rt["aim_dist"]: (aim_n, eid, dist), RAM only). True only when it
    strictly fell; a new aim's first cycle (or a restart) has no previous
    distance, so False. Updates the previous distance either way."""
    rt = _rt(srv)
    d = _dist(srv, eid)
    prev = rt.get("aim_dist")
    rt["aim_dist"] = (rt.get("aim_n"), eid, d)
    return (prev is not None and prev[0] == rt.get("aim_n") and prev[1] == eid
            and prev[2] is not None and d is not None and d < prev[2])


def _nudge_once(srv, n: int, now: float, eid: str, move) -> None:
    """#333, one nudge: Blurp's own move (`move`: its direction and its force,
    the stride it chose this cycle) at a thing, through its own method; a
    `nudge` line {n, ts, eid, dist, rc, aim, force, did}, and the error fee
    for a nudge refused or outside its world. dist: how far its position is
    from the thing's field spot (None when either has none); the cycle only
    calls this while that distance is falling (_closing)."""
    est = srv.entity_store
    me = _entity(srv)
    move = np.asarray(move if move is not None else [], dtype=float)
    dist = _dist(srv, eid)
    from .night import method_call
    _ctx.cycle = {"srv": srv, "act": "nudge", "now": now, "eid": eid}
    try:
        out = method_call(srv, est, None, None, me.eid, "nudge", {"target": eid})
        c = _ctx.cycle
    finally:
        _ctx.cycle = None
    if "rc" not in c:
        _append(srv, {"ts": now, "kind": "error", "n": n, "act": "nudge",
                      "why": str(out.get("head") or out.get("error") or "")[:200]})
        return
    rt = _rt(srv)
    _append(srv, {"ts": now, "kind": "nudge", "n": n, "eid": eid, "dist": dist, "rc": c["rc"],
                  "aim": rt.get("aim_n") if rt.get("aim_eid") == eid else None,
                  "force": round(float(np.linalg.norm(move)), 4) if move.size else 0.0,
                  "did": c.get("did") or ""})
    if c["rc"] == NUDGE_REFUSED:
        from . import energy as _E
        _E.error(srv, f"nudge refused: {_short(eid)}: {c.get('did') or ''}", now=now)


def _toy_by(recs: list[dict], eid: str, toy: dict) -> list[str]:
    """WHO CHANGED IT (#244), the ground truth on self's reach record: every
    `by` on the toy's log (its block's lines, for a block) since self's last
    look at this thing, through this look's own act. Kept beside the answer,
    never in the reading -- self predicts from what it saw, not from who did it."""
    from . import toybox as _TB
    prev = next((r.get("log_at") for r in reversed(recs)
                 if r.get("kind") == "reach" and r.get("eid") == eid and r.get("log_at") is not None), None)
    start = toy["at0"] if prev is None or prev > toy["at0"] else prev   # a reset starts a fresh log
    return _TB.causes(toy["name"], start, toy["at1"], toy.get("block"))


def _place_once(srv, eid: str) -> None:
    """THE FIRST LOOK PLACES (#244): a thing self reaches that has no field
    point yet gets one (toybox.place: its summary, else its name, embedded) --
    once per thing per run; quiet when the field or its embedder is missing."""
    rt = _rt(srv)
    done = rt.setdefault("placed", set())
    if eid in done:
        return
    from . import toybox as _TB
    if _TB.place(srv, eid) is not None:
        done.add(eid)
        rt.pop("off_field", None)


def _answer(read: dict) -> tuple[str, str]:
    """changed / unchanged / none -- a throttle, a hold, an error or no reading
    is no answer, never the world's."""
    if not read.get("fired"):
        return "none", str(read.get("reason") or "did not fire")
    if read.get("error"):
        return "none", "held" if read.get("held") else "error"
    if read.get("changed") is None:
        return "none", "no reading"
    return ("changed" if read["changed"] else "unchanged"), ""


def _notice(srv, seed: str) -> dict:
    """Where I ended up; the push I did not account for; learn it.
    (For a reach: whether it had changed -- and learn its tempo.)"""
    c = _ctx.cycle
    if c.get("act") == "reach":
        c["answer"], c["why"] = _answer(c.get("read") or {})
        if not c.get("audit"):                               # inside an audit it learns nothing
            t = _tempo(srv).setdefault(c["eid"], {"changed": 0, "answered": 0})
            if c["answer"] == "none":
                t["none"] = int(t.get("none", 0)) + 1
            else:
                prev = t.get("prev")                         # the answer before this look
                t["answered"] += 1
                t["changed"] += int(c["answer"] == "changed")
                aft = t.setdefault("after", {})              # the one bit: what followed what
                if prev in ("changed", "unchanged"):
                    e = aft.setdefault(prev, [0, 0])
                    e[0] += int(c["answer"] == "changed")
                    e[1] += 1
                t["prev"] = c["answer"]
                t["last"] = c.get("now")
            _save_json(srv, "tempo.json", _tempo(srv))
        return {"rc": 0, "text": "noticed"}
    ent = _entity(srv)
    got = np.asarray(ent.position, dtype=float)
    push = got - c["intent"]
    if c.get("still"):                       # #282: a still world teaches nothing about the current
        c["observed"] = got
        c["push"] = float(np.linalg.norm(push))
        return {"rc": 0, "text": "noticed"}
    p = c["pos"]
    pp = ent.properties or {}
    sd = dict(pp.get("script_data") or {})
    m = _model(srv, len(got))
    if m is None:
        m = {"mp": p, "mw": push, "cov": 0.0, "var": 0.0}
    else:
        mp = np.asarray(m["mp"]) + LEARN * (p - np.asarray(m["mp"]))
        mw = np.asarray(m["mw"]) + LEARN * (push - np.asarray(m["mw"]))
        m = {"mp": mp, "mw": mw,
             "cov": (1 - LEARN) * float(m["cov"]) + LEARN * float((push - mw) @ (p - mp)),
             "var": (1 - LEARN) * float(m["var"]) + LEARN * float((p - mp) @ (p - mp))}
    m = {"mp": [float(x) for x in m["mp"]], "mw": [float(x) for x in m["mw"]],
         "cov": float(m["cov"]), "var": float(m["var"])}
    _rt(srv)["model"] = m
    _save_model(srv, m)
    sd.pop("current_model", None)            # the old home (cut to 128 items on save)
    sd["pull"] = round(-m["cov"] / m["var"], 4) if m["var"] > 1e-9 else None
    pp["script_data"] = sd
    ent.properties = pp
    c["observed"] = got
    c["push"] = float(np.linalg.norm(push))
    return {"rc": 0, "text": "noticed"}


def from_command(rest: str) -> dict:
    """`self::<method> <seed>` (the no-shell dialect, night.run_command_entity)."""
    if getattr(_ctx, "cycle", None) is None:
        return {"rc": 1, "text": "self's methods run only in its own loop"}
    try:
        parts = shlex.split(rest)
    except ValueError:
        parts = rest.split()
    if not parts or parts[0] not in METHODS:
        return {"rc": 2, "text": f"self has no method {parts[0] if parts else ''!r}"}
    fn = {"try": _try, "expect": _expect, "notice": _notice, "reach": _reach,
          "nudge": _nudge}[parts[0]]   # looked up per call
    srv = _ctx.cycle["srv"]
    return fn(srv, parts[1] if len(parts) > 1 else "0")


# ---- #312: the mirror ------------------------------------------------------------

def _mirror_axis(dim: int) -> np.ndarray:
    """The fixed axis a move is read on (seeded: the same on every seat and run)."""
    v = np.random.default_rng(312).normal(size=dim)
    return v / (np.linalg.norm(v) or 1.0)


def _mirror_move(move) -> float:
    """A move as one scalar: the direction of the displacement on the mirror's
    axis, x sqrt(dim) (~N(0, 1) for a direction at random); no move is 0."""
    m = np.asarray(move, dtype=float)
    n = float(np.linalg.norm(m))
    if not len(m) or n <= CALM:
        return 0.0
    return float(np.sqrt(len(m)) * (m / n) @ _mirror_axis(len(m)))


def _mirror_toy(srv) -> str | None:
    """The mirror self feeds: the first mirror toy by name in its world (#337);
    None without one."""
    est = getattr(srv, "entity_store", None)
    names = sorted({str(((e.properties or {}).get("toybox") or {}).get("name") or "")
                    for eid, e in list(((est.docs if est is not None else None) or {}).items())
                    if ((e.properties or {}).get("toybox") or {}).get("kind") == "mirror"
                    and _in_world(srv, eid)} - {""})
    return names[0] if names else None


def _mirror_rows(recs: list[dict], toy: str | None = None, n: int | None = None) -> list[dict]:
    """The latest mirror lines (of one toy), oldest first; at most n."""
    out = []
    for r in reversed(recs):
        if r.get("kind") == "mirror" and (toy is None or r.get("toy") == toy):
            out.append(r)
            if n is not None and len(out) >= n:
                break
    return out[::-1]


def _ridge(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    P = MIRROR_RIDGE * np.eye(X.shape[1])
    P[0, 0] = 0.0                                          # the intercept is free
    return np.linalg.solve(X.T @ X + P, X.T @ y)


def _mirror_predict(rows: list[dict], side: str, s_now: float):
    """SELF'S PREDICTION of a side's next value, before it reads it, from the
    rows before only (prequential): (p, b) -- p from its own moves (this one,
    s_now, and the MIRROR_OWN-1 before) and the side's last MIRROR_PAST values,
    b (the no-self baseline) from the side's values alone. The same learner
    on both sides: nothing tells it which is the glass. None before
    MIRROR_MIN_FIT rows to fit on."""
    k = max(MIRROR_OWN - 1, MIRROR_PAST)
    rows = rows[-(MIRROR_FIT + k):]

    def feats(i, s_i):                                     # row i's features (its own move s_i)
        past = [rows[i - j][side] for j in range(1, MIRROR_PAST + 1)]
        own = [s_i] + [rows[i - j].get("s") for j in range(1, MIRROR_OWN)]
        if any(v is None for v in past + own):
            return None
        return [1.0] + [float(v) for v in past] + [float(v) for v in own]

    X, y = [], []
    for i in range(k, len(rows)):
        if rows[i].get(side) is None:
            continue
        f = feats(i, rows[i].get("s"))
        if f is not None:
            X.append(f)
            y.append(float(rows[i][side]))
    if len(X) < MIRROR_MIN_FIT or len(rows) < k:
        return None
    rows = rows + [{"s": s_now}]                            # the row being predicted
    f = feats(len(rows) - 1, s_now)
    if f is None:
        return None
    X, y, f = np.asarray(X), np.asarray(y), np.asarray(f)
    nb = 1 + MIRROR_PAST
    try:
        return float(f @ _ridge(X, y)), float(f[:nb] @ _ridge(X[:, :nb], y))
    except np.linalg.LinAlgError:
        return None


def _mirror_once(srv, n: int, now: float, move) -> None:
    """RUNG 9'S PRACTICE (#312), each cycle while recognize itself is open:
    predict both sides of the mirror, feed it this cycle's move, read both,
    and write one `mirror` line: {s, glass, stranger, p_*, b_*} (scalars)."""
    name = _mirror_toy(srv)
    if name is None:
        return
    s = round(_mirror_move(move), 4)
    rows = _mirror_rows(_read(srv), name, MIRROR_FIT + MIRROR_OWN + MIRROR_PAST)
    pred = {side: _mirror_predict(rows, side, s) for side in ("glass", "stranger")}
    from . import toybox as _TB
    try:
        out = _TB.mirror_feed(name, s, now=now)
    except Exception as e:                                  # noqa: BLE001
        out = {"ok": False, "error": repr(e)}
    if not out.get("ok"):
        return
    rec = {"ts": now, "kind": "mirror", "n": n, "toy": name, "s": s,
           "glass": out.get("glass"), "stranger": out.get("stranger")}
    for side, pb in pred.items():
        if pb is not None:
            rec[f"p_{side}"], rec[f"b_{side}"] = round(pb[0], 4), round(pb[1], 4)
    _append(srv, rec)


# ---- #320: change itself -- its own learning settings, and the dream ----------------

def _defaults() -> dict:
    """The settings as the code has them (no adoption): today's constants."""
    return {"lam_looks": LAM_LOOKS, "lam_prior_looks": LAM_PRIOR_LOOKS}


def _clean_settings(doc) -> dict:
    """Each setting's value from doc when it is on its grid, else the default;
    nothing else is read."""
    out = _defaults()
    doc = doc if isinstance(doc, dict) else {}
    for s, grid in CHANGE_SETTINGS.items():
        v = doc.get(s)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v in grid:
            out[s] = grid[grid.index(v)]
    return out


def _load_settings(srv) -> dict:
    return _clean_settings(_load_json(srv, "settings.json"))


def _settings(srv) -> dict:
    """The learning settings the live loop runs with (#320)."""
    rt = _rt(srv)
    if rt.get("settings") is None:
        rt["settings"] = _load_settings(srv)
    return rt["settings"]


def _save_settings(srv, doc: dict) -> None:
    """Persist (self/settings.json, beside mode.json) and run with them."""
    s = _clean_settings(doc)
    _save_json(srv, "settings.json", {**s, "ts": time.time()})
    _rt(srv)["settings"] = s


def _fittable(r: dict) -> bool:
    """A look _lam fits on: answered, outside every audit, with its dt."""
    return (r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5")
            and r.get("answer") in ("changed", "unchanged") and bool(r.get("dt")))


class _RateFit:
    """_lam over a record read once, line by line (add), at any settings:
    lam(eid, looks, prior) equals _lam(the lines added so far, eid, looks,
    prior) (tests/test_self_change_320.py). The looks and shifts are kept in
    record order, so a shift's prior is the looks before its line."""

    def __init__(self):
        self.fit: list[tuple] = []                 # (eid, ts, dt, changed): every fittable look
        self.mine: dict[str, list[tuple]] = {}
        self.start: dict[str, tuple] = {}          # eid -> (its looks, the seat's looks) at its last shift
        self.ver: dict[str, int] = {}
        self.memo: dict[tuple, tuple] = {}

    def add(self, r: dict) -> None:
        k = r.get("kind")
        if k == "shift" and r.get("eid") is not None:
            e = r["eid"]
            self.start[e] = (len(self.mine.get(e, ())), len(self.fit))
            self.ver[e] = self.ver.get(e, 0) + 1
        elif k == "reach" and _fittable(r):
            o = (r["eid"], r["ts"], float(r["dt"]), r["answer"] == "changed")
            self.fit.append(o)
            self.mine.setdefault(o[0], []).append(o)
            self.ver[o[0]] = self.ver.get(o[0], 0) + 1

    def lam(self, eid: str, looks: int, prior: float) -> float | None:
        key, v = (eid, int(looks), float(prior)), self.ver.get(eid, 0)
        hit = self.memo.get(key)
        if hit is not None and hit[0] == v:
            return hit[1]
        W, mine, st = int(looks), self.mine.get(eid, []), self.start.get(eid)
        obs = (mine[st[0]:] if st else mine)[-W:]
        out = None
        if obs:
            ll = _lam_ll(obs)
            if st is not None:
                old = (mine[:st[0]] or self.fit[:st[1]])[-W:]
                if old:
                    ll = ll + _lam_prior_ll(eid, old, float(prior))
            out = float(LAM_GRID[int(np.argmax(ll))])
        self.memo[key] = (v, out)
        return out


def _rate_looks(recs: list[dict]) -> list[int]:
    """The looks whose p reads the rate fit -- a timed look, or a reply check
    once timed looks exist (the lam_shrink harness) -- and that have a rate to
    read (a fittable look at the thing since its last shift), as indices."""
    out, since, timed = [], {}, False
    for i, r in enumerate(recs):
        k = r.get("kind")
        if k == "shift" and r.get("eid") is not None:
            since[r["eid"]] = 0
        elif k == "reach":
            timed = timed or bool(r.get("timed"))
            ok = _fittable(r)
            if ok and (r.get("timed") or (r.get("reply") and timed)) and since.get(r["eid"], 0) > 0:
                out.append(i)
            if ok:
                since[r["eid"]] = since.get(r["eid"], 0) + 1
    return out


def _replay_at(recs: list[dict], want: dict) -> dict:
    """THE DREAM'S REPLAY: one pass over the record, predict then learn.
    want: (looks, prior) -> the indices of the looks to predict at those
    settings. -> {(looks, prior): {index: p}} -- p = 1 - exp(-lam dt), the
    rate fit from the lines before the look only."""
    fit, out = _RateFit(), {v: {} for v in want}
    need: dict[int, list] = {}
    for v, idx in want.items():
        for i in idx:
            need.setdefault(i, []).append(v)
    for i, r in enumerate(recs):
        for v in need.get(i, ()):
            lam = fit.lam(r["eid"], *v)
            out[v][i] = (1.0 - float(np.exp(-lam * float(r["dt"])))) if lam else None
        fit.add(r)
    return out


def _replay(recs: list[dict], variants: list[tuple]) -> list[tuple]:
    """Every rate-read look, predicted at each (looks, prior): [(ts, y, [p, ...])]."""
    idx = _rate_looks(recs)
    got = _replay_at(recs, {tuple(v): idx for v in variants})
    return [(recs[i]["ts"], int(recs[i]["answer"] == "changed"), [got[tuple(v)][i] for v in variants]) for i in idx]


def _bz(blocks) -> float:
    """The batch-means z of the blocks' mean (looks come in sessions, so one
    look is not independent of the next; consecutive blocks nearly are)."""
    b = np.asarray(blocks, dtype=float)
    if len(b) < 2:
        return 0.0
    m, sd = float(b.mean()), float(b.std(ddof=1))
    if sd <= 1e-12:
        return 0.0 if abs(m) <= 1e-12 else float(np.copysign(99.0, m))
    return float(m / (sd / np.sqrt(len(b))))


def _pair(recs: list[dict], idx: list[int], pa: dict, pb: dict) -> dict:
    """The paired score of b against a on those looks: Brier of each, the
    gain per look (positive: b predicted better), its blocks and their z."""
    y = np.array([float(recs[i]["answer"] == "changed") for i in idx])
    a = (np.array([pa[i] for i in idx]) - y) ** 2
    b = (np.array([pb[i] for i in idx]) - y) ** 2
    blocks = [float(x.mean()) for x in np.array_split(a - b, CHANGE_BLOCKS)]
    return {"looks": len(idx), "brier_from": round(float(a.mean()), 6), "brier_to": round(float(b.mean()), 6),
            "gain": round(float((a - b).mean()), 6), "z": round(float(_bz(blocks)), 2),
            "blocks": [round(x, 6) for x in blocks]}


def _clears(sc: dict) -> bool:
    """The adoption bar."""
    return float(sc["gain"]) >= CHANGE_MARGIN and float(sc["z"]) >= CHANGE_Z


def _candidate(recs: list[dict], settings: dict, k: int):
    """The k-th dream's candidate: (setting, value) -- a grid neighbour,
    rotating over the settings and both directions (the k-th turn first, then
    the next ones), never back to a value a setting left within the test's
    window; None when there is none."""
    order = [(s, d) for s in CHANGE_SETTINGS for d in (1, -1)]
    lines = [r for r in recs if r.get("kind") == "change"][-(CHANGE_WINDOW - 1):]
    left = {(r.get("setting"), r.get("from")) for r in lines if r.get("verdict") == "adopted"}
    for j in range(len(order)):
        s, d = order[(k + j) % len(order)]
        grid, cur = CHANGE_SETTINGS[s], settings.get(s)
        if cur not in grid:
            continue
        i = grid.index(cur) + d
        if 0 <= i < len(grid) and (s, grid[i]) not in left:
            return s, grid[i]
    return None


def _variant(settings: dict) -> tuple:
    return int(settings["lam_looks"]), float(settings["lam_prior_looks"])


def _dream(recs: list[dict], settings: dict, now: float, k: int) -> list[dict]:
    """ONE DREAM (#320) over a snapshot of the record (read only): the checks
    of earlier proposals that have CHANGE_LOOKS rate-read looks after them
    (scored on those looks alone), then one proposal -- its candidate scored
    against the current value on the last CHANGE_LOOKS rate-read looks, and
    adopted when it clears the bar. -> the lines to write ({"kind": "change"}
    / {"kind": "change_check"}); none when there is nothing to dream over."""
    idx = _rate_looks(recs)
    out, want, jobs = [], {}, []
    checked = {r.get("of") for r in recs if r.get("kind") == "change_check"}
    props = [r for r in recs if r.get("kind") == "change"][-2 * CHANGE_WINDOW:]
    for p in [p for p in props if p.get("ts") not in checked and p.get("setting") in CHANGE_SETTINGS][:CHANGE_PENDING]:
        after = [i for i in idx if float(recs[i]["ts"]) > float(p["ts"])][:CHANGE_LOOKS]
        if len(after) < CHANGE_LOOKS:
            continue
        base = {**settings, **(p.get("base") or {})}
        a, b = _variant({**base, p["setting"]: p["from"]}), _variant({**base, p["setting"]: p["to"]})
        jobs.append(("check", p, after, a, b))
    cand = _candidate(recs, settings, k)
    if cand is not None and len(idx) >= CHANGE_LOOKS:
        a, b = _variant(settings), _variant({**settings, cand[0]: cand[1]})
        jobs.append(("propose", cand, idx[-CHANGE_LOOKS:], a, b))
    for _, _, ii, a, b in jobs:
        want.setdefault(a, set()).update(ii)
        want.setdefault(b, set()).update(ii)
    got = _replay_at(recs, want) if jobs else {}
    for kind, what, ii, a, b in jobs:
        ii = [i for i in ii if got[a][i] is not None and got[b][i] is not None]
        if not ii:
            continue
        sc = _pair(recs, ii, got[a], got[b])
        if kind == "check":
            p = what
            line = {"ts": now, "kind": "change_check", "of": p["ts"], "setting": p["setting"], "from": p["from"],
                    "to": p["to"], "verdict": p.get("verdict"), "first": recs[ii[0]]["ts"], **sc}
            if p.get("verdict") == "adopted":
                line["held"] = sc["gain"] > 0
            else:
                line["miss"] = _clears(sc)
            out.append(line)
            continue
        s, v = what
        sc.pop("blocks")
        if sc["gain"] < 0:
            verdict, why = "rejected", "worse: the candidate predicted worse on these looks"
        elif sc["gain"] < CHANGE_MARGIN:
            verdict, why = "rejected", f"below the margin: gain {sc['gain']:+.4f} < {CHANGE_MARGIN}"
        elif sc["z"] < CHANGE_Z:
            verdict, why = "rejected", f"not significant: z {sc['z']:.1f} < {CHANGE_Z}"
        else:
            verdict, why = "adopted", f"better: gain {sc['gain']:+.4f} at z {sc['z']:.1f}"
        out.append({"ts": now, "kind": "change", "setting": s, "from": settings[s], "to": v,
                    "base": dict(settings), **sc, "verdict": verdict, "reason": why})
    return out


def _dream_apply(srv, out: dict, n: int, now: float) -> None:
    """The cycle writes what a finished dream found, and adopts (#320)."""
    if not _available(srv, CHANGE_RUNG):
        return
    for line in out.get("lines") or ():
        line = {**line, "ts": now, "n": n}
        if line["kind"] == "change":
            line["cost_s"] = round(float(out.get("cost_s") or 0.0), 3)
            cur = _settings(srv)
            if line["verdict"] == "adopted":
                if cur.get(line["setting"]) != line["from"]:          # the settings moved under it: never blind
                    line.update(verdict="rejected", reason="stale: the setting changed while it dreamt")
                else:
                    _save_settings(srv, {**cur, line["setting"]: line["to"]})
        _append(srv, line)


def _dream_tick(srv, n: int, now: float) -> None:
    """CHANGE ITSELF'S PRACTICE (#320), each cycle: write a finished dream's
    lines; on the beat (every CHANGE_EVERY cycles), while the skill is open
    and no dream is running, start one on a thread of its own over a snapshot
    of the record -- the cycle never waits for it."""
    rt = _rt(srv)
    done = rt.pop("dream_out", None)
    if done is not None:
        _dream_apply(srv, done, n, now)
    th = rt.get("dream")
    if th is not None and th.is_alive():
        return
    if n % CHANGE_EVERY or not _available(srv, CHANGE_RUNG):
        return
    recs = list(_read(srv))
    settings = dict(_settings(srv))
    k = _dreams(recs)

    def work():
        t = time.time()
        try:
            lines = _dream(recs, settings, now, k)
        except Exception as e:                              # noqa: BLE001
            import sys
            print(f"[self] dream failed ({e!r})", file=sys.stderr)
            lines = []
        rt["dream_out"] = {"lines": lines, "cost_s": time.time() - t}
    if DREAM_THREAD:
        rt["dream"] = threading.Thread(target=work, name="self-dream", daemon=True)
        rt["dream"].start()
    else:
        work()
        _dream_apply(srv, rt.pop("dream_out"), n, now)


# ---- the cycle and its guard --------------------------------------------------

def _runs(srv) -> bool:
    from .lanes import nerve_paused
    rt = _rt(srv)
    try:
        paused = bool(nerve_paused(srv))
    except Exception:
        paused = bool(getattr(srv, "paused", False))
    return rt["mode"] != "off" and not rt["held"] and not paused


def _guard_ok(rt: dict, now: float) -> bool:
    if rt["last"] is not None and now - rt["last"] < PACE_S:
        return False
    rt["burst"] = [t for t in rt["burst"] if now - t < 60.0]
    return len(rt["burst"]) < BURST_PER_MIN


def cycle(srv, now: float | None = None) -> dict | None:
    """One try -> expect -> notice. None when nothing ran (off, paused, held,
    the guard, or nowhere to stand: no current and no position yet).

    A STILL WORLD (#282): with no current (a fresh boot, nobody has spoken
    or played lately) self still cycles -- the current is where it stands,
    so the world pushes nothing. It keeps looking, playing and taking turns;
    the cycle line says `still: true` (change "still"), it learns nothing
    about the current from it, and the rung stats that read push / gap /
    naive skip it (rung 1 reads only change "world"; no aim lines)."""
    now = time.time() if now is None else now
    rt = _rt(srv)
    from . import energy as _E
    if not _runs(srv) or _E.asleep(srv) or not _guard_ok(rt, now):   # #345: asleep, no cycle
        return None
    cur = _current(srv)
    est = srv.entity_store
    ent = _entity(srv)
    pos = np.asarray(ent.position, dtype=float) if ent.position is not None else None
    still = cur is None
    if still:
        if pos is None or not len(pos):
            return None                                           # never born into a still world
        pos = _unit(pos)
        cur = pos                                                 # the world is still: no push
    elif pos is None or len(pos) != len(cur):
        pos = _unit(cur + _seed_dir("birthplace", len(cur)))      # born near the current, not on it
    rt["last"] = now
    rt["burst"].append(now)
    n = _cycles(_read(srv)) + 1
    seed = f"{n}"
    aim = None
    if 1 in _passed(_read(srv)):            # steer: earned by feeling itself
        if (rt.get("aim") is None or rt.get("aim_count", 0) >= AIM_HOLD
                or len(rt["aim"]) != len(pos)):
            # #333: once it nudges, an aim is a thing of its world's field spot, at random among
            # them (none on the field: a spot near where it is, as before, and nothing to nudge)
            spots = _spots(srv, len(pos)) if _practised(srv, "nudge") else {}
            if spots:
                import random
                eid = random.Random(f"aim:{n}").choice(sorted(spots))
                rt["aim"], rt["aim_eid"] = spots[eid], eid
            else:
                rt["aim"], rt["aim_eid"] = _unit(pos + AIM_REACH * _seed_dir(f"aim:{n}", len(pos))), None
            rt["aim_n"], rt["aim_count"] = n, 0
        rt["aim_count"] += 1
        aim = rt["aim"]
    from .night import method_call
    _ctx.cycle = {"srv": srv, "pos": pos, "current": cur, "aim": aim, "still": still}
    try:
        outs = []
        for m in ("expect", "try", "notice"):
            outs.append(method_call(srv, est, None, None, ent.eid, m, {"seed": seed}))
            if outs[-1].get("rc") != 0:
                break
        ok = all(o.get("rc") == 0 for o in outs)
        c = _ctx.cycle
    finally:
        _ctx.cycle = None
    if not ok or "observed" not in c:
        rt["fails"] += 1
        if rt["fails"] >= HOLD_AFTER:
            rt["held"] = True
        _append(srv, {"ts": now, "kind": "error", "n": n,
                      "why": next((str(o.get("head") or o.get("error") or "") for o in outs
                                   if o.get("rc") != 0), "")[:200]})
        return None
    rt["fails"] = 0
    gap = float(np.linalg.norm(c["observed"] - c["expected"]))
    naive = float(np.linalg.norm(c["observed"] - c["intent"]))
    rec = {"ts": now, "kind": "cycle", "n": n, "act": "move",
           "step": round(float(np.linalg.norm(c["mine"])), 4) if c.get("mine") is not None else STEP,
           "push": c["push"], "gap": gap, "naive": naive,
           "change": "still" if still else ("world" if c["push"] > CALM else "knowledge")}
    if still:
        rec["still"] = True                  # #282: no current -- the rung stats skip it
    elif aim is not None:
        # where it landed against its aim, and where the same stride would have
        # landed without leaning into the current (the world's push is the same)
        world = PUSH_GAIN * (cur - pos)
        naive_land = _unit(pos + c["naive_mine"] + world)
        rec.update({"aim_n": rt["aim_n"], "aim_err": float(np.linalg.norm(c["observed"] - aim)),
                    "aim_naive": float(np.linalg.norm(naive_land - aim))})
    if aim is not None and rt.get("aim_eid"):
        rec["aim_eid"] = rt["aim_eid"]       # #333: the thing it aims at (and nudges)
    _append(srv, rec)
    est.upsert(ent, embedder=None)
    _mark_rung7_restart(srv, now)
    _mark_glitch_fix(srv, now)
    if (aim is not None and rt.get("aim_eid") and _practised(srv, "nudge")
            and _closing(srv, rt["aim_eid"])):
        # #333: its own move nudges what it aims at -- only while it is getting closer to it
        _nudge_once(srv, n, now, rt["aim_eid"], c["mine"])
    if _available(srv, 2):
        _reach_once(srv, est, ent, n, now)
    if _available(srv, 9):
        _mirror_once(srv, n, now, c["observed"] - pos)      # #312: its move, to the mirror
    _dream_tick(srv, n, now)                                # #320: off the cycle's thread
    _open_rungs(srv)
    _unlock_toys(srv)
    _show(srv, ent, pos, c, rec)
    rt["wake"].set()                      # my state changed: that is what wakes the next try
    return rec


def _unlock_toys(srv) -> None:
    """#267: a pass that unlocks toys writes its unlock line and switches them
    on (toy_unlocks.sync -- after the rungs open; no rung's test reads it)."""
    from . import toy_unlocks as _TU
    _TU.sync(srv)


def _available(srv, rung: int) -> bool:
    """An achievement's skill may be practised (#295; by its test's rung
    number): built, self awake, and every prerequisite earned AND accepted
    (#299) -- not the rung below: siblings practise in any order."""
    i = RUNG_ID.get(rung)
    return i is not None and ACHIEVEMENTS[i]["built"] and i in _allowed(srv)


def _replies(srv) -> bool:
    """THE REPLY CHECKS (#295): after a push self looks for the other's reply,
    and a push records how long since the other last spoke -- the machinery
    both take turns (its reward) and understand an other (its outcome) read,
    so it runs while either may be practised."""
    return _available(srv, 7) or _available(srv, 8) or _available(srv, 11)   # #311: speak reads them too


def _in_world(srv, eid: str) -> bool:
    """#337: is this thing in Blurp's world? Every read Blurp makes asks."""
    from . import self_world as _SW
    try:
        return _SW.has(srv, eid)
    except Exception:
        return False


def _targets(srv) -> list[str]:
    """What Blurp can look at: the things in its world (#337) with a getter."""
    from .night import effective_script
    est = srv.entity_store
    out = []
    for eid, e in list((est.docs or {}).items()):
        if eid == _eid() or not _in_world(srv, eid):
            continue
        try:
            code, _ = effective_script(est, e, "getter")
        except Exception:
            code = None
        if code:
            out.append(eid)
    return sorted(out)


def _reach_once(srv, est, ent, n: int, now: float) -> None:
    """Rung 2's practice: expect -> reach -> notice on one thing, then the
    blind audit's bookkeeping."""
    import random
    _audit_tick(srv, now)
    targets = _targets(srv)
    if not targets:
        return
    steer = _available(srv, 3)
    timed = _available(srv, 4)
    recs = _read(srv)
    kinds = _kinds(recs) if steer else {}
    rng = random.Random(f"reach:{n}")
    lam = None
    reply = _pending(recs, now) if _replies(srv) else None    # #295: for turns or expect
    if reply is not None and reply["to"] not in targets:
        reply = None
    if reply is not None and _hold_reply(srv, reply, now):
        targets = [e for e in targets if e != reply["to"]]   # wait for the reply before looking
        reply = None
        if not targets:
            return
    if reply is not None:                          # rung 7: after I speak I check for the reply
        eid = reply["to"]
        lam = _lam_of(srv, recs, eid) if timed else None
    elif timed:                                    # rung 4: look when a change has become likely
        eid, lam = _due(srv, recs, targets, kinds, now, rng)
        if eid is None:
            rt = _rt(srv)
            rt["waits"] = rt.get("waits", 0) + 1
            if rt["waits"] % WAIT_EVERY == 1:
                _append(srv, {"ts": now, "kind": "wait", "n": n})
            return
    else:
        tilt = _curiosity(recs, targets)           # #212: learning progress tilts the base weights
        if steer:                                  # rung 3: act on the sort -- silent things rarely
            weights = [(SILENT_WEIGHT if kinds.get(e) == "silent" else 1.0) * tilt[e] for e in targets]
        else:
            weights = [tilt[e] for e in targets]
        eid = rng.choices(targets, weights=weights)[0]
    last = (_tempo(srv).get(eid) or {}).get("last")
    au = _rt(srv).get("audit")
    inside = bool(au and au.get("rung", 2) == 2 and au["eid"] == eid)
    in5 = bool(au and au.get("rung") == 5 and au["eid"] == eid)
    from .night import method_call
    _ctx.cycle = {"srv": srv, "act": "reach", "eid": eid, "audit": inside, "now": now, "reply": reply,
                  "lam": lam, "dt": (now - float(last)) if last is not None else None}
    try:
        outs = []
        for m, args in (("expect", {"seed": str(n)}), ("reach", {"target": eid}), ("notice", {"seed": str(n)})):
            outs.append(method_call(srv, est, None, None, ent.eid, m, args))
            if outs[-1].get("rc") != 0:
                break
        c = _ctx.cycle
    finally:
        _ctx.cycle = None
    if "answer" not in c:
        _append(srv, {"ts": now, "kind": "error", "n": n, "act": "reach",
                      "why": next((str(o.get("head") or o.get("error") or "") for o in outs
                                   if o.get("rc") != 0), "")[:200]})
        return
    rec = {"ts": now, "kind": "reach", "n": n, "eid": eid, "p": round(c["p"], 4),
           "answer": c["answer"], "why": c["why"],
           "dt": (round(now - float(last), 3) if last is not None else None)}
    try:
        from .eids import field_id
        rec["fid"] = field_id(eid)                       # #244: where the page finds it on the field
    except Exception:
        pass
    if c.get("toy") and c["toy"].get("at1") is not None:  # #244: who changed the toy (ground truth)
        try:
            rec["by"] = _toy_by(recs, eid, c["toy"])
            rec["log_at"] = c["toy"]["at1"]
        except Exception:
            pass
    if reply is not None:                                # rung 7: the reply check after a push
        rec["reply"] = True
        rec["replied"] = _replied(srv, reply, now, c["answer"])   # #258: they spoke AFTER the push
        if _medium(reply) != "chat":
            rec["medium"] = _medium(reply)                       # #264: a check of a turn with the ball
        if _clock_of(reply) != 1:
            rec["clock"] = _clock_of(reply)                      # #278: ...on the ball's own clock
    if c.get("p_self") is not None:                      # rung 8: self's own read (#254)
        rec["p_self"] = round(c["p_self"], 4)
    if c.get("p_llm") is not None:                       # rung 8: the advisor's read, same look
        rec["p_llm"] = round(c["p_llm"], 4)
        if c.get("llm_model"):
            rec["llm_model"] = c["llm_model"]
        rec["llm_cal"] = list(c["llm_cal"])
        rec["p_mix"] = round(c["p_mix"], 4)
    if timed and reply is None:                          # a reply check is not a patience-timed look
        rec["timed"] = True
        if lam:
            rec["lam"] = round(lam, 6)
    if steer:
        rec["steer"] = True
        pp = ent.properties or {}
        sd = dict(pp.get("script_data") or {})
        sd["kinds"] = {e: k for e, k in kinds.items() if k != "unknown"}
        pp["script_data"] = sd
        ent.properties = pp
    if _available(srv, 6):
        pp = ent.properties or {}
        sd = dict(pp.get("script_data") or {})
        sd["split"] = {e: s for e, s in _split(recs, _tempo(srv)).items() if s != "unknown"}
        pp["script_data"] = sd
        ent.properties = pp
    if inside:
        rec["audit"] = True
        if c["answer"] != "none":
            au["reaches"] += 1
            au["changes"] += int(c["answer"] == "changed")
    watch = _available(srv, 5)
    if watch:
        rec["watch5"] = True
    if in5:
        rec["audit5"] = au["phase"]
        if c["answer"] != "none":
            au["looks"] += 1
    _append(srv, rec)
    _place_once(srv, eid)
    if watch and c["answer"] != "none" and not inside:
        _shift_check(srv, eid, now)
    _audit_tick(srv, now)
    _audit5_tick(srv, now)


def _lam(recs: list[dict], eid: str, looks: int | None = None, prior: float | None = None) -> float | None:
    """A thing's rate of change in time: the maximum-likelihood lambda for
    P(changed | dt) = 1 - exp(-lambda dt) over its last LAM_LOOKS answered
    looks (outside audits); None before it has any. After a rung-5 shift the
    fit reads only the looks since it, but not from nothing (#235): the rate
    before the shift -- the thing's own last LAM_LOOKS looks, or the seat's when
    it has none -- is a prior worth LAM_PRIOR_LOOKS looks, so 3-5 looks alone can't
    run lambda to the grid's edge and the new looks lead after ~10. #320:
    `looks` and `prior` stand in for LAM_LOOKS and LAM_PRIOR_LOOKS (what
    change itself adopted: _lam_of); None reads the constants."""
    W = LAM_LOOKS if looks is None else int(looks)
    K = LAM_PRIOR_LOOKS if prior is None else float(prior)
    since = _last_shift_ts(recs, eid)
    fit = [(r["eid"], r["ts"], float(r["dt"]), r["answer"] == "changed") for r in recs
           if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5")
           and r.get("answer") in ("changed", "unchanged") and r.get("dt")]
    mine = [o for o in fit if o[0] == eid]
    obs = [o for o in mine if since is None or o[1] > since][-W:]
    if not obs:
        return None
    ll = _lam_ll(obs)
    if since is not None:
        old = ([o for o in mine if o[1] <= since] or [o for o in fit if o[1] <= since])[-W:]
        if old:                                    # + half a look each way (Jeffreys): peaks inside the grid
            ll = ll + _lam_prior_ll(eid, old, K)
    return float(LAM_GRID[int(np.argmax(ll))])


def _lam_prior_ll(eid: str, old: list[tuple], k: float) -> np.ndarray:
    """#235's prior: the rate before a shift, worth k looks, plus half a
    changed and half an unchanged look at the old looks' median dt."""
    mid = float(np.median([o[2] for o in old]))
    half = 0.5 * (_lam_ll([(eid, 0, mid, True)]) + _lam_ll([(eid, 0, mid, False)]))
    return k * (_lam_ll(old) + half) / (len(old) + 1)


def _lam_of(srv, recs: list[dict], eid: str) -> float | None:
    """The live rate fit (#320): _lam at the settings change itself adopted."""
    s = _settings(srv)
    return _lam(recs, eid, looks=s["lam_looks"], prior=s["lam_prior_looks"])


def _lam_ll(obs: list[tuple]) -> np.ndarray:
    """The log-likelihood of (eid, ts, dt, changed) looks at each LAM_GRID rate."""
    dt = np.array([o[2] for o in obs])[None, :]
    ch = np.array([o[3] for o in obs], dtype=float)[None, :]
    p = np.clip(1.0 - np.exp(-LAM_GRID[:, None] * dt), 1e-9, 1 - 1e-9)
    return (ch * np.log(p) + (1 - ch) * np.log(1 - p)).sum(axis=1)


def _last_shift_ts(recs: list[dict], eid: str):
    return next((r["ts"] for r in reversed(recs) if r.get("kind") == "shift" and r.get("eid") == eid), None)


def _tail(p: float, w: int, k: int) -> float:
    """The chance of a window at least this far out under the long memory:
    P(X >= k) when it came out high, P(X <= k) when low; X ~ Binomial(w, p)."""
    import math
    pmf = [math.comb(w, i) * p ** i * (1 - p) ** (w - i) for i in range(w + 1)]
    return sum(pmf[k:]) if k / w > p else sum(pmf[:k + 1])


def _shift_check(srv, eid: str, now: float) -> None:
    """RUNG 5's noticing: are my last few looks at this thing both far from and
    unlikely under my long memory of it? Then the world changed: record the
    shift and re-learn from those looks. A window holding a look whose
    interval spans a record gap (_stalls: a restart or a stall) is no evidence
    (#266): the skip is the glitch's, so no shift and no rate refit."""
    recs = _read(srv)
    since = _last_shift_ts(recs, eid)
    looks, spans, at = [], [], None           # #266: does each look's interval span a record gap?
    for r, st in zip(recs, _stalls(recs)):
        if (r.get("kind") == "reach" and r.get("eid") == eid and not r.get("audit")
                and r.get("answer") in ("changed", "unchanged")):
            if since is None or r["ts"] > since:
                looks.append(r["answer"] == "changed")
                spans.append(at is not None and st != at)
            at = st
    t = _tempo(srv).get(eid) or {}
    C, A = float(t.get("changed", 0)), float(t.get("answered", 0))
    for w in SHIFT_WINDOWS:
        if len(looks) < w or A - w < 1:
            continue
        if any(spans[-w:]):                      # a restart/stall in the window: the glitch's, no evidence
            continue
        k = sum(looks[-w:])
        p = (C - k + 0.5) / (A - w + 1.0)            # the long memory, without the window
        if abs(k / w - p) > SHIFT_GAP and _tail(p, w, k) < SHIFT_P:
            _append(srv, {"ts": now, "kind": "shift", "eid": eid, "was": round(p, 3),
                          "now": round(k / w, 3), "looks": w})
            t.update({"changed": k, "answered": w})  # re-learn from the looks that showed it
            t.pop("after", None)                     # the one bit of memory restarts with the new world
            _save_json(srv, "tempo.json", _tempo(srv))
            au = _rt(srv).get("audit")
            if au and au.get("rung") == 5 and au["eid"] == eid:
                au["shifts"].append({"phase": au["phase"], "down": k / w < p, "at": au["looks"]})
            return


def _audit5_tick(srv, now: float) -> None:
    """RUNG 5'S BLIND AUDIT (stop the source, then restart it): a moving
    thing's getter is paused while self looks at it AUDIT5_LOOKS times, then
    restarted for AUDIT5_LOOKS more. Self isn't told -- and here it DOES learn
    (re-learning is the point). It passes if self flagged "went still" during
    the pause and "moves again" after it."""
    from . import watch_agents as _WA
    rt = _rt(srv)
    au = rt.get("audit")
    if au is not None:
        if au.get("rung") != 5:
            return
        if au["phase"] == "pause" and (au["looks"] >= AUDIT5_LOOKS or time.time() >= au["until"]):
            _WA.unpause_getter(au["eid"])
            au.update({"phase": "resume", "paused_looks": au["looks"], "looks": 0,
                       "until": time.time() + AUDIT5_RESUME_S})
            _append(srv, {"ts": now, "kind": "audit", "rung": 5, "phase": "resume", "eid": au["eid"]})
        elif au["phase"] == "resume":
            again = any(not s["down"] for s in au["shifts"] if s["phase"] == "resume" and s["at"] <= AUDIT5_LOOKS)
            if again or au["looks"] >= AUDIT5_LOOKS or time.time() >= au["until"]:
                still = any(s["down"] for s in au["shifts"] if s["phase"] == "pause" and s["at"] <= AUDIT5_LOOKS)
                full = au["paused_looks"] >= AUDIT5_LOOKS and (again or au["looks"] >= AUDIT5_LOOKS)
                rt["audit"] = None
                _append(srv, {"ts": now, "kind": "audit", "rung": 5, "phase": "end", "eid": au["eid"],
                              "still": still, "again": again,
                              "passed": (still and again) if full else None})
        return
    if not _available(srv, 5):
        return
    recs = _read(srv)
    if any(r.get("kind") == "audit" and r.get("rung") == 5 and r.get("phase") == "end"
           and r.get("passed") is True for r in recs):
        return
    if sum(1 for r in recs if r.get("watch5")) < RUNG5_LOOKS:
        return
    moving = [e for e, k in _kinds(recs).items() if k == "moving"]
    if not moving:
        return
    eid = max(sorted(moving), key=lambda e: _p_changed(srv, e))   # the surest mover: its stop is unmistakable
    until = time.time() + AUDIT5_PAUSE_S
    _WA.pause_getter(eid, until)
    rt["audit"] = {"rung": 5, "eid": eid, "phase": "pause", "looks": 0, "until": until, "shifts": []}
    _append(srv, {"ts": now, "kind": "audit", "rung": 5, "phase": "pause", "eid": eid})


def _due(srv, recs, targets, kinds, now, rng):
    """Rung 4's choice: the things whose next change has become likely
    (PATIENCE_P), or a silent one now and then; (None, None) -- wait. Among
    the due (#266), each is weighed by what the look can teach -- the entropy
    of the p self would predict for it (_info), floored at INFO_FLOOR -- times
    the curiosity tilt. The two are separate questions: the entropy asks how
    unsure the answer to THIS look is, the tilt whether looks at the thing have
    lately been turning surprise into a better model. Their product is the
    learning self expects from the look; either floor keeps a thing in play."""
    tempo = _tempo(srv)
    due = []
    for e in targets:
        if kinds.get(e) == "silent":
            continue
        last = (tempo.get(e) or {}).get("last")
        lam = _lam_of(srv, recs, e)
        if last is None or lam is None:
            due.append((e, lam))
            continue
        wait = min(PATIENCE_MAX_S, max(PACE_S, -np.log(1.0 - PATIENCE_P) / lam))
        if now - float(last) >= wait:
            due.append((e, lam))
    silent = [e for e in targets if kinds.get(e) == "silent"]
    if silent and rng.random() < SILENT_WEIGHT:
        return rng.choice(silent), None
    if not due:
        return None, None
    tilt = _curiosity(recs, [e for e, _ in due])   # #212: among the due, the frontier first
    if not ATTENTION:
        return rng.choices(due, weights=[tilt[e] for e, _ in due])[0]
    weights = [max(INFO_FLOOR, _info(_due_p(srv, tempo, e, lam, now))) * tilt[e] for e, lam in due]
    return rng.choices(due, weights=weights)[0]   # #266: what the look can teach x the frontier


def _due_p(srv, tempo, eid, lam, now) -> float:
    """The p self would predict for a look at this thing now -- the same as
    _expect's: the rate's 1 - exp(-lambda dt), or the tempo's share before a
    rate (or a first look)."""
    last = (tempo.get(eid) or {}).get("last")
    if lam and last is not None:
        return 1.0 - float(np.exp(-lam * (now - float(last))))
    return _p_changed(srv, eid)


def _info(p: float) -> float:
    """WHAT A LOOK CAN TEACH (#266): the binary entropy of its predicted p, in
    bits -- the surprise self expects from the answer when p is calibrated
    (rung 4 checks it is). 1 at p = 0.5; ~0 at p ~ 0 or 1: a look whose answer
    self already knows teaches nothing."""
    p = min(1.0, max(0.0, float(p)))
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return float(-(p * np.log2(p) + (1.0 - p) * np.log2(1.0 - p)))


# ---- curiosity: where self practises (#212) ----------------------------------

def _ordinary_errors(recs: list[dict]) -> dict:
    """eid -> the Brier errors (answer - p)^2 of its ORDINARY looks, oldest
    first: answered reaches outside every audit. A look inside a rung-2 audit
    (`audit`) or either phase of a rung-5 audit (`audit5`: the paused source and
    its restart) never counts -- self stays blind to its audits (#161 rule 2).
    Nor does a look whose interval -- since the same thing's previous look --
    spans a gap between consecutive record lines longer than CURIO_GAP_S: the
    seat restarted or stalled, so the miss is the glitch's, not the thing's
    (#259). Read from the record's timestamps only."""
    out: dict[str, list] = {}
    seen = {}                               # per thing, the stall count at its last look
    for r, stalls in zip(recs, _stalls(recs)):
        if (r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged")
                and not r.get("audit") and not r.get("audit5") and r.get("p") is not None):
            eid = r["eid"]
            spans = eid in seen and seen[eid] != stalls
            seen[eid] = stalls
            if spans:
                continue
            x = 1.0 if r["answer"] == "changed" else 0.0
            out.setdefault(eid, []).append((x - float(r["p"])) ** 2)
    return out


def _stalls(recs: list[dict]) -> list[int]:
    """For each record line, how many gaps longer than CURIO_GAP_S between
    consecutive timestamped lines came before it (#259): a look whose count
    differs from its thing's previous look spans a restart or a stall. Read
    from the record's timestamps only (no boot marker); shared by curiosity
    and rung 5's noticing (#266)."""
    out, n, prev = [], 0, None
    if recs and recs[0].get("kind") == SUMMARY:       # #345: the summary's lines carry their counts
        st = [int(x) for x in recs[0].get("stalls") or ()]
        out = [st[0] if st else 0] + st
        n = st[-1] if st else 0
        prev = next((float(r["ts"]) for r in reversed(recs[1:1 + len(st)]) if r.get("ts") is not None), None)
        recs = recs[1 + len(st):]
    for r in recs:
        ts = r.get("ts")
        if ts is not None:
            ts = float(ts)
            if prev is not None and ts - prev > CURIO_GAP_S:
                n += 1
            prev = ts
        out.append(n)
    return out


def _window_error(errs: list) -> float:
    """A window's error: the mean of its middle half (CURIO_TRIM off each end,
    #259). A miss rarer than one look in four can't move it, so a mastered
    thing's rare glitch ageing out is no progress; a thing that is often wrong
    still is, and its learning still shows."""
    s = sorted(errs)
    k = int(len(s) * CURIO_TRIM)
    mid = s[k:len(s) - k] or s
    return sum(mid) / len(mid)


def _progress(recs: list[dict], detail: bool = False) -> dict:
    """LEARNING PROGRESS per thing: its mean error over the CURIO_WINDOW
    ordinary looks before the latest CURIO_WINDOW, minus its mean error over
    the latest ones (positive: its prediction is getting better). The window
    means are the smoothing; each is an interquartile mean (_window_error). A thing with fewer than two windows of looks has
    none yet. With `detail`, {eid: {progress, error}} (error: the recent mean).
    Computed from the record at the moment it is read -- never stored."""
    W = CURIO_WINDOW
    out = {}
    for eid, errs in _ordinary_errors(recs).items():
        if len(errs) < 2 * W:
            continue
        early, late = _window_error(errs[-2 * W:-W]), _window_error(errs[-W:])
        out[eid] = {"progress": early - late, "error": late} if detail else early - late
    return out


def _curiosity(recs: list[dict], targets: list[str]) -> dict:
    """THE TILT on the base weights (kinds at rung 3, patience at rung 4):
    each target's multiplier, by the RISE of its bar (#161 rule 1) -- the
    fastest-improving thing gets 1, a flat one (mastered or unlearnable) or a
    worsening one the floor CURIO_FLOOR, so it is still retried now and then; a
    thing too young to have progress gets 1 (worth a look). With no progress
    anywhere above CURIO_MIN, or CURIOSITY off, every multiplier is 1: the base
    chooser runs as it did. Read only when self chooses (rule 3)."""
    if not CURIOSITY:
        return {e: 1.0 for e in targets}
    lp = _progress(recs)
    rise = {e: max(0.0, lp[e]) for e in targets if e in lp}
    top = max(rise.values(), default=0.0)
    if top < CURIO_MIN:
        return {e: 1.0 for e in targets}
    return {e: (CURIO_FLOOR + (1.0 - CURIO_FLOOR) * rise[e] / top) if e in rise else 1.0
            for e in targets}


def _audit_tick(srv, now: float) -> None:
    """THE BLIND AUDIT (stop the source): a candidate's getter is paused until
    I have reached for it AUDIT_REACHES more times; a change recorded while
    the source is stopped is a mark that can't fail -- the audit fails. Self
    isn't told: its reaches answer "unchanged", and it learns nothing inside."""
    from . import watch_agents as _WA
    rt = _rt(srv)
    au = rt.get("audit")
    if au is not None and au.get("rung", 2) != 2:
        return                             # another rung's audit holds the slot
    if au is not None:
        done = au["reaches"] >= AUDIT_REACHES
        if done or time.time() >= au["until"]:
            _WA.unpause_getter(au["eid"])
            rt["audit"] = None
            _append(srv, {"ts": now, "kind": "audit", "phase": "end", "eid": au["eid"],
                          "reaches": au["reaches"], "changes": au["changes"],
                          "passed": (au["changes"] == 0) if done else None})
        return
    recs = _read(srv)
    if 2 in _passed(recs):
        return                             # rung 2 is earned: no more pausing getters for it
    audited = {r["eid"] for r in recs if r.get("kind") == "audit" and r.get("phase") == "end"
               and r.get("passed") is not None}
    for eid, st in _rung2_stats(recs, _tempo(srv)).items():
        if eid in audited or not st["candidate"] or not st["auditable"] or not _in_world(srv, eid):
            continue
        until = time.time() + AUDIT_MAX_S
        _WA.pause_getter(eid, until)
        rt["audit"] = {"eid": eid, "until": until, "reaches": 0, "changes": 0, "rung": 2}
        _append(srv, {"ts": now, "kind": "audit", "phase": "start", "eid": eid, "rung": 2})
        return


def _pull(srv, dim: int):
    """Where it has learned the current pulls: the fixed point of
    push ~ b - g*pos (b = mean push + g*mean position), or None."""
    m = _model(srv, dim)
    if m is None or float(m.get("var") or 0.0) <= 1e-9:
        return None
    g = -float(m["cov"]) / float(m["var"])
    if g <= 1e-6:
        return None
    return _unit(np.asarray(m["mw"]) / g + np.asarray(m["mp"]))


def _show(srv, ent, pos, c, rec) -> None:
    """The cycle onto the viewer (fieldpulse.self_cycle): the points as
    vectors for the beat to place, the rest as numbers -- never text."""
    try:
        from . import fieldpulse as _FP
        fp = _FP.of(srv)
        if fp is None:
            return
        st = status(srv)
        fp.self_cycle({"from": pos, "intent": c["intent"], "expected": c["expected"],
                       "at": c["observed"], "pull": _pull(srv, len(pos)), "aim": c.get("aim")},
                      {"n": rec["n"], "gap": rec["gap"], "naive": rec["naive"], "push": rec["push"],
                       "rung": st["rung"], "level": st["level"], "bar": st["bar"], "skill": st["skill"],
                       "mode": st["mode"], "slipping": st["slipping"], "waiting_on": st["waiting_on"],
                       "next": st["next"], "cycles": st["cycles"], "held": st["held"],
                       "rung_name": st["rung_name"], "limit": st["limit"], "awaiting": st["awaiting"],
                       "split": st.get("split"), "meets": st.get("meets"),
                       "steer": (st.get("steer") or {}).get("skill")})
    except Exception:
        pass


# ---- the ladder and the exp bar ----------------------------------------------

def _rung1(recs: list[dict], tempo: dict | None = None) -> dict:
    pushed = [r for r in recs if r.get("kind") == "cycle" and r.get("change") == "world"][-RUNG1_CYCLES:]
    nv = sum(r["naive"] for r in pushed)
    skill = (1.0 - sum(r["gap"] for r in pushed) / nv) if nv > 0 else 0.0
    crit = {"pushes": min(1.0, len(pushed) / RUNG1_CYCLES),
            "skill": max(0.0, min(1.0, skill / RUNG1_SKILL))}
    return {"crit": crit, "skill": round(skill, 4), "passed": all(v >= 1.0 for v in crit.values())}


def _rung2_stats(recs: list[dict], tempo: dict) -> dict:
    """Per thing, over its last RUNG2_REACHES answered reaches (outside any
    audit): the Brier skill of my predictions against the seat-wide change
    rate, and whether it moves often enough to be audited."""
    ans = [r for r in recs if r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged")
           and not r.get("audit")]
    if not ans:
        return {}
    pool = ans[-RUNG2_REACHES * 4:]
    p0 = sum(r["answer"] == "changed" for r in pool) / len(pool)
    out = {}
    for eid in sorted({r["eid"] for r in ans}):
        mine = [r for r in ans if r["eid"] == eid][-RUNG2_REACHES:]
        a = [1.0 if r["answer"] == "changed" else 0.0 for r in mine]
        brier = sum((x - r["p"]) ** 2 for x, r in zip(a, mine))
        base = sum((x - p0) ** 2 for x in a)
        skill = (1.0 - brier / base) if base > 1e-9 else 0.0
        t = tempo.get(eid) or {}
        p_hat = (float(t.get("changed", 0)) + 0.5) / (float(t.get("answered", 0)) + 1.0)
        out[eid] = {"reaches": len(mine), "skill": round(skill, 4), "p": round(p_hat, 3),
                    "candidate": len(mine) >= RUNG2_REACHES and skill >= RUNG2_SKILL,
                    "auditable": p_hat >= AUDIT_P}
    return out


def _rung2(recs: list[dict], tempo: dict | None = None) -> dict:
    stats = _rung2_stats(recs, tempo or {})
    audits = {r["eid"]: r["passed"] for r in recs
              if r.get("kind") == "audit" and r.get("phase") == "end" and r.get("passed") is not None}
    if not stats:
        return {"crit": {"reaches": 0.0, "skill": 0.0, "audit": 0.0}, "skill": 0.0, "passed": False}
    def rank(eid):
        s = stats[eid]
        return (audits.get(eid) is True, s["auditable"], s["candidate"], s["skill"], s["reaches"])
    best = max(stats, key=rank)
    s = stats[best]
    crit = {"reaches": min(1.0, s["reaches"] / RUNG2_REACHES),
            "skill": max(0.0, min(1.0, s["skill"] / RUNG2_SKILL)),
            "audit": 1.0 if audits.get(best) is True else 0.0}
    return {"crit": crit, "skill": s["skill"], "eid": best,
            "passed": all(v >= 1.0 for v in crit.values())}


def _kinds(recs: list[dict], window: int = RUNG3_WINDOW) -> dict:
    """What each thing is, from my own looks (outside audits), over its last
    `window` reaches: silent (no answer), moving, still, intermittent -- or
    unknown, before KIND_MIN looks."""
    by: dict[str, list] = {}
    for r in recs:
        if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5"):
            by.setdefault(r["eid"], []).append(r["answer"])
    out = {}
    for eid, ans in by.items():
        ans = ans[-window:]
        none = sum(a == "none" for a in ans)
        answered = len(ans) - none
        if len(ans) >= KIND_MIN and none / len(ans) >= 0.8:
            out[eid] = "silent"
        elif answered >= KIND_MIN:
            p = sum(a == "changed" for a in ans) / answered
            out[eid] = "moving" if p >= 0.6 else ("still" if p <= 0.2 else "intermittent")
        else:
            out[eid] = "unknown"
    return out


def _kind_rate(recs: list[dict], window: int = RUNG3_WINDOW) -> dict:
    """Each thing's RATE over its last `window` reaches (outside audits): the
    no-answer share and the change share among the answered (None before any
    answer). {} before KIND_MIN looks. The kind label is `_kinds`; the rate is
    what the two halves must agree on (a look's drift is not a new kind)."""
    by: dict[str, list] = {}
    for r in recs:
        if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5"):
            by.setdefault(r["eid"], []).append(r["answer"])
    out = {}
    for eid, ans in by.items():
        ans = ans[-window:]
        if len(ans) < KIND_MIN:
            continue
        none = sum(a == "none" for a in ans)
        answered = len(ans) - none
        out[eid] = {"none": none / len(ans),
                    "p": (sum(a == "changed" for a in ans) / answered) if answered else None}
    return out


def _close(a: float | None, b: float | None) -> bool:
    """Two rates agree within a look's drift; a silent half on both sides."""
    return (abs(a - b) <= RUNG3_DRIFT) if (a is not None and b is not None) else (a is None and b is None)


def _split(recs: list[dict], tempo: dict | None = None) -> dict:
    """THE SELF/OTHER SPLIT (rung 6): each thing self predicts near-perfectly
    (Brier skill >= BODY_SKILL -- zero gap, its own echo) is its BODY; what it
    can't predict (answers variably or with delay -- a nonzero gap) is an OTHER;
    fewer than KIND_MIN answered looks is unknown."""
    out = {}
    for eid, s in _rung2_stats(recs, tempo or {}).items():
        if s["reaches"] < KIND_MIN:
            out[eid] = "unknown"
        else:
            out[eid] = "body" if s["skill"] >= BODY_SKILL else "other"
    return out


def _rung3(recs: list[dict], tempo: dict | None = None) -> dict:
    """Tell things apart: sorted reaches (looks), kinds whose RATES agree across
    the two halves of the last RUNG3_WINDOW reaches, the no-answer share under
    RUNG3_FOCUS (focus), and rung 2's skill still holding -- over the moving
    (auditable) things only, so a still counter can't carry the bar."""
    reaches = [r for r in recs if r.get("kind") == "reach" and not r.get("audit")]
    steered = [r for r in reaches if r.get("steer")]
    looks = min(1.0, len(steered) / RUNG3_LOOKS)
    last = reaches[-RUNG3_WINDOW:]
    half = len(last) // 2
    ra, rb = _kind_rate(last[:half]), _kind_rate(last[half:])
    stable = [e for e in ra if e in rb and _close(ra[e]["p"], rb[e]["p"]) and abs(ra[e]["none"] - rb[e]["none"]) <= RUNG3_DRIFT]
    kinds = _kinds(last)
    agree = bool(stable) and len({kinds.get(e) for e in stable} - {None, "unknown"}) >= 2
    tail = steered[-RUNG3_LOOKS:]
    share = (sum(r["answer"] == "none" for r in tail) / len(tail)) if tail else 1.0
    focus = 1.0 if share < RUNG3_FOCUS else max(0.0, (1.0 - share) / (1.0 - RUNG3_FOCUS))
    r2 = _rung2_stats(recs, tempo or {})
    best = max((s["skill"] for s in r2.values() if s["auditable"]), default=0.0)
    crit = {"looks": looks, "kinds": 1.0 if agree else 0.0, "focus": focus,
            "skill": max(0.0, min(1.0, best / RUNG2_SKILL))}
    return {"crit": crit, "skill": round(best, 4), "passed": all(v >= 1.0 for v in crit.values())}


def _rung4(recs: list[dict], tempo: dict | None = None) -> dict:
    """Patience: enough timed looks; they find news -- at least half the
    time, or 0.15 more often than its fixed-pace looks at the same things did
    (whichever is easier: a thing already newsy only has to stay so); and its
    predictions are calibrated (mean predicted chance within RUNG4_CALIB of
    the share that found news)."""
    ans = [r for r in recs if r.get("kind") == "reach" and not r.get("audit")
           and r.get("answer") in ("changed", "unchanged")]
    timed = [r for r in ans if r.get("timed")]
    if not timed:
        return {"crit": {"timed": 0.0, "news": 0.0, "calibration": 0.0}, "skill": 0.0, "passed": False}
    last = timed[-RUNG4_LAST:]
    news = sum(r["answer"] == "changed" for r in last) / len(last)
    same = {r["eid"] for r in last}
    fixed = [r for r in ans if not r.get("timed") and r["eid"] in same]
    cf = _carry(recs).get("fixed") or {}              # #345: the fixed-pace looks archived before the summary
    nf = len(fixed) + sum(int((cf.get(e) or (0, 0))[0]) for e in same)
    kf = sum(r["answer"] == "changed" for r in fixed) + sum(int((cf.get(e) or (0, 0))[1]) for e in same)
    base = (kf / nf) if nf else 0.0
    need = min(0.5, base + 0.15)
    err = abs(sum(r["p"] for r in last) / len(last) - news)
    crit = {"timed": min(1.0, len(timed) / RUNG4_TIMED),
            "news": 1.0 if news >= need else max(0.0, news / need),
            "calibration": 1.0 if err <= RUNG4_CALIB else max(0.0, 1.0 - (err - RUNG4_CALIB) / 0.4)}
    return {"crit": crit, "skill": round(news, 4), "passed": all(v >= 1.0 for v in crit.values())}


def _rung5(recs: list[dict], tempo: dict | None = None) -> dict:
    """Notice the world changed: enough looks with noticing on, then the
    blind audit (went still during the pause, moves again after it). Its skill
    is the share of those two shifts it has noticed, 0..1 like the other rungs."""
    looks = sum(1 for r in recs if r.get("watch5"))
    ends = [r for r in recs if r.get("kind") == "audit" and r.get("rung") == 5 and r.get("phase") == "end"]
    ok = any(r.get("passed") is True for r in ends)
    crit = {"looks": min(1.0, looks / RUNG5_LOOKS), "audit": 1.0 if ok else 0.0}
    shifts = len([r for r in recs if r.get("kind") == "shift"])
    return {"crit": crit, "skill": min(1.0, shifts / RUNG5_SHIFTS),
            "passed": all(v >= 1.0 for v in crit.values())}


def _other(recs: list[dict]) -> str | None:
    """The other self is meeting now: the latest push's `to` (None before any)."""
    return next((r.get("to") for r in reversed(recs) if r.get("kind") == "push"), None)


def _rung6(recs: list[dict], tempo: dict | None = None) -> dict:
    """Meet an other: enough pushes to chat:out, enough answered looks at the
    answerer, and the answerer classed OTHER -- not body (a perfect echo is
    self's own echo, so the split reads 0 and the rung stays shut).

    It reads the CURRENT other only -- the latest push's `to` (#208): when the
    person changes, pushes to and looks at the previous one stop counting, so
    a rung passed against someone else slips until this person earns it.

    #264, CHAT GOES QUIET: once self's turns are with the ball, rung 6 -- met
    in chat -- reads its record AS IT STOOD AT THE SWITCH (the lines before
    the first turn with the ball). Nothing after it can slip it: self sends
    no more lines by design, and the person's chat may fall silent too, and a
    silent chat:in is predicted near-perfectly -- it would read "body", not
    "other", which is the silence, not a regression. A seat that was
    slipping at the switch stays slipping (the record isn't rewritten)."""
    first_toy = next((i for i, r in enumerate(recs) if r.get("kind") == "push" and _medium(r) == "toy"), None)
    if first_toy is not None:
        recs = recs[:first_toy]
    to = _other(recs)
    pushes = [r for r in recs if r.get("kind") == "push" and r.get("to") == to]
    heard = sum(1 for r in recs if r.get("kind") == "reach" and r.get("eid") == to
                and r.get("answer") in ("changed", "unchanged") and not r.get("audit"))
    split = _split(recs, tempo or {}).get(to or "", "unknown")
    crit = {"pushes": min(1.0, len(pushes) / RUNG6_PUSHES),
            "heard": min(1.0, heard / RUNG6_HEARD),
            "split": 1.0 if split == "other" else 0.0}
    return {"crit": crit, "skill": 1.0 if split == "other" else 0.0,
            "eid": to, "passed": all(v >= 1.0 for v in crit.values())}


def _rung7(recs: list[dict], tempo: dict | None = None) -> dict:
    """Take turns (#258): self's pushes land in the window it learned gets
    replies (a) more often than a push at a shuffled moment would, and (b)
    get more replies than a timing-blind push would -- over its last
    RUNG7_PUSHES policy pushes at the CURRENT other with a reply check (see
    the constants). Staged: pushes, contingency, payoff. The headline skill
    is the payoff: the reply rate gained over timing-blind."""
    to = _other(recs)
    rows = _turn_rows(recs, to) if to else []
    w = _turn_score(rows)
    ck = _clock(rows[-1].get("clock", 1) if rows else 1)   # #278: the bar of the turns' clock
    crit = {"pushes": min(1.0, w["n"] / RUNG7_PUSHES)}
    if not w["n"]:
        crit.update({"contingency": 0.0, "payoff": 0.0})
        return {"crit": crit, "skill": 0.0, "passed": False, **{k: w[k] for k in w if k != "passed"}}
    exc = w["share"] - w["chance"]
    crit["contingency"] = max(0.0, min(1.0, w["z"] / ck["cont_z"], exc / ck["cont_margin"]))
    crit["payoff"] = max(0.0, min(1.0, w["payoff_z"] / ck["pay_z"], w["payoff"] / ck["pay_margin"]))
    out = {"crit": crit, "skill": round(w["payoff"], 4), "eid": to, "clock": rows[-1].get("clock", 1),
           "passed": bool(w["passed"] and all(v >= 1.0 for v in crit.values()))}
    out.update({k: (round(v, 4) if isinstance(v, float) else v) for k, v in w.items() if k != "passed"})
    return out


def _rung8(recs: list[dict], tempo: dict | None = None) -> dict:
    """Understand an other (#254; rung 7 until #258, unchanged): over the last RUNG8_LOOKS reply checks at it,
    SELF's prediction p (its own p_self, mixed with the model's p_llm by the
    advisor's earned weight when the model answered) removes RUNG8_MARGIN of
    the RUNNING RATE's Brier error -- the other's prior-smoothed reply rate
    over the checks before each one, no lookahead. Knowing the rate scores 0;
    only what self learned beyond it (timing, runs) counts. Only checks at the
    CURRENT other count (#208, see _rung6)."""
    to = _other(recs)
    if to is None:
        rc = [r for r in recs if r.get("kind") == "reach" and r.get("reply") and not r.get("audit")]
        to = rc[-1].get("eid") if rc else None
    rows = _rung8_series(recs, to)[-RUNG8_LOOKS:] if to else []
    crit = {"looks": min(1.0, len(rows) / RUNG8_LOOKS)}
    if not rows:
        return {"crit": {**crit, "skill": 0.0}, "skill": 0.0, "passed": False}
    b_rate = sum((r["o"] - r["rate"]) ** 2 for r in rows) / len(rows)
    b_p = sum((r["o"] - r["p"]) ** 2 for r in rows) / len(rows)
    skill = (b_rate - b_p) / b_rate if b_rate > 1e-9 else 0.0
    crit["skill"] = max(0.0, min(1.0, skill / RUNG8_MARGIN))
    return {"crit": crit, "skill": round(skill, 4),
            "eid": to, "passed": all(v >= 1.0 for v in crit.values())}


def _mirror_side(rows: list[dict], side: str) -> dict:
    """One side's score over rows: skill = 1 - SSE(p)/SSE(b) (its own moves
    against the no-self baseline), the sign-test z of p landing closer than b
    (a tie is no win), and the label meet's split would give it."""
    e_p = [(r[side] - r[f"p_{side}"]) ** 2 for r in rows]
    e_b = [(r[side] - r[f"b_{side}"]) ** 2 for r in rows]
    sb = sum(e_b)
    skill = (1.0 - sum(e_p) / sb) if sb > 1e-12 else 0.0
    n = len(rows)
    z = ((sum(a < b for a, b in zip(e_p, e_b)) - n / 2) / (np.sqrt(n) / 2)) if n else 0.0
    body = skill >= BODY_SKILL and z >= MIRROR_Z
    return {"skill": round(skill, 4), "z": round(float(z), 2), "label": "body" if body else "other"}


def _rung9(recs: list[dict], tempo: dict | None = None) -> dict:
    """Recognize itself (#312): over the last MIRROR_LOOKS mirror lines with
    both sides read and predicted, its own moves predict the GLASS far beyond
    the glass's own past (skill >= BODY_SKILL and sign-test z >= MIRROR_Z: it
    reads as body, self) and do NOT so predict the STRANGER -- its own old
    moves, the same statistics -- (it reads as other). Staged: looks, self,
    other. The headline skill is the glass's. Reads the current mirror only
    (the latest line's toy)."""
    last = next((r for r in reversed(recs) if r.get("kind") == "mirror"), None)
    keys = ("glass", "stranger", "p_glass", "b_glass", "p_stranger", "b_stranger")
    rows = [r for r in _mirror_rows(recs, last.get("toy") if last else None)
            if all(r.get(k) is not None for k in keys)][-MIRROR_LOOKS:]
    crit = {"looks": min(1.0, len(rows) / MIRROR_LOOKS)}
    if not rows:
        crit.update({"self": 0.0, "other": 0.0})
        return {"crit": crit, "skill": 0.0, "z": 0.0, "passed": False}
    g, s = _mirror_side(rows, "glass"), _mirror_side(rows, "stranger")
    crit["self"] = max(0.0, min(1.0, g["skill"] / BODY_SKILL, g["z"] / MIRROR_Z))
    crit["other"] = 1.0 if s["label"] == "other" else 0.0
    return {"crit": crit, "skill": g["skill"], "z": g["z"],
            "stranger_skill": s["skill"], "stranger_z": s["z"],
            "labels": {"glass": g["label"], "stranger": s["label"]},
            "passed": all(v >= 1.0 for v in crit.values())}


# ---- change itself (#320): its test, from the dream's lines -----------------------------

def _rung10(recs: list[dict], tempo: dict | None = None) -> dict:
    """Change itself (#320), staged over its last CHANGE_WINDOW proposals,
    from the record's lines alone (the dream did the replays):
      dreams    -- CHANGE_WINDOW proposals scored;
      judgement -- at least one adoption checked out of sample, every checked
                   one held (gain > 0 on the looks after it), their pooled
                   gain >= CHANGE_MARGIN at z >= CHANGE_CHECK_Z, and no checked
                   rejection a miss (it would have cleared the bar after);
      honesty   -- every adoption cleared its own bar, from the value the
                   setting had (chained from the first line's base), one grid
                   step, and no setting went back to a value it left (A->B->A).
    The headline skill is the pooled out-of-sample gain (Brier per look)."""
    lines = [r for r in recs if r.get("kind") == "change"]
    win = lines[-CHANGE_WINDOW:]
    crit = {"dreams": min(1.0, len(win) / CHANGE_WINDOW), "judgement": 0.0, "honesty": 0.0}
    if not win:
        return {"crit": crit, "skill": 0.0, "passed": False, "dreams": 0, "adopted": 0, "held": 0, "misses": 0,
                "checked": 0, "z_after": 0.0, "gain_after": None}
    checks = {r.get("of"): r for r in recs if r.get("kind") == "change_check"}
    adopted = [p for p in win if p.get("verdict") == "adopted"]
    ca = [checks[p["ts"]] for p in adopted if p.get("ts") in checks]
    cr = [checks[p["ts"]] for p in win if p.get("verdict") != "adopted" and p.get("ts") in checks]
    misses = sum(1 for c in cr if _clears(c))
    held = sum(1 for c in ca if float(c.get("gain") or 0) > 0)
    blocks = [float(b) for c in ca for b in (c.get("blocks") or ())]
    gain = float(np.mean(blocks)) if blocks else None
    z = _bz(blocks) if blocks else 0.0
    if ca and held == len(ca) and not misses:
        crit["judgement"] = float(max(0.0, min(1.0, z / CHANGE_CHECK_Z, gain / CHANGE_MARGIN)))
    ok, cur, back = True, {**_defaults(), **(lines[0].get("base") or {})}, {s: [] for s in CHANGE_SETTINGS}
    inwin = {id(p) for p in win}
    for p in lines:
        s, mine = p.get("setting"), id(p) in inwin
        if s not in CHANGE_SETTINGS:
            ok = ok and not mine
            continue
        if mine and p.get("from") != cur[s]:
            ok = False
        if p.get("verdict") != "adopted":
            continue
        grid = CHANGE_SETTINGS[s]
        step = (p.get("from") in grid and p.get("to") in grid
                and abs(grid.index(p["to"]) - grid.index(p["from"])) == 1)
        if mine and (not step or not _clears(p) or p.get("to") in back[s]):
            ok = False
        if mine:
            back[s].append(p.get("from"))
        cur[s] = p.get("to")
    crit["honesty"] = 1.0 if ok else 0.0
    return {"crit": crit, "skill": round(gain, 6) if gain is not None else 0.0,
            "passed": all(v >= 1.0 for v in crit.values()), "dreams": len(win), "adopted": len(adopted),
            "checked": len(ca) + len(cr), "held": held, "misses": misses, "z_after": round(z, 2),
            "gain_after": None if gain is None else round(gain, 6)}


def _change_stats(srv, recs: list[dict]) -> dict:
    """For the operator (#320): the settings it runs with (and today's
    constants), its dreams, what it adopted, its last proposal."""
    lines = [r for r in recs if r.get("kind") == "change"]
    last = lines[-1] if lines else None
    cr = _carry(recs)                                 # #345: what was archived before the summary
    return {"settings": dict(_settings(srv)), "defaults": _defaults(), "dreams": len(lines) + int(cr.get("change", 0)),
            "adopted": sum(1 for r in lines if r.get("verdict") == "adopted"),   # every adoption is kept
            "checks": sum(1 for r in recs if r.get("kind") == "change_check") + int(cr.get("change_check", 0)),
            "last": ({k: last.get(k) for k in ("ts", "setting", "from", "to", "verdict", "gain", "z", "reason",
                                                "cost_s")} if last else None)}


# ---- speak (#311): its turns carry words from its own record --------------------------

def _speak_words(eid: str, answer: str) -> str | None:
    """THE TEMPLATE: a thing's name (the last part of its short name:
    toy:dice -> dice) and what a look at it found, in self's two words for
    it (SPEAK_SAY) -- None for a look with no answer. Nothing else goes into
    a line: no model, no chat words."""
    say = SPEAK_SAY.get(answer)
    return f"{_short(eid).rsplit(':', 1)[-1]}: {say}" if say and eid else None


def _speak_about(recs: list[dict], to: str | None) -> dict | None:
    """What self has to say: its latest answered look at a thing -- not at the
    other it speaks to, not at itself, not inside a blind audit -- as
    {eid, answer, ts}; None when there is none."""
    me = _eid()
    for r in reversed(recs):
        if (r.get("kind") == "reach" and r.get("answer") in SPEAK_SAY and not r.get("audit")
                and r.get("eid") and r.get("eid") not in (to, me)):
            return {"eid": r["eid"], "answer": r["answer"], "ts": r.get("ts")}
    return None


def _speak(recs: list[dict], to: str | None, n: int) -> dict:
    """One push's speech: {text, speech, [words, about]}. A fair coin, seeded
    by the cycle, picks words or plain when self has something to say; with
    nothing to say the push is plain and "mute" (it counts for neither)."""
    import random
    about = _speak_about(recs, to)
    words = _speak_words(about["eid"], about["answer"]) if about is not None else None
    if words is None:
        return {"text": SPEAK_PLAIN, "speech": "mute"}
    if random.Random(f"speak:{n}").random() < 0.5:
        return {"text": words, "speech": "words", "words": words, "about": about}
    return {"text": SPEAK_PLAIN, "speech": "plain"}


def _speak_rows(recs: list[dict], eid: str | None) -> list[dict]:
    """Speak's pushes at `eid` in chat, oldest first, each with its reply
    check's `replied` and -- for words -- whether they were TRUE of the record
    at the push: the look they cite is on the record before the push, at that
    thing, with that answer, the latest answered look at it (no audit), and
    the words are the template's. Mute pushes, turns with the ball and pushes
    from before speak (no `speech`) count for nothing."""
    rows: list[dict] = []
    last: dict = {}                                       # eid -> its latest answered look so far
    pend = None
    for r in recs:
        k = r.get("kind")
        if k == "push":
            pend = None
            if r.get("to") == eid and eid and _medium(r) == "chat" and r.get("speech") in ("words", "plain"):
                ok = None
                if r["speech"] == "words":
                    a = r.get("about") or {}
                    seen = last.get(a.get("eid"))
                    ok = bool(seen is not None and a.get("eid") != eid and seen.get("ts") == a.get("ts")
                              and seen.get("answer") == a.get("answer")
                              and r.get("words") == _speak_words(a.get("eid"), a.get("answer")))
                pend = {"ts": float(r.get("ts") or 0), "speech": r["speech"], "true": ok, "n": r.get("n")}
        elif k == "reach":
            if pend is not None and r.get("eid") == eid:
                if r.get("reply") and isinstance(r.get("replied"), bool):
                    rows.append({**pend, "replied": r["replied"]})
                pend = None                               # only the first look after the push is its check
            if r.get("answer") in SPEAK_SAY and not r.get("audit") and r.get("eid"):
                last[r["eid"]] = r
    return rows


def _speak_lift(rows: list[dict]) -> dict:
    """Words' reply rate against plain's over `rows`: the counts, the rates,
    the lift and its pooled two-proportion z; `passed` when both kinds have
    SPEAK_EACH and the lift clears SPEAK_LIFT_Z and SPEAK_LIFT_MARGIN."""
    w = [r["replied"] for r in rows if r["speech"] == "words"]
    p = [r["replied"] for r in rows if r["speech"] == "plain"]
    nw, np_ = len(w), len(p)
    rw = sum(w) / nw if nw else None
    rp = sum(p) / np_ if np_ else None
    lift = (rw - rp) if nw and np_ else 0.0
    z = 0.0
    if nw and np_:
        pool = (sum(w) + sum(p)) / (nw + np_)
        se = float(np.sqrt(pool * (1.0 - pool) * (1.0 / nw + 1.0 / np_)))
        z = lift / se if se > 1e-12 else 0.0
    ok = min(nw, np_) >= SPEAK_EACH and z >= SPEAK_LIFT_Z and lift >= SPEAK_LIFT_MARGIN
    return {"n_words": nw, "n_plain": np_, "rate_words": rw, "rate_plain": rp, "lift": float(lift),
            "z": float(z), "passed": bool(ok)}


def _rung11(recs: list[dict], tempo: dict | None = None) -> dict:
    """Speak (#311): over its last SPEAK_PUSHES pushes at the CURRENT other in
    chat with a reply check -- staged: pushes (SPEAK_EACH of each kind), truth
    (every utterance true of the record), lift (words earn more replies than
    plain pushes), timing (rung 7's contingency over the same pushes, at rung
    7's bar). The headline skill is the lift."""
    to = _other(recs)
    rows = _speak_rows(recs, to)[-SPEAK_PUSHES:] if to else []
    s = _speak_lift(rows)
    said = [r for r in rows if r["speech"] == "words"]
    false = sum(1 for r in said if not r["true"])
    crit = {"pushes": max(0.0, min(1.0, len(rows) / SPEAK_PUSHES, min(s["n_words"], s["n_plain"]) / SPEAK_EACH)),
            "truth": 1.0 if said and not false else 0.0,
            "lift": max(0.0, min(1.0, s["z"] / SPEAK_LIFT_Z, s["lift"] / SPEAK_LIFT_MARGIN)),
            "timing": 0.0}
    tw = {"n": 0, "share": None, "chance": None, "z": 0.0}
    if rows:
        at = {r["ts"] for r in rows}
        trows = [r for r in _turn_rows(recs, to, "chat", 1) if sum(r["dwell"]) > 0]
        d = [x for r, x in zip(trows, _turn_deltas(trows)) if r["ts"] in at]
        tw = _turn_window(d, 1)
        if tw["n"]:
            crit["timing"] = max(0.0, min(1.0, tw["z"] / RUNG7_CONT_Z, (tw["share"] - tw["chance"]) / RUNG7_CONT_MARGIN))

    def r4(x):
        return None if x is None else round(float(x), 4)
    return {"crit": crit, "skill": round(s["lift"], 4), "eid": to, "passed": all(v >= 1.0 for v in crit.values()),
            "n": len(rows), "n_words": s["n_words"], "n_plain": s["n_plain"], "rate_words": r4(s["rate_words"]),
            "rate_plain": r4(s["rate_plain"]), "z": round(s["z"], 2), "false": false,
            "timing_share": r4(tw["share"]), "timing_chance": r4(tw["chance"]), "timing_z": round(tw["z"], 2)}


def _speak_stats(recs: list[dict]) -> dict:
    """For the operator (#311): speak at the current other -- its pushes by
    kind (all time), its last words, and the test's window: counts, reply
    rates, lift and z, false utterances, and its timing's in-window share
    against chance."""
    to = _other(recs)
    ps = [r for r in recs if r.get("kind") == "push" and r.get("to") == to and r.get("speech")]
    p = _rung11(recs)
    last = next((r for r in reversed(ps) if r.get("speech") == "words"), None)
    ct = _to_carry(recs, to)                          # #345: its pushes archived before the summary
    return {"words": sum(1 for r in ps if r["speech"] == "words") + ct.get("words", 0),
            "plain": sum(1 for r in ps if r["speech"] == "plain") + ct.get("plain", 0),
            "mute": sum(1 for r in ps if r["speech"] == "mute") + ct.get("mute", 0),
            "last_words": last.get("words") if last else None,
            "window": p["n"], "n_words": p["n_words"], "n_plain": p["n_plain"], "rate_words": p["rate_words"],
            "rate_plain": p["rate_plain"], "lift": p["skill"], "z": p["z"], "false": p["false"],
            "timing_share": p["timing_share"], "timing_chance": p["timing_chance"], "timing_z": p["timing_z"]}


PROGRESS = {1: _rung1, 2: _rung2, 3: _rung3, 4: _rung4, 5: _rung5, 6: _rung6, 7: _rung7,
            8: _rung8, 9: _rung9, 10: _rung10, 11: _rung11}   # rung -> its test over the record
# #249: the line each rung's headline skill has to reach (the number status()['skill'] reads);
# rung 4's news share has a moving bar (its fixed-pace base + 0.15), so it has none here
SKILL_PASS = {1: RUNG1_SKILL, 2: RUNG2_SKILL, 3: RUNG2_SKILL, 4: None, 5: 1.0, 6: 1.0,
              7: RUNG7_PAY_MARGIN, 8: RUNG8_MARGIN, 9: BODY_SKILL, 10: CHANGE_MARGIN, 11: SPEAK_LIFT_MARGIN}


def _ladder_ok(r: dict, n) -> bool:
    """A pass line's ladder is new enough for its rung: a rebuilt rung (#333,
    REBUILT) needs LADDER_V; a `rung` line above 6 needs the #258 flip's."""
    try:
        v, n = int(r.get("ladder") or 0), int(n or 0)
    except (TypeError, ValueError):
        return False
    if n in REBUILT:
        return v >= LADDER_V
    return r.get("kind") != "rung" or n < FLIPPED_FROM or v >= LADDER_FLIP


def _rung_lines(recs: list[dict], old_wiring: bool = False) -> list[dict]:
    """The passes that count: every `rung` line except one with a later
    `rung_void` line for its rung (#254: a pass a broken test recorded stays
    on the append-only record but doesn't count; a later pass does), and --
    #258, the ladder flip -- a pass above rung 6 only when it carries
    "ladder": 2 or later (before the flip rung 7 was "understand an other": an
    old pass is neither the new rung 7 nor rung 8).

    #295: an `achieve` line (the skill tree's pass) counts too, as its test's
    rung (it carries `rung`; its id names it otherwise), under the same voids.

    #333: a pass of a rebuilt rung (6-9) counts only with "ladder": 3.
    old_wiring=True gives the passes that rule alone voids instead (what
    counted before it): the voids' migration and the sheet read them."""
    voids: dict = {}
    for r in recs:
        if r.get("kind") == "rung_void" and r.get("issue") != 333:   # #333's voids: the ladder rule below
            n = r.get("rung") if r.get("rung") is not None else ACHIEVEMENTS.get(r.get("id"), {}).get("rung")
            voids[n] = max(voids.get(n, 0.0), float(r.get("ts") or 0))
    out = []
    for r in recs:
        k = r.get("kind")
        if k == "achieve":
            n = ACHIEVEMENTS.get(r.get("id"), {}).get("rung")
            if n is None:
                continue
            if r.get("rung") != n:
                r = {**r, "rung": n}
        elif k == "rung":
            n = r.get("rung")
            if not (int(n or 0) < FLIPPED_FROM or r.get("ladder") in (LADDER_FLIP, LADDER_V)):
                continue
        else:
            continue
        if _ladder_ok(r, n) == old_wiring:                  # old_wiring: only what #333's rule voids
            continue
        if n in voids and float(r.get("ts") or 0) < voids[n]:
            continue
        out.append(r)
    return out


def _passed(recs: list[dict]) -> set:
    return {0} | {r["rung"] for r in _rung_lines(recs)}


def _open_rungs(srv) -> None:
    """An achievement is earned when its test passes (#295) -- only while
    self is awake and every prerequisite is earned and accepted (#299);
    siblings in any order. The pass goes on the record as an `achieve` line,
    and waits for the operator's accept before it opens anything."""
    _migrate_accept(srv)                     # a pass from here on is new: it needs an accept
    recs = _read(srv)
    allowed = _allowed(srv)
    progress = True
    while progress:
        progress = False
        for i in _open_ids(recs):
            if i not in allowed:
                continue
            n = ACHIEVEMENTS[i]["rung"]
            p = PROGRESS[n](recs, _tempo(srv))
            if p["passed"]:
                line = {"ts": time.time(), "kind": "achieve", "id": i, "rung": n, "skill": p["skill"],
                        "ladder": LADDER_V}                  # #333: a pass on the nudge's ladder
                if p.get("eid"):
                    line["eid"] = p["eid"]
                _append(srv, line)
                recs, progress = _read(srv), True
                allowed = _allowed(srv)
                break


def _staged(crit: dict) -> tuple[float, str | None]:
    """THE BAR, STAGED: a rung's criteria are stages, in order (rung 1:
    pushes then skill; rung 2: reaches, skill, the audit). The bar fills
    through them -- each stage one equal share -- and names the first one not
    yet met; an earlier stage that slips takes the bar back with it. (The
    weakest-criterion bar sat at 0% for ~20 minutes on the live seat while
    reaches piled up: skill and audit can't move before enough reaches.)"""
    if not crit:
        return 1.0, None
    k = len(crit)
    for i, (name, v) in enumerate(crit.items()):
        if v < 1.0:
            return (i + max(0.0, v)) / k, name
    return 1.0, None


def _earned_rows(recs: list[dict]) -> list[dict]:
    """The earned achievements, oldest pass first: {id, name, ts, skill}."""
    times = _earn_times(recs)
    rows = [{"id": i, "name": ACHIEVEMENTS[i]["name"], "ts": float(r.get("ts") or 0), "skill": r.get("skill")}
            for i, r in times.items()]
    return sorted(rows, key=lambda x: (x["ts"], list(ACHIEVEMENTS).index(x["id"])))


def status(srv) -> dict:
    """Self's state for the panel and the page. #295, the skill tree: `level`
    (= `rung`, kept for older readers) counts the achievements earned;
    `rung_name` is the latest one's name; `next` the open ones (built,
    unearned, every prerequisite earned and accepted); `earned` the passes in
    order (the page's celebration reads it); #299: `accepted` the earned ones
    the operator accepted, `awaiting` the earned ones not yet (tree order).
    The bar reads the open achievement closest to passing; with none, the
    latest earned one's own test -- so a pass can be seen slipping."""
    rt = _rt(srv)
    recs = _read(srv)
    born = any(r.get("kind") == "birth" for r in recs)
    done = _passed(recs)
    earned = _earned_rows(recs)
    got = {e["id"] for e in earned}
    level = len(got)
    latest = earned[-1]["id"] if earned else None
    lim = limit(srv)
    allowed = _allowed(srv)
    opens = _open_ids(recs)
    tempo = _tempo(srv)
    run_ = (lambda n: PROGRESS[n](recs, tempo))
    progs = {i: run_(ACHIEVEMENTS[i]["rung"]) for i in opens if i in allowed}
    late = run_(ACHIEVEMENTS[latest]["rung"]) if latest is not None else None
    if progs:
        aim_id = max(progs, key=lambda i: (_staged(progs[i]["crit"])[0], -BUILT.index(i)))
        prog = progs[aim_id]
    elif latest is not None:
        aim_id, prog = latest, late
    else:
        aim_id, prog = None, {"crit": {}, "skill": 0.0, "passed": True}
    aim = ACHIEVEMENTS[aim_id]["rung"] if aim_id is not None else None
    bar, waiting = _staged(prog["crit"])
    slipping = bool(late is not None and not late["passed"])
    acc = _accepted(recs)
    awaiting = [i for i in _earn_order(recs) if i not in acc]
    if not progs and awaiting and rt["mode"] != "off" and not slipping:
        waiting = "your accept"                  # #299: nothing open runs; an earned one waits to be accepted
    if rt["mode"] != "off" and 1 not in done and _current(srv) is None:
        # no current right now (a fresh boot: nobody has spoken yet) -- say
        # so: self cycles in a still world (#282), but feel itself (feel the
        # push) can't move until the world does; past it nothing waits on it
        if aim == 1 and not any(r.get("kind") == "cycle" and not r.get("still") for r in recs):
            bar = 0.0
        if aim == 1:
            waiting = "the world"

    ladder = []                                  # the old ladder's view of the tree, by rung number
    for n, name in RUNG_NAMES.items():
        i = RUNG_ID.get(n)
        if n == 0:
            state = "passed"
        elif i is None or not ACHIEVEMENTS[i]["built"]:
            state = "unbuilt"
        elif i in got:
            state = "passed"
        elif any(p not in got for p in ACHIEVEMENTS[i]["prereqs"]):
            state = "locked"
        else:
            state = "current" if i in allowed else "capped"
        ladder.append({"rung": n, "name": name, "state": state, "id": i})
    r1 = _rung1(recs)
    spl = _split(recs, tempo)
    pushes = [r for r in recs if r.get("kind") == "push"]
    other = pushes[-1].get("to") if pushes else None
    now = time.time()
    # the exchange's ages for the home viewer's beam + flash (rung 6): how long
    # since self pushed (a beam) and since the other last answered (a flash) --
    # ages, not wall clocks, so the viewer's clock never has to agree with the seat's
    answered = [r for r in recs
                if r.get("kind") == "reach" and r.get("eid") == other
                and r.get("answer") == "changed" and not r.get("audit")]
    medium = _medium(pushes[-1]) if pushes else "chat"
    heard = answered[-1]["ts"] if answered else None
    if medium == "toy":                          # #264: the other's last act, not a look that read a change
        heard = _last_acted(srv, now)
    meets = {"pushes": len(pushes) + int(_carry(recs).get("push", 0)), "other": other, "medium": medium,
             "push_age": round(now - pushes[-1]["ts"], 3) if pushes else None,
             "heard_age": round(now - heard, 3) if heard is not None else None}
    return {"mode": rt["mode"], "limit": lim, "born": born, "held": rt["held"],
            "cycles": _cycles(recs),
            "rung": level, "level": level, "rung_name": ACHIEVEMENTS[latest]["name"] if latest else RUNG_NAMES[0],
            "next": opens, "earned": [{k: e[k] for k in ("id", "name", "ts")} for e in earned],
            "old_wiring": _old_wiring(recs),                                              # #333
            "accepted": [i for i in ACHIEVEMENTS if i in acc], "awaiting": awaiting,          # #299
            "allow": allow(srv), "allowed": [i for i in BUILT if i in allowed], "aim": aim_id,
            "bar": round(bar, 4), "waiting_on": waiting, "skill": prog.get("skill", r1["skill"]),
            "skill_rung": aim, "skill_pass": SKILL_PASS.get(aim),          # #249: the bar the skill has to beat
            "skill_measured": any(v > 0 for v in (prog.get("crit") or {}).values()),   # ...and whether anything feeds it yet
            "slipping": slipping, "ladder": ladder,
            "world": {e: k for e, k in _kinds(recs).items() if k != "unknown"},
            "split": {e: s for e, s in spl.items() if s != "unknown"},
            "meets": meets,
            "steer": _steer_stats(recs),
            "curiosity": _curiosity_stats(recs),
            "rung7": _rung7_stats(recs),
            "rung8": _rung8_stats(recs),
            "speak": _speak_stats(recs),                     # #311
            "change": _change_stats(srv, recs),              # #320
            "toys": _toys(srv, recs),
            "unlock": _latest_unlock(recs),                  # #267: the level-up's "new toy"
            "off_field": _off_field(srv, recs)}


def _latest_unlock(recs: list[dict]) -> dict | None:
    from . import toy_unlocks as _TU
    return _TU.latest(recs)


def _off_field(srv, recs: list[dict]) -> dict:
    """#244: of self's last OFF_FIELD_LOOKS looks, how many land on a thing
    with no live field point (the page then draws a ghost, not a beam to
    it), and which things those are (most looked-at first). Cached for
    TOY_TTL_S; None for `off` when the seat has no field to ask."""
    rt = _rt(srv)
    now = time.time()
    hit = rt.get("off_field")
    if hit is not None and now - hit[0] < TOY_TTL_S:
        return hit[1]
    looks = [r["eid"] for r in recs[-TOY_SCAN:] if r.get("kind") == "reach" and r.get("eid")][-OFF_FIELD_LOOKS:]
    fld = getattr(srv, "efield", None)
    get = getattr(fld, "get", None)
    out = {"looks": len(looks), "off": None, "things": []}
    if get is not None:
        from .eids import field_id
        on: dict[str, bool] = {}
        for e in set(looks):
            try:
                st = get(field_id(e))
                on[e] = bool(st is not None and st.tier == "live" and st.state is not None)
            except Exception:
                on[e] = False
        off = [e for e in looks if not on[e]]
        cnt: dict[str, int] = {}
        for e in off:
            cnt[e] = cnt.get(e, 0) + 1
        out["off"] = len(off)
        out["things"] = sorted(cnt, key=lambda e: (-cnt[e], e))[:12]
    rt["off_field"] = (now, out)
    return out


def recent(srv, n=30) -> list[dict]:
    """#230: the last n record lines, newest first, capped at RECENT_MAX --
    for the page's activity feed (copies: the record is never edited)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 30
    n = max(1, min(n, RECENT_MAX))
    with _lock:
        tail = [r for r in _read(srv)[-(n + 1):] if r.get("kind") != SUMMARY][-n:]   # #345: never the summary
    return [dict(r) for r in reversed(tail)]


def _short(eid: str) -> str:
    """membrane::Component:toy:dice -> toy:dice (the name a toy goes by)."""
    from .entities import MEMBRANE_NS
    s = eid[len(MEMBRANE_NS):] if eid.startswith(MEMBRANE_NS) else eid
    head, sep, rest = s.partition(":")
    return rest if sep and head[:1].isupper() and rest else s


def _toys(srv, recs: list[dict]) -> list[dict]:
    """#230: the things made for self to play with -- toy: and cradle: things,
    and words (w:) that carry a getter -- each with a health mark for the
    operator: `no getter` (it can't be read), `unread` (self has not reached
    it yet), `empty` (its last reads gave no answer), else `ok`. The store
    walk is cached for TOY_TTL_S; the reads are recounted each call."""
    rt = _rt(srv)
    now = time.time()
    cached = rt.get("toys")
    if cached is None or now - cached[0] > TOY_TTL_S:
        from .night import effective_script
        est = getattr(srv, "entity_store", None)
        found = []
        for eid, e in list(((est.docs if est is not None else None) or {}).items()):
            if not _in_world(srv, eid):
                continue                                       # #337: only the toys in its world
            name = _short(eid)
            word = name.startswith(TOY_WORDS)
            tb = (e.properties or {}).get("toybox") or {}
            kind = tb.get("kind")                              # #239: a built-in toy, any name
            if not (word or kind or name.startswith(TOY_PREFIXES)):
                continue
            try:
                getter = bool(effective_script(est, e, "getter")[0])
            except Exception:
                getter = False
            if word and not getter and not kind:
                continue
            found.append((eid, getter, kind or "script", tb.get("name") or "", tb.get("block")))
        cached = rt["toys"] = (now, sorted(found))
    eids = {t[0] for t in cached[1]}
    reads: dict[str, list] = {e: [] for e in eids}
    for r in reversed(recs[-TOY_SCAN:]):
        if r.get("kind") == "reach" and not r.get("audit") and r.get("eid") in eids:
            got = reads[r["eid"]]
            if len(got) < TOY_READS:
                got.append(r.get("answer"))
    fed = {r.get("toy") for r in recs[-TOY_SCAN:] if r.get("kind") == "mirror"}   # #312: self's mirror
    out = []
    for eid, getter, kind, toy, block in cached[1]:
        got = reads[eid]
        answered = sum(1 for a in got if a in ("changed", "unchanged"))
        health = ("no getter" if not getter else "unread" if not got
                  else "empty" if not answered else "ok")
        if kind == "mirror":                               # no getter by design: self sees it by feeding it
            health = "ok" if toy in fed else "unread"
        row = {"eid": eid, "kind": kind, "getter": getter, "reads": len(got), "answered": answered,
               "health": health}
        if toy:                                            # #244: the play buttons' target
            row["toy"] = toy
            if block:
                row["block"] = block
        out.append(row)
    return out


def _rung7_stats(recs: list[dict]) -> dict:
    """For the operator (#258): rung 7 at the current other -- its policy
    pushes (`pushes`) and those whose reply check came (`checked`); per
    spoke_age bin the pushes, replies and reply rate over the last
    TURN_MEMORY (what the policy reads) and the window it believes best;
    over the window the test reads: the in-window share against chance and
    its z (contingency), the reply rate against timing-blind's, the payoff
    and its t."""
    to = _other(recs)
    pushes = sum(1 for r in recs if r.get("kind") == "push" and r.get("to") == to and r.get("aim") is not None) \
        + _to_carry(recs, to).get("aim", 0)          # #345: its policy pushes archived before the summary
    rows = _turn_rows(recs, to) if to else []
    medium = _cur_medium(recs, to)
    clock = _cur_clock(recs, to, medium)
    names = _clock(clock)["names"]                      # #278: the windows of the turns' clock
    k, n = _turn_post(rows)
    best = None
    if n.sum() > 0:
        mean = (k + 1.0) / (n + 2.0)
        best = names[max(range(len(k)), key=lambda b: (mean[b], n[b], -b))]
    bins = [{"bin": names[b], "pushes": int(n[b]), "replied": int(k[b]),
             "rate": round(float(k[b] / n[b]), 4) if n[b] else None} for b in range(len(k))]
    w = _turn_score(rows)

    def r4(x):
        return None if x is None else round(float(x), 4)
    return {"medium": medium, "clock": clock, "pushes": pushes, "checked": len(rows[-TURN_MEMORY:]),
            "bins": bins, "best": best,
            "window": w["n"], "share": r4(w["share"]), "chance": r4(w["chance"]), "z": round(w["z"], 2),
            "payoff": r4(w["payoff"]), "payoff_z": round(w["payoff_z"], 2),
            "reply_rate": r4(w["reply_rate"]), "blind_rate": r4(w["blind_rate"])}


def _rung8_stats(recs: list[dict]) -> dict:
    """For the operator (#217, #254): rung 8 at the current other -- the pushes
    that asked the model, those whose reply check came (`checked`), those the
    model answered in time (`paired`), the pair rate per checked ask; over
    the reply checks rung 8 reads, the Brier error of self's own p_self, of
    the mix self passes on (`brier_mix`), of the running rate it must beat,
    and of the model alone on the checks it answered; the weight self gives
    the current advisor's next answer and that advisor's model id; and the
    base (all-time) and recent rates the model is told (#246)."""
    to = _other(recs)
    asked = [r for r in recs if r.get("kind") == "push" and r.get("asked") and r.get("to") == to]
    ct = _to_carry(recs, to)                          # #345: its asks archived before the summary
    checked, paired = ct.get("checked", 0), ct.get("paired", 0)
    dropped = _dropped(recs)
    for p in asked:
        if float(p.get("ts") or 0) in dropped:
            continue                                  # #345: dropped at sleep -- never checked, never missed
        look = _first_look_after(recs, to, float(p.get("ts") or 0))
        if look is None:
            continue
        checked += 1
        paired += int(look.get("p_llm") is not None and look.get("answer") in ("changed", "unchanged"))
    allrows = _rung8_series(recs, to) if to else []
    rows = allrows[-RUNG8_LOOKS:]
    llm = [r for r in rows if r.get("p_llm") is not None]

    def brier(rs, key):
        return round(sum((r["o"] - float(r[key])) ** 2 for r in rs) / len(rs), 4) if rs else None
    model = next((r["model"] for r in reversed(allrows) if r.get("p_llm") is not None), None)
    return {"asked": len(asked) + ct.get("asked", 0), "checked": checked, "paired": paired,
            "pair_rate": round(paired / checked, 4) if checked else None,
            "checks": len(rows),
            "brier_self": brier(rows, "p_self"), "brier_mix": brier(rows, "p"),
            "brier_rate": brier(rows, "rate"), "brier_model": brier(llm, "p_llm"),
            "advisor_weight": _advisor_cal(allrows, model)[1],
            "advisor_model": model,
            "base_rate": round(_base_rate(recs, to)[0], 4) if to else None,
            "recent_rate": round(_recent_rate(recs, to)[0], 4) if to else None}


def _curiosity_stats(recs: list[dict], top: int = 3) -> list[dict]:
    """For the operator (#212): the things whose prediction is improving
    fastest -- their learning progress and recent error, best first. Self's
    choice reads the same numbers; nothing here is stored or fed back."""
    lp = _progress(recs, detail=True)
    best = sorted(lp.items(), key=lambda kv: (-kv[1]["progress"], kv[0]))[:top]
    return [{"eid": e, "progress": round(d["progress"], 4), "error": round(d["error"], 4)}
            for e, d in best]


def _steer_stats(recs: list[dict]) -> dict | None:
    """How close it lands to its aim (last 20 steered cycles), against the
    same stride without leaning into the current."""
    rs = [r for r in recs if r.get("kind") == "cycle" and "aim_err" in r][-STEER_LAST:]
    if not rs:
        return None
    err = sum(r["aim_err"] for r in rs) / len(rs)
    naive = sum(r["aim_naive"] for r in rs) / len(rs)
    return {"err": round(err, 4), "naive": round(naive, 4),
            "skill": round(1.0 - err / naive, 4) if naive > 1e-9 else 0.0}


# ---- #345 part 2: consolidation -- nothing raw stays through sleep ----------------------
#
# At the moment Blurp falls asleep (energy._fall_asleep) every raw line of the
# waking period moves, unchanged, to the dated archive
# self/archive/record-<UTC date of the line's own ts>.jsonl, and the working
# record becomes ONE line: the summary,
#
#   {"kind": "summary", "id", "at", "n", "carry": {...}, "stalls": [...], "lines": [...]}
#
# `lines` are the windows the readers read (copies of archived lines, in record
# order: _keep says which and why), `carry` the counts no window can give back
# (_tally: the cycles, the pushes, the dreams and their checks, rung 8's checks
# before its window, rung 4's fixed-pace looks, each other's asks, words and
# policy pushes), `stalls` each window line's record-gap count (curiosity and
# rung 5 read gaps between consecutive lines). _read hands readers the summary,
# its lines, then the lines since: within every window the same record they
# read before. The archive is for the dashboard and later analysis (history());
# nothing on the cycle path reads it.
#
# RESTART SAFETY: the journal self/consolidating.json is written first (stage
# "archive", with the archive files' sizes before), the archive appended and
# synced, the manifest line appended, the new record written beside the old
# one (record.jsonl.next) and synced, the journal set to "swap", the record
# swapped in (os.replace), the journal removed. On load (_recover) a journal
# at "archive" is undone (the archive cut back to its sizes) and the
# consolidation redone from the untouched record; one at "swap" finishes the
# swap. No line is lost or doubled.

LAM_MAX = max(CHANGE_SETTINGS["lam_looks"])     # the most looks any rate fit reads (change itself's grid)
_ALWAYS = ("birth", "rung", "achieve", "rung_void", "accept", "accept_migrated", "unlock", "unlock_built",
           "rung7_restart", "fix", "audit")    # read whole by their readers; a few lines each


def _day(ts) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime("%Y-%m-%d")


def _ans_na(r: dict) -> bool:
    return r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged") and not r.get("audit")


class _Index:
    """One pass over the record for _keep and _tally: each push's end (the
    next push, or the first look at its other: T), each reply check's push,
    each look's thing's last shift before it, words' cited looks, the turns
    dropped at sleep, and each thing's looks for _first_look_after."""

    def __init__(self, recs: list):
        self.recs = recs
        self.lo = 1 if recs and recs[0].get("kind") == SUMMARY else 0
        self.T: dict = {}
        self.push_of: dict = {}
        self.shift_of: dict = {}
        self.say_of: dict = {}
        self.looks: dict = {}           # eid -> ([index], [prefix max ts])
        self.dropped: dict = {}         # push ts -> its sleep line
        pend = last_push = None
        shift: dict = {}
        say: dict = {}
        for i in range(self.lo, len(recs)):
            r = recs[i]
            k = r.get("kind")
            if k == "push":
                if pend is not None:
                    self.T[pend] = i
                pend = last_push = i
                a = r.get("about") or {}
                if r.get("speech") == "words" and isinstance(a, dict):
                    self.say_of[i] = say.get(a.get("eid"))
            elif k == "reach":
                e = r.get("eid")
                if pend is not None and e == recs[pend].get("to"):
                    self.T[pend] = i
                    pend = None
                if r.get("reply"):
                    self.push_of[i] = last_push
                self.shift_of[i] = shift.get(e)
                ix, mx = self.looks.setdefault(e, ([], []))
                t = float(r.get("ts") or 0)
                ix.append(i)
                mx.append(max(t, mx[-1]) if mx else t)
                if r.get("answer") in SPEAK_SAY and not r.get("audit") and e:
                    say[e] = i
            elif k == "shift" and r.get("eid") is not None:
                shift[r["eid"]] = i
            elif k == "sleep":
                d = r.get("dropped")
                if isinstance(d, dict) and d.get("turn") is not None:
                    self.dropped[float(d["turn"])] = i

    def first_after(self, i: int):
        """_first_look_after for push i: its other's first look stamped after it."""
        import bisect
        p = self.recs[i]
        ix, mx = self.looks.get(p.get("to"), ([], []))
        j = bisect.bisect_right(mx, float(p.get("ts") or 0))
        return ix[j] if j < len(ix) else None


def _keep(recs: list, ix: _Index | None = None) -> set:
    """THE SUMMARY'S WINDOWS (#345): the record lines every reader reads, by
    each reader's own constant -- no window of its own. Readers that count a
    whole record are carried as counts (_tally) instead. -> indices."""
    ix = ix or _Index(recs)
    R, lo, N = recs, ix.lo, len(recs)
    K: set = set()
    add = K.add

    def tail(pred, n, key=None, hi=None):
        """The last n lines matching pred (per key), before hi."""
        cnt: dict = {}
        for i in range(N if hi is None else hi, lo, -1):
            r = R[i - 1]
            if not pred(r):
                continue
            kk = key(r) if key is not None else None
            c = cnt.get(kk, 0)
            if c < n:
                add(i - 1)
                cnt[kk] = c + 1
            elif key is None:
                break

    def eid(r):
        return r.get("eid")

    def to(r):
        return r.get("to")

    def kind(k):
        return lambda r: r.get("kind") == k

    reach, push, cyc, shift, mirror = kind("reach"), kind("push"), kind("cycle"), kind("shift"), kind("mirror")
    first: dict = {}
    for i in range(lo, N):
        r = R[i]
        k = r.get("kind")
        if k in _ALWAYS:
            add(i)                                      # passes, voids, accepts, unlocks, audits, fixes
        elif k == "reach" and r.get("timed"):
            first.setdefault("timed", i)                # _rate_looks: from the first timed look on
        elif k == "push" and _medium(r) == "toy":
            first.setdefault("toy", i)                  # _rung6: chat as it stood at the switch
        elif k == "change":
            first.setdefault("change", i)               # _rung10: the honesty chain's start
            if r.get("verdict") == "adopted":
                add(i)                                  # ...and every adoption on it
    K.update(first.values())
    K.update(range(max(lo, N - TOY_SCAN), N))           # _toys, _off_field, recent(), the card: the last lines
    # the cycles: rung 1, steering, a world that ever moved
    tail(lambda r: cyc(r) and r.get("change") == "world", RUNG1_CYCLES)
    tail(lambda r: cyc(r) and "aim_err" in r, STEER_LAST)
    tail(lambda r: cyc(r) and not r.get("still"), 1)
    # the looks: rungs 2-5, kinds, the split, the other's last words, a toy's log
    tail(_ans_na, RUNG2_REACHES, key=eid)
    tail(_ans_na, RUNG2_REACHES * 4)
    tail(lambda r: reach(r) and not r.get("audit") and not r.get("audit5"), RUNG3_WINDOW, key=eid)
    tail(lambda r: reach(r) and not r.get("audit"), RUNG3_WINDOW)
    tail(lambda r: reach(r) and not r.get("audit") and r.get("steer"), RUNG3_LOOKS)
    tail(lambda r: _ans_na(r) and r.get("timed"), max(RUNG4_LAST, RUNG4_TIMED))
    tail(lambda r: r.get("watch5"), RUNG5_LOOKS)
    tail(lambda r: reach(r) and r.get("answer") == "changed" and not r.get("audit"), 1, key=eid)
    tail(lambda r: reach(r) and r.get("log_at") is not None, 1, key=eid)
    tail(lambda r: reach(r) and r.get("reply") and not r.get("audit"), 1)
    spoke: dict = {}
    for i in range(lo, N):                              # _last_spoke: the latest time it read them changed
        r = R[i]
        if reach(r) and r.get("answer") == "changed":
            t = float(r.get("ts") or 0)
            if r.get("eid") not in spoke or t >= spoke[r.get("eid")][0]:
                spoke[r.get("eid")] = (t, i)
    K.update(i for _, i in spoke.values())
    # shifts: rung 5's count, each thing's last (the rate fit, noticing)
    tail(shift, RUNG5_SHIFTS)
    tail(lambda r: shift(r) and r.get("eid") is not None, 1, key=eid)
    # pushes: the other, rung 6's count, the current medium and clock, speak's last words
    tail(push, 1)
    tail(push, RUNG6_PUSHES, key=to)
    tail(push, 1, key=lambda r: (r.get("to"), _medium(r)))
    tail(lambda r: push(r) and r.get("speech") == "words", 1, key=to)
    lp = next((i for i in range(N - 1, lo - 1, -1) if push(R[i])), None)
    if lp is not None:                                  # _p_self at the latest push: each thing's last changed look by then
        t, seen = float(R[lp].get("ts") or 0), set()
        for i in range(N - 1, lo - 1, -1):
            r = R[i]
            if reach(r) and r.get("answer") == "changed" and r.get("eid") not in seen \
                    and float(r.get("ts") or 0) <= t:
                seen.add(r.get("eid"))
                add(i)
    for i in range(lo, N):                              # an ask still waiting for its look (never doubles a count)
        r = R[i]
        if push(r) and r.get("asked") and float(r.get("ts") or 0) not in ix.dropped and ix.first_after(i) is None:
            add(i)
    # the mirror: rung 9's fit and window, the current toy
    tail(mirror, 1)
    tail(mirror, MIRROR_FIT + MIRROR_OWN + MIRROR_PAST, key=lambda r: r.get("toy"))
    keys9 = ("glass", "stranger", "p_glass", "b_glass", "p_stranger", "b_stranger")
    tail(lambda r: mirror(r) and all(r.get(k) is not None for k in keys9), MIRROR_LOOKS, key=lambda r: r.get("toy"))
    # rung 6 as it stood at the switch to the ball
    ft = first.get("toy")
    if ft is not None:
        j = next((i for i in range(ft - 1, lo - 1, -1) if push(R[i])), None)
        if j is not None:
            add(j)
            to6 = R[j].get("to")
            tail(lambda r: push(r) and r.get("to") == to6, RUNG6_PUSHES, hi=ft)
            tail(lambda r: _ans_na(r) and r.get("eid") == to6, max(RUNG6_HEARD, RUNG2_REACHES), hi=ft)
            tail(_ans_na, RUNG2_REACHES * 4, hi=ft)
    _keep_turns(R, lo, N, K)
    _keep_rung8(R, lo, N, K)
    _keep_rates(R, lo, N, K)
    _keep_curiosity(R, lo, N, K)
    # change itself: its window and the dreams' pending proposals, with their checks
    tail(kind("change"), 2 * CHANGE_WINDOW)
    props = {float(R[i].get("ts") or 0) for i in K if R[i].get("kind") == "change"}
    for i in range(lo, N):
        r = R[i]
        if r.get("kind") == "change_check" and r.get("of") is not None and float(r["of"]) in props:
            add(i)
    _lean(ix, K)
    if lo:
        K.discard(0)                                    # the old summary itself is never a window line
    return K


def _keep_turns(R, lo, N, K) -> None:
    """Rung 7's rows (push + its check) per other, medium and clock: the last
    RUNG7_PUSHES + TURN_MEMORY; speak's rows per other: the last SPEAK_PUSHES,
    and the turn rows its timing reads (TURN_MEMORY before its window)."""
    turns: dict = {}
    pend = None
    for i in range(lo, N):
        r = R[i]
        k = r.get("kind")
        if k == "push":
            pend = i if (r.get("aim") is not None and r.get("dwell")) else None
        elif k == "reach" and pend is not None and r.get("eid") == R[pend].get("to"):
            if r.get("reply") and isinstance(r.get("replied"), bool):
                p = R[pend]
                turns.setdefault((p.get("to"), _medium(p), _clock_of(p)), []).append((pend, i))
            pend = None
    for rows in turns.values():
        for a, b in rows[-(RUNG7_PUSHES + TURN_MEMORY):]:
            K.update((a, b))
    speak: dict = {}
    pend = None
    for i in range(lo, N):
        r = R[i]
        k = r.get("kind")
        if k == "push":
            pend = None
            if r.get("to") and _medium(r) == "chat" and r.get("speech") in ("words", "plain"):
                pend = i
        elif k == "reach" and pend is not None and r.get("eid") == R[pend].get("to"):
            if r.get("reply") and isinstance(r.get("replied"), bool):
                speak.setdefault(R[pend].get("to"), []).append((pend, i))
            pend = None
    for who, rows in speak.items():
        win = rows[-SPEAK_PUSHES:]
        for a, b in win:
            K.update((a, b))
        at = {float(R[a].get("ts") or 0) for a, _ in win}
        trows = [x for x in turns.get((who, "chat", 1), []) if sum(R[x[0]].get("dwell") or ()) > 0]
        j = next((n for n, x in enumerate(trows) if float(R[x[0]].get("ts") or 0) in at), None)
        if j is not None:
            for a, b in trows[max(0, j - TURN_MEMORY):]:
                K.update((a, b))


def _keep_rung8(R, lo, N, K) -> None:
    """Rung 8's reply checks per other, medium and clock (each with what set
    its gap: its push, and that push's last changed look when it carries no
    spoke_age): the last RUNG8_FIT + RUNG8_RECENT (p_self's fit and its
    recent rate); the checks whose p is read -- the last RUNG8_LOOKS and the
    advisor's last RUNG8_TRUST per model -- and, for one of those recorded
    without its own p_self or calibration (before #254), the checks its
    recomputation reads."""
    order = sorted(range(lo, N), key=lambda i: float(R[i].get("ts") or 0))
    gap: dict = {}
    last_ch: dict = {}
    combos: dict = {}
    for i in order:
        r = R[i]
        k = r.get("kind")
        if k == "push":
            e = r.get("to")
            dep = [i]
            age = r.get("spoke_age")
            if not (isinstance(age, (int, float)) and age >= 0) and _medium(r) != "toy" and e in last_ch:
                dep.append(last_ch[e])
            gap[e] = dep
        elif k == "reach" and not r.get("audit"):
            e = r.get("eid")
            if r.get("reply") and _reply_o(r) is not None:
                combos.setdefault((e, _medium(r), _clock_of(r)), []).append([i] + gap.get(e, []))
            if r.get("answer") == "changed":
                last_ch[e] = i
    need = RUNG8_FIT + RUNG8_RECENT
    for rows in combos.values():
        ctx = set(range(max(0, len(rows) - need), len(rows)))      # the live p_self's fit and recent rate
        win = range(max(0, len(rows) - RUNG8_LOOKS), len(rows))    # the checks whose p is read
        by_model: dict = {}
        for j, row in enumerate(rows):
            if R[row[0]].get("p_llm") is not None:
                by_model.setdefault(R[row[0]].get("llm_model"), []).append(j)
        ps = set(win)                                    # ...and whose p_self is read:
        for js in by_model.values():
            ps.update(js[-RUNG8_TRUST:])                 # the advisor's calibration, per model
        for j in win:
            r = R[rows[j][0]]
            cal = r.get("llm_cal")
            if r.get("p_llm") is not None and not (isinstance(cal, (list, tuple)) and len(cal) == 2):
                ps.update([x for x in by_model.get(r.get("llm_model"), []) if x < j][-RUNG8_TRUST:])
        for j in ps:
            if R[rows[j][0]].get("p_self") is None:      # recomputed from the RUNG8_FIT checks before it
                ctx.update(range(max(0, j - need), j))
        for j in ctx | ps:
            K.update(rows[j])


def _keep_rates(R, lo, N, K) -> None:
    """The rate fits: each thing's last LAM_MAX fittable looks since its last
    shift, and before it (its own, and the seat's) -- the live fit (_lam);
    and the dream's (_replay_at): its last CHANGE_LOOKS rate-read looks and
    the CHANGE_LOOKS after each proposal not yet checked, each with the
    looks its fit reads at that moment."""
    from collections import deque
    fits = [i for i in range(lo, N) if _fittable(R[i])]
    last_shift: dict = {}
    for i in range(lo, N):
        r = R[i]
        if r.get("kind") == "shift":
            last_shift[r.get("eid")] = float(r["ts"]) if r.get("ts") is not None else None
    by: dict = {}
    for i in fits:
        by.setdefault(R[i]["eid"], []).append(i)
    for e, mine in by.items():
        since = last_shift.get(e)
        K.update([i for i in mine if since is None or float(R[i]["ts"]) > since][-LAM_MAX:])
        if since is not None:
            K.update([i for i in mine if float(R[i]["ts"]) <= since][-LAM_MAX:])
            K.update([i for i in fits if float(R[i]["ts"]) <= since][-LAM_MAX:])
    idx = _rate_looks(R)
    need = set(idx[-CHANGE_LOOKS:])
    checked = {r.get("of") for r in R if r.get("kind") == "change_check"}
    props = [r for r in R if r.get("kind") == "change"][-2 * CHANGE_WINDOW:]
    for p in props:
        if p.get("ts") not in checked and p.get("setting") in CHANGE_SETTINGS:
            need.update([i for i in idx if float(R[i]["ts"]) > float(p["ts"])][:CHANGE_LOOKS])
    if not need:
        return
    since: dict = {}
    mine: dict = {}
    seat = deque(maxlen=LAM_MAX)
    at_shift: dict = {}
    for i in range(lo, N):
        r = R[i]
        if i in need:
            e = r.get("eid")
            K.add(i)
            K.update(since.get(e, ()))
            if e in at_shift:
                K.update(at_shift[e])
        k = r.get("kind")
        if k == "shift" and r.get("eid") is not None:
            e = r["eid"]
            at_shift[e] = [i] + list(mine.get(e, ())) + list(seat)
            since[e] = deque(maxlen=LAM_MAX)
        elif _fittable(r):
            e = r["eid"]
            since.setdefault(e, deque(maxlen=LAM_MAX)).append(i)
            mine.setdefault(e, deque(maxlen=LAM_MAX)).append(i)
            seat.append(i)


def _keep_curiosity(R, lo, N, K) -> None:
    """Curiosity's windows (_progress): each thing's last 2 x CURIO_WINDOW
    ordinary looks that span no record gap, every look between them, and the
    one before (whose gap count says whether the first spans one)."""
    st = _stalls(R)
    by: dict = {}
    seen: dict = {}
    for i in range(lo, N):
        r = R[i]
        if (r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged")
                and not r.get("audit") and not r.get("audit5") and r.get("p") is not None):
            e = r["eid"]
            spans = e in seen and seen[e] != st[i]
            seen[e] = st[i]
            by.setdefault(e, []).append((i, spans))
    for rows in by.values():
        got = 0
        j = len(rows)
        while j > 0 and got < 2 * CURIO_WINDOW:
            j -= 1
            got += 0 if rows[j][1] else 1
        K.update(i for i, _ in rows[max(0, j - 1):])


def _lean(ix: _Index, K: set) -> None:
    """Keep what the kept lines lean on, so no reader pairs them differently:
    a push's end (the next push or its other's first look: T) and its first
    look stamped after it; a reply check's push; a timed look's or a check's
    last shift of its thing (the rate fit's start); words' cited look; the
    sleep line that dropped a push."""
    R = ix.recs
    todo = list(K)
    done: set = set()
    while todo:
        i = todo.pop()
        if i in done:
            continue
        done.add(i)
        r = R[i]
        k = r.get("kind")
        deps = []
        if k == "push":
            deps += [ix.T.get(i), ix.first_after(i), ix.dropped.get(float(r.get("ts") or 0)), ix.say_of.get(i)]
        elif k == "reach":
            if r.get("reply"):
                deps.append(ix.push_of.get(i))
            if _fittable(r) and (r.get("timed") or r.get("reply")):
                deps.append(ix.shift_of.get(i))
        for j in deps:
            if j is not None and j not in K:
                K.add(j)
                todo.append(j)


def _tally(recs: list, idxs, ix: _Index) -> dict:
    """The counts the readers read over a whole record, over these lines."""
    out: dict = {"cycle": 0, "push": 0, "change": 0, "change_check": 0, "to": {}, "r8": {}, "fixed": {}}
    for i in idxs:
        r = recs[i]
        k = r.get("kind")
        if k in ("cycle", "change", "change_check"):
            out[k] += 1
        elif k == "push":
            out["push"] += 1
            t = out["to"].setdefault(str(r.get("to")), {})

            def inc(name):
                t[name] = t.get(name, 0) + 1
            if r.get("aim") is not None:
                inc("aim")
            if r.get("speech") in ("words", "plain", "mute"):
                inc(r["speech"])
            if r.get("asked"):
                inc("asked")
                if float(r.get("ts") or 0) not in ix.dropped:
                    f = ix.first_after(i)
                    if f is not None:
                        inc("checked")
                        look = recs[f]
                        if look.get("p_llm") is not None and look.get("answer") in ("changed", "unchanged"):
                            inc("paired")
        elif k == "reach":
            if r.get("reply") and not r.get("audit") and _reply_o(r) is not None:
                c = out["r8"].setdefault(_r8_key(r.get("eid"), _medium(r), _clock_of(r)), [0, 0])
                c[0] += 1
                c[1] += int(_reply_o(r))
            if _ans_na(r) and not r.get("timed"):
                c = out["fixed"].setdefault(str(r.get("eid")), [0, 0])
                c[0] += 1
                c[1] += int(r["answer"] == "changed")
    return out


def _merge(a: dict, b: dict) -> dict:
    """Counts added: numbers add, dicts merge, [n, k] pairs add."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict):
            out[k] = _merge(out.get(k) or {}, v)
        elif isinstance(v, list):
            w = out.get(k) or [0] * len(v)
            out[k] = [int(x) + int(y) for x, y in zip(w, v)]
        else:
            out[k] = int(out.get(k, 0)) + int(v)
    return out


def _sync_write(path: Path, data: bytes, mode: str = "ab") -> None:
    with open(path, mode) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _journal(srv, doc: dict) -> None:
    """The consolidation's journal, synced before the step it announces."""
    d = _dir(srv)
    tmp = d / (CONSOLIDATING + ".tmp")
    _sync_write(tmp, json.dumps(doc).encode(), "wb")
    os.replace(tmp, d / CONSOLIDATING)


def _c_append(path: Path, lines: list) -> None:
    """Append raw lines to an archive file, synced."""
    _sync_write(path, b"".join(ln + b"\n" for ln in lines))


def _c_manifest(srv, entry: dict) -> None:
    _sync_write(_dir(srv) / ARCHIVE / MANIFEST, (json.dumps(entry) + "\n").encode())


def _c_swap(tmp: Path, p: Path) -> None:
    os.replace(tmp, p)


def _c_done(srv) -> None:
    (_dir(srv) / CONSOLIDATING).unlink(missing_ok=True)


def _undo(srv, j: dict) -> None:
    """Cut the archive back to the sizes the journal holds (files it created go)."""
    a = _dir(srv) / ARCHIVE
    for name, size in list((j.get("sizes") or {}).items()) + [(MANIFEST, j.get("manifest"))]:
        f = a / name
        if size is None:
            f.unlink(missing_ok=True)
        elif f.exists():
            with open(f, "r+b") as fh:
                fh.truncate(int(size))
    p = record_path(srv)
    (p.with_name(p.name + ".next")).unlink(missing_ok=True)


def _recover(srv) -> float | None:
    """A consolidation the seat died in: finish the swap (journal at "swap"),
    or undo the half-written archive (at "archive") and say when to redo it.
    -> the redo's time, or None."""
    d = _dir(srv)
    j = _load_json(srv, CONSOLIDATING) if d is not None else None
    if not isinstance(j, dict):
        return None
    p = record_path(srv)
    tmp = p.with_name(p.name + ".next")
    if j.get("stage") == "swap":
        if tmp.exists():
            os.replace(tmp, p)
        (d / CONSOLIDATING).unlink(missing_ok=True)
        return None
    _undo(srv, j)
    (d / CONSOLIDATING).unlink(missing_ok=True)
    return float(j.get("at") or time.time())


def consolidate(srv, now: float | None = None) -> dict | None:
    """SLEEP'S CONSOLIDATION (#345 part 2): archive the waking period's raw
    lines, unchanged, by their own UTC date, and leave the working record as
    one summary line (its windows and counts). Run when Blurp falls asleep
    (energy). -> {archived, kept, bytes, id}, or None when there is nothing
    new or it failed (the record is then left whole, the archive as it was)."""
    import uuid
    now = time.time() if now is None else float(now)
    p = record_path(srv)
    if p is None or not p.exists():
        return None
    with _lock:
        recs = _read(srv)
        raw = [ln for ln in p.read_bytes().split(b"\n") if ln.strip()]
        if raw and _head(recs):
            raw = raw[1:]                           # the old summary: its lines are archived already
        if not raw:
            return None
        cid = uuid.uuid4().hex
        swapped = False
        try:
            ix = _Index(recs)
            keep = sorted(_keep(recs, ix))
            kept = set(keep)
            gone = [i for i in range(ix.lo, len(recs)) if i not in kept]
            st = _stalls(recs)
            lines = [recs[i] for i in keep]
            head = {"kind": SUMMARY, "id": cid, "at": now, "n": len(lines),
                    "carry": _merge(_carry(recs), _tally(recs, gone, ix)), "stalls": [st[i] for i in keep]}
            text = (json.dumps({**head, "lines": lines}) + "\n").encode()
            runs: list = []
            files: dict = {}
            day = _day(now)
            for ln in raw:
                try:
                    t = json.loads(ln).get("ts")
                    day = _day(t) if t is not None else day
                except (ValueError, AttributeError, TypeError, OverflowError, OSError):
                    pass                            # no time of its own: its neighbour's day
                name = f"record-{day}.jsonl"
                files.setdefault(name, []).append(ln)
                if runs and runs[-1][0] == name:
                    runs[-1][1] += 1
                else:
                    runs.append([name, 1])
            a = _dir(srv) / ARCHIVE
            a.mkdir(parents=True, exist_ok=True)
            j = {"id": cid, "at": now, "stage": "archive",
                 "sizes": {n: ((a / n).stat().st_size if (a / n).exists() else None) for n in files},
                 "manifest": (a / MANIFEST).stat().st_size if (a / MANIFEST).exists() else None}
            _journal(srv, j)
            for name, ls in files.items():
                _c_append(a / name, ls)
            _c_manifest(srv, {"id": cid, "at": now, "runs": runs})
            tmp = p.with_name(p.name + ".next")
            _sync_write(tmp, text, "wb")
            _journal(srv, {**j, "stage": "swap"})
            _c_swap(tmp, p)
            swapped = True
            _c_done(srv)
        except Exception as e:                      # noqa: BLE001
            import sys
            print(f"[self] consolidation failed ({e!r}); the record stays whole", file=sys.stderr)
            if not swapped:
                j = _load_json(srv, CONSOLIDATING)
                if isinstance(j, dict) and j.get("id") == cid:
                    _undo(srv, j)
                (_dir(srv) / CONSOLIDATING).unlink(missing_ok=True)
                return None
            (_dir(srv) / CONSOLIDATING).unlink(missing_ok=True)
        rt = _rt(srv)
        rt["recs"], rt["recs_bytes"] = [head] + lines, len(text)
        for k in ("sheet", "dock", "off_field"):
            rt.pop(k, None)
        return {"archived": len(raw), "kept": len(lines), "bytes": len(text), "id": cid}


def _open_at_sleep(srv, now: float) -> dict:
    """#345: what is open as Blurp falls asleep, to be dropped -- a turn
    awaiting its reply check ({"turn": its push's ts, "to"}). An open draft
    would be named here too; none exists yet (#333)."""
    push = _pending(_read(srv), now)
    return {"turn": push.get("ts"), "to": push.get("to")} if push is not None else {}


def _manifest(srv) -> list:
    d = _dir(srv)
    f = d / ARCHIVE / MANIFEST if d is not None else None
    out = []
    if f is not None and f.exists():
        for ln in f.read_text().splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out


def archive_sig(srv) -> tuple:
    """Which consolidations the archive holds (changes only when one is added)."""
    m = _manifest(srv)
    return (len(m), m[-1].get("id") if m else None)


def _archive_lines(srv, manifest: list) -> list:
    """The archived lines in the order they were written (the manifest's runs)."""
    a = _dir(srv) / ARCHIVE
    files: dict = {}
    out = []
    for entry in manifest:
        for name, count in entry.get("runs") or ():
            it = files.get(name)
            if it is None:
                it = files[name] = iter((a / name).read_bytes().split(b"\n"))
            for _ in range(int(count)):
                ln = next(it, None)
                while ln is not None and not ln.strip():
                    ln = next(it, None)
                if ln is None:
                    break
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass                            # as _read: a line that isn't JSON is no line
    return out


def archived(srv) -> list:
    """Every archived line, oldest first (read now, from disk; never on the cycle path)."""
    return _archive_lines(srv, _manifest(srv))


def history(srv) -> list:
    """THE WHOLE RECORD (#345): the archive, then the lines since the last
    sleep -- what the record was before consolidation, for the sheet and the
    dock. Read from disk each time; never on the cycle path."""
    with _lock:
        recs = _read(srv)
        new = list(recs[_head(recs):])
        m = _manifest(srv)
    return _archive_lines(srv, m) + new


# ---- the thread -----------------------------------------------------------

def step(srv, now: float | None = None) -> dict | None:
    """One turn of the loop's thread (#345): off, paused or held, nothing runs
    and no time counts; asleep, no cycle runs and energy restores (energy.tick);
    awake, one cycle, charged its wall time and the CPU it used on this thread
    (time.thread_time around it). -> the cycle line, or None."""
    from . import energy as _E
    if not _runs(srv):
        _E.pause(srv)
        return None
    if _E.asleep(srv):
        _E.tick(srv, time.time() if now is None else now)
        return None
    t0 = time.thread_time()
    rec = cycle(srv, now)
    cpu = time.thread_time() - t0
    _E.tick(srv, time.time() if now is None else now, cpu_s=cpu)
    return rec


def start(srv) -> threading.Thread:
    """The loop: wakes on its own change (or the switch), runs one step when
    the guard allows (a cycle, or a sleeping Blurp's rest, #345), nothing when off."""
    rt = _rt(srv)
    from . import energy as _E

    def run():
        while True:
            rt["wake"].wait(timeout=PACE_S)
            rt["wake"].clear()
            try:
                if _runs(srv) and not _E.asleep(srv):
                    wait = (rt["last"] + PACE_S - time.time()) if rt["last"] else 0.0
                    if wait > 0:
                        time.sleep(wait)
                step(srv)
            except Exception as e:                          # noqa: BLE001
                import sys
                print(f"[self] cycle failed ({e!r})", file=sys.stderr)
    t = threading.Thread(target=run, name="self-loop", daemon=True)
    t.start()
    return t
