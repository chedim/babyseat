// The SETTINGS VIEW as a c1f view pack (membrane/apps/barest.c1f/settings_view.c1f, #1143), plugged into the shell, on the wasm engine in Node: the page's VM with the packs `dom`, `fetch`,
// `shell_view`, `chat_view`, `settings_view`. The test plays the HOST (a DomNode event is a write; a `Fetch:<x>.request` is answered by a write of `request_result`; `retire` removes the
// entity) and the SEAT (the rows of GET /ui/settings come from a REAL seat's answer, tests/js/fixtures/settings_feed.json; a write of `Setting:<id>.set` is answered here as the seat's own
// setter would: native/tests/settings_1143.rs proves the seat side). Asserted: the rows per group in order, hidden rows absent, a live write applies and the row updates, an out-of-range
// number shows the seat's reason and the value in force is unchanged, a refusal of the call itself (403) is text, a restart row has no control, the list and the allow-list editors round
// trip, a model list is a select, a failed read says so and can be tried again, no secret is on the page, and an idle page does no work.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');
const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";
const FETCH = read('../../membrane/packs/fetch.c1f');
const PACKS = { shell_view: read('../../membrane/apps/barest.c1f/shell_view.c1f'), chat_view: read('../../membrane/apps/barest.c1f/chat_view.c1f'), settings_view: read('../../membrane/apps/barest.c1f/settings_view.c1f') };
const FEED = JSON.parse(read('./fixtures/settings_feed.json'));
const clone = (x) => JSON.parse(JSON.stringify(x));

const T0 = 1_000_000;
function world(extra = {}) {
  const f = new Field();
  f.set_pack('dom', DOM); f.set_pack('fetch', FETCH);
  for (const [n, s] of Object.entries({ ...PACKS, ...extra })) f.set_pack(n, s);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'the packs load');
  const w = { f, t: T0, log: [] };
  w.flush = () => {
    const all = [];
    for (;;) {                                                           // the host's rule: a retire write removes the entity, and what that changes is drained again
      const o = JSON.parse(f.drain()); w.log.push(...o.writes); all.push(...o.writes);
      const gone = o.writes.filter((x) => x.prop === 'retire' && x.new === true);
      for (const x of gone) f.remove(x.eid, w.t);
      if (!gone.length) return all;
    }
  };
  w.get = (e) => JSON.parse(f.get(e));
  w.write = (e, p, v) => { w.t += 0.01; f.write(e, p, JSON.stringify(v ?? null), w.t); return w.flush(); };
  w.feed = (e, cls, props) => { if (f.get(e) === 'null') f.load(e, cls, '{}'); for (const [k, v] of Object.entries(props)) f.write(e, k, JSON.stringify(v ?? null), w.t); w.flush(); };
  w.tick = (dt = 0.1) => { w.t += dt; f.tick(w.t); return w.flush(); };
  w.asked = (x) => (w.get('Fetch:' + x) || {}).request;
  w.reply = (x, status, body) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'ok', rc: 0, head: String(status), http_status: status, headers: {}, body: typeof body === 'string' ? body : JSON.stringify(body), started: w.t, finished: w.t, ms: 1 });
  w.fail = (x, why) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'error', rc: 1, head: why, started: w.t, finished: w.t, ms: 1 });
  w.click = (eid) => w.write(eid, 'clicked', w.t);
  w.boot = () => { w.feed('Shell:main', 'Shell', { view: 'chat', sid: 'main' }); w.feed('Shell:main', 'Shell', { open: true }); };
  w.n = (rid, part) => w.get('DomNode:set:' + rid + ':' + part) || {};
  w.type = (rid, part, v) => w.write('DomNode:set:' + rid + ':' + part, 'value', v);
  w.submit = (rid) => w.write('DomNode:set:' + rid + ':form', 'submitted', w.t);
  w.eids = (prefix) => [...new Set(w.log.map((x) => x.eid).filter((e) => e.startsWith(prefix) && w.get(e)))];
  w.shown = () => { w.click('DomNode:shell:nav:settings'); return w; };
  // the seat's feed (a real answer), with `edit` applied to a copy; the page asks, the test answers
  w.rows = (edit) => { const j = clone(FEED); if (edit) edit(j.settings); return j; };
  w.feedAnswer = (edit) => w.reply('settings', 200, w.rows(edit));
  // the DOM order of a node's children, as the host makes it: appended in the order they were made, `before` puts a node above a sibling (the chain is followed from the node nothing points at)
  w.kids = (parent) => {
    const made = []; for (const x of w.log) if (x.prop === 'parent' && x.new === parent && !made.includes(x.eid) && w.get(x.eid)?.parent === parent) made.push(x.eid);
    const above = new Map(made.map((e) => [e, w.get(e).before]).filter(([, b]) => made.includes(b)));
    const order = []; const pointed = new Set(above.values());
    for (const e of made) { if (order.includes(e)) continue; let cur = e; const chain = []; while (cur && !order.includes(cur) && !chain.includes(cur)) { chain.push(cur); cur = [...above].find(([, b]) => b === cur)?.[0]; } order.push(...chain.reverse()); }
    return order;
  };
  w.open = (edit) => { w.boot(); w.shown(); w.feedAnswer(edit); return w; };
  return w;
}
const byId = (rows, id) => rows.find((r) => r.id === id);
// what the seat's setter answers to `Setting:<id>.set = v` for the rows used here (the seat side is tested in native/tests/settings_1143.rs): the new feed
const applied = (rows, id, v) => { const r = byId(rows, id); r.current = v; r.refused = ''; r.applied = 1.7e9; };
const refused = (rows, id, why) => { byId(rows, id).refused = why; };
const MCP_OK = (text = 'wrote') => ({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text }], isError: false } });
const body = (w, x) => JSON.parse(w.asked(x).body);
const allText = (w) => w.log.filter((x) => x.eid.startsWith('DomNode:') && typeof x.new === 'string' && !x.born).map((x) => x.new).join('\n');
let n = 0;
const scenario = (name, fn) => { fn(); n++; console.log('  ok', name); };

