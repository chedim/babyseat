"""The shared substrate's identity and the peers layer.

  field_id_for   a stable, filesystem-safe id for one project's field: the
                 hex digest of the resolved field path, first 12 chars --
                 derived, not configured, so the same project is the same
                 membrane everywhere (the claims commons on the Couchbase
                 bucket keys by it)
  Peers          self-registration + peer discovery for the live-ask layer:
                 every membrane heartbeats into ~/.membrane/peers.jsonl
                 (or MEMBRANE_PEERS_PATH), a peer's /membrane/ask answers
                 from ITS field's entity evidence; a failed ask degrades to
                 "no help" and the caller falls through to the model

The old JSON commons (GlobalField + Ledger shards, operator promotion,
recall top-up, residency drift) retired with the exemplar field -- the
commons on the entity field is the claims layer (claims.py, promoted at
night). Git history is the archive.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


def field_id_for(field_path: str | Path) -> str:
    """Stable per-project membrane id: hash of the RESOLVED field path."""
    real = Path(field_path).resolve()
    return hashlib.sha256(str(real).encode()).hexdigest()[:12]


# ---- slice 4: peers + the live ask ------------------------------------------
# ~/.membrane/peers.jsonl: one JSON line per LIVE membrane proxy
# {"fid", "port", "field", "ts"} -- self-registered at boot (O_APPEND), stale
# entries (heartbeat older than PEER_STALE_S) self-expire on every read. A
# failed ask to a peer degrades to "no help" -- escalation falls through to
# the model (the same covenant as judge-error -> ACCEPT; degrade toward the
# safe direction).

PEERS_MAX = 128            # capped registry (stale rows are garbage-collected)
PEER_STALE_S = 15 * 60     # a heartbeat older than this = a ghost
PEER_ASK_TIMEOUT = 20      # seconds; a slow peer is a dead peer
PEER_ASK_SIM_MIN = 0.60    # a peer answer must clear its own bar to count


def _peers_path(default_dir: Path | None = None) -> Path:
    env = os.environ.get("MEMBRANE_PEERS_PATH")
    if env:
        return Path(env)
    if default_dir is not None:
        return default_dir / "peers.jsonl"
    return Path(os.path.expanduser("~/.membrane/peers.jsonl"))


class Peers:
    """Self-registration + peer discovery for the live-ask layer.

    No election, no leader: every membrane appends its own heartbeat line and
    reads everyone else's. A peer's /membrane/ask endpoint answers from ITS
    field's local kNN (its lived knowledge, never the commons) -- this is the
    slice-4 routing layer "around ghosts": a live ask to a crashed peer fails
    and the caller falls through to the model.
    """

    def __init__(self, fid: str, path: Path | None = None) -> None:
        self.fid = fid
        self.path = path or _peers_path()
        self.info: dict = {}
        # THE ROUTE HEALTH (the ':11502 unreachable' exhibit): a peer
        # whose ask failed recently cooldowns off livelist (the seat
        # MOVED ports; the stale heartbeat row keeps its old port
        # until the file's GC -- a dead route must not be retried on
        # every turn in the meantime). Success clears; expiry
        # re-admits.
        self._route_bad: dict[str, float] = {}
        self._route_bad_s: float = 300.0

    def register(self, port: int, field: str, peers_path: Path | None = None
                 ) -> Path:
        """Append this membrane's heartbeat (idempotent per append; reads
        garbage-collect stale rows of everyone)."""
        p = peers_path or self.path
        rec = {"fid": self.fid, "port": int(port),
               "field": str(getattr(self, "field_path", field) or field),
               "ts": time.time()}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        return p

    def livelist(self, exclude_self: bool = True, peers_path: Path | None = None
                 ) -> list[dict]:
        """Live peers (heartbeat fresh), newest per fid. Stale rows are
        dropped in memory; the file is rewritten (compacted) lazily."""
        p = peers_path or self.path
        now = time.time()
        by_fid: dict[str, dict] = {}
        raw: list[str] = []
        try:
            raw = [l for l in p.read_text().splitlines() if l.strip()]
        except OSError:
            return []
        for ln in raw:
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if now - float(r.get("ts", 0)) > PEER_STALE_S:
                continue                      # ghost: expired
            fid = r.get("fid") or ""
            if not fid:
                continue
            prev = by_fid.get(fid)
            if prev is None or float(r.get("ts", 0)) >= float(prev.get("ts", 0)):
                by_fid[fid] = r
        if exclude_self:
            by_fid.pop(self.fid, None)
        live = list(by_fid.values())
        # THE ROUTE HEALTH: the cooldown's dead routes drop here (the
        # compact above is the file's GC; this is the memory's). An
        # UNRECORDED port passes; a recorded one within its window is
        # presumed still-dead (the expiry is the re-admit).
        t_now = time.time()
        live = [r for r in live
                if (rk := str(int(r.get("port") or 0))) not in self._route_bad
                or t_now - self._route_bad[rk] > self._route_bad_s]
        # lazy compaction: rewrite only when garbage piled up
        if len(raw) > PEERS_MAX and len(live) < len(raw) // 2:
            try:
                tmp = p.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(r) + "\n" for r in
                                       sorted(by_fid.values(),
                                              key=lambda r: r.get("fid", ""))))
                os.replace(tmp, p)
            except OSError:
                pass
        return live

    def ask_peer(self, peer: dict, situation: str,
                 sim_min: float = PEER_ASK_SIM_MIN,
                 timeout: float = PEER_ASK_TIMEOUT) -> dict | None:
        """THE ROUTE-HEALTH-AWARE ask: the static ask() with the outcome
        recorded -- a dead route cooldowns off livelist; an answered
        one clears its record."""
        out = self.ask(peer, situation, sim_min=sim_min, timeout=timeout)
        key = str(int(peer.get("port", 0)))
        if out is None:
            self._route_bad[key] = time.time()
        else:
            self._route_bad.pop(key, None)
        return out

    @staticmethod
    def ask(peer: dict, situation: str, sim_min: float = PEER_ASK_SIM_MIN,
            timeout: float = PEER_ASK_TIMEOUT) -> dict | None:
        """One live ask: GET http://127.0.0.1:<port>/membrane/ask?q=...
        Returns {"fid", "situation", "action", "sim", "ts"} or None (no match
        / peer down / any error -- ALL failures degrade to None, the caller
        falls through to the model). Read-only on the peer; timeout-guarded.
        ``timeout`` lets the live chat path use a tighter budget than the
        default (a hung peer must not stall a turn)."""
        import urllib.parse
        import urllib.request
        q = urllib.parse.quote(situation[:400])
        url = f"http://127.0.0.1:{int(peer.get('port', 0))}/membrane/ask?q={q}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read())
        except Exception:
            return None
        try:
            if float(data.get("sim", 0.0)) < sim_min:
                return None
        except (TypeError, ValueError):
            return None
        if not (data.get("situation") and data.get("action")):
            return None
        return data


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"