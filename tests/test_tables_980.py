"""#980 rows 18-19,23-25: the migrated tables render byte-identically to the former literals."""
import hashlib
import importlib
import json
import os
import time
import unittest

from membrane import tables

GOLD = json.load(open(os.path.join(os.path.dirname(__file__), "golden_tables_980.json")))


class TablesParity(unittest.TestCase):
    def test_every_table_matches_golden(self):
        for key, h in GOLD.items():
            m, n = key.split(".")
            v = getattr(importlib.import_module("membrane." + m), n)
            self.assertEqual(hashlib.sha256(repr(v).encode()).hexdigest(), h, key)

    def test_judge_prompt_renders_at_call_time(self):
        from membrane import efgate
        out = tables.get("efgate", "AB_JUDGE_PROMPT").format(q="Q", a="A", b="B")
        self.assertTrue(out.endswith("ANSWER B: B"))
        self.assertEqual(out, efgate.AB_JUDGE_PROMPT.format(q="Q", a="A", b="B"))

    def test_edit_lands_without_reimport(self):
        p = os.path.join(tables._DIR, "efgate.json")
        raw = open(p, encoding="utf-8").read()
        try:
            d = json.loads(raw)
            d["AB_JUDGE_PROMPT"] += "!"
            open(p, "w", encoding="utf-8").write(json.dumps(d))
            os.utime(p, ns=(time.time_ns() + 10**9, time.time_ns() + 10**9))
            self.assertTrue(tables.get("efgate", "AB_JUDGE_PROMPT").endswith("!"))
        finally:
            open(p, "w", encoding="utf-8").write(raw)
            os.utime(p, ns=(time.time_ns() + 2 * 10**9, time.time_ns() + 2 * 10**9))
            tables.get("efgate", "AB_JUDGE_PROMPT")

    def test_hot_read_is_cheap(self):
        tables.get("ch_blurp", "SKILLS")
        t = time.perf_counter()
        for _ in range(20000):
            tables.get("ch_blurp", "SKILLS")
        us = (time.perf_counter() - t) / 20000 * 1e6
        print(f"tables.get hot: {us:.2f} us/call")
        self.assertLess(us, 50)
