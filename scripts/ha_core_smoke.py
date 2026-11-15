"""The Home Assistant component against a REAL, throwaway Home Assistant Core (#1143): what tests/ha/fakeha.py models, run in the real thing.

NEEDS (nothing here installs anything; it is a manual run, not a CI job):
  - a venv with Home Assistant Core (`python3.14 -m venv V && V/bin/pip install homeassistant`); run THIS script with that venv's python.
    Home Assistant installs the pip requirements of the components it sets up (the frontend, conversation's intents) into the same venv
    on its first start.
  - a cliffvm binary (`cd native && cargo build --release --no-default-features --features bin`, or any built one): --cliffvm or $CLIFFVM.

  V/bin/python scripts/ha_core_smoke.py --cliffvm native/target/release/cliffvm [--work DIR] [--ha-port 8133] [--keep] [--skip-rest] [--skip-discovery] [--skip-inside]

What it does, all on 127.0.0.1, all removed at the end (the work folder too, unless --keep):
  1. a private seat: the release launcher (scripts/seat: strict door, its own data folder, a free port), a FAKE claude that answers
     "Done: <message>" (tests/ha/realseat.py's FAKE_CLAUDE): never a real model;
  2. a real `hass` (its own config folder, http on --ha-port, the component copied into custom_components/), onboarded over REST;
  3. the manual config flow over REST, the entities, the hidden user, Assist, `membrane.ask`, a refused token -> reauth -> repaired, the
     entry's removal (the hidden user goes with it); every WARNING/ERROR line of the log that names the component is printed;
  4. the add-on's Supervisor discovery (`async_step_hassio`) through Home Assistant's own flow manager, in-process (Core cannot get a real
     Supervisor announcement): confirm form, entry, a second announcement with a new token, the Ollama hint with an `ollama` entry present;
  5. RUN A SEAT INSIDE HOME ASSISTANT (the default path, seat_runner.py) in another real `hass`: a release tarball built here with
     scripts/dist/build.sh from --cliffvm (this host's target), a FAKE OpenAI-compatible endpoint on loopback (it streams "Done: <message>" and
     records each request), a fake 0.7 store in <config>/membrane/.membrane (the history cutover); the menu -> inside -> openai flow over REST,
     the seat started by the component, Assist through it to the endpoint, the options flow (the seat restarts), a reload, Home Assistant's stop,
     then a start that DOWNLOADS the tarball from a local release server (the entry's `release_base`, written into .storage while hass is down:
     the flow has no field for it) and checks it, and the removal: after each, the seat process that was running is gone.
Exit status 0 when every check passed; each check prints PASS/FAIL with its evidence.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "custom_components" / "membrane"
CLIENT_ID = "http://127.0.0.1/"          # an IndieAuth client id HA accepts for loopback
RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, evidence="") -> bool:
    RESULTS.append((bool(ok), name, str(evidence)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {evidence}" if evidence else ""), flush=True)
    return bool(ok)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def fake_claude_source() -> str:
    """FAKE_CLAUDE of tests/ha/realseat.py, read without importing it (it imports pytest)."""
    tree = ast.parse((ROOT / "tests" / "ha" / "realseat.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "FAKE_CLAUDE" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("tests/ha/realseat.py has no FAKE_CLAUDE")


# ---- the seat ----------------------------------------------------------------------------------------------------------------------------------

class Seat:
    def __init__(self, work: Path, cliffvm: str):
        self.dir = work / "seat"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.claude = self.dir / "fake-claude"
        self.claude.write_text(fake_claude_source())
        self.claude.chmod(0o755)
        self.cliffvm = cliffvm
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.child = None

    def start(self) -> "Seat":
        env = dict(os.environ, ANTHROPIC_API_KEY="sk-ant-harness-default-0000", CLIFFVM=self.cliffvm, CLAUDE_BIN=str(self.claude))
        log = open(self.dir / "seat.log", "a")
        # the facts of tests/ha/realseat.py: the seat thinks through the fake claude ONLY; its model backend is a dead port, so no model server on this machine
        # (a local Ollama, the launcher's default) is listed or called, whatever the model select picks
        facts = ["--fact", "seat_backend=http://127.0.0.1:1", "--fact", "router_on=false",
                 "--fact", 'cloud_models=[{"name":"claude","backend":"claude-agent"},{"name":"claude-agent:sonnet","backend":"claude-agent"}]',
                 "--fact", 'minds={"loops":"claude","cortex":"claude","workers":"claude","seat_default":"claude"}']
        self.child = subprocess.Popen(["sh", str(ROOT / "scripts" / "seat"), "--data-dir", str(self.dir / "data"), "--port", str(self.port), *facts],
                                      cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        t = time.time()
        while not (self.token() and self.listening()):           # the token file comes before the door listens
            if self.child.poll() is not None or time.time() - t > 60:
                raise SystemExit(f"the seat did not start: {(self.dir / 'seat.log').read_text()[-2000:]}")
            time.sleep(0.2)
        return self

    def listening(self) -> bool:
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
            return True
        except OSError:
            return False

    def token(self) -> str:
        try:
            return (self.dir / "data" / "admin.token").read_text().strip()
        except OSError:
            return ""

    def stop(self) -> None:
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(15)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
        self.child = None

    def new_key(self) -> str:
        """The seat restarted with a NEW door secret (its key changed): every old token is refused. -> the new admin token."""
        self.stop()
        for n in ("door.secret", "admin.token"):
            (self.dir / "data" / n).unlink(missing_ok=True)
        self.start()
        return self.token()


# ---- the real hass -----------------------------------------------------------------------------------------------------------------------------

CONFIG = """homeassistant:
  name: throwaway
  latitude: 0
  longitude: 0
  elevation: 0
  unit_system: metric
  time_zone: UTC
http:
  server_host: 127.0.0.1
  server_port: {port}
api:
auth:
onboarding:
config:
conversation:
repairs:
logger:
  default: info
  logs:
    custom_components.membrane: debug
