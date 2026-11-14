// The field as a 3D graph, a c1f VIEW PACK (membrane/apps/barest.c1f/field_view.c1f, #1143), on the wasm engine in Node: the page's VM with the packs `dom`, `fetch` and
// `field_view`. The test plays the two things around the engine: the HOST (a DomNode event is a write; a `Fetch:<x>.request` is answered by a write of `request_result`, the way
// fieldvm.js does; a `retire` write removes the entity, as the host does) and the CLOCK (`tick`). Asserted: the skeleton, the rows -> dots with a projection, the growth of the graph
// as rows appear, the window following the field's activity (a row that left the answer leaves the engine), the layout (spreads, then goes quiet), the camera, the selection, the
// window buttons, the poll cadence and the failure note.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');
const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";
const FETCH = read('../../membrane/packs/fetch.c1f');
const PACK = read('../../membrane/apps/barest.c1f/field_view.c1f');

const T0 = 1_000_000;
function world(facts) {
  const f = new Field();
  f.set_pack('dom', DOM); f.set_pack('fetch', FETCH); f.set_pack('field_view', PACK);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'field_view loads once dom and fetch are there');
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
  w.write = (e, p, v) => { f.write(e, p, JSON.stringify(v ?? null), w.t); return w.flush(); };
  w.feed = (e, cls, props) => { if (f.get(e) === 'null') f.load(e, cls, '{}'); for (const [k, v] of Object.entries(props)) f.write(e, k, JSON.stringify(v ?? null), w.t); w.flush(); };
  w.tick = (dt = 0.1) => { w.t += dt; f.tick(w.t); w.flush(); };
  w.ticks = (n, dt = 0.1) => { for (let i = 0; i < n; i++) w.tick(dt); };
  w.asked = () => (w.get('Fetch:map') || {}).request;
  w.reqs = () => w.log.filter((e) => e.eid === 'Fetch:map' && e.prop === 'request' && !e.born);
  w.reply = (status, body) => w.write('Fetch:map', 'request_result', { id: w.asked().id, status: 'ok', rc: 0, head: String(status), http_status: status, headers: {}, body: typeof body === 'string' ? body : JSON.stringify(body), started: w.t, finished: w.t, ms: 1 });
  w.fail = (why) => w.write('Fetch:map', 'request_result', { id: w.asked().id, status: 'error', rc: 1, head: why, started: w.t, finished: w.t, ms: 1 });
  w.open = (facts) => { if (facts) w.feed('Boot:seat', 'Boot', facts); w.feed('Scene:field', 'Scene', { open: true }); };
  w.n = (k) => w.get('DomNode:f:' + k) || {};
  w.dot = (id) => w.get('Dot:' + id);
  return w;
}
const rows = (...r) => ({ nodes: r.map(([id, cls, updated, parent]) => ({ id, cls, updated, parent: parent ?? null })), n_total: 500, hidden: [], m: 150 });
const BASE = [['Chat:1', 'Chat', 100.1234], ['Chat:1:a', 'Chat', 101.2345, 'Chat:1'], ['Chat:1:b', 'Chat', 102.3456, 'Chat:1'], ['Goal:x', 'Goal', 103.4567], ['Help:cliff', 'Help', 104.5678]];
const dist = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]));
const places = (w, ids) => ids.map((i) => w.dot(i).p);
let n = 0;
const scenario = (name, fn) => { fn(); n++; console.log('  ok', name); };

scenario('open draws the skeleton and asks for the window (150 tried, the fact says otherwise)', () => {
  const w = world(); w.open();
  assert.equal(w.n('root').class, 'fieldview');
  assert.equal(w.n('svg').tag, 'svg'); assert.equal(w.n('svg').viewBox, '-100 -100 200 200');
  assert.deepEqual([w.n('dots').tag, w.n('lines').tag, w.n('labels').tag], ['g', 'g', 'g']);
  assert.equal(w.asked().url, '/ui/fieldvm/active?m=150', 'the first call: the tried window');
  const v = world(); v.open({ field_window: 40, field_poll_ms: 500 });
  assert.equal(v.asked().url, '/ui/fieldvm/active?m=40', 'the launch fact field_window');
  assert.equal(v.get('Scene:field').poll_ms, 500);
});

