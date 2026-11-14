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
  f.set_pack('dom', DOM); f.set_pack('fetch', FETCH); f.set_pack('shell_view', read('../../membrane/apps/barest.c1f/shell_view.c1f')); f.set_pack('field_view', PACK);
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
  const reply0 = w.reply; w.reply = (st, b) => { const r = reply0(st, b); if (w.autoEnd && w.wireReq() && (w.get('Fetch:wire') || {}).request_result?.id !== w.wireReq().id) w.wireEnds(); return r; };
  w.fail = (why) => w.write('Fetch:map', 'request_result', { id: w.asked().id, status: 'error', rc: 1, head: why, started: w.t, finished: w.t, ms: 1 });
  // the write stream (`Fetch:wire`, a Fetch with stream: true): its frames arrive as Chunk entities, one per frame, and its receipt is the end of the stream. Most scenarios below are about the POLL
  // (the fallback), so `open` lets the stream end at once; `open(facts, {wire: true})` keeps it.
  w.wireReq = () => (w.get('Fetch:wire') || {}).request;
  w.wireN = 0;
  w.frame = (o) => { w.wireN++; w.feed('Chunk:Fetch:wire:' + w.wireN, 'Chunk', { fetch: 'Fetch:wire', id: w.wireReq().id, n: w.wireN, data: JSON.stringify({ type: 'field.write', prop: 'n', ts: w.t, ...o }), done: false }); };
  w.wireEnds = (why) => w.write('Fetch:wire', 'request_result', { id: w.wireReq().id, status: why ? 'error' : 'ok', rc: why ? 1 : 0, head: why || '200 OK', started: w.t, finished: w.t, ms: 1 });
  w.open = (facts, o = {}) => { if (facts) w.feed('Boot:seat', 'Boot', facts); w.feed('Scene:field', 'Scene', { open: true }); w.autoEnd = !o.wire; if (w.autoEnd) w.wireEnds(); };
  w.n = (k) => w.get('DomNode:f:' + k) || {};
  w.dot = (id) => w.get('Dot:' + id);
  return w;
}
const rows = (...r) => ({ nodes: r.map(([id, cls, updated, parent]) => ({ id, cls, updated, parent: parent ?? null })), n_total: 500, hidden: ['Message', 'Span'], hidden_prefixes: ['Chat:message:'], m: 150 });
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

scenario('the pointer: a drag turns the camera, a wheel zooms, a hover does nothing', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.write('DomNode:f:spin', 'clicked', w.t); w.ticks(400);   // quiet layout, paused spin
  const cx = () => w.get('DomNode:f:c:Chat:1:a').cx, cy = () => w.get('DomNode:f:c:Chat:1:a').cy;
  const pt = (type, x, y, buttons = 1) => w.write('DomNode:f:svg', 'pointer', { type, x, y, w: 400, h: 400, buttons, time: w.t });
  const x0 = cx(), y0 = cy();
  pt('pointerdown', 100, 100); assert.equal(cx(), x0, 'a press alone moves nothing');
  pt('pointermove', 140, 100); const x1 = cx();
  assert.notEqual(x1, x0, 'a horizontal drag turns the camera');
  pt('pointermove', 140, 130); assert.notEqual(cy(), y0, 'a vertical drag tilts it');
  const b = w.get('Scene:field');
  assert.ok([b.rx, b.ry, b.rz].every((v) => Math.abs(Math.hypot(...v) - 1) < 1e-6), 'the basis stays unit');
  assert.ok(Math.abs(b.rx[0] * b.ry[0] + b.rx[1] * b.ry[1] + b.rx[2] * b.ry[2]) < 1e-2, 'and square');
  pt('pointerup', 140, 130, 0);
  const x2 = cx(); pt('pointermove', 300, 300); assert.equal(cx(), x2, 'after the release a move does nothing');
  const r1 = w.get('DomNode:f:c:Chat:1').r;
  w.write('DomNode:f:svg', 'wheel', { dy: -200, x: 10, y: 10, w: 400, h: 400, time: w.t });
  assert.ok(w.get('DomNode:f:c:Chat:1').r > r1, 'wheel up: nearer');
  w.write('DomNode:f:svg', 'wheel', { dy: 100000, x: 10, y: 10, w: 400, h: 400, time: w.t + 1 });
  assert.equal(w.get('Scene:field').zoom, 0.25, 'the zoom stops at its floor');
});

