"""VOTER_WEIGHTS (2026-11-06, the operator: "each voter gets its own weight from question type -- the voter efficiency
matrix; hard-code the weights from our experiments for now"; "the ratio as-is"): the starting weights of the finders'
vote, per question type. A weight is the finder's usefulness ratio -- how many times more likely it holds a message
when that message is evidence than when it is not (its hit rate on evidence / on every other message of the talk),
measured on the quick bench, arm 65 (LoCoMo conversations 0-1, LongMemEval 8 conversations; 4 questions per LME
type, so those rows are rough). A ranker starts from these and re-estimates them; a seat ships with them.
FLAGGED: a constants table, approved as starting weights only.
"""
from __future__ import annotations

SOURCE = "qgraph arm 65 (six finders), 2026-11-06"
FINDERS = ("embedder", "word lane", "move log", "learned map", "entity recall", "latest mentions")
VOTER_WEIGHTS = {
    "single-hop":             (14.1, 15.4, 16.8, 16.3, 17.1, 13.2),
    "temporal":               (19.1, 17.5, 19.0, 19.2, 26.7, 20.2),
    "multi-hop":              (8.8, 5.5, 7.5, 11.4, 14.8, 5.8),
    "open-domain":            (8.7, 10.8, 12.1, 10.9, 21.3, 6.4),
    "knowledge-update":       (27.3, 16.3, 19.6, 23.2, 16.1, 19.6),
    "temporal-reasoning":     (22.2, 15.5, 15.5, 12.2, 24.6, 22.2),
}


def weights(qtype: str | None) -> tuple:
    """The finders' weights for a question type (its name with or without the 'LME ' prefix); equal when unknown."""
    key = str(qtype or "").replace("LME ", "")
    return VOTER_WEIGHTS.get(key, (1.0,) * len(FINDERS))