scenario('rows become dots: a circle each, projected, sized by mass, a line to the owner, coloured by class', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.tick();
  for (const [id] of BASE) {
    const c = w.get('DomNode:f:c:' + id);
    assert.equal(c.tag, 'circle', id); assert.equal(c.parent, 'DomNode:f:dots');
    assert.equal(typeof c.cx, 'number'); assert.equal(typeof c.cy, 'number'); assert.ok(c.r > 0, id);
    assert.match(c.fill, /^rgba\(\d+,\d+,\d+,[0-9.]+\)$/);
  }
  assert.ok(w.get('DomNode:f:c:Chat:1').r > w.get('DomNode:f:c:Goal:x').r, 'mass (two children) makes the owner bigger');
  assert.equal(w.dot('Chat:1').mass, 2);
  assert.equal(w.get('DomNode:f:l:Chat:1:a').tag, 'line'); assert.equal(w.get('DomNode:f:l:Chat:1:a').hidden, false);
  assert.equal(w.get('DomNode:f:l:Goal:x').hidden, true, 'no owner here: no line');
  assert.notEqual(w.get('DomNode:f:c:Chat:1').fill.split(',').slice(0, 3).join(), w.get('DomNode:f:c:Goal:x').fill.split(',').slice(0, 3).join(), 'a class a colour');
  assert.equal(w.n('count').text, '5 of 500 entities in view (the 150 most recently active)');
  assert.deepEqual(w.get('Scene:field').classes, ['Chat', 'Goal', 'Help']);
  assert.deepEqual(['Chat', 'Goal', 'Help'].map((c) => w.get('DomNode:f:lgt:' + c).text), ['Chat', 'Goal', 'Help'], 'the legend: a row per class');
  assert.equal(w.get('DomNode:f:lgc:Chat').fill, 'rgb(' + w.get('DomNode:f:c:Chat:1').fill.slice(5).split(',').slice(0, 3).join(',') + ')', 'the legend dot has the class colour');
});

scenario('a class keeps its colour when another class arrives (first sight, not alphabetical)', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.tick();
  const rgb = (id) => w.get('DomNode:f:c:' + id).fill.split(',').slice(0, 3).join();
  const was = { chat: rgb('Chat:1'), goal: rgb('Goal:x') };
  w.t += 2.5; w.tick(); w.reply(200, rows(['Ant:1', 'Ant', 120.5], ...BASE)); w.tick();
  assert.equal(rgb('Chat:1'), was.chat); assert.equal(rgb('Goal:x'), was.goal);
  assert.notEqual(rgb('Ant:1'), was.chat); assert.notEqual(rgb('Ant:1'), was.goal);
});

scenario('the graph GROWS: a new row is born next to its owner, and an old dot does not jump', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.ticks(5);
  const before = w.dot('Goal:x').p.slice();
  w.t += 2.5; w.tick();                                                  // the poll period passes: the next call
  assert.equal(w.reqs().length, 2, 'a second call');
  w.reply(200, rows(['Chat:1:c', 'Chat', 110.7654, 'Chat:1'], ...BASE));
  const born = w.dot('Chat:1:c'), owner = w.dot('Chat:1');
  assert.ok(born && born.p, 'the new row is a dot');
  assert.ok(dist(born.p, owner.p) < 12, `born next to its owner (${dist(born.p, owner.p).toFixed(1)} away)`);
  assert.equal(w.dot('Chat:1').mass, 3, 'the owner has one more child');
  assert.equal(w.get('DomNode:f:c:Chat:1:c').tag, 'circle');
  assert.ok(dist(w.dot('Goal:x').p, before) < 12, 'the others stay where they were (the layout moves them a little, not a jump)');
});