"""


def config_dir(path: Path, port: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "configuration.yaml").write_text(CONFIG.format(port=port))
    dst = path / "custom_components" / "membrane"
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(CC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return path


class Hass:
    def __init__(self, cfg: Path, port: int):
        self.cfg, self.port = cfg, port
        self.url = f"http://127.0.0.1:{port}"
        self.child = None
        self.token = ""

    @property
    def log(self) -> Path:
        return self.cfg / "hass.log"

    async def start(self, s: aiohttp.ClientSession) -> None:
        out = open(self.cfg / "hass.stdout", "a")
        self.child = subprocess.Popen([sys.executable, "-m", "homeassistant", "-c", str(self.cfg), "--log-file", str(self.log)],
                                      cwd=self.cfg, stdout=out, stderr=subprocess.STDOUT)     # cwd: the copied component, not this checkout's
        t = time.time()
        while True:
            if self.child.poll() is not None:
                raise SystemExit(f"hass exited {self.child.returncode}: {self.log.read_text()[-3000:] if self.log.exists() else ''}")
            try:
                async with s.get(self.url + "/api/onboarding") as r:
                    if r.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            if time.time() - t > 900:
                raise SystemExit("hass did not answer in 15 minutes")
            await asyncio.sleep(2)

    async def onboard(self, s: aiohttp.ClientSession) -> None:
        async with s.post(self.url + "/api/onboarding/users", json={"name": "Smoke Owner", "username": "owner", "password": "throwaway-pass-1",
                                                                     "client_id": CLIENT_ID, "language": "en"}) as r:
            body = await r.json()
            assert r.status == 200, (r.status, body)
        async with s.post(self.url + "/auth/token", data={"grant_type": "authorization_code", "code": body["auth_code"], "client_id": CLIENT_ID}) as r:
            tok = await r.json()
            assert r.status == 200, (r.status, tok)
        self.token = tok["access_token"]

    def h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def req(self, s, method, path, body=None):
        async with s.request(method, self.url + path, json=body, headers=self.h()) as r:
            text = await r.text()
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, text

    async def ws(self, s, msg: dict):
        """One command on the websocket API (what the frontend uses for users, repairs)."""
        async with s.ws_connect(self.url + "/api/websocket") as w:
            await w.receive_json()
            await w.send_json({"type": "auth", "access_token": self.token})
            assert (await w.receive_json())["type"] == "auth_ok"
            await w.send_json({"id": 1, **msg})
            return await w.receive_json()

    def stop(self) -> None:
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(60)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()

    EXPECTED = ("We found a custom integration membrane which has not been tested",     # HA's notice for every custom integration
                "could not authenticate: the seat refused the token",                   # HA's own lines for the reauths this run provokes
                "could not authenticate: no seat address or token")

    def component_lines(self) -> list[str]:
        """The log's WARNING/ERROR lines (and the traceback after each) that name the component, but the EXPECTED ones."""
        text = "\n".join(p.read_text(errors="replace") for p in (self.log.with_name(self.log.name + ".1"), self.log) if p.exists())   # a restart rolls hass.log over
        out, keep = [], False
        for line in text.splitlines():
            head = line[:40].strip()[:4].isdigit()
            if head:
                keep = ((" WARNING " in line or " ERROR " in line or " CRITICAL " in line) and "membrane" in re.sub(r"/[^\s'\"]*", "", line)   # not in a path
                        and not any(x in line for x in self.EXPECTED))
            if keep:
                out.append(line)
        return out


async def wait_for(fn, timeout=30.0, every=0.5):
    t = time.time()
    while True:
        got = await fn()
        if got or time.time() - t > timeout:
            return got
        await asyncio.sleep(every)


