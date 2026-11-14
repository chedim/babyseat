"""The seat's use of Home Assistant (#1143, slice 2): the model writes a `HaCall` on the seat, this component PULLS the pending ones over its own session (the owner's token, the one
it already talks to the seat with), runs them with Home Assistant's own services and states, and writes the receipt back. The seat holds no Home Assistant secret. Home Assistant's own
line stays here: only what the person exposed to Assist (Settings -> Voice assistants -> Expose) is in the catalog, can be read, or can be named by a call. What the model MAY call is the
seat's allow-list (`HaAllow`, default deny; membrane/packs/ha.c1f), checked on the seat before a call is ever offered."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import Unauthorized

from . import const
from .seat_client import SeatAuthError, SeatClient, SeatError, SeatForbidden

_LOGGER = logging.getLogger(__name__)


def jsonable(x):
    """A value as plain JSON (Home Assistant's states carry datetimes, read-only dicts, enums)."""
    return json.loads(json.dumps(x, default=str))


async def find_user(hass: HomeAssistant, user_id: str | None = None):
    """The hidden Home Assistant user: by the id the entry keeps, else by its name among the system users. None when there is none."""
    if user_id:
        user = await hass.auth.async_get_user(user_id)
        if user is not None:
            return user
    for u in await hass.auth.async_get_users():
        if getattr(u, "system_generated", False) and u.name == const.HA_USER_NAME:
            return u
    return None


def refusal(e: Exception, domain: str, service: str) -> str:
    """Home Assistant's Unauthorized carries no message, only what it was about: say it, so the model reads who was refused what."""
    what = ", ".join(str(x) for x in (getattr(e, "entity_id", None),) if x)
    perm = " ".join(str(x) for x in (getattr(e, "perm_category", None), getattr(e, "permission", None)) if x)
    return (f"Home Assistant refused {domain}.{service} for its {const.HA_USER_NAME} user (unauthorized"
            f"{': ' + perm if perm else ''}{' on ' + what if what else ''}): the person grants the user more in Home Assistant, or not")


def entity_ids(request: dict) -> list:
    """The entities a service call names: target.entity_id and data.entity_id (a text or a list)."""
    out = []
    for part in (request.get("target"), request.get("data")):
        v = (part or {}).get("entity_id") if isinstance(part, dict) else None
        out += [v] if isinstance(v, str) else (list(v) if isinstance(v, list) else [])
    return [str(x) for x in out]


class House:
    def __init__(self, hass: HomeAssistant, client: SeatClient, entry=None) -> None:
        self.hass = hass
        self.client = client
        self.entry = entry                      # keeps the hidden user's id (data `ha_user_id`); None: found by name only
        self.forbidden = False                  # the seat said the token is not the owner's: nothing to pull
        self._digest: str | None = None         # of the catalog the seat has
        self.last_catalog: dict = {"entities": [], "services": []}

    # ---- the user every call runs as ---------------------------------------------------------------------------------------------------------

    async def ensure_user(self):
        """The hidden Home Assistant system user "Membrane" (non-admin, local only, group USER): found by the entry's id, else by name, else created; its id is kept in the entry.
        Home Assistant's own permission model decides what a call run as it may touch."""
        user = await find_user(self.hass, (self.entry.data.get(const.CONF_HA_USER_ID) if self.entry is not None else None))
        if user is None:
            user = await self.hass.auth.async_create_system_user(const.HA_USER_NAME, group_ids=[GROUP_ID_USER], local_only=True)
            _LOGGER.info("membrane: created the Home Assistant user %s (hidden, non-admin, local only)", const.HA_USER_NAME)
        if self.entry is not None and self.entry.data.get(const.CONF_HA_USER_ID) != user.id:
            self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, const.CONF_HA_USER_ID: user.id})
        return user

    # ---- the catalog ---------------------------------------------------------------------------------------------------------------------------

    def _exposed(self, entity_id: str) -> bool:
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose
        return bool(async_should_expose(self.hass, "conversation", entity_id))

    async def catalog(self) -> dict:
        """What Home Assistant exposes: the entities exposed to Assist, and every service ({id: domain.service, target: takes entities, fields: names})."""
        from homeassistant.helpers.service import async_get_all_descriptions
        desc = await async_get_all_descriptions(self.hass)
        services = [{"id": f"{dom}.{svc}", "target": bool((d or {}).get("target")), "fields": sorted(((d or {}).get("fields") or {}).keys())}
                    for dom, svcs in sorted(desc.items()) for svc, d in sorted((svcs or {}).items())]
        entities = sorted(({"entity_id": st.entity_id, "name": str(st.attributes.get("friendly_name") or "")}
                           for st in self.hass.states.async_all() if self._exposed(st.entity_id)), key=lambda e: e["entity_id"])
        return {"entities": entities, "services": services}

    async def push_catalog(self, force: bool = False) -> bool:
        """The catalog to the seat when it changed since the last push (or `force`). -> whether it was pushed."""
        cat = await self.catalog()
        digest = hashlib.sha1(json.dumps(cat, sort_keys=True, default=str).encode()).hexdigest()
        if digest == self._digest and not force:
            return False
        await self.client.push_catalog(cat)
        self._digest = digest
        self.last_catalog = cat
        return True

    # ---- one call ------------------------------------------------------------------------------------------------------------------------------

    async def execute(self, request) -> dict:
        """-> {ok: True, response} or {ok: False, error}. Never raises: whatever Home Assistant says is the answer."""
        try:
            return await self._execute(request)
        except Exception as e:                  # noqa: BLE001 (a service's own failure, a bad value: the model reads the reason)
            return {"ok": False, "error": str(e) or type(e).__name__}

    async def _execute(self, request) -> dict:
        if not isinstance(request, dict):
            return {"ok": False, "error": "the request is not an object"}
        if "read" in request:
            what = request["read"]
            if what == "catalog":
                await self.push_catalog(force=True)
                cat = self.last_catalog
                return {"ok": True, "response": {"entities": len(cat["entities"]), "services": len(cat["services"])}}
            if not isinstance(what, str) or not self._exposed(what):
                return {"ok": False, "error": f"{what} is not exposed to Assist: the person exposes an entity in Home Assistant (Settings, Voice assistants, Expose)"}
            st = self.hass.states.get(what)
            if st is None:
                return {"ok": False, "error": f"no such entity: {what}"}
            return {"ok": True, "response": {"state": st.state, "attributes": jsonable(dict(st.attributes)), "last_changed": st.last_changed.isoformat()}}
        domain, service = request.get("domain"), request.get("service")
        if not isinstance(domain, str) or not isinstance(service, str):
            return {"ok": False, "error": "a service call needs domain and service"}
        hidden = [e for e in entity_ids(request) if not self._exposed(e)]
        if hidden:
            return {"ok": False, "error": f"{', '.join(hidden)} is not exposed to Assist: the person exposes an entity in Home Assistant (Settings, Voice assistants, Expose)"}
        if not self.hass.services.has_service(domain, service):
            return {"ok": False, "error": f"Service {domain}.{service} not found"}
        wants = self.hass.services.supports_response(domain, service) != SupportsResponse.NONE
        user = await self.ensure_user()
        try:
            out = await self.hass.services.async_call(domain, service, request.get("data") or {}, blocking=True, target=request.get("target") or None,
                                                      return_response=wants, context=Context(user_id=user.id))
        except Unauthorized as e:
            return {"ok": False, "error": refusal(e, domain, service)}
        return {"ok": True, "response": jsonable(out) if wants else {"called": f"{domain}.{service}"}}

    # ---- the pull ------------------------------------------------------------------------------------------------------------------------------

    async def pump_once(self) -> int:
        """Ask for the pending calls, run each one the seat lets this component claim, answer it. -> how many were answered. A seat that is down or says the token is not the
        owner's is an empty pull (the health poll owns the reauth)."""
        try:
            pending = await self.client.calls()
            self.forbidden = False
        except SeatForbidden:
            self.forbidden = True
            return 0
        except SeatAuthError:
            return 0
        except SeatError as e:
            _LOGGER.debug("membrane: pending calls not read: %s", e)
            return 0
        done = 0
        for call in pending:
            try:
                if not await self.client.claim(call["id"]):
                    continue
                result = await self.execute(call.get("request"))
                await self.client.finish(call["id"], result)       # a seat that dies here leaves the call `running`: it may have run, so it is not offered again
                done += 1
            except SeatError as e:
                _LOGGER.warning("membrane: call %s: %s", call.get("id"), e)
        return done

    async def run(self) -> None:
        """The loop setup starts (a background task of the entry): pull every CALL_PULL_S, rebuild the catalog every CATALOG_S."""
        loop = asyncio.get_running_loop()
        last_catalog = float("-inf")
        while True:
            try:
                if loop.time() - last_catalog >= const.CATALOG_S:
                    last_catalog = loop.time()
                    try:
                        await self.push_catalog()
                    except SeatError as e:      # a seat that is down or not ours to write to: the pull below says so by itself
                        _LOGGER.debug("membrane: catalog not pushed: %s", e)
                await self.pump_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:              # noqa: BLE001 (the loop must outlive anything)
                _LOGGER.debug("membrane: house loop: %r", e)
            await asyncio.sleep(const.CALL_PULL_S)
