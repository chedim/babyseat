"""THE SEAT'S WAY INTO HOME ASSISTANT (2026-10-12, the operator: "can it just...
integrate?" -- then "ok, letz do it that way"; 2026-10-12 detached: the seat is
not HA's child -- the token RIDES TO IT over its own serve, POST /membrane/ha/token,
and it lands in the seat's ~/.config/membrane/hass.env).

- ACT: a hidden system user "Membrane" (non-admin, local-only; HA's own way for a
  component -- the Supervisor and Cloud have theirs), a system refresh token, and an
  access token minted at each seat start: the seat gets HASS_URL + HASS_TOKEN and
  uses HA's REST API like any client. The user is removed with the integration.
  Inside mode delivers the pair in the child process's environment; CONNECT
  (detached) mode POSTs it to the seat instead -- re-pushed at every HA start
  (membrane/opencode.py reads the file on its next turn, no seat restart).
- SENSE: the seat marks field entities with properties.ha_entity; HA pushes those
  entities' state changes to the seat (POST /membrane/ha/states, batched), the list
  re-read every minute (GET /membrane/ha/subscriptions) -- membrane/ha_bridge.py.
  StatePush targets comp["serve_url"] in BOTH modes: inside = the local child,
  connect = the seat elsewhere; the pair is already on the entry.
- MAPPED (2026-10-30, the operator: "automatic homeassistant entity, services and
  methods mapping onto membrane graph"): HA's catalog -- every entity and every
  service, with whether it targets entities and its field names -- is pushed to
  POST /membrane/ha/catalog when it changes (checked every minute); the seat makes
  classes, live entities and methods of it (ha_bridge.import_catalog).
- MQTT (same day: "auto-integrate mqtt with the field if its configured in hass"):
  when HA runs its MQTT integration, the topics the seat marked (mqtt_topics in the
  subscriptions read) are subscribed through HA's own client and their messages
  pushed to POST /membrane/ha/mqtt, batched with the states. No broker config on
  the seat; nothing at all when HA has no MQTT.
- TOLD: restarts.py's contract -- the integration posts {kind: "ha-start"}
  (and, on the way down, {kind: "ha-stop"}) to the seat's POST /membrane/seat/event,
  so a detached seat never reads an HA restart as the world going quiet.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from datetime import timedelta
from urllib.parse import urlsplit

import aiohttp

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

USER_NAME = "Membrane"
TOKEN_LIFE = timedelta(days=3650)       # minted fresh at every seat start anyway
FLUSH_S = 2.0
SUBS_EVERY_S = 60.0


async def _find_user(hass: HomeAssistant):
    for u in await hass.auth.async_get_users():
        if u.system_generated and u.name == USER_NAME:
            return u
    return None


async def ensure_user(hass: HomeAssistant, entry):
    """The hidden user, found by its name (never stored in the entry: an entry
    update reloads the integration -- and restarts the seat)."""
    user = await _find_user(hass)
    if user is None:
        user = await hass.auth.async_create_system_user(USER_NAME, group_ids=[GROUP_ID_USER], local_only=True)
        _LOGGER.info("membrane: created the seat's Home Assistant user (hidden, non-admin, local only)")
    return user


async def mint_token(hass: HomeAssistant, entry) -> str:
    user = await ensure_user(hass, entry)
    rt = next((t for t in user.refresh_tokens.values() if t.token_type == "system"), None)
    if rt is None:
        rt = await hass.auth.async_create_refresh_token(user, access_token_expiration=TOKEN_LIFE)
    return hass.auth.async_create_access_token(rt)


async def remove_user(hass: HomeAssistant, entry) -> None:
    user = await _find_user(hass)
    if user is not None:
        await hass.auth.async_remove_user(user)


def local_url(hass: HomeAssistant) -> str:
    api = getattr(hass.config, "api", None)
    port = getattr(api, "port", None) or 8123
    scheme = "https" if getattr(api, "use_ssl", False) else "http"
    return f"{scheme}://127.0.0.1:{port}"


def routable_url(hass: HomeAssistant) -> str:
    """HA's address as the detached seat sees it -- a phone on the LAN, not this
    machine's loopback (the operator's own URL, then HA's external, then local)."""
    for u in (getattr(hass.config, "internal_url", None), getattr(hass.config, "external_url", None)):
        if isinstance(u, str) and u.startswith("http"):
            return u.rstrip("/")
    return local_url(hass)