async def drive_rest(hass: Hass, seat: Seat, s: aiohttp.ClientSession) -> None:
    # (a) the manual config flow
    st, menu = await hass.req(s, "POST", "/api/config/config_entries/flow", {"handler": "membrane", "show_advanced_options": False})
    check(st == 200 and menu.get("type") == "menu" and menu.get("menu_options") == ["inside", "connect"], "the first step is the menu (inside first, then connect)",
          f"{st} {json.dumps(menu)[:300]}")
    st, form = await hass.req(s, "POST", f"/api/config/config_entries/flow/{menu['flow_id']}", {"next_step_id": "connect"})
    check(st == 200 and form.get("step_id") == "connect" and [f["name"] for f in form.get("data_schema", [])] == ["serve_url", "serve_pass"],
          "connect shows the form (serve_url, serve_pass)", f"{st} {json.dumps(form)[:400]}")
    st, bad = await hass.req(s, "POST", f"/api/config/config_entries/flow/{form['flow_id']}", {"serve_url": seat.url, "serve_pass": "not-a-token"})
    check(st == 200 and bad.get("errors") == {"base": "invalid_auth"}, "a wrong token is invalid_auth on the form", f"{st} {bad.get('errors')}")
    st, done = await hass.req(s, "POST", f"/api/config/config_entries/flow/{form['flow_id']}", {"serve_url": seat.url + "/", "serve_pass": seat.token()})
    check(st == 200 and done.get("type") == "create_entry", "the seat's admin token makes the entry", f"{st} {json.dumps(done)[:400]}")
    entry_id = (done.get("result") or {}).get("entry_id")

    async def loaded():
        _, es = await hass.req(s, "GET", "/api/config/config_entries/entry?domain=membrane")
        return es if es and es[0].get("state") == "loaded" else None
    es = await wait_for(loaded)
    check(bool(es), "the entry is loaded", json.dumps(es)[:300])
    st, again = await hass.req(s, "POST", "/api/config/config_entries/flow", {"handler": "membrane"})
    check(again.get("type") == "abort" and again.get("reason") == "already_configured", "a second manual flow aborts: one entry per Home Assistant",
          json.dumps(again)[:200])

    _, states = await hass.req(s, "GET", "/api/states")
    ours = {x["entity_id"]: x["state"] for x in states if x["entity_id"].startswith(("conversation.", "sensor.", "binary_sensor.", "select."))}
    conv = next((e for e in ours if e.startswith("conversation.") and "membrane" in e), None)
    check(conv is not None and any(e.startswith("binary_sensor.") for e in ours) and any(e.startswith("select.") for e in ours),
          "the entities are in /api/states (conversation agent, reachable, model select)", json.dumps(ours))
    reach = next((e for e in ours if e.startswith("binary_sensor.") and "reachable" in e), None)
    check(reach is not None and ours.get(reach) == "on", "the seat is reachable", f"{reach}={ours.get(reach)}")

    sel = next((e for e in ours if e.startswith("select.")), "")
    _, sst = await hass.req(s, "GET", f"/api/states/{sel}")
    opts = (sst.get("attributes") or {}).get("options") or []
    pick = next((o for o in opts if o != sst.get("state") and o.startswith("claude")), None)    # only the fake claude's models
    if pick:
        st, _ = await hass.req(s, "POST", "/api/services/select/select_option", {"entity_id": sel, "option": pick})

        async def picked():
            _, now = await hass.req(s, "GET", f"/api/states/{sel}")
            return now.get("state") == pick
        check(st == 200 and await wait_for(picked, 10), "the model select writes the seat's model", f"{sel}: {sst.get('state')} -> {pick} ({opts})")
    else:
        check(False, "the model select lists the seat's models", json.dumps(sst)[:300])

    print("      the component's states: " + json.dumps({x["entity_id"]: [x["state"], x["attributes"].get("friendly_name")] for x in states
                                                        if "membrane" in x["entity_id"]}), flush=True)
    _, svcs = await hass.req(s, "GET", "/api/services")
    ask = next((d["services"].get("ask") for d in svcs if d["domain"] == "membrane"), None) or {}
    check(ask.get("name") == "Ask the seat" and sorted(ask.get("fields") or {}) == ["session", "text"] and (ask.get("response") or {}).get("optional") is False,
          "membrane.ask is described (services.yaml + strings: name, fields, response only)", json.dumps(ask)[:300])
    for cat, want in (("config", ["step.user.menu_options.inside", "step.connect_ollama.description", "step.reauth_confirm.description", "step.reauth_no_seat.description",
                                  "step.inside.data.provider", "step.anthropic.data.anthropic_api_key", "step.reauth_inside.description", "abort.unsupported_platform",
                                  "step.hassio_confirm.description", "step.hassio_confirm_ollama.description", "error.invalid_auth", "abort.invalid_discovery"]),
                      ("services", ["ask.name", "send_message.fields.text.name"])):
        tr = ((await hass.ws(s, {"type": "frontend/get_translations", "language": "en", "category": cat, "integration": ["membrane"]})).get("result") or {}).get("resources") or {}
        miss = [k for k in want if f"component.membrane.{cat}.{k}" not in tr]
        check(not miss, f"the frontend gets the {cat} translations", f"missing {miss} of {len(tr)} keys")

    async with s.get(hass.url + "/api/brands/integration/membrane/icon.png", headers=hass.h()) as r:
        png = await r.read()
    check(r.status == 200 and png == (CC / "brand" / "icon.png").read_bytes(), "Home Assistant serves the component's own brand icon (brand/icon.png)",
          f"{r.status} {len(png)} bytes")

    users = (await hass.ws(s, {"type": "config/auth/list"})).get("result") or []
    hidden = [u for u in users if u.get("name") == "Membrane"]
    check(len(hidden) == 1 and hidden[0].get("system_generated") and not hidden[0].get("is_owner") and "system-users" in hidden[0].get("group_ids", [])
          and hidden[0].get("local_only"), "the hidden Membrane user: system, group users, local only", json.dumps(hidden))

    # (b) Assist
    st, said = await hass.req(s, "POST", "/api/conversation/process", {"text": "turn the porch light on", "agent_id": conv, "language": "en"})
    speech = (((said or {}).get("response") or {}).get("speech") or {}).get("plain", {}).get("speech", "") if isinstance(said, dict) else ""
    check(st == 200 and "Done:" in speech and "turn the porch light on" in speech, "Assist answers with the seat's (fake claude's) reply",
          f"{st} {speech!r}")
    check("[HA user: Smoke Owner]" in speech, "the speaker line reaches the seat (the fake claude echoes the last text block)", repr(speech))

    # (c) the service
    st, asked = await hass.req(s, "POST", "/api/services/membrane/ask?return_response", {"text": "what is on?", "session": "smoke"})
    ans = ((asked or {}).get("service_response") or {}) if isinstance(asked, dict) else {}
    check(st == 200 and "Done:" in str(ans.get("answer")) and ans.get("session") == "smoke", "membrane.ask returns the seat's answer",
          f"{st} {json.dumps(asked)[:300]}")
    st, nores = await hass.req(s, "POST", "/api/services/membrane/ask", {"text": "no response asked"})
    check(st == 400, "membrane.ask without return_response is refused by HA (SupportsResponse.ONLY)", f"{st} {str(nores)[:200]}")
    st, sent = await hass.req(s, "POST", "/api/services/membrane/send_message", {"text": "the door is open", "session": "smoke"})
    check(st == 200, "membrane.send_message is accepted", f"{st} {str(sent)[:200]}")

    # (d) the seat's key changes: the token is refused -> reauth; the new token repairs it
    new_token = await asyncio.to_thread(seat.new_key)
    st, _ = await hass.req(s, "POST", f"/api/config/config_entries/entry/{entry_id}/reload")

    async def reauth_flow():
        r = await hass.ws(s, {"type": "config_entries/flow/progress"})
        return [f for f in r.get("result") or [] if f.get("handler") == "membrane" and (f.get("context") or {}).get("source") == "reauth"]
    flows = await wait_for(reauth_flow, 30)
    _, es = await hass.req(s, "GET", "/api/config/config_entries/entry?domain=membrane")
    check(bool(flows) and es[0]["state"] == "setup_error", "a refused token: the entry is in setup_error and a reauth flow is in progress",
          f"state={es[0]['state']} reason={es[0].get('reason')} flows={json.dumps(flows)[:300]}")
    issues = (await hass.ws(s, {"type": "repairs/list_issues"})).get("result", {}).get("issues", [])
    check(any(i.get("domain") == "homeassistant" and "config_entry_reauth" in i.get("issue_id", "") for i in issues),
          "the reauth shows in Repairs (homeassistant's config_entry_reauth issue)", json.dumps([i.get("issue_id") for i in issues]))
    if flows:
        fid = flows[0]["flow_id"]
        st, rform = await hass.req(s, "GET", f"/api/config/config_entries/flow/{fid}")
        check(rform.get("step_id") == "reauth_confirm" and rform.get("description_placeholders", {}).get("url") == seat.url,
              "the reauth form names the seat", json.dumps(rform)[:400])
        st, still = await hass.req(s, "POST", f"/api/config/config_entries/flow/{fid}", {"serve_pass": "still-wrong"})
        check(still.get("errors") == {"base": "invalid_auth"}, "a still-wrong token stays on the form", json.dumps(still)[:200])
        st, fixed = await hass.req(s, "POST", f"/api/config/config_entries/flow/{fid}", {"serve_pass": new_token})
        check(fixed.get("type") == "abort" and fixed.get("reason") == "reauth_successful", "the new token: reauth_successful", json.dumps(fixed)[:200])
        es = await wait_for(loaded)
        check(bool(es), "the entry is loaded again", json.dumps(es)[:200])
        st, said = await hass.req(s, "POST", "/api/conversation/process", {"text": "back again", "agent_id": conv})
        speech = (((said or {}).get("response") or {}).get("speech") or {}).get("plain", {}).get("speech", "")
        check("Done:" in speech, "Assist answers after the repair", repr(speech))

    # Home Assistant restarts: the entry comes back with its token, and finds its hidden user again (by the id the entry keeps)
    await asyncio.to_thread(hass.stop)
    await hass.start(s)
    es = await wait_for(loaded, 60)
    users = (await hass.ws(s, {"type": "config/auth/list"})).get("result") or []
    check(bool(es) and len([u for u in users if u.get("name") == "Membrane"]) == 1, "after a restart the entry loads and there is still one hidden user",
          f"{json.dumps(es)[:200]} users={[u.get('name') for u in users]}")

    # the unload and the removal
    st, rel =await hass.req(s, "POST", f"/api/config/config_entries/entry/{entry_id}/reload")
    check(st == 200 and (rel or {}).get("require_restart") is False, "a reload unloads and sets up cleanly", f"{st} {rel}")
    st, gone = await hass.req(s, "DELETE", f"/api/config/config_entries/entry/{entry_id}")
    check(st == 200 and gone.get("require_restart") is False, "the entry is removed", f"{st} {gone}")
    users = (await hass.ws(s, {"type": "config/auth/list"})).get("result") or []
    check(not [u for u in users if u.get("name") == "Membrane"], "the hidden user went with the entry", json.dumps([u.get("name") for u in users]))
    _, svcs = await hass.req(s, "GET", "/api/services")
    check("membrane" not in {d["domain"] for d in svcs}, "the services went with the entry", "")


