"""The in-window review surface -- what I (Claude, in the window) run to see
what Membrane's entity field holds and whether the gate is earning its keep.
Passive supervision, not user labeling.

Two readouts, both on the entity field (the audit log's exemplar metrics --
absorb/re-ask over ExemplarStore -- retired with the exemplar field):

  ab_report     the replay A/B: every turn the gate found replayable, whether
                the replay was served (--replay-ab P), and -- once the model
                answered -- the answer similarity + the idle judge's verdict
                (efgate.ab_report over the `annotations` rows the proxy wrote)
  vitals        the field's shape: events by stream/status, entities by tier,
                null coherence, effective rank / free energy (efield.vitals)

Run:
  python -m membrane.review --ab                # replay A/B
  python -m membrane.review                     # field vitals
"""

from __future__ import annotations

import argparse


def vitals_report(field_path: str, backend=None) -> str:
    """The field's shape -- counts by stream and tier + the geometry block."""
    from .efield import EntityField
    from .substrate import _origin_tag
    if backend is None:
        from .efstore import open_backend
        from .project_env import load_project_env
        load_project_env()
        # issue #127: review is read-only telemetry -- a snapshot beside a
        # live serve, never the field writer lock.
        backend = open_backend(field_path, readonly=True)
    fld = EntityField(backend, None, _origin_tag(field_path))
    v = fld.vitals()
    g = v.get("geometry")
    lines = [
        f"entity field ({fld.origin}):",
        f"  events:      {', '.join(f'{k}: {n}' for k, n in sorted((v.get('events') or {}).items())) or 'none'}",
        f"  entities:    {', '.join(f'{k}: {n}' for k, n in sorted((v.get('entities') or {}).items())) or 'none'}",
        f"  seq last:    {v.get('seq_last')}   cursor: {v.get('cursor')}",
    ]
    if g:
        lines += [
            f"  eff rank:    {g['eff_rank']}/{g['entities']} (ratio {g['rank_ratio']}) -- variety",
            f"  free energy: {g['free_energy']}  (variety - conflict tax {g['conflict_frac']})",
        ]
    nc = v.get("null_coherence")
    if isinstance(nc, (int, float)):
        lines.append(f"  null coherence: {nc:.3f}")
    return "\n".join(lines)


def ab_summary(field_path: str, origin: str | None = None, last: int = 10, backend=None) -> str:
    """The replay A/B report: every turn the gate found replayable, the
    served mode, and -- once the model answered -- the answer similarity
    and the idle judge's verdict."""
    from .efgate import ab_report
    if backend is None:
        from .efstore import open_backend
        from .project_env import load_project_env
        load_project_env()
        # issue #127: audit snapshots read-only beside a live serve.
        backend = open_backend(field_path, readonly=True)
    if origin is None:
        from .substrate import _origin_tag
        origin = _origin_tag(field_path)

    from types import SimpleNamespace
    r = ab_report(SimpleNamespace(b=backend, origin=origin))   # reads backend + origin only
    lines = [f"Replay A/B ({origin}): {r['eligible']} replayable turns, "
             f"{r['served_replay']} served from the field, {r['paired']} paired with the "
             f"model's answer (mean answer similarity {r['mean_answer_sim']}), "
             f"{r['judged']} judged {r['verdicts'] or ''}".rstrip()]
    rows = sorted((d for _, d in backend.scan("annotations", {"origin": origin, "_k": "ab"})),
                  key=lambda d: -(d.get("ts") or 0))[:last]
    for d in rows:
        j = d.get("judge") or {}
        lines.append(f"\n- {d.get('query', '')[:120]!r}  [q #{d.get('q_event')} -> a "
                     f"#{d.get('a_event')}, sim {d.get('sim')}, {d.get('served')}]")
        lines.append(f"    replay: {' '.join((d.get('replay') or '').split())[:200]}")
        if d.get("model_answer") is not None:
            lines.append(f"    model:  {' '.join(d['model_answer'].split())[:200]}")
            lines.append(f"    answer sim {d.get('answer_sim')}; judge "
                         f"{j.get('verdict', 'pending')}{': ' + j['reason'] if j.get('reason') else ''}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Review what the Membrane entity field learned.")
    ap.add_argument("--field", default=".membrane/field.json",
                    help="the field path (backend via COUCHBASE_* env)")
    ap.add_argument("--metrics", action="store_true", help="same as default readout")
    ap.add_argument("--ab", action="store_true",
                    help="the entity field's replay A/B: replayable turns, the replay "
                         "next to the model's answer, the judge's verdicts")
    ap.add_argument("--origin", default=None, help="the field origin (--ab; default: this seat's)")
    ap.add_argument("--last", type=int, default=10, help="pairs to list (--ab)")
    args = ap.parse_args()
    if args.ab:
        print(ab_summary(args.field, args.origin, args.last))
        return
    print(vitals_report(args.field))


if __name__ == "__main__":
    main()