scenario('the window FOLLOWS the field: a row that is no longer among the most active leaves the engine and the page', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.ticks(3);
  assert.ok(w.dot('Help:cliff'));
  w.t += 2.5; w.tick(); w.reply(200, rows(...BASE.slice(0, 3)));
  assert.equal(w.dot('Help:cliff'), null, 'the dot left the engine');
  assert.equal(w.dot('Goal:x'), null);
  assert.equal(w.get('DomNode:f:c:Help:cliff'), null, 'and its circle node');
  assert.equal(w.get('DomNode:f:l:Help:cliff'), null); assert.equal(w.get('DomNode:f:t:Help:cliff'), null);
  const retired = w.log.filter((e) => e.prop === 'retire').map((e) => e.eid);
  assert.ok(retired.includes('DomNode:f:c:Help:cliff') && retired.includes('Dot:Help:cliff'), 'a retire write for the dot and its nodes');
  assert.equal(w.n('count').text, '3 of 500 entities in view (the 150 most recently active)');
  assert.deepEqual(['lg', 'lgd', 'lgc', 'lgt'].map((k) => w.get(`DomNode:f:${k}:Goal`)), [null, null, null, null], 'a class that left leaves the legend');
  assert.ok(w.dot('Chat:1:a'), 'the rest stays');
});

scenario('the layout spreads the dots out and then goes quiet', () => {
  const w = world(); w.open();
  const many = [['A:1', 'A', 100.1111]]; for (let i = 0; i < 24; i++) many.push([`A:1:${i}`, 'A', 101.01 + i * 0.37, 'A:1']);
  w.reply(200, rows(...many));
  const ids = many.map((m) => m[0]);
  const min0 = () => { const p = places(w, ids); let m = 1e9; for (let i = 0; i < p.length; i++) for (let j = i + 1; j < p.length; j++) m = Math.min(m, dist(p[i], p[j])); return m; };
  const first = min0();
  w.ticks(120);
  assert.ok(min0() > first, `the closest pair moved apart (${first.toFixed(2)} -> ${min0().toFixed(2)})`);
  assert.ok(min0() > 3, 'no two dots on top of each other');
  const parts = places(w, ids.slice(1)).map((p) => dist(p, w.dot('A:1').p));
  assert.ok(Math.max(...parts) < 80, `children stay near their owner (${Math.max(...parts).toFixed(1)})`);
  w.ticks(300);
  assert.equal(w.get('Scene:field').heat, 0, 'cooled: the layout is quiet');
  const frozen = places(w, ids); w.ticks(5);
  assert.deepEqual(places(w, ids), frozen, 'a quiet layout does not move');
  w.t += 2.5; w.tick(); w.reply(200, rows(['A:1:new', 'A', 200.5, 'A:1'], ...many));
  assert.equal(w.get('Scene:field').heat, 1, 'a birth warms it again');
});

scenario('the camera: it spins, pauses, zooms; a spin changes the picture and not the places', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.write('DomNode:f:spin', 'clicked', w.t); w.ticks(400); w.write('DomNode:f:spin', 'clicked', w.t);   // let the layout go quiet first
  assert.equal(w.get('Scene:field').heat, 0);
  const p0 = w.dot('Chat:1:a').p.slice(), x0 = w.get('DomNode:f:c:Chat:1:a').cx;
  w.ticks(30);
  assert.notEqual(w.get('DomNode:f:c:Chat:1:a').cx, x0, 'the camera turned: the picture moved');
  const b = w.get('Scene:field');
  assert.ok(Math.abs(Math.hypot(...b.rx) - 1) < 1e-6 && Math.abs(Math.hypot(...b.rz) - 1) < 1e-6, 'the basis stays a basis');
  w.write('DomNode:f:spin', 'clicked', w.t);
  assert.equal(w.get('Scene:field').spin, false); assert.equal(w.n('spin').text, 'Spin');
  const x1 = w.get('DomNode:f:c:Chat:1:a').cx; w.ticks(10);
  assert.equal(w.get('DomNode:f:c:Chat:1:a').cx, x1, 'paused');
  const r1 = w.get('DomNode:f:c:Chat:1').r;
  w.write('DomNode:f:zin', 'clicked', w.t);
  assert.ok(w.get('DomNode:f:c:Chat:1').r > r1, 'nearer: bigger');
  w.write('DomNode:f:zout', 'clicked', w.t); w.write('DomNode:f:zout', 'clicked', w.t + 1);
  assert.ok(w.get('DomNode:f:c:Chat:1').r < r1, 'farther: smaller');
});

