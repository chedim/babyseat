"""Per-person reply models (membrane/replies.py): a person's reply counts steer which message answers."""
from types import SimpleNamespace as NS

from membrane import facts as F
from membrane import replies as RP


def _est(talk):
    docs = {}
    for i, (who, text) in enumerate(talk):
        docs[f"c{i}"] = NS(cls=F.CLASS_CLAUSE, eid=f"c{i}",
                           properties={"msg": f"m{i}", "speaker": who, "said": float(i), "text": text})
    return NS(docs=docs)


TALK = [("Ann", "where did you go camping"), ("Bob", "we went camping at the lake"),
        ("Ann", "where was the party"), ("Bob", "the party was at the lake house"),
        ("Bob", "where is the lake"), ("Ann", "no idea honestly"),
        ("Ann", "where did you hike"), ("Bob", "we hiked at the lake trail")]


def test_person_tables_split_by_who_replies():
    R = RP.Replies(_est(TALK))
    assert R.people == ["Ann", "Bob"]
    assert R.person["Bob"].pair["where"]["lake"] == 3      # Bob answered "where" with "lake" three times
    assert R.person["Ann"].pair["where"]["lake"] == 0


def test_the_answering_persons_model_prefers_their_kind_of_reply():
    R = RP.Replies(_est(TALK))
    q = RP.words("where did Bob go")
    assert R.score(q, "m7") > R.score(q, "m4")             # Bob answers "where" ("we hiked at the lake trail"),
    #                                                        his own question "where is the lake" is no answer
    gen_bob = R.generated(q, "Bob")
    assert gen_bob.get("lake", 0) > R.generated(q, "Ann").get("lake", 0)


def test_cache_rebuilds_when_the_talk_grows():
    est = _est(TALK[:4])
    a = RP.of(est)
    est.docs["c9"] = NS(cls=F.CLASS_CLAUSE, eid="c9", properties={"msg": "m9", "speaker": "Ann", "said": 9.0,
                                                                  "text": "ok"})
    assert RP.of(est) is not a


def test_background_talk_feeds_everyones_counts_not_a_persons():
    bg = [[RP.words("where did you go"), RP.words("we went to the lake")]]
    R = RP.Replies(_est(TALK[:2]), bg)
    assert R.all.pair["where"]["lake"] == 2              # one from this talk, one from the background
    assert R.person["Bob"].pair["where"]["lake"] == 1    # the person's own counts stay this talk's
