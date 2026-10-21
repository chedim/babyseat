"""BLURP'S ENERGY, SLEEP AND WAKE (#345, part 1 of 3).

Energy runs from 0 to 1 (shown as 0-100%) and lives with Blurp in
<self dir>/energy.json, so a restart neither refills nor drains it: the
first loop step after a boot, or after the loop was off, paused or held
(pause()), counts no time. A Blurp with no stored energy starts rested (1.0).

  BASAL_PER_S    energy an awake second costs: 1 / (16 h), so a rested, idle
                 Blurp runs out after a human waking day. Part 2 sets it
                 from the room's measured rhythm instead.
  CPU_PER_S      energy a second of the cycle's CPU costs (time.thread_time()
                 around the cycle, on the loop's own thread): the same as an
                 awake second, so at a 50% share Blurp tires 1.5x as fast
                 (~10.7 h).
  AWAKE_LEN_S    the awake reserve's length, 16 h: a placeholder for the
                 room's usual awake stretch (part 2 measures it).
  ERROR_COST     an error costs 0.01 (approved): using a thing wrong
                 (error()); the acts that can be wrong come with #333.
  RESTORE_PER_S  sleep gives back 0.001 a second (approved): empty to full
                 in ~17 min.

THE SLEEP PULL is 1 - the lowest reserve (the operator's decision). Each
reserve runs from 1 (full) to 0 (empty):
  energy   the energy itself;
  disk     the free bytes on the volume holding self/ now / the free bytes
           when Blurp last woke (clamped to [0, 1]; full when there is no
           baseline yet);
  awake    1 - the seconds awake since it woke / AWAKE_LEN_S;
  record   1 - the record held in memory (self_loop.record_bytes: its
           serialized lines) / the record cap. THE CAP IS DERIVED, NOT A
           CONSTANT (the operator): the person's CPU share
           (options.cpu_share(), default 0.5) x the RAM available when
           Blurp woke, so the throttle that bounds its CPU bounds its
           memory too. Recomputed at each wake. The RAM is read (_read_ram)
           from /proc/meminfo's MemAvailable (Linux, Android), else
           os.sysconf's available pages, else macOS's `vm_stat` (free +
           inactive + speculative pages, a 2 s timeout; no root). No
           reading: no cap, the reserve reads 1.0 (a bad reading never
           puts Blurp to sleep) and the card says the cap is unknown.
When a reserve runs out (the pull reaches 1) Blurp falls asleep: a `sleep`
line says which (`why`) and the energy. Asleep, no cycle runs (cycle() and
the loop's step() check asleep()), and energy comes back at RESTORE_PER_S.
It wakes when energy is full again (`why: rested`) or when a person speaks
(person_spoke(): a chat or voice message from a person, never Blurp's own
lines or the model's; `why: spoke`, `who`). Waking starts a fresh day: the
free disk now is the new baseline and the awake clock is back at zero.
Asleep is not the operator's off: the mode stays as it was.

share(throttle, srv) is the person's CPU throttle x energy, for #343's
pacing (a tired Blurp gets less CPU); nothing on main reads it yet.

CONSOLIDATION (#345 part 2): falling asleep drops what is open (a turn
awaiting its reply check: the sleep line's `dropped`) and consolidates the
day (self_loop.consolidate): every raw line to its dated archive, the
working record down to one summary line, so the record reserve recovers.

Later parts of #345: the dream in sleep; refunds for confirmed predictions;
the room's rhythm.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

FILE = "energy.json"
BASAL_PER_S = 1 / (16 * 3600)
CPU_PER_S = 1 / (16 * 3600)
AWAKE_LEN_S = 16 * 3600
ERROR_COST = 0.01
RESTORE_PER_S = 0.001
RESERVES = ("energy", "disk", "awake", "record")      # the order breaks a tie for the limiting reserve
_FULL = 1.0 - 1e-9                          # float slack: restored "to 1.0" in steps


def _SL():
    from . import self_loop as SL
    return SL


def _state(srv) -> dict:
    """The stored state, cached on self's runtime: energy, asleep, awake_s
    (seconds awake since it woke), disk_base / disk_free (bytes), since (ts
    of the last sleep or wake)."""
    SL = _SL()
    rt = SL._rt(srv)
    with SL._lock:
        st = rt.get("energy")
        if st is None:
            doc = SL._load_json(srv, FILE)
            doc = doc if isinstance(doc, dict) else {}
            try:
                e = min(1.0, max(0.0, float(doc.get("energy", 1.0))))
            except (TypeError, ValueError):
                e = 1.0
            st = {"energy": e, "asleep": bool(doc.get("asleep")),
                  "awake_s": float(doc.get("awake_s") or 0.0),
                  "disk_base": doc.get("disk_base"), "disk_free": doc.get("disk_free"),
                  "record_cap": doc.get("record_cap"), "since": doc.get("since")}
            rt["energy"] = st
        return st


def _save(srv, st: dict) -> None:
    _SL()._save_json(srv, FILE, {**st, "ts": time.time()})


def _free(srv) -> int | None:
    """Free bytes on the volume that holds self/ (its nearest existing parent)."""
    d = _SL()._dir(srv)
    if d is None:
        return None
    p = d
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return int(shutil.disk_usage(p).free)
    except OSError:
        return None


def _parse_meminfo(text: str) -> int | None:
    m = re.search(r"^MemAvailable:\s+(\d+)\s*kB", text or "", re.M)
    return int(m.group(1)) * 1024 if m else None


def _meminfo() -> int | None:
    try:
        with open("/proc/meminfo") as fh:
            return _parse_meminfo(fh.read())
    except OSError:
        return None


def _sysconf_avail() -> int | None:
    try:
        n = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None
    return int(n) if n > 0 else None


def _parse_vm_stat(text: str) -> int | None:
    page = re.search(r"page size of (\d+) bytes", text or "")
    pages = {k: re.search(r"^Pages %s:\s+(\d+)" % k, text or "", re.M) for k in ("free", "inactive", "speculative")}
    if not page or not pages["free"]:
        return None
    return sum(int(m.group(1)) for m in pages.values() if m) * int(page.group(1))


def _vm_stat() -> int | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2.0, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_vm_stat(out)


def _read_ram() -> int | None:
    """Available RAM in bytes, the most honest reading this host gives; None if none."""
    for f in (_meminfo, _sysconf_avail, _vm_stat):
        n = f()
        if n:
            return n
    return None


def _avail_ram() -> int | None:
    return _read_ram()


def _record_cap(avail) -> float | None:
    """The record cap: the person's CPU share x the RAM available now."""
    if not isinstance(avail, (int, float)) or avail <= 0:
        return None
    from . import options as O
    return O.cpu_share() * float(avail)


