"""#335: the message fit is a TRUE non-negative least squares, not a clipped
unconstrained one. With mentioned states that resemble each other (the live
field's pairwise cos is ~0.6), clipping the negative weights of an unconstrained
solve overshot the message (|fit| > |x| in 73% of the live seat's events) and
turned the residual -- the news that pulls the current -- back against the
message's own subjects (median cos -0.74)."""
import unittest

import numpy as np

from membrane import efield as EF


def _alike(n, dim=64, cos=0.6, seed=0):
    """n unit states with pairwise cos ~`cos` (a shared part plus their own)."""
    rng = np.random.default_rng(seed)
    common = rng.normal(size=dim)
    common /= np.linalg.norm(common)
    out = []
    for _ in range(n):
        own = rng.normal(size=dim)
        own -= (own @ common) * common
        own /= np.linalg.norm(own)
        v = np.sqrt(cos) * common + np.sqrt(1 - cos) * own
        out.append(v / np.linalg.norm(v))
    return np.asarray(out)


def _msg(A, seed=1):
    rng = np.random.default_rng(seed)
    x = A.T @ rng.uniform(0, 1, size=len(A)) + 0.8 * rng.normal(size=A.shape[1]) / np.sqrt(A.shape[1])
    return x / np.linalg.norm(x)


class TestFitIsNonNegativeLeastSquares(unittest.TestCase):
    def test_the_fit_never_overshoots_the_message(self):
        for seed in range(40):
            A = _alike(6, seed=seed)
            x = _msg(A, seed=seed + 100)
            xh, _ = EF.fit_weights(x, A)
            self.assertLessEqual(float(np.linalg.norm(xh)), 1.0 + 1e-9, f"seed {seed}")

    def test_the_residual_is_orthogonal_to_every_thing_it_used(self):
        for seed in range(40):
            A = _alike(6, seed=seed)
            x = _msg(A, seed=seed + 100)
            xh, w = EF.fit_weights(x, A)
            r = x - xh
            for a, wi in zip(A, w):
                if wi > 1e-12:
                    self.assertAlmostEqual(float(r @ a), 0.0, places=9, msg=f"seed {seed}")

    def test_no_unused_thing_would_improve_the_fit(self):
        """The optimality condition: a thing left at weight 0 does not point along the residual."""
        for seed in range(40):
            A = _alike(6, seed=seed)
            x = _msg(A, seed=seed + 100)
            xh, w = EF.fit_weights(x, A)
            r = x - xh
            for a, wi in zip(A, w):
                if wi <= 1e-12:
                    self.assertLessEqual(float(r @ a), 1e-9, f"seed {seed}")

    def test_weights_are_non_negative(self):
        for seed in range(40):
            A = _alike(6, seed=seed)
            _, w = EF.fit_weights(_msg(A, seed=seed + 100), A)
            self.assertTrue(np.all(w >= 0.0))

    def test_it_beats_the_clipped_fit_it_replaces(self):
        """Never a worse reconstruction than clipping an unconstrained solve."""
        for seed in range(40):
            A = _alike(6, seed=seed)
            x = _msg(A, seed=seed + 100)
            xh, _ = EF.fit_weights(x, A)
            w0, *_ = np.linalg.lstsq(A.T, x, rcond=None)
            old = A.T @ np.clip(w0, 0.0, None)
            self.assertLessEqual(float(np.linalg.norm(x - xh)), float(np.linalg.norm(x - old)) + 1e-12)

    def test_orthogonal_things_fit_as_before(self):
        """Where nothing needed clipping, the answer is the old one."""
        A = np.eye(5)[:3]
        x = np.array([0.6, 0.3, 0.5, 0.4, 0.2])
        xh, w = EF.fit_weights(x, A)
        self.assertTrue(np.allclose(w, [0.6, 0.3, 0.5]))
        self.assertTrue(np.allclose(xh, [0.6, 0.3, 0.5, 0.0, 0.0]))

    def test_fit_is_the_same_solve(self):
        A = _alike(5, seed=7)
        x = _msg(A, seed=8)
        self.assertTrue(np.allclose(EF.fit(x, A), EF.fit_weights(x, A)[0]))


if __name__ == "__main__":
    unittest.main()
