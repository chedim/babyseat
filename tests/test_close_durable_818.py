"""A card answered / entity added AFTER store.close() must survive a reload
(#818): close() must not leave later writes to a debounce timer that dies
with the process."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore


class CloseDurable(unittest.TestCase):
    def test_writes_after_close_survive_reload(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "e.json")
            s = EntityStore(path=p)
            s.add("Decision", "card-before", "q")
            s.edit("card-before", properties={"status": "open"})
            s.close()
            # the operator's answer path writes the doc, then save_soon()
            cid = [k for k in s.docs if "card-before" in k][0]
            s.docs[cid].properties.update({"status": "decided", "value": "A"})
            s.save_soon()
            s.add("Component", "worker-ent-after-close", "w")
            # process dies before the 10 s debounce: only disk remains
            if s._save_timer is not None:
                s._save_timer.cancel()
            s2 = EntityStore(path=p)
            keys = list(s2.docs)
            card = [k for k in keys if "card-before" in k]
            self.assertTrue(card)
            props = getattr(s2.docs[card[0]], "properties", {})
            self.assertEqual(props.get("status"), "decided")
            self.assertTrue(any("worker-ent-after-close" in k for k in keys))


if __name__ == "__main__":
    unittest.main()
