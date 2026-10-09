"""WHO SAID IT (2026-10-09, the operator: "don't my messages appear as
operator-linked entities on the field? ... should LLM's messages appear
the same way? ... we should unify the way information flows to and thru
membrane", red first).

A person existed only as the WORDS of the HA proxy's speaker line
(w:dmitrii, 2317 cells of header) and the model's replies named no one.
Pinned: every discourse event names its speaker as an entity, in the one
place events become mentions (so every intake lane gets it): person:<slug>
from the speaker line, person:operator without one, seat:self for the
model's own words (never its chosen name); the speaker line is cut from
the embedded prose (the stored text keeps it); tools/code speak for no one.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _ef_fakes as _fakes  # noqa: E402
from membrane import speakers as SP  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402

HEAD = "[HA user: Dmitrii Chechetkin]\n"


class TestResolve(unittest.TestCase):
    def test_who(self):
        ev = lambda role, text, stream="discourse": {"stream": stream, "role": role, "text": text}  # noqa: E731
        self.assertEqual(SP.speaker_of(ev("user", HEAD + "how is the battery?")),
                         ("person:dmitrii-chechetkin", "Dmitrii Chechetkin"))
        self.assertEqual(SP.speaker_of(ev("user", "no header here")), SP.OPERATOR)
        self.assertEqual(SP.speaker_of(ev("model", "it is fine")), SP.SEAT)
        self.assertIsNone(SP.speaker_of(ev("", "ls", stream="observation")))
        self.assertIsNone(SP.speaker_of(ev("note", "a lesson")))
        self.assertEqual(SP.prose_of(ev("user", HEAD + "how is it?")), "how is it?")
        self.assertEqual(SP.split_speaker("[HA user: Rudy Telson] hi")[0], "Rudy Telson")


class TestTheField(unittest.TestCase):
    def _field(self):
        f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
        for i in range(4):
            f.write_events(ST.split_turn(
                HEAD + f"situation {i}: the jibberword ladder is stuck again near the anchor",
                f"we unfroze action {i} and the router walked its ladder down slowly"),
                session="s")
        f.write_events(ST.split_turn("[HA user: Rudy Telson]\nthe dinghy outboard needs fresh fuel today", ""),
                       session="p")
        f.process()
        return f

    def _events(self, f):
        return [d for _, d in f.b.scan("events", {"origin": f.origin})]

    def test_speakers_are_live_entities(self):
        f = self._field()
        for eid in ("person:dmitrii-chechetkin", "seat:self", "person:rudy-telson"):
            st = f.get(eid)
            self.assertIsNotNone(st, eid)
            self.assertEqual(st.tier, "live", eid)

    def test_every_event_names_its_speaker(self):
        f = self._field()
        for d in self._events(f):
            if d.get("stream") != "discourse" or not d.get("sids"):
                continue
            self.assertEqual(d["speaker"][0], SP.speaker_of(d)[0], d.get("text"))
            self.assertNotIn(d["speaker"][0], [m[0] for m in d.get("mentions") or []])  # not a topic

    def test_speakers_talk_about_topics(self):
        f = self._field()
        rels = [r for _, r in f.b.scan("relations", {"origin": f.origin}) if r.get("rel") == "talks_about"]
        self.assertTrue(rels)
        self.assertTrue(all(r["subj"].startswith(("person:", "seat:")) for r in rels))

    def test_a_speaker_is_not_news(self):
        f = self._field()
        self.assertFalse([e for e in f.changes_since(0) if e.startswith(("person:", "seat:"))])

    def test_the_speaker_line_is_not_prose(self):
        f = self._field()
        self.assertIsNone(f.get("w:dmitrii"))
        self.assertIsNone(f.get("w:chechetkin"))
        user = [d for d in self._events(f) if d.get("role") == "user"]
        self.assertTrue(all(d["text"].startswith("[HA user: ") for d in user))   # stored text keeps it


class TestSourceTags(unittest.TestCase):
    def test_a_track_says_where_its_latest_line_came_from(self):
        from membrane import entity_block as EB
        f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
        for i in range(4):
            f.write_events(ST.split_turn(HEAD + f"situation {i}: the jibberword ladder is stuck near the anchor chain",
                                         ""), session="s")
        f.process()
        ev = f.evidence("w:jibberword", k=1)
        self.assertEqual(ev[0]["speaker"], "person:dmitrii-chechetkin")
        line = EB.track_line(f, "w:jibberword")
        self.assertIn("latest (said by person:dmitrii-chechetkin)", line)

    def test_tags(self):
        from membrane.entity_block import source_tag
        self.assertEqual(source_tag({"stream": "observation", "tool": "exec"}), "observed: tool:exec")
        self.assertEqual(source_tag({"stream": "discourse", "role": "model"}), "said by seat:self")
        self.assertEqual(source_tag({"stream": "discourse", "role": "user"}), "said (user)")
        self.assertEqual(source_tag({"stream": "discourse", "role": "note"}), "noted")


if __name__ == "__main__":
    unittest.main()
