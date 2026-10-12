/* STANDALONE MODE'S HOST (2026-10-12): what Home Assistant's `hass` object gave
 * the panels, made by membrane itself. No HA: the page talks to its own seat
 * (same origin), the person is the name they typed once (kept in this
 * browser), everyone on the LAN is an admin, the voices are the browser's.
 * The seat stamps the speaker line from the X-Membrane-Speaker header. */
window.MEMBRANE_STANDALONE = true;

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

export function makeHost(name) {
  const who = encodeURIComponent(String(name || "guest").trim());
  return {
    user: { name, id: "lan:" + slug(name), is_admin: true },
    states: {},                                   // no HA tts.*: the page speaks with the browser's voices
    panels: {},
    standalone: true,
    fetchWithAuth(url, o = {}) {
      const headers = new Headers(o.headers || {});
      headers.set("X-Membrane-Speaker", who);
      return fetch(url, { ...o, headers, credentials: "same-origin" });
    },
    callApi() { return Promise.reject(new Error("no Home Assistant in standalone mode")); },
    callService() { return Promise.reject(new Error("no Home Assistant in standalone mode")); },
  };
}
