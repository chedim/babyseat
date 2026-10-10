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

// ---- VOICE + TILT (2026-10-10, the operator: "PTT and PTT Lock buttons that
// do stt into the chat + TTS button that makes membrane say its answers ...
// rotate the graph based on accelerometer/gyro"; laptops too)
const LS_TTS = "membrane.home.tts";
const STT_PATH = "/api/ai_terminal/stt";
const REC_MAX_S = 60, REC_MIN_S = 0.35, STT_RATE = 16000;
const TILT_GAIN = 1.5;

/** Float32 at inRate -> Int16 at outRate (box-averaged decimation). */
function downsample(f32, inRate, outRate) {
  const ratio = inRate / outRate, n = Math.floor(f32.length / ratio), out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * ratio), b = Math.min(f32.length, Math.floor((i + 1) * ratio));
    let sum = 0; for (let j = a; j < b; j++) sum += f32[j];
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

/** 16-bit mono PCM -> WAV bytes. */
function wavBytes(i16, rate) {
  const buf = new ArrayBuffer(44 + i16.length * 2), v = new DataView(buf);
  const str = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + i16.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
  v.setUint32(40, i16.length * 2, true);
  for (let i = 0; i < i16.length; i++) v.setInt16(44 + i * 2, i16[i], true);
  return new Uint8Array(buf);
}

/** Text -> chunks the TTS speaks one after another (sentences, <= max chars). */
function speechChunks(text, max = 220) {
  const out = [];
  for (const s of String(text || "").match(/[^.!?]+[.!?]*/g) || []) {
    let t = s.trim();
    while (t.length > max) { const cut = t.lastIndexOf(" ", max); const k = cut > 40 ? cut : max; out.push(t.slice(0, k)); t = t.slice(k).trim(); }
    if (t) out.push(t);
  }
  return out;
}

/** Device orientation -> (left-right, front-back) degrees for the screen's
 * current rotation (landscape swaps the axes). */
function tiltAxes(beta, gamma, angle) {
  const a = ((angle || 0) % 360 + 360) % 360;
  if (a === 90) return [beta, -gamma];
  if (a === 270) return [-beta, gamma];
  if (a === 180) return [-gamma, -beta];
  return [gamma, beta];
}

/** The orbit angles for a tilt relative to where it started. */
function tiltOrbit(base, lr, fb, gain = TILT_GAIN) {
  const d = Math.PI / 180;
  // the operator, 2026-10-10: "feels like it should be inverted" -- tilt
  // right turns the field right, tilt toward you looks from above
  return { theta: base.theta + (lr - base.lr) * d * gain,
           phi: Math.max(0.05, Math.min(Math.PI - 0.05, base.phi + (fb - base.fb) * d * gain)) };
}

