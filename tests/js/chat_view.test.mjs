// The bare chat as a c1f VIEW PACK (membrane/apps/barest.c1f/chat_view.c1f, #1143), on the wasm engine in Node: the page's VM with the packs `dom`,
// `fetch` and `chat_view`. The test plays the two things around the engine: the HOST (a DomNode event is a write: `value`, `key`, `submitted`,
// `clicked`; a `Fetch:<x>.request` is answered by a write of `request_result`, the way fieldvm.js does) and the CLOCK (`tick`). What is asserted is
// what the legacy chat (membrane/apps/barest.c1f/ui/chat.js) shows: who / bubbles / pending / tools, the composer, the double-submit guard,
// the failure note, the polls (legacy constants as facts) and "Load older turns".
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');
const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";
const FETCH = read('../../membrane/packs/fetch.c1f');
const PACK = read('../../membrane/apps/barest.c1f/chat_view.c1f');

const T0 = 1_000_000;
function world(facts) {
  const f = new Field();
  f.set_pack('dom', DOM); f.set_pack('fetch', FETCH); f.set_pack('chat_view', PACK);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'chat_view loads once dom and fetch are there');
  const w = { f, t: T0, log: [] };
  w.flush = () => { const o = JSON.parse(f.drain()); w.log.push(...o.writes); return o.writes; };
  w.get = (e) => JSON.parse(f.get(e));
  w.write = (e, p, v) => { f.write(e, p, JSON.stringify(v ?? null), w.t); return w.flush(); };
  w.feed = (e, cls, props) => { if (f.get(e) === 'null') f.load(e, cls, '{}'); for (const [k, v] of Object.entries(props)) f.write(e, k, JSON.stringify(v ?? null), w.t); w.flush(); };
  w.tick = (dt) => { w.t += dt; f.tick(w.t); w.flush(); };
  // the host's Fetch: what the pack asked on `Fetch:<x>`, and the receipt back
  w.asked = (x) => (w.get('Fetch:' + x) || {}).request;
  w.reply = (x, status, body) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'ok', rc: 0, head: String(status), http_status: status, headers: {}, body: typeof body === 'string' ? body : JSON.stringify(body), started: w.t, finished: w.t, ms: 1 });
  w.fail = (x, why) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'error', rc: 1, head: why, started: w.t, finished: w.t, ms: 1 });
  w.reqs = (x) => w.log.filter((e) => e.eid === 'Fetch:' + x && e.prop === 'request' && !e.born);
  w.facts = (p) => { if (p) w.feed('Boot:seat', 'Boot', p); };
  w.open = () => w.feed('View:chat', 'View', { open: true });
  w.n = (k) => w.get('DomNode:chat:' + k) || {};
  return w;
}
const short = (v, n = 90) => { const t = typeof v === 'string' ? v : JSON.stringify(v); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
const MSGS = [
  { id: '1000-1', text: 'hello', state: 'done', response: 'hi there', turn: { state: 'done', started: 100, ended: 105.4 }, tools: ['a', 'b'],
    tool_calls: [{ name: 'mcp__membrane__entity_read', input: { spec: 'X', prop: 'y' } }, { name: 'Bash', input: 'x'.repeat(120) }] },
  { id: '1001-2', text: 'again', state: 'running', response: null, turn: { state: 'running', started: 110 }, tools: ['a'], tool_calls: [] },
  { id: '1002-3', text: 'bad', state: 'error', response: 'Name the CLI first', turn: { state: 'error', started: 1, ended: 2 }, tools: [], tool_calls: [] },
  { id: '1003-4', text: 'later', state: 'queued', response: null, turn: null, tools: [], tool_calls: [] },
];
const page = (messages, more = false, next = null) => ({ messages, more, next });
const row = (w, id, k = '') => w.get(`DomNode:chat:row:${id}${k}`) || {};
let n = 0;
const scenario = (name, fn) => { fn(); n++; console.log('  ok', name); };

// 1. opening the view draws the skeleton and asks for the newest page (the legacy first load: 20 turns)
scenario('skeleton and first load', () => {
  const w = world(); w.open();
  assert.equal(w.n('root').tag, 'div'); assert.equal(w.n('root').class, 'chatview');
  assert.equal(w.n('log').parent, 'DomNode:chat:root'); assert.equal(w.n('log').class, 'log'); assert.equal(w.n('log')['aria-live'], 'polite');
  assert.equal(w.n('form').tag, 'form'); assert.equal(w.n('form').class, 'composer'); assert.equal(w.n('form').parent, 'DomNode:chat:root');
  assert.equal(w.n('input').tag, 'textarea'); assert.equal(w.n('input').parent, 'DomNode:chat:form'); assert.equal(w.n('input').rows, 2);
  assert.equal(w.n('input').placeholder, 'Tell the seat what you are working on. Enter sends, Shift+Enter adds a line.');
  assert.equal(w.n('input')['aria-label'], 'Message');
  assert.equal(w.n('send').tag, 'button'); assert.equal(w.n('send').text, 'Send'); assert.equal(w.n('send').type, 'submit');
  assert.equal(w.n('older').hidden, true, 'no older turns known yet');
  const q = w.asked('history');
  assert.equal(q.method ?? 'GET', 'GET'); assert.equal(q.url, '/membrane/chat/messages?limit=20');
});

// 2. a page of turns: who / bubbles / pending / tools, as the legacy chat draws them
scenario('a page of turns is drawn as the legacy chat draws it', () => {
  const w = world(); w.open(); w.reply('history', 200, page(MSGS));
  const [a, b, c, d] = ['1000-1', '1001-2', '1002-3', '1003-4'];
  assert.equal(row(w, a).tag, 'li'); assert.equal(row(w, a).class, 'msg'); assert.equal(row(w, a)['data-state'], 'done');
  assert.equal(row(w, a).parent, 'DomNode:chat:list');
  assert.equal(row(w, a, ':me').text, 'hello'); assert.equal(row(w, a, ':me').class, 'bubble me');
  assert.equal(row(w, a, ':who').text, 'seat · 5 s'); assert.equal(row(w, a, ':who').class, 'who');
  assert.equal(row(w, a, ':ans').text, 'hi there'); assert.equal(row(w, a, ':ans').class, 'bubble seat'); assert.ok(!row(w, a, ':ans').hidden);
  assert.equal(row(w, b, ':who').text, 'working'); assert.equal(row(w, b, ':ans').text, 'Working on it…'); assert.equal(row(w, b, ':ans').class, 'bubble seat pending');
  assert.equal(row(w, b)['data-state'], 'running');
  assert.equal(row(w, c, ':ans').class, 'bubble note', 'an error answer is shown as what to do next'); assert.equal(row(w, c, ':ans').text, 'Name the CLI first');
  assert.equal(row(w, d, ':who').text, 'queued'); assert.equal(row(w, d, ':ans').text, 'Waiting for a turn…'); assert.equal(row(w, d, ':ans').class, 'bubble seat pending');
  // the tools folded under the answer: a count, then each tool's name and its input shortened to 90
  assert.equal(row(w, a, ':tools').tag, 'details'); assert.equal(row(w, a, ':tools').class, 'tools-run');
  assert.equal(row(w, a, ':tools:sum').text, '2 tools');
  const names = w.log.filter((e) => e.prop === 'text' && /:tools:t:.*:name$/.test(e.eid)).map((e) => e.new);
  assert.deepEqual(names, ['mcp__membrane__entity_read', 'Bash']);
  const ins = w.log.filter((e) => e.prop === 'text' && /:tools:t:.*:in$/.test(e.eid)).map((e) => e.new);
  assert.deepEqual(ins, [' ' + short({ spec: 'X', prop: 'y' }), ' ' + short('x'.repeat(120))]);
  assert.equal(short('x'.repeat(120)).length, 90);
  assert.equal(row(w, b, ':tools').hidden, true, 'a running turn lists no finished tools (tool_calls is [])');
  assert.equal(w.n('older').hidden, true, 'no more turns');
  // the order of the rows in the list is the order of the page (oldest first)
  const order = w.log.filter((e) => e.prop === 'parent' && e.new === 'DomNode:chat:list').map((e) => e.eid.split(':').pop());
  assert.deepEqual(order, ['1000-1', '1001-2', '1002-3', '1003-4']);
});

// 3. the composer: typing is a write, Enter or Send is one POST, the guard
scenario('typing, Enter, Send, and the double-submit guard', () => {
  const w = world(); w.open(); w.reply('history', 200, page([]));
  w.write('DomNode:chat:input', 'value', 'hi there  ');
  assert.equal(w.get('Composer:chat').draft, 'hi there  ', 'the input event is the draft');
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: true, ctrl: false, time: 1 });
  assert.equal(w.reqs('send').length, 0, 'Shift+Enter is a new line, not a send');
  w.write('DomNode:chat:input', 'key', { key: 'a', shift: false, ctrl: false, time: 2 });
  assert.equal(w.reqs('send').length, 0);
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 3, composing: true });
  assert.equal(w.reqs('send').length, 0, 'Enter that ends an IME composition is not a send');
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 4 });
  assert.equal(w.reqs('send').length, 1, 'Enter sends');
  const q = w.asked('send');
  assert.equal(q.url, '/membrane/chat/message'); assert.equal(q.method, 'POST');
  assert.deepEqual(q.headers, { 'content-type': 'application/json' });
  assert.deepEqual(JSON.parse(q.body), { text: 'hi there' }, 'the text goes trimmed, as the legacy chat sends it');
  assert.equal(w.get('Composer:chat').sending, true);
  assert.equal(w.n('input').disabled, true); assert.equal(w.n('send').disabled, true, 'both are disabled while the POST is out');
  // a second Enter, a held key, a click while it is out: nothing
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 5 });
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 6 });
  w.write('DomNode:chat:form', 'submitted', 7);
  assert.equal(w.reqs('send').length, 1, 'one POST per message');
  // the seat answers 202: the draft is cleared, the controls are back, the message is a row at once
  w.reply('send', 202, { id: '2000-9' });
  assert.equal(w.get('Composer:chat').sending, false); assert.equal(w.get('Composer:chat').draft, '');
  assert.equal(w.n('input').value, ''); assert.equal(w.n('input').disabled, false); assert.equal(w.n('send').disabled, false);
  assert.equal(w.n('input').focused, true, 'focus goes back to the box');
  assert.equal(row(w, '2000-9', ':me').text, 'hi there'); assert.equal(row(w, '2000-9', ':who').text, 'queued');
  assert.equal(row(w, '2000-9')['data-state'], 'queued');
  // the form's submit (the Send button) sends too; an empty draft does not
  w.write('DomNode:chat:form', 'submitted', 8);
  assert.equal(w.reqs('send').length, 1, 'nothing in the box: nothing sent');
  w.write('DomNode:chat:input', 'value', 'second');
  w.write('DomNode:chat:form', 'submitted', 9);
  assert.equal(w.reqs('send').length, 2); assert.equal(JSON.parse(w.asked('send').body).text, 'second');
  assert.notEqual(w.reqs('send')[0].new.id, w.reqs('send')[1].new.id, 'a request id is never reused (the host would not fetch it twice)');
});

