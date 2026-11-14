// Which renderer owns the chat pane (docs/ui-rebuild-plan.md section (d)): the legacy chat.js (the DEFAULT) or the view pack chat_view.c1f in the page's
// field engine. The choice, in this order: the page's `?ui=pack` / `?ui=legacy` (development), else the seat fact `ui_views` ({"chat": "pack"}) as
// `GET /ui/fieldvm` reports it in `views`, else legacy. A seat that cannot say (no door_fieldvm, an error) gets the legacy chat: the pack view never
// replaces it on a guess. The pack path loads the packs of that same snapshot into the page's VM (fieldvm.js `start`), feeds its entities (the Boot facts
// the view reads), and births `View:chat` {open: true}: the pack draws everything else. The shell only ticks the clock the polls read.
export function chooseChat(search, views) {
  const q = new URLSearchParams(search).get('ui');
  if (q === 'pack' || q === 'legacy') return q;
  return views && views.chat === 'pack' ? 'pack' : 'legacy';
}

async function snapshot() {
  const r = await fetch('/ui/fieldvm', { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`snapshot: ${r.status}`);
  return r.json();
}

/** parts: {log, form} of the legacy chat; section: the pane; startLegacy(): starts chat.js. Returns the mode shown. */
export async function mountChat(parts, section, startLegacy, search = location.search) {
  let snap = null;
  const want = new URLSearchParams(search).get('ui');
  if (want !== 'legacy') { try { snap = await snapshot(); } catch (e) { snap = null; } }
  const mode = chooseChat(search, snap && snap.views);
  if (mode !== 'pack' || !snap) { startLegacy(); return 'legacy'; }
  try {
    const m = await import('./fieldvm.js');
    const root = document.createElement('div');
    root.className = 'chatmount';
    root.id = 'chatmount';
    const host = await m.start(root);
    for (const [name, src] of Object.entries(snap.packs || {})) host.pack(name, src);
    if (host.errors.chat_view) throw new Error(host.errors.chat_view);
    for (const e of snap.entities || []) host.feed(e.eid, e.cls, e.props, true);
    parts.log.hidden = true; parts.form.hidden = true;
    section.append(root);
    host.feed('View:chat', 'View', { open: true }, true);
    const v = host.get('View:chat') || {};
    window.MEMBRANE_CHAT_VIEW = host;
    setInterval(() => { host.f.tick(host.now()); host.flush(); }, v.tick_ms);
    return 'pack';
  } catch (e) {
    console.warn('chat view pack:', e);
    parts.log.hidden = false; parts.form.hidden = false;
    startLegacy();
    return 'legacy';
  }
}
