"""THE BOOT THAT SKIPPED ITS OWN TAIL (2026-10-08, red first).

Since 2026-10-02 the field's boot block touched srv.entity_store before the
store existed: 'ThreadingHTTPServer' object has no attribute 'entity_store'
-> the outer except printed '[efield] capture off' and skipped the rest of
the block on every boot: the field-watch listener, the thinker's feed, the
dream corpus, the judges, the pause gate, the field pulse.

Pinned (statically, main() is the whole seat): inside main(), no
`srv.entity_store.` attribute use comes before the store is assigned, and
the pool reaches the store where the store is made.
"""
import re
import sys
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()


class TestBootOrder(unittest.TestCase):
    def test_no_store_use_before_the_store(self):
        main = SRC[SRC.index("\ndef main() -> None:"):]
        made = main.index("srv.entity_store = EntityStore(")
        for m in re.finditer(r"srv\.entity_store\.[A-Za-z_]", main):
            line = main[main.rfind("\n", 0, m.start()) + 1:main.find("\n", m.start())]
            if line.strip().startswith("#"):
                continue
            self.assertGreater(m.start(), made, line.strip())

    def test_the_pool_reaches_the_store_where_it_is_made(self):
        main = SRC[SRC.index("\ndef main() -> None:"):]
        made = main.index("srv.entity_store = EntityStore(")
        wire = main.index("srv.entity_store._events_pool = srv.events_pool")
        self.assertGreater(wire, made)


if __name__ == "__main__":
    unittest.main()
