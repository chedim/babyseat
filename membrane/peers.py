"""The multi-seat substrates: proxy REGISTRY (ephemeral bucket, TTL docs,
heartbeats -- a dead proxy's doc expires itself) + the PEER CHANNEL
(permanent collection, a chat lane between the LLMs, visible on every
connected seat's banner).

THE REGISTRY (cortex's episodic_memory pattern): each proxy registers on
boot -- {pid, cwd, port, model, ts} under 'proxy::<port>' with a TTL of
3 heartbeats. Every HEARTBEAT (default 60s) re-upserts the doc,
refreshing the TTL; a proxy that dies stops heartbeating and its doc
expires -- the registry self-cleans. Consumers: the banner's 'seats:'
line (who else is connected), membrane_doctor, the fork.

THE PEER CHANNEL (cortex-core._default.peer_chat, permanent): messages
are {from, ts, text, reply_to?} -- any seat can post, every seat's
banner shows the latest unread line(s) (bounded, cursor-tracked per
seat). The operator sees cross-seat chatter in opencode on EVERY
connected seat; the model reads/posts via MCP.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import timedelta
from typing import Callable

HEARTBEAT_S = 60.0             # registration refresh cadence
REGISTRY_TTL_S = 3 * 180       # doc TTL: 3 missed heartbeats -> expired
CHANNEL_MAX_FETCH = 5          # unread lines fetched per render
CHANNEL_TEXT_CAP = 240         # per-message text cap
CHANNEL_TAIL = 8               # the cursor memory (last seen ts rides the
                               # registry doc; a seat's cursor survives its
                               # own restarts via the doc's residual TTL)


class PeerSubstrate:
    """The registry + the chat channel, over couchbase's ephemeral and
    permanent collections. Degradation covenant: couchbase down -> both
    lanes go silent (register/heartbeat/post degrade to no-ops; reads
    return empty). One class, one connection attempt, no leaks."""

    def __init__(self, field_path: str, port: int, model: str = "",
                 heartbeat_s: float = HEARTBEAT_S,
                 roster_fn: "Callable[[], list] | None" = None) -> None:
        self.field_path = field_path
        self.port = port
        self.model = model
        self.heartbeat_s = heartbeat_s
        # THE SEAT-FIRST FALLBACK (stage 5): when the ephemeral registry
        # is down or empty, the PRESENCE entities answer who's-alive.
        self._roster_fn = roster_fn
        self.cwd = os.getcwd()
        self.seat_id = f"proxy-{self.port}"
        self._cluster = None
        self._bucket = None
        self._reg = None           # proxies collection (ephemeral bucket)
        self._reg_bucket = None
        self._chan = None          # peer_chat collection (permanent)
        self._cursor_ts = 0.0
        self._last_beat = 0.0
        self._connect()

    # ---- the connection (degrade covenant) ----
    # THE OVERNIGHT-ABORT COVENANT (both seats died the same night, 06:25
    # SIGABRT via __cxa_rethrow in _core.so -- a C++ exception escaping a
    # couchbase asio completion callback -> std::terminate, SDK 4.6.3):
    # an idle seat's connection dies (sleep/NAT/cluster restart), the SDK
    # raises C++-side through the callback boundary, and the process dies
    # with it. Three defenses below:
    #   1. CLUSTER TIMEOUTS -- every op is bounded (microseconds in the
    #      ClusterTimeoutOptions wire shape) so a broken connection cannot
    #      hang the serial serve loop (the 1688 parked handler threads in
    #      the same crash report: requests piling on the feed lock while
    #      the loop sat in a hung couchbase call).
    #   2. BOUNDED READY-WAIT -- a connect that can't come up inside the
    #      window degrades at boot instead of stalling it.
    #   3. ONE-REBUILD -- repeated failures drop the cluster and rebuild
    #      once; a dead connection recovers instead of the seat degrading
    #      forever (and the rebuild failure re-degrades silently).
    _CB_TIMEOUTS = dict(
        kv_connect_timeout=timedelta(seconds=10),
        kv_timeout=timedelta(seconds=5),
        query_timeout=timedelta(seconds=10),
        view_timeout=timedelta(seconds=10),
        management_timeout=timedelta(seconds=10),
    )
    _REBUILD_AFTER = 3             # consecutive failed op batches -> rebuild

    def _connect(self) -> None:
        try:
            from couchbase.cluster import Cluster
            from couchbase.auth import PasswordAuthenticator
            from couchbase.options import (ClusterOptions,
                                           ClusterTimeoutOptions)
            conn = os.environ.get("COUCHBASE_CONN_STRING",
                                  "couchbase://127.0.0.1")
            user = os.environ.get("COUCHBASE_USER", "Administrator")
            pw = os.environ.get("COUCHBASE_PASSWORD", "password")
            bucket_name = os.environ.get("COUCHBASE_BUCKET", "cortex-core")
            self._cluster = Cluster(conn, ClusterOptions(
                PasswordAuthenticator(user, pw),
                timeout_options=ClusterTimeoutOptions(**self._CB_TIMEOUTS)))
            try:
                from datetime import timedelta
                self._cluster.wait_until_ready(timedelta(seconds=8))
            except Exception:
                pass                    # degrade per-op; never stall the boot
            self._bucket = self._cluster.bucket(bucket_name)
            self._reg, self._reg_bucket = self._ensure_eph_registry()
            self._ensure_collection(self._bucket, "peer_chat")
            self._chan = self._bucket.scope("_default").collection(
                "peer_chat")
        except Exception as e:
            print(f"[peers] substrate unavailable ({str(e)[:80]}) -- "
                  f"registry + chat degrade to silent", file=sys.stderr)
            self._reg = self._chan = self._cluster = None

    def _op_failed(self) -> None:
        """The rebuild counter: consecutive couchbase failures (the
        overnight abort's precondition -- a dead connection keeps failing).
        Past the bar, drop the cluster AND the registry handles so the
        next op rebuilds them fresh (register()/list_seats() re-ensure)."""
        self._fail_streak = getattr(self, "_fail_streak", 0) + 1
        if self._fail_streak >= self._REBUILD_AFTER \
                and self._cluster is not None:
            print(f"[peers] {self._fail_streak} consecutive couchbase "
                  f"failures -- rebuilding the cluster handle",
                  file=sys.stderr)
            try:
                self._cluster.close()
            except Exception:
                pass
            self._cluster = self._bucket = self._reg = self._reg_bucket = None
            self._chan = None
            self._fail_streak = 0

    def _op_ok(self) -> None:
        self._fail_streak = 0

    def _ensure_collection(self, bucket, name: str) -> None:
        try:
            try:
                bucket.collections().create_collection("_default", name)
            except Exception:
                pass                    # exists
        except Exception:
            pass

    def _ensure_eph_registry(self):
        """The ephemeral bucket + proxies collection: explicit env, then
        the canon-ephemeral convention, then create (ephemeral TTL does
        the GC -- expired proxy docs vanish without a janitor)."""
        # THE REBUILD: a dropped cluster (the _op_failed path) reconnects
        # here -- the covenant's degrade-and-recover, not degrade-forever.
        if self._cluster is None:
            self._connect()
        if self._cluster is None:
            return None, None
        name = os.environ.get("COUCHBASE_EPHEMERAL_BUCKET")
        # THE REBUILD CASE: a rebuild (all handles dropped) re-enters here
        # with _bucket None -- the canon candidates fall back to the
        # cortex convention instead of crashing on the None.name.
        base = self._bucket.name if self._bucket is not None else "cortex-core"
        candidates = [name] if name else [
            f"{base}-ephemeral", "cortex-ephemeral"]
        for cand in candidates:
            if not cand:
                continue
            try:
                b = self._cluster.bucket(cand)
                self._ensure_collection(b, "proxies")
                self._ensure_registry_index(b)
                return b.scope("_default").collection("proxies"), b
            except Exception:
                continue
        try:
            eph = f"{base}-ephemeral"
            # creation via the management REST surface (the SDK can't
            # create ephemeral buckets directly); failure degrades
            self._cluster.buckets().create_bucket(
                name=eph, bucket_type="ephemeral", ttl=int(HEARTBEAT_S * 10))
            b = self._cluster.bucket(eph)
            self._ensure_collection(b, "proxies")
            self._ensure_registry_index(b)
            return b.scope("_default").collection("proxies"), b
        except Exception:
            return None, None

    def _ensure_registry_index(self, bucket) -> None:
        """A PRIMARY index on the proxies collection: registration is a KV
        upsert (index-free), but the READS are N1QL scans (list_seats /
        fetch_unread) -- without #primary they raise
        QueryIndexNotFoundException and the degrade covenant silently
        serves an empty registry (the 'seats: [] with my own seat up'
        exhibit). Best-effort: an index build failure only degrades reads."""
        try:
            from couchbase.management.queries import (
                CreatePrimaryQueryIndexOptions)
            self._cluster.query_indexes().create_primary_index(
                bucket.name,
                CreatePrimaryQueryIndexOptions(scope_name="_default",
                                               collection_name="proxies",
                                               ignore_if_exists=True))
        except Exception:
            pass

    # ---- the registry ----
    def register(self, port: int | None = None, field: str | None = None
                 ) -> bool:
        """Proxy boot registration: proxy::<port> {pid, cwd, port, model,
        ts, chan_cursor} with a TTL. Returns False on couchbase failure
        (silent). The positional (port, field) form is the slice-4
        Peers.register signature; accepted here so call sites written
        against either substrate work, but the doc already carries the
        authoritative port."""
        if port is not None and int(port) != self.port:
            self.port = int(port)
            self.seat_id = f"proxy-{self.port}"
        if self._reg is None:
            self._reg, self._reg_bucket = self._ensure_eph_registry()
        if self._reg is None:
            return False
        doc = {"pid": os.getpid(), "cwd": self.cwd, "port": self.port,
               "model": self.model, "ts": time.time(),
               "heartbeat_s": self.heartbeat_s,
               "chan_cursor": self._cursor_ts}
        if field:
            doc["field"] = str(field)
        try:
            from couchbase.options import UpsertOptions
            from datetime import timedelta
            self._reg.upsert(
                self.seat_id, doc,
                UpsertOptions(expiry=timedelta(seconds=REGISTRY_TTL_S)))
            self._op_ok()
            return True
        except BaseException:
            self._op_failed()
            return False

    def heartbeat(self) -> bool:
        """The periodic refresh (re-register, refreshing the TTL). Called
        from the server tick; a dead proxy's doc simply expires."""
        if time.time() - self._last_beat < self.heartbeat_s:
            return True
        ok = self.register()
        if ok:
            self._last_beat = time.time()
        return ok

    def _query(self, statement: str, **params) -> list[dict]:
        """One N1QL query via the cluster handle (Collections have no
        .query). Empty on any failure (the degrade covenant)."""
        try:
            from couchbase.options import QueryOptions
            import datetime as _dt
            q = self._cluster.query(
                "SELECT meta().id AS id, t.* FROM `{b}`.`_default`.`{c}` t"
                .format(b=self._bucket.name),
                QueryOptions(timeout=_dt.timedelta(seconds=10),
                             named_parameters={} if False else None))
            rows = [dict(r) for r in q]
            self._op_ok()
            return rows
        except BaseException:
            self._op_failed()
            return []

    def list_seats(self) -> list[dict]:
        """The live seats: every registry doc (expired ones are gone by
        TTL -- the GC is the clock). THE SEAT-FIRST FALLBACK (stage 5,
        the peers re-point): an EMPTY registry -- down, or a seat-first
        world where seats no longer register ephemeral docs -- answers
        from the PRESENCE roster instead (the published membrane::
        presence entities; stale ones pruned)."""
        if self._reg is None:
            self._reg, self._reg_bucket = self._ensure_eph_registry()
        out = []
        if self._reg is not None:
            try:
                from couchbase.options import QueryOptions
                import datetime as _dt
                b = self._reg_bucket
                q = self._cluster.query(
                    f"SELECT t.* FROM `{b.name}`.`_default`.`proxies` t",
                    QueryOptions(timeout=_dt.timedelta(seconds=10)))
                for row in q:
                    out.append(dict(row))
                self._op_ok()
            except BaseException:
                self._op_failed()
                return []
        if out:
            return out
        return self._presence_seats()

    _PRESENCE_STALE_S = 600.0        # a quiet seat older than 10min is
                                     # presumed dead (TTL without TTLs)

    def _presence_seats(self) -> list[dict]:
        """The roster fallback: map the foreign presence entities into
        seat-doc shapes. Raises never (the covenant)."""
        now = time.time()
        try:
            rows = list(self._roster_fn()) if self._roster_fn else []
        except Exception:
            return []
        out = []
        for p in rows:
            props = p.get("properties") or {}
            ts = float(props.get("presence_ts") or 0)
            if ts and now - ts > self._PRESENCE_STALE_S:
                continue             # a dead seat's last word is not life
            seat = str(p.get("origin") or props.get("seat") or "?")
            out.append({"origin": seat, "seat": seat,
                        "port": None,               # not the registry's unit
                        "branch": props.get("branch"),
                        "head": props.get("head"),
                        "uptime_s": props.get("uptime"),
                        "docs": props.get("docs"),
                        "presence_ts": props.get("presence_ts")})
        return out

    # ---- the slice-4 Peers compat surface (opencode call sites written
    # against the JSONL substrate: livelist/ask/register(port, field)) ----
    def livelist(self, exclude_self: bool = True) -> list[dict]:
        """The slice-4 name for the live registry: every seat doc except
        (optionally) this one. Empty on any failure (the covenant)."""
        seats = self.list_seats()
        if exclude_self:
            seats = [s for s in seats
                     if str(s.get("port") or "") != str(self.port)]
        return seats

    @staticmethod
    def ask(peer: dict, situation: str, sim_min: float = 0.60,
            timeout: float = 20.0) -> dict | None:
        """One live ask: GET http://127.0.0.1:<port>/membrane/ask?q=...
        The slice-4 Peers.ask contract, verbatim (peer dicts from
        livelist() carry 'port'; all failures degrade to None)."""
        import urllib.parse
        import urllib.request
        q = urllib.parse.quote(situation[:400])
        url = f"http://127.0.0.1:{int(peer.get('port', 0))}/membrane/ask?q={q}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read())
        except Exception:
            return None
        if data.get("block"):
            return data                   # v2: the sibling's entity field answered
        try:
            if float(data.get("sim", 0.0)) < sim_min:
                return None
        except (TypeError, ValueError):
            return None
        if not (data.get("situation") and data.get("action")):
            return None
        return data

    def close(self) -> None:
        """Shutdown: close the couchbase cluster so its IO threads stop
        BEFORE interpreter finalization (a live asio thread touching
        CPython during Py_Finalize is a SIGSEGV, seen in the wild)."""
        cluster = getattr(self, "_cluster", None)
        if cluster is None:
            return
        self._cluster = self._bucket = self._reg = self._reg_bucket = None
        self._chan = None
        try:
            cluster.close()
        except Exception:
            pass

    # ---- the chat channel ----
    def post(self, text: str, reply_to: str = "") -> bool:
        """One channel message: {from, ts, text, reply_to?}. Permanent
        (no TTL) -- the chat is the commons' memory of its own talk."""
        if self._chan is None:
            return False
        msg = {"from": self.seat_id, "ts": time.time(),
               "text": str(text)[:2000]}
        if reply_to:
            msg["reply_to"] = reply_to
        try:
            key = f"msg::{int(time.time() * 1000)}::{self.seat_id}"
            self._chan.upsert(key, msg)
            self._op_ok()
            return True
        except BaseException:
            self._op_failed()
            return False

    def fetch_unread(self) -> list[dict]:
        """Every channel message past this seat's cursor (stored in the
        registry doc, rides the heartbeat), oldest first, capped. Marks
        the cursor. Read-once per seat: the operator sees each line on
        their seat until read."""
        if self._chan is None or self._reg is None:
            return []
        try:
            from couchbase.options import QueryOptions
            import datetime as _dt
            q = self._cluster.query(
                f"SELECT t.* FROM `{self._bucket.name}`.`_default`."
                f"peer_chat t WHERE t.ts > $since "
                "AND t.`from` != $me ORDER BY t.ts ASC LIMIT $lim",
                QueryOptions(timeout=_dt.timedelta(seconds=10),
                             named_parameters={
                                 "since": self._cursor_ts,
                                 "me": self.seat_id,
                                 "lim": CHANNEL_MAX_FETCH}))
            rows = [dict(r) for r in q]
            if rows:
                last = rows[-1].get("ts")
                if last:
                    self._cursor_ts = float(last)
                    self.register()   # the cursor rides the heartbeat doc
            self._op_ok()
            return rows
        except BaseException:
            self._op_failed()
            return []


def _timedelta(s: float):
    from datetime import timedelta
    return timedelta(seconds=s)