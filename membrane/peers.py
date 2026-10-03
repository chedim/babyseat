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

import urllib.request as _urllib_request_ur
from pathlib import Path


def _urlopen(req, timeout):
    """The ask's transport seam (the tests swap it; production runs
    urllib)."""
    return _urllib_request_ur.urlopen(req, timeout=timeout)

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
        # THE ROUTE HEALTH (P1, the stale-route defect): a peer whose
        # ask failed recently (seat moved ports, seat down) drops from
        # livelist for a cooldown window instead of being retried
        # against the grave every turn; a successful ask clears it.
        self._route_bad: dict[str, float] = {}
        self._route_bad_s: float = 300.0
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
        # THE CURSOR'S LOCAL HOME (the ephemeral-registry-death fix):
        # the cursor ALSO persists beside the field (mode 600) — the
        # ephemeral doc's copy is a fast-start hint, never the
        # authority (the TTL'd doc GC'd = the seat re-fetching the
        # whole channel from epoch = the spiral's repeat engine).
        self._cursor_file: Path | None = (
            Path(field_path).resolve().parent / "chan-cursor.json"
            if field_path else None)
        # the seen-echoes ledger: (from, text) heads the window keeps;
        # a message-id ring for the retry-safe dedupe
        self._seen_window: dict[tuple, float] = {}
        self._ECHO_WINDOW_S = 600.0
        self.restore_cursor()               # the local file governs
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
                # THE FALLBACK'S LIFE: a dead registry used to kill the
                # read here ('return []') -- the LAN lanes below (mdns +
                # presence) never ran. A registry failure is a lane
                # failure, not a seat-read failure: fall through.
                self._op_failed()
                out = []
        if out:
            return out
        # THE LAN LANE (the zeroconf peers): the roster union's newest
        # member -- mDNS finds the LAN's seats without couchbase.
        try:
            # the instance seam (a fake/_zc override wins; None falls
            # through to the module's real lane)
            mdns = mdns_peers(getattr(self, "_zc_impl", None)
                              or getattr(self, "_zc", None))
            presence = self._presence_seats()
            by_seat: dict[str, dict] = {}
            # mdns rows WIN the dedupe (the live browse is the
            # freshest read of who is actually THERE now; presence is
            # TTL-presumed life) -- dedupe by seat, one lane per seat
            for m in mdns:
                by_seat[str(m.get("seat") or "")] = m
            for s in presence:
                by_seat.setdefault(str(s.get("seat") or ""), s)
            return list(by_seat.values())
        except Exception:
            return self._presence_seats()

    _PRESENCE_STALE_S = 600.0        # a quiet seat older than 10min is
                                     # presumed dead (TTL without TTLs)

    @staticmethod
    def _route_key(peer: dict) -> str:
        return f"{peer.get('host') or '127.0.0.1'}:{int(peer.get('port') or 0)}"

    def note_route_bad(self, peer: dict) -> None:
        """THE DEAD ROUTE: an ask (or reachability probe) that failed --
        this route isn't retried for _route_bad_s (the stale-route
        cooldown; a seat that RE-announces at a new port re-enters as a
        fresh key)."""
        self._route_bad[self._route_key(peer)] = time.time()

    def note_route_good(self, peer: dict) -> None:
        self._route_bad.pop(self._route_key(peer), None)

    def _route_alive(self, peer: dict) -> bool:
        ts = self._route_bad.get(self._route_key(peer))
        return ts is None or time.time() - ts > self._route_bad_s

    def livelist(self, exclude_self: bool = True) -> list[dict]:
        """The slice-4 name for the live registry: every seat doc except
        (optionally) this one, minus peers whose routes recently failed
        (the stale-route cooldown). Empty on any failure (the
        covenant)."""
        seats = self.list_seats()
        if exclude_self:
            seats = [s for s in seats
                     if str(s.get("port") or "") != str(self.port)]
        seats = [s for s in seats if self._route_alive(s)]
        return seats

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
    def ask_peer(self, peer: dict, situation: str, sim_min: float = 0.60,
                 timeout: float = 20.0) -> dict | None:
        """THE ROUTE-HEALTH AWARE ask: the static ask() with the outcome
        recorded -- a dead route (the seat moved ports, seat down)
        cooldowns off livelist; a hit clears the record."""
        out = self.ask(peer, situation, sim_min=sim_min, timeout=timeout)
        if out is None:
            self.note_route_bad(peer)
        else:
            self.note_route_good(peer)
        return out

    @staticmethod
    def ask(peer: dict, situation: str, sim_min: float = 0.60,
            timeout: float = 20.0) -> dict | None:
        """One live ask: GET http://<host>:<port>/membrane/ask?q=...
        The slice-4 Peers.ask contract; THE LAN REMOVAL (the '127.0.0.1
        assumption' wall): host arrives via the peer dict
        (mdns_peers/registry rows; default 127.0.0.1 -- the legacy
        same-host calls byte-identical); all failures degrade None."""
        import urllib.parse
        import urllib.request
        q = urllib.parse.quote(situation[:400])
        host = str(peer.get("host") or "127.0.0.1")
        url = f"http://{host}:{int(peer.get('port', 0))}/membrane/ask?q={q}"
        try:
            with _urlopen(req := urllib.request.Request(url), timeout) as r:
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
    def post(self, text: str, reply_to: str = "", mid: str = "") -> bool:
        """One channel message: {from, ts, text, reply_to?, mid?}.
        Permanent (no TTL) -- the chat is the commons' memory of its own
        talk. THE MESSAGE-ID (the retry-safe id: a repost of the same
        text carries the same mid -- the readers' echo filter collapses
        the retry): mid defaults to from+text-head+ts-ms."""
        if self._chan is None:
            return False
        ts = time.time()
        if not mid:
            import hashlib as _h
            mid = _h.sha1(
                f"{self.seat_id}|{text[:160]}|{int(ts*1000)%100000}".encode()
            ).hexdigest()[:10]
        msg = {"from": self.seat_id, "ts": ts, "text": str(text)[:2000],
               "mid": mid}
        if reply_to:
            msg["reply_to"] = reply_to
        try:
            key = f"msg::{int(ts * 1000)}::{self.seat_id}"
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
        their seat until read.
        THE CURSOR'S AUTHORITY (the spiral's repeat engine): the LOCAL
        file cursor rules (restore first); the ephemeral doc's copy is
        never read as authority (a TTL'd registry GC'd resets it to
        0.0, and a 0.0 fetch = the whole channel re-rendered + the
        models replying to repeats). The echo filter drops a REPEATED
        (from, mid/text) inside the window even when a cursor loss
        slips one through."""
        if self._chan is None or self._reg is None:
            return []
        self.restore_cursor()
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
            # THE ECHO FILTER: a repeated (from, mid/text-head) drops
            # (a re-fetch or a retry's echo never re-renders)
            rows = [m for m in rows if not self._echo_seen(m)]
            if rows:
                self.note_cursor(float(rows[-1].get("ts") or 0.0))
            self._op_ok()
            return rows
        except BaseException:
            self._op_failed()
            return []


    def note_cursor(self, ts: float) -> None:
        """The cursor's authority: the LOCAL file write (mode 600)
        first, on-disk always; the RAM state follows; the ephemeral
        doc's copy rides the next heartbeat (the hint)."""
        self._cursor_ts = float(ts)
        try:
            if self._cursor_file is not None:
                self._cursor_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._cursor_file.with_suffix(".tmp")
                tmp.write_text(json.dumps({"chan_cursor": self._cursor_ts,
                                           "saved_ts": time.time()}))
                import os as _os
                _os.chmod(tmp, 0o600)
                _os.replace(tmp, self._cursor_file)
        except OSError:
            pass                 # RAM carries on an unwritable seat

    def restore_cursor(self) -> None:
        """Boot/read-start: the LOCAL file is the authority; the RAM
        default (0.0) only when no file exists. The ephemeral doc's
        copy (the registry's chan_cursor) is NEVER read as authority
        (the TTL can have expired between save and restore)."""
        try:
            if self._cursor_file is not None and \
                    self._cursor_file.exists():
                d = json.loads(self._cursor_file.read_text())
                self._cursor_ts = float(d.get("chan_cursor") or 0.0)
        except Exception:
            pass                 # the file lost: start at 0 (honest)

    def _echo_seen(self, msg: dict, now: float | None = None,
                   window_s: float | None = None) -> bool:
        """THE ECHO FILTER (the belt to the cursor's braces): a
        message's (from, text-head) seen inside the window marks it
        (returns True -- the caller drops), a NEW one records
        (returns False -- the caller renders). The window prunes."""
        now = now if now is not None else time.time()
        window_s = window_s if window_s is not None else self._ECHO_WINDOW_S
        key = (str(msg.get("from") or "?"),
               str(msg.get("text") or "")[:160],
               str(msg.get("mid") or ""))
        # prune older sightings
        for k in [k for k, t in self._seen_window.items()
                  if now - t > window_s]:
            self._seen_window.pop(k, None)
        if key in self._seen_window:
            return True
        self._seen_window[key] = now
        return False


