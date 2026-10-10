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
const LS_FOLLOW = "membrane.home.follow";
// what the camera follows (the operator, 2026-10-10: "the seat (the most
// interesting entity) or thinker/chat, whoever's active, to show what Argus
// is thinking/doing, or even just where stuff is changing")
const FOLLOW_MODES = ["activity", "seat", "you"];
const SEAT_IDS = ["seat:self", "seat"];
const ACTIVITY_EVERY_MS = 1500;

/** The orbit angles that put the camera on the ray from the field's centre
 * through p (beyond it), so it looks at the centre THROUGH p -- the
 * activity in front, the field behind. null when p is the centre. */
function throughCenter(p) {
  const n = Math.hypot(p[0], p[1], p[2]);
  if (n < 40) return null;
  const d = [p[0] / n, p[1] / n, p[2] / n];
  return { phi: Math.max(0.05, Math.min(Math.PI - 0.05, Math.acos(d[1]))), theta: Math.atan2(d[0], d[2]) };
}

/** a -> b the short way round (angles). */
function angleStep(a, b, k) {
  let d = b - a;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d < -Math.PI) d += 2 * Math.PI;
  return a + d * k;
}

// HA's sidebar as a drop-down (2026-10-10, the operator: "replace the HA
// button with that sidebar ... float over the field like a drop-down menu;
// exit the field whenever the user uses it to navigate away; any click
// outside closes it; don't load membrane automatically if the user last left
// membrane thru that menu")
const LS_LEFT = "membrane.home.left";      // panel.js's landing rule reads it
const BUILTIN_TITLES = { lovelace: "Overview", energy: "Energy", map: "Map", logbook: "Logbook", history: "History",
  "media-browser": "Media", todo: "To-do lists", calendar: "Calendar", config: "Settings",
  "developer-tools": "Developer tools", profile: "Profile" };
const BUILTIN_ICONS = { lovelace: "mdi:view-dashboard", energy: "mdi:lightning-bolt", map: "mdi:tooltip-account",
  logbook: "mdi:format-list-bulleted-type", history: "mdi:chart-box", "media-browser": "mdi:play-box-multiple",
  todo: "mdi:clipboard-list", calendar: "mdi:calendar", config: "mdi:cog", "developer-tools": "mdi:hammer",
  profile: "mdi:account" };
const TAIL = ["developer-tools", "config", "profile"];

/** HA's sidebar entries, in the user's own sidebar order, minus the hidden
 * ones and this page; settings / developer tools / profile last. */
function menuEntries(panels, order, hidden, self) {
  const out = [];
  for (const p of Object.values(panels || {})) {
    if (!p || !p.url_path || p.url_path === self) continue;
    const title = p.title || BUILTIN_TITLES[p.url_path] || BUILTIN_TITLES[p.component_name];
    if (!title || (hidden || []).includes(p.url_path)) continue;
    out.push({ path: p.url_path, title, icon: p.icon || BUILTIN_ICONS[p.url_path] || BUILTIN_ICONS[p.component_name] || "mdi:circle-small" });
  }
  const rank = (e) => {
    const t = TAIL.indexOf(e.path);
    if (t >= 0) return 1e6 + t;
    const i = (order || []).indexOf(e.path);
    return i >= 0 ? i : 1e5 + (e.path === "lovelace" ? -1 : 0);
  };
  return out.sort((a, b) => rank(a) - rank(b) || a.title.localeCompare(b.title));
}

function nextMode(m) { const i = FOLLOW_MODES.indexOf(m); return FOLLOW_MODES[(i + 1) % FOLLOW_MODES.length]; }

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

const HomeLogic = { personSlug, menuEntries, nextMode, FOLLOW_MODES, throughCenter, angleStep };
if (typeof module !== "undefined" && module.exports) module.exports = HomeLogic;