def level(srv) -> float:
    return _state(srv)["energy"]


def asleep(srv) -> bool:
    return bool(_state(srv)["asleep"])


def share(throttle: float, srv) -> float:
    """#343's CPU share for Blurp: the person's throttle x its energy."""
    return float(throttle) * level(srv)


def _reserves(st: dict, rbytes: int) -> dict:
    base, free = st.get("disk_base"), st.get("disk_free")
    disk = 1.0
    if isinstance(base, (int, float)) and base > 0 and isinstance(free, (int, float)):
        disk = min(1.0, max(0.0, free / base))
    cap = st.get("record_cap")
    record = min(1.0, max(0.0, 1.0 - rbytes / cap)) if isinstance(cap, (int, float)) and cap > 0 else 1.0
    return {"energy": st["energy"], "disk": disk,
            "awake": min(1.0, max(0.0, 1.0 - st["awake_s"] / AWAKE_LEN_S)), "record": record}


def _limiting(rs: dict) -> str:
    return min(RESERVES, key=lambda k: (rs[k], RESERVES.index(k)))


def report(srv) -> dict:
    """What the card and the sheet show: energy, asleep, the reserves, the
    pull (1 - the lowest) and which reserve is lowest (`limiting`)."""
    SL = _SL()
    with SL._lock:
        st = _state(srv)
        rb = SL.record_bytes(srv)
        rs = _reserves(st, rb)
        lim = _limiting(rs)
        return {"energy": st["energy"], "asleep": bool(st["asleep"]), "reserves": rs,
                "pull": 1.0 - rs[lim], "limiting": lim, "since": st.get("since"),
                "record_bytes": rb, "record_cap": st.get("record_cap")}


def _changed(srv) -> None:
    rt = _SL()._rt(srv)
    rt.pop("sheet", None)                   # the panel shows it at once
    rt.pop("dock", None)