scenario('the view is lazy: nothing is drawn or asked before the tab is shown; then the placeholder goes and the rows are read', () => {
  const w = world(); w.boot();
  assert.equal(w.get('SettingsView:main'), null, 'not drawn yet');
  assert.equal(w.asked('settings'), undefined, 'no request before the tab is shown');
  assert.equal(w.get('Nav:settings').title, 'Settings');
  w.shown();
  assert.equal(w.get('DomNode:setv:root').parent, 'DomNode:shell:slot:settings');
  assert.equal(w.get('Nav:settings').filled, true);
  assert.equal(w.get('DomNode:shell:slot:settings:empty').hidden, true, 'the shell\'s placeholder is gone');
  assert.equal(w.asked('settings').url, '/ui/settings');
  assert.equal(w.get('DomNode:setv:notetext').text, 'Loading the settings…');
  assert.equal(w.get('DomNode:setv:note').hidden, false);
  w.feedAnswer();
  assert.equal(w.get('DomNode:setv:note').hidden, true, 'the loading line goes with the rows');
});

scenario('one section per group, in the operator\'s order, each row in its order; the Map rows are not shown', () => {
  const w = world().open();
  assert.deepEqual(w.kids('DomNode:setv:groups'), ['model', 'chat', 'home', 'import', 'access', 'limits'].map((g) => 'DomNode:setg:' + g), 'Model, Chat, Home, Import, Access, Limits; no Map');
  for (const [g, t] of [['model', 'Model'], ['chat', 'Chat'], ['home', 'Home'], ['import', 'Import'], ['access', 'Access'], ['limits', 'Limits']]) {
    assert.equal(w.get('DomNode:setg:' + g + ':h').text, t); assert.equal(w.get('DomNode:setg:' + g).tag, 'section');
  }
  assert.match(w.get('DomNode:setg:access:sub').text, /Read-only/); assert.match(w.get('DomNode:setg:limits:sub').text, /restart/);
  const rowsOf = (g) => w.kids('DomNode:setg:' + g + ':rows').map((e) => e.replace('DomNode:set:', ''));
  assert.deepEqual(rowsOf('model'), ['model.chat', 'model.default', 'model.list', 'model.cloud', 'model.signin', 'model.cli']);
  assert.deepEqual(rowsOf('chat'), ['chat.max_steps', 'chat.timeout']);
  assert.deepEqual(rowsOf('home'), ['home.allow']);
  assert.deepEqual(rowsOf('access'), ['access.owner', 'access.token_life', 'access.token_file', 'access.mode', 'access.people', 'access.port']);
  assert.equal(rowsOf('limits').length, 9);
  assert.equal(w.eids('DomNode:set:map.').length, 0, 'a hidden row has no node'); assert.equal(w.eids('DomNode:setg:map').length, 0, 'and no group');
  assert.equal(w.get('SetRow:map.window'), null);
  // a row: label, one line of help, the badge, what is in force
  assert.equal(w.n('chat.timeout', 'label').text, 'Answer time limit');
  assert.match(w.n('chat.timeout', 'help').text, /How long one answer may run/);
  assert.equal(w.n('chat.timeout', 'badge').text, 'Applies now');
  assert.equal(w.n('chat.timeout', 'now').text, 'In force: 21600 seconds');
  assert.equal(w.n('chat.timeout', 'limits').text, 'Allowed: 1 to 604800 seconds. Default: 21600');
  assert.equal(w.n('chat.max_steps', 'limits').text, 'Allowed: 1 to 100000 steps, whole numbers. Default: 2048');
  assert.equal(w.n('chat.timeout', 'in').tag, 'input'); assert.equal(w.n('chat.timeout', 'in').type, 'text', 'a number is typed in a text input: the browser\'s own validation would hide the seat\'s reason'); assert.equal(w.n('chat.timeout', 'in').value, '21600');
  assert.equal(w.n('chat.timeout', 'unit').text, 'seconds'); assert.equal(w.n('chat.timeout', 'save').text, 'Save');
  const parts = (id) => w.kids('DomNode:set:' + id).map((e) => e.replace('DomNode:set:' + id + ':', ''));
  assert.deepEqual(parts('chat.timeout'), ['top', 'help', 'limits', 'now', 'form', 'state'], 'an editable row: the label line, help, limits, what is in force, the control, the result');
  assert.deepEqual(parts('access.port'), ['top', 'help', 'limits', 'value', 'how', 'state'], 'a restart row: the label line, help, the value, how to change it');
  assert.deepEqual(w.kids('DomNode:set:chat.timeout:form').map((e) => e.split(':').pop()), ['in', 'unit', 'save']);
  assert.equal(w.n('model.chat', 'in').tag, 'input'); assert.equal(w.n('model.chat', 'in').type, 'text'); assert.equal(w.n('model.chat', 'in').value, 'claude', 'no model list: a text input');
});

