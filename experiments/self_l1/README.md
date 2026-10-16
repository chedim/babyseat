# layer 1 in the lab: can a self find its body? (#161, 2026-10-15)

The contingency ledger (`contingency.py`) asks of every entity: how much more
often does it change in the window after *my* act than on its own? Entities that
answer nearly always, at a steady short delay, are **body**; ones that answer
above chance but unreliably or late are **others**; the rest is **world**.

    python sandbox.py      # 10 toy worlds: recovery, the null, stability, the learning curve

## the sandbox (`sandbox.py`): a world with a planted answer

`self` babbles 5 kinds of act at random (~1 per 20 steps). Planted truth:
- **4 body parts**: one answers every act, three answer one act kind each (p 0.9-0.95, delay 0-1), and each also twitches on its own now and then;
- **2 others**: answer any act about half the time, 3-10 steps late, and act on their own too;
- **24 world entities**: spontaneous churn from rare (0.001/step) to frantic (0.3/step).

Window 12 steps (wide enough to catch the slow others), 8000 steps (~400 acts), 10 seeds:

| | body found / claimed correctly | others found / claimed correctly | body found from shuffled acts (null) | same body in both halves |
|---|---|---|---|---|
| steadiness = std of delays | 0.70 / 1.00 | 1.00 / 0.63 | 0 | 0.60 |
| **steadiness = MAD of delays** | **1.00 / 1.00** | **1.00 / 0.97** | **0** | **0.97** |

Learning curve (body recall vs acts seen): **0.85** at ~28 acts, 0.93 at ~100, 0.97 at ~200, **1.00** at ~400.

Read: the std version lost a body part whenever it missed one act and its
answer to the *next* act was counted as a long delay: one stray in 70 pushed
its spread over the line, and more data didn't help. The median absolute
deviation is robust to that, and now more experience gives a better body
map, which is what layer 1 needs. The null never finds a body. The one
remaining slip is a single frantic world entity called "other" in 1 of 10
worlds.

**Caveat:** this world was designed by us, so passing it only shows that the
method can work. The next rung replays a real seat's history (its acts in the
audit/predict ledgers, the field's change pulses) and asks whether the body
map that emerges makes sense and stays stable.