scenario('a click on a dot selects it: its neighbours get a label, the detail panel opens (id, class, words); again deselects and closes it', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.ticks(2);
  const panel = () => w.get('Panel:detail') || {};
  assert.ok(!panel().open, 'nothing is open until something is selected');
  assert.equal(w.get('DomNode:f:t:Chat:1:a').hidden, true);
  w.write('DomNode:f:c:Chat:1', 'clicked', w.t);
  assert.equal(w.get('Scene:field').selected, 'Chat:1');
  assert.equal(panel().open, true); assert.equal(panel().owner, 'field'); assert.equal(panel().title, 'Chat:1');
  assert.match(panel().sub, /^Chat:1 · Chat · 2 under it · changed \d+ s ago$/, 'the words, before the seat has said the class');
  assert.equal(w.get('DomNode:f:t:Chat:1').hidden, false); assert.equal(w.get('DomNode:f:t:Chat:1:a').hidden, false, 'the children are labelled');
  assert.equal(w.get('DomNode:f:t:Goal:x').hidden, true, 'a stranger is not');
  assert.equal(w.get('DomNode:f:c:Chat:1').stroke, 'currentColor');
  w.write('DomNode:f:c:Chat:1', 'clicked', w.t + 1);
  assert.ok(w.get('Scene:field').selected == null); assert.equal(panel().open, false, 'deselecting closes the panel');
});

scenario('the panel closed by the shell clears the selection; the panel is not re-opened by it', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.ticks(2);
  w.write('DomNode:f:c:Chat:1', 'clicked', w.t);
  w.write('Panel:detail', 'open', false);
  assert.ok(w.get('Scene:field').selected == null, 'closing the panel deselects the dot');
  assert.equal(w.get('Panel:detail').open, false);
  assert.equal(w.get('DomNode:f:c:Chat:1').stroke, undefined);
});

