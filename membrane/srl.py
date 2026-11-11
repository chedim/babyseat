"""SRL SPANS FOR SPEAKING (#932): an open semantic-role encoder (default: dannashao/bert-base-uncased-
finetuned-advanced-srl_arg, BERT-base, Universal PropBank, Apache-2.0), when its files are on disk
(MEMBRANE_SRL_MODEL, or ~/projects/membrane-bench/models/srl-bert-base). Given a sentence's words and one verb,
it says which word heads each of that verb's roles. Without the files, nothing (the 🧩 speaks from the
reader's blocks alone)."""
from __future__ import annotations

import os

ROLE = {"ARG0": "DOER", "ARG1": "OBJ", "ARG2": "OBJ", "ARGM-LOC": "PLACE", "ARGM-TMP": "TIME",
        "ARGM-MNR": "MANNER", "ARGM-CAU": "CAUSE"}
_M: dict = {}


def available() -> bool:
    return os.path.isdir(_path())


def _path() -> str:
    return os.environ.get("MEMBRANE_SRL_MODEL") or os.path.expanduser("~/projects/membrane-bench/models/srl-bert-base")


def heads(words: list, verb: int) -> dict:
    """{word index: our block} for the words heading the verb's roles."""
    if not available() or not (0 <= verb < len(words)):
        return {}
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer
    if not _M:
        _M["tok"] = AutoTokenizer.from_pretrained(_path())
        _M["model"] = AutoModelForTokenClassification.from_pretrained(_path()).eval()
    tok, model = _M["tok"], _M["model"]
    enc = tok(list(words[:verb]) + ["[V]"] + list(words[verb:]), is_split_into_words=True, return_tensors="pt",
              truncation=True)
    with torch.no_grad():
        pred = model(**enc).logits.argmax(-1)[0].tolist()
    out, seen = {}, set()
    for ti, w in enumerate(enc.word_ids()):
        if w is None or w in seen or w == verb:
            continue
        seen.add(w)
        role = model.config.id2label[pred[ti]].replace("C-", "").replace("R-", "")
        b = ROLE.get(role)
        if b:
            out[w - 1 if w > verb else w] = b
    return out
