"""The field lock is never held across an embedding call (2026-10-31: the forewarn act
listener embedded each tool call's act over HTTP inside apply_pending's lock, so every
reader -- the chat's recall, the night, the dreamer -- waited minutes behind the network)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import forewarn as FW  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


class LockWatchingEmbedder(FakeEmbedder):
    def __init__(self):
        super().__init__()
        self.field = None
        self.under_lock = 0

    def embed(self, texts):
        if self.field is not None and self.field._lock.locked():
            self.under_lock += 1
        return super().embed(texts)


def _field():
    emb = LockWatchingEmbedder()
    f = EF.EntityField(MemBackend(), emb, "test@host")
    emb.field = f
    fw = FW.Forewarn(f, min_pool=2, k=3)
    f.step_listeners.append(fw.listener)
    f.step_prepare.append(fw.prepare)
    fw.ready()
    return f, fw, emb


class TestNoEmbedUnderTheFieldLock(unittest.TestCase):
    def test_a_tool_calls_act_is_embedded_outside_the_lock(self):
        f, fw, emb = _field()
        for i in range(3):
            f.write_events([ST.Event(stream="observation", tool="shell", args={"command": f"run {i}"},
                                     result_head="done", outcome="ok", ts=1000.0 + i,
                                     call_id=f"c{i}")], session="s1")
            f.process()
        self.assertEqual(emb.under_lock, 0)
        self.assertEqual(len(fw._acts), 3)               # the acts still join the pool

    def test_prepare_stores_the_act_vector_the_listener_reads(self):
        f, fw, _ = _field()
        ev = {"stream": "observation", "tool": "shell", "args": {"command": "ls"}, "outcome": "ok"}
        fw.prepare(ev)
        self.assertTrue(ev.get("act_vec"))

    def test_prepare_skips_what_the_listener_skips(self):
        f, fw, _ = _field()
        for ev in ({"stream": "discourse", "text": "hi"},
                   {"stream": "observation", "tool": "tick", "outcome": "ok"}):
            fw.prepare(ev)
            self.assertNotIn("act_vec", ev)


if __name__ == "__main__":
    unittest.main()