# ---- the Supervisor discovery, in-process ------------------------------------------------------------------------------------------------------

async def drive_discovery(cfg: Path, seat: Seat) -> None:
    from homeassistant import bootstrap, config_entries
    from homeassistant.helpers.service_info.hassio import HassioServiceInfo
    from homeassistant.runner import RuntimeConfig

    (cfg / "configuration.yaml").write_text(CONFIG.replace("http:\n  server_host: 127.0.0.1\n  server_port: {port}\n", "")
                                            .replace("onboarding:\n", "").replace("api:\n", ""))
    hass = await bootstrap.async_setup_hass(RuntimeConfig(config_dir=str(cfg), log_file=str(cfg / "hass.log")))
    check(hass is not None, "Home Assistant bootstraps in-process")
    await hass.async_start()
    try:
        mgr = hass.config_entries.flow
        token = await asyncio.to_thread(seat.token)                     # no file read in Home Assistant's loop (HA flags it)

        def info(token, port=seat.port):
            return HassioServiceInfo(config={"host": "127.0.0.1", "port": port, "token": token}, name="babyseat", slug="local_babyseat", uuid="u-smoke")
        res = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(token))
        check(res.get("type") == "form" and res.get("step_id") == "hassio_confirm" and res.get("description_placeholders") == {"addon": "babyseat"},
              "discovery: the confirm form", str(res)[:300])
        bad = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info("wrong-token"))
        check(bad.get("type") == "abort" and bad.get("reason") in ("already_in_progress", "invalid_auth"),
              "discovery: a second announcement while one is open", str(bad)[:200])
        done = await mgr.async_configure(res["flow_id"], {})
        check(done.get("type") == "create_entry" and done["data"]["serve_pass"] == token and done["data"]["serve_url"] == seat.url,
              "discovery: confirm makes the entry", str(done)[:300])
        await hass.async_block_till_done()
        entry = hass.config_entries.async_entries("membrane")[0]
        check(entry.state is config_entries.ConfigEntryState.LOADED and entry.unique_id == f"127.0.0.1:{seat.port}",
              "discovery: the entry is loaded, keyed by host:port", f"{entry.state} {entry.unique_id}")

        new_token = await asyncio.to_thread(seat.new_key)
        again = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(new_token))
        await hass.async_block_till_done()
        entry = hass.config_entries.async_entries("membrane")[0]
        check(again.get("type") == "abort" and again.get("reason") == "already_configured" and entry.data["serve_pass"] == new_token
              and entry.state is config_entries.ConfigEntryState.LOADED and len(hass.config_entries.async_entries("membrane")) == 1,
              "discovery: a new token is stored and the entry reloaded (no duplicate)", f"{again} {entry.state}")
        same = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(new_token))
        check(same.get("reason") == "already_configured", "discovery: the same announcement again is quiet", str(same))

        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        ollama = config_entries.ConfigEntry(domain="ollama", title="Ollama", data={"url": "http://ollama.invalid:11434"}, options={}, source="user",
                                            version=3, minor_version=1, unique_id=None, discovery_keys={}, subentries_data=[
                                                {"data": {"model": "qwen3:4b"}, "subentry_type": "conversation", "title": "Ollama Conversation",
                                                 "unique_id": None}])
        hass.config_entries._entries[ollama.entry_id] = ollama          # present, never set up: the hint only reads it (MockConfigEntry.add_to_hass)
        res = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(new_token))
        ph = res.get("description_placeholders") or {}
        check(res.get("step_id") == "hassio_confirm_ollama" and ph.get("ollama_url") == "http://ollama.invalid:11434" and ph.get("ollama_model") == "qwen3:4b",
              "discovery: the Ollama hint is in the confirm form", str(res)[:300])
        done = await mgr.async_configure(res["flow_id"], {})
        check(done.get("type") == "create_entry", "discovery: the confirm with the Ollama hint makes the entry", str(done)[:200])
        await hass.async_block_till_done()

        from homeassistant.helpers.translation import async_get_translations
        tr = await async_get_translations(hass, "en", "config", ["membrane"])
        keys = [k for k in ("step.hassio_confirm_ollama.description", "step.connect_ollama.description", "abort.invalid_discovery")
                if f"component.membrane.config.{k}" not in tr]
        check(not keys, "the translations HA loads name every step and abort used", f"missing: {keys}")

        def reauths():
            return mgr.async_progress_by_handler("membrane", match_context={"source": config_entries.SOURCE_REAUTH})

        # a refused token, its reauth open in Repairs, then the add-on announces the new token
        entry = hass.config_entries.async_entries("membrane")[0]
        newer = await asyncio.to_thread(seat.new_key)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        check(entry.state is config_entries.ConfigEntryState.SETUP_ERROR and len(reauths()) == 1,
              "discovery: a refused token opens a reauth", f"{entry.state} {len(reauths())}")
        again = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(newer))
        await hass.async_block_till_done()
        check(entry.state is config_entries.ConfigEntryState.LOADED and entry.data["serve_pass"] == newer,
              "discovery: the announcement repairs the refused entry", f"{again.get('reason')} {entry.state}")
        check(not reauths(), "discovery: the open reauth is closed once the announcement repaired the entry",
              f"reauth flows still open: {[(f['flow_id'], f['step_id']) for f in reauths()]}")
        for f in reauths():
            mgr.async_abort(f["flow_id"])
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        # an ignored announcement stays ignored; the manual form still works
        uid = f"127.0.0.1:{seat.port}"
        ign = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_IGNORE}, data={"unique_id": uid, "title": "babyseat"})
        await hass.async_block_till_done()
        later = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(newer))
        user = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_USER})
        if user.get("type") == "menu":
            user = await mgr.async_configure(user["flow_id"], {"next_step_id": "connect"})
        check(ign.get("type") == "create_entry" and later.get("type") == "abort" and later.get("reason") == "already_configured"
              and user.get("type") == "form", "discovery: an ignored seat stays ignored, the manual form is still offered",
              f"ignore={ign.get('type')} later={later.get('reason')} user={user.get('type')}/{user.get('reason')}")
        mgr.async_abort(user["flow_id"])
        for e in hass.config_entries.async_entries("membrane", include_ignore=True):
            await hass.config_entries.async_remove(e.entry_id)
        await hass.async_block_till_done()

        # an entry of the old component that ran the seat inside Home Assistant but names no Ollama URL (1.2, a stale sensor): nothing to run, so
        # migrated with no address, reauth_no_seat, then the add-on fills it (one WITH its Ollama URL runs its seat again: the inside run, 5)
        from homeassistant.helpers import entity_registry as er
        old = config_entries.ConfigEntry(domain="membrane", title="Membrane", data={"mode": "inside"}, source="user", version=1, minor_version=2,
                                         unique_id="membrane", discovery_keys={}, subentries_data=[],
                                         options={"seats": [{"id": "s1", "inside": True}], "active": "s1"})
        hass.config_entries._entries[old.entry_id] = old
        reg = er.async_get(hass)
        stale = reg.async_get_or_create("sensor", "membrane", "old_turns", config_entry=old)
        await hass.config_entries.async_setup(old.entry_id)
        await hass.async_block_till_done()
        flows = reauths()
        check(old.minor_version == 5 and old.data.get("serve_url") == "" and reg.async_get(stale.entity_id) is None,
              "migration: a 1.2 inside entry with no Ollama URL is 1.5 with no address, its old sensor gone", f"{old.minor_version} {dict(old.data)} {stale.entity_id}")
        check(old.state is config_entries.ConfigEntryState.SETUP_ERROR and len(flows) == 1 and flows[0]["step_id"] == "reauth_no_seat",
              "migration: the inside entry asks for a seat (reauth_no_seat)", f"{old.state} {[(f['step_id']) for f in flows]}")
        again = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(newer))
        await hass.async_block_till_done()
        check(old.state is config_entries.ConfigEntryState.LOADED and old.data["serve_url"] == seat.url and old.unique_id == uid,
              "migration: the add-on's announcement gives the inside entry its seat", f"{again.get('reason')} {old.state} {dict(old.data)}")
        check(not reauths(), "migration: the reauth_no_seat is closed once the announcement gave the seat",
              f"reauth flows still open: {[(f['flow_id'], f['step_id']) for f in reauths()]}")

        # the confirm is open while the person sets the seat up by hand: one entry per Home Assistant still holds
        for e in hass.config_entries.async_entries("membrane", include_ignore=True):
            await hass.config_entries.async_remove(e.entry_id)
        await hass.async_block_till_done()
        pending = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_HASSIO}, data=info(newer))
        manual = await mgr.async_init("membrane", context={"source": config_entries.SOURCE_USER})
        manual = await mgr.async_configure(manual["flow_id"], {"next_step_id": "connect"})
        check(manual.get("step_id") == "connect_ollama" and "http://ollama.invalid:11434" in str(manual.get("description_placeholders")),
              "the manual form carries the Ollama hint", str(manual)[:300])
        made = await mgr.async_configure(manual["flow_id"], {"serve_url": seat.url, "serve_pass": newer})
        await hass.async_block_till_done()
        late = await mgr.async_configure(pending["flow_id"], {})
        await hass.async_block_till_done()
        n = len(hass.config_entries.async_entries("membrane"))
        check(str(pending.get("step_id")).startswith("hassio_confirm") and made.get("type") == "create_entry" and late.get("type") == "abort" and n == 1,
              "a confirm left open while the seat was set up by hand aborts (one entry per Home Assistant)",
              f"confirm -> {late.get('type')}/{late.get('reason')}; membrane entries: {n}")
    finally:
        await hass.async_stop()


