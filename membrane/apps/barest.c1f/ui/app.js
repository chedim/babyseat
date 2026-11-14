// The barest seat's page: a graph viewer of the field's entities (left) and the one chat (right).
// Plain static JavaScript served from the app folder, no build step, no dependencies. (A later piece ports it to c1f view packs.)
import { queryAll, readEntity } from './mcp.js';
import { Graph, classColor } from './graph.js';
import { startChat } from './chat.js';
import { mountChat } from './chatmount.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 3000;
const READS_PER_POLL = 6;          // entities whose properties are read for reference edges, oldest read first
const props = new Map();            // eid -> {at, cls, props}
let rows = [];
let more = false;
let selected = null;
let listOn = false;

const graph = new Graph($('canvas'), { onSelect: (id) => { selected = id; showSide(); } });

// ---- edges: ownership from the eid, references from the properties ----
function owner(eid, ids) {
  for (let i = eid.lastIndexOf(':'); i > 0; i = eid.lastIndexOf(':', i - 1)) {
    const up = eid.slice(0, i);
    if (!up.endsWith(':') && ids.has(up)) return up;
  }
  return null;
}

function refsOf(v, ids, out, depth = 0) {
  if (typeof v === 'string') { if (ids.has(v)) out.add(v); }
  else if (v && typeof v === 'object' && depth < 5) {
    if (typeof v.$ref === 'string' && ids.has(v.$ref)) out.add(v.$ref);
    for (const x of Array.isArray(v) ? v : Object.values(v)) refsOf(x, ids, out, depth + 1);
  }
}

function computeEdges() {
  const ids = new Set(rows.map((r) => r.eid)), edges = [], seen = new Set();
  const add = (a, b, kind) => { const k = a + '\n' + b; if (a !== b && !seen.has(k) && !seen.has(b + '\n' + a)) { seen.add(k); edges.push({ a, b, kind }); } };
  for (const r of rows) {
    const o = owner(r.eid, ids);
    if (o) add(o, r.eid, 'own');
  }
  for (const r of rows) {
    const p = props.get(r.eid);
    if (!p) continue;
    const out = new Set();
    refsOf(p.props, ids, out);
    for (const t of out) add(r.eid, t, 'ref');
  }
  return edges;
}

// ---- the filter, the legend, the list ----
function visible() {
  const f = $('filter').value.trim().toLowerCase();
  return rows.filter((r) => !f || r.eid.toLowerCase().includes(f));
}

function render() {
  const vis = visible(), hide = new Set(rows.filter((r) => !vis.includes(r)).map((r) => r.eid));
  graph.setHidden(hide);
  $('count').textContent = (vis.length === rows.length ? `${rows.length} entities` : `${vis.length} of ${rows.length} entities`) + (more ? ' (first ' + rows.length + ')' : '');
  $('empty').hidden = rows.length > 0;
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const classes = [...new Set(rows.map((r) => r.cls))].sort();
  $('legend').replaceChildren(...classes.map((c) => {
    const s = document.createElement('span');
    s.className = 'chip';
    s.style.setProperty('--dot', classColor(c, dark));
    s.textContent = c;
    return s;
  }));
  if (listOn) renderList(vis);
}

function renderList(vis) {
  $('list').replaceChildren(...vis.map((r) => {
    const li = document.createElement('li'), b = document.createElement('button');
    b.type = 'button'; b.textContent = r.eid; b.title = r.cls;
    b.style.setProperty('--dot', classColor(r.cls, matchMedia('(prefers-color-scheme: dark)').matches));
    if (r.eid === selected) b.setAttribute('aria-current', 'true');
    b.addEventListener('click', () => graph.select(r.eid));
    li.append(b);
    return li;
  }));
}

// ---- the side panel: the selected entity's properties ----
function showSide() {
  const side = $('side');
  if (!selected) { side.hidden = true; return; }
  side.hidden = false;
  $('side-eid').textContent = selected;
  const row = rows.find((r) => r.eid === selected);
  $('side-cls').textContent = row ? row.cls : '';
  const id = selected;
  readEntity(id).then((e) => { if (e) props.set(id, { at: Date.now(), ...e }); fillSide(); }).catch(() => fillSide());
  fillSide();
  if (listOn) renderList(visible());
}

function fillSide() {
  if (!selected) return;
  const p = props.get(selected), dl = $('side-props'), ids = new Set(rows.map((r) => r.eid));
  dl.replaceChildren();
  if (!p) { dl.textContent = 'Reading…'; return; }
  for (const [k, v] of Object.entries(p.props)) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = k;
    if (typeof v === 'string' && ids.has(v)) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'link'; b.textContent = v;
      b.addEventListener('click', () => graph.select(v)); dd.append(b);
    } else {
      const pre = document.createElement('pre');
      const t = typeof v === 'string' ? v : JSON.stringify(v, null, 1);
      pre.textContent = t.length > 1500 ? t.slice(0, 1500) + '…' : t;
      dd.append(pre);
    }
    dl.append(dt, dd);
  }
  if (!Object.keys(p.props).length) dl.textContent = 'No stored properties yet.';
  const links = new Set();
  const edges = graph.edges.filter((e) => e.a === selected || e.b === selected);
  for (const e of edges) links.add(e.a === selected ? e.b : e.a);
  $('side-links').replaceChildren(...[...links].map((t) => {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'link'; b.textContent = t;
    b.addEventListener('click', () => graph.select(t)); return b;
  }));
}

// ---- the poll ----
let busy = false;
async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const q = $('showreq').checked ? '' : 'kind!:Request';
    const r = await queryAll(q);
    rows = r.rows; more = r.more;
    const live = new Set(rows.map((x) => x.eid));
    for (const k of [...props.keys()]) if (!live.has(k)) props.delete(k);
    const due = rows.filter((x) => x.eid !== selected).sort((a, b) => ((props.get(a.eid)?.at) || 0) - ((props.get(b.eid)?.at) || 0)).slice(0, READS_PER_POLL);
    if (selected) due.unshift({ eid: selected });
    await Promise.all(due.map(async (x) => { const e = await readEntity(x.eid).catch(() => null); if (e) props.set(x.eid, { at: Date.now(), ...e }); }));
    graph.setData(rows, computeEdges());
    render();
    if (selected) fillSide();
    setConn(true);
  } catch (e) {
    setConn(false, e.message);
  } finally { busy = false; }
}

function setConn(ok, why) {
  const c = $('conn');
  c.textContent = ok ? 'live' : 'reconnecting';
  c.title = ok ? 'The seat answered the last read' : String(why || '');
  c.dataset.ok = ok ? '1' : '0';
}

function loop() { if (!document.hidden) refresh(); setTimeout(loop, POLL_MS); }

$('refresh').addEventListener('click', refresh);
$('fit').addEventListener('click', () => graph.fit());
$('showreq').addEventListener('change', refresh);
$('filter').addEventListener('input', render);
$('side-close').addEventListener('click', () => graph.select(null));
$('listbtn').addEventListener('click', () => {
  listOn = !listOn;
  $('listbtn').setAttribute('aria-pressed', String(listOn));
  $('list').hidden = !listOn;
  if (listOn) renderList(visible());
});

// the chat pane: the legacy chat.js is the default; the view pack chat_view.c1f only when the seat fact `ui_views` or `?ui=pack` says so (ui/chatmount.js)
const chatParts = { log: $('log'), form: $('composer'), input: $('input'), send: $('send') };
mountChat(chatParts, document.querySelector('.pane.chat'), () => startChat(chatParts));
refresh().then(() => graph.fit());
setTimeout(loop, POLL_MS);
