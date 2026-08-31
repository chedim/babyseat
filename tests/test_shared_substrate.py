"""Tests for the shared-substrate layer (origin axis + veil + CAN arbitration).

Plain-python runnable (no pytest dependency):
    .venv/bin/python -m tests.test_shared_substrate

The couchbase SDK is not needed: a fake collection object with the same
surface (get/insert/upsert/remove, CAS semantics, query stub) stands in for
the bucket, so arbitration is tested in-process with no server.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from membrane.field import (
    Exemplar, ExemplarStore, _ex_key, _situ_key, _origin_tag,
    ALTERNATES_MAX, VEIL_POKE_SIM,
)


class FakeResult:
    def __init__(self, doc, cas):
        self._doc = doc
        self.cas = cas

    @property
    def content_as(self):
        class _CA:
            def __getitem__(_, t):
                return self._doc
        return _CA()


class CasMismatch(Exception):
    pass


class Exists(Exception):
    pass


class NotFound(Exception):
    pass


class FakeColl:
    """CAS-faithful stand-in for a Couchbase collection."""

    def __init__(self):
        self.docs = {}
        self._cas = 0

    def get(self, k):
        if k not in self.docs:
            raise NotFound(k)
        d, c = self.docs[k]
        return FakeResult(dict(d), c)

    @property
    def cas(self):
        return 0  # unused; FakeResult carries nothing

    def insert(self, k, doc):
        if k in self.docs:
            raise Exists(k)
        self._cas += 1
        self.docs[k] = (dict(doc), self._cas)

    def upsert(self, k, doc, cas=None):
        if cas is not None:
            cur = self.docs.get(k)
            if cur is None or cur[1] != cas:
                raise CasMismatch(k)
        self._cas += 1
        self.docs[k] = (dict(doc), self._cas)

    def remove(self, k):
        self.docs.pop(k, None)


def _vec(seed: float, dim: int = 16) -> list[float]:
    v = np.zeros(dim)
    v[int(seed) % dim] = 1.0
    return list(v) + [0.1] * 0


def _store(origin: str = "", coll: FakeColl | None = None) -> ExemplarStore:
    st = ExemplarStore()
    st.origin = origin
    st._cb = coll if coll is not None else FakeColl()
    st._cb_keys = set()
    return st


def test_keys_namespaced_by_origin():
    a = Exemplar(situation="deploy", action="A", outcome="pending",
                 vec=_vec(1), origin="m@lap")
    b = Exemplar(situation="deploy", action="A", outcome="pending",
                 vec=_vec(1), origin="m@desk")
    assert _ex_key(a) != _ex_key(b)          # same Q/A, separate patches
    assert _situ_key(a.situation) == _situ_key(b.situation)  # same contention
    # legacy rows (origin "") hash as the old scheme did
    old = Exemplar(situation="deploy", action="A", outcome="pending", vec=_vec(1))
    import hashlib
    legacy = hashlib.sha1("deploy\x00A".encode()).hexdigest()[:20]
    assert _ex_key(old) == legacy
    print("ok_keys_namespaced")


def test_save_stamps_origin_and_never_writes_foreign():
    coll = FakeColl()
    st = _store("proj@lap", coll)
    # pre-existing remote foreign doc (they wrote it, not us)
    theirs = _ex_key(Exemplar(situation="deploy", action="theirs",
                              outcome="pending", vec=_vec(1),
                              origin="proj@desk"))
    coll.docs[theirs] = ({"situation": "deploy", "action": "theirs",
                          "outcome": "pending", "vec": _vec(1),
                          "origin": "proj@desk"}, 1)
    st.append(Exemplar(situation="deploy", action="mine", outcome="pending",
                       vec=_vec(1)))                 # born here, origin blank
    st.append(Exemplar(situation="deploy", action="theirs", outcome="pending",
                       vec=_vec(1), origin="proj@desk"))  # foreign scenery
    st._save_couchbase()
    keys = list(coll.docs)
    mine = _ex_key(Exemplar(situation="deploy", action="mine",
                            outcome="pending", vec=_vec(1), origin="proj@lap"))
    assert mine in keys
    assert coll.docs[mine][0]["origin"] == "proj@lap"   # stamped at the wire
    # the foreign row survives untouched by further saves (never re-written)
    before = coll.docs[theirs]
    st._save_couchbase()
    st._save_couchbase()
    assert coll.docs[theirs] == before
    print("ok_origin_stamped")


def test_save_never_deletes_foreign_or_sit_docs():
    coll = FakeColl()
    st = _store("proj@lap", coll)
    coll.docs["foreign-doc"] = ({"situation": "x"}, 1)
    coll.docs["sit:abc"] = ({"ex_key": "foreign-doc", "origin": "proj@desk"}, 1)
    st.append(Exemplar(situation="deploy", action="mine", outcome="pending",
                       vec=_vec(1)))
    st._save_couchbase()
    st._cb_keys = {k for k in st._cb_keys} | {"ghost-from-boot"}
    st._save_couchbase()                       # ghost is "mine" -> removed
    assert "ghost-from-boot" not in coll.docs
    assert "foreign-doc" in coll.docs and "sit:abc" in coll.docs
    print("ok_no_foreign_deletes")


def test_arbitration_winner_and_loser_paths():
    # incumbent (theirs) on the channel; salience needs a lived field beyond
    # the contender itself (a lone exemplar scores 0 by design)
    def run(theirs_action: str, my_vec_seed: int, their_sal: float,
            support_seed: int):
        coll = FakeColl()
        st = _store("proj@lap", coll)
        if support_seed is not None:
            st.append(Exemplar(situation="support topic", action="helper",
                               outcome="pending", vec=_vec(support_seed)))
        their_key = "k-theirs"
        coll.docs[their_key] = ({
            "situation": "deploy", "action": theirs_action,
            "outcome": "pending", "vec": _vec(3),
            "origin": "proj@desk"}, 1)
        channel = "sit:" + _situ_key("deploy")
        coll.docs[channel] = ({"ex_key": their_key, "origin": "proj@desk",
                               "salience": their_sal, "alternates": []}, 1)
        mine = Exemplar(situation="deploy", action="mine", outcome="pending",
                        vec=_vec(my_vec_seed))
        st.append(mine)
        st._save_couchbase()
        return coll, channel, mine, st

    # WIN: my field resonates with my contender (support shares its basis) ->
    # salience ~1.0 beats their 0.1 claim: I master, their action defers
    coll, channel, mine, st = run("theirs", my_vec_seed=5, their_sal=0.1,
                                  support_seed=5)
    ch = coll.docs[channel][0]
    assert ch["origin"] == "proj@lap"
    alts = ch["alternates"]
    assert len(alts) == 1 and alts[0]["action"] == "theirs" \
        and alts[0]["origin"] == "proj@desk"
    assert any(a["action"] == "theirs" for a in mine.alternates)
    # LOSE: my contender is directionally isolated (support elsewhere) ->
    # salience ~0 loses to their 0.99 claim: channel stays theirs, MY action
    # defers on the channel, their doc lands in my RAM as read-only scenery
    coll, channel, mine, st = run("theirs", my_vec_seed=1, their_sal=0.99,
                                  support_seed=2)
    ch = coll.docs[channel][0]
    assert ch["origin"] == "proj@desk"
    assert any(a["action"] == "mine" for a in ch["alternates"])
    assert any(e.origin == "proj@desk" and e.action == "theirs"
               for e in st.exemplars)
    print("ok_arbitration_paths")


def test_no_contention_lost():
    """The loser is never destroyed: after losing, my action survives on the
    channel's alternates list (non-destructive CAN queuing)."""
    coll = FakeColl()
    st = _store("proj@lap", coll)
    coll.docs["k-theirs"] = ({"situation": "deploy", "action": "theirs",
                              "outcome": "pending", "vec": _vec(3)}, 1)
    channel = "sit:" + _situ_key("deploy")
    coll.docs[channel] = ({"ex_key": "k-theirs", "origin": "proj@desk",
                           "salience": 0.99, "alternates": []}, 1)
    mine = Exemplar(situation="deploy", action="mine", outcome="pending",
                    vec=_vec(2))
    st.append(mine)
    st._save_couchbase()
    alts = coll.docs[channel][0]["alternates"]
    assert any(a["action"] == "mine" for a in alts), "loser must defer, not die"
    st._save_couchbase()                       # idempotent replay: no dup
    assert sum(1 for a in coll.docs[channel][0]["alternates"]
               if a["action"] == "mine") == 1
    print("ok_loser_survives")