# ---- run a seat inside Home Assistant ----------------------------------------------------------------------------------------------------------

SMOKE_KEY = "sk-smoke-OPENAI-0123456789"          # the fake endpoint's key: must reach the endpoint as a bearer and appear nowhere else


class FakeOpenAI:
    """An OpenAI-compatible endpoint on loopback: POST /v1/chat/completions streams `Done: <the last user text>` as server-sent events. `seen`: (model, bearer, last user text)."""

    def __init__(self):
        self.seen: list[tuple[str, str, str]] = []

    @staticmethod
    def _last_user(messages) -> str:
        for m in reversed(messages or []):
            if m.get("role") == "user":
                c = m.get("content")
                return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
        return ""

    async def _chat(self, req):
        from aiohttp import web
        body = await req.json()
        text = self._last_user(body.get("messages"))
        self.seen.append((str(body.get("model")), req.headers.get("Authorization", ""), text))
        r = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await r.prepare(req)
        for piece in ("Done: ", text.splitlines()[-1] if text else ""):
            await r.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}) + "\n\n").encode())
        await r.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n").encode())
        await r.write_eof()
        return r

    async def start(self):
        from aiohttp import web
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._chat)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        return self


class ReleaseFolder:
    """The release download folder on loopback (what `release_base` names); `asked` is every path asked."""

    def __init__(self, folder: Path):
        self.folder, self.asked = folder, []

    async def _get(self, req):
        from aiohttp import web
        self.asked.append(req.path)
        f = self.folder / req.match_info["name"]
        return web.Response(body=f.read_bytes()) if f.is_file() else web.Response(status=404)

    async def start(self):
        from aiohttp import web
        app = web.Application()
        app.router.add_get("/{name}", self._get)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        return self