// 4. a failed POST: the note, the draft kept, the guard cleared
scenario('a failed POST shows the note and keeps the draft', () => {
  const w = world(); w.open(); w.reply('history', 200, page([]));
  w.write('DomNode:chat:input', 'value', 'keep me');
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 1 });
  w.reply('send', 500, { error: 'the turn queue is full' });
  assert.equal(w.get('Composer:chat').error, 'The message did not reach the seat: the turn queue is full. It is still in the box; send it again when the seat is back.');
  assert.equal(w.n('note').hidden, false); assert.equal(w.n('note:bubble').class, 'bubble note');
  assert.equal(w.n('note:bubble').text, w.get('Composer:chat').error);
  assert.equal(w.get('Composer:chat').draft, 'keep me'); assert.equal(w.get('Composer:chat').sending, false);
  assert.equal(w.n('input').disabled, false);
  // the next send clears the note; a body that is not JSON says the status; a network failure says why
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 2 });
  assert.equal(w.n('note').hidden, true, 'a new attempt takes the old note away');
  w.reply('send', 502, '<html>bad gateway</html>');
  assert.match(w.get('Composer:chat').error, /^The message did not reach the seat: the seat answered 502\. /);
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 3 });
  w.fail('send', 'Failed to fetch');
  assert.match(w.get('Composer:chat').error, /^The message did not reach the seat: Failed to fetch\. /);
  // a 200 without an id is a failure too (the legacy rule: ok and an id)
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 4 });
  w.reply('send', 200, {});
  assert.match(w.get('Composer:chat').error, /the seat answered 200/);
});

