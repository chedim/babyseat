"""A fake Home Assistant for the component's tests (#1143): just the surface `custom_components/membrane` imports, so the config
flow, the migration, the setup and the Assist agent run here with no Home Assistant installed. It models what HA does around the
component (the flow's forms and entries, the update coordinator's errors, the reauth start, the conversation entity's hooks);
what the component does is the thing under test. Never a network call beyond what a test opens on 127.0.0.1.

`load(monkeypatch)` puts the fake modules in `sys.modules` for one test (and takes them out after) and returns the component
package freshly imported, so one test's module state never reaches another's.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class AbortFlow(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ConfigEntryAuthFailed(Exception):
    pass


class ConfigEntryNotReady(Exception):
    pass


class UpdateFailed(Exception):
    pass


class HomeAssistantError(Exception):
    pass


class ServiceNotFound(HomeAssistantError):
    pass


class Unauthorized(HomeAssistantError):
    """Home Assistant's own refusal of a context user: it carries no message (str() is empty), only what it was about."""
    def __init__(self, context=None, user_id=None, entity_id=None, config_entry_id=None, perm_category=None, permission=None):
        super().__init__()
        self.context, self.user_id, self.entity_id = context, user_id, entity_id
        self.config_entry_id, self.perm_category, self.permission = config_entry_id, perm_category, permission


class FakeUser:
    def __init__(self, uid, name, system_generated, group_ids, local_only):
        self.id, self.name, self.system_generated = uid, name, system_generated
        self.group_ids, self.local_only, self.is_admin = list(group_ids), local_only, "system-admin" in group_ids


class SupportsResponse:
    """HA's enum: NONE (no data back), OPTIONAL (the caller may ask for it), ONLY (the data is the point)."""
    NONE, OPTIONAL, ONLY = "none", "optional", "only"


class Context:
    def __init__(self, user_id=None):
        self.user_id = user_id


class ServiceCall:
    def __init__(self, domain, service, data, context=None, return_response=False):
        self.domain, self.service, self.data = domain, service, dict(data or {})
        self.context, self.return_response = context or Context(), return_response


class FakeState:
    def __init__(self, entity_id, state, attributes=None, last_changed=None):
        import datetime
        self.entity_id, self.state, self.attributes = entity_id, state, dict(attributes or {})
        self.last_changed = last_changed or datetime.datetime(2026, 11, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)


class FakeStates:
    def __init__(self):
        self._s = {}

    def set(self, entity_id, state, attributes=None):
        self._s[entity_id] = FakeState(entity_id, state, attributes)

    def get(self, entity_id):
        return self._s.get(entity_id)

    def async_all(self):
        return list(self._s.values())


class FakeServices:
    """The service registry: `register(domain, service, handler, supports)` for the services HA has; `calls` is what was called, in order."""

    def __init__(self):
        self._reg = {}
        self.calls = []
        self.contexts = []                        # the Context of each call that reached a handler, in order
        self.policy = None                        # fn(user_id, domain, service, data) -> raises Unauthorized to refuse, as HA's permission model does

    def register(self, domain, service, handler, supports=SupportsResponse.NONE):
        self._reg.setdefault(domain, {})[service] = (handler, supports)

    def async_register(self, domain, service, handler, schema=None, supports_response=SupportsResponse.NONE):
        self.register(domain, service, handler, supports_response)

    def async_remove(self, domain, service):
        self._reg.get(domain, {}).pop(service, None)

    def has_service(self, domain, service):
        return service in self._reg.get(domain, {})

    def supports_response(self, domain, service):
        if not self.has_service(domain, service):
            raise ServiceNotFound(f"Service {domain}.{service} not found")
        return self._reg[domain][service][1]

    def async_services(self):
        return {d: {s: object() for s in svcs} for d, svcs in self._reg.items()}

    async def async_call(self, domain, service, service_data=None, blocking=False, context=None, target=None, return_response=False):
        if not self.has_service(domain, service):
            raise ServiceNotFound(f"Service {domain}.{service} not found")
        handler, sup = self._reg[domain][service]
        if return_response and sup == SupportsResponse.NONE:
            raise HomeAssistantError(f"Service {domain}.{service} does not support responses")
        if not return_response and sup == SupportsResponse.ONLY:
            raise HomeAssistantError(f"Service {domain}.{service} requires a response")
        if self.policy is not None and context is not None and context.user_id:
            self.policy(context.user_id, domain, service, dict(service_data or {}, **(target or {})))
        self.contexts.append(context)
        data = dict(service_data or {})
        if target:
            data.update(target)
        self.calls.append({"domain": domain, "service": service, "data": dict(service_data or {}), "target": target, "blocking": blocking,
                           "return_response": return_response})
        res = handler(ServiceCall(domain, service, data, context, return_response))
        if hasattr(res, "__await__"):
            res = await res
        return res if return_response else None


