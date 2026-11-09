"""#980 rows 26-27,39: the dials (consequences, attention, claims windows, toybox kit) as seed-pack data.

DATA (dials_data.py) is the release; PACK installs it as entities `membrane::dials:<name>` through the
versioned loader, so a seat edit survives a release as an overlay. get() reads the live entity once
sync(est) has run, else the packaged value: the modules' constants are identical either way.
"""
from __future__ import annotations

import copy

from .dials_data import DATA
from .entities import MEMBRANE_NS

VERSION = 1
PACK = {"name": "dials", "version": VERSION,
        "entities": [{"eid": MEMBRANE_NS + "dials:" + n, "cls": "Other",
                      "properties": {"name": "dials " + n, "silent_birth": True, **copy.deepcopy(v)}}
                     for n, v in DATA.items()]}
_LIVE: dict = {}


def get(name: str, key: str):
    src = _LIVE.get(name)
    if src is not None and key in src:
        return copy.deepcopy(src[key])
    return copy.deepcopy(DATA[name][key])


def tup(name: str, key: str) -> tuple:
    return tuple(tuple(x) if isinstance(x, list) else x for x in get(name, key))


def install(est) -> dict:
    from . import seedpack
    out = seedpack.load(est, PACK)
    sync(est)
    return out


def sync(est) -> None:
    for n in DATA:
        d = (est.docs or {}).get(MEMBRANE_NS + "dials:" + n)
        if d is not None:
            _LIVE[n] = d.properties