def _ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _seat_place(host: str) -> str:
    """Where the seat is, from its serve_url host: "local" (loopback -- HA's
    own host), "public" (globally routable) or "lan" (private, link-local,
    CGNAT, or a name that does not resolve here). A name is resolved; any
    loopback answer makes it local."""
    ip = _ip(host)
    ips = [ip] if ip else []
    if ip is None and host:
        try:
            ips = [_ip(a[4][0].split("%")[0]) for a in socket.getaddrinfo(host, None)]
        except (OSError, UnicodeError):
            ips = []
        ips = [i for i in ips if i is not None]
    if any(i.is_loopback for i in ips):
        return "local"
    if ips and all(i.is_global for i in ips):
        return "public"
    return "lan"


def _get_url(hass: HomeAssistant, **kw) -> str | None:
    """HA's own answer (homeassistant.helpers.network.get_url), None when it has none."""
    try:
        from homeassistant.helpers.network import NoURLAvailableError, get_url
    except ImportError:
        return None
    try:
        return get_url(hass, **kw).rstrip("/")
    except NoURLAvailableError:
        return None


def _bound_to_loopback(hass: HomeAssistant) -> bool:
    ip = _ip(str(getattr(getattr(hass.config, "api", None), "host", None) or ""))
    return bool(ip and ip.is_loopback)


async def seat_ha_url(hass: HomeAssistant, comp: dict) -> str | None:
    """The URL the detached seat is handed for HA (#529): the ha_base_url
    option first; a seat on HA's host keeps routable_url (down to the local
    door); a seat on HA's LAN gets HA's internal address (then its external);
    a seat outside the LAN gets HA's external address. Never a loopback
    address to a seat elsewhere: None, with a warning naming the option."""
    if comp.get("ha_base_url"):
        return str(comp["ha_base_url"])
    seat = comp.get("serve_url") or ""
    host = urlsplit(seat).hostname or ""
    place = await asyncio.get_running_loop().run_in_executor(None, _seat_place, host)
    if place == "local":
        return routable_url(hass)
    if place == "public":
        url = _get_url(hass, allow_internal=False, allow_external=True, prefer_external=True)
    else:
        # HA bound to loopback (a reverse proxy in front): HA's source-IP
        # fallback pairs the LAN IP with HA's own port -- no address; only a
        # configured internal_url speaks for the LAN then
        internal = not _bound_to_loopback(hass) or bool(getattr(hass.config, "internal_url", None))
        url = _get_url(hass, allow_internal=internal, allow_external=True, prefer_external=False)
    ip = _ip(urlsplit(url or "").hostname or "")
    if url and not (ip and ip.is_loopback) and (urlsplit(url).hostname or "") != "localhost":
        return url
    _LOGGER.warning(
        "membrane: Home Assistant has no address the seat at %s can reach (it is not on this host and "
        "Home Assistant has no %s URL); set the integration's option \"HA's address as the seat sees it\" "
        "(ha_base_url) -- the seat gets no Home Assistant token until then",
        seat, "external" if place == "public" else "internal or external")
    return None


def _auth(comp: dict) -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(comp.get("serve_user") or "opencode", comp.get("serve_pass") or "")


async def push_token(hass: HomeAssistant, entry, comp: dict) -> bool:
    """Deliver HA access to a DETACHED seat: a fresh token, POSTed to its serve
    (/membrane/ha/token) -- the seat writes ~/.config/membrane/hass.env and reads
    it on its next turn. Re-run at every HA start: tokens stay un-minted-at-rest
    on HA's side (restarts.py's property, kept by re-pushing)."""
    try:
        token = await mint_token(hass, entry)
    except Exception as e:                           # noqa: BLE001
        _LOGGER.warning("membrane: could not mint the seat's Home Assistant token (%r)", e)
        return False
    url = comp.get("serve_url") or ""
    if not (url and comp.get("serve_ready")):
        return False
    ha_url = await seat_ha_url(hass, comp)
    if not ha_url:
        return False
    body = {"url": ha_url, "token": token}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url + "/membrane/ha/token", json=body, auth=_auth(comp),
                              timeout=aiohttp.ClientTimeout(total=30)) as r:
                ok = r.status == 200
                await r.read()
    except Exception as e:                           # noqa: BLE001
        _LOGGER.debug("membrane: the token did not reach the seat (%r)", e)
        return False
    if ok:
        _LOGGER.info("membrane: Home Assistant access delivered to the detached seat")
    else:
        _LOGGER.debug("membrane: the seat did not take the token")
    return ok