// 5. polling on the clock: the list poll, the active poll, one request at a time, and again after a send
scenario('polls: list period, active period, no overlap, and again after a send', () => {
  const w = world(); w.open();
  assert.equal(w.reqs('history').length, 1);
  w.tick(10); assert.equal(w.reqs('history').length, 1, 'the first load is still out: no second request');
  w.reply('history', 200, page([MSGS[0], MSGS[2]]));          // all finished
  w.tick(2.9); assert.equal(w.reqs('history').length, 1, 'under the list period (3000 ms)');
  w.tick(0.2); assert.equal(w.reqs('history').length, 2, 'the list poll');
  assert.equal(w.asked('history').url, '/membrane/chat/messages?limit=10', 'a poll reads the newest 10');
  w.tick(5); assert.equal(w.reqs('history').length, 2, 'still out');
  w.reply('history', 200, page([MSGS[0], MSGS[1], MSGS[2]]));  // one is running now
  w.tick(0.5); assert.equal(w.reqs('history').length, 2, 'a running turn: under the active period (700 ms)');
  w.tick(0.3); assert.equal(w.reqs('history').length, 3, 'the active poll');
  w.reply('history', 200, page([MSGS[0], { ...MSGS[1], state: 'done', response: 'finished now', turn: { started: 110, ended: 112 } }, MSGS[2]]));
  assert.equal(row(w, '1001-2', ':ans').text, 'finished now'); assert.equal(row(w, '1001-2', ':ans').class, 'bubble seat');
  assert.equal(row(w, '1001-2', ':who').text, 'seat · 2 s'); assert.equal(row(w, '1001-2')['data-state'], 'done');
  w.tick(1); assert.equal(w.reqs('history').length, 3, 'all finished: back to the list period');
  // after a send: a poll at the next tick whatever the period
  w.write('DomNode:chat:input', 'value', 'go');
  w.write('DomNode:chat:input', 'key', { key: 'Enter', shift: false, ctrl: false, time: 1 });
  w.reply('send', 202, { id: '3000-1' });
  w.tick(0.1); assert.equal(w.reqs('history').length, 4, 'a poll right after a send');
});

