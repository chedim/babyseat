"""#244: how many of self's looks land on a thing with no field point.

Read-only: it reads a seat's data dir (self/record.jsonl and the field's
entity_states journal) and writes nothing -- safe beside a running seat.

    python experiments/off_field_looks.py --data .membrane [--last 200]

It prints the looks counted, how many land off the field (the thing has no
live state in the field: the home view draws them as ghosts), and the things
those are, most looked-at first. The live seat's own number is
status().off_field (GET /membrane/seat -> state.self.off_field); this script
is the same count for a data dir, e.g. before and after a seat restarts on
#244 (whose first look at each thing places it).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.eids import field_id  # noqa: E402


def live_ids(field_dir: Path) -> set[str]:
    """The field ids with a live state (last journal line wins; a legacy
    .json snapshot first)."""
    rows: dict[str, dict] = {}
    snap = field_dir / "entity_states.json"
    if snap.exists():
        try:
            rows.update(json.loads(snap.read_text()))
        except ValueError:
            pass
    log = field_dir / "entity_states.jsonl"
    if log.exists():
        with log.open() as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("k"):
                    if r.get("d") is None:
                        rows.pop(r["k"], None)
                    else:
                        rows[r["k"]] = r["d"]
    return {str(d.get("eid")) for d in rows.values()
            if isinstance(d, dict) and d.get("tier") == "live" and d.get("state")}


def count(data: Path, last: int = 200) -> dict:
    looks = []
    rec = data / "self" / "record.jsonl"
    if rec.exists():
        with rec.open() as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("kind") == "reach" and r.get("eid"):
                    looks.append(r["eid"])
    looks = looks[-last:]
    live = live_ids(data / "field")
    off = Counter(e for e in looks if field_id(e) not in live)
    return {"looks": len(looks), "off": sum(off.values()),
            "things": [f"{e} ({n})" for e, n in off.most_common(12)]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", default=".membrane", help="the seat's data dir (read only)")
    ap.add_argument("--last", type=int, default=200, help="how many of the latest looks (default 200)")
    a = ap.parse_args(argv)
    out = count(Path(a.data), a.last)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
