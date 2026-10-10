// MEMBRANE HOME (2026-10-10, the operator: "after login we get HA's UI
// hidden behind a button and instead we get the 3d-view of our seat/user on
// the graph with membrane chat overlayed over it, so that the graph is
// visible").
//
// The whole field in 3D (field3d.js) fills the screen, the camera following
// the signed-in user's own entity (person:<slug>, the speaker the proxy's
// '[HA user: <name>]' line makes); the chat (panel.js's element in overlay
// mode) floats over it on glass. HA's own UI is one button away: "HA" docks
// the sidebar back and goes to the dashboards; this page hides it again.

const HSERVE = "/api/ai_terminal/serve";
const FOLLOW_EVERY_MS = 15000;
const IDLE_BEFORE_FOLLOW_MS = 30000;
const LS_CHAT = "membrane.home.chat";
const LS_IMMERSIVE = "membrane.home.immersive";

function personSlug(name) {
  return String(name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

function hhash(s) {
  let h = 2166136261;
  s = String(s);
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function hKindColor(kind) { return "hsl(" + (hhash("kind:" + kind) % 360) + ", 68%, 62%)"; }

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } };

const HomeLogic = { personSlug };
if (typeof module !== "undefined" && module.exports) module.exports = HomeLogic;

const HSTYLE = `
  :host { display:block; position:relative; height:100vh; height:100dvh; overflow:hidden;
          background:var(--primary-background-color,#0b0e14); color:var(--primary-text-color,#ddd);
          font:14px/1.5 system-ui,-apple-system,sans-serif; }
  * { box-sizing:border-box; }
  button { font:inherit; color:inherit; cursor:pointer; }
  .field { position:absolute; inset:0; }
  .glass { background:color-mix(in srgb, var(--card-background-color,#111) 62%, transparent);
           backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px);
           border:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); }
  .top { position:absolute; top:12px; left:12px; z-index:3; display:flex; gap:6px; align-items:center;
         padding:4px; border-radius:999px; max-width:calc(100% - 24px); }
  .top button { border:0; background:transparent; border-radius:999px; padding:6px 12px; white-space:nowrap; }
  .top button:hover, .top button.on { background:color-mix(in srgb, var(--primary-color,#3477d6) 28%, transparent); }
  .top .ha { font-weight:600; }
  .status { font-size:12px; opacity:.7; padding:0 10px 0 4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
  .chat { position:absolute; z-index:2; top:12px; right:12px; bottom:12px; width:min(460px, 44vw);
          border-radius:16px; overflow:hidden; display:flex; flex-direction:column;
          transition:transform .2s ease, opacity .2s ease; }
  .chat.hidden { transform:translateX(calc(100% + 24px)); opacity:0; pointer-events:none; }
  .chat ai-terminal-panel { flex:1; min-height:0; height:auto; }
  .tip { position:absolute; z-index:4; pointer-events:none; display:none; padding:4px 8px; border-radius:8px;
         font-size:12px; max-width:320px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .card { position:absolute; z-index:3; left:12px; bottom:12px; max-width:min(420px, calc(100% - 24px));
          padding:10px 12px; border-radius:12px; display:none; }
  .card.open { display:block; }
  .card .t { font-weight:600; overflow-wrap:anywhere; }
  .card .k { font-size:12px; opacity:.65; }
  .card .row { display:flex; gap:6px; margin-top:8px; flex-wrap:wrap; }
  .card button { border:0; border-radius:8px; padding:4px 10px;
                 background:color-mix(in srgb, var(--primary-color,#3477d6) 30%, transparent); }
  .msg { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; z-index:1;
         pointer-events:none; opacity:.6; white-space:pre-line; text-align:center; padding:24px; }
  @media (max-width: 760px) {
    .chat { top:auto; left:8px; right:8px; bottom:8px; width:auto; height:56vh; height:56dvh; }
    .card { bottom:calc(56dvh + 16px); }
    .chat.hidden ~ .card { bottom:12px; }
    .status { display:none; }
  }
`;

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" &&
    !customElements.get("membrane-home-panel")) {
  class MembraneHomePanel extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._v3 = null; this._me = null; this._lastTouch = 0; this._hidSidebar = false;
    }

    set hass(h) {
      this._hass = h;
      if (this._chat) this._chat.hass = h;
      if (!this._started && this.isConnected && this.shadowRoot.firstChild) this._start();
    }
    get hass() { return this._hass; }
    set narrow(n) { this._narrow = !!n; if (this._chat) this._chat.narrow = n; }
    set panel(p) { this._panel = p; }

    connectedCallback() {
      if (!this.shadowRoot.firstChild) this._render();
      if (this._hass && !this._started) this._start();
      if (lsGet(LS_IMMERSIVE, "1") === "1") this._dock("always_hidden");
    }

    disconnectedCallback() {
      this._started = false;
      clearInterval(this._follow);
      if (this._v3) { this._v3.stop(); this._v3 = null; }
      if (this._hidSidebar) this._dock("restore");
    }

    // ---------------------------------------------------------- HA's own UI, one button away
    _dock(mode) {
      // HA's frontend listens for this (the sidebar's docked / auto /
      // always_hidden setting, which it keeps in localStorage 'dockedSidebar')
      if (mode === "always_hidden" && !this._hidSidebar) {
        let was = "docked";
        try { was = JSON.parse(lsGet("dockedSidebar", '"docked"')) || "docked"; } catch (e) { /* keep docked */ }
        this._sidebarWas = was === "always_hidden" ? "docked" : was;
      }
      if (mode === "restore") mode = this._sidebarWas || "docked";
      this.dispatchEvent(new CustomEvent("hass-dock-sidebar", { detail: { dock: mode }, bubbles: true, composed: true }));
      this._hidSidebar = mode === "always_hidden";
    }

    _toHA() {
      this._dock("restore");
      history.pushState(null, "", "/lovelace");
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    }

    // ---------------------------------------------------------- dom
    _render() {
      const s = document.createElement("style");
      s.textContent = HSTYLE;
      const root = document.createElement("div");
      root.innerHTML = `
        <div class="field"></div>
        <div class="msg">finding you on the field…</div>
        <div class="top glass">
          <button class="ha" title="Home Assistant's dashboards (and its sidebar)">HA</button>
          <button class="me" title="Back to you">◎</button>
          <button class="chatbtn" title="Show / hide the chat">💬</button>
          <button class="start" title="Open Home Assistant here (this browser)">☆</button>
          <span class="status"></span>
        </div>
        <div class="chat glass"><ai-terminal-panel overlay></ai-terminal-panel></div>
        <div class="card glass"><div class="t"></div><div class="k"></div>
          <div class="row"><button class="ask">Ask about it</button><button class="go">Fly there</button><button class="x">×</button></div></div>
        <div class="tip glass"></div>`;
      this.shadowRoot.append(s, root);
      const $ = (q) => root.querySelector(q);
      this._host3d = $(".field"); this._msgEl = $(".msg"); this._statusEl = $(".status");
      this._chatBox = $(".chat"); this._chat = $("ai-terminal-panel");
      this._card = $(".card"); this._tip = $(".tip");
      if (this._hass) this._chat.hass = this._hass;
      if (this._narrow != null) this._chat.narrow = this._narrow;
      const chatOn = lsGet(LS_CHAT, "1") === "1";
      this._chatBox.classList.toggle("hidden", !chatOn);
      $(".chatbtn").classList.toggle("on", chatOn);
      $(".ha").addEventListener("click", () => this._toHA());
      $(".me").addEventListener("click", () => { this._lastTouch = 0; this._followMe(true); });
      $(".chatbtn").addEventListener("click", (e) => {
        const on = this._chatBox.classList.toggle("hidden") === false;
        e.currentTarget.classList.toggle("on", on);
        lsSet(LS_CHAT, on ? "1" : "0");
      });
      $(".start").addEventListener("click", () => {
        lsSet("defaultPanel", JSON.stringify((this._panel && this._panel.url_path) || "membrane"));
        this._statusEl.textContent = "this browser opens here now (Profile → Dashboard changes it)";
      });
      $(".x").addEventListener("click", () => { this._card.classList.remove("open"); if (this._v3) this._v3.select(null); });
      $(".go").addEventListener("click", () => { if (this._v3 && this._sel) { this._lastTouch = performance.now(); this._v3.flyTo(this._sel.id); } });
      $(".ask").addEventListener("click", () => this._askAbout());
      this._host3d.addEventListener("pointerdown", () => { this._lastTouch = performance.now(); });
      this._host3d.addEventListener("wheel", () => { this._lastTouch = performance.now(); }, { passive: true });
    }

    async _get(path) {
      const r = await this._hass.fetchWithAuth(HSERVE + path);
      const t = await r.text();
      let d = null; try { d = t ? JSON.parse(t) : null; } catch (e) { d = t; }
      if (!r.ok) throw new Error(path + " → " + r.status);
      return d;
    }

    async _start() {
      this._started = true;
      const hass = this._hass;
      this._me = "person:" + personSlug(hass.user && hass.user.name);
      try {
        if (!window.MembraneField3D) await import("/api/ai_terminal/www/field3d.js");
        const api = {
          get: (p) => this._get(p),
          stream: (p, signal) => hass.fetchWithAuth(HSERVE + p, { signal, headers: { Accept: "text/event-stream" } }),
          kindColor: hKindColor, isAdmin: !!(hass.user && hass.user.is_admin),
          onStatus: (t) => { this._statusEl.textContent = t; },
          onSelect: (m) => this._select(m),
          onRelated: () => {},
          onHover: (m, x, y) => {
            if (!m) { this._tip.style.display = "none"; return; }
            this._tip.textContent = m.label + "  ·  " + m.kind;
            this._tip.style.left = Math.min(x + 14, (this._host3d.clientWidth || 0) - 240) + "px";
            this._tip.style.top = (y + 14) + "px"; this._tip.style.display = "block";
          },
        };
        const v = this._v3 = new window.MembraneField3D(this._host3d, api);
        if (window.matchMedia("(max-width: 760px)").matches) { v.budget = 3000; v.cache.budget = 3000; }
        await v.start();
        if (!this._started) { v.stop(); return; }
        await this._followMe(true);
      } catch (e) {
        this._msgEl.style.display = ""; this._msgEl.textContent = "the field is unavailable\n" + ((e && e.message) || e);
        return;
      }
      clearInterval(this._follow);
      this._follow = setInterval(() => this._followMe(false), FOLLOW_EVERY_MS);
    }

    async _followMe(force) {
      const v = this._v3; if (!v) return;
      if (!force && performance.now() - this._lastTouch < IDLE_BEFORE_FOLLOW_MS) return;
      if (!force) v.locateCache.delete(this._me);           // it moves as you talk
      const ok = await v.flyTo(this._me);
      if (ok) { this._msgEl.style.display = "none"; v.select(null); return; }
      this._msgEl.style.display = "";
      this._msgEl.textContent = "you are not on the field yet — say something to membrane";
      setTimeout(() => { if (this._msgEl.textContent.startsWith("you are not")) this._msgEl.style.display = "none"; }, 6000);
    }

    _select(m) {
      this._sel = m || null;
      if (!m) { this._card.classList.remove("open"); return; }
      this._lastTouch = performance.now();
      this._card.querySelector(".t").textContent = m.label || m.id;
      this._card.querySelector(".k").textContent = (m.kind || "") + (m.mass != null ? "  ·  mass " + m.mass : "");
      this._card.classList.add("open");
    }

    _askAbout() {
      if (!this._sel || !this._chat || !this._chat.shadowRoot) return;
      const ta = this._chat.shadowRoot.querySelector("textarea");
      if (!ta) return;
      ta.value = (ta.value ? ta.value + " " : "") + "[about " + this._sel.id + "] ";
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      if (this._chatBox.classList.contains("hidden")) this.shadowRoot.querySelector(".chatbtn").click();
      ta.focus();
    }
  }
  customElements.define("membrane-home-panel", MembraneHomePanel);
  // the chat element lives in panel.js (the AI Dashboard's): load it once
  if (!customElements.get("ai-terminal-panel")) import("/api/ai_terminal/www/panel.js");
}