// 6. "Load older turns": the button, `before`, the older rows ABOVE the loaded ones
scenario('Load older turns uses before and puts the older turns above', () => {
  const w = world(); w.open();
  w.reply('history', 200, page([MSGS[2], MSGS[3]], true, '1002-3'));
  assert.equal(w.n('older').hidden, false); assert.equal(w.n('older:btn').text, 'Load older turns'); assert.equal(w.n('older:btn').tag, 'button');
  w.write('DomNode:chat:older:btn', 'clicked', 1);
  assert.equal(w.asked('older').url, '/membrane/chat/messages?limit=20&before=1002-3');
  assert.equal(w.n('older:btn').disabled, true, 'one click, one request');
  w.write('DomNode:chat:older:btn', 'clicked', 2);
  assert.equal(w.reqs('older').length, 1);
  w.reply('older', 200, page([MSGS[0], MSGS[1]], false, null));
  assert.equal(w.n('older').hidden, true, 'no older turns left');
  assert.equal(w.n('older:btn').disabled, false);
  // ONE list, no generations: each older row sits above the oldest row that was there (`before`), so the DOM order is oldest first
  assert.equal(w.get('View:chat').gen, undefined, 'no list generation');
  assert.equal(w.get('DomNode:chat:list:1'), null);
  assert.equal(row(w, '1000-1').parent, 'DomNode:chat:list'); assert.equal(row(w, '1000-1').before, 'DomNode:chat:row:1002-3');
  assert.equal(row(w, '1001-2').parent, 'DomNode:chat:list'); assert.equal(row(w, '1001-2').before, 'DomNode:chat:row:1002-3');
  assert.equal(row(w, '1002-3').before, undefined, 'the rows that were there are not moved');
  assert.equal(row(w, '1000-1', ':me').text, 'hello');
  assert.notEqual(w.n('list').hidden, true);
  // a failure of the older fetch says so on the button, and it can be tried again
  const v = world(); v.open(); v.reply('history', 200, page([MSGS[3]], true, '1003-4'));
  v.write('DomNode:chat:older:btn', 'clicked', 1); v.fail('older', 'Failed to fetch');
  assert.equal(v.n('older:btn').text, 'Load older turns (the seat did not answer: try again)'); assert.equal(v.n('older:btn').disabled, false);
});