class FakeRegistryEntry:
    def __init__(self, entity_id, unique_id, config_entry_id):
        self.entity_id, self.unique_id, self.config_entry_id = entity_id, unique_id, config_entry_id
        self.domain = entity_id.split(".", 1)[0]


class FakeEntityRegistry:
    def __init__(self):
        self.entries = {}

    def add(self, entity_id, unique_id, config_entry_id):
        self.entries[entity_id] = FakeRegistryEntry(entity_id, unique_id, config_entry_id)

    def async_remove(self, entity_id):
        self.entries.pop(entity_id, None)


class FakeConfigEntry:
    def __init__(self, data=None, options=None, version=1, minor_version=4, domain="membrane", entry_id="e1", unique_id="membrane"):
        self.data, self.options = dict(data or {}), dict(options or {})
        self.version, self.minor_version, self.domain = version, minor_version, domain
        self.entry_id, self.unique_id = entry_id, unique_id
        self.runtime_data = None
        self.reauth_started = 0
        self.unloaders = []
        self.tasks = []

    def async_create_background_task(self, hass, coro, name=None):
        import asyncio
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t

    def async_start_reauth(self, hass, context=None, data=None):
        self.reauth_started += 1

    def async_on_unload(self, fn):
        self.unloaders.append(fn)

    def add_update_listener(self, fn):
        return lambda: None


class FakeConfigEntries:
    def __init__(self):
        self.entries: list[FakeConfigEntry] = []
        self.forwarded: list = []
        self.updated: list = []

    def async_entries(self, domain=None):
        return [e for e in self.entries if domain in (None, e.domain)]

    def async_update_entry(self, entry, *, data=None, options=None, minor_version=None, version=None, unique_id=None, title=None):
        if data is not None:
            entry.data = dict(data)
        if options is not None:
            entry.options = dict(options)
        if minor_version is not None:
            entry.minor_version = minor_version
        if version is not None:
            entry.version = version
        self.updated.append(entry)
        return True

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append(list(platforms))

    async def async_unload_platforms(self, entry, platforms):
        return True

    async def async_reload(self, entry_id):
        self.reloaded = entry_id


class FakeHass:
    def __init__(self, session=None):
        self.data = {}
        self.config_entries = FakeConfigEntries()
        self.session = session
        self.users = {}
        self.states = FakeStates()
        self.services = FakeServices()
        self.registry = FakeEntityRegistry()
        self.exposed = set()                      # the entity ids exposed to Assist
        self.descriptions = {}                    # the services' descriptions, as async_get_all_descriptions answers
        self.created_users = []                   # every async_create_system_user call: (name, group_ids, local_only)
        self.auth = types.SimpleNamespace(async_get_user=self._get_user, async_get_users=self._get_users,
                                          async_create_system_user=self._create_system_user, async_remove_user=self._remove_user)
        self.tasks = []

    async def _get_user(self, uid):
        return self.users.get(uid)

    async def _get_users(self):
        return list(self.users.values())

    async def _create_system_user(self, name, *, group_ids=None, local_only=None):
        self.created_users.append((name, list(group_ids or []), local_only))
        u = FakeUser(f"sys-{len(self.created_users)}", name, True, group_ids or [], local_only)
        self.users[u.id] = u
        return u

    async def _remove_user(self, user):
        self.users.pop(user.id, None)

    def async_create_task(self, coro):
        import asyncio
        t = asyncio.ensure_future(coro)
        self.tasks.append(t)
        return t


