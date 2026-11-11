"""#1101: unknown tool names call cliffhanger methods (__call) through a context stack (turn frame -> chat frame
-> the chat's own entities -> the seat); the usual file tools on a no-bash seat; the `in:` line."""
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from membrane import callctx as CC
from membrane import toolset as TS


def doc(eid, **props):
    return NS(eid=eid, properties=props)


class Est:
    def __init__(self, docs):
        self.docs = {d.eid: d for d in docs}

    def _resolve_spec(self, spec):
        return self.docs.get(spec) or self.docs.get("membrane::" + spec)


def seat(docs):
    d = tempfile.mkdtemp()
    return NS(entity_store=Est(docs), data_dir=d)


def m(name, *owners, **kw):
    return doc(f"membrane::method:{name}:{'-'.join(owners) or 'g'}", method_name=name, command="echo",
               method_of=list(owners), **kw)


class Resolution(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch("membrane.night.ancestors", lambda est, o: []),
                        mock.patch("membrane.night._cls_owners", lambda est, o: [])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_turn_frame_beats_chat_frame(self):
        a, b = doc("membrane::goal:a"), doc("membrane::Command:b")
        srv = seat([a, b, m("run", a.eid), m("run", b.eid)])
        CC.new_turn(srv, "s1")
        CC.enter(srv, a.eid)
        CC.touch(srv, b.eid)
        self.assertEqual(CC.resolve(srv, "run")["object"], b.eid)
        CC.new_turn(srv, "s1")                       # the turn frame drops; the chat frame stays
        self.assertEqual(CC.resolve(srv, "run"), {"object": a.eid, "level": "this chat's stack"})

    def test_innermost_entered_first(self):
        a, b = doc("membrane::goal:a"), doc("membrane::goal:b")
        srv = seat([a, b, m("run", a.eid), m("run", b.eid)])
        CC.new_turn(srv, "s1")
        CC.enter(srv, a.eid)
        CC.enter(srv, b.eid)
        self.assertEqual(CC.resolve(srv, "run")["object"], b.eid)
        CC.exit_(srv)
        self.assertEqual(CC.resolve(srv, "run")["object"], a.eid)

    def test_chat_entities_then_seat(self):
        own = doc("membrane::Command:t", sid="s1")
        srv = seat([own, m("test", own.eid), m("hello")])
        CC.new_turn(srv, "s1")
        self.assertEqual(CC.resolve(srv, "test"), {"object": own.eid, "level": "this chat's entities"})
        self.assertEqual(CC.resolve(srv, "hello")["level"], "the seat")

    def test_two_at_one_level_is_a_miss_naming_both(self):
        x, y = doc("membrane::a:x", sid="s1"), doc("membrane::a:y", sid="s1")
        srv = seat([x, y, m("go", x.eid), m("go", y.eid)])
        CC.new_turn(srv, "s1")
        miss = CC.resolve(srv, "go")["miss"]
        self.assertIn("a:x", miss)
        self.assertIn("a:y", miss)

    def test_none_is_a_helpful_miss_and_nothing_is_created(self):
        g = doc("membrane::goal:a")
        srv = seat([g, m("build", g.eid)])
        CC.new_turn(srv, "s1")
        CC.enter(srv, g.eid)
        n = len(srv.entity_store.docs)
        miss = CC.resolve(srv, "run_tests")["miss"]
        self.assertIn("no tool or method `run_tests`", miss)
        self.assertIn("build", miss)
        self.assertIn("exec define", miss)
        self.assertEqual(len(srv.entity_store.docs), n)

    def test_the_line(self):
        g = doc("membrane::goal:dfa55447")
        srv = seat([g])
        CC.new_turn(srv, "s1")
        CC.enter(srv, g.eid)
        CC.touch(srv, "membrane::Command:gradle")
        self.assertEqual(CC.line(srv), "in: chat › goal:dfa55447 › Command:gradle")

    def test_the_chat_frame_persists(self):
        g = doc("membrane::goal:a")
        srv = seat([g])
        CC.new_turn(srv, "s1")
        CC.enter(srv, g.eid)
        srv2 = NS(entity_store=srv.entity_store, data_dir=srv.data_dir)
        CC.new_turn(srv2, "s1")
        self.assertEqual(CC.line(srv2), "in: chat › goal:a")


class Names(unittest.TestCase):
    def test_unknown_names_are_calls(self):
        self.assertEqual(TS.resolve("run_tests", {"target": "unit"}),
                         ("__call", {"method": "run_tests", "args": {"target": "unit"}}))

    def test_known_names_pass(self):
        self.assertEqual(TS.resolve("entity_read", {"spec": "x"})[0], "entity_read")

    def test_enter_exit_are_entity_actions(self):
        self.assertEqual(TS.resolve("entity", {"action": "enter", "spec": "goal:a"}), ("ctx_enter", {"spec": "goal:a"}))
        self.assertEqual(TS.resolve("entity", {"action": "exit"})[0], "ctx_exit")

    def test_usual_tools_on_a_no_bash_seat(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_CLIFFHANGER_ONLY": "1"}):
            self.assertEqual(TS.resolve("Read", {"file_path": "a.py"})[0], "fs_read")
            self.assertEqual(TS.resolve("Write", {"file_path": "a.py", "content": "x"})[0], "fs_write")
            self.assertEqual(TS.resolve("Edit", {"file_path": "a", "old_string": "o", "new_string": "n"})[0], "fs_edit")
            self.assertEqual(TS.resolve("Glob", {"pattern": "*.py"})[0], "fs_list")
            self.assertEqual(TS.resolve("LS", {"path": "."})[0], "fs_list")
            self.assertEqual(TS.resolve("Bash", {"command": "ls"})[0], "code_run")   # refused at the one door
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(TS.resolve("Read", {"file_path": "a.py"})[0], "__call")


class FileTools(unittest.TestCase):
    def test_read_write_edit_list(self):
        from membrane import mcp_server as MS
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.txt")
        self.assertIn("write done", MS.tool_fs_write({"path": p, "content": "one\ntwo\n"}, 0))
        self.assertIn("2\ttwo", MS.tool_fs_read({"path": p}, 0))
        self.assertIn("edit", MS.tool_fs_edit({"path": p, "old": "two", "new": "three"}, 0))
        self.assertIn("three", open(p).read())
        self.assertIn("a.txt", MS.tool_fs_list({"path": d}, 0))


if __name__ == "__main__":
    unittest.main()