// 7. the legacy constants are facts, with today's values when no fact says otherwise
scenario('the carried-over constants are Boot facts', () => {
  const d = world(); d.open();
  assert.deepEqual([d.get('View:chat').page_size, d.get('View:chat').poll_size, d.get('View:chat').list_ms, d.get('View:chat').active_ms, d.get('View:chat').stick_px, d.get('View:chat').tool_chars],
    [20, 10, 3000, 700, 80, 90]);
  const w = world();
  w.facts({ chat_page_size: 5, chat_poll_size: 2, chat_list_poll_ms: 1000, chat_active_poll_ms: 100, chat_stick_px: 40, chat_tool_chars: 10 });
  w.open();
  assert.equal(w.asked('history').url, '/membrane/chat/messages?limit=5');
  w.reply('history', 200, page([{ ...MSGS[0], tool_calls: [{ name: 'T', input: 'abcdefghijklmnop' }] }]));
  w.tick(1.1); assert.equal(w.asked('history').url, '/membrane/chat/messages?limit=2');
  assert.equal(w.log.filter((e) => e.prop === 'text' && /:tools:t:.*:in$/.test(e.eid)).pop().new, ' abcdefghi…', 'tool_chars shortens');
  assert.equal(w.get('View:chat').tick_ms, 100, 'the host ticks the clock at the active poll');
  assert.equal(d.get('View:chat').tick_ms, 700);
  // the scroll of the log: near the bottom within stick_px (the shell has no scroll write yet: the state is kept, nothing moves it)
  w.write('DomNode:chat:log', 'scroll', { top: 120, height: 500, client: 360 });
  assert.equal(w.get('View:chat').near, true);
  w.write('DomNode:chat:log', 'scroll', { top: 0, height: 500, client: 360 });
  assert.equal(w.get('View:chat').near, false);
});

// 7b. the log is kept at the bottom: a scroll write 'end' after the rows are drawn, unless the person scrolled up (near false)
scenario('stick to the bottom: the pack writes scroll end unless the person scrolled away', () => {
  const w = world(); w.open();
  const ends = () => w.log.filter((e) => e.eid === 'DomNode:chat:log' && e.prop === 'scroll' && e.new === 'end').length;
  w.reply('history', 200, page([MSGS[0]]));
  assert.ok(ends() >= 1, 'the first load puts the log at the bottom');
  w.write('DomNode:chat:log', 'scroll', { top: 0, height: 500, client: 360 });      // the person scrolled up
  const n = ends();
  w.tick(3.1); w.reply('history', 200, page([MSGS[0], MSGS[3]]));
  assert.equal(ends(), n, 'a new row while the person reads up does not move the log');
  w.write('DomNode:chat:log', 'scroll', { top: 140, height: 500, client: 360 });    // back near the bottom
  w.tick(3.1); w.reply('history', 200, page([MSGS[0], MSGS[3], { ...MSGS[2], id: '1004-5' }]));
  assert.ok(ends() > n, 'near the bottom: the log follows');
});

// 7c. a hidden tab pauses the polls (Conn:page.hidden, written by the shell)
scenario('polls pause while the page is hidden', () => {
  const w = world(); w.open();
  w.reply('history', 200, page([MSGS[0]]));
  w.feed('Conn:page', 'Conn', { hidden: true });
  w.tick(10); assert.equal(w.reqs('history').length, 1, 'hidden: no poll, however late');
  w.feed('Conn:page', 'Conn', { hidden: false });
  w.tick(0.1); assert.equal(w.reqs('history').length, 2, 'visible again: the poll that was due goes');
});

// 7d. two identical tool calls are two lines (each call has an id from the history route)
scenario('tool rows are keyed by the call id', () => {
  const w = world(); w.open();
  const call = (id) => ({ id, name: 'Bash', input: 'ls' });
  w.reply('history', 200, page([{ ...MSGS[0], tools: ['Bash', 'Bash'], tool_calls: [call('t:1'), call('t:2')] }]));
  assert.equal(new Set(w.log.filter((e) => e.prop === 'parent' && e.new === 'DomNode:chat:row:1000-1:tools:list').map((e) => e.eid)).size, 2, 'two calls, two lines');
});

// 8. the pack on the seat does nothing: no View entity, no request
scenario('no View entity, nothing happens', () => {
  const w = world(); w.tick(100);
  assert.equal(w.get('Fetch:history'), null); assert.equal(w.get('DomNode:chat:root'), null);
});
console.log(`chat_view: ok (${n} scenarios)`);
