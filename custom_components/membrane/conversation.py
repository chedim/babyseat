"""The seat as Home Assistant's conversation agent (Assist): one seat session per HA conversation, `[HA user: X]` as the first part,
the seat's answer back as the speech. An error is spoken as one, with the seat's own cause."""
from __future__ import annotations

import logging
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import device_info, speaker_name
from .seat_client import SeatAuthError, SeatTurnError, SeatUnreachable

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([MembraneConversation(hass, entry)])


class MembraneConversation(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:brain"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = device_info(entry)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return "*"                  # the seat's model handles the language

    @property
    def available(self) -> bool:
        return self._entry.runtime_data.health.last_update_success

    async def _speaker(self, user_input) -> str:
        return await speaker_name(self.hass, getattr(getattr(user_input, "context", None), "user_id", None))

    async def _turn(self, conversation_id: str, text: str, speaker: str) -> str:
        return await self._entry.runtime_data.say(conversation_id, text, speaker)

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        try:
            speech = await self._turn(chat_log.conversation_id, user_input.text, await self._speaker(user_input)) \
                or "The seat returned no text."
        except SeatAuthError as e:
            self._entry.async_start_reauth(self.hass)
            speech = f"The seat refused Home Assistant's token ({e}). Open the Membrane integration and paste a new token."
        except SeatTurnError as e:
            speech = f"The seat could not answer: {e}"
        except SeatUnreachable as e:
            speech = f"The seat is not reachable: {e}"
        except Exception as e:                      # noqa: BLE001
            _LOGGER.warning("membrane: turn failed: %s", e)
            speech = f"The seat could not answer: {e}"
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=self.entity_id, content=speech))
        return conversation.async_get_result_from_chat_log(user_input, chat_log)