scenario('a live write applies: ONE POST /mcp entity_write of Setting:<id>.set, then the rows are read back and the row says Applied', () => {
  const w = world().open();
  w.type('chat.timeout', 'in', '90');
  assert.equal(w.asked('setw:chat.timeout'), undefined, 'typing writes nothing to the seat');
  w.submit('chat.timeout');
  const rq = w.asked('setw:chat.timeout');
  assert.equal(rq.url, '/mcp'); assert.equal(rq.method, 'POST'); assert.equal(rq.headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(rq.body), { jsonrpc: '2.0', id: JSON.parse(rq.body).id, method: 'tools/call', params: { name: 'entity_write', arguments: { spec: 'Setting:chat.timeout', prop: 'set', value: 90 } } });
  assert.equal(w.n('chat.timeout', 'save').disabled, true, 'one write at a time'); assert.equal(w.n('chat.timeout', 'state').text, 'Saving…');
  w.submit('chat.timeout'); assert.equal(w.log.filter((x) => x.eid === 'Fetch:setw:chat.timeout' && x.prop === 'request' && !x.born).length, 1, 'a second submit while one is out is not a second POST');
  const before = w.asked('settings').id;
  w.reply('setw:chat.timeout', 200, MCP_OK());
  assert.notEqual(w.asked('settings').id, before, 'the rows are read again after the write');
  assert.equal(w.n('chat.timeout', 'now').text, 'In force: 21600 seconds', 'until the seat says so, the value in force is the old one');
  w.reply('settings', 200, w.rows((r) => applied(r, 'chat.timeout', 90)));
  assert.equal(w.n('chat.timeout', 'now').text, 'In force: 90 seconds');
  assert.equal(w.n('chat.timeout', 'state').text, 'Applied.'); assert.equal(w.n('chat.timeout', 'state')['data-kind'], 'ok');
  assert.equal(w.n('chat.timeout', 'save').disabled, false); assert.equal(w.n('chat.timeout', 'save').text, 'Save');
  assert.equal(w.get('SetRow:chat.timeout').draft, '90');
});

