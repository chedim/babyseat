---
name: membrane-restart
description: Restart membrane safely (the operator asks for a restart, "bounce the proxy", "load new code", "did my edit take effect"). Encodes the safe procedure and the never-do list: never re-exec the running process from within itself, never restart mid-edit.
---

# Membrane restart

Membrane is a long-running proxy process. Restarting loads code changed on
disk and re-hydrates the field from its backend (Couchbase or JSON). The
FIELD IS DURABLE: a restart loses nothing except in-flight turns, so there
is no reason to fear one -- and no reason to improvise one.

## The safe procedure

From a shell (Bash tool) in the project root:

    ./run.sh restart          # or: membrane reset

That kills whatever listens on the opencode seat's port (:11500, or the
`--listen` you pass), then boots a fresh `membrane opencode` with the same
flags.

If ONLY a boot smoke on a spare port is wanted (never disturb the live one):

    .venv/bin/python -m membrane.opencode --listen 18787 --no-launch --field .membrane/field.json ...

Then verify with `field state` (the membrane-state skill) that the field
re-hydrated: exemplar count in the expected range, safe_frac sane.

## The NEVER list (disaster routes)

- NEVER re-exec or `os.execv` the RUNNING membrane from inside itself (no
  watcher, no self-restart on file change). A membrane must not restart
  while it is running -- and never while IT is being edited. Restarts are
  operator-initiated, from the shell, at rest.
- Never kill the proxy mid-write of the field. Membrane saves are atomic
  (temp + rename), but a kill during a CAS/arbitration pass can leave a
  retryable -- not corrupt -- remote state. Check no learn/compactor cycle
  is mid-flight (quiet stderr) before bouncing.
- Never restart to "fix" a confused field. Restart reloads code; it does
  not clean state. Use `membrane: forget ...` for content, not a reboot.
- A dirty git tree + `membrane update` is a no-op by design (it refuses to
  force-merge). Commit or stash first; do not work around it.

## After a restart

1. Post-boot, run `field state` once and READ it (one honest read beats
   trusting the prep).
2. If code changed, the boot update check may print a changelog + prompt:
   answer it in the terminal, or run `membrane update` explicitly.
3. The schema sliding window makes old/new mixed-version state safe; if a
   migration warning appears at boot (`field file vN envelope -> ...`),
   that is normal and one-line -- note it, do not hand-edit the field.