def _timedelta(s: float):
    from datetime import timedelta
    return timedelta(seconds=s)

# ---- the LAN lane (zeroconf/mDNS, the operator's 2026-10-02 ask) ----
SERVICE_TYPE = "_membrane-proxy._tcp.local."
MDNS_PEER_TTL_S = 60.0      # a browse entry's staleness cap

try:
    import zeroconf as _zc
except Exception:
    _zc = None              # import fails -> the lane is off (degrade)


def lan_enabled() -> bool:
    return os.environ.get("MEMBRANE_LAN", "1") not in ("0", "false", "off")


def presence_txt(model: str, seat: str, port: int, docs: int = 0,
                 branch: str = "", head: str = "",
                 extra: dict | None = None) -> dict:
    """THE TXT RECORD: the presence entity's PUBLIC fields only. THE
    AUDIT: no absolute paths, no usernames, no message text -- only
    what the presence entity publishes to the GLOBAL tier (a LAN seat
    could have read them there anyway)."""
    txt = {"model": (model or "")[:40],
           "seat": (seat or "")[:64],
           "port": str(int(port or 0)),
           "docs": str(int(docs or 0)),
           "branch": (branch or "")[:40],
           "head": (head or "")[:12],
           "proto": "membrane/1"}
    if extra:
        for k in ("ephemeral", "free_vram_gib"):
            if k in extra:
                txt[k] = str(extra[k])
    return txt


