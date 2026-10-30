"""#687 (part B, the operator's amendment): each recalled line carries the
branch that was checked out when its event happened, read from git:branch's
readings ring -- provenance only: no row dropped, no order changed."""
import types

from membrane import recall_banner as RB

H = 3600.0
NOW = 100 * H


def _row(n, ts, fact="a fact"):
    return {"a_n": [n], "q_n": n, "ts": ts, "via": "direct", "role": "model", "fact": fact}


def _est(readings):
    ent = types.SimpleNamespace(eid="membrane::Component:git:branch",
                                properties={"readings": readings})

    def resolve(spec, exact=False):
        return ent if spec == RB.BRANCH_ENTITY else None
    return types.SimpleNamespace(resolve=resolve)


RING = [{"ts": NOW - 10 * H, "v": {"branch": "main", "head": "a1"}},
        {"ts": NOW - 5 * H, "v": {"branch": "feature-x", "head": "b2"}},
        {"ts": NOW - 1 * H, "v": {"branch": "main", "head": "c3"}}]


def test_periods_from_the_readings_ring():
    assert RB.branch_periods(_est(RING)) == [(NOW - 10 * H, "main"), (NOW - 5 * H, "feature-x"),
                                             (NOW - 1 * H, "main")]


def test_an_event_inside_feature_x_gets_its_label():
    p = RB.branch_periods(_est(RING))
    assert RB.branch_at(p, NOW - 2 * H) == "feature-x"
    line = RB.line_of(_row(7, NOW - 2 * H), NOW, branches=p)
    assert line.startswith("▸ #7 (2h ago, feature-x, ")


def test_before_the_timeline_no_label():
    p = RB.branch_periods(_est(RING))
    assert RB.branch_at(p, NOW - 20 * H) is None
    assert RB.line_of(_row(7, NOW - 20 * H), NOW, branches=p) == RB.line_of(_row(7, NOW - 20 * H), NOW)


def test_no_entity_no_label_and_lines_byte_identical():
    est = types.SimpleNamespace(resolve=lambda spec, exact=False: None)
    assert RB.branch_periods(est) == []
    assert RB.branch_periods(None) == []
    r = _row(3, NOW - 2 * H)
    assert RB.line_of(r, NOW, branches=[]) == RB.line_of(r, NOW)
    assert RB.line_of(r, NOW).startswith("▸ #3 (2h ago, ")


def test_the_label_never_reorders_or_drops():
    p = RB.branch_periods(_est(RING))
    rows = [_row(1, NOW - 2 * H), _row(2, NOW - 30 * H), _row(3, NOW - 0.5 * H)]
    plain = [RB.line_of(r, NOW) for r in rows]
    lab = [RB.line_of(r, NOW, branches=p) for r in rows]
    assert len(lab) == len(plain)
    assert [ln.split(" ")[1] for ln in lab] == [ln.split(" ")[1] for ln in plain]


def test_a_ring_without_branch_values_gives_no_periods():
    assert RB.branch_periods(_est([{"ts": 1.0, "v": {"other": 1}}])) == []


def test_query_hits_carry_the_same_label():
    p = RB.branch_periods(_est(RING))
    assert RB.hit_branch({"ts": NOW - 2 * H}, p) == "feature-x"
    assert RB.hit_branch({"ts": NOW - 20 * H}, p) is None
