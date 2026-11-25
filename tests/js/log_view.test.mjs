// The seat's Log tab (membrane/apps/barest.c1f/log_view.c1f) in the page's field engine: a tab with the boundary log and the raw chat with the model, and nothing in it names a peer.
// The page's other packs are stubs (`dom`, `fetch`, `shell_view`): what is under test is this pack's own writes and its reads. The tab ASKS with the door (`POST /mcp`, `entity_write` of `<ns>::LogAsk:main`) and
// READS BY THE LINK: `membrane::LogView:<ns>` (head, rows) and `membrane::ModelChat:main` (text) are values the link feeds (`feed_val`, as `fieldvm.js Link` does), so no read is a request (#1143, operator 2026-11-24).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/apps/barest.c1f/ui/fieldvm_core.js';

const ui = new URL('../../membrane/apps/barest.c1f/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const LOG = readFileSync(fileURLToPath(new URL('../../membrane/apps/barest.c1f/log_view.c1f', import.meta.url)), 'utf8');

assert.ok(!/membrama|@NS@/.test(LOG.replace(/--.*$/gm, '')), 'the code names no peer');

const f = new Field();
f.set_pack('dom', "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n");
f.set_pack('fetch', 'PACK fetch 1\n');
f.set_pack('shell_view', 'PACK shell_view 1\n');
f.set_pack('log_view', LOG);
assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'the log view loads');
f.set_ns('p1aaaaaaa');

const get = (e) => JSON.parse(f.get(e));
const w = (e, k, v, now = 1) => { f.write(e, k, JSON.stringify(v), now); f.drain(); };
const post = (e) => { const r = (get(e) || {}).request; return r && JSON.parse(r.body); };
let rev = 0;
const feed = (e, k, v, now) => { f.feed_val(e, k, JSON.stringify(v), ++rev, now); f.drain(); };
let rc = 0;   // a receipt differs from the one before (the real ones carry their own id and time), so `Fetch.mq` changes and its setter hears it
const receipt = () => ({ status: 'ok', http_status: 200, body: JSON.stringify({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: 'wrote ' + (++rc) }] } }) });
const NS = 'p1';
const VIEW = `membrane::LogView:${NS}`;
const row = (n) => ({ n, eid: `Box:${n}`, prop: 'x', op: 'set', writer: 'person', ts: n, size: 1 });
const lines = () => get('DomNode:log:text').text;

// the shell opens: the tab registers itself, and draws into its slot when it is shown first
f.load('Shell:main', 'Shell', '{}');
f.load('Link:page', 'Link', '{}');
w('Link:page', 'ns', NS);
w('Shell:main', 'status_ms', 1000);
w('Shell:main', 'open', true);
assert.equal(get('Nav:log').title, 'Log');
w('Nav:log', 'active', true);
const root = get('DomNode:log:root');
assert.equal(root.parent, 'DomNode:shell:slot:log'); assert.equal(root.class, 'panelrows');
assert.equal(get('DomNode:log:text').tag, 'pre');
assert.equal(get('DomNode:log:chat').tag, 'pre'); assert.equal(get('DomNode:log:chat').parent, 'DomNode:log:root', 'the raw chat is a section of the tab');
assert.match(get('DomNode:log:chathead').text, /Chat with the model/);
assert.equal(get('Nav:log').filled, true);

// with the link DOWN nothing is asked and nothing is read: the tab waits for the link
w('Shell:main', 'tick', 50, 50);
assert.ok(!post('Fetch:log:n'), 'no ask while the link is down');
assert.match(get('DomNode:log:chat').text, /nothing yet/);

// the link comes up: the tick asks the seat for the log in the page's own namespace, with the door (a write), and reads nothing by a request
w('Link:page', 'up', true);
w('Shell:main', 'tick', 100, 100);
const ask = post('Fetch:log:n');
assert.ok(ask && ask.params.name === 'entity_write' && ask.params.arguments.spec === `${NS}::LogAsk:main` && ask.params.arguments.prop === 'n', 'the log is asked in the page\'s own namespace, by a write');
assert.equal(get('Fetch:log:n').request.url, '/mcp');
assert.ok(!post('Fetch:log:read'), 'no read is a request any more');
assert.ok(!post('Fetch:logchat:read'), 'nor the raw chat');