async def seat_event(comp: dict, kind: str, timeout_s: float = 30.0) -> bool:
    """A word to the detached seat: {kind: ha-start|ha-stop} (restarts.py) -- its
    /membrane/seat/event answers {"ok": False, ...} for unknown kinds."""
    url = comp.get("serve_url") or ""
    if not (url and comp.get("serve_ready")):
        return False
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url + "/membrane/seat/event", json={"kind": kind}, auth=_auth(comp),
                              timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
                await r.read()
                return r.status == 200
    except Exception as e:                           # noqa: BLE001
        _LOGGER.debug("membrane: %s did not reach the seat (%r)", kind, e)
        return False


def seat_event_sync(comp: dict, kind: str, timeout_s: float = 5.0) -> bool:
    """The same word, without the loop: the ha-stop listener runs from an
    executor thread during HA's shutdown (async listeners' tasks are cancelled
    first), so the POST goes by hand. Returns False on anything."""
    import json as _json
    import urllib.request

    url = (comp.get("serve_url") or "").rstrip("/")
    if not (url and comp.get("serve_ready")):
        return False
    req = urllib.request.Request(
        url + "/membrane/seat/event", data=_json.dumps({"kind": kind}).encode(),
        headers={"Content-Type": "application/json"})
    import base64
    pair = base64.b64encode(f"{comp.get('serve_user') or 'opencode'}:{comp.get('serve_pass')}".encode()).decode()
    req.add_header("Authorization", "Basic " + pair)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            r.read()
            return r.status == 200
    except Exception as e:                           # noqa: BLE001
        _LOGGER.debug("membrane: %s did not reach the seat (%r)", kind, e)
        return False


