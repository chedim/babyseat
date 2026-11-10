"""#935 review round 3, red first: attach() wires llm_works, so a habit stands down while the LLM works."""
import types
from membrane import ch_blurp as B


def _srv():
    vm = types.SimpleNamespace(learner=None)
    return types.SimpleNamespace(ch_vm=vm), vm


def test_attach_passes_llm_works(monkeypatch):
    srv, vm = _srv()
    monkeypatch.setattr(B, "granted", lambda s, p: True, raising=False)
    B.attach(srv)
    assert vm.learner is not None
    assert vm.learner.llm_works is not None


def test_llm_works_true_only_for_a_noted_llm_chain(monkeypatch):
    srv, vm = _srv()
    B.attach(srv)
    B.note_turn("chain-llm-1", "conv-1")
    assert vm.learner.llm_works("chain-llm-1") is True
    assert vm.learner.llm_works("other-chain") is False
    assert vm.learner.llm_works(None) is False
