// Which renderer owns the chat pane (docs/ui-rebuild-plan.md section (d)): the legacy chat.js (the DEFAULT) or the view pack chat_view.c1f in the page's
// field engine. The choice, in this order: the page's `?ui=pack` / `?ui=legacy` (development), else the seat fact `ui_views` ({"chat": "pack"}) as
// `GET /ui/fieldvm` reports it in `views`, else legacy. A seat that cannot say (no door_fieldvm, an error) gets the legacy chat: the pack view never
// replaces it on a guess. The pack path loads the packs of that same snapshot into the page's VM (fieldvm.js `start`), feeds its entities (the Boot facts
// the view reads), and births `View:chat` {open: true}: the pack draws everything else. The shell only runs the clock the polls read (`run_clock`) and says whether the tab is hidden (`track_hidden`).
export function chooseView(search, views, name) {
  const q = new URLSearchParams(search).get('ui');
  if (q === 'pack' || q === 'legacy') return q;
  return views && views[name] === 'pack' ? 'pack' : 'legacy';
}
export const chooseChat = (search, views) => chooseView(search, views, 'chat');

async function snapshot() {
  const r = await fetch('/ui/fieldvm', { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`snapshot: ${r.status}`);
  return r.json();
}

// ONE field engine in the page for every pack view (the chat, the field): the snapshot is fetched once, the VM started once, the packs and the seat's facts fed once; each view
// then births its own entity (`View:chat`, `Scene:field`). The clock is ONE timer: it runs at the fastest period any mounted view asks for (a view's rules compare time, so a faster tick only checks sooner).
let shared = null;
function pageHost() {
  if (!shared) {
    shared = (async () => {
      const snap = await snapshot();
      const m = await import('./fieldvm.js');
      const root = document.createElement('div');
      const host = await m.start(root);
      for (const [name, src] of Object.entries(snap.packs || {})) host.pack(name, src);
      for (const e of snap.entities || []) host.feed(e.eid, e.cls, e.props, true);
      host.track_hidden();                    // Conn:page.hidden: the polls pause in a hidden tab
      return { snap, host };
    })();
    shared.catch(() => { shared = null; });
  }
  return shared;
}
function runClock(host, ms) { host.clockMs = Math.min(host.clockMs || Infinity, ms); host.run_clock(host.clockMs); }

/** parts: {log, form} of the legacy chat; section: the pane; startLegacy(): starts chat.js. Returns the mode shown. */
export async function mountChat(parts, section, startLegacy, search = location.search) {
  let st = null;
  const want = new URLSearchParams(search).get('ui');
  if (want !== 'legacy') { try { st = await pageHost(); } catch (e) { st = null; } }
  const mode = chooseView(search, st && st.snap.views, 'chat');
  if (mode !== 'pack' || !st) { startLegacy(); return 'legacy'; }
  try {
    const { host } = st;
    if (host.errors.chat_view) throw new Error(host.errors.chat_view);
    const root = document.createElement('div');
    root.className = 'chatmount';
    root.id = 'chatmount';
    parts.log.hidden = true; parts.form.hidden = true;
    section.append(root);
    host.root = root;                       // the DOM mount of the chat view's DomNodes (a node with no `parent`)
    host.feed('View:chat', 'View', { open: true }, true);
    const v = host.get('View:chat') || {};
    window.MEMBRANE_CHAT_VIEW = host;
    runClock(host, v.tick_ms);
    return 'pack';
  } catch (e) {
    console.warn('chat view pack:', e);
    parts.log.hidden = false; parts.form.hidden = false;
    startLegacy();
    return 'legacy';
  }
}

/** The field view: `section` is the graph pane, `legacy` the elements of the legacy graph (hidden when the pack draws). Returns 'pack' or 'legacy'. */
export async function mountField(section, legacy, search = location.search) {
  let st = null;
  const want = new URLSearchParams(search).get('ui');
  if (want !== 'legacy') { try { st = await pageHost(); } catch (e) { st = null; } }
  const mode = chooseView(search, st && st.snap.views, 'field');
  if (mode !== 'pack' || !st) return 'legacy';
  try {
    const { host } = st;
    if (host.errors.field_view) throw new Error(host.errors.field_view);
    const root = document.createElement('div');
    root.className = 'fieldmount';
    root.id = 'fieldmount';
    for (const el of legacy) el.hidden = true;
    section.append(root);
    host.page.set('DomNode:mount:field', root);        // the field view's root DomNode names this element as its parent (the host's page elements: a pack's `parent` may be one)
    host.feed('Scene:field', 'Scene', { open: true }, true);
    window.MEMBRANE_FIELD_VIEW = host;
    runClock(host, (host.get('Scene:field') || {}).tick_ms || 100);
    return 'pack';
  } catch (e) {
    console.warn('field view pack:', e);
    for (const el of legacy) el.hidden = false;
    return 'legacy';
  }
}
