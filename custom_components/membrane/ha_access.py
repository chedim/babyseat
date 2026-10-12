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
- TOLD: restarts.py's contract -- the integration posts {kind: "ha-start"}
  (and, on the way down, {kind: "ha-stop"}) to the seat's POST /membrane/seat/event,
  so a detached seat never reads an HA restart as the world going quiet.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

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
    if not (url and comp.get("serve_pass")):
        return False
    body = {"url": str(comp.get("ha_base_url") or "") or routable_url(hass), "token": token}
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
    if not (url and comp.get("serve_pass")):
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


class StatePush:
    """HA's state changes of the entities the seat mirrors, pushed in batches."""

    def __init__(self, hass: HomeAssistant, comp: dict):
        self.hass, self.comp = hass, comp
        self.subs: set[str] = set()
        self.pending: dict[str, dict] = {}
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
            if url and self.comp.get("serve_pass"):
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(url + "/membrane/ha/subscriptions", auth=self._auth(),
                                         timeout=aiohttp.ClientTimeout(total=30)) as r:
                            new = set((await r.json()).get("entity_ids") or []) if r.status == 200 else self.subs
                    for eid in new - self.subs:          # a new mirror starts with the state as it is
                        st = self.hass.states.get(eid)
                        if st is not None:
                            self.pending[eid] = {"entity_id": eid, "state": st.state, "attributes": dict(st.attributes),
                                                 "last_changed": st.last_changed.isoformat()}
                    self.subs = new
                except Exception as e:                    # noqa: BLE001
                    _LOGGER.debug("membrane: subscriptions read failed: %r", e)
            await asyncio.sleep(SUBS_EVERY_S)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_S)
            if not self.pending or not self.comp.get("serve_pass"):
                continue
            batch, self.pending = list(self.pending.values()), {}
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(self.comp["serve_url"] + "/membrane/ha/states", json={"states": batch},
                                      auth=self._auth(), timeout=aiohttp.ClientTimeout(total=30)) as r:
                        await r.read()
            except Exception as e:                        # noqa: BLE001
                _LOGGER.debug("membrane: state push failed: %r", e)