scenario('an out-of-range number: the seat\'s reason is shown next to the row, the value in force is unchanged, the box keeps what was typed (twice the same)', () => {
  const w = world().open();
  const why = 'Answer time limit must be at least 1 (0 is below it)';
  for (let i = 0; i < 2; i++) {
    w.type('chat.timeout', 'in', '0'); w.submit('chat.timeout');
    assert.equal(JSON.parse(w.asked('setw:chat.timeout').body).params.arguments.value, 0, 'the seat is asked: the page never clamps');
    w.reply('setw:chat.timeout', 200, MCP_OK());
    w.reply('settings', 200, w.rows((r) => refused(r, 'chat.timeout', why)));
    assert.equal(w.n('chat.timeout', 'state').text, 'Refused: ' + why, `refusal ${i + 1}`); assert.equal(w.n('chat.timeout', 'state')['data-kind'], 'bad');
    assert.equal(w.n('chat.timeout', 'state').hidden, false);
    assert.equal(w.n('chat.timeout', 'now').text, 'In force: 21600 seconds', 'nothing changed');
    assert.equal(w.get('SetRow:chat.timeout').draft, '0', 'what was typed stays in the box');
    assert.equal(w.n('chat.timeout', 'save').disabled, false);
  }
  // text that is no number goes as typed: the seat's reason names it
  w.type('chat.max_steps', 'in', 'soon'); w.submit('chat.max_steps');
  assert.equal(JSON.parse(w.asked('setw:chat.max_steps').body).params.arguments.value, 'soon');
  w.reply('setw:chat.max_steps', 200, MCP_OK());
  w.reply('settings', 200, w.rows((r) => refused(r, 'chat.max_steps', 'Most steps per answer must be a number, not soon')));
  assert.match(w.n('chat.max_steps', 'state').text, /must be a number, not soon/);
  // the next good value clears it
  w.type('chat.max_steps', 'in', '40'); w.submit('chat.max_steps');
  assert.equal(JSON.parse(w.asked('setw:chat.max_steps').body).params.arguments.value, 40);
  assert.equal(w.n('chat.max_steps', 'state').text, 'Saving…', 'the old refusal is not shown while the new write is out');
});

