"""Test seats never load the reranker (2026-11-11): a subprocess test seat started loading bge-reranker (an
hf_xet download) and was killed mid-download; hf_xet aborted at interpreter exit and macOS showed a crash
dialog on the operator's Mac. The suite runs with MEMBRANE_RERANK=0; tests of the reranker itself turn it on
explicitly."""
import os


def test_the_suite_runs_with_the_reranker_off():
    assert os.environ.get("MEMBRANE_RERANK") == "0"
