"""membrane/backfill.py: the audit log + codebase replayed through the
intake -- one-shot equals interrupted-and-resumed, a second run writes
nothing, timestamp-less history sorts first, sessions split at gaps."""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import backfill as BF  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

T0 = 1_790_000_000.0


def fixture_audit(d: str) -> str:
    recs = []
    for i in range(6):                                   # no-ts prefix (old log)
        recs.append({"event": "decision", "query": f"how does the gateway handle retry number {i}?",
                     "response": f"The gateway retries with backoff, attempt {i}."})
    for i in range(10):
        ts = T0 + i * 60 + (3600 if i >= 5 else 0)       # a >30 min gap after 5
        recs.append({"event": "decision", "ts": ts,
                     "query": f"tool bash for: run the tests round {i}\nargs: "
                              '{"command": "python -m unittest discover -s tests"}',
                     "response": f"Ran {500 + i} tests in 5s OK"})
        recs.append({"event": "decision", "ts": ts + 1,
                     "query": f"plan: what next for the gateway round {i}",
                     "response": f"I updated pkg/gw.py to widen the retry window {i}."})
    recs.append({"event": "frame-wait", "ts": T0})       # non-decision: skipped
    p = os.path.join(d, "audit.jsonl")
    with open(p, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
        fh.write("not json\n")
    return p


def code_root(d: str) -> str:
    p = Path(d) / "pkg/gw.py"
    p.parent.mkdir(parents=True)
    p.write_text(textwrap.dedent('''
        def retry(n):
            """Retry the gateway call with exponential backoff."""
        '''))
    return d


def run(audit, root, **kw):
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    BF.backfill_code(f, root, progress=lambda m: None)
    r = BF.backfill_audit(f, audit, chunk=7, progress=lambda m: None, **kw)
    return f, r


def live(f):
    return {d["eid"]: np.asarray(d["state"]) for _, d in f.b.scan("entity_states", {"tier": "live"})}


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="ef-bf-")
        self.audit = fixture_audit(self.d)
        self.root = code_root(tempfile.mkdtemp(prefix="ef-bf-code-"))

    def test_resumed_equals_one_shot(self):
        a, ra = run(self.audit, self.root)
        self.assertTrue(ra["done"])
        b = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        BF.backfill_code(b, self.root, progress=lambda m: None)
        r1 = BF.backfill_audit(b, self.audit, chunk=7, max_records=9, progress=lambda m: None)
        self.assertFalse(r1["done"])
        b2 = EF.EntityField(b.b, FakeEmbedder(), "seat@host")      # a fresh process
        r2 = BF.backfill_audit(b2, self.audit, chunk=7, progress=lambda m: None)
        self.assertTrue(r2["done"])
        la, lb = live(a), live(b2)
        self.assertEqual(la.keys(), lb.keys())
        for k in la:
            np.testing.assert_allclose(la[k], lb[k], atol=1e-9)

    def test_a_second_run_writes_nothing(self):
        f, _ = run(self.audit, self.root)
        n = len(list(f.b.scan("events")))
        del f._ostats["backfill"]                  # a full re-run from scratch
        f._ostats["backfill_code_done"] = False
        BF.backfill_code(f, self.root, progress=lambda m: None)
        BF.backfill_audit(f, self.audit, chunk=7, progress=lambda m: None)
        self.assertEqual(len(list(f.b.scan("events"))), n)

    def test_history_order_sessions_and_streams(self):
        f, _ = run(self.audit, self.root)
        evs = sorted((d for _, d in f.b.scan("events")), key=lambda d: d["seq"])
        first_ts = next(i for i, d in enumerate(evs) if d.get("ts") is not None)
        self.assertTrue(all(d.get("ts") is None for d in evs[:first_ts]))
        sessions = {d["session"] for d in evs if d["stream"] == "discourse" and d.get("ts")}
        self.assertEqual(sessions, {"b:0", "b:1"})
        kinds = {(d["stream"], d["role"]) for d in evs}
        self.assertIn(("observation", "tool"), kinds)
        self.assertIn(("code", "code"), kinds)
        self.assertIn(("discourse", "model"), kinds)
        self.assertEqual(f.get("s:pkg.gw.retry").tier, "live")

    def test_finish_settles_claims(self):
        f, _ = run(self.audit, self.root)
        BF.finish(f, progress=lambda m: None)
        cs = [d for _, d in f.b.scan("claims")]
        tests = [c for c in cs if c["kind"] == "tests"]
        self.assertTrue(all(c["status"] != "pending" for c in cs if c["kind"] in ("action", "tests")))
        self.assertTrue(tests == [] or all(c["status"] in ("backed", "contradicted", "unbacked")
                                           for c in tests))
        acts = [c for c in cs if c["kind"] == "action"]
        self.assertTrue(acts)


if __name__ == "__main__":
    unittest.main()