scenario('a refusal of the call itself (a person who is not the owner: 403) or a failure is text, with the reason, and the row is usable again', () => {
  const w = world().open();
  w.type('chat.timeout', 'in', '5'); w.submit('chat.timeout');
  w.reply('setw:chat.timeout', 403, { type: 'error', error: { message: 'for the owner' } });
  assert.equal(w.n('chat.timeout', 'state').text, 'Not saved: the seat answered: for the owner'); assert.equal(w.n('chat.timeout', 'state')['data-kind'], 'bad');
  assert.equal(w.n('chat.timeout', 'save').disabled, false);
  assert.equal(w.n('chat.timeout', 'now').text, 'In force: 21600 seconds');
  w.submit('chat.timeout'); w.fail('setw:chat.timeout', 'the connection was reset');
  assert.equal(w.n('chat.timeout', 'state').text, 'Not saved: the seat answered: the connection was reset');
  w.submit('chat.timeout'); w.reply('setw:chat.timeout', 500, 'boom');
  assert.equal(w.n('chat.timeout', 'state').text, 'Not saved: the seat answered: the seat answered 500');
  // a JSON-RPC error object is read the same way
  w.submit('chat.timeout'); w.reply('setw:chat.timeout', 200, { jsonrpc: '2.0', id: 1, error: { code: -32000, message: 'tool failed: x' } });
  assert.equal(w.n('chat.timeout', 'state').text, 'Not saved: the seat answered: tool failed: x');
});

scenario('a restart row has NO control: its value, the badge and the launch line, in text', () => {
  const w = world().open();
  for (const id of ['access.port', 'limits.command_timeout_s', 'model.signin', 'model.cli', 'import.page', 'access.token_file']) {
    assert.equal(w.n(id, 'badge').text, 'Needs a restart', id);
    assert.match(w.n(id, 'how').text, /^How to change it: start the seat with --/, id);
    const mine = w.eids('DomNode:set:' + id + ':');
    assert.ok(mine.length >= 5, id);
    for (const e of mine) assert.ok(!['input', 'textarea', 'select', 'button', 'form', 'option'].includes(w.get(e).tag), `${id}: ${e} is a ${w.get(e).tag}`);
    assert.equal(w.get('DomNode:set:' + id + ':now'), null, `${id}: no "in force" line (an editable row's)`);
  }
  assert.equal(w.n('limits.command_timeout_s', 'value').text, '120 seconds');
  assert.equal(w.n('limits.eval_stats', 'value').text, 'yes');
  assert.equal(w.n('limits.memory_budget_mb', 'value').text, 'not set');
  assert.equal(w.n('access.mode', 'value').text, 'open');
  assert.equal(w.n('model.list', 'value').text, 'none');
  assert.equal(w.n('model.cloud', 'value').text, 'gpt · openai · key in OTHER_PROVIDER_KEY', 'a provider is shown by the NAME of the variable that holds its key');
  // typing into the page's nodes of such a row does nothing: no draft, no request
  assert.equal(w.get('SetRow:access.port').draft, undefined); assert.equal(w.asked('setw:access.port'), undefined);
  // a write to its door is not made by the view even if its `go` is poked
  w.write('SetRow:access.port', 'go', 1); assert.equal(w.asked('setw:access.port'), undefined);
});

