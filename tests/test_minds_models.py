"""THE MINDS' MODELS (2026-10-08, the operator: "does switching model in the
chat switch it for all membrane mechanisms? can you add a second selector
for those?", red first).

The chat picker sets ONE session's model. The rest ran on boot-time picks:
the watch loops on the loop runner's model, the thinker / dreamer / claim
judges on the idle cortex. Pinned: both are readable and settable at
runtime, applied live (the runner, the cortex, the judge), remembered
across a restart; bad names are refused.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import minds_models as MM  # noqa: E402


class _Runner:
    model = "glm-5.3-flash:cloud"


class _Judge:
    model = "qwen3:8b"


class _Srv:
    def __init__(self, d):
        self._loop_runner = _Runner()
        self.idle_cortex_model = "qwen3:8b"
        self._claim_judge = _Judge()
        self._models_path = str(Path(d) / "models.json")
        self.default_seat_model = "glm-5.3-flash:cloud"


class TestMindsModels(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.srv = _Srv(self.d)

    def test_read(self):
        r = MM.read(self.srv)
        self.assertEqual(r["loops"], "glm-5.3-flash:cloud")
        self.assertEqual(r["cortex"], "qwen3:8b")

    def test_set_applies_live_and_persists(self):
        r = MM.apply(self.srv, loops="gpt-oss:20b-cloud", cortex="llama3.2:latest")
        self.assertEqual(r["loops"], "gpt-oss:20b-cloud")
        self.assertEqual(self.srv._loop_runner.model, "gpt-oss:20b-cloud")
        self.assertEqual(self.srv.idle_cortex_model, "llama3.2:latest")
        self.assertEqual(self.srv._claim_judge.model, "llama3.2:latest")
        saved = json.load(open(self.srv._models_path))
        self.assertEqual(saved, {"loops": "gpt-oss:20b-cloud", "cortex": "llama3.2:latest"})

    def test_one_at_a_time(self):
        MM.apply(self.srv, cortex="llama3.2:latest")
        self.assertEqual(self.srv._loop_runner.model, "glm-5.3-flash:cloud")

    def test_a_restart_remembers(self):
        MM.apply(self.srv, loops="gpt-oss:20b-cloud")
        fresh = _Srv(self.d)
        MM.load(fresh)
        self.assertEqual(fresh._loop_runner.model, "gpt-oss:20b-cloud")
        self.assertEqual(fresh.idle_cortex_model, "qwen3:8b")          # untouched

    def test_bad_names_are_refused(self):
        for bad in ("", "x" * 200, "a b", "line\nbreak"):
            with self.assertRaises(ValueError):
                MM.apply(self.srv, loops=bad)
        self.assertEqual(self.srv._loop_runner.model, "glm-5.3-flash:cloud")

    def test_the_known_list_is_enforced_when_given(self):
        with self.assertRaises(ValueError):
            MM.apply(self.srv, loops="made-up:1b", known=["qwen3:8b", "glm-5.3-flash:cloud"])
        MM.apply(self.srv, loops="qwen3:8b", known=["qwen3:8b"])
        self.assertEqual(self.srv._loop_runner.model, "qwen3:8b")


if __name__ == "__main__":
    unittest.main()


class TestPrimeAtBoot(unittest.TestCase):
    """The saved picks apply the moment the models path is known, not a
    minute later: a watch-loop fallback, the thinker or the judges that ran
    in between used the launch default (qwen3:14b next to the saved 8b), and
    Ollama evicted the embedder (2026-10-29)."""

    def test_prime_applies_the_saved_cortex_and_loops(self):
        import json as _j
        import tempfile as _t
        from types import SimpleNamespace
        d = _t.mkdtemp()
        p = os.path.join(d, "models.json")
        open(p, "w").write(_j.dumps({"cortex": "qwen3:8b", "loops": "claude-agent:sonnet"}))
        srv = SimpleNamespace(idle_cortex_model="qwen3:14b", _models_path=p)
        MM.prime(srv)
        self.assertEqual(srv.idle_cortex_model, "qwen3:8b")
        self.assertEqual(srv._minds_picked["loops"], "claude-agent:sonnet")

    def test_prime_with_no_file_changes_nothing(self):
        from types import SimpleNamespace
        srv = SimpleNamespace(idle_cortex_model="qwen3:14b", _models_path="/nonexistent/models.json")
        MM.prime(srv)
        self.assertEqual(srv.idle_cortex_model, "qwen3:14b")

    def test_the_seat_primes_right_after_the_models_path(self):
        src = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()
        i = src.index('srv._models_path = str(_wdir / "models.json")')
        self.assertLess(i, src.index("prime(srv)"))
        self.assertLess(src.index("prime(srv)"), src.index("Thinker(srv)"))