// the raw chat is the value the link brings: a text the seat has not made yet says so; the next text replaces it
assert.match(get('DomNode:log:chat').text, /nothing yet/);
feed('membrane::ModelChat:main', 'text', '── main:message:1 round 1 ──\n[user]\nhello', 100.5);
assert.equal(get('DomNode:log:chat').text, '── main:message:1 round 1 ──\n[user]\nhello');
feed('membrane::ModelChat:main', 'text', '── main:message:1 round 1 ──\n[user]\nhello\n[assistant]\nhi', 100.6);
assert.match(get('DomNode:log:chat').text, /\[assistant\]\nhi$/);

// the receipt of the ask, then the head arrives as the view's value: the tab shows the last 200 lines, so the next ask is from the line after head - 199 (here the log is short: from 0)
w('Fetch:log:n', 'request_result', receipt(), 100.001);
feed(VIEW, 'head', 9, 101);
assert.equal(get('LogTab:main').phase, 'follow');
assert.equal(get('LogTab:main').polling, false);
assert.equal(get('LogTab:main').first, 0);
assert.equal(get('LogTab:main').last, -1);

// the next tick asks from the line after the last one shown (two writes, chained by their receipts), and the rows come by the link
w('Shell:main', 'tick', 200, 200);
const from = post('Fetch:log:from');
assert.ok(from && from.params.arguments.spec === `${NS}::LogAsk:main` && from.params.arguments.prop === 'from' && from.params.arguments.value === 0);
w('Fetch:log:from', 'request_result', receipt(), 200.001);
const n2 = post('Fetch:log:n');
assert.equal(n2.params.arguments.prop, 'n');
w('Fetch:log:n', 'request_result', receipt(), 200.002);
assert.equal(get('LogTab:main').polling, false, 'the ask is over with its second receipt');
feed(VIEW, 'rows', [row(0), row(1), row(2)], 200.5);
assert.equal(lines(), '0 Box:0.x set by person\n1 Box:1.x set by person\n2 Box:2.x set by person');
assert.equal(get('LogTab:main').last, 2);

// a later ask brings only the new lines, appended; a row already shown is not shown twice
w('Shell:main', 'tick', 300, 300);
assert.equal(post('Fetch:log:from').params.arguments.value, 3);
w('Fetch:log:from', 'request_result', receipt(), 300.001);
w('Fetch:log:n', 'request_result', receipt(), 300.002);
feed(VIEW, 'rows', [row(2), row(3), row(4)], 300.5);
assert.equal(get('LogTab:main').last, 4);
assert.equal(lines().split('\n').length, 5);
assert.ok(lines().endsWith('4 Box:4.x set by person'));

// a write the seat did not answer is said on the tab, and the next tick tries again
w('Shell:main', 'tick', 400, 400);
w('Fetch:log:from', 'request_result', { status: 'ok', http_status: 500, body: 'broken' }, 400.001);
assert.match(get('LogTab:main').note, /the seat did not answer the log tab/);
assert.equal(get('LogTab:main').polling, false);
// "Load older lines": the 200 lines before the first one shown. Asked from max(0, first - 200); the rows that come by the link and are older than the first go ABOVE the lines
w('LogTab:main', 'first', 300, 500); w('LogTab:main', 'last', 399, 500); w('LogTab:main', 'lines', ['300 Box:300.x set by person'], 500); w('LogTab:main', 'polling', false, 500); w('LogTab:main', 'note', null, 500);
w('DomNode:log:older:btn', 'clicked', true, 501);
const older = post('Fetch:log:from');
assert.ok(older && older.params.arguments.prop === 'from' && older.params.arguments.value === 100, 'the older ask starts 200 lines before the first one shown');
assert.equal(get('LogTab:main').older_out, true);
w('Fetch:log:from', 'request_result', receipt(), 501.001);
w('Fetch:log:n', 'request_result', receipt(), 501.002);
const oldRows = []; for (let i = 100; i < 300; i++) oldRows.push(row(i));
feed(VIEW, 'rows', oldRows, 501.5);
assert.equal(get('LogTab:main').older_out, false);
assert.equal(get('LogTab:main').first, 100, 'the first line shown is now the oldest asked for');
assert.equal(get('LogTab:main').pages, 2);
assert.equal(get('LogTab:main').lines.length, 201, 'the 200 older lines are above the one that was shown');
assert.equal(get('LogTab:main').lines[0], '100 Box:100.x set by person');
assert.equal(get('LogTab:main').lines[200], '300 Box:300.x set by person');
console.log('log view: ok');