def old_store(path: Path) -> None:
    """A 0.7 Python seat's store, the real schema (c_sessions, c_messages, c_entities: native/tests/legacy_fixture), one session of one question and its answer."""
    import sqlite3
    path.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(path)
    cx.executescript("CREATE TABLE c_sessions (k TEXT PRIMARY KEY, body TEXT NOT NULL); CREATE TABLE c_messages (k TEXT PRIMARY KEY, body TEXT NOT NULL); "
                     "CREATE TABLE c_entities (k TEXT PRIMARY KEY, body TEXT NOT NULL);")
    s = {"id": "ses_old", "title": "From the old seat", "agent": "build", "directory": "/config/membrane", "projectID": "membrane", "version": "1.0",
         "time": {"created": 1_790_000_000_000, "updated": 1_790_000_100_000}}
    cx.execute("INSERT INTO c_sessions VALUES (?, ?)", ("ses_old", json.dumps({"s": s})))
    u = {"info": {"role": "user", "id": "msg_u1", "agent": "build", "time": {"created": 1_790_000_010_000}, "sessionID": "ses_old"},
         "parts": [{"text": "an old question about the porch light", "id": "prt_u1", "type": "text", "sessionID": "ses_old", "messageID": "msg_u1"}]}
    a = {"info": {"role": "assistant", "id": "msg_a1", "parentID": "msg_u1", "agent": "build", "mode": "build", "sessionID": "ses_old", "finish": "stop",
                  "time": {"created": 1_790_000_010_050, "completed": 1_790_000_012_000}},
         "parts": [{"id": "prt_a1", "type": "text", "text": "an old answer", "time": {"start": 1, "end": 2}}]}
    cx.execute("INSERT INTO c_messages VALUES (?, ?)", ("ses_old|msg_u1", json.dumps({"n": 1, "sid": "ses_old", "mid": "msg_u1", "row": u})))
    cx.execute("INSERT INTO c_messages VALUES (?, ?)", ("ses_old|msg_a1", json.dumps({"n": 2, "sid": "ses_old", "mid": "msg_a1", "row": a})))
    cx.commit()
    cx.close()


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, TypeError, ValueError):
        return False


