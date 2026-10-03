"""THE TWO UNIVERSES (the seat's datum, now a contract): the graph
surfaces answer from ONE universe per query. entity_query's kind verbs
understand the legacy kind spellings (file/files/f, symbols/s,
models/m, shell/c, urls/u -- the eids._LEGACY_KIND family, both
directions); imported guests (tier='guest', the pull lane's flavor:
entity::cortex::*, episodic::*) are PARTITIONED -- hidden by default,
visible only when the query asks (guest:true / imported_by:X), and a
stale absolute path naming a dead root retires instead of living
forever."""
import json
import time

from membrane.entities import EntityStore, QuantumEntity
from membrane.entity_query import run, parse


def _store(tmp):
    import pathlib
    tmp = pathlib.Path(tmp) if isinstance(tmp, str) else tmp
    st = EntityStore(path=str(tmp / "ents.json"), shared=False)
    f_local = QuantumEntity(eid="membrane::f:membrane/router.py",
                            cls="Component",
                            properties={"name": "router"})
    f_legacy = QuantumEntity(eid="membrane::files:users/moved/membrane/x.py",
                             cls="Component",
                             properties={"name": "x"})
    guest_c = QuantumEntity(eid="entity::cortex::couchbase_db",
                            cls="Component",
                            properties={"name": "couchbase_db"})
    guest_e = QuantumEntity(
        eid="episodic::cortex_action_call_homeassistant_service_0",
        cls="Component", properties={"name": "ha call"})
    for e in (f_local, f_legacy, guest_c, guest_e):
        st.docs[e.eid] = e
    return st


def test_kind_alias_file_matches_the_family():
    """kind:file:* / kind:files:* / kind:f:* all find the f:/files: eids
    and nothing else."""
    import tempfile
    st = _store(tempfile.mkdtemp())
    r_file = run(st, "kind:file:*", count=True)
    r_files = run(st, "kind:files:*", count=True)
    r_f = run(st, "kind:f:*", count=True)
    assert r_file == r_files == r_f, (r_file, r_files, r_f)
    assert "2" in r_file, (r_file,)         # 2 matching eids (f: + files:)


def test_kind_symbols_alias():
    import tempfile
    st = _store(tempfile.mkdtemp())
    st.docs["membrane::s:entity_query.run"] = QuantumEntity(
        eid="membrane::s:entity_query.run", cls="Component",
        properties={"name": "run"})
    st.docs["membrane::symbols:entity_query.parse"] = QuantumEntity(
        eid="membrane::symbols:entity_query.parse", cls="Component",
        properties={"name": "parse"})
    assert "2" in run(st, "kind:symbol:*", count=True)
    assert "2" in run(st, "kind:s:*", count=True)
    assert "2" in run(st, "kind:symbols:*", count=True)


def test_guests_hidden_by_default_visible_on_ask():
    import tempfile
    st = _store(tempfile.mkdtemp())
    # the cortex/episodic eids are guests by flavor: the default scan
    # hides them; the asker's term surfaces them:
    assert "couchbase" not in run(st, "match-all") .__str__()
    rows = run(st, "match-all")
    assert all(not r["eid"].startswith(("entity::cortex::", "episodic::"))
               for r in rows), rows
    asked = run(st, "match-all & guest:true")
    assert any("cortex" in r["eid"] for r in asked), asked


def test_guest_flavor_by_classname(tmp_path):
    """Entity::cortex::/episodic:: eids are guests EVEN without the
    pull metadata: the eid's namespace IS the flavor -- stamped at
    the load door (a file with such eids loads guest-flagged)."""
    import json as _json, tempfile, pathlib as _pl
    d = tmp_path / "ents.json"
    d.write_text(_json.dumps({"docs": {
        "entity::cortex::couchbase_db": {
            "meta": {"type": "QuantumEntity"},
            "data": {"class": "Component",
                     "properties": {"name": "couchbase_db"}}},
        "membrane::f:membrane/router.py": {
            "meta": {"type": "QuantumEntity"},
            "data": {"class": "Component",
                     "properties": {"name": "router"}}}}}))
    st = EntityStore(path=str(d), shared=False)
    g = st.docs.get("entity::cortex::couchbase_db")
    loc = st.docs.get("membrane::f:membrane/router.py")
    assert g is not None and (g.properties or {}).get("guest") is True
    assert loc is not None and not (loc.properties or {}).get("guest")


def test_stale_absolute_path_retires_at_pull(tmp_path):
    """The pull lane's stale-route defense: an imported eid whose body
     names an absolute path OUTSIDE the seat's root gets retired=True
     (a dead route, not a living ghost)."""
    from membrane.entities import MEMBRANE_NS
    st = EntityStore(path=str(tmp_path / "ents.json"), shared=False)
    st.origin = "membrane@G0XW2RC6K3"
    st.root = str(tmp_path)                    # the seat's root
    doc = {"meta": {"type": "QuantumEntity"},
           "data": {"class": "Component",
                    "properties": {"name": "old file"}},
           "published": {"by": "agatha@boat", "eid": "membrane::files:"
                         "/Users/old/agatha/membrane/opencode.py"}}
    eid = (doc.get("published") or {}).get("eid")
    q = QuantumEntity.from_doc(eid, doc)
    assert q is not None
    props = q.properties or {}
    props.update(EntityStore._strip_code(props))
    props.update({"imported_by": "agatha@boat", "guest": True})
    # THE RETIRE RULE (what import_published now stamps): an absolute
    # body outside the seat's root is a dead route.
    body = eid[len(MEMBRANE_NS):] if eid.startswith(MEMBRANE_NS) else eid
    _head, _, _rest = body.partition(":")     # the legacy head strips
    if _head in ("files", "symbols", "models", "shell", "urls"):
        body = _rest
    root = st.root
    if body.startswith("/") and root and not body.startswith(root):
        props["retired"] = True
        props["retired_ts"] = time.time()
    q.properties = props
    st.docs[eid] = q
    assert (st.docs[eid].properties or {}).get("retired") is True