scenario('the allow-list editor: add, edit, remove an entry, Save: one write of the whole list', () => {
  const w = world().open((rows) => { byId(rows, 'home.allow').current = [{ name: 'kitchen', services: ['light.turn_on'], entities: ['light.*'] }, { name: 'temp', services: ['states.read'], entities: ['sensor.t'] }]; });
  assert.equal(w.n('home.allow', 'now').text, 'In force: 2 entries: kitchen, temp');
  assert.equal(w.n('home.allow', 'add').text, 'Add an entry');
  assert.deepEqual(w.kids('DomNode:set:home.allow:form').map((e) => e.split(':').pop()), ['lines', 'add', 'save']);
  assert.deepEqual(w.kids('DomNode:set:home.allow:lines').map((e) => e.split(':').pop()), ['eikitchen', 'eitemp'], 'the entries keep the seat\'s order');
  assert.equal(w.n('home.allow', 'eikitchen:a').value, 'kitchen'); assert.equal(w.n('home.allow', 'eikitchen:b').value, 'light.turn_on'); assert.equal(w.n('home.allow', 'eikitchen:c').value, 'light.*');
  assert.equal(w.n('home.allow', 'eitemp:a').value, 'temp');
  // edit the services of kitchen, remove temp, add a new entry
  w.type('home.allow', 'eikitchen:b', 'light.turn_on, light.turn_off');
  w.click('DomNode:set:home.allow:eitemp:x');
  assert.equal(w.get('DomNode:set:home.allow:eitemp'), null, 'the removed entry\'s nodes leave'); assert.equal(w.get('DomNode:set:home.allow:eitemp:a'), null);
  w.click('DomNode:set:home.allow:add');
  assert.equal(w.n('home.allow', 'en1:a').parent, 'DomNode:set:home.allow:en1:fa', 'an empty entry appears');
  assert.equal(w.n('home.allow', 'lines').tag, 'div', 'the entries\' container stays when entries come and go'); assert.equal(w.n('home.allow', 'en1').parent, 'DomNode:set:home.allow:lines');
  assert.equal(w.n('home.allow', 'eikitchen:b').value, 'light.turn_on, light.turn_off', 'the entry that stayed keeps what was typed');
  w.type('home.allow', 'en1:a', 'hall'); w.type('home.allow', 'en1:b', 'light.*'); w.type('home.allow', 'en1:c', 'light.hall, light.stairs');
  assert.equal(w.asked('setw:home.allow'), undefined, 'nothing goes to the seat before Save');
  w.submit('home.allow');
  assert.deepEqual(JSON.parse(w.asked('setw:home.allow').body).params.arguments, { spec: 'Setting:home.allow', prop: 'set', value: [
    { name: 'kitchen', services: ['light.turn_on', 'light.turn_off'], entities: ['light.*'] },
    { name: 'hall', services: ['light.*'], entities: ['light.hall', 'light.stairs'] }] });
  w.reply('setw:home.allow', 200, MCP_OK());
  w.reply('settings', 200, w.rows((r) => applied(r, 'home.allow', [{ name: 'kitchen', services: ['light.turn_on', 'light.turn_off'], entities: ['light.*'] }, { name: 'hall', services: ['light.*'], entities: ['light.hall', 'light.stairs'] }])));
  assert.equal(w.n('home.allow', 'state').text, 'Applied.'); assert.equal(w.n('home.allow', 'now').text, 'In force: 2 entries: kitchen, hall');
  assert.equal(w.n('home.allow', 'lines').tag, 'div', 'the container survives the redraw'); assert.equal(w.n('home.allow', 'eihall').parent, 'DomNode:set:home.allow:lines');
  assert.equal(w.n('home.allow', 'eihall:b').value, 'light.*', 'the entries are drawn again from what is in force'); assert.equal(w.get('DomNode:set:home.allow:en1'), null);
  // an entry with no name is the seat's to refuse, and its reason is shown
  w.click('DomNode:set:home.allow:add'); w.submit('home.allow');
  assert.equal(JSON.parse(w.asked('setw:home.allow').body).params.arguments.value.at(-1).name, '');
  w.reply('setw:home.allow', 200, MCP_OK());
  w.reply('settings', 200, w.rows((r) => { applied(r, 'home.allow', [{ name: 'kitchen', services: ['light.turn_on', 'light.turn_off'], entities: ['light.*'] }, { name: 'hall', services: ['light.*'], entities: ['light.hall', 'light.stairs'] }]); refused(r, 'home.allow', 'Allowed actions must be a list of {name: text, services: [text], entities: [text]}'); }));
  assert.match(w.n('home.allow', 'state').text, /^Refused: Allowed actions must be a list/);
  assert.ok(w.get('DomNode:set:home.allow:en2:a'), 'the empty entry is still there to fill or remove');
});

