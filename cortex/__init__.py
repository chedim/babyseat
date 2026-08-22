"""cortex-core: a field-first runtime memory / decision substrate.

The field is the default actor; the LLM is the escalation. See README.md for the
full design and the "measure variety" open problem.
"""

from .field import Exemplar, ExemplarStore
from .variety import effective_rank, spread, novelty
from .gate import decide
from .embed import OllamaEmbedder
from .cortex import Cortex, Responder, StubResponder, OllamaChatResponder
from .review import summarize

__all__ = [
    "Exemplar",
    "ExemplarStore",
    "effective_rank",
    "spread",
    "novelty",
    "decide",
    "OllamaEmbedder",
    "Cortex",
    "Responder",
    "StubResponder",
    "OllamaChatResponder",
    "summarize",
]