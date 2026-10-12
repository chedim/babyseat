"""The seat as Home Assistant's conversation agent (Assist): ported from the boat's
membrane_ai; one seat session per HA conversation, "[HA user: X]" first."""
from __future__ import annotations

import logging
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import (
    AddConfigEntryEntitiesCallback)

from .const import DOMAIN

REQUEST_TIMEOUT_S = 600.0          # the turn loop may run minutes
SESSION_TITLE_PREFIX = "hass-"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
        hass: HomeAssistant,
        entry: ConfigEntry,
        async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    comp = hass.data[DOMAIN]
    comp.setdefault("sessions", {})
    comp["agent_http"] = async_get_clientsession(hass)
    async_add_entities([MembraneServeEntity(hass, entry)])


def speaker_line(name: str) -> str:
    """The same first line ai_terminal's proxy sends: who is talking."""
    clean = " ".join(str(name or "").split()).replace("]", ")")
    return f"[HA user: {clean}]"


def reply_text(result: dict) -> str:
    """The visible reply from a serve message result ({info, parts}):
    the text parts joined; tool parts surface nothing."""
    parts = (result or {}).get("parts") or []
    out = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text" and p.get("text"):
            out.append(p["text"])
    return "\n".join(out).strip()


class MembraneServeEntity(conversation.ConversationEntity):
    """One memory-full brain per config entry; a serve session per chat."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        comp = hass.data[DOMAIN]
        self._comp = comp
        self._attr_name = comp.get("name") or "Bob"   # the seat's own name (a rename shows after a reload)
        from .sensor import device_info
        self._attr_device_info = device_info(entry)
        self._attr_unique_id = entry.entry_id
        self._attr_icon = "mdi:brain"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"                  # the brain's model handles the language

    async def _session_for(self, conversation_id: str) -> str:
        """The serve session id for this hass conversation (lazy create
        on the brain; the mapping lives in RAM, rebuilt after a reload)."""
        comp = self._comp
        sid = comp["sessions"].get(conversation_id)
        if sid:
            return sid
        sess = comp["agent_http"]
        auth = aiohttp.BasicAuth(comp.get("serve_user") or "opencode", comp.get("serve_pass") or "")
        url = comp["serve_url"].rstrip("/") + "/session"
        async with sess.post(url, json={"title": SESSION_TITLE_PREFIX
                                        + conversation_id},
                             auth=auth,
                             timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"session create answered {resp.status}")
            data = await resp.json()
        sid = data["id"]
        comp["sessions"][conversation_id] = sid
        return sid

    async def _speaker(self, user_input) -> str:
        """The HA user behind this turn (none for e.g. a voice satellite
        without a logged-in user -- then no speaker line is sent)."""
        uid = getattr(getattr(user_input, "context", None), "user_id", None)
        if not uid:
            return ""
        user = await self.hass.auth.async_get_user(uid)
        return (user.name or "") if user else ""

    async def _async_handle_message(
            self, user_input: conversation.ConversationInput,
            chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """One turn: text in, the brain's own answer out (the serve's
        SYNC message lane runs the whole turn loop, tools included)."""
        try:
            comp = self._comp
            sid = await self._session_for(chat_log.conversation_id)
            sess = comp["agent_http"]
            auth = aiohttp.BasicAuth(comp.get("serve_user") or "opencode", comp.get("serve_pass") or "")
            url = comp["serve_url"].rstrip("/") + \
                f"/session/{sid}/message"
            parts = [{"type": "text", "text": user_input.text}]
            name = await self._speaker(user_input)
            if name:
                parts.insert(0, {"type": "text", "text": speaker_line(name),
                                 "synthetic": True})
            payload = {"agent": "build", "parts": parts}
            async with sess.post(url, json=payload, auth=auth,
                                 timeout=aiohttp.ClientTimeout(
                                     total=REQUEST_TIMEOUT_S)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"serve answered {resp.status}")
                data = await resp.json()
            speech = reply_text(data) or "The seat returned no text."
        except Exception as e:                      # noqa: BLE001
            _LOGGER.warning("membrane: turn failed: %s", e)
            # a dead session id (brain store reset) re-resolves next turn
            self._comp["sessions"].pop(chat_log.conversation_id, None)
            speech = "The seat isn't answering right now."
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=self.entity_id,
                                          content=speech))
        return conversation.async_get_result_from_chat_log(user_input,
                                                           chat_log)
