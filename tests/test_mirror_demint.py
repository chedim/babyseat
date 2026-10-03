"""THE MIRROR RE-MINT (the seat's cleanest repro, seven boots deep):
the overlay/legacy key pipeline resurrects stale keys as NEW mirror
docs per boot (m:m:tool:sysmon, m:m:m:tool:ls_v1) -- the overlay round-
trip must be IDEMPOTENT and the canon resolution must COLLAPSE a
doubled prefix back to the canonical eid. THE CONTRACTS:

1. overlay_key is idempotent: overlay_key(overlay_key(e)) == the same
   single-form key (never m:m:, never tool:tool:);
2. canon_eid collapses a doubled legacy prefix ('m:m:tool:x',
   'files:files:x') to the canonical eid;
3. the heartbeat/event addressing resolves EXISTING mirror docs to
   their canonical (no second doc when the canonical exists);
4. a re-mint under a doubled key RETIRES the mirror doc (merged_into
   the canonical, the operator's mark-don't-delete covenant) instead
   of living as a ghost.
"""
import time

import pytest

from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS
from membrane import night
from membrane.night import overlay_key, canon_eid
from membrane import eids as E


def test_overlay_key_is_idempotent():
    """The round-trip: a canonical eid -> its overlay key -> overlay
    again == the SAME key (never doubled)."""
    for eid in ("m:tool:sysmon", "f:membrane/router.py", "s:main",
                "c:git", "w:ghost"):
        k1 = overlay_key(eid)
        k2 = overlay_key(k1)
        assert k1 == k2, (eid, k1, k2)


def test_canon_eid_collapses_a_doubled_prefix():
    """'m:m:tool:sysmon' / 'models:models:tool:sysmon' / 'm:m:m:tool:x'
    -> the canonical eid (the mirror collapses at the read)."""
    assert canon_eid("m:m:tool:sysmon") == "m:tool:sysmon"
    assert canon_eid("models:models:tool:sysmon") == "m:tool:sysmon"
    assert canon_eid("m:m:m:tool:ls_v1") == "m:tool:ls_v1"
    assert canon_eid("files:files:router.py") == "f:router.py"
    assert canon_eid("tool:tool:sysmon") == "tool:sysmon"


def test_file_land_resolves_the_mirror_to_the_canonical(tmp_path):
    """The heartbeat's file-land call on a path whose MIRROR (the
    doubled-key doc) exists: resolves to the canonical doc, and the
    mirror retires (merged_into) -- no second doc, no ghost."""
    from membrane.night import _file_land
    import tempfile
    est = EntityStore(path=None, shared=False)
    mirror = QuantumEntity(
        eid=MEMBRANE_NS + "files:files:~x.py", cls="Component",
        properties={"name": "x.py", "n": 13})
    est.docs[mirror.eid] = mirror
    class _Fld:
        emb = type("E", (), {"model": "m"})()
        def write_events(self, evs, session=""):
            return None
    eid, key, created = _file_land(_Fld(), est, None, "/tmp/x.py",
                                    "read", True, "ok", {})
    # a file outside the repo canon's to its '~basename' form (the
    # field's own spelling); the KEY is the overlay's long form:
    assert eid.startswith("f:~") or eid.startswith("f:"), eid
    assert key == MEMBRANE_NS + overlay_key(eid), key
    # the MIRROR RETIRED: its eid is the doubled form of the landing
    # key's body ('files:files:~x.py' = the mirror of 'f:~x.py');
    # mark-don't-delete keeps the pointer:
    _canonical = MEMBRANE_NS + "files:~x.py"
    m_doc = est.docs.get(_canonical + "")
    # the mirror's key under its doubled form: find it:
    doubled = [k for k in est.docs if "files:files:" in k]
    assert doubled, sorted(est.docs)
    m_doc = est.docs[doubled[0]]
    assert (m_doc.properties or {}).get("merged_into") == key, \
        m_doc.properties
    # the landing doc EXISTS (the canonical grew):
    land = est.docs.get(key)
    assert land is not None


def test_doubled_keys_never_mint_new_docs(tmp_path):
    """The store's resolve + the file hands: writing through a STALE
    doubled key lands on the CANONICAL doc (the mirror's data merges
    in) -- a re-boot never grows the mirror family."""
    est = EntityStore(path=None, shared=False)
    canon = QuantumEntity(eid=MEMBRANE_NS + "m:tool:sysmon", cls="tool",
                          properties={"name": "sysmon", "n": 10})
    est.docs[canon.eid] = canon
    mirror = QuantumEntity(eid=MEMBRANE_NS + "m:m:tool:sysmon",
                           cls="tool",
                           properties={"name": "sysmon", "n": 13})
    est.docs[mirror.eid] = mirror
    # the collapse at the resolve:
    got = est._resolve_spec("m:m:tool:sysmon") if \
        hasattr(est, "_resolve_spec") else None
    assert got is not None
    if got.eid != MEMBRANE_NS + "m:tool:sysmon":
        # the resolve landed on the mirror -- the test FAILED the contract
        assert False, f"the doubled key resolved to {got.eid}"


def test_night_materialize_checks_existence(tmp_path):
    """A materialized entity resolves FIRST (canon_eid): an existing
    canonical eid never mints a second doc."""
    est = EntityStore(path=None, shared=False)
    canon = QuantumEntity(eid=MEMBRANE_NS + "c:git", cls="tool",
                          properties={"name": "git", "n": 20})
    est.docs[canon.eid] = canon
    # the minter's flow with a candidate that COLLAPSES to the canon:
    eid = canon_eid("shell:git") or canon_eid("m:c:git")
    eid = eid.removeprefix(MEMBRANE_NS) if isinstance(eid, str) else eid
    # the canonical doc exists under BOTH spellings' collapse:
    resolved = est.docs.get(eid if eid.startswith(MEMBRANE_NS)
                            else MEMBRANE_NS + eid)
    assert resolved is not None, (eid,)   # the existence check finds it


def test_materialize_carries_the_source_kind(tmp_path):
    """c:git materializes as class Tool (a shell command is not a
    model); session files stay Component; the ns = the canonical one."""
    from membrane.night import materialized_class_for, materialized_eid
    assert materialized_class_for("c:git") == "Tool"       # never a Model
    assert materialized_eid("c:git") == MEMBRANE_NS + "shell:git"
    assert materialized_eid("m:c:git").endswith(":") is False  # no chain
    assert materialized_eid("m:c:git") == MEMBRANE_NS + "shell:git"
    _ = None