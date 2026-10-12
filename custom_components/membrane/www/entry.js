/* The UI's entry under Home Assistant: the seat's name first (the labels and
 * the "Hey <name>" wake words are read when home.js / panel.js load), then the
 * module the panel asked for: entry.js?m=home.js | panel.js | field.js. */
try {
  const r = await fetch("/api/membrane/name", { credentials: "same-origin" });
  const c = r.ok ? await r.json() : {};
  if (c.name && !window.MEMBRANE_NAME) window.MEMBRANE_NAME = c.name;
  if (!window.MEMBRANE_ALIASES) window.MEMBRANE_ALIASES = c.aliases || [];
} catch (e) { /* the seat's name stays the default */ }
const m = new URL(import.meta.url).searchParams.get("m") || "home.js";
// the chat first: membrane home creates it, and wants it defined when it does
if (m === "home.js" && !customElements.get("ai-terminal-panel")) await import("/api/membrane/www/panel.js");
if (/^[a-z0-9]+\.js$/.test(m)) await import("/api/membrane/www/" + m);
