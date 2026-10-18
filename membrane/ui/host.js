/* STANDALONE MODE'S HOST (2026-10-12): what Home Assistant's `hass` object gave
 * the panels, made by membrane itself. No HA: the page talks to its own seat
 * (same origin), the person is the name they typed once (kept in this
 * browser), everyone on the LAN is an admin, the voices are the browser's.
 * The seat stamps the speaker line from the X-Membrane-Speaker header. */
window.MEMBRANE_STANDALONE = true;
// the page's voice log goes to the seat: <data dir>/page-voice.log (ui_serve.py)
window.MEMBRANE_SPEECH_LOG = (who, what, text) => {
  const line = JSON.stringify({ t: new Date().toISOString(), who, what, text, ua: navigator.userAgent.split(" ").pop() });
  try { navigator.sendBeacon("/ui/log", new Blob([line], { type: "application/json" })); } catch (e) { /* best effort */ }
};

const LS_NAME = "membrane-standalone-person";

export function slug(name) {
  return String(name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "guest";
}

export function savedName() {
  try { return (localStorage.getItem(LS_NAME) || "").trim(); } catch (e) { return ""; }
}

export function saveName(name) {
  try { localStorage.setItem(LS_NAME, String(name || "").trim()); } catch (e) { /* private window: asked again */ }
}

/* JWT MODE (docs/auth.md): the seat answers 401 until the page holds a token.
 * The person pastes theirs once; POST /ui/login turns it into an HttpOnly
 * cookie the page can't read (the script never keeps the token itself), and
 * the request that hit the 401 is sent again. */
let _login = null;

export function askToken(seat, failed) {
  return new Promise((done) => {
    const box = document.createElement("div");
    box.className = "who";
    box.innerHTML = `<form><h1></h1><p>This seat asks for a token. Paste the one you were given
      (<code>membrane token mint</code> makes them).</p>
      <input name="t" type="password" autocomplete="off" placeholder="Token" required>
      <button>Sign in</button><div class="note" hidden>That token was not accepted.</div></form>`;
    box.querySelector("h1").textContent = seat ? "Hi, I'm " + seat + "." : "Hi.";
    if (failed) box.querySelector(".note").hidden = false;
    box.querySelector("form").addEventListener("submit", (e) => {
      e.preventDefault();
      const t = box.querySelector("input").value.trim();
      if (!t) return;
      box.remove(); done(t);
    });
    document.body.appendChild(box);
    box.querySelector("input").focus();
  });
}

export function login(seat) {
  // one prompt at a time, however many requests hit the 401 together
  if (!_login) {
    _login = (async () => {
      let failed = false;
      for (;;) {
        const token = await askToken(seat || window.MEMBRANE_NAME, failed);
        const r = await fetch("/ui/login", { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
        if (r.ok) return (await r.json()).who;
        failed = true;
      }
    })().finally(() => { _login = null; });
  }
  return _login;
}

export function makeHost(name) {
  const who = encodeURIComponent(String(name || "guest").trim());
  return {
    user: { name, id: "lan:" + slug(name), is_admin: true },
    states: {},                                   // no HA tts.*: the page speaks with the browser's voices
    panels: {},
    standalone: true,
    async fetchWithAuth(url, o = {}) {
      const headers = new Headers(o.headers || {});
      headers.set("X-Membrane-Speaker", who);     // open mode's speaker; JWT mode ignores it
      const r = await fetch(url, { ...o, headers, credentials: "same-origin" });
      if (r.status !== 401 || window.MEMBRANE_AUTH !== "jwt") return r;
      await login();                              // the cookie expired or was cleared: ask, then once more
      return fetch(url, { ...o, headers, credentials: "same-origin" });
    },
    callApi() { return Promise.reject(new Error("no Home Assistant in standalone mode")); },
    callService() { return Promise.reject(new Error("no Home Assistant in standalone mode")); },
  };
}
