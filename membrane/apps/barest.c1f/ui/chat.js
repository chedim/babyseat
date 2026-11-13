// The chat pane: the one chat's messages are entities (membrane::Chat:message:<id>); the run that answers one is its TURN (:turn), which owns
// the answer (:turn:response) and the tools it ran (:turn:tool:<id>). Each turn is drawn as one group: the message, the answer, the tools
// folded under the answer. Entities persist, so a reload brings the history back. A running message is read from its event stream (GET /membrane/chat/message/<id>/events: the partial answer as it is written); only when the stream cannot be had (no EventSource, or it errors) is it polled every 700 ms until it is done.
import { queryAll, readEntity } from './mcp.js';

const PREFIX = 'membrane::Chat:message:';
const LIST_MS = 3000, ACTIVE_MS = 700;
const msgs = new Map();            // id -> {id, text, state, response, turn: {started, ended}, tools: [{eid, name, input}]}
const toolEids = new Map();        // id -> [tool eids]
const nodes = new Map();           // id -> <li>
const streams = new Map();         // id -> the open EventSource of a running message
let ui;

const done = (m) => m.state === 'done' || m.state === 'error';

async function getView(id) {
  const r = await fetch(`/membrane/chat/message/${encodeURIComponent(id)}`);
  return r.ok ? r.json() : null;
}

function short(v, n = 90) {
  const t = typeof v === 'string' ? v : JSON.stringify(v);
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
}

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

function draw(m) {
  let li = nodes.get(m.id);
  if (!li) { li = el('li', 'msg'); nodes.set(m.id, li); }
  const state = m.state || 'queued';
  li.dataset.state = state;
  const me = el('div', 'bubble me', m.text);
  const t = m.turn || {};
  const secs = t.started != null && t.ended != null ? Math.max(0, Math.round(t.ended - t.started)) : null;
  const who = el('div', 'who', state === 'queued' ? 'queued' : state === 'running' ? 'working' : 'seat' + (secs != null ? ' · ' + secs + ' s' : ''));
  const parts = [me, who];
  if (m.response) {
    // an error answer is shown as what to do next, not as a failure
    const b = el('div', state === 'error' ? 'bubble note' : 'bubble seat', m.response);
    parts.push(b);
  } else if (!done(m)) parts.push(el('div', 'bubble seat pending', state === 'queued' ? 'Waiting for a turn…' : 'Working on it…'));
  if (m.tools && m.tools.length) {
    const box = el('details', 'tools-run');
    box.append(el('summary', null, m.tools.length + (m.tools.length === 1 ? ' tool' : ' tools')));
    const ul = el('ul');
    for (const tl of m.tools) {
      const li2 = el('li');
      li2.append(el('code', 'tname', tl.name || tl.eid), el('span', 'tin', tl.input != null ? ' ' + short(tl.input) : ''));
      ul.append(li2);
    }
    box.append(ul);
    parts.push(box);
  }
  li.replaceChildren(...parts);
  if (!li.isConnected) ui.log.append(li);
}

function stick(fn) {
  const l = ui.log, near = l.scrollHeight - l.scrollTop - l.clientHeight < 80;
  fn();
  if (near) l.scrollTop = l.scrollHeight;
}

async function loadTools(m) {
  const eids = toolEids.get(m.id) || [];
  if ((m.tools || []).length >= eids.length) return;
  const have = new Set((m.tools || []).map((t) => t.eid));
  const fresh = [];
  for (const eid of eids) {
    if (have.has(eid)) continue;
    const e = await readEntity(eid).catch(() => null);
    if (e) fresh.push({ eid, name: String(e.props.name || '').replace(/^mcp__membrane__/, ''), input: e.props.input });
  }
  m.tools = [...(m.tools || []), ...fresh];
}

async function update(id) {
  const v = await getView(id);
  if (!v) return;
  const m = msgs.get(id) || { id };
  Object.assign(m, { text: v.text, state: v.state, response: v.response, turn: v.turn });
  msgs.set(id, m);
  if (done(m)) await loadTools(m);
  stick(() => draw(m));
  if (!done(m)) watch(id);
}

// a running message: one EventSource; each `message` event is the whole state (partial answer, tool names). The end of the stream is a `done` or
// `error` state: one poll then fetches the final view and the tools. An error of the stream drops it, and the message is polled again.
function watch(id) {
  if (streams.has(id) || typeof EventSource === 'undefined') return;
  const es = new EventSource(`/membrane/chat/message/${encodeURIComponent(id)}/events`);
  streams.set(id, es);
  const stop = () => { es.close(); streams.delete(id); };
  es.addEventListener('message', (ev) => {
    let v; try { v = JSON.parse(ev.data); } catch (e) { return; }
    const m = msgs.get(id) || { id };
    Object.assign(m, { text: v.text, state: v.state, response: v.response, turn: v.turn });
    if (!done(m)) m.tools = (v.tools || []).map((name) => ({ name }));
    msgs.set(id, m);
    if (done(m)) { stop(); m.tools = []; update(id).catch(() => {}); } else stick(() => draw(m));
  });
  es.onerror = stop;
}

async function list() {
  const { rows } = await queryAll('name~:' + PREFIX);
  const ids = [];
  toolEids.clear();
  for (const r of rows) {
    const rest = r.eid.slice(PREFIX.length), i = rest.indexOf(':');
    if (i < 0) ids.push(rest);
    else if (rest.slice(i + 1).startsWith('turn:tool:')) { const k = rest.slice(0, i); (toolEids.get(k) || toolEids.set(k, []).get(k)).push(r.eid); }
  }
  ids.sort();
  for (const id of ids) if (!msgs.has(id) || !done(msgs.get(id)) || (toolEids.get(id) || []).length > (msgs.get(id).tools || []).length) await update(id);
}

async function send(text) {
  const r = await fetch('/membrane/chat/message', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.id) throw new Error(j.error || `the seat answered ${r.status}`);
  msgs.set(j.id, { id: j.id, text, state: 'queued' });
  ui.log.scrollTop = ui.log.scrollHeight;
  stick(() => draw(msgs.get(j.id)));
  ui.log.scrollTop = ui.log.scrollHeight;
  watch(j.id);
}

export function startChat(parts) {
  ui = parts;
  const submit = async () => {
    const text = ui.input.value.trim();
    if (!text) return;
    ui.send.disabled = true;
    try { await send(text); ui.input.value = ''; }
    catch (e) { const li = el('li', 'msg'); li.append(el('div', 'bubble note', 'The message did not reach the seat: ' + e.message + '. It is still in the box; send it again when the seat is back.')); ui.log.append(li); setTimeout(() => li.remove(), 8000); }
    finally { ui.send.disabled = false; ui.input.focus(); }
  };
  ui.form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
  ui.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  const tickList = () => { (document.hidden ? Promise.resolve() : list().catch(() => {})).then(() => setTimeout(tickList, LIST_MS)); };
  const tickActive = () => {
    const live = [...msgs.values()].filter((m) => !done(m) && !streams.has(m.id));
    Promise.all(live.map((m) => update(m.id).catch(() => {}))).then(() => setTimeout(tickActive, ACTIVE_MS));
  };
  tickList(); tickActive();
}