class StatePush:
    """HA's state changes of the entities the seat mirrors, pushed in batches."""

    def __init__(self, hass: HomeAssistant, comp: dict):
        self.hass, self.comp = hass, comp
        self.subs: set[str] = set()
        self.pending: dict[str, dict] = {}
        self.mqtt_unsubs: dict = {}                  # topic filter -> HA's unsubscribe
        self.mqtt_pending: list[dict] = []
        self._catalog_digest: str | None = None
        self._unsub = None
        self._tasks: list[asyncio.Task] = []

    def _auth(self):
        return aiohttp.BasicAuth(self.comp.get("serve_user") or "opencode", self.comp.get("serve_pass") or "")

    def start(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_change)
        self._tasks = [self.hass.async_create_background_task(self._subs_loop(), "membrane ha subs"),
                       self.hass.async_create_background_task(self._flush_loop(), "membrane ha push")]

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        for t in self._tasks:
            t.cancel()
        for un in self.mqtt_unsubs.values():
            try:
                un()
            except Exception:                         # noqa: BLE001
                pass
        self.mqtt_unsubs = {}

    @callback
    def _on_change(self, event: Event) -> None:
        eid = event.data.get("entity_id")
        if eid not in self.subs:
            return
        st = event.data.get("new_state")
        self.pending[eid] = {"entity_id": eid, "state": st.state if st else None,
                             "attributes": dict(st.attributes) if st else {},
                             "last_changed": st.last_changed.isoformat() if st else ""}

    async def _subs_loop(self) -> None:
        while True:
            url = self.comp.get("serve_url")
            if url and self.comp.get("serve_ready"):
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(url + "/membrane/ha/subscriptions", auth=self._auth(),
                                         timeout=aiohttp.ClientTimeout(total=30)) as r:
                            js = (await r.json()) if r.status == 200 else None
                        new = set(js.get("entity_ids") or []) if js else self.subs
                        if js is not None:
                            await self._sync_mqtt(set(js.get("mqtt_topics") or []))
                        await self._push_catalog(s)
                    for eid in new - self.subs:          # a new mirror starts with the state as it is
                        st = self.hass.states.get(eid)
                        if st is not None:
                            self.pending[eid] = {"entity_id": eid, "state": st.state, "attributes": dict(st.attributes),
                                                 "last_changed": st.last_changed.isoformat()}
                    self.subs = new
                except Exception as e:                    # noqa: BLE001
                    _LOGGER.debug("membrane: subscriptions read failed: %r", e)
            await asyncio.sleep(SUBS_EVERY_S)

    async def _catalog(self) -> dict:
        from homeassistant.helpers.service import async_get_all_descriptions
        desc = await async_get_all_descriptions(self.hass)
        services = {dom: {svc: {"target": bool((d or {}).get("target")),
                                "fields": sorted(((d or {}).get("fields") or {}).keys())}
                          for svc, d in (svcs or {}).items()}
                    for dom, svcs in desc.items()}
        entities = sorted(({"entity_id": st.entity_id, "name": str(st.attributes.get("friendly_name") or "")}
                           for st in self.hass.states.async_all()), key=lambda e: e["entity_id"])
        return {"entities": entities, "services": services, "mqtt": "mqtt" in self.hass.config.components}

    async def _push_catalog(self, s: aiohttp.ClientSession) -> None:
        """HA's catalog to the seat, only when it changed since the last push."""
        import hashlib
        import json
        try:
            cat = await self._catalog()
            dig = hashlib.sha1(json.dumps(cat, sort_keys=True, default=str).encode()).hexdigest()
            if dig == self._catalog_digest:
                return
            async with s.post(self.comp["serve_url"] + "/membrane/ha/catalog", json=cat, auth=self._auth(),
                              timeout=aiohttp.ClientTimeout(total=120)) as r:
                if r.status == 200:
                    self._catalog_digest = dig
        except Exception as e:                        # noqa: BLE001
            _LOGGER.debug("membrane: catalog push failed: %r", e)

    @callback
    def _on_mqtt(self, msg) -> None:
        payload = msg.payload if isinstance(msg.payload, str) else bytes(msg.payload).decode("utf-8", "replace")
        self.mqtt_pending.append({"topic": msg.topic, "payload": payload})
        del self.mqtt_pending[:-500]                 # a flood keeps its newest

    async def _sync_mqtt(self, want: set) -> None:
        """Subscribe the seat's topics through HA's MQTT client (when HA has one)."""
        if "mqtt" not in self.hass.config.components:
            return
        from homeassistant.components import mqtt
        for t in set(self.mqtt_unsubs) - want:
            try:
                self.mqtt_unsubs.pop(t)()
            except Exception:                         # noqa: BLE001
                pass
        for t in want - set(self.mqtt_unsubs):
            try:
                self.mqtt_unsubs[t] = await mqtt.async_subscribe(self.hass, t, self._on_mqtt, qos=0)
            except Exception as e:                    # noqa: BLE001
                _LOGGER.debug("membrane: mqtt subscribe %s failed: %r", t, e)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            if not self.comp.get("serve_ready"):
                continue
            if self.mqtt_pending:
                msgs, self.mqtt_pending = self.mqtt_pending, []
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.post(self.comp["serve_url"] + "/membrane/ha/mqtt", json={"messages": msgs},
                                          auth=self._auth(), timeout=aiohttp.ClientTimeout(total=30)) as r:
                            await r.read()
                except Exception as e:                    # noqa: BLE001
                    _LOGGER.debug("membrane: mqtt push failed: %r", e)
            if not self.pending:
                continue
            batch, self.pending = list(self.pending.values()), {}
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(self.comp["serve_url"] + "/membrane/ha/states", json={"states": batch},
                                      auth=self._auth(), timeout=aiohttp.ClientTimeout(total=30)) as r:
                        await r.read()
            except Exception as e:                        # noqa: BLE001
                _LOGGER.debug("membrane: state push failed: %r", e)
