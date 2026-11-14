// The page's bootstrap, the only JavaScript of the barest page besides the shared host (membrane/ui/fieldvm.js: the DOM mount, the event capture, Fetch/SSE, the clock).
// Everything the person sees is c1f: the view packs of this folder (shell_view.c1f, chat_view.c1f, field_view.c1f) run in the page's field engine (WebAssembly).
// This file starts the engine, hands it the seat's packs and facts (`GET /ui/fieldvm`), gives the shell its DOM mount and the page's <html> element, starts the clock, and says
// in plain text when any of that fails (nothing fails silently).
import { start } from './fieldvm.js';

const CLOCK_MS = 100;                       // the ONE clock of the page: the packs' rules compare time, so a fast tick only checks sooner (the Field view needs 100 ms)
const app = document.getElementById('app');

function say(text) {
  let p = document.getElementById('boot');
  if (!p) { p = document.createElement('p'); p.id = 'boot'; p.setAttribute('role', 'alert'); document.body.prepend(p); }
  p.setAttribute('role', 'alert');
  p.textContent = text;
}
window.addEventListener('error', (e) => say('The page hit an error: ' + (e.message || e.error)));
window.addEventListener('unhandledrejection', (e) => say('The page hit an error: ' + ((e.reason && e.reason.message) || e.reason)));

// packs that import another pack load after it: a pack that fails is tried again until a round loads none more (what is left is an error, shown by the shell)
function loadPacks(host, packs) {
  let todo = Object.entries(packs);
  for (let more = true; more && todo.length;) {
    const left = [];
    for (const [name, src] of todo) if (host.pack(name, src)) left.push([name, src]);
    more = left.length < todo.length;
    todo = left;
  }
}

(async () => {
  const r = await fetch('/ui/fieldvm', { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`the seat's /ui/fieldvm answered ${r.status}`);
  const snap = await r.json();
  const mount = document.createElement('div');
  mount.id = 'mount';
  const host = await start(mount);
  loadPacks(host, snap.packs || {});
  for (const e of snap.entities || []) host.feed(e.eid, e.cls, e.props, true);
  host.page.set('DomNode:page:html', document.documentElement);     // the theme: the shell writes `data-theme` on it (an allow-listed attribute)
  host.track_hidden();                                                // Conn:page.hidden: the polls pause in a hidden tab
  host.feed('Shell:main', 'Shell', { view: 'chat', sid: 'main', load_errors: host.errors }, true);
  host.feed('Shell:main', 'Shell', { open: true }, true);
  app.replaceChildren(mount);
  host.run_clock(CLOCK_MS);
  window.MEMBRANE_PAGE = host;                                        // for tests and the console
})().catch((e) => say('The page could not start: ' + (e && e.message ? e.message : e)));
