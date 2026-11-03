"""BRIEF TOOL RESULTS, FULL OUTPUT IN THE FIELD (the operator, 2026-11-03: inside one answer, every
old tool result is re-sent on every later step -- code_run and bash were 53% of the result tokens).
A run's full output is kept on its result entity (ingested into the field); code_run / bash answer
with it whole when it is short (< BRIEF_WHOLE chars), else the rc, its size, the first and last
BRIEF_LINES lines, capped at BRIEF_CAP chars, and how to page the rest: entity_read lines=A-B."""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402


def _out(text, rc=0, res="membrane::result:ab12:3"):
    return {"root": "membrane::node:sh:ab12", "ok": rc == 0, "rc": rc, "outcome": "ok" if rc == 0 else "failed",
            "executor": "membrane::tool:executor", "result": res, "text": "ls", "output": text,
            "output_len": len(text)}


class Brief(unittest.TestCase):
    def test_the_operators_numbers(self):
        self.assertEqual((M.BRIEF_WHOLE, M.BRIEF_LINES, M.BRIEF_CAP), (256, 15, 4000))

    def test_a_short_output_comes_whole(self):
        r = M._brief_output(_out("total 3\na\nb\n"))
        self.assertIn("total 3\na\nb", r)
        self.assertNotIn("entity_read", r)

    def test_a_long_output_is_its_head_tail_and_a_pointer(self):
        text = "\n".join(f"line {i}" for i in range(1, 401))
        r = M._brief_output(_out(text))
        self.assertIn("400 lines", r)
        self.assertIn("line 1\n", r)
        self.assertIn("line 15\n", r)
        self.assertNotIn("line 16\n", r)
        self.assertIn("line 386", r)
        self.assertIn("line 400", r)
        self.assertIn("entity_read", r)
        self.assertIn("lines=16-385", r)
        self.assertLessEqual(len(r), M.BRIEF_CAP)

    def test_long_lines_still_fit_the_cap(self):
        text = "\n".join("x" * 3000 for _ in range(40))
        self.assertLessEqual(len(M._brief_output(_out(text))), M.BRIEF_CAP)

    def test_code_run_answers_briefly(self):
        text = "\n".join(f"line {i}" for i in range(1, 401))
        old = M._http
        M._http = lambda port, method, path, body=None: _out(text)
        try:
            r = M.tool_code_run({"code": "seq 400"}, 1)
        finally:
            M._http = old
        self.assertNotIn("line 200\n", r)
        self.assertIn("lines=16-385", r)


class Paging(unittest.TestCase):
    def test_entity_read_pages_an_outputs_lines(self):
        text = "\n".join(f"line {i}" for i in range(1, 401))
        old = M._http
        M._http = lambda port, method, path, body=None: {"ok": True, "eid": "membrane::result:ab12:3",
                                                         "cls": "Component", "properties": {"output": text}}
        try:
            r = M.tool_entity_read({"spec": "membrane::result:ab12:3", "lines": "16-18"}, 1)
        finally:
            M._http = old
        self.assertIn("line 16\nline 17\nline 18", r)
        self.assertNotIn("line 19", r)
        self.assertIn("of 400", r)


class NeverSilentlyCut(unittest.TestCase):
    """Memo on #912: every place the model gets less than the output says so."""
    def test_the_header_names_the_lines_actually_shown_with_long_lines(self):
        text = "\n".join(f"{i:02d}" + "x" * 3000 for i in range(1, 41))
        r = M._brief_output(_out(text))
        self.assertIn("long lines cut", r)
        self.assertIn("shown: lines 1-1 and 40-40", r)
        self.assertIn("lines=2-39", r)

    def test_a_single_long_line_says_what_is_shown(self):
        r = M._brief_output(_out("{" + "\"k\": 1, " * 3000 + "}"))
        self.assertIn("1 line", r)
        self.assertIn("first and last", r)
        self.assertIn("prop=output", r)

    def test_an_output_past_the_kept_cap_says_its_middle_is_gone(self):
        kept = "\n".join(f"line {i}" for i in range(1, 301))
        r = M._brief_output({**_out(kept), "output_len": 99_892})
        self.assertIn("99892 chars", r)
        self.assertIn("middle of the run's output is gone", r)

    def _page(self, text, span):
        old = M._http
        M._http = lambda port, method, path, body=None: {"ok": True, "eid": "membrane::result:ab12:3",
                                                         "cls": "Component", "properties": {"output": text}}
        try:
            return M.tool_entity_read({"spec": "membrane::result:ab12:3", "lines": span}, 1)
        finally:
            M._http = old

    def test_a_page_past_the_char_cap_says_where_to_continue(self):
        text = "\n".join(f"line {i:03d} " + "y" * 200 for i in range(1, 401))
        r = self._page(text, "16-385")
        first = r.split("\n", 1)[0]
        self.assertIn("cut at", first)
        self.assertIn("continue with lines=", first)
        last_shown = int(first.split("lines 16-")[1].split(" ")[0])
        self.assertLess(last_shown, 385)
        self.assertIn(f"lines={last_shown + 1}-385", first)

    def test_an_out_of_range_page_is_an_error(self):
        r = self._page("\n".join(f"line {i}" for i in range(1, 401)), "900-950")
        self.assertIn("outside", r)
        self.assertIn("1-400", r)


class KeptInTheField(unittest.TestCase):
    def test_a_real_run_keeps_its_output_on_the_result_entity(self):
        import types
        from membrane import lang as LG
        from membrane import night as N
        from membrane.efield import EntityField
        from membrane.efstore import MemBackend
        from membrane.entities import EntityStore
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _ef_fakes import FakeEmbedder
        f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
        est = EntityStore(path=None, shared=False)
        N.seed_executor_entity(est)
        srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None,
                                    capturer=types.SimpleNamespace(mode="on"))
        est._srv = srv
        out = LG.run(srv, code="seq 400")
        self.assertTrue(out["ok"], out)
        p = est.docs[out["result"]].properties
        self.assertEqual(p["output"].split("\n")[:3], ["1", "2", "3"])
        self.assertIn("400", p["output"])
        self.assertEqual(p["output_len"], len(out["output"]))


if __name__ == "__main__":
    unittest.main()
