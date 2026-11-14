// The graph link, slice 2, the PAGE GLUE (#1143, docs/graph-link.md "Slice 2: built"): `Link` in fieldvm.js, the host's part of the link, on STUBBED engine hooks (docs/graph-link.md 15b: `drain()` carries `subs` / `unsubs`,
// `feed_vals`, `feed_refused`, `feed_gone`, `link_reset`) and a fake `fetch`. Node, no browser, no wall clock: the test drives the microtask schedule and the retry timer itself, and COUNTS frames (POSTs), their bodies
// and the calls into the engine. What is asserted:
//   1. ONE frame per flush: many notes before the schedule runs are one POST, subs flat [[eid, prop]];
//   2. a reply's values are fed per entity with the reply's n as the revision, and the next frame acks it;
//   3. an idle page: exactly ONE request in flight (the held poll), one more per empty reply (the keepalive), nothing else;
//   4. news while a poll is held is a second POST at once; the released poll (`superseded`) does not start a third;
//   5. a hidden page sends ONE frame ({hidden: true}) and nothing more, however much the engine notes; visible again it sends ONE frame with `resync` and everything queued;
//   6. a changed epoch or `reset` is `link_reset()`, the ack back to 0, and the engine's full set sent again;
//   7. a failed POST is retried (the SAME frame) after a jitter of 0 to 3 s (growing to 30 s while it keeps failing), `Link:page.up` is false meanwhile;
//   8. `refused` rows reach the engine as `feed_refused`; a duplicate or superseded reply feeds nothing; the 501st replica is refused here, never fed;
//   9. a page whose engine has no link hooks (an older wasm) never starts: no POST, `up` is not set.
import assert from 'node:assert/strict';
import { Link } from '../../membrane/apps/barest.c1f/ui/fieldvm.js';

function world({ hooks = true, random = () => 0.5 } = {}) {
  const w = { t: 1000, posts: [], timers: [], micro: [], engine: [], fed: [], drains: [] };
  const f = {
    link_reset() { w.engine.push(['link_reset']); w.drains.push({ subs: w.all.map(([eid, props]) => ({ eid, props })), unsubs: [] }); },
    feed_vals(eid, props, rev, now) { w.engine.push(['vals', eid, JSON.parse(props), rev]); },
    feed_refused(eid, why, rev, now) { w.engine.push(['refused', eid, why, rev]); },
    feed_gone(eid, rev, now) { w.engine.push(['gone', eid, rev]); },
  };
  if (!hooks) for (const k of Object.keys(f)) delete f[k];
  w.all = [];                                                      // every live subscription the stub engine would re-announce after link_reset
  const host = { f, now: () => w.t, flush() { w.flushes = (w.flushes || 0) + 1; const d = w.drains.shift(); if (d) link.note(d); }, feed(eid, cls, props) { w.fed.push([eid, props]); } };
  const fetch = (url, init) => new Promise((resolve, reject) => { w.posts.push({ url, body: JSON.parse(init.body), init, resolve, reject }); });
  const link = new Link(host, { ns: 'p1a2b3c4d', fetch, schedule: (fn) => w.micro.push(fn), after: (fn, ms) => w.timers.push({ fn, ms }), random });
  w.link = link;
  w.run = () => { while (w.micro.length) w.micro.shift()(); };
  w.say = async (i, body, status = 200) => { w.posts[i].resolve({ ok: status < 300, status, json: async () => body }); for (let k = 0; k < 6; k++) await Promise.resolve(); w.run(); };
  w.fail = async (i) => { w.posts[i].reject(new Error('network down')); for (let k = 0; k < 6; k++) await Promise.resolve(); w.run(); };
  return w;
}
const sub = (eid, ...props) => ({ eid, props });
const E = 'epoch-1';

