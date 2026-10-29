"""THE CONVERSATION SO FAR is this chat's own history (#668, no-context): it
was rebuilt from every session of the seat's current boot (other chats,
watcher loops, seat notices) within 6 h, by a full scan of the events, and
lost the chat's history at every restart. Now: this chat's events by its
`conv` key (indexed), across boots."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import efstore  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_800_000_000.0


def field_two_chats():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    rows = [("s:100:0", "sid:A", "user", "chat A asks about the bilge pump alarm topic"),
            ("s:100:1", "sid:B", "user", "chat B asks about the deploy script"),
            ("s:100:0", "sid:A", "model", "chat A hears the alarm goes to boat/bilge"),
            ("s:200:0", "sid:A", "user", "chat A after a restart asks which port the broker uses"),
            ("s:200:1", "sid:B", "model", "chat B hears the deploy restarts both seats")]
    for i, (sess, conv, role, text) in enumerate(rows):
        k = f"e{i}"
        f.b.upsert("events", k, {"origin": "seat@host", "session": sess, "conv": conv, "stream": "discourse",
                                 "role": role, "text": text, "ts": T0 + i, "seq": i + 1, "n": i + 1,
                                 "status": "applied"})
    return f


class TestReconstructByChat(unittest.TestCase):
    def test_only_this_chat_across_boots(self):
        f = field_two_chats()
        block = EB.reconstruct(f, "s:200:", conv="sid:A", now=T0 + 10)
        self.assertIn("bilge pump alarm topic", block)          # before the restart
        self.assertIn("which port the broker uses", block)      # after it
        self.assertNotIn("chat B", block)

    def test_reads_the_chat_index_not_every_event(self):
        f = field_two_chats()
        real, seen = efstore.matches, []

        def spy(d, eq):
            seen.append(1)
            return real(d, eq)
        with mock.patch.object(efstore, "matches", spy):
            EB.reconstruct(f, "s:200:", conv="sid:A", now=T0 + 10)
        self.assertLessEqual(len(seen), 3)                       # chat A's three events

    def test_without_a_chat_key_it_behaves_as_before(self):
        f = field_two_chats()
        self.assertEqual(EB.reconstruct(f, "s:200:", now=T0 + 10), EB.reconstruct(f, "s:200:", conv="", now=T0 + 10))
        self.assertIn("chat B hears", EB.reconstruct(f, "s:200:", now=T0 + 10))

    def test_the_proxy_passes_the_chat(self):
        src = (Path(__file__).parent.parent / "membrane" / "lanes.py").read_text()
        self.assertIn("reconstruct(fld, _own_session_prefix(srv), conv=_CAPMOD.current_conversation())", src)


if __name__ == "__main__":
    unittest.main()