def _fall_asleep(srv, st: dict, now: float, why: str, rs: dict) -> None:
    """Falling asleep: what is open is dropped (named on the sleep line:
    `dropped`, {} when nothing was), then the day is consolidated (#345 part
    2: self_loop.consolidate -- the raw lines to the dated archive, the
    working record down to its summary)."""
    SL = _SL()
    st.update(asleep=True, since=now)
    dropped = SL._open_at_sleep(srv, now)
    SL._append(srv, {"ts": now, "kind": "sleep", "why": why, "energy": st["energy"], "reserves": rs,
                     "dropped": dropped})
    SL.consolidate(srv, now)
    _changed(srv)


def _wake(srv, st: dict, now: float, why: str, free, avail, who: str | None = None) -> None:
    st.update(asleep=False, since=now, awake_s=0.0, disk_base=free, disk_free=free,
              record_cap=_record_cap(avail))
    rec = {"ts": now, "kind": "wake", "why": why, "energy": st["energy"]}
    if who is not None:
        rec["who"] = who
    _SL()._append(srv, rec)
    _changed(srv)
    _SL()._rt(srv)["wake"].set()            # the loop runs its next cycle


def pause(srv) -> None:
    """The loop is not running (off, paused, held): the next step counts no time."""
    _SL()._rt(srv).pop("energy_last", None)


def tick(srv, now: float, cpu_s: float = 0.0, free: int | None = None, avail: int | None = None) -> dict:
    """One loop step: awake, drain (BASAL_PER_S x the seconds since the last
    step + CPU_PER_S x the cycle's CPU seconds) and fall asleep when a reserve
    is out; asleep, restore and wake at full energy. -> report()."""
    SL = _SL()
    now = float(now)
    free = _free(srv) if free is None else free
    rt = SL._rt(srv)
    with SL._lock:
        st = _state(srv)
        last = rt.get("energy_last")
        rt["energy_last"] = now
        dt = max(0.0, now - last) if last is not None else 0.0
        st["disk_free"] = free
        if st.get("disk_base") is None:
            st["disk_base"] = free                  # a Blurp's first day: the disk as it is now
        if st.get("record_cap") is None and not st["asleep"]:
            st["record_cap"] = _record_cap(_avail_ram() if avail is None else avail)   # ...and the RAM
        if st["asleep"]:
            st["energy"] = min(1.0, st["energy"] + RESTORE_PER_S * dt)
            if st["energy"] >= _FULL:
                st["energy"] = 1.0
                _wake(srv, st, now, "rested", free, _avail_ram() if avail is None else avail)
        else:
            st["energy"] = max(0.0, st["energy"] - BASAL_PER_S * dt - CPU_PER_S * max(0.0, float(cpu_s)))
            st["awake_s"] += dt
            rs = _reserves(st, SL.record_bytes(srv))
            lim = _limiting(rs)
            if rs[lim] <= 0.0:
                _fall_asleep(srv, st, now, lim, rs)
        _save(srv, st)
        return report(srv)


def error(srv, why: str, now: float | None = None) -> float:
    """An error (using a thing wrong) costs ERROR_COST and goes on the record
    as an `error` line with its cost and the energy left. -> the energy."""
    SL = _SL()
    now = time.time() if now is None else float(now)
    with SL._lock:
        st = _state(srv)
        st["energy"] = max(0.0, st["energy"] - ERROR_COST)
        SL._append(srv, {"ts": now, "kind": "error", "why": str(why or "")[:200], "cost": ERROR_COST,
                         "energy": st["energy"]})
        _save(srv, st)
        return st["energy"]


def person_spoke(srv, person: str, now: float | None = None, free: int | None = None,
                 avail: int | None = None) -> bool:
    """A person spoke (chat or voice): a sleeping Blurp wakes. -> woke."""
    SL = _SL()
    now = time.time() if now is None else float(now)
    if not asleep(srv):
        return False
    free = _free(srv) if free is None else free             # measured outside the lock (vm_stat may take a moment)
    avail = _avail_ram() if avail is None else avail
    with SL._lock:
        st = _state(srv)
        if not st["asleep"]:
            return False
        _wake(srv, st, now, "spoke", free, avail, who=str(person or ""))
        _save(srv, st)
        return True