def test_veil_poke_through():
    # poke geometry: basis vectors are orthogonal, so make the "far" probe
    # basis-identical to a NON-held exemplar and the "near" one to a held one.
    # My store: exemplar at basis 7 only. _vec(7) vs _vec(3) are orthogonal
    # (cosine 0), far below VEIL_POKE_SIM; _vec(7) vs _vec(7) is 1.0.
    st = _store("proj@lap")
    st.exemplars = [Exemplar(situation="7", action="mine", outcome="pending",
                             vec=_vec(7))]
    foreign_close = {"situation": "7", "action": "theirs",
                     "outcome": "pending", "vec": _vec(7),
                     "origin": "proj@desk"}
    # adoption itself doesn't cosine; the poke threshold does
    assert st._adopt_foreign_doc(dict(foreign_close)) is True
    assert st._adopt_foreign_doc(dict(foreign_close)) is False  # idempotent
    V = np.asarray([e.vec for e in st.exemplars], dtype=float)
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    far = np.asarray(_vec(3), dtype=float)
    far = far / (np.linalg.norm(far) + 1e-12)
    near = np.asarray(_vec(7), dtype=float)
    near = near / (np.linalg.norm(near) + 1e-12)
    assert float((Vn @ far).max()) < VEIL_POKE_SIM, "far stays behind the veil"
    assert float((Vn @ near).max()) >= VEIL_POKE_SIM, "near pokes through"
    print("ok_veil_poke")


def test_origin_tag_shape():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # per-project layout: <proj>/.membrane/field.json -> START folder
        (tdp / "myproj" / ".membrane").mkdir(parents=True)
        t = _origin_tag(tdp / "myproj" / ".membrane" / "field.json")
        assert t.split("@")[0] == "myproj", t
        assert "@" in t and t.split("@", 1)[1], t  # hostname present
        # bare field file (no data dir): folder is the file's own dir
        (tdp / "plain").mkdir()
        t2 = _origin_tag(tdp / "plain" / "field.json")
        assert t2.split("@")[0] == "plain", t2
    print("ok_origin_tag")


if __name__ == "__main__":
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)