def seat_pid(cfg: Path):
    try:
        return int((cfg / "membrane" / "seat.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def argv_of(pid) -> str:
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


async def mcp_read(s, cfg: Path, port: int, spec: str):
    tok = (cfg / "membrane" / "admin.token").read_text().strip()
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_read", "arguments": {"spec": spec}}}
    async with s.post(f"http://127.0.0.1:{port}/mcp", json=body, headers={"Authorization": f"Bearer {tok}"}) as r:
        got = await r.json(content_type=None)
    return ((got.get("result") or {}).get("content") or [{}])[0].get("text", "")


async def drive_inside(work: Path, cliffvm: str, ha_port: int, s) -> None:
    sys.path.insert(0, str(ROOT))
    from custom_components.membrane import seat_runner as sr      # the platform map and the asset name (the hass venv has what its package imports)
    sys.path.pop(0)
    version = json.loads((CC / "manifest.json").read_text())["version"]
    target = sr.release_target()
    rel, stage = work / "release", work / "stage" / version
    rel.mkdir(parents=True, exist_ok=True)
    tarball = rel / sr.asset_name(version, target)
    b = subprocess.run(["sh", str(ROOT / "scripts" / "dist" / "build.sh"), "--bin", cliffvm, "--out", str(stage), "--archive", str(tarball)],
                       capture_output=True, text=True)
    check(b.returncode == 0 and tarball.is_file() and Path(str(tarball) + ".sha256").is_file(), f"the release tarball of {version} ({target}) is built",
          (b.stdout + b.stderr)[-300:])
    cfg = config_dir(work / "ha-inside", ha_port)
    # the first start finds the dist folder in place (the flow names no release_base, and nothing may reach GitHub here); the download is step (f)
    shutil.copytree(stage, cfg / "membrane" / "app" / version)
    old_store(cfg / "membrane" / ".membrane" / "field.sqlite" / "field.sqlite3")
    oai = await FakeOpenAI().start()
    relsrv = await ReleaseFolder(rel).start()
    hass = Hass(cfg, ha_port)
    try:
        await hass.start(s)
        await hass.onboard(s)

        # (a) the flow over REST: menu -> inside -> openai
        st, menu = await hass.req(s, "POST", "/api/config/config_entries/flow", {"handler": "membrane"})
        fid = menu.get("flow_id")
        st, pick = await hass.req(s, "POST", f"/api/config/config_entries/flow/{fid}", {"next_step_id": "inside"})
        check(pick.get("step_id") == "inside" and [f["name"] for f in pick.get("data_schema", [])] == ["provider"],
              "inside: the provider form", json.dumps(pick)[:300])
        st, form = await hass.req(s, "POST", f"/api/config/config_entries/flow/{fid}", {"provider": "openai"})
        check(form.get("step_id") == "openai" and {f["name"] for f in form.get("data_schema", [])} == {"base_url", "model", "api_key", "seat_name"},
              "inside: the openai form (base_url, model, api_key, seat_name)", json.dumps(form)[:300])
        st, bad = await hass.req(s, "POST", f"/api/config/config_entries/flow/{fid}", {"base_url": "not a url", "model": "m"})
        check(bad.get("errors") == {"base_url": "invalid_url"}, "inside: a URL that is not http(s) stays on the form", json.dumps(bad)[:200])
        t0 = time.time()
        st, done = await hass.req(s, "POST", f"/api/config/config_entries/flow/{fid}", {"base_url": oai.url, "model": "smoke-model", "api_key": SMOKE_KEY})
        check(done.get("type") == "create_entry", "inside: the entry is made", json.dumps(done)[:300])
        entry_id = (done.get("result") or {}).get("entry_id")

        async def loaded():
            _, es = await hass.req(s, "GET", "/api/config/config_entries/entry?domain=membrane")
            return es if es and es[0].get("state") == "loaded" else None
        es = await wait_for(loaded, 120)
        stored = json.loads((cfg / ".storage" / "core.config_entries").read_text())["data"]["entries"]
        data = next(e["data"] for e in stored if e["domain"] == "membrane")
        port = int(data.get("serve_port") or 0)
        pid1 = seat_pid(cfg)
        check(bool(es) and data.get("mode") == "inside" and data.get("provider") == "openai" and port > 0,
              "inside: the entry is loaded, its seat on 127.0.0.1", f"{time.time() - t0:.1f} s; port {port}; state {json.dumps(es)[:200]}")
        cmd = argv_of(pid1)
        check(pid_alive(pid1) and "serve" in cmd and f"--port {port}" in cmd and "--bind 127.0.0.1" in cmd and SMOKE_KEY not in cmd,
              "inside: the seat process runs `cliffvm serve` with no key on its command line", cmd[:300])
        async with s.get(f"http://127.0.0.1:{port}/global/health") as r:
            check(r.status == 401, "inside: the seat's door wants a token", str(r.status))

        # (b) Assist -> the component -> the seat -> the endpoint
        _, states = await hass.req(s, "GET", "/api/states")
        conv = next((x["entity_id"] for x in states if x["entity_id"].startswith("conversation.") and "membrane" in x["entity_id"]), None)
        st, said = await hass.req(s, "POST", "/api/conversation/process", {"text": "turn the porch light on", "agent_id": conv, "language": "en"})
        speech = (((said or {}).get("response") or {}).get("speech") or {}).get("plain", {}).get("speech", "") if isinstance(said, dict) else ""
        check(st == 200 and "Done:" in speech and "turn the porch light on" in speech, "inside: Assist answers through the seat the component started",
              f"{st} {speech!r}")
        check(any(m == "smoke-model" and a == f"Bearer {SMOKE_KEY}" and "porch light" in t for m, a, t in oai.seen),
              "inside: the endpoint got the model and the key as a bearer", json.dumps([(m, a[:12] + "...", t[-60:]) for m, a, t in oai.seen])[:400])

        # (c) the history cutover finds <config>/membrane/.membrane by itself
        async def cut():
            txt = await mcp_read(s, cfg, port, "Legacy:cutover")
            try:
                led = json.loads(txt[txt.index("{"):])
            except ValueError:
                return None
            return led if led.get("state") in ("done", "error") else None
        led = await wait_for(cut, 120, 1) or {}
        check(led.get("state") == "done" and str(led.get("path", "")).endswith(".membrane/field.sqlite/field.sqlite3") and led.get("total") == 1,
              "inside: the history cutover found the 0.7 store in <config>/membrane/.membrane and imported its one session",
              json.dumps(led)[:400] if led else (await mcp_read(s, cfg, port, "Legacy:cutover"))[:400])

        # (d) the options flow: another model, the key kept (an empty key field): the seat restarts with it
        st, of = await hass.req(s, "POST", "/api/config/config_entries/options/flow", {"handler": entry_id})
        st, of2 = await hass.req(s, "POST", f"/api/config/config_entries/options/flow/{of['flow_id']}", {"provider": "openai"})
        st, ofd = await hass.req(s, "POST", f"/api/config/config_entries/options/flow/{of['flow_id']}", {"base_url": oai.url, "model": "smoke-model-2"})
        check(of.get("step_id") == "init" and of2.get("step_id") == "openai" and ofd.get("type") == "create_entry", "options: provider -> openai form -> saved",
              f"{of.get('step_id')} {of2.get('step_id')} {ofd.get('type')}")

        async def restarted():
            p = seat_pid(cfg)
            return p if p and p != pid1 and pid_alive(p) and await loaded() else None
        pid2 = await wait_for(restarted, 120)
        check(bool(pid2) and not pid_alive(pid1), "options: the seat restarted (the old process is gone)", f"{pid1} -> {pid2}")
        st, said = await hass.req(s, "POST", "/api/conversation/process", {"text": "and the kitchen", "agent_id": conv, "language": "en"})
        speech = (((said or {}).get("response") or {}).get("speech") or {}).get("plain", {}).get("speech", "") if isinstance(said, dict) else ""
        check("Done:" in speech and any(m == "smoke-model-2" and a == f"Bearer {SMOKE_KEY}" for m, a, _ in oai.seen),
              "options: the new model answers, the stored key kept", f"{speech!r} {[m for m, _, _ in oai.seen]}")

        # (e) a reload, then Home Assistant's stop: each time the running seat is gone
        st, _ = await hass.req(s, "POST", f"/api/config/config_entries/entry/{entry_id}/reload")

        async def reloaded():
            p = seat_pid(cfg)
            return p if p and p != pid2 and pid_alive(p) and await loaded() else None
        pid3 = await wait_for(reloaded, 120)
        check(bool(pid3) and not pid_alive(pid2), "reload: a new seat, the old process gone", f"{pid2} -> {pid3}")
        await asyncio.to_thread(hass.stop)
        check(not pid_alive(pid3), "Home Assistant's stop stops the seat", f"pid {pid3}: {argv_of(pid3)[:120]}")

        # (f) the download: no dist folder, the entry names the local release (written while hass is down), the start fetches and checks it
        shutil.rmtree(cfg / "membrane" / "app")
        store = cfg / ".storage" / "core.config_entries"
        doc = json.loads(store.read_text())
        for e in doc["data"]["entries"]:
            if e["domain"] == "membrane":
                e["data"]["release_base"] = relsrv.url
        store.write_text(json.dumps(doc, indent=2))
        await hass.start(s)
        es = await wait_for(loaded, 180)
        app = cfg / "membrane" / "app" / version
        check(bool(es) and relsrv.asked == [f"/{tarball.name}", f"/{tarball.name}.sha256"] and (app / "bin" / "cliffvm").is_file()
              and (app / "MANIFEST.sha256").read_text() == (stage / "MANIFEST.sha256").read_text(),
              "download: the start fetched the tarball and its sha256 from release_base, checked and unpacked it", f"asked {relsrv.asked}; {json.dumps(es)[:150]}")
        st, said = await hass.req(s, "POST", "/api/conversation/process", {"text": "after the download", "agent_id": conv, "language": "en"})
        speech = (((said or {}).get("response") or {}).get("speech") or {}).get("plain", {}).get("speech", "") if isinstance(said, dict) else ""
        check("Done:" in speech and "after the download" in speech, "download: Assist answers through the downloaded seat", repr(speech))
        pid4 = seat_pid(cfg)

        # (g) the removal: the seat goes with the entry
        st, gone = await hass.req(s, "DELETE", f"/api/config/config_entries/entry/{entry_id}")
        await asyncio.sleep(1)
        check(st == 200 and pid4 and not pid_alive(pid4) and seat_pid(cfg) is None, "removal: the seat process is gone", f"{st} pid {pid4} {argv_of(pid4)[:100]}")
    finally:
        await asyncio.to_thread(hass.stop)
        await oai.runner.cleanup()
        await relsrv.runner.cleanup()
        for p in (seat_pid(cfg),):
            if p and pid_alive(p) and str(cfg) in argv_of(p):      # only a seat of this run's folder
                os.kill(p, 15)
    logs = "\n".join(x.read_text(errors="replace") for x in cfg.glob("hass.log*"))
    check(SMOKE_KEY not in logs and not re.search(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.", logs), "no key and no token in hass.log", "")
    check(any("membrane seat:" in ln for ln in logs.splitlines()), "the seat's own lines reach Home Assistant's log", "")
    lines = hass.component_lines()
    check(not lines, "inside: no unexpected WARNING/ERROR from the component", "\n      ".join(lines[:40]))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cliffvm", default=os.environ.get("CLIFFVM", str(ROOT / "native" / "target" / "release" / "cliffvm")))
    ap.add_argument("--work", default="")
    ap.add_argument("--ha-port", type=int, default=8133)
    ap.add_argument("--keep", action="store_true", help="keep the work folder (logs, config)")
    ap.add_argument("--skip-rest", action="store_true")
    ap.add_argument("--skip-discovery", action="store_true")
    ap.add_argument("--skip-inside", action="store_true")
    a = ap.parse_args()
    if not Path(a.cliffvm).is_file():
        raise SystemExit(f"no cliffvm at {a.cliffvm}")
    import homeassistant.const as hc
    print(f"Home Assistant {hc.__version__} on Python {sys.version.split()[0]}", flush=True)
    work = Path(a.work or tempfile.mkdtemp(prefix="ha-core-smoke-"))
    work.mkdir(parents=True, exist_ok=True)
    seat = Seat(work, a.cliffvm).start()
    print(f"seat {seat.url} (data {seat.dir / 'data'})", flush=True)
    hass = None
    try:
        if not a.skip_rest:
            hass = Hass(config_dir(work / "ha", a.ha_port), a.ha_port)
            async with aiohttp.ClientSession() as s:
                await hass.start(s)
                await hass.onboard(s)
                lines = hass.component_lines()
                check(not lines, "the component loads with no warning or error", "\n      ".join(lines[:20]))
                await drive_rest(hass, seat, s)
            await asyncio.to_thread(hass.stop)
            lines = hass.component_lines()
            check(not lines, "no WARNING/ERROR from the component in hass.log over the whole run", "\n      ".join(lines[:40]))
        if not a.skip_inside:             # before the in-process run: Home Assistant's blocking-call detector stays in this process after it
            async with aiohttp.ClientSession() as s:
                await drive_inside(work, a.cliffvm, a.ha_port, s)
        if not a.skip_discovery:
            await drive_discovery(config_dir(work / "ha-discovery", a.ha_port), seat)
            lines = await asyncio.to_thread(Hass(work / "ha-discovery", 0).component_lines)
            check(not lines, "no WARNING/ERROR from the component in the in-process run", "\n      ".join(lines[:40]))
    finally:
        if hass is not None:
            await asyncio.to_thread(hass.stop)
        await asyncio.to_thread(seat.stop)
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"kept {work}")
    bad = [n for ok, n, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