scenario('the panel holds the entity properties and the EDGES of the selection (owner, children, refs), each with its kind, from the seat', () => {
  const w = world(); w.open(); w.reply(200, rows(...BASE)); w.ticks(2);
  const edgeReq = () => (w.get('Fetch:edges') || {}).request, entReq = () => (w.get('Fetch:entity') || {}).request;
  const answer = (x, b) => w.write('Fetch:' + x, 'request_result', { id: (w.get('Fetch:' + x).request).id, status: 'ok', rc: 0, head: '200', http_status: 200, headers: {}, body: JSON.stringify(b), started: w.t, finished: w.t, ms: 1 });
  const panel = () => w.get('Panel:detail') || {};
  const row = (key) => (panel().rows || []).find((r) => r.key === key);
  assert.equal(edgeReq(), undefined, 'nothing is asked until something is selected'); assert.equal(entReq(), undefined);
  w.write('DomNode:f:c:Chat:1:a', 'clicked', w.t);
  assert.equal(edgeReq().url, '/ui/fieldvm/edges?eid=Chat:1:a&n=150', 'the selection asks for its edges (n = the window)');
  assert.equal(entReq().url, '/ui/fieldvm/entity?eid=Chat:1:a', 'and for its properties');
  assert.equal(panel().note, 'Reading the properties…');
  answer('entity', { eid: 'Chat:1:a', cls: 'Chat', props: [{ k: 'note', v: 'hello' }, { k: 'n', v: 7 }, { k: 'link', v: '{"$ref":"Goal:x"}' }] });
  assert.equal(panel().note, ''); assert.match(panel().sub, /^Chat · Chat:1:a · Chat/);
  assert.deepEqual([row('b:note').text, row('b:n').text, row('b:link').text], ['hello', '7', '{"$ref":"Goal:x"}'], 'every property, its value as text');
  answer('edges', { eid: 'Chat:1:a', owner: { id: 'Chat:1', cls: 'Chat' }, children: [{ id: 'Chat:1:a:x', cls: 'Chat' }, { id: 'Chat:1:a:y', cls: 'Chat' }], refs: [{ id: 'Goal:x', cls: 'Goal' }] });
  const edges = panel().rows.filter((r) => r.key.startsWith('d'));
  assert.deepEqual(edges.map((r) => r.text), ['owner · Chat:1', 'child · Chat:1:a:x', 'child · Chat:1:a:y', 'ref → Goal:x'], 'a row per edge, the kind shown');
  assert.deepEqual(edges.map((r) => r.target), ['Chat:1', 'Chat:1:a:x', 'Chat:1:a:y', 'Goal:x'], 'each a link');
  // a row is a way to move along the graph: the shell writes `picked`; the selection follows and asks for ITS edges and properties
  w.write('Panel:detail', 'picked', 'Chat:1');
  assert.equal(w.get('Scene:field').selected, 'Chat:1'); assert.equal(edgeReq().url, '/ui/fieldvm/edges?eid=Chat:1&n=150'); assert.equal(entReq().url, '/ui/fieldvm/entity?eid=Chat:1');
  assert.equal(panel().title, 'Chat:1');
  // an answer for an entity that is no longer selected is not shown
  answer('edges', { eid: 'Chat:1:a', owner: null, children: [], refs: [{ id: 'Stale:1', cls: 'Stale' }] });
  assert.ok(!panel().rows.some((r) => r.text === 'ref → Stale:1'), 'a stale answer is dropped');
  // the answer for the selection replaces the rows; none at all says so; a failed read of the properties is text
  answer('edges', { eid: 'Chat:1', owner: null, children: [], refs: [] });
  assert.equal(row('d4:none').text, 'no owner, no children, no references');
  w.write('Fetch:entity', 'request_result', { id: entReq().id, status: 'ok', rc: 0, head: '404', http_status: 404, headers: {}, body: JSON.stringify({ error: { message: 'no such entity' } }), started: w.t, finished: w.t, ms: 1 });
  assert.equal(panel().note, 'The properties could not be read: no such entity.');
  // deselect: the panel closes
  w.write('DomNode:f:c:Chat:1', 'clicked', w.t + 2);
  assert.equal(panel().open, false);
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

scenario('the write STREAM: open asks for it; a frame draws a dot next to its owner with no poll', () => {
  const w = world(); w.open(null, { wire: true });
  assert.equal(w.wireReq().url, '/ui/fieldvm/events'); assert.equal(w.wireReq().stream, true);
  w.reply(200, rows(...BASE)); w.ticks(3);
  const polls = w.reqs().length;
  w.frame({ eid: 'Chat:1:c', cls: 'Chat', ts: 120.5 });
  assert.ok(w.dot('Chat:1:c') && w.dot('Chat:1:c').p, 'the dot is there the moment the frame is');
  assert.equal(w.dot('Chat:1:c').parent_id, 'Chat:1', 'next to its owner');
  assert.ok(dist(w.dot('Chat:1:c').p, w.dot('Chat:1').p) < 12);
  assert.equal(w.get('DomNode:f:c:Chat:1:c').tag, 'circle'); assert.equal(w.dot('Chat:1').mass, 3);
  assert.equal(w.n('count').text, '6 of 501 entities in view (the 150 most recently active)', 'the total grew by one');
  assert.equal(w.get('Scene:field').heat, 1, 'the layout is warmed');
  w.ticks(60, 0.1); assert.equal(w.reqs().length, polls, 'no poll while the stream is open');
  assert.equal(w.get('Chunk:Fetch:wire:1'), null, 'a frame is let go once read');
});

scenario('the stream: a frame of a known entity moves it to the front; hidden classes and prefixes are not drawn; a retire removes the dot', () => {
  const w = world(); w.open(null, { wire: true }); w.reply(200, rows(...BASE)); w.ticks(2);
  w.frame({ eid: 'Chat:1', cls: 'Chat', ts: 300.25 });
  assert.equal(w.dot('Chat:1').raw.updated, 300.25, 'its `updated` is the frame\'s');
  assert.equal(w.n('count').text, '5 of 500 entities in view (the 150 most recently active)', 'no new entity: the same count');
  w.frame({ eid: 'Chat:message:9', cls: 'Chat', ts: 301 }); w.frame({ eid: 'Chat:message:9:body', cls: 'Chat', ts: 301 }); w.frame({ eid: 'Msg:1', cls: 'Message', ts: 301 });
  assert.equal(w.dot('Chat:message:9'), null, 'a hidden eid prefix'); assert.equal(w.dot('Chat:message:9:body'), null); assert.equal(w.dot('Msg:1'), null, 'a hidden class');
  w.frame({ eid: 'Goal:x', cls: 'Goal', prop: 'retire', ts: 302 });
  assert.equal(w.dot('Goal:x'), null, 'a retired entity leaves the view'); assert.equal(w.get('DomNode:f:c:Goal:x'), null);
  w.frame({ eid: 'Early:1', cls: 'Early', ts: 303 }); assert.ok(w.dot('Early:1'));
});

scenario('the stream: the window holds: a new entity pushes the least recently active one out', () => {
  const w = world(); w.open({ field_window: 4 }, { wire: true });
  w.reply(200, rows(['A:1', 'A', 100.5], ['B:1', 'B', 101.5], ['C:1', 'C', 102.5], ['D:1', 'D', 103.5]));
  w.frame({ eid: 'A:1', cls: 'A', ts: 110.5 });                    // A is active again: B is now the oldest
  w.frame({ eid: 'E:1', cls: 'E', ts: 111.5 });
  const have = ['A:1', 'B:1', 'C:1', 'D:1', 'E:1'].filter((i) => w.dot(i));
  assert.deepEqual(have, ['A:1', 'C:1', 'D:1', 'E:1'], 'B, the least recently active, left the view and the engine');
  assert.equal(w.get('DomNode:f:c:B:1'), null);
  w.frame({ eid: 'F:1', cls: 'F', ts: 112.5 });
  assert.equal(['A:1', 'C:1', 'D:1', 'E:1', 'F:1'].filter((i) => w.dot(i)).length, 4);
});

scenario('the stream: a child that arrives before its owner is joined to it when the owner comes', () => {
  const w = world(); w.open(null, { wire: true }); w.reply(200, rows(['Z:1:kid', 'Z', 100.5]));
  assert.ok(w.dot('Z:1:kid').parent_id == null);
  w.frame({ eid: 'Z:1', cls: 'Z', ts: 101.5 });
  assert.equal(w.dot('Z:1:kid').parent_id, 'Z:1'); assert.equal(w.dot('Z:1').mass, 1);
});

scenario('the stream drops: the poll is the fallback, and the stream is asked for again', () => {
  const w = world(); w.open(null, { wire: true }); w.reply(200, rows(...BASE)); w.ticks(30);
  assert.equal(w.reqs().length, 1, 'no poll while the stream is up');
  const first = w.wireReq().id;
  w.wireEnds('connection reset');
  w.ticks(25); assert.equal(w.reqs().length, 2, 'the stream is gone: the poll resumes');
  assert.notEqual(w.wireReq().id, first, 'and a new stream is asked for'); assert.equal(w.wireReq().url, '/ui/fieldvm/events');
  w.reply(200, rows(...BASE));
  w.frame({ eid: 'New:1', cls: 'New', ts: 400.5 }); assert.ok(w.dot('New:1'), 'frames of the new stream count');
  w.ticks(60); assert.equal(w.reqs().length, 2, 'up again: no more polls');
});

console.log(`field view: ${n} scenarios ok`);
