/**
 * <ai-terminal-panel>: the AI dashboard's panel element -- a web chat
 * client for membrane's NATIVE serve surface (the opencode-compatible
 * REST/SSE on the brain phone), THROUGH the integration's proxy view
 * (/api/ai_terminal/serve/*): HA auth per request, the serve credential
 * stays server-side; works on the LAN and from outside (chegate tunnel)
 * alike.
 *
 * Sessions are per HA user and follow you across devices: the proxy
 * scopes the brain's session list to the caller, the panel opens the most
 * recently active one and offers the rest in a dropdown. The proxy also
 * prefixes every message with a "[HA user: <name>]" line (who is talking,
 * for the brain's memory); the panel hides that line when rendering.
 *
 * Turn lifecycle: POST prompt_async, then poll the message store + the
 * serve's /session/status until the session reports idle (the brain's
 * own truth -- never a "nothing changed for N polls" guess, which ended
 * long tool calls early).
 */
const SERVE = "/api/ai_terminal/serve";
const POLL_MS = 1200;
// prompt_async returns before the runner marks the session busy: a short
// grace where "idle" still means "not started yet"
const START_GRACE_MS = 8000;
const SPEAKER_PREFIX = "[HA user: ";
const NEW_TITLE = "New chat";

class AiTerminalPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._sid = null;
    this._busy = false;
    this._sawBusy = false;
    this._sentAt = 0;
    this._pollTimer = null;
    this._booted = false;
  }

  set hass(h) {
    this._hass = h;
    if (!this._booted && this.isConnected) { this._booted = true; this._boot(); }
  }
  get hass() { return this._hass; }

  connectedCallback() {
    if (!this.shadowRoot.firstChild) this._render();
    if (!this._booted && this._hass) { this._booted = true; this._boot(); }
  }

  disconnectedCallback() {
    this._stopPoll();
    this._booted = false;
  }

  _stopPoll() {
    if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
  }

  _render() {
    const s = document.createElement("style");
    s.textContent = `
      :host { display:block; height:100%; }
      .wrap { display:flex; flex-direction:column; height:100%; min-height:120px;
              background:var(--primary-background-color,#111);
              color:var(--primary-text-color,#ddd); }
      .top { display:flex; align-items:center; gap:8px; padding:6px 12px;
             border-bottom:1px solid var(--divider-color,rgba(128,128,128,.25));
             font:13px system-ui,sans-serif; }
      .top .title { opacity:.8; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      .top select { flex:1; min-width:0; max-width:420px; border-radius:6px; padding:5px 8px;
                    border:1px solid rgba(128,128,128,.35); color:inherit;
                    background:var(--secondary-background-color,#222); font:13px system-ui,sans-serif; }
      .top button { border:0; border-radius:6px; padding:6px 12px; cursor:pointer;
                    background:var(--secondary-background-color,#222); color:inherit;
                    font:13px system-ui,sans-serif; }
      .err { color:var(--error-color,#f66); font:13px monospace; padding:0 12px;
             white-space:pre-wrap; }
      .err:not(:empty) { padding:8px 12px; }
      .msgs { flex:1; min-height:0; overflow-y:auto; padding:14px 16px; box-sizing:border-box; }
      .row { margin:10px 0; font:14px/1.45 system-ui,-apple-system,sans-serif;
             white-space:pre-wrap; overflow-wrap:anywhere; }
      .row.user { text-align:right; }
      .row.user .bub { display:inline-block; text-align:left;
                       background:var(--primary-color,#3477d6);
                       color:var(--text-primary-color,#fff);
                       border-radius:12px 12px 2px 12px; padding:8px 12px; max-width:86%; }
      .row.ai .bub { display:inline-block; background:var(--card-background-color,#1c1c1e);
                     border-radius:12px 12px 12px 2px; padding:10px 12px; max-width:92%; }
      .chip { display:inline-block; font:12px/1.6 monospace; opacity:.85;
              border:1px solid rgba(128,128,128,.4); border-radius:8px;
              padding:0 8px; margin:3px 4px 3px 0; }
      .pending { opacity:.55; font-style:italic; }
      .empty { opacity:.5; text-align:center; margin-top:20%; font:14px system-ui,sans-serif; }
      .inputbar { display:flex; gap:8px; padding:10px 12px; align-items:flex-end;
                  border-top:1px solid var(--divider-color,rgba(128,128,128,.25)); }
      .inputbar textarea { flex:1; min-height:44px; max-height:180px; resize:vertical;
                           border-radius:8px; border:1px solid rgba(128,128,128,.35);
                           background:var(--card-background-color,#111); color:inherit;
                           padding:8px; font:14px system-ui,sans-serif; box-sizing:border-box; }
      .inputbar button { min-width:76px; height:36px; border:0; border-radius:8px;
                         cursor:pointer; background:var(--primary-color,#3477d6);
                         color:var(--text-primary-color,#fff); font:14px system-ui,sans-serif; }
      .inputbar button:disabled { opacity:.5; cursor:default; }
      .inputbar .stop { background:var(--error-color,#c0392b); }`;
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    wrap.innerHTML = `
      <div class="top"><span class="title">AI Dashboard</span>
        <select class="sessions" title="Your chats (on every device)"></select>
        <button class="new" title="Start a fresh brain session">New chat</button></div>
      <div class="err"></div>
      <div class="msgs"></div>
      <div class="inputbar">
        <textarea placeholder="ask the brain… (Enter send / Shift+Enter newline)"></textarea>
        <button class="send">Send</button>
        <button class="stop" style="display:none">Stop</button>
      </div>`;
    this.shadowRoot.append(s, wrap);
    const $ = (q) => wrap.querySelector(q);
    this._title = $(".title");
    this._picker = $(".sessions");
    this._picker.addEventListener("change", () => this._switchTo(this._picker.value));
    this._errEl = $(".err");
    this._msgs = $(".msgs");
    this._ta = $("textarea");
    this._send = $(".send");
    this._stop = $(".stop");
    this._send.addEventListener("click", () => this._sendText());
    this._stop.addEventListener("click", () => this._doAbort());
    $(".new").addEventListener("click", () => this._newChat());
    this._ta.addEventListener("keydown", (ev) => {
      // keep HA's global shortcuts (e, c, m...) out of the textarea
      ev.stopPropagation();
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        this._sendText();
      }
    });
  }

  _err(msg) { this._errEl.textContent = msg || ""; }

  async _api(path, opts) {
    const o = { ...(opts || {}) };
    if (o.body !== undefined) {
      o.headers = { "Content-Type": "application/json", ...(o.headers || {}) };
    }
    const r = await this._hass.fetchWithAuth(path, o);
    let data = null;
    if (r.status !== 204) {
      const txt = await r.text();
      try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = txt; }
    }
    return { status: r.status, ok: r.ok, data };
  }

  async _boot() {
    this._err("");
    try {
      const conf = await this._api("/api/ai_terminal/conf");
      if (!conf.ok || !conf.data || !conf.data.serve_ok) {
        this._err("brain lane off: set ai_terminal serve_url + serve_pass in configuration.yaml");
        return;
      }
      this._title.textContent = "AI Dashboard";
      this._title.title = conf.data.serve_url;
      const health = await this._api(SERVE + "/global/health");
      if (!health.ok) {
        this._err("brain unreachable (" + health.status + "): " +
                  (typeof health.data === "string" ? health.data : ""));
        return;
      }
      this._sid = await this._resolveSession();
      this._renderPicker();
      await this._loadHistory();
      // a turn may still be running from a previous visit
      if (await this._isBusy()) this._startPoll(true);
    } catch (e) {
      this._err("chat boot failed: " + (e && e.message || e));
    }
  }

  async _listSessions() {
    const r = await this._api(SERVE + "/session");
    if (!r.ok || !Array.isArray(r.data))
      throw new Error("session list failed (" + r.status + "): " +
        (typeof r.data === "string" ? r.data : JSON.stringify(r.data)));
    this._sessions = r.data;               // the proxy sorts: newest first
    return r.data;
  }

  _renderPicker() {
    const sel = this._picker;
    sel.innerHTML = "";
    for (const ses of this._sessions || []) {
      const o = document.createElement("option");
      const t = ses.time && (ses.time.updated || ses.time.created);
      o.value = ses.id;
      o.textContent = (ses.title || NEW_TITLE) +
        (t ? "  ·  " + new Date(t).toLocaleString([], { month: "short",
          day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
      sel.appendChild(o);
    }
    if (this._sid) sel.value = this._sid;
  }

  async _resolveSession() {
    const list = await this._listSessions();
    const sid = list.length ? list[0].id : await this._createSession();
    if (!list.length) await this._listSessions();
    return sid;
  }

  async _createSession() {
    const created = await this._api(SERVE + "/session", {
      method: "POST", body: JSON.stringify({ title: NEW_TITLE }),
    });
    if (!created.ok || !created.data || !created.data.id)
      throw new Error("session create failed (" + created.status + "): " +
        (typeof created.data === "string" ? created.data : JSON.stringify(created.data)));
    return created.data.id;
  }

  async _newChat() {
    if (this._busy) return;
    this._err("");
    try {
      this._sid = await this._createSession();
      await this._listSessions();
      this._renderPicker();
      this._renderRows([]);
      this._ta.focus();
    } catch (e) { this._err(e.message || String(e)); }
  }

  async _switchTo(sid) {
    if (!sid || sid === this._sid) return;
    this._stopPoll();
    this._setBusy(false);
    this._sid = sid;
    this._err("");
    await this._loadHistory();
    if (await this._isBusy()) this._startPoll(true);
  }

  async _titleFrom(text) {
    const ses = (this._sessions || []).find((x) => x.id === this._sid);
    if (ses && ses.title && ses.title !== NEW_TITLE) return;
    const title = text.replace(/\s+/g, " ").slice(0, 48) +
      (text.length > 48 ? "…" : "");
    await this._api(SERVE + "/session/" + this._sid, {
      method: "PATCH", body: JSON.stringify({ title }),
    });
  }

  async _loadHistory() {
    if (!this._sid) return;
    const r = await this._api(SERVE + "/session/" + this._sid + "/message");
    this._renderRows(r.ok && Array.isArray(r.data) ? r.data : []);
  }

  async _isBusy() {
    const r = await this._api(SERVE + "/session/status");
    const st = r.ok && r.data && r.data[this._sid];
    return !!(st && st.type === "busy");
  }

  _renderRows(rows) {
    const box = this._msgs;
    const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    box.innerHTML = "";
    if (!rows.length && !this._busy) {
      const e = document.createElement("div");
      e.className = "empty";
      e.textContent = "New conversation with the brain.";
      box.appendChild(e);
    }
    for (const r of rows) {
      const info = (r && (r.info || r.message)) || r || {};
      const row = document.createElement("div");
      row.className = "row " + (info.role === "user" ? "user" : "ai");
      const bub = document.createElement("span");
      bub.className = "bub";
      let text = "";
      for (const p of (r && r.parts) || []) {
        if (!p || typeof p !== "object") continue;
        if (p.type === "text") {
          if (p.text) text += (text ? "\n" : "") + p.text;
        } else if (p.type === "tool") {
          const chip = document.createElement("span");
          chip.className = "chip";
          const st = p.state && p.state.status;
          chip.textContent = "🛠 " + (p.tool || p.name || p.type) +
            (st && st !== "completed" ? " (" + st + ")" : "");
          bub.appendChild(chip);
        }
      }
      // the proxy's speaker line is for the brain, not for the screen
      if (text.startsWith(SPEAKER_PREFIX)) {
        const nl = text.indexOf("\n");
        text = nl >= 0 ? text.slice(nl + 1) : "";
      }
      if (text) bub.appendChild(document.createTextNode(text));
      if (!bub.childNodes.length) bub.textContent = "…";
      row.appendChild(bub);
      box.appendChild(row);
    }
    if (this._busy) {
      const row = document.createElement("div");
      row.className = "row ai pending";
      row.textContent = "working…";
      box.appendChild(row);
    }
    if (stick) box.scrollTop = box.scrollHeight;
  }

  _setBusy(b) {
    this._busy = b;
    this._send.disabled = b;
    this._stop.style.display = b ? "" : "none";
  }

  async _sendText() {
    const text = (this._ta.value || "").trim();
    if (!text || this._busy || !this._sid) return;
    this._err("");
    this._ta.value = "";
    this._setBusy(true);
    try {
      const r = await this._api(
        SERVE + "/session/" + this._sid + "/prompt_async", {
          method: "POST",
          body: JSON.stringify({ agent: "build", parts: [{ type: "text", text }] }),
        });
      if (!r.ok) {
        this._err("send failed (" + r.status + "): " +
                  (typeof r.data === "string" ? r.data : JSON.stringify(r.data)));
        this._ta.value = text;
        this._setBusy(false);
        return;
      }
    } catch (e) {
      this._err("send failed: " + (e.message || e));
      this._ta.value = text;
      this._setBusy(false);
      return;
    }
    try { await this._titleFrom(text); } catch (e) { /* cosmetic */ }
    await this._loadHistory();
    this._startPoll(false);
  }

  _startPoll(alreadyBusy) {
    this._setBusy(true);
    this._sawBusy = alreadyBusy;
    this._sentAt = Date.now();
    this._stopPoll();
    this._pollTimer = setInterval(() => this._pollOnce(), POLL_MS);
  }

  async _pollOnce() {
    if (!this._busy || this._polling) return;
    this._polling = true;
    try {
      const busy = await this._isBusy();
      if (busy) this._sawBusy = true;
      await this._loadHistory();
      const graceOver = Date.now() - this._sentAt > START_GRACE_MS;
      if (!busy && (this._sawBusy || graceOver)) this._turnDone();
    } catch (e) {
      /* transient: keep polling */
    } finally {
      this._polling = false;
    }
  }

  _turnDone() {
    this._stopPoll();
    this._setBusy(false);
    this._loadHistory();
    // the dropdown's order + titles follow the activity
    this._listSessions().then(() => this._renderPicker()).catch(() => {});
  }

  async _doAbort() {
    if (!this._sid) return;
    try {
      await this._api(SERVE + "/session/" + this._sid + "/abort",
                      { method: "POST", body: "{}" });
    } catch (e) {}
    this._turnDone();
  }
}

customElements.define("ai-terminal-panel", AiTerminalPanel);
