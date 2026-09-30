"""One-shot re-embed migration for the entity field.

WHY: a different embedding model means a different geometry -- sentence
vectors, term seed caches and the entity states built from them must be
rebuilt together, or nothing lines up.

WHAT: `EntityField.reembed(embedder)` -- wipe this origin's live states,
deltas and activity, re-embed every live event under the NEW model
(model-scoped sentence/term ids so the old rows simply stay), and re-apply
in seq order. Pinned equal to embedding the same replay into a fresh field
on the new model.

Run:

    python -m membrane.reembed --efield --embed-model mxbai-embed-large
    # or via the CLI: membrane reembed --efield [--origin O]

The exemplar field's re-embed (field.json time-prefix migration) retired
with the exemplar field; git history is the archive.
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="membrane reembed",
        description="re-embed the entity field with another embedding model "
                    "(every live event re-embedded and re-applied in order)")
    ap.add_argument("--field", default=".membrane/field.json")
    ap.add_argument("--backend", default="http://localhost:11434")
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    ap.add_argument("--origin", default=None,
                    help="the origin (default: this seat's)")
    ap.add_argument("--efield", action="store_true",
                    help="accepted for CLI compatibility (reembed IS the "
                         "entity field's re-embed)")
    args = ap.parse_args()
    from .efield import EntityField
    from .efstore import open_backend
    from .embed import OllamaEmbedder
    from .substrate import _origin_tag
    from .project_env import load_project_env
    load_project_env()
    fld = EntityField(open_backend(args.field),
                      OllamaEmbedder(base_url=args.backend.rstrip("/"),
                                     model=args.embed_model),
                      args.origin or _origin_tag(args.field))
    n = fld.reembed(fld.emb, progress=lambda c: print(f"[reembed] cursor "
                                                      f"#{c}", file=sys.stderr))
    print(f"[reembed] {n} events re-embedded with {args.embed_model}",
          file=sys.stderr)


if __name__ == "__main__":
    main()