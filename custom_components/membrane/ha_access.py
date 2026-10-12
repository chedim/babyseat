"""THE SEAT'S WAY INTO HOME ASSISTANT (2026-10-12, the operator: "can it just...
integrate?" -- then "ok, letz do it that way").

- ACT: a hidden system user "Membrane" (non-admin, local-only; HA's own way for a
  component -- the Supervisor and Cloud have theirs), a system refresh token, and an
  access token minted at each seat start: the seat gets HASS_URL + HASS_TOKEN and
  uses HA's REST API like any client. The user is removed with the integration.
- SENSE: the seat marks field entities with properties.ha_entity; HA pushes those
  entities' state changes to the seat (POST /membrane/ha/states, batched), the list
  re-read every minute (GET /membrane/ha/subscriptions) -- membrane/ha_bridge.py.
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
