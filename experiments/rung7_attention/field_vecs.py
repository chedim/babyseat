"""Export entity positions from a COPY of a seat's field (never the live one)
for the field-neighbourhood predictor of attention.py:

    cp -r <seat>/.membrane/field.cblite /scratch/f/field.cblite      # a copy
    MEMBRANE_CBL_DIR=vendor/cbl/macos python experiments/rung7_attention/field_vecs.py \
        /scratch/f/field.cblite experiments/rung7_attention/data

Opens the copy read-only, reads `entity_states`, and writes
<data>/field-vecs.json: {key: unit mean vector} under attention.norm's keys,
for the entities the operator touched or self looked at (the first state
seen per key wins). The geometry is the END of the record: a static map
that has seen the whole period (a mild lookahead, see the README).
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane.cblstore import CBLiteBackend  # noqa: E402
from membrane.efstore import unpack_f16  # noqa: E402

import attention as A  # noqa: E402

VEC_FIELDS = ("mean", "state")      # the accrued mean position; the state as a fallback


def vec_of(doc):
    for f in VEC_FIELDS:
        v = doc.get(f)
        if isinstance(v, str):
            try:
                return np.asarray(unpack_f16(v), dtype=float)
            except Exception:
                continue
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            return np.asarray(v, dtype=float)
    return None


def main(argv):
    if "--peek" in argv:
        be = CBLiteBackend(argv[1], readonly=True)
        for key, r in be.scan("entity_states", limit=3):
            print(key, json.dumps({k: (str(v)[:80]) for k, v in r.items()}))
        print("count", be.count("entity_states"))
        return
    data = Path(argv[2])
    turns, looks, _ = A.build(data)
    want = {e for t in turns for e in t["ents"]} | {k for _, k in looks}
    be = CBLiteBackend(argv[1], readonly=True)
    out = {}
    for key, d in be.scan("entity_states"):
        k = A.norm(d.get("eid") or key or "")
        if k not in want or k in out:
            continue
        v = vec_of(d)
        if v is not None:
            n = np.linalg.norm(v)
            if n > 0:
                out[k] = (v / n).round(5).tolist()
    json.dump(out, open(data / "field-vecs.json", "w"))
    print(f"{len(out)} of {len(want)} touched/looked entities have a position -> {data / 'field-vecs.json'}")


if __name__ == "__main__":
    main(sys.argv)