const HSTYLE = `
  :host { display:block; position:relative; height:100vh; height:100dvh; overflow:hidden;
          background:var(--primary-background-color,#0b0e14); color:var(--primary-text-color,#ddd);
          font:14px/1.5 system-ui,-apple-system,sans-serif; }
  * { box-sizing:border-box; }
  button { font:inherit; color:inherit; cursor:pointer; }
  .field { position:absolute; inset:0; }
  /* no backdrop-filter: a blur over a live WebGL canvas is re-computed every
     frame for every glass box and saturated the Mac's compositor (2026-10-10) */
  .glass { background:color-mix(in srgb, var(--card-background-color,#111) 80%, transparent);
           border:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); }
  .top { position:absolute; top:12px; left:12px; z-index:3; display:flex; gap:6px; align-items:center;
         padding:4px; border-radius:999px; max-width:calc(100% - 24px); }
  .top button { border:0; background:transparent; border-radius:999px; padding:6px 12px; white-space:nowrap; }
  .top button:hover, .top button.on { background:color-mix(in srgb, var(--primary-color,#3477d6) 28%, transparent); }
  .top .ha { font-weight:600; }
  .hamenu { position:absolute; z-index:6; top:58px; left:12px; min-width:220px; max-width:calc(100% - 24px);
            max-height:70vh; max-height:70dvh; overflow:auto; border-radius:14px; padding:6px; display:none; }
  .hamenu.open { display:block; }
  .hamenu a { display:flex; align-items:center; gap:12px; padding:8px 12px; border-radius:10px; color:inherit;
              text-decoration:none; white-space:nowrap; }
  .hamenu a:hover, .hamenu a:focus-visible { background:color-mix(in srgb, var(--primary-color,#3477d6) 25%, transparent); outline:none; }
  .hamenu ha-icon { --mdc-icon-size:20px; opacity:.85; }
  .hamenu hr { border:0; border-top:1px solid color-mix(in srgb, var(--divider-color,#888) 50%, transparent); margin:4px 6px; }
  .sbox { position:relative; }
  .sbox input { width:180px; border:0; border-radius:999px; padding:6px 12px; font:inherit; color:inherit; outline:none;
                background:color-mix(in srgb, var(--secondary-background-color,#222) 70%, transparent); }
  .hits { position:absolute; top:36px; left:0; width:300px; max-height:50vh; overflow:auto; display:none; z-index:5;
          border-radius:12px; padding:4px; }
  .hits.open { display:block; }
  .hit { padding:5px 8px; border-radius:8px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .hit:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 25%, transparent); }
  .hit .sw { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
  .hit .k { opacity:.5; font-size:12px; margin-left:4px; }
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
    .sbox input { width:110px; }
    .top { max-width:calc(100% - 24px) !important; }
  }
  .top { max-width:calc(100% - min(460px, 44vw) - 36px); flex-wrap:wrap; }
  .top:has(~ .chat.hidden) { max-width:calc(100% - 24px); }
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
      lsSet(LS_LEFT, "0");               // back on membrane: fresh starts land here again
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

    _toggleMenu(open) {
      open = open == null ? !this._menu.classList.contains("open") : open;
      if (open) this._paintMenu();
      this._menu.classList.toggle("open", open);
      this._haBtn.setAttribute("aria-expanded", String(open));
      this._haBtn.classList.toggle("on", open);
    }

    _paintMenu() {
      const h = this._hass || {};
      let order = [], hidden = [];
      try { order = JSON.parse(lsGet("sidebarPanelOrder", "[]")) || []; } catch (e) { /* HA's own key */ }
      try { hidden = JSON.parse(lsGet("sidebarHiddenPanels", "[]")) || []; } catch (e) { /* HA's own key */ }
      const self = (this._panel && this._panel.url_path) || "membrane";
      const items = menuEntries(h.panels, order, hidden, self);
      const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      const row = (e) => '<a role="menuitem" href="/' + esc(e.path) + '" data-path="' + esc(e.path) + '"><ha-icon icon="' +
        esc(e.icon) + '"></ha-icon><span>' + esc(e.title) + "</span></a>";
      const main = items.filter((e) => !TAIL.includes(e.path)), tail = items.filter((e) => TAIL.includes(e.path));
      this._menu.innerHTML = main.map(row).join("") + (tail.length ? "<hr>" + tail.map(row).join("") : "");
    }

    /** Leave the field for an HA page: HA's sidebar comes back, and the next
     * fresh start stays in HA (until membrane is opened again). */
    _leaveTo(path) {
      lsSet(LS_LEFT, "1");
      this._toggleMenu(false);
      this._dock("restore");
      history.pushState(null, "", path);
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
          <button class="ha" title="Home Assistant: its dashboards and pages" aria-haspopup="menu" aria-expanded="false">☰ HA</button>
          <button class="me" title="What the camera follows: activity (whichever mind is working, else where the field changes) · seat · you">◎ activity</button>
          <button class="chatbtn" title="Show / hide the chat">💬</button>
          <button class="start" title="Open Home Assistant here (this browser)">☆</button>
          <span class="sbox"><input class="q" placeholder="search entities…" autocomplete="off"><div class="hits glass"></div></span>
          <span class="status"></span>
        </div>
        <nav class="hamenu glass" role="menu"></nav>
        <div class="chat glass"><ai-terminal-panel overlay></ai-terminal-panel></div>
        <div class="card glass"><div class="t"></div><div class="k"></div>
          <div class="row"><button class="ask">Ask about it</button><button class="go">Fly there</button><button class="x">×</button></div></div>
        <div class="tip glass"></div>`;
      this.shadowRoot.append(s, root);
      const $ = (q) => root.querySelector(q);
      this._host3d = $(".field"); this._msgEl = $(".msg"); this._statusEl = $(".status");
      this._chatBox = $(".chat"); this._chat = $("ai-terminal-panel");
      this._card = $(".card"); this._tip = $(".tip");
      this._q = $(".q"); this._hits = $(".hits");
      this._q.addEventListener("input", () => this._showHits());
      this._q.addEventListener("focus", () => this._showHits());
      this._q.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { this._q.value = ""; this._showHits(); this._q.blur(); }
        else if (e.key === "Enter") { const h = this._hits.querySelector(".hit"); if (h) h.click(); }
      });
      this._hits.addEventListener("click", (e) => {
        const h = e.target.closest(".hit"); if (!h) return;
        this._pickHit(h.dataset.id);
      });
      root.addEventListener("pointerdown", (e) => { if (!e.composedPath().includes(this._q.parentNode)) this._hits.classList.remove("open"); });
      if (this._hass) this._chat.hass = this._hass;
      if (this._narrow != null) this._chat.narrow = this._narrow;
      const chatOn = lsGet(LS_CHAT, "1") === "1";
      this._chatBox.classList.toggle("hidden", !chatOn);
      $(".chatbtn").classList.toggle("on", chatOn);
      this._menu = $(".hamenu"); this._haBtn = $(".ha");
      this._haBtn.addEventListener("click", () => this._toggleMenu());
      this._menu.addEventListener("click", (e) => {
        const a = e.target.closest("a[data-path]"); if (!a) return;
        e.preventDefault();
        this._leaveTo("/" + a.dataset.path);
      });
      // any click outside the menu closes it
      root.addEventListener("pointerdown", (e) => {
        if (!this._menu.classList.contains("open")) return;
        const path = e.composedPath();
        if (!path.includes(this._menu) && !path.includes(this._haBtn)) this._toggleMenu(false);
      });
      root.addEventListener("keydown", (e) => { if (e.key === "Escape") this._toggleMenu(false); });
      this._mode = FOLLOW_MODES.includes(lsGet(LS_FOLLOW, "activity")) ? lsGet(LS_FOLLOW, "activity") : "activity";
      this._modeBtn = $(".me"); this._modeBtn.textContent = "◎ " + this._mode;
      this._modeBtn.addEventListener("click", () => {
        this._mode = nextMode(this._mode); lsSet(LS_FOLLOW, this._mode); this._tracked = null;
        this._modeBtn.textContent = "◎ " + this._mode;
        this._lastTouch = 0; this._goal = null; this._aim(true);
      });
      $(".chatbtn").addEventListener("click", (e) => {
        const on = this._chatBox.classList.toggle("hidden") === false;
        e.currentTarget.classList.toggle("on", on);
        lsSet(LS_CHAT, on ? "1" : "0");
      });
      $(".start").addEventListener("click", () => {
        lsSet("defaultPanel", JSON.stringify((this._panel && this._panel.url_path) || "membrane"));
        this._statusEl.textContent = "this browser opens here now (Profile → Dashboard changes it)";
      });
      $(".x").addEventListener("click", () => { this._card.classList.remove("open"); if (this._v3) this._v3.select(null); this._select(null); });
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
          // the live layer for every signed-in user (the proxy relays it to them all)
          kindColor: hKindColor, isAdmin: true, maxFps: 30, labelIds: () => this._labelIds(),
          snapshot: "membrane-home-" + ((hass.user && hass.user.id) || "anon"),   // IndexedDB: the last view at once
          onStatus: (t) => { this._v3Txt = t; this._paintStatus(); },
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
        await this._aim(true);
        this._glide();
      } catch (e) {
        this._msgEl.style.display = ""; this._msgEl.textContent = "the field is unavailable\n" + ((e && e.message) || e);
        return;
      }
      clearInterval(this._follow);
      this._follow = setInterval(() => this._aim(false), ACTIVITY_EVERY_MS);
    }

    // ---------------------------------------------------------- the camera
    async _locateWorld(ids) {
      const v = this._v3;
      for (const id of ids) {
        v.locateCache.delete(id);                            // entities move
        const hit = await v._locate(id);
        if (hit && hit.p) return [(hit.p[0] - 0.5) * 1000, (hit.p[1] - 0.5) * 1000, (hit.p[2] - 0.5) * 1000];
      }
      return null;
    }

    /** The labels: what is tracked and what it interacts with (its edges),
     * and in activity mode whatever is active. */
    _labelIds() {
      const v = this._v3, out = new Set();
      if (!v) return out;
      if (v.sel) {
        out.add(v.sel);
        if (v.selNb && v.selNb.id === v.sel) for (const x of v.selNb.nb) out.add(x.id);
        return out;
      }
      if (this._mode === "activity") {
        for (const id of v.activeIds()) out.add(id);
      } else if (this._tracked) {
        out.add(this._tracked.id);
        for (const id of this._tracked.nb) out.add(id);
      }
      return out;
    }

    async _trackNb(ids) {
      const v = this._v3;
      if (!v || !v.pid) return;
      for (const id of ids) {
        try {
          const hit = await v.api.get("/membrane/map/locate?pid=" + v.pid + "&eid=" + encodeURIComponent(id) + "&nb=1");
          if (hit && hit.id) { this._tracked = { id: hit.id, nb: (hit.neighbours || []).map((x) => x.id) }; return; }
        } catch (e) { /* the next candidate */ }
      }
    }

    async _posOf(id) {
      const v = this._v3, s = v.slots.get(id);
      if (s != null) return [v.posA[s * 3], v.posA[s * 3 + 1], v.posA[s * 3 + 2]];
      return this._locateWorld([id]);
    }

    async _aim(force) {
      const v = this._v3; if (!v) return;
      if (this._selFollow) {                                // a selected node: the camera stays on it
        const id = this._selFollow, p = await this._posOf(id);
        if (p && this._selFollow === id) {
          this._goal = { p, r: this._goal && this._goal.sel ? this._goal.r : Math.min(v.orbit.r, 520), sel: true };
          this._say("following the selected " + id);
        }
        return;
      }
      const idle = performance.now() - this._lastTouch >= IDLE_BEFORE_FOLLOW_MS;
      if (!force && !idle) return;
      const now = performance.now();
      if (this._mode === "activity") {
        const a = v.activity();
        if (a) {
          this._goal = { p: a.p, r: a.kind === "mind" ? 520 : 900 };
          this._say(a.kind === "mind" ? "following " + a.agent + " · " + a.eid : "following the field's changes");
          this._msgEl.style.display = "none";
          return;
        }
        if (this._goal && !force) return;                   // quiet: stay where it last happened
      }
      // seat / you, and activity's first aim before anything happened
      if (!force && this._lastLocate && now - this._lastLocate < FOLLOW_EVERY_MS) return;
      this._lastLocate = now;
      const ids = this._mode === "you" ? [this._me] : SEAT_IDS;
      const p = await this._locateWorld(ids);
      if (p) this._trackNb(ids);
      if (p) {
        this._goal = { p, r: 600 };
        this._msgEl.style.display = "none";
        this._say(this._mode === "you" ? "following you" : (this._mode === "seat" ? "following the seat" : "waiting for activity · at the seat"));
        return;
      }
      if (this._mode === "you") {
        this._msgEl.style.display = "";
        this._msgEl.textContent = "you are not on the field yet — say something to membrane";
        setTimeout(() => { if (this._msgEl.textContent.startsWith("you are not")) this._msgEl.style.display = "none"; }, 6000);
      } else {
        this._msgEl.style.display = "none";
      }
    }

    _say(t) { this._followTxt = t; this._paintStatus(); }
    _paintStatus() {
      if (this._statusEl) this._statusEl.textContent = [this._followTxt, this._v3Txt].filter(Boolean).join("  ·  ");
    }

    _glide() {
      let last = performance.now();
      const id = this._glideId = (this._glideId || 0) + 1;     // one loop, ever
      const step = (t) => {
        if (!this._started || !this._v3 || id !== this._glideId) return;
        const dt = Math.min(0.1, (t - last) / 1000); last = t;
        const v = this._v3, g = this._goal;
        const idle = performance.now() - this._lastTouch >= IDLE_BEFORE_FOLLOW_MS;
        if (g && !v._flyAnim && (idle || g.sel)) {
          const k = 1 - Math.exp(-dt * (g.sel ? 2.2 : 0.9));
          for (let i = 0; i < 3; i++) v.orbit.target[i] += (g.p[i] - v.orbit.target[i]) * k;
          if (idle) {
            v.orbit.r += (g.r - v.orbit.r) * k * 0.6;
            const a = throughCenter(v.orbit.target);
            if (a) {
              const ka = 1 - Math.exp(-dt * 0.5);
              v.orbit.theta = angleStep(v.orbit.theta, a.theta, ka);
              v.orbit.phi += (a.phi - v.orbit.phi) * ka;
            }
          }
        }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    _select(m) {
      this._sel = m || null;
      this._selFollow = m ? m.id : null;
      if (!m) { this._card.classList.remove("open"); this._goal = null; this._aim(true); return; }
      this._aim(true);
      this._lastTouch = performance.now();
      this._card.querySelector(".t").textContent = m.label || m.id;
      this._card.querySelector(".k").textContent = (m.kind || "") + (m.mass != null ? "  ·  mass " + m.mass : "");
      this._card.classList.add("open");
    }

    _showHits() {
      const v = this._v3, q = this._q.value;
      const hits = v && q.trim() ? v.search(q) : [];
      const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      this._hitMeta = new Map(hits.map((n) => [n.id, n]));
      this._hits.innerHTML = hits.map((n) => '<div class="hit" data-id="' + esc(n.id) + '"><span class="sw" style="background:' +
        hKindColor(n.kind) + '"></span>' + esc(n.label) + '<span class="k">' + esc(n.kind) + "</span></div>").join("") ||
        (q.trim() && v ? '<div class="hit" data-id="' + esc(q.trim()) + '">go to ' + esc(q.trim()) + '<span class="k">(by id)</span></div>' : "");
      this._hits.classList.toggle("open", !!this._hits.innerHTML);
    }

    async _pickHit(id) {
      const v = this._v3; if (!v) return;
      this._hits.classList.remove("open");
      const m = (this._hitMeta && this._hitMeta.get(id)) || { id, label: id, kind: "" };
      this._lastTouch = performance.now();
      const ok = await v.flyTo(id);
      if (!ok) { this._say("not on the field: " + id); return; }
      this._select(m);
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
