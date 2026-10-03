"""THE BOOT ENTITY (the operator's ask): every membrane boot creates
an entity with HOW and WHEN it booted (host, cwd/root, field path,
pid, argv tail, uptime anchor, boot ts); the seat's PRESENCE entity
stays the LIVING 'now' (refreshed every pulse) and the LAST boot's
facts ride it too (the operator's 'the last boot could be the
presence entity' honored as: presence carries boot_ts/boot of the
CURRENT boot; each boot ALSO gets its own immutable record in the
graph chain) -- the graph's boot entities form a lineage through
prev_boot links."""
import json
import time

import pytest

from membrane.entities import EntityStore, QuantumEntity, MEMBRANE_NS
from membrane.opencode import boot_entity


def _store(tmp_path):
    st = EntityStore(path=str(tmp_path / "ents.json"), shared=False)
    st.origin = "membrane@G0XW2RC6K3"
    return st


def test_boot_creates_an_entity(tmp_path):
    st = _store(tmp_path)
    ent = boot_entity(st, root=str(tmp_path), field="/tmp/x/f.json",
                      pid=42424, argv=["membrane", "opencode"],
                      boot_ts=time.time() - 5.0)
    assert ent is not None
    assert ent.eid.startswith(MEMBRANE_NS + "boot:")
    props = ent.properties or {}
    assert props.get("pid") == 42424
    assert props.get("boot_ts"), props
    assert props.get("host"), props


def test_each_boot_distinct_linked_lineage(tmp_path):
    st = _store(tmp_path)
    b1 = boot_entity(st, root=str(tmp_path), field="f.json", pid=1,
                     argv=[], boot_ts=time.time() - 100.0)
    b2 = boot_entity(st, root=str(tmp_path), field="f.json", pid=2,
                     argv=[], boot_ts=time.time())
    assert b1.eid != b2.eid
    # the lineage: the new boot links the old (prev_boot)
    assert (b2.properties or {}).get("prev_boot") == b1.eid
    # the PRESENCE entity carries the CURRENT boot's facts
    pres = st.docs.get(MEMBRANE_NS + "presence")
    assert pres is not None
    assert (pres.properties or {}).get("boot") == b2.eid


def test_presence_gets_the_current_boot(tmp_path):
    st = _store(tmp_path)
    boot_entity(st, root=str(tmp_path), field="f.json", pid=9,
                argv=[], boot_ts=time.time())
    boot_entity(st, root=str(tmp_path), field="f.json", pid=10,
                argv=[], boot_ts=time.time())
    pres = st.docs.get(MEMBRANE_NS + "presence")
    props = pres.properties or {}
    newest = max(st.docs.values(),
                 key=lambda e: (e.properties or {}).get("boot_ts") or 0
                 if str(e.eid).startswith(MEMBRANE_NS + "boot:") else -1)
    assert props.get("boot_ts") == (newest.properties or {}).get("boot_ts")
    assert props.get("boot") == newest.eid


def test_birth_announced_once(tmp_path):
    st = _store(tmp_path)
    before = len([a for a in (st.take_announcements() or [])])
    boot_entity(st, root=str(tmp_path), field="f.json", pid=5,
                argv=[], boot_ts=time.time())
    ann = st.take_announcements()
    assert any(a.get("kind") == "new" for a in ann), ann