class ConfigFlow:
    VERSION = 1
    MINOR_VERSION = 1

    def __init_subclass__(cls, domain=None, **kw):
        cls.domain = domain

    hass = None
    context: dict = {}
    unique_id = None

    async def async_set_unique_id(self, uid, *, raise_on_progress=True):
        self.unique_id = uid

    def _abort_if_unique_id_configured(self, updates=None, **kw):
        if any(e.unique_id == self.unique_id for e in self.hass.config_entries.async_entries(self.domain)):
            raise AbortFlow("already_configured")

    def async_show_form(self, *, step_id, data_schema=None, errors=None, description_placeholders=None, last_step=None):
        return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {},
                "description_placeholders": description_placeholders or {}}

    def async_create_entry(self, *, title, data, options=None, description=None):
        return {"type": "create_entry", "title": title, "data": dict(data), "options": options}

    def async_abort(self, *, reason, description_placeholders=None):
        return {"type": "abort", "reason": reason}

    def _get_reauth_entry(self):
        return self.context["entry"]

    def async_update_reload_and_abort(self, entry, *, data_updates=None, data=None, reason="reauth_successful", **kw):
        d = dict(entry.data if data is None else data)
        d.update(data_updates or {})
        self.hass.config_entries.async_update_entry(entry, data=d)
        return {"type": "abort", "reason": reason}


class DataUpdateCoordinator:
    def __init__(self, hass, logger, *, name, update_interval=None, config_entry=None, **kw):
        self.hass, self.name, self.update_interval, self.config_entry = hass, name, update_interval, config_entry
        self.data = None
        self.last_update_success = True

    def __class_getitem__(cls, item):
        return cls

    async def async_config_entry_first_refresh(self):
        try:
            self.data = await self._async_update_data()
        except ConfigEntryAuthFailed:
            raise
        except UpdateFailed as e:
            raise ConfigEntryNotReady(str(e)) from e

    async def async_request_refresh(self):
        await self.async_refresh()

    async def async_refresh(self):
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except ConfigEntryAuthFailed:
            self.last_update_success = False
            self.config_entry.async_start_reauth(self.hass)
        except UpdateFailed:
            self.last_update_success = False


class ConversationEntity:
    hass = None
    entity_id = "conversation.membrane"


class AssistantContent:
    def __init__(self, agent_id, content):
        self.agent_id, self.content = agent_id, content


class ChatLog:
    def __init__(self, conversation_id="c1"):
        self.conversation_id = conversation_id
        self.content = []

    def async_add_assistant_content_without_tools(self, c):
        self.content.append(c)


class ConversationInput:
    def __init__(self, text, user_id=None, conversation_id="c1", language="en"):
        self.text, self.language, self.conversation_id = text, language, conversation_id
        self.context = types.SimpleNamespace(user_id=user_id)


