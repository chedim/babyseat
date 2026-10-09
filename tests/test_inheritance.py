"""INHERITANCE THAT GENERALIZES (2026-10-09, the operator: "it also has entity
inheritance to do generalizations, why is it not doing that?", red first).

ISA carried constraints (living, latch, fn schemas) and pipelines, but no
BEHAVIOUR: a getter written on a class reached none of its members, so the
seat re-derived every light's read. Pinned (A): an entity without its own
script runs its nearest ancestor's, on itself; its own script wins; ticks
inherit only where the class opts in.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, CLASS_COMPONENT  # noqa: E402

GETTER = "result = {'state': {'who': state['eid'], 'id': state['properties'].get('entity_id')}}"


def _add(est, name, summary):
    r = est.add(cls=CLASS_COMPONENT, name=name, summary=summary)
    return est.docs[r["eid"]]


def _world():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    srv = type("S", (), {"efield": f, "entity_store": est, "membrane": None,
                         "capturer": None, "events_pool": None})()
    cls = _add(est, "ha light", "a Home Assistant light")
    cls.properties["getter_script"] = GETTER
    kids = []
    for n in ("aft cabin light", "port strip"):
        k = _add(est, n, "a light")
        k.properties["entity_id"] = "light." + n.replace(" ", "_")
        k.properties["parents"] = [cls.eid]
        kids.append(k)
    return srv, est, cls, kids


class TestScriptInheritance(unittest.TestCase):
    def test_a_class_getter_runs_on_each_member(self):
        srv, est, cls, kids = _world()
        for k in kids:
            r = WA.entity_read_back(srv, k, force=True)
            self.assertTrue(r["fired"], r)
            self.assertEqual(r["state"]["who"], k.eid)
            self.assertEqual(r["state"]["id"], k.properties["entity_id"])
            self.assertEqual(r["inherited_from"], cls.eid)

    def test_its_own_script_wins_and_grandparents_reach(self):
        srv, est, cls, kids = _world()
        kids[0].properties["getter_script"] = "result = {'state': {'who': 'mine'}}"
        self.assertEqual(WA.entity_read_back(srv, kids[0], force=True)["state"]["who"], "mine")
        top = _add(est, "device", "anything with a state")
        top.properties["getter_script"] = GETTER
        cls.properties.pop("getter_script")
        cls.properties["parents"] = [top.eid]
        self.assertEqual(N.effective_script(est, kids[1], "getter")[1], top.eid)

    def test_ticks_inherit_only_where_the_class_opts_in(self):
        srv, est, cls, kids = _world()
        cls.properties["tick_script"] = "result = {}"
        self.assertIsNone(N.effective_script(est, kids[0], "tick")[0])
        cls.properties["tick_inherits"] = True
        self.assertIsNotNone(N.effective_script(est, kids[0], "tick")[0])
        self.assertIsNone(N.effective_script(est, kids[0], "watch")[0])      # watch stays per entity

    def test_a_cycle_does_not_hang(self):
        srv, est, cls, kids = _world()
        cls.properties.pop("getter_script")
        cls.properties["parents"] = [kids[0].eid]
        self.assertEqual(N.effective_script(est, kids[1], "getter"), (None, None))


if __name__ == "__main__":
    unittest.main()


class TestClassInduction(unittest.TestCase):
    def _store(self):
        from membrane.entities import EntityStore
        est = EntityStore(path=None, shared=False)
        for n in ("ha:light.port_strip", "ha:light.aft_cabin", "ha:light.cabin_port_light",
                  "argus:organs:vessel", "argus:organs:body", "argus:organs:duty",
                  "shell:curl-a", "shell:curl-b", "shell:curl-c", "lonely:thing"):
            _add(est, n, "x")
        return est

    def test_families_the_names_show(self):
        from membrane import classes as C
        self.assertEqual(C.short_name("membrane::Component:ha:light.port_strip"), "ha:light.port_strip")
        self.assertEqual(C.family_key("membrane::Component:ha:light.port_strip"), "ha:light")
        self.assertIsNone(C.family_key("membrane::shell:curl-a"))           # bookkeeping kinds: never
        self.assertIsNone(C.family_key("membrane::c:agdroid"))               # a 1-letter stem says nothing
        ps = {p["stem"]: p for p in C.proposals(self._store())}
        self.assertEqual(ps["ha:light"]["n"], 3)
        self.assertEqual(ps["argus:organs"]["label"], "argus organs")
        self.assertNotIn("lonely", " ".join(ps))

    def test_one_call_classifies_a_family_and_scripts_reach_it(self):
        from membrane import classes as C
        est = self._store()
        r = C.inherit_many(est, "ha:light.*", ["ha light"])
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(r["linked"]), 3)
        self.assertEqual(len(r["minted"]), 1)
        cls = est.docs[r["minted"][0]]
        cls.properties["getter_script"] = GETTER
        kid = next(d for e, d in est.docs.items() if e.endswith("ha:light.port_strip"))
        self.assertEqual(N.effective_script(est, kid, "getter")[1], cls.eid)
        self.assertNotIn("ha:light", {p["stem"] for p in C.proposals(est)})  # not proposed again

    def test_a_too_wide_pattern_is_refused(self):
        from membrane import classes as C
        est = self._store()
        self.assertFalse(C.inherit_many(est, "nomatch*", ["x"])["ok"])