scenario('a click on a dot selects it: its words show, its neighbours get a label; again deselects', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.ticks(2);
  assert.equal(w.n('side').hidden, true);
  assert.equal(w.get('DomNode:f:t:Chat:1:a').hidden, true);
  w.write('DomNode:f:c:Chat:1', 'clicked', w.t);
  assert.equal(w.get('Scene:field').selected, 'Chat:1');
  assert.match(w.n('side').text, /^Chat:1 · Chat · 2 under it · changed \d+ s ago$/);
  assert.equal(w.n('side').hidden, false);
  assert.equal(w.get('DomNode:f:t:Chat:1').hidden, false); assert.equal(w.get('DomNode:f:t:Chat:1:a').hidden, false, 'the children are labelled');
  assert.equal(w.get('DomNode:f:t:Goal:x').hidden, true, 'a stranger is not');
  assert.equal(w.get('DomNode:f:c:Chat:1').stroke, 'currentColor');
  w.write('DomNode:f:c:Chat:1', 'clicked', w.t + 1);
  assert.ok(w.get('Scene:field').selected == null); assert.equal(w.n('side').hidden, true);
});

scenario('the window buttons halve and double it, and the next call asks for the new size', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE));
  w.write('DomNode:f:wless', 'clicked', w.t);
  assert.equal(w.get('Scene:field').window, 75);
  w.tick();
  assert.equal(w.asked().url, '/ui/fieldvm/active?m=75', 'asked at the next tick, not after the poll period');
  w.reply(200, rows(...BASE)); w.write('DomNode:f:wmore', 'clicked', w.t); w.write('DomNode:f:wmore', 'clicked', w.t + 1);
  assert.equal(w.get('Scene:field').window, 300);
  w.write('DomNode:f:wless', 'clicked', w.t + 2);
});

scenario('the poll: one call at a time, the period after the last answer, paused in a hidden tab', () => {
  const w = world(); w.open();
  assert.equal(w.reqs().length, 1);
  w.t += 5; w.tick(); assert.equal(w.reqs().length, 1, 'none while the first is out');
  w.reply(200, rows(...BASE));
  w.ticks(5); assert.equal(w.reqs().length, 1, 'not before the period (2 s) has passed');
  w.ticks(20); assert.equal(w.reqs().length, 2, 'then one');
  w.reply(200, rows(...BASE)); w.feed('Conn:page', 'Conn', { hidden: true });
  w.ticks(60); assert.equal(w.reqs().length, 2, 'a hidden tab does not poll');
  w.feed('Conn:page', 'Conn', { hidden: false }); w.ticks(2); assert.equal(w.reqs().length, 3);
});

scenario('a failed call keeps what is in view and says so; the next call goes on', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.ticks(3);
  w.t += 2.5; w.tick(); w.fail('connection refused');
  assert.match(w.n('note').text, /did not give the active entities: connection refused/); assert.equal(w.n('note').hidden, false);
  assert.ok(w.dot('Chat:1'), 'the view keeps its dots');
  w.t += 2.5; w.tick(); w.reply(500, { error: { message: 'm (the number of entities, at least 1) is required' } });
  assert.match(w.n('note').text, /m \(the number/);
  w.t += 2.5; w.tick(); w.reply(200, rows(...BASE));
  assert.equal(w.n('note').hidden, true, 'an answer clears the note');
});

scenario('speed: a hot step (layout + camera + draw) of 150 dots (the window tried) fits the 100 ms clock; 300 is printed', () => {
  for (const N of [150, 300]) {
    const w = world(); w.open();
    const many = [['A:1', 'A', 100.1111]]; for (let i = 0; i < N - 1; i++) many.push([`B:${i}`, 'B', 101.01 + i * 0.3711, i % 7 ? null : 'A:1']);
    w.reply(200, rows(...many));
    const t0 = performance.now(); w.ticks(10); const ms = (performance.now() - t0) / 10;
    console.log(`    one hot tick of ${N} dots: ${ms.toFixed(1)} ms`);
    if (N === 150) assert.ok(ms < 100, `a tick fits the 100 ms clock (${ms.toFixed(1)} ms)`);
  }
});

console.log(`field view: ${n} scenarios ok`);
