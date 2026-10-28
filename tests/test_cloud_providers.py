"""Multi-provider cloud auth + rungs: the provider seam end to end.

Pure-data tests (no network): the auth store round-trip + env precedence per
provider, the registry's provider-tagged cloud rungs (price pins, collision
safety, persistence), and the ladder mixing rungs from DIFFERENT providers
(cheapest-first floor, outage failover across providers).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import auth as A
from membrane import router as R


class _Reg(R.Registry):
    """Registry with the live HTTP walk stubbed out: no Ollama, no accidental
    loading of a real router.json from the working directory."""

    def refresh(self):     # noqa: D102 - no discovery in these tests
        return


class TestAuthStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_roundtrip_and_600(self):
        p = A.save_api_key("openrouter", "sk-or-test-123", home=self.home)
        self.assertEqual(json.loads(p.read_text())["openrouter"],
                         "sk-or-test-123")
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_store_merges_never_removes(self):
        A.save_api_key("anthropic", "sk-ant-aaa", home=self.home)
        A.save_api_key("groq", "gsk_bbb", home=self.home)
        d = json.loads(A.auth_path(self.home).read_text())
        self.assertEqual(d["anthropic"], "sk-ant-aaa")
        self.assertEqual(d["groq"], "gsk_bbb")

    def test_env_wins_over_store(self):
        A.save_api_key("groq", "stored-key", home=self.home)
        got = A.load_api_key("groq", home=self.home,
                             environ={"GROQ_API_KEY": "env-key"})
        self.assertEqual(got, "env-key")
        # a DIFFERENT provider's env var does not leak into this lookup
        got2 = A.load_api_key("groq", home=self.home,
                              environ={"ANTHROPIC_API_KEY": "other"})
        self.assertEqual(got2, "stored-key")

    def test_every_registry_provider_has_shape(self):
        for name, prov in A.PROVIDERS.items():
            self.assertTrue(prov["base"].startswith("https://"), name)
            self.assertTrue(prov["chat_path"].startswith("/"), name)
            self.assertTrue(prov["models_path"].startswith("/"), name)
            self.assertTrue(prov.get("env"), name)
            self.assertIn(prov.get("auth_style"), ("bearer", "anthropic"),
                          name)

    def test_zen_free_ids_are_declared_paid_stay_paid(self):
        # only what Zen declares free pins near-floor: a zero budget must
        # never open its paid models
        self.assertTrue(A.is_free_model({}, "big-pickle", "zen"))
        self.assertTrue(A.is_free_model({}, "nemotron-3-ultra-free", "zen"))
        self.assertFalse(A.is_free_model({}, "claude-sonnet-5", "zen"))
        # the declaration is per provider: another provider's id is not free
        self.assertFalse(A.is_free_model({}, "big-pickle", "groq"))
        self.assertEqual(A.PROVIDERS["zen"]["default_price"], 1.0)

    def test_header_styles(self):
        h = A._headers("anthropic", "K")
        self.assertEqual(h["x-api-key"], "K")
        self.assertIn("anthropic-version", h)
        h2 = A._headers("groq", "K")
        self.assertEqual(h2["Authorization"], "Bearer K")
        self.assertNotIn("x-api-key", h2)
        # openrouter carries the app-attribution header
        self.assertEqual(A._headers("openrouter", "K").get("X-Title"),
                         "membrane")

    def test_mask(self):
        self.assertEqual(A.mask(""), "(none)")
        m = A.mask("sk-ant-api03-0123456789abcdef")
        self.assertTrue(m.startswith("sk-ant-"))
        self.assertTrue(m.endswith("cdef"))
        self.assertNotIn("0123456789", m)


class TestCloudRungs(unittest.TestCase):
    def _reg(self, tmp: str) -> R.Registry:
        return _Reg(backend="http://127.0.0.1:11434", embedder=None,
                    state_path=str(Path(tmp) / "router.json"))

    def test_add_cloud_models_registers_provider_rungs(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            n = reg.add_cloud_models(["m-a", "m-b"], provider="groq",
                                     price=0.75)
            self.assertEqual(n, 2)
            for mid in ("m-a", "m-b"):
                rec = reg.records[mid]
                self.assertEqual(rec.kind, "cloud")
                self.assertEqual(rec.backend, "groq")
                self.assertEqual(rec.price(), 0.75)
                self.assertIn("tools", rec.caps)

    def test_price_table_beats_nothing_but_pin_wins_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            # a pinned rung: the pin wins even when the name would table-hit
            reg.add_cloud_models(["claude-sonnet-9"], provider="openrouter",
                                 price=1.0)
            self.assertEqual(reg.records["claude-sonnet-9"].price(), 1.0)
            # an unpinned rung falls through to the table (claude -> 6.0)
            reg.add_cloud_models(["claude-opus-9"], provider="anthropic")
            self.assertEqual(reg.records["claude-opus-9"].price(), 6.0)

    def test_name_collision_keeps_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            local = R.ModelRecord(name="shared-name", kind="local",
                                  size_bytes=2**30)
            reg.records["shared-name"] = local
            n = reg.add_cloud_models(["shared-name", "cloud-only"],
                                     provider="mistral")
            self.assertEqual(n, 1)               # only cloud-only landed
            self.assertEqual(reg.records["shared-name"].kind, "local")
            self.assertEqual(reg.records["shared-name"].backend, "")

    def test_persistence_roundtrip_keeps_backend_and_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = str(Path(tmp) / "router.json")
            reg = _Reg(backend="http://127.0.0.1:11434", embedder=None,
                       state_path=sp)
            reg.add_cloud_models(["free-model"], provider="openrouter",
                                 price=1.0)
            reg.save()
            reg2 = _Reg(backend="http://127.0.0.1:11434", embedder=None,
                        state_path=sp)
            rec = reg2.records["free-model"]
            self.assertEqual(rec.backend, "openrouter")
            self.assertEqual(rec.price_override, 1.0)
            self.assertEqual(rec.price(), 1.0)

    def test_ladder_mixes_providers_cheapest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            reg.add_cloud_models(["groq-fast"], provider="groq", price=0.75)
            reg.add_cloud_models(["or-free"], provider="openrouter",
                                 price=1.0)
            reg.add_cloud_models(["big-expensive"], provider="anthropic",
                                 price=9.0)
            stack = reg.ladder(needs_tools=True)
            names = [r.name for r in stack if r.kind == "cloud"]
            self.assertEqual(names[0], "groq-fast")
            self.assertEqual(names[1], "or-free")
            # ceiling at the middle price drops the expensive rung
            stack2 = reg.ladder(needs_tools=True, ceiling="or-free")
            names2 = [r.name for r in stack2 if r.kind == "cloud"]
            self.assertNotIn("big-expensive", names2)
            self.assertIn("or-free", names2)

    def test_outage_failover_to_other_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            reg.add_cloud_models(["groq-fast"], provider="groq", price=0.75)
            reg.add_cloud_models(["or-free"], provider="openrouter",
                                 price=1.0)
            reg.note_outage("groq-fast", "HTTP 429")
            stack = [r.name for r in reg.ladder(needs_tools=True)]
            self.assertEqual(stack[0], "or-free")   # crossed the provider seam

    def test_free_rungs_pin_near_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            # a mixed listing: one :free id, one paid
            reg.add_cloud_models(["z-ai/glm-5.2:free", "z-ai/paid"],
                                 provider="openrouter", price=1.0,
                                 free_ids={"z-ai/glm-5.2:free"})
            self.assertEqual(reg.records["z-ai/glm-5.2:free"].price(),
                             R.FREE_TIER_PRICE)
            self.assertEqual(reg.records["z-ai/paid"].price(), 1.0)
            # free wins the floor: cheapest-first
            stack = [r.name for r in reg.ladder(needs_tools=True)]
            self.assertEqual(stack[0], "z-ai/glm-5.2:free")

    def test_free_rung_below_paid_respects_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            reg.add_cloud_models(["free-m"], provider="openrouter",
                                 price=1.0, free_ids={"free-m"})
            reg.add_cloud_models(["paid-m"], provider="groq", price=0.75)
            # a ceiling at the paid rung keeps the free rung in (cheaper)
            stack = [r.name for r in reg.ladder(needs_tools=True,
                                                ceiling="paid-m")]
            self.assertIn("free-m", stack)
            self.assertIn("paid-m", stack)

    def test_free_pin_never_raises_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = self._reg(tmp)
            # a low provider default stays as-is; the pin only lowers
            reg.add_cloud_models(["free-m"], provider="groq", price=0.05,
                                 free_ids={"free-m"})
            self.assertEqual(reg.records["free-m"].price(), 0.05)


class TestFetchModelsParsing(unittest.TestCase):
    def test_openai_shape_and_bare_list(self):
        calls = []

        class FakeResp:
            def __init__(self, payload):
                self._p = json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self._p

        payloads = {
            A.PROVIDERS["openrouter"]["base"] + "/v1/models":
                {"data": [{"id": "deepseek/free-x"}, {"id": "qwen/y"}]},
            A.PROVIDERS["github"]["base"] + "/catalog/models":
                [{"id": "openai/gpt-5"}, {"name": "meta/llama"},
                 {"id": ""}, "junk"],
        }
        import urllib.request as _u

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_full_url())
            return FakeResp(payloads[req.get_full_url()])

        orig = _u.urlopen
        _u.urlopen = fake_urlopen
        try:
            ids1 = A.fetch_models("openrouter", "k")
            ids2 = A.fetch_models("github", "k")
        finally:
            _u.urlopen = orig
        self.assertEqual(ids1, ["deepseek/free-x", "qwen/y"])
        self.assertEqual(ids2, ["openai/gpt-5", "meta/llama"])

    def test_failure_degrades_to_empty(self):
        ids = A.fetch_models("groq", "k", timeout=0.01)
        self.assertEqual(ids, [])
        self.assertEqual(A.fetch_models("nope", "k"), [])


class TestFreeDetection(unittest.TestCase):
    def test_is_free_model_shapes(self):
        # openrouter's two free markers: pricing==0 and the :free suffix
        self.assertTrue(A.is_free_model(
            {"pricing": {"prompt": "0", "completion": "0"}}, "any/id"))
        self.assertTrue(A.is_free_model({}, "vendor/model:free"))
        # priced rows are NOT free
        self.assertFalse(A.is_free_model(
            {"pricing": {"prompt": "0.0000015", "completion": "0.000002"}},
            "vendor/paid"))
        # missing/unparseable pricing -> not free by declaration
        self.assertFalse(A.is_free_model({}, "vendor/mystery"))
        self.assertFalse(A.is_free_model(
            {"pricing": {"prompt": "free?"}}, "vendor/mystery"))

    def test_fetch_models_info_parses_flag(self):
        class FakeResp:
            def __init__(self, payload):
                self._p = json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self._p

        payloads = {
            A.PROVIDERS["openrouter"]["base"] + "/v1/models":
                {"data": [
                    {"id": "a/free", "pricing": {"prompt": "0",
                                                 "completion": "0"}},
                    {"id": "a/paid:free"},
                    {"id": "a/paid", "pricing": {"prompt": "0.001",
                                                 "completion": "0.002"}},
                ]},
        }
        import urllib.request as _u

        def fake_urlopen(req, timeout=None):
            return FakeResp(payloads[req.get_full_url()])

        orig = _u.urlopen
        _u.urlopen = fake_urlopen
        try:
            pairs = A.fetch_models_info("openrouter", "k")
        finally:
            _u.urlopen = orig
        self.assertEqual(pairs, [("a/free", True, 0), ("a/paid:free", True, 0),
                                 ("a/paid", False, 0)])


class TestOpenAIProvider(unittest.TestCase):
    """The openai row (#630): api.openai.com on the generic provider seam."""

    def test_row_shape(self):
        p = A.PROVIDERS["openai"]
        self.assertEqual(p["base"], "https://api.openai.com")
        self.assertEqual(p["chat_path"], "/v1/chat/completions")
        self.assertEqual(p["models_path"], "/v1/models")
        self.assertEqual(p["auth_style"], "bearer")
        self.assertEqual(p["env"], ("OPENAI_API_KEY",))
        self.assertEqual(A._headers("openai", "sk-x"),
                         {"Authorization": "Bearer sk-x"})

    def test_env_key(self):
        got = A.load_api_key("openai", home=Path(tempfile.mkdtemp()),
                             environ={"OPENAI_API_KEY": "sk-env"})
        self.assertEqual(got, "sk-env")

    def test_listing_keeps_chat_models_only(self):
        rows = {"data": [{"id": i} for i in (
            "gpt-4o", "gpt-5", "o3-mini", "text-embedding-3-small",
            "whisper-1", "tts-1", "dall-e-3", "gpt-image-1",
            "omni-moderation-latest", "gpt-4o-transcribe",
            "gpt-4o-realtime-preview", "gpt-4o-audio-preview")]}

        class _R:
            def read(self):
                return json.dumps(rows).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request
        real = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: _R()
        try:
            ids = A.fetch_models("openai", "sk-x")
        finally:
            urllib.request.urlopen = real
        self.assertEqual(ids, ["gpt-4o", "gpt-5", "o3-mini"])


if __name__ == "__main__":
    unittest.main()
