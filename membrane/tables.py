"""#980 rows 18-19, 23-25: string/skill/model tables as data, read by native code at call time.

membrane/tables/<module>.json holds {NAME: value}; get(module, NAME) returns it (lists become
tuples, `set_=True` a frozenset-able set) and re-reads the file when its mtime changes, so a table
edit lands without a code change. The rendered values are byte-identical to the former literals
(tests/test_tables_980.py checks them against tests/golden_tables_980.json).
"""
from __future__ import annotations

import json
import os
import threading

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tables")
_CACHE: dict = {}
_LK = threading.Lock()


def _tup(v):
    if isinstance(v, list):
        return tuple(_tup(x) for x in v)
    if isinstance(v, dict):
        return {k: _tup(x) for k, x in v.items()}
    return v


def table(module: str) -> dict:
    path = os.path.join(_DIR, module + ".json")
    mt = os.stat(path).st_mtime_ns
    hit = _CACHE.get(module)
    if hit and hit[0] == mt:
        return hit[1]
    with _LK:
        with open(path, encoding="utf-8") as f:
            data = {k: _tup(v) for k, v in json.load(f).items()}
        _CACHE[module] = (mt, data)
        return data


def get(module: str, name: str, set_: bool = False):
    v = table(module)[name]
    return set(v) if set_ else v