scenario('an allow-list with no entry says nothing is allowed; the first entry can be added and saved', () => {
  const w = world().open();
  assert.equal(w.n('home.allow', 'now').text, 'In force: no entry: nothing is allowed');
  w.click('DomNode:set:home.allow:add'); w.type('home.allow', 'en1:a', 'a'); w.type('home.allow', 'en1:b', 'states.read'); w.type('home.allow', 'en1:c', '');
  w.submit('home.allow');
  assert.deepEqual(JSON.parse(w.asked('setw:home.allow').body).params.arguments.value, [{ name: 'a', services: ['states.read'], entities: [] }]);
});

scenario('a list editor (a row of kind list; the Map lists are not shown in this release): lines, add, remove, empty lines left out, Save', () => {
  const w = world().open((rows) => { const r = byId(rows, 'chat.timeout'); r.kind = 'list'; r.current = ['Message', 'Span']; r.unit = null; r.whole = false; });
  assert.equal(w.n('chat.timeout', 'now').text, 'In force: Message, Span');
  assert.equal(w.n('chat.timeout', 'add').text, 'Add a line');
  assert.equal(w.n('chat.timeout', 'eiMessage:a').value, 'Message'); assert.equal(w.get('DomNode:set:chat.timeout:eiMessage:fb').hidden, true, 'a line has one field');
  w.click('DomNode:set:chat.timeout:eiSpan:x');   // (a line that was read is keyed `i<its text>`)
  assert.equal(w.get('DomNode:set:chat.timeout:eiSpan'), null);
  w.click('DomNode:set:chat.timeout:add'); w.click('DomNode:set:chat.timeout:add');
  w.type('chat.timeout', 'en1:a', 'Fetch');   // the second added line stays empty
  w.submit('chat.timeout');
  assert.deepEqual(JSON.parse(w.asked('setw:chat.timeout').body).params.arguments.value, ['Message', 'Fetch']);
});

scenario('the chat model: a select when the seat gives its model list (the one in force selected), else a text input; a change is saved like any text', () => {
  const w = world().open((rows) => { byId(rows, 'model.chat').choices = ['claude', 'claude-agent:opus', 'local:qwen']; byId(rows, 'model.chat').current = 'claude-agent:opus'; });
  assert.equal(w.n('model.chat', 'in').tag, 'select');
  assert.deepEqual(['claude-agent:opus', 'claude', 'local:qwen'].map((o) => w.get('DomNode:set:model.chat:o:' + o).text), ['claude-agent:opus', 'claude', 'local:qwen'], 'the model in force first, then the seat\'s list');
  assert.equal(w.get('DomNode:set:model.chat:o:claude-agent:opus').selected, true); assert.equal(w.get('DomNode:set:model.chat:o:claude').selected, false);
  assert.equal(w.n('model.chat', 'in').parent, 'DomNode:set:model.chat:form');
  w.write('DomNode:set:model.chat:in', 'value', 'local:qwen');
  w.submit('model.chat');
  assert.equal(JSON.parse(w.asked('setw:model.chat').body).params.arguments.value, 'local:qwen');
});