def async_get_result_from_chat_log(user_input, chat_log):
    """The result HA would build: the last assistant content is the speech."""
    last = chat_log.content[-1].content if chat_log.content else ""
    return {"conversation_id": chat_log.conversation_id, "speech": last}


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def load(monkeypatch, session=None):
    """-> (the component package, a fake hass). `session`: what `async_get_clientsession` returns (a test's real aiohttp session)."""
    import aiohttp
    ha = _mod("homeassistant"); ha.__path__ = []
    comps = _mod("homeassistant.components"); comps.__path__ = []
    helpers = _mod("homeassistant.helpers"); helpers.__path__ = []
    hass = FakeHass(session)
    mods = {
        "homeassistant": ha,
        "homeassistant.config_entries": _mod("homeassistant.config_entries", ConfigFlow=ConfigFlow, ConfigEntry=FakeConfigEntry,
                                             ConfigFlowResult=dict),
        "homeassistant.core": _mod("homeassistant.core", HomeAssistant=FakeHass, callback=lambda f: f, ServiceCall=ServiceCall, Context=Context,
                                   SupportsResponse=SupportsResponse, ServiceResponse=dict),
        "homeassistant.const": _mod("homeassistant.const", Platform=types.SimpleNamespace(
            CONVERSATION="conversation", SENSOR="sensor", BINARY_SENSOR="binary_sensor", SELECT="select")),
        "homeassistant.exceptions": _mod("homeassistant.exceptions", ConfigEntryAuthFailed=ConfigEntryAuthFailed,
                                         ConfigEntryNotReady=ConfigEntryNotReady, HomeAssistantError=HomeAssistantError,
                                         ServiceNotFound=ServiceNotFound, Unauthorized=Unauthorized),
        "homeassistant.auth": _mod("homeassistant.auth"),
        "homeassistant.auth.const": _mod("homeassistant.auth.const", GROUP_ID_USER="system-users", GROUP_ID_ADMIN="system-admin"),
        "homeassistant.components.sensor": _mod("homeassistant.components.sensor", SensorEntity=_Entity,
                                                SensorDeviceClass=types.SimpleNamespace(TIMESTAMP="timestamp")),
        "homeassistant.components.binary_sensor": _mod("homeassistant.components.binary_sensor", BinarySensorEntity=_Entity,
                                                       BinarySensorDeviceClass=types.SimpleNamespace(RUNNING="running", CONNECTIVITY="connectivity")),
        "homeassistant.components.select": _mod("homeassistant.components.select", SelectEntity=_Entity),
        "homeassistant.components.homeassistant": _mod("homeassistant.components.homeassistant"),
        "homeassistant.components.homeassistant.exposed_entities": _mod(
            "homeassistant.components.homeassistant.exposed_entities",
            async_should_expose=lambda h, assistant, entity_id: entity_id in h.exposed),
        "homeassistant.helpers.entity_registry": _mod(
            "homeassistant.helpers.entity_registry", async_get=lambda h: h.registry,
            async_entries_for_config_entry=lambda reg, entry_id: [e for e in reg.entries.values() if e.config_entry_id == entry_id]),
        "homeassistant.helpers.service": _mod("homeassistant.helpers.service", async_get_all_descriptions=_descriptions),
        "homeassistant.components": comps,
        "homeassistant.components.conversation": _mod(
            "homeassistant.components.conversation", ConversationEntity=ConversationEntity, ConversationInput=ConversationInput,
            ConversationResult=dict, ChatLog=ChatLog, AssistantContent=AssistantContent,
            async_get_result_from_chat_log=async_get_result_from_chat_log),
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.aiohttp_client": _mod("homeassistant.helpers.aiohttp_client",
                                                     async_get_clientsession=lambda h: h.session),
        "homeassistant.helpers.update_coordinator": _mod("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator,
                                                         UpdateFailed=UpdateFailed, CoordinatorEntity=_CoordinatorEntity),
        "homeassistant.helpers.entity_platform": _mod("homeassistant.helpers.entity_platform", AddConfigEntryEntitiesCallback=object),
    }
    comps.conversation = mods["homeassistant.components.conversation"]
    helpers.entity_registry = mods["homeassistant.helpers.entity_registry"]
    try:
        import voluptuous  # noqa: F401
    except ImportError:
        mods["voluptuous"] = _mod("voluptuous", Schema=_Schema, Required=_Marker, Optional=_Marker)
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    for k in [k for k in sys.modules if k == "cc_membrane" or k.startswith("cc_membrane.")]:
        monkeypatch.delitem(sys.modules, k)
    import importlib.util
    d = ROOT / "custom_components" / "membrane"
    spec = importlib.util.spec_from_file_location("cc_membrane", d / "__init__.py", submodule_search_locations=[str(d)])
    pkg = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "cc_membrane", pkg)
    spec.loader.exec_module(pkg)
    return pkg, hass


class _Entity:
    """What SensorEntity / BinarySensorEntity / SelectEntity share: attributes the component sets, written state counted."""
    hass = None
    entity_id = None
    writes = 0

    def async_write_ha_state(self):
        self.writes += 1


class _CoordinatorEntity(_Entity):
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


async def _descriptions(hass):
    return hass.descriptions


class _Marker:
    def __init__(self, schema, default=None):
        self.schema, self.default = schema, default


class _Schema:
    def __init__(self, schema):
        self.schema = schema

    def fields(self):
        return {getattr(k, "schema", k): v for k, v in self.schema.items()}
