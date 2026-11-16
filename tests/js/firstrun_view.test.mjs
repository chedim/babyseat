// The FIRST-RUN WINDOW as a c1f view pack (membrane/apps/barest.c1f/firstrun_view.c1f, #1143), on the wasm engine in Node, like settings_view.test.mjs: the page's VM with the packs `dom`, `fetch`, `shell_view`, `settings_view`,
// `firstrun_view`. The test plays the HOST and the SEAT (the rows of GET /ui/settings: native/tests/ollama_default_1143.rs proves the seat side: `chosen`, `choices`, `refused`).
// Asserted: the window is a function of the two values only (the model chosen, the name chosen), opens at every page load while one is not, a real select of the listed models, the name field (prefilled when it is
// chosen already: a launch fact or an imported name), one submit writes both through the Settings rows' door and the window closes when the seat says both are chosen, an empty name keeps it open, the empty-list
// state says how to pull a model and has Refresh, the seat's refusal is shown.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/apps/barest.c1f/ui/fieldvm_core.js';

const ui = new URL('../../membrane/apps/barest.c1f/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');
const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";
const FETCH = read('../../membrane/packs/fetch.c1f');
const app = (n) => read('../../membrane/apps/barest.c1f/' + n + '.c1f');
const PACKS = { markdown: app('markdown'), shell_view: app('shell_view'), chat_view: app('chat_view'), settings_view: app('settings_view'), firstrun_view: app('firstrun_view') };

const T0 = 1_000_000;
function world(extra = {}) {
  const f = new Field();
  f.set_pack('dom', DOM); f.set_pack('fetch', FETCH);
  for (const [n, s] of Object.entries({ ...PACKS, ...extra })) f.set_pack(n, s);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'the packs load');
  const w = { f, t: T0, log: [], subs: [], unsubs: [] };
  w.flush = () => {
    const all = [];
    for (;;) {                                                           // the host's rule: a retire write removes the entity, and what that changes is drained again
      const o = JSON.parse(f.drain()); w.log.push(...o.writes); all.push(...o.writes); w.subs.push(...(o.subs || [])); w.unsubs.push(...(o.unsubs || []));
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
  return w;
}
const row = (id, over) => ({ id, group: id.split('.')[0], key: id.split('.')[1], label: id, kind: 'text', live: 'live', order: 1, hidden: false, current: '', chosen: false, refused: '', ...over });
const feed = ({ choices = [], model = '', name = '', nameChosen = false, mrefused = '', nrefused = '' } = {}) => ({ settings: [
  row('model.chat', { kind: 'model', current: model, chosen: model !== '', choices, refused: mrefused }),
  row('chat.name', { current: name || 'Bob', chosen: nameChosen, refused: nrefused })] });
const MCP_OK = { jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: 'wrote' }], isError: false } };
const args = (w, x) => JSON.parse(w.asked(x).body).params.arguments;
const open = (f) => { const w = world(); w.boot(); w.reply('firstrun', 200, f); return w; };
const shown = (w) => w.get('DomNode:fr:back')?.hidden === false;
let n = 0;
const scenario = (name, fn) => { fn(); n++; console.log('  ok', name); };

scenario('the page reads the rows at load; with a model and a name chosen the window never shows', () => {
  const w = world(); w.boot();
  assert.equal(w.asked('firstrun').url, '/ui/settings');
  w.reply('firstrun', 200, feed({ choices: ['glm-4:9b'], model: 'glm-4:9b', name: 'Rosie', nameChosen: true }));
  assert.equal(shown(w), false);
});

scenario('no model chosen: the window opens with a real select of the listed models (an empty option first) and an empty name field (no silent Bob)', () => {
  const w = open(feed({ choices: ['glm-4:9b', 'qwen3:8b'] }));
  assert.equal(shown(w), true);
  assert.equal(w.get('DomNode:fr:model').tag, 'select');
  assert.deepEqual(['', 'glm-4:9b', 'qwen3:8b'].map((o) => w.get('DomNode:fr:o:' + o).tag), ['option', 'option', 'option']);
  assert.equal(w.get('DomNode:fr:o:').text, 'Choose a model…'); assert.equal(w.get('DomNode:fr:o:').selected, true);
  assert.equal(w.get('DomNode:fr:name').tag, 'input'); assert.equal(w.get('DomNode:fr:name').value, '', 'the default name is not a choice');
  assert.equal(w.get('DomNode:fr:hint').hidden, true, 'a list: no pull hint');
});

