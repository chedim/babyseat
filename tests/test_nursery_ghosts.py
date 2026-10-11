"""THE NURSERY GHOSTS (2026-10-11, Argus's lesson: "chopped turns can land
mangled-fragment entities (m:m:...-style) that the graph layer reads but query
and remove can never reach -- poking at them replicates them"). The model-tag
regex minted any word:word token; a 404 naming 'm:a:b' minted 'm:m:a:b'."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import eids as E  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

GHOSTS = ["the c:-prefixed ids", "a:b", "mac 4c:ba:c4:f5", "c:...:fisher3", "adb:5565 /api:states",
          "no entity matches spec 'm:a:b'"]


def ms(text):
    return [e for e, _ in E.mentions_of(text) if e.startswith("m:")]


class TestTheMintPoint(unittest.TestCase):
    def test_fragments_are_not_names(self):
        for t in GHOSTS:
            self.assertEqual([e for e in ms(t) if E.is_junk_tag(e[2:])], [], t)
        for frag in ("c:-prefixed", "a:b", "ba:c4:f5", "c:...:fisher3", "c:agdroid...exec", "m:a:b"):
            self.assertTrue(E.is_junk_tag(frag), frag)
        self.assertNotIn("m:m:a:b", ms("no entity matches spec 'm:a:b'"))
        self.assertNotIn("m:ba:c4:f5", ms("mac 4c:ba:c4:f5"))
        self.assertNotIn("m:adb:5565", ms("adb:5565"))

    def test_real_models_still_land(self):
        self.assertIn("m:glm-5.3-flash:cloud", ms("the seat runs glm-5.3-flash:cloud"))
        self.assertTrue(E.is_model_tag("qwen3:8b"))
        self.assertTrue(E.is_model_tag("llama3:latest"))
        self.assertFalse(E.is_model_tag("api:states"))
        self.assertFalse(E.is_model_tag("c:agdroid"))


def field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")


def say(f, texts):
    f.write_events([ST.Event(stream="discourse", role="user", text=t, ts=1000.0 + i) for i, t in enumerate(texts)],
                   session="s1")
    f.process()


def m_states(f):
    return sorted(d["eid"] for _, d in f.b.scan("entity_states") if str(d.get("eid", "")).startswith("m:"))


class TestTheField(unittest.TestCase):
    def test_a_store_name_lands_a_fragment_does_not(self):
        f = field()
        f.name_ok = lambda bare: bare == "component:phone:battery"
        say(f, ["read Component:phone:battery now", "and api:states and ok:go and x:yz:wq"])
        self.assertEqual(m_states(f), ["m:component:phone:battery"])

    def test_the_purge_takes_states_deltas_relations_and_mentions(self):
        f = field()
        say(f, ["first api:states then hello:world", "again api:states with hello:world"])
        before = m_states(f)
        self.assertIn("m:api:states", before)                    # the old permissive field (no name check)
        f.name_ok = lambda bare: False
        dry = f.purge_ghosts(dry_run=True)
        self.assertEqual(dry["ghosts"], len(before))
        self.assertEqual(m_states(f), before)                    # dry: nothing touched
        out = f.purge_ghosts(dry_run=False)
        self.assertEqual(m_states(f), [])
        self.assertGreaterEqual(out["events"], 1)
        self.assertFalse([d for _, d in f.b.scan("entity_deltas") if str(d.get("eid", "")).startswith("m:")])
        for _, ev in f.b.scan("events"):
            self.assertFalse([m for m in ev.get("mentions") or [] if str(m[0]).startswith("m:")])
        f.rebuild()
        self.assertEqual(m_states(f), [])                        # and a rebuild does not mint them again


if __name__ == "__main__":
    unittest.main()