scenario('re-showing the tab reads the rows again; a draft that was typed stays unless the value in force changed; a failed read is text with Try again', () => {
  const w = world().open();
  w.type('chat.timeout', 'in', '77');
  w.click('DomNode:shell:nav:chat'); w.click('DomNode:shell:nav:settings');
  const id = w.asked('settings').id;
  w.reply('settings', 200, w.rows());
  assert.equal(w.get('SetRow:chat.timeout').draft, '77', 'an unsaved edit survives a re-read');
  // the value in force changed elsewhere: the box follows it
  w.click('DomNode:shell:nav:chat'); w.click('DomNode:shell:nav:settings');
  w.reply('settings', 200, w.rows((r) => applied(r, 'chat.timeout', 120)));
  assert.equal(w.get('SetRow:chat.timeout').draft, '120'); assert.equal(w.n('chat.timeout', 'now').text, 'In force: 120 seconds');
  // a read that fails: text with the reason, the rows stay, Try again asks again
  w.click('DomNode:shell:nav:chat'); w.click('DomNode:shell:nav:settings');
  w.reply('settings', 500, { error: { message: 'the store is busy' } });
  assert.equal(w.get('DomNode:setv:notetext').text, 'The settings could not be read: the store is busy.'); assert.equal(w.get('DomNode:setv:note').role, 'alert');
  assert.equal(w.get('DomNode:setv:retry').hidden, false); assert.ok(w.n('chat.timeout', 'label').text);
  const first = w.asked('settings').id;
  w.click('DomNode:setv:retry');
  assert.notEqual(w.asked('settings').id, first);
  w.fail('settings', 'the connection was reset');
  assert.equal(w.get('DomNode:setv:notetext').text, 'The settings could not be read: the connection was reset.');
  w.click('DomNode:setv:retry'); w.reply('settings', 200, w.rows());
  assert.equal(w.get('DomNode:setv:note').hidden, true, 'a good read clears it');
  assert.ok(id);
});

scenario('an answer of an older read (a newer one was asked for) is dropped', () => {
  const w = world().open();
  w.click('DomNode:shell:nav:chat'); w.click('DomNode:shell:nav:settings');
  const old = w.asked('settings');
  w.click('DomNode:shell:nav:chat'); w.click('DomNode:shell:nav:settings');
  assert.notEqual(w.asked('settings').id, old.id);
  w.write('Fetch:settings', 'request_result', { id: old.id, status: 'ok', rc: 0, head: '200', http_status: 200, headers: {}, body: JSON.stringify(w.rows((r) => applied(r, 'chat.timeout', 555))), started: w.t, finished: w.t, ms: 1 });
  assert.equal(w.n('chat.timeout', 'now').text, 'In force: 21600 seconds', 'the stale answer is not drawn');
});

scenario('no secret is on the page: the view shows only what the feed holds, and the feed holds names of variables, never values', () => {
  const SECRETS = ['sk-ant-secret-0001', 'hunter2pw', 'owner-jwt-token-xyz'];
  const w = world().open((rows) => {
    byId(rows, 'model.cloud').current = [{ name: 'gpt', backend: 'openai', api_key_env: 'OTHER_PROVIDER_KEY' }];   // what the seat's feed carries (native/tests/settings_1143.rs: the real feed holds no secret)
    byId(rows, 'access.token_file').current = '/data/owner.token';
  });
  const page = allText(w) + JSON.stringify(w.log.filter((x) => x.eid.startsWith('DomNode:')).map((x) => x.new));
  for (const s of SECRETS) assert.ok(!page.includes(s), s);
  assert.ok(page.includes('OTHER_PROVIDER_KEY') && page.includes('/data/owner.token'), 'the NAME of the variable and the PATH of the file are shown');
  // and nothing the page sends carries one either: a write is the setting id and the value typed
  w.type('chat.timeout', 'in', '90'); w.submit('chat.timeout');
  assert.ok(!w.asked('setw:chat.timeout').body.includes('sk-'), 'the write holds no key');
});

scenario('an idle page does no work: ticks write nothing, a view with no poll', () => {
  const w = world().open();
  const before = w.log.length;
  for (let i = 0; i < 50; i++) w.tick(0.1);
  const mine = w.log.slice(before).filter((x) => /^(SetRow|SettingsView|DomNode:set)/.test(x.eid) || x.eid === 'Fetch:settings');
  assert.equal(mine.length, 0, 'no settings write while idle');
  assert.equal(w.get('Nav:settings').active, true);
});

console.log(`settings_view: ${n} scenarios ok`);