scenario('one submit writes both through the Settings rows\' door; the window closes when the seat says both are chosen', () => {
  const w = open(feed({ choices: ['glm-4:9b', 'qwen3:8b'] }));
  w.write('DomNode:fr:model', 'value', 'qwen3:8b'); w.write('DomNode:fr:name', 'value', ' Rosie ');
  w.write('DomNode:fr:form', 'submitted', w.t);
  const m = args(w, 'frw:model'), nm = args(w, 'frw:name');
  assert.deepEqual([m.spec, m.prop, m.value], ['Setting:model.chat', 'set', 'qwen3:8b']);
  assert.deepEqual([nm.spec, nm.prop, nm.value], ['Setting:chat.name', 'set', 'Rosie']);
  assert.equal(shown(w), true, 'not closed by the write: the seat says when');
  w.reply('frw:model', 200, MCP_OK); w.reply('frw:name', 200, MCP_OK);
  w.reply('firstrun', 200, feed({ choices: ['glm-4:9b', 'qwen3:8b'], model: 'qwen3:8b', name: 'Rosie', nameChosen: true }));
  assert.equal(shown(w), false);
});

scenario('a name that is chosen already (a launch fact or an imported name) is prefilled; only the model is written', () => {
  const w = open(feed({ choices: ['glm-4:9b'], name: 'Blurp', nameChosen: true }));
  assert.equal(shown(w), true, 'the model is not chosen');
  assert.equal(w.get('DomNode:fr:name').value, 'Blurp');
  w.write('DomNode:fr:model', 'value', 'glm-4:9b'); w.write('DomNode:fr:form', 'submitted', w.t);
  assert.equal(args(w, 'frw:model').value, 'glm-4:9b');
  assert.equal(w.asked('frw:name'), undefined, 'the name was not changed: nothing written');
});

scenario('the model is chosen but no name: the window opens for the name alone, and an empty name keeps it open with a reason and writes nothing', () => {
  const w = open(feed({ choices: ['glm-4:9b'], model: 'glm-4:9b' }));
  assert.equal(shown(w), true);
  w.write('DomNode:fr:form', 'submitted', w.t);
  assert.equal(w.get('DomNode:fr:error').text, 'Give the seat a name.');
  assert.equal(w.asked('frw:name'), undefined); assert.equal(w.asked('frw:model'), undefined);
  w.write('DomNode:fr:name', 'value', 'Rosie'); w.write('DomNode:fr:form', 'submitted', w.t);
  assert.equal(args(w, 'frw:name').value, 'Rosie'); assert.equal(w.asked('frw:model'), undefined, 'the model in force is not written again');
});

scenario('an empty list: the window says how to pull a model, the dropdown is off, Refresh asks the seat again', () => {
  const w = open(feed({ choices: [] }));
  assert.equal(shown(w), true);
  assert.match(w.get('DomNode:fr:hint').text, /ollama pull <name>/); assert.equal(w.get('DomNode:fr:hint').hidden, false);
  assert.equal(w.get('DomNode:fr:model').disabled, true);
  const id = w.asked('firstrun').id;
  w.click('DomNode:fr:refresh');
  assert.notEqual(w.asked('firstrun').id, id, 'a new read');
  w.reply('firstrun', 200, feed({ choices: ['glm-4:9b'] }));
  assert.equal(w.get('DomNode:fr:hint').hidden, true); assert.equal(w.get('DomNode:fr:model').disabled, false);
  w.write('DomNode:fr:form', 'submitted', w.t);
  assert.match(w.get('DomNode:fr:error').text, /Choose a model first/);
});

scenario('the seat\'s reason for a refused value is shown in the window, and the window stays open', () => {
  const w = open(feed({ choices: ['glm-4:9b'] }));
  w.write('DomNode:fr:model', 'value', 'glm-4:9b'); w.write('DomNode:fr:name', 'value', 'Rosie'); w.write('DomNode:fr:form', 'submitted', w.t);
  w.reply('frw:model', 200, MCP_OK); w.reply('frw:name', 200, MCP_OK);
  w.reply('firstrun', 200, feed({ choices: ['glm-4:9b'], mrefused: 'Model is not a model name: x' }));
  assert.equal(shown(w), true); assert.match(w.get('DomNode:fr:error').text, /Model is not a model name: x/);
});

scenario('a seat that does not answer is said in text', () => {
  const w = world(); w.boot(); w.fail('firstrun', 'connection refused');
  assert.equal(shown(w), false, 'unknown values open nothing');
  assert.match(w.get('FirstRun:main').note, /connection refused/);
});
console.log(`firstrun_view: ${n} scenarios ok`);
