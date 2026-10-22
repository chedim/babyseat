"""#429: three recall bugs the memory probe found.

1. A person's line carries its speaker line ('[user: <name>]'); the field
   embeds the line WITHOUT it, so the gate must read it without it too, or
   the earlier ask has no stored sentence and is dropped.
2. The sentence index is approximate: on the couchbase-lite home it must
   find what an exact search finds (a true nearest sentence was missing
   from its top 2000).
3. Every sentence of a long reply leads back to its event, not only the
   first few."""

import base64
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import cblite  # noqa: E402
from membrane import efgate as G  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import speakers as SP  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0
Q = "how do I restart membrane/router.py cleanly?"
A = "Stop the seat, then run make restart; membrane/router.py reloads its table."


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


class TestSpeakerLine(unittest.TestCase):
    def _history(self):
        f = _field()
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0, text="[user: Ann] " + Q),
                        ST.Event(stream="discourse", role="model", ts=T0 + 5, text=A)],
                       session="s:1:0")
        f.process()
        return f

    def test_a_live_chat_line_is_read_as_stored(self):
        f = self._history()
        qv = G.message_vec(f, Q)
        rows = G.near_asks(f, qv)
        self.assertEqual(len(rows), 1, rows)
        sim, _key, d = rows[0]
        self.assertGreater(sim, 0.99)
        self.assertEqual(SP.speaker_of(d), ("person:ann", "Ann"))     # attribution kept
        v = G.candidate(f, Q)
        self.assertTrue(v["eligible"], v)
        self.assertEqual(v["answer"], A)

    def test_the_speaker_line_never_moves_the_vector(self):
        f = self._history()
        a = G.message_vec(f, "[user: Ann] " + Q)
        self.assertAlmostEqual(float(a @ G.message_vec(f, Q)), 1.0, places=5)


WORDS = ("amber basalt cobalt dahlia ember fjord garnet harbor indigo juniper "
         "kelp lagoon marble nectar onyx pepper quartz raven saffron tundra "
         "umber velvet walnut xylem yarrow zephyr acorn birch cedar delta").split()


class TestLongReply(unittest.TestCase):
    def test_every_sentence_of_a_long_reply_finds_its_event(self):
        f = _field()
        sents = [f"The {w} valve number {i} opens with the {w} lever slowly." for i, w in enumerate(WORDS)]
        f.write_events([ST.Event(stream="discourse", role="user", ts=T0, text="tell me about the valves"),
                        ST.Event(stream="discourse", role="model", ts=T0 + 5, text=" ".join(sents))],
                       session="s:1:0")
        f.process()
        reply = next(k for k, d in f.b.scan("events", {"role": "model"}))
        self.assertGreaterEqual(len(f.b.get("events", reply)["sids"]), 30)
        for i in (0, 9, 20, 29):
            qv = G.message_vec(f, sents[i])
            _hits, evs = G.sentence_events(f, qv, 3)
            self.assertIn(reply, evs, f"sentence {i}")
            self.assertIn(f.b.get("events", reply)["n"], [d["n"] for d, _ in f.events_near(qv, k=3)])

    def test_a_forgotten_reply_stays_forgotten(self):
        f = _field()
        sents = [f"The {w} valve number {i} opens with the {w} lever slowly." for i, w in enumerate(WORDS)]
        f.write_events([ST.Event(stream="discourse", role="model", ts=T0, text=" ".join(sents))],
                       session="s:1:0")
        f.process()
        reply = next(k for k, _ in f.b.scan("events", {"role": "model"}))
        f.b.cas_update("events", reply, lambda d: dict(d, status="forgotten"))
        self.assertEqual(f.events_near(G.message_vec(f, sents[25]), k=3), [])


HAVE = cblite.lib_paths() is not None


def _f16(v):
    return "f16:" + base64.b64encode(np.asarray(v, np.float16).tobytes()).decode()


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestApproximateIndex(unittest.TestCase):
    def test_the_index_finds_what_an_exact_search_finds(self):
        from membrane.cblstore import CBLiteBackend
        b = CBLiteBackend(Path(tempfile.mkdtemp()) / "field.cblite")
        try:
            r = np.random.default_rng(3)
            c = r.standard_normal((200, 1024)).astype(np.float32)
            V = c[r.integers(0, 200, 4000)] + 0.7 * r.standard_normal((4000, 1024)).astype(np.float32)
            V /= np.linalg.norm(V, axis=1, keepdims=True)
            b.upsert_multi("sentences", {f"s{i}": {"_k": "sent", "vec": _f16(v)} for i, v in enumerate(V)})
            b.catch_up()
            overlap = []
            for q in V[r.integers(0, 4000, 20)] + 0.5 * r.standard_normal((20, 1024)).astype(np.float32):
                q = q / np.linalg.norm(q)
                exact = {f"s{i}" for i in np.argsort(-(V @ q))[:50]}
                overlap.append(len(exact & {k for k, _ in b.vector_top_k("sentences", "vec", q, 50)}) / 50)
            self.assertGreater(float(np.mean(overlap)), 0.85, overlap)    # 0.50 at the index's default probes
        finally:
            b.close()


if __name__ == "__main__":
    unittest.main()