{ // 1. ONE frame per flush
  const w = world();
  w.link.start(); w.run();
  assert.equal(w.posts.length, 1, 'the first frame (the poll that also says hello)');
  assert.equal(w.posts[0].url, '/ui/link');
  assert.deepEqual(w.posts[0].body, { v: 1, ns: 'p1a2b3c4d', n: 1, ack: 0, hidden: false, epoch: '', subs: [], unsubs: [] });
  await w.say(0, { v: 1, epoch: E, n: 0, vals: [] });
  assert.equal(w.posts.length, 2, 'an empty answer: the next poll, one request in flight');
  w.link.note({ subs: [sub('membrane::Setting:a', 'current', 'label')], unsubs: [] });
  w.link.note({ subs: [sub('membrane::Setting:b', 'current')], unsubs: [sub('membrane::Setting:z', 'current')] });
  assert.equal(w.posts.length, 2, 'nothing is sent before the schedule runs');
  w.run();
  assert.equal(w.posts.length, 3, 'two notes, ONE more frame');
  assert.deepEqual(w.posts[2].body.subs, [['membrane::Setting:a', 'current'], ['membrane::Setting:a', 'label'], ['membrane::Setting:b', 'current']]);
  assert.deepEqual(w.posts[2].body.unsubs, [['membrane::Setting:z', 'current']]);
  assert.equal(w.posts[2].body.n, 3, 'frame numbers count every POST');
  assert.equal(w.posts[2].body.epoch, E, 'the epoch the page last saw');
}
{ // 2. values fed per entity, rev = the seat's n; the next frame acks; 4. news while held; the superseded poll starts nothing
  const w = world();
  w.link.start(); w.run();
  await w.say(0, { v: 1, epoch: E, n: 0, vals: [] });               // poll #2 is held (posts[1])
  w.link.note({ subs: [sub('membrane::Setting:a', 'current', 'label')], unsubs: [] }); w.run();   // posts[2]: news at once
  assert.equal(w.posts.length, 3);
  await w.say(1, { v: 1, epoch: E, n: 0, superseded: true, vals: [] });
  assert.equal(w.posts.length, 3, 'the released poll starts no third request: the news frame is still in flight');
  assert.deepEqual(w.engine, []);
  await w.say(2, { v: 1, epoch: E, n: 1, resync: false, vals: [{ id: 'membrane::Setting:a', prop: 'current', value: 7 }, { id: 'membrane::Setting:a', prop: 'label', value: 'A' }] });
  assert.deepEqual(w.engine, [['vals', 'membrane::Setting:a', { current: 7, label: 'A' }, 1]], 'one feed per entity, the seat frame number as the revision');
  assert.equal(w.posts.length, 4, 'then exactly one poll again');
  assert.equal(w.posts[3].body.ack, 1, 'and it acks what it applied');
}
{ // 3. idle: one request per empty reply
  const w = world();
  w.link.start(); w.run();
  for (let i = 0; i < 5; i++) await w.say(i, { v: 1, epoch: E, n: 0, vals: [] });
  assert.equal(w.posts.length, 6, 'five keepalive answers, five more polls: one request per hold, never two');
  assert.equal(w.timers.length, 0, 'no timer of its own while the seat answers');
  assert.deepEqual(w.engine, []);
}
{ // 5. hidden: one frame, then silence; visible: one frame with resync and the queue
  const w = world();
  w.link.start(); w.run();
  await w.say(0, { v: 1, epoch: E, n: 0, vals: [] });
  w.link.visibility(true); w.run();
  assert.equal(w.posts.length, 3);
  assert.equal(w.posts[2].body.hidden, true);
  await w.say(1, { v: 1, epoch: E, n: 0, superseded: true, vals: [] });
  await w.say(2, { v: 1, epoch: E, n: 0, paused: true, vals: [] });
  assert.equal(w.posts.length, 3, 'a hidden page sends nothing after its one frame');
  w.link.note({ subs: [sub('membrane::Setting:a', 'current')], unsubs: [] }); w.run();
  w.link.note({ subs: [sub('membrane::Setting:b', 'current')], unsubs: [] }); w.run();
  assert.equal(w.posts.length, 3, 'not even when the engine names new things');
  w.link.visibility(false); w.run();
  assert.equal(w.posts.length, 4);
  assert.equal(w.posts[3].body.hidden, false);
  assert.equal(w.posts[3].body.resync, true);
  assert.deepEqual(w.posts[3].body.subs, [['membrane::Setting:a', 'current'], ['membrane::Setting:b', 'current']], 'everything queued while hidden, in ONE frame');
  const h = world(); h.link.visibility(true); h.link.start(); h.run();
  assert.equal(h.posts.length, 0, 'a page that loads hidden sends nothing at all');
}
{ // 6. epoch change and reset: link_reset, ack 0, the full set again
  const w = world();
  w.all = [['membrane::Setting:a', ['current']], ['membrane::Setting:b', ['label']]];
  w.link.start(); w.run();
  await w.say(0, { v: 1, epoch: E, n: 1, vals: [{ id: 'membrane::Setting:a', prop: 'current', value: 1 }] });
  assert.equal(w.posts[1].body.ack, 1);
  await w.say(1, { v: 1, epoch: 'epoch-2', n: 0, vals: [] });           // the seat restarted
  assert.deepEqual(w.engine.filter((x) => x[0] === 'link_reset').length, 1);
  assert.equal(w.posts[2].body.ack, 0, 'the numbering of the new seat starts again');
  assert.deepEqual(w.posts[2].body.subs, [['membrane::Setting:a', 'current'], ['membrane::Setting:b', 'label']], 'every live subscription is announced again, in one frame');
  assert.equal(w.posts[2].body.epoch, 'epoch-2');
  await w.say(2, { v: 1, epoch: 'epoch-2', n: 0, reset: true, vals: [] });
  assert.equal(w.engine.filter((x) => x[0] === 'link_reset').length, 2, 'a reset answer does the same');
}
{ // 7. a failed POST: the same frame again after a jitter, never a storm; up is false meanwhile
  const w = world({ random: () => 1 });
  w.link.start(); w.run();
  await w.say(0, { v: 1, epoch: E, n: 0, vals: [] });
  assert.deepEqual(w.fed.filter(([e]) => e === 'Link:page').pop()[1], { up: true });
  await w.fail(1);
  assert.deepEqual(w.fed.filter(([e]) => e === 'Link:page').pop()[1], { up: false });
  assert.equal(w.posts.length, 2, 'no request until the jitter is over');
  assert.equal(w.timers.length, 1);
  assert.ok(w.timers[0].ms >= 0 && w.timers[0].ms <= 3000, `the first retry is within 3 s: ${w.timers[0].ms}`);
  w.timers.shift().fn();
  assert.equal(w.posts.length, 3);
  assert.equal(w.posts[2].body.n, w.posts[1].body.n, 'the SAME frame is sent again (the seat drops a duplicate by n)');
  await w.fail(2);
  assert.ok(w.timers[0].ms > 3000 && w.timers[0].ms <= 30000, `it backs off while it keeps failing: ${w.timers[0].ms}`);
  w.timers.shift().fn();
  await w.say(3, { v: 1, epoch: E, n: 0, dup: true, vals: [] });
  assert.deepEqual(w.fed.filter(([e]) => e === 'Link:page').pop()[1], { up: true });
}
{ // 8. refused, duplicate, superseded, the replica cap
  const w = world();
  w.link.start(); w.run();
  await w.say(0, { v: 1, epoch: E, n: 1, vals: [{ id: 'membrane::Chat:main', refused: 'refused: chat is never sent to a page' }, { id: 'membrane::Setting:a', prop: 'current', value: 1 }] });
  assert.deepEqual(w.engine, [['refused', 'membrane::Chat:main', 'refused: chat is never sent to a page', 1], ['vals', 'membrane::Setting:a', { current: 1 }, 1]]);
  w.engine.length = 0;
  await w.say(1, { v: 1, epoch: E, n: 1, dup: true, vals: [{ id: 'membrane::Setting:a', prop: 'current', value: 99 }] });
  assert.deepEqual(w.engine, [], 'a duplicate answer feeds nothing');
  const c = world();
  c.link.start(); c.run();
  const many = Array.from({ length: 502 }, (_, i) => ({ id: `membrane::Setting:r${i}`, prop: 'current', value: i }));
  await c.say(0, { v: 1, epoch: E, n: 1, vals: many });
  assert.equal(c.engine.filter((x) => x[0] === 'vals').length, 500, '500 replicas per page');
  assert.deepEqual(c.engine.filter((x) => x[0] === 'refused').map((x) => x[1]), ['membrane::Setting:r500', 'membrane::Setting:r501']);
  assert.match(c.engine.find((x) => x[0] === 'refused')[2], /too many replicas/);
}
{ // 9. an engine without the hooks: the link does not start
  const w = world({ hooks: false });
  w.link.start(); w.run();
  assert.equal(w.posts.length, 0);
  assert.deepEqual(w.fed, []);
}
console.log('link client: ok');
