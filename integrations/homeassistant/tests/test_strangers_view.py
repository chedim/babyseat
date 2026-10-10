"""The login page's public door (2026-10-10): only checked numbers leave HA."""
import unittest

from ai_terminal import strangers as SV


class TestStrangerParams(unittest.TestCase):
    T = "0123456789abcdef0123456789abcdef"

    def test_only_the_four_ops(self):
        self.assertIsNone(SV.clean_params("entity", {}))
        self.assertEqual(SV.clean_params("visit", {"anything": "x"}), {})

    def test_the_token_must_be_hex(self):
        self.assertIsNone(SV.clean_params("view", {"t": "../../session"}))
        self.assertEqual(SV.clean_params("view", {"t": self.T}), {"t": self.T})

    def test_events_carry_a_known_kind_and_integers_only(self):
        self.assertIsNone(SV.clean_params("event", {"t": self.T, "kind": "say hi"}))
        self.assertIsNone(SV.clean_params("event", {"t": self.T, "kind": "move", "dx": "1e9"}))
        self.assertEqual(SV.clean_params("event", {"t": self.T, "kind": "move", "n": "3", "dx": "99999", "dy": "-4", "text": "x"}),
                         {"t": self.T, "kind": "move", "n": "3", "dx": "2000", "dy": "-4"})

    def test_the_bucket_throttles_per_address(self):
        b = SV.Bucket(size=3, rate=1.0)
        self.assertEqual([b.take("a", now=10.0) for _ in range(4)], [True, True, True, False])
        self.assertTrue(b.take("b", now=10.0))
        self.assertTrue(b.take("a", now=11.1))

    def test_the_seat_sees_a_hash_not_the_address(self):
        k = SV.ip_key("203.0.113.9")
        self.assertNotIn("203", k)
        self.assertEqual(len(k), 16)
        self.assertEqual(k, SV.ip_key("203.0.113.9"))


if __name__ == "__main__":
    unittest.main()
