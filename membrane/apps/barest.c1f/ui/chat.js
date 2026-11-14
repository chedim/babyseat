// The chat pane: the one chat's messages are entities (membrane::Chat:message:<id>); the run that answers one is its TURN (:turn), which owns
// the answer (:turn:response) and the tools it ran (:turn:tool:<id>). Each turn is drawn as one group: the message, the answer, the tools
// folded under the answer. Entities persist, so a reload brings the history back: ONE request (GET /membrane/chat/messages) answers the newest 20 turns with their tools, and 'Load older turns' fetches the next page. A running message is read from its event stream (GET /membrane/chat/message/<id>/events: the partial answer as it is written); only when the stream cannot be had (no EventSource, or it errors) is it polled every 700 ms until it is done.
const PAGE = 20, POLL = 10;
const LIST_MS = 3000, ACTIVE_MS = 700;
const msgs = new Map();            // id -> {id, text, state, response, turn: {started, ended}, tools: [{name, input}], full}
const nodes = new Map();           // id -> <li>
const streams = new Map();         // id -> the open EventSource of a running message
let ui, older = null, olderLi = null;   // older: the cursor of the next page of older turns (null: none left)

const done = (m) => m.state === 'done' || m.state === 'error';

// ONE request answers a page of turns: the newest `limit`, or those before the message `before`; each carries its tools (GET /membrane/chat/messages)
async function getPage(limit, before) {
  const r = await fetch(`/membrane/chat/messages?limit=${limit}` + (before ? `&before=${encodeURIComponent(before)}` : ''));
  if (!r.ok) throw new Error(`the seat answered ${r.status}`);
  return r.json();
}

function short(v, n = 90) {
  const t = typeof v === 'string' ? v : JSON.stringify(v);
  return t.length > n ? t.slice(0, n - 1) + '…' : t;
}

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

function draw(m, anchor) {
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
  if (!li.isConnected) { if (anchor) ui.log.insertBefore(li, anchor); else ui.log.append(li); }
}

function stick(fn) {
  const l = ui.log, near = l.scrollHeight - l.scrollTop - l.clientHeight < 80;
  fn();
  if (near) l.scrollTop = l.scrollHeight;
}

// a view of a message (a page row, or an event of the stream) into the message the page holds; a page row brings the finished tools
function apply(v) {
  const m = msgs.get(v.id) || { id: v.id };
  Object.assign(m, { text: v.text, state: v.state, response: v.response, turn: v.turn });
  if (v.tool_calls) { m.tools = v.tool_calls; m.full = true; }
  else if (!m.full) m.tools = (v.tools || []).map((name) => ({ name }));
  msgs.set(v.id, m);
  return m;
}

// the newest turns again: a message unknown or not yet finished is drawn (and a running one watched); a finished one is left alone
async function refresh() {
  const { messages } = await getPage(POLL);
  for (const v of messages) {
    const had = msgs.get(v.id);
    if (had && done(had) && had.full) continue;
    const m = apply(v);
    stick(() => draw(m));
    if (!done(m)) watch(m.id);
  }
}

// a running message: one EventSource; each `message` event is the whole state (partial answer, tool names). The end of the stream is a `done` or
// `error` state: one refresh then brings the finished tools. An error of the stream drops it, and refresh keeps polling the message.
function watch(id) {
  if (streams.has(id) || typeof EventSource === 'undefined') return;
  const es = new EventSource(`/membrane/chat/message/${encodeURIComponent(id)}/events`);
  streams.set(id, es);
  const stop = () => { es.close(); streams.delete(id); };
  es.addEventListener('message', (ev) => {
    let v; try { v = JSON.parse(ev.data); } catch (e) { return; }
    const m = apply(v);
    if (done(m)) { stop(); stick(() => draw(m)); refresh().catch(() => {}); } else stick(() => draw(m));
  });
  es.onerror = stop;
}

// the first load: the newest turns, drawn oldest first
async function load() {
  const j = await getPage(PAGE);
  older = j.more ? j.next : null;
  for (const v of j.messages) { const m = apply(v); draw(m); if (!done(m)) watch(m.id); }
  ui.log.scrollTop = ui.log.scrollHeight;
  showOlder();
}

// the button 'Load older turns' is the first item of the log while older turns exist; a click fetches the next page and puts it above the loaded ones
function showOlder() {
  if (!older) { if (olderLi) { olderLi.remove(); olderLi = null; } return; }
  if (olderLi) return;
  olderLi = el('li', 'older');
  const b = el('button', null, 'Load older turns');
  b.type = 'button';
  b.addEventListener('click', async () => {
    b.disabled = true;
    try { await loadOlder(); } catch (e) { b.textContent = 'Load older turns (the seat did not answer: try again)'; }
    b.disabled = false;
  });
  olderLi.append(b);
  ui.log.prepend(olderLi);
}

async function loadOlder() {
  const j = await getPage(PAGE, older);
  const l = ui.log, before = l.scrollHeight, top = l.scrollTop;
  let anchor = olderLi ? olderLi.nextSibling : l.firstChild;
  for (const v of j.messages.slice().reverse()) { const m = apply(v); draw(m, anchor); anchor = nodes.get(m.id); if (!done(m)) watch(m.id); }
  older = j.more ? j.next : null;
  if (olderLi) olderLi.remove();
  olderLi = null;
  showOlder();
  l.scrollTop = top + (l.scrollHeight - before);   // the turns the person was reading stay where they were
}

async function send(text) {
  const r = await fetch('/membrane/chat/message', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.id) throw new Error(j.error || `the seat answered ${r.status}`);
  msgs.set(j.id, { id: j.id, text, state: 'queued', tools: [] });
  ui.log.scrollTop = ui.log.scrollHeight;
  stick(() => draw(msgs.get(j.id)));
  ui.log.scrollTop = ui.log.scrollHeight;
  watch(j.id);
}

export function startChat(parts) {
  ui = parts;
  let sending = false;   // one POST at a time: a slow seat must not get the same message twice (an Enter and a click, a held key)
  const submit = async () => {
    if (sending) return;
    const text = ui.input.value.trim();
    if (!text) return;
    sending = true;
    ui.send.disabled = true;
    ui.input.disabled = true;
    try { await send(text); ui.input.value = ''; }
    catch (e) { const li = el('li', 'msg'); li.append(el('div', 'bubble note', 'The message did not reach the seat: ' + e.message + '. It is still in the box; send it again when the seat is back.')); ui.log.append(li); setTimeout(() => li.remove(), 8000); }
    finally { sending = false; ui.send.disabled = false; ui.input.disabled = false; ui.input.focus(); }
  };
  ui.form.addEventListener('submit', (e) => { e.preventDefault(); submit(); });
  ui.input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
  });
  const tickList = () => { (document.hidden ? Promise.resolve() : refresh().catch(() => {})).then(() => setTimeout(tickList, LIST_MS)); };
  // a running message the stream does not carry (no EventSource, or it dropped) is polled through the newest page
  const tickActive = () => {
    const live = [...msgs.values()].some((m) => !done(m) && !streams.has(m.id));
    (live && !document.hidden ? refresh().catch(() => {}) : Promise.resolve()).then(() => setTimeout(tickActive, ACTIVE_MS));
  };
  load().catch(() => {}).then(() => { setTimeout(tickList, LIST_MS); tickActive(); });
}
