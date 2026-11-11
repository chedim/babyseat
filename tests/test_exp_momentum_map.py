import os
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import numpy as np

from membrane import momentum_map as MM


def st(kind, state, seed=None, eps=(), ts=1.0, surface=""):
    return NS(kind=kind, tier="live", state=np.array(state, dtype=np.float32),
              seed=None if seed is None else np.array(seed, dtype=np.float32),
              episodes=[(np.array(e, dtype=np.float32), 3) for e in eps], last_ts=ts, surface=surface)


class Map(unittest.TestCase):
    def setUp(self):
        MM._WORDS.update(t=0.0, M=None, names=[])

    def test_speed_and_heading(self):
        fld = NS(_states={
            "w:east": st("w", [1, 0], surface="east"), "w:north": st("w", [0, 1], surface="north"),
            "f:a.py": st("f", [1, 0], eps=[[0, 0]], ts=5.0),
            "person:op": st("p", [0, 1], seed=[0, 0.5], ts=4.0)})
        ls = MM.lines(fld)
        self.assertTrue(ls[0].startswith("f:a.py  ↗ 1.00  east"), ls)
        self.assertTrue(ls[1].startswith("person:op  ↗ 0.50  north"), ls)
        self.assertFalse(any(l.startswith("w:") for l in ls))

    def test_rewrite_drops_history_and_banners(self):
        fld = NS(_states={})
        msgs = [{"role": "system", "content": "doctrine"}, {"role": "user", "content": "old"},
                {"role": "assistant", "content": "old reply"}, {"role": "system", "content": "BANNER"},
                {"role": "user", "content": "now"}, {"role": "assistant", "tool_calls": [1]},
                {"role": "tool", "content": "r"}]
        out = MM.rewrite(msgs, fld)
        self.assertEqual(out[0]["content"], "doctrine")
        self.assertIn("No chat history is sent", out[1]["content"])
        self.assertEqual([m["role"] for m in out[2:]], ["user", "assistant", "tool"])
        self.assertNotIn("BANNER", str(out))
        self.assertNotIn("old", str(out[2:]))

    def test_switch(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_EXP_MOMENTUM_MAP": "1"}):
            self.assertTrue(MM.on())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(MM.on())