class MdnsAnnouncer:
    """The seat's mDNS face: announceServiceInfo (SERVICE_TYPE, port,
    the TXT props) on the boot + a re-announce beside the heartbeat.
    A None/injected zc impl (the tests) or a missing zeroconf module
    degrades the whole lane silently."""

    def __init__(self, model: str, seat: str, port: int, docs: int = 0,
                 branch: str = "", head: str = "", zc_impl=None,
                 extra: dict | None = None):
        self.port = int(port or 0)
        self.model = model
        self.seat = seat
        self.zc = zc_impl if zc_impl is not None else (
            _zc.Zeroconf() if _zc is not None and lan_enabled() else None)
        self._info = None
        self._txt = presence_txt(model, seat, port, docs, branch, head,
                                 extra)

    def _make_info(self):
        props = dict(self._txt)
        props["seen_ts"] = time.time()
        if _zc is None or not hasattr(_zc, "ServiceInfo"):
            # the injected/fake impl: a plain dict carries the
            # properties (the fake's browse reads them)
            return {"name": f"{self.seat}.{SERVICE_TYPE}",
                    "port": self.port, "properties": props,
                    "seen_ts": time.time()}
        return _zc.ServiceInfo(SERVICE_TYPE,
                               f"{self.seat}.{SERVICE_TYPE}",
                               port=self.port,
                               properties={k.encode(): v.encode()
                                           for k, v in props.items()})

    def announce(self) -> bool:
        if self.zc is None:
            return False
        try:
            info = self._make_info()
            if info is None:
                return False
            self.zc.register_service(info)
            self._info = info
            return True
        except Exception:
            self.zc = None      # a raise mid-announce kills the lane
            return False

    def close(self) -> None:
        try:
            if self.zc is not None and self._info is not None:
                self.zc.unregister_service(self._info)
        except Exception:
            pass


def _int_ttl() -> int:
    return 120


def mdns_peers(zc_impl=None, now: float | None = None) -> list[dict]:
    """The LAN's seats: browse the service type -> the roster rows
    ({seat, port, model, docs, via: 'mdns'}); entries older than
    MDNS_PEER_TTL_S (the caller's clock) drop (the GC is the clock's)."""
    now = float(now if now is not None else time.time())
    impl = zc_impl
    if impl is None and _zc is not None and lan_enabled():
        try:
            impl = _zc.Zeroconf()
        except Exception:
            impl = None
    if impl is None:
        return []
    out = []
    try:
        # BOTH shapes: a fake's browse() dict or a real ZC's get_service_info
        found = {}
        if hasattr(impl, "browse"):
            found = impl.browse() or {}
        out = []
        for name, info in (found or {}).items():
            txt = getattr(info, "properties", None) or \
                (info.get("properties") if isinstance(info, dict) else None) \
                or {}
            def _s(k):
                v = txt.get(k, b"")
                return (v.decode() if isinstance(v, bytes) else str(v))
            seat = _s("seat")
            try:
                port = int(_s("port") or 0)
            except ValueError:
                port = 0
            seen_ts = getattr(info, "seen_ts", None)
            if seen_ts is None and isinstance(info, dict):
                seen_ts = info.get("seen_ts")
            if seen_ts is not None and now - float(seen_ts) > MDNS_PEER_TTL_S:
                continue            # stale: the clock GC'd it
            out.append({"seat": seat, "port": port, "model": _s("model"),
                        "docs": int(_s("docs") or 0), "via": "mdns"})
        return out
    except Exception:
        return []


def note_peer_miss(pool, peer: dict, situation: str,
                   why: str | None, srv=None) -> None:
    """THE ESCALATION EVENT (the operator's 2026-10-02 ask): a peer ask
    that found NOTHING is an event in the watch-events lane (the loops'
    pool sees failed escalations); an ANSWERED ask is quiet (the answer
    rode as context). THE WAKE CALL (srv passed): the room changed --
    the night mind opens its eyes."""
    if pool is None or why is None:
        return
    ev = {"kind": "peer-miss",
          "peer": str((peer or {}).get("seat")
                      or (peer or {}).get("port") or "?"),
          "situation": str(situation or "")[:160],
          "why": str(why)[:80],
          "ts": time.time()}
    pool.put(ev)
    if srv is not None:
        try:
            from .thinker import note_wakeup_signal
            note_wakeup_signal(srv, "peer-miss", ev)
        except Exception:
            pass
