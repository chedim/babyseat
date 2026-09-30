# Session changes for the ollama-membrane fork port (Sep 2026)

> **Status (2026-09-30, after the entity-field rewrite): HISTORICAL — and
> the fork is now independent.** The fork in `../ollama-membrane` stopped
> tracking this repo when the exemplar field was replaced outright by the
> entity field on this side (stages 1–17; docs/entity-field.md): the gate's
> substrate, the field, the injection and the replay lane it describes no
> longer exist in this codebase — one vector per situation/response pair is
> now events → entities → blocks. Porting these notes onto the fork as
> written would port a dead architecture; treat the whole document as the
> record of the Sep-2026 session (14 sections below, kept in place) and,
> if a fork port is ever wanted again, read entity-field.md for what the
> current design is and re-derive the mapping.
>
> The operator's standing decision for this repo: **ignore the fork** —
> coupling removed (`fork_owns_field` is gone from this codebase), the fork
> runs its own capture/inject path on its own field design.

Everything below landed on membrane `main` (uncommitted tree as of this
writing). The fork in `../ollama-membrane` shares the gate/field DNA but has
its own capture/inject path (`membraneRecallInject`, fork doctrine), so when
porting, map each change to the fork's equivalent surface. Nothing here
depends on opencode specifics except where noted.