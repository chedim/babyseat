"""The reranker's device (the operator, 2026-11-10: ROCm in 0.7.5): a CUDA device when PyTorch reports one -- NVIDIA,
or an AMD GPU through a ROCm build, which PyTorch exposes as `cuda` -- else Apple's MPS, else the CPU."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import recall_rerank as RR  # noqa: E402


def _torch(cuda: bool, mps: bool):
    return types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: cuda),
                                 backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)))


def test_cuda_first_then_mps_then_cpu():
    assert RR._device(_torch(cuda=True, mps=False)) == "cuda"       # NVIDIA, or AMD under a ROCm build
    assert RR._device(_torch(cuda=True, mps=True)) == "cuda"
    assert RR._device(_torch(cuda=False, mps=True)) == "mps"
    assert RR._device(_torch(cuda=False, mps=False)) == "cpu"


def test_a_torch_without_mps_still_answers():
    t = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False), backends=types.SimpleNamespace())
    assert RR._device(t) == "cpu"
