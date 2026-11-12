---
name: membrane-restart
description: Restart membrane safely (the operator asks for a restart, "bounce the seat", "load new code", "did my edit take effect"). The planned restart is presence.restart; a cold start is only for a seat that is down.
---

# Membrane restart

Membrane is a long-running seat. A restart loads code changed on disk and
re-hydrates the field from its backend. The FIELD IS DURABLE: a restart loses
nothing except a turn cut off mid-flight -- and the planned restart below
never cuts a person's turn off.

## The planned restart: presence.restart

Write `restart` on `presence`, with the reason as `why`:

- from the model: `entity(action="write", spec="presence", prop="restart", value={"why": "..."})`
- from a shell, against a running seat (port = its `--listen`, 11500 by default):

      ~/.membrane/venv/bin/python - <<'EOF'
      import json, urllib.request
      from membrane import jwtauth as J
      h = dict(J.client_headers(), **{"Content-Type": "application/json"})
      body = {"spec": "presence", "properties": {"restart": {"why": "load #NNN"}}}
      req = urllib.request.Request("http://127.0.0.1:11500/membrane/entity/edit",
                                   data=json.dumps(body).encode(), headers=h)
      print(json.load(urllib.request.urlopen(req, timeout=30)))
      EOF

  `J.client_headers()` carries the seat's credential (a minted JWT in JWT
  mode, the seat token otherwise); a bare `Authorization: Bearer
  $(cat ~/.membrane/seat-token)` is refused when the seat runs in JWT mode.

What it does (`restarts.do_restart`): it waits until no PERSON turn is busy
(loops never hold it; their work is re-queued at boot), writes the reason for
the next boot's notice, sends itself a clean SIGTERM so the usual drain runs,
and the teardown's end re-execs the same command in the same PID. A person's
pause is carried across. Only one restart waits at a time.

Before calling it, you can check `GET /session/status` on the seat's serve
port (4097) with the same headers: every session should be `idle`.

## A cold start (the seat is down)

    ./run.sh restart          # kills whatever listens on :11500, starts it fresh

or start it the way it was started, e.g.
`membrane standalone --serve-port 4097 --listen 11500 --no-browser`.
Use this only when the seat is not running or does not answer; on a live seat
it cuts in-flight turns off.

## Never

- Never kill the seat mid-write of the field to "speed up" a restart; use
  presence.restart and let it drain.
- Never restart to "fix" a confused field. A restart reloads code; it does
  not clean state. Use `membrane: forget ...` for content, not a reboot.
- A dirty git tree + `membrane update` is a no-op by design (it refuses to
  force-merge). Commit first; do not work around it.

## After a restart

1. The boot log shows `[seat] restarting (re-exec)`, then the field load
   (`couchbase lite: N hot rows loaded in Ns`) and `[replies] built from N
   messages`. One `[boot] Ns: setup Ns, field rows Ns, ...` line closes the boot
   with each step's wall time. Then run `field state` (the membrane-state skill) once and read it.
2. If code changed, the boot update check may print a changelog + prompt:
   answer it in the terminal, or run `membrane update` explicitly.
3. A migration warning at boot (`field file vN envelope -> ...`) is normal
   and one line -- note it, do not hand-edit the field.