const HomeLogic = { personSlug, menuEntries, downsample, wavBytes, speechChunks, tiltAxes, tiltOrbit, nextMode, FOLLOW_MODES, throughCenter, angleStep };
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
  .top .ha { font-size:18px; line-height:1; padding:5px 11px; }
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
  .voice { position:absolute; z-index:3; left:12px; bottom:12px; display:flex; gap:6px; padding:5px; border-radius:999px; }
  .voice button { width:46px; height:46px; border-radius:50%; border:0; font-size:20px; line-height:1; touch-action:none;
                  user-select:none; -webkit-user-select:none; -webkit-touch-callout:none;
                  background:color-mix(in srgb, var(--secondary-background-color,#222) 70%, transparent); }
  .voice button.on { background:color-mix(in srgb, var(--primary-color,#3477d6) 55%, transparent); }
  .voice button.rec { background:var(--error-color,#c0392b); color:#fff; animation:recpulse 1.1s ease-in-out infinite; }
  .voice button.busy { opacity:.6; }
  .vsay { position:absolute; left:0; bottom:calc(100% + 8px); max-width:min(360px, calc(100vw - 32px)); padding:6px 10px;
          border-radius:12px; font-size:13px; line-height:1.35; white-space:normal; display:none; pointer-events:none; }
  .vsay.show { display:block; }
  @keyframes recpulse { 50% { box-shadow:0 0 0 8px color-mix(in srgb, var(--error-color,#c0392b) 30%, transparent); } }
  .card { bottom:76px !important; }
  .top .tilt[hidden] { display:none; }
  .msg { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; z-index:1;
         pointer-events:none; opacity:.6; white-space:pre-line; text-align:center; padding:24px; }
  @media (max-width: 760px) {
    .chat { top:auto; left:8px; right:8px; bottom:8px; width:auto; height:56vh; height:56dvh; }
    .voice { bottom:calc(56dvh + 16px); }
    .chat.hidden ~ .voice { bottom:12px; }
    .card { bottom:calc(56dvh + 80px) !important; }
    .chat.hidden ~ .card { bottom:76px !important; }
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
      this._listenKeys(true);
      if (this._hass && !this._started) this._start();
      if (lsGet(LS_IMMERSIVE, "1") === "1") this._dock("always_hidden");
    }

    disconnectedCallback() {
      this._started = false;
      if (this._rec) this._recStop(false);
      this._hushTts(); this._tiltStop();
      this._listenKeys(false);
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
      this._menu.innerHTML = main.map(row).join("") + (tail.length ? "<hr>" + tail.map(row).join("") : "") +
        '<hr><a role="menuitem" href="#" data-logout="1"><ha-icon icon="mdi:logout"></ha-icon><span>Log out</span></a>';
    }

    /** Log out (HA's own 'hass-logout': it revokes the refresh token and goes
     * to the login page). Not a "left through the menu": the next login lands
     * on membrane again. */
    _logout() {
      lsSet(LS_LEFT, "0");
      this._toggleMenu(false);
      this._dock("restore");
      this.dispatchEvent(new CustomEvent("hass-logout", { bubbles: true, composed: true }));
    }

    /** Leave the field for an HA page: HA's sidebar comes back, and the next
     * fresh start stays in HA (until membrane is opened again). */
    _leaveTo(path) {
      // the profile page is where people log out: it is not "switching to HA"
      lsSet(LS_LEFT, path === "/profile" ? "0" : "1");
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
          <button class="ha" title="Home Assistant: its dashboards and pages" aria-haspopup="menu" aria-expanded="false" aria-label="Home Assistant menu">☰</button>
          <button class="me" title="What the camera follows: activity (whichever mind is working, else where the field changes) · seat · you">◎ activity</button>
          <button class="chatbtn" title="Show / hide the chat">💬</button>
          <button class="start" title="Open Home Assistant here (this browser)">☆</button>
          <button class="tilt" hidden title="Turn the view by tilting the device">📱</button>
          <span class="sbox"><input class="q" placeholder="search entities…" autocomplete="off"><div class="hits glass"></div></span>
          <span class="status"></span>
        </div>
        <nav class="hamenu glass" role="menu"></nav>
        <div class="chat glass"><ai-terminal-panel overlay></ai-terminal-panel></div>
        <div class="voice glass">
          <div class="vsay glass" role="status" aria-live="polite"></div>
          <button class="ptt" title="Hold to talk (or hold Space)" aria-label="Hold to talk">🎙</button>
          <button class="lock" title="Hands-free: tap to start, tap again to send" aria-label="Talk hands-free">🔒</button>
          <button class="tts" title="Speak membrane's answers" aria-label="Speak answers" aria-pressed="false">🔈</button>
        </div>
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
      this._wireVoice(root);
      this._wireTilt(root);
      if (this._hass) this._chat.hass = this._hass;
      if (this._narrow != null) this._chat.narrow = this._narrow;
      const chatOn = lsGet(LS_CHAT, "1") === "1";
      this._chatBox.classList.toggle("hidden", !chatOn);
      $(".chatbtn").classList.toggle("on", chatOn);
      this._menu = $(".hamenu"); this._haBtn = $(".ha");
      this._haBtn.addEventListener("click", () => this._toggleMenu());
      this._menu.addEventListener("click", (e) => {
        if (e.target.closest("a[data-logout]")) { e.preventDefault(); this._logout(); return; }
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
            const a = this._tiltAng ? null : throughCenter(v.orbit.target);
            if (a) {
              const ka = 1 - Math.exp(-dt * 0.5);
              v.orbit.theta = angleStep(v.orbit.theta, a.theta, ka);
              v.orbit.phi += (a.phi - v.orbit.phi) * ka;
            }
          }
        }
        if (this._tiltAng) {                               // the device's tilt turns the view
          const kt = 1 - Math.exp(-dt * 6);
          v.orbit.theta = angleStep(v.orbit.theta, this._tiltAng.theta, kt);
          v.orbit.phi += (this._tiltAng.phi - v.orbit.phi) * kt;
        }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    // ---------------------------------------------------------- voice
    _wireVoice(root) {
      const $ = (q) => root.querySelector(q);
      this._ptt = $(".ptt"); this._lockBtn = $(".lock"); this._ttsBtn = $(".tts");
      this._ttsOn = lsGet(LS_TTS, "0") === "1";
      this._paintTts();
      const down = (e) => { e.preventDefault(); this._unlockAudio(); this._ptt.setPointerCapture && this._ptt.setPointerCapture(e.pointerId); this._recStart("ptt"); };
      const up = () => { if (this._rec && this._rec.mode === "ptt") this._recStop(true); };
      this._ptt.addEventListener("pointerdown", down);
      this._ptt.addEventListener("pointerup", up);
      this._ptt.addEventListener("pointercancel", () => { if (this._rec && this._rec.mode === "ptt") this._recStop(false); });
      this._ptt.addEventListener("contextmenu", (e) => e.preventDefault());
      this._lockBtn.addEventListener("click", () => {
        this._unlockAudio();
        if (this._rec && this._rec.mode === "lock") this._recStop(true); else this._recStart("lock");
      });
      this._ttsBtn.addEventListener("click", () => {
        this._unlockAudio();
        this._ttsOn = !this._ttsOn; lsSet(LS_TTS, this._ttsOn ? "1" : "0"); this._paintTts();
        if (!this._ttsOn) this._hushTts();
      });
      // a laptop's PTT: hold Space anywhere but a text box
      const typing = (e) => e.composedPath().some((n) => n && (n.tagName === "TEXTAREA" || n.tagName === "INPUT" || n.isContentEditable));
      this._onKeyDown = (e) => { if (e.code === "Space" && !e.repeat && !typing(e) && this.isConnected) { e.preventDefault(); this._recStart("ptt"); } };
      this._onKeyUp = (e) => { if (e.code === "Space" && this._rec && this._rec.mode === "ptt" && !typing(e)) { e.preventDefault(); this._recStop(true); } };
      this._listenKeys(true);
      // voice in -> voice out: an answer to something SAID is spoken even with 🔈 off
      this._chat.addEventListener("ai-reply", (e) => {
        const said = this._voiceTurns > 0;
        if (said) this._voiceTurns -= 1;
        if ((this._ttsOn || said) && e.detail && e.detail.text) this._speak(e.detail.text);
      });
    }

    _listenKeys(on) {
      if (!this._onKeyDown || on === this._keysOn) return;
      this._keysOn = on;
      const f = on ? "addEventListener" : "removeEventListener";
      window[f]("keydown", this._onKeyDown); window[f]("keyup", this._onKeyUp);
    }

    /** The voice status, by the buttons (the top bar's status is hidden on phones). */
    _vsay(text, ms = 6000) {
      const el = this.shadowRoot.querySelector(".vsay"); if (!el) return;
      clearTimeout(this._vsayT);
      el.textContent = text || ""; el.classList.toggle("show", !!text);
      if (text && ms) this._vsayT = setTimeout(() => el.classList.remove("show"), ms);
      if (text) this._say(text);
    }

    /** Phones only play sound that a tap started: the first tap on a voice
     * button plays a blink of silence on THE player, which every reply reuses. */
    _unlockAudio() {
      if (this._player) return;
      const a = this._player = new Audio();
      try {
        const blob = new Blob([wavBytes(new Int16Array(800), 16000)], { type: "audio/wav" });
        a.src = URL.createObjectURL(blob);
        a.play().catch(() => {});
      } catch (e) { /* the fallback voice remains */ }
      try { if (window.speechSynthesis) window.speechSynthesis.speak(new SpeechSynthesisUtterance("")); } catch (e) { /* none */ }
    }

    _busyChat() {
      const st = this._chat && this._chat._chat && this._chat._chat.status;
      return !!(st && st.type && st.type !== "idle");
    }

    _paintTts() {
      this._ttsBtn.classList.toggle("on", this._ttsOn);
      this._ttsBtn.textContent = this._ttsOn ? "🔊" : "🔈";
      this._ttsBtn.setAttribute("aria-pressed", String(this._ttsOn));
    }

    async _recStart(mode) {
      if (this._rec || this._recBusy) return;
      this._hushTts();                                     // talking over it stops it
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        this._vsay("no microphone here (it needs https and a browser that allows it)"); return;
      }
      const rec = this._rec = { mode, chunks: [], t0: performance.now() };
      (mode === "ptt" ? this._ptt : this._lockBtn).classList.add("rec");
      try {
        rec.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        if (this._rec !== rec) { rec.stream.getTracks().forEach((t) => t.stop()); return; }   // released while asking
        const Ctx = window.AudioContext || window.webkitAudioContext;
        rec.ctx = new Ctx();
        rec.src = rec.ctx.createMediaStreamSource(rec.stream);
        rec.proc = rec.ctx.createScriptProcessor(4096, 1, 1);
        rec.proc.onaudioprocess = (ev) => rec.chunks.push(new Float32Array(ev.inputBuffer.getChannelData(0)));
        rec.src.connect(rec.proc); rec.proc.connect(rec.ctx.destination);
        rec.timer = setTimeout(() => { if (this._rec === rec) this._recStop(true); }, REC_MAX_S * 1000);
        this._vsay(mode === "lock" ? "listening… tap 🔒 again to send" : "listening… release to send", 0);
      } catch (e) {
        this._recCleanup(rec);
        this._vsay("the microphone is blocked: " + ((e && e.name) || e));
      }
    }

    _recCleanup(rec) {
      if (!rec) return;
      clearTimeout(rec.timer);
      try { rec.proc && rec.proc.disconnect(); rec.src && rec.src.disconnect(); } catch (e) { /* gone */ }
      if (rec.stream) rec.stream.getTracks().forEach((t) => t.stop());
      if (rec.ctx) rec.ctx.close().catch(() => {});
      this._ptt.classList.remove("rec"); this._lockBtn.classList.remove("rec");
      if (this._rec === rec) this._rec = null;
    }

    async _recStop(send) {
      const rec = this._rec; if (!rec) return;
      const rate = rec.ctx ? rec.ctx.sampleRate : 48000;
      this._recCleanup(rec);
      const n = rec.chunks.reduce((a, c) => a + c.length, 0);
      if (!send || n / rate < REC_MIN_S) { this._vsay(send ? "too short to hear" : ""); return; }
      const all = new Float32Array(n); let o = 0;
      for (const c of rec.chunks) { all.set(c, o); o += c.length; }
      let ss = 0; for (let i = 0; i < n; i += 4) ss += all[i] * all[i];
      if (Math.sqrt(ss / Math.max(1, n / 4)) < 0.004) { this._vsay("didn't hear anything (is the mic muted?)"); return; }   // silence: whisper would invent "You"
      const wav = wavBytes(downsample(all, rate, STT_RATE), STT_RATE);
      this._recBusy = true; this._ptt.classList.add("busy"); this._lockBtn.classList.add("busy");
      this._vsay("transcribing…", 0);
      try {
        const r = await this._hass.fetchWithAuth(STT_PATH, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || ("speech service " + r.status));
        const text = (d.text || "").trim();
        if (!text) { this._vsay("didn't catch that"); return; }
        const queued = this._busyChat();
        this._voiceTurns = (this._voiceTurns || 0) + 1;
        this._sendToChat(text);
        this._vsay("“" + text + "” — " + (queued ? "Argus is still on an earlier turn; yours is queued"
                                                  : "sent; Argus is thinking…"), 12000);
      } catch (e) {
        this._vsay("couldn't transcribe: " + ((e && e.message) || e));
      } finally {
        this._recBusy = false; this._ptt.classList.remove("busy"); this._lockBtn.classList.remove("busy");
      }
    }

    _sendToChat(text) {
      const sr = this._chat && this._chat.shadowRoot; if (!sr) return;
      const ta = sr.querySelector("textarea"), send = sr.querySelector(".send");
      if (!ta || !send) return;
      ta.value = text;
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      if (this._chatBox.classList.contains("hidden")) this.shadowRoot.querySelector(".chatbtn").click();
      send.click();
    }

    // ---------------------------------------------------------- speech out
    _ttsEngine() {
      const ids = Object.keys((this._hass && this._hass.states) || {}).filter((k) => k.startsWith("tts."));
      return ids.find((k) => k.includes("google_translate")) || ids[0] || null;
    }

    _hushTts() {
      this._ttsGen = (this._ttsGen || 0) + 1;
      if (this._audio) { try { this._audio.pause(); if (this._audio.onended) this._audio.onended(); } catch (e) { /* gone */ } this._audio = null; }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    }

    async _speak(text) {
      this._hushTts();
      const gen = this._ttsGen, engine = this._ttsEngine();
      for (const chunk of speechChunks(text)) {
        if (gen !== this._ttsGen || !this._ttsOn) return;
        try {
          if (!engine) throw new Error("no tts entity");
          const r = await this._hass.callApi("POST", "tts_get_url", { engine_id: engine, message: chunk });
          if (gen !== this._ttsGen) return;
          await new Promise((res, rej) => {
            // THE unlocked player (phones refuse a fresh one); the PATH (same
            // origin): the url may be HA's http LAN address, which https can't play
            const a = this._audio = this._player || new Audio();
            a.onended = () => res();
            a.onerror = () => rej(new Error("audio failed"));
            a.src = r.path || r.url;
            a.play().catch(rej);                           // refused: the browser's voice instead
          });
        } catch (e) {
          if (gen !== this._ttsGen) return;
          if (!window.speechSynthesis) { this._vsay("can't play sound here: tap 🔊 once"); return; }
          await new Promise((res) => { const u = new SpeechSynthesisUtterance(chunk); u.onend = u.onerror = () => res(); window.speechSynthesis.speak(u); });
        }
      }
    }

    // ---------------------------------------------------------- tilt
    _wireTilt(root) {
      const btn = this._tiltBtn = root.querySelector(".tilt");
      const touch = (navigator.maxTouchPoints || 0) > 0 || window.matchMedia("(pointer: coarse)").matches;
      btn.hidden = !(typeof window.DeviceOrientationEvent !== "undefined" && touch);
      btn.addEventListener("click", () => (this._tiltOn ? this._tiltStop() : this._tiltStart()));
    }

    async _tiltStart() {
      const DOE = window.DeviceOrientationEvent;
      try { if (DOE && typeof DOE.requestPermission === "function" && (await DOE.requestPermission()) !== "granted") { this._say("motion access was refused"); return; } }
      catch (e) { this._say("motion access was refused"); return; }
      this._tiltOn = true; this._tiltBase = null; this._tiltBtn.classList.add("on");
      this._onTilt = (e) => {
        if (e.beta == null || e.gamma == null || !this._v3) return;
        const ang = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
        const [lr, fb] = tiltAxes(e.beta, e.gamma, ang);
        if (!this._tiltBase) this._tiltBase = { lr, fb, theta: this._v3.orbit.theta, phi: this._v3.orbit.phi };
        this._tiltAng = tiltOrbit(this._tiltBase, lr, fb);
      };
      window.addEventListener("deviceorientation", this._onTilt);
      setTimeout(() => { if (this._tiltOn && !this._tiltBase) { this._tiltStop(); this._say("no motion sensor answered"); } }, 2500);
      this._say("tilt to turn the view");
    }

    _tiltStop() {
      this._tiltOn = false; this._tiltAng = null; this._tiltBase = null;
      if (this._tiltBtn) this._tiltBtn.classList.remove("on");
      if (this._onTilt) window.removeEventListener("deviceorientation", this._onTilt);
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
