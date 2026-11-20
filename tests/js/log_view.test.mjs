// The seat's Log tab (membrane/apps/barest.c1f/log_view.c1f) in the page's field engine: a tab with the boundary log and the raw chat with the model, and nothing in it names a peer.
// The page's other packs are stubs (`dom`, `fetch`, `shell_view`): what is under test is this pack's own writes and its door calls, driven by writing the entities the shell and the Fetch driver would write.
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

const get = (e) => JSON.parse(f.get(e));
const w = (e, k, v, now = 1000) => { f.write(e, k, JSON.stringify(v), now); f.drain(); };
const post = (e) => { const r = get(e).request; return r && JSON.parse(r.body); };

// the shell opens: the tab registers itself, and draws into its slot when it is shown first
f.load('Shell:main', 'Shell', '{}');
f.load('Link:page', 'Link', '{}');
w('Link:page', 'ns', 'p1');
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

// the tick asks the seat for the log (its own ns) and for the raw chat (by the door, `entity_read` of ModelChat:main), with no peer anywhere
w('Shell:main', 'tick', 100, 100000);
const chat = post('Fetch:logchat:read');
assert.equal(chat.method, 'tools/call');
assert.deepEqual(chat.params, { name: 'entity_read', arguments: { spec: 'ModelChat:main', prop: 'text' } });
assert.equal(get('Fetch:logchat:read').request.url, '/mcp');
const ask = post('Fetch:log:n');
assert.ok(ask && ask.params.arguments.spec === 'p1::LogAsk:main', 'the log is asked in the page\'s own namespace');
assert.equal(get('Fetch:log:n').request.url, '/mcp');

// its receipt: the text is shown; a text the seat has not made yet says so; a failure is said under the heading
const receipt = (text, status = 200) => ({ status: 'ok', http_status: status, body: JSON.stringify({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text }] } }) });
w('Fetch:logchat:read', 'request_result', receipt('ModelChat:main has no text'), 100001);
assert.match(get('DomNode:log:chat').text, /nothing yet/);
w('Shell:main', 'tick', 200, 200000);
w('Fetch:logchat:read', 'request_result', receipt('── main:message:1 round 1 ──\n[user]\nhello'), 200001);
assert.equal(get('DomNode:log:chat').text, '── main:message:1 round 1 ──\n[user]\nhello');
w('Shell:main', 'tick', 300, 300000);
w('Fetch:logchat:read', 'request_result', { status: 'ok', http_status: 500, body: 'broken' }, 300001);
assert.match(get('DomNode:log:chat').text, /^── main:message:1 round 1/, 'the last text stays');
assert.match(get('DomNode:log:chat').text, /the seat did not answer the model chat/);

// a view that is not in front asks for nothing
w('Nav:log', 'active', false);
const before = get('LogChat:main').seq;
w('Shell:main', 'tick', 900, 900000);
assert.equal(get('LogChat:main').seq, before);
console.log('log_view: ok');
