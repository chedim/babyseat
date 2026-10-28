"""EXPERIMENT (#649 follow-up): --no-context with MEMBRANE_EXP_NORECON=1 drops
the reconstruction (the recent turns as the field remembers them): the model
sees the system prompt, the recall block and the current turn only."""
from membrane import lanes


def test_reconstruction_rides_by_default(monkeypatch):
    monkeypatch.delenv("MEMBRANE_EXP_NORECON", raising=False)
    assert lanes._exp_recon_on() is True


def test_norecon_drops_the_reconstruction(monkeypatch):
    monkeypatch.setenv("MEMBRANE_EXP_NORECON", "1")
    assert lanes._exp_recon_on() is False
