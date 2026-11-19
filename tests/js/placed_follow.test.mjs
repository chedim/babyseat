// The page follows the packs placed on it (#1143, docs/graph-link.md section 16): `Link` with `follow_placed` asks for `Placed:<ns>` (the names), then for each `Placed:<ns>:<name>` (the text), loads what arrives with
// `host.pack` while the page runs, retries a pack whose import arrives later, and unloads a name that leaves the list. Node, no browser: stubbed engine hooks and a fake fetch, as in link_client.test.mjs.
import assert from 'node:assert/strict';
import { Link } from '../../membrane/apps/barest.c1f/ui/fieldvm.js';

function world() {
  const w = { posts: [], micro: [], timers: [], packs: [], loaded: new Set() };
  const f = { link_reset() {}, feed_vals() {}, feed_refused() {}, feed_gone() {} };
  const host = {
    f, now: () => 1000, flush() {}, feed() {},
    pack(name, src) {                                   // the page's own: a pack whose import is not loaded yet is an error, an empty text unloads
      w.packs.push([name, src]);
      if (src === '') { w.loaded.delete(name); return null; }
      const need = [...src.matchAll(/^IMPORT (\w+) /gm)].map((m) => m[1]).filter((n) => !['dom'].includes(n));
      if (need.some((n) => !w.loaded.has(n))) return `imports ${need.join(',')}, which isn't loaded`;
      w.loaded.add(name); return null;
    },
  };
  const fetch = (url, init) => new Promise((resolve) => { w.posts.push({ body: JSON.parse(init.body), resolve }); });
  w.link = new Link(host, { ns: 'p1a2b3c4d', fetch, schedule: (fn) => w.micro.push(fn), after: (fn, ms) => w.timers.push({ fn, ms }), random: () => 0.5, follow_placed: true });
  w.run = () => { while (w.micro.length) w.micro.shift()(); };
  w.say = async (i, body) => { w.posts[i].resolve({ ok: true, status: 200, json: async () => body }); for (let k = 0; k < 6; k++) await Promise.resolve(); w.run(); };
  return w;
}
const E = 'epoch-1', IDX = 'membrane::Placed:p1a2b3c4d', P = (n) => `${IDX}:${n}`;

const w = world();
w.link.start(); w.run();
assert.deepEqual(w.posts[0].body.subs, [[IDX, 'names']], 'the first frame asks for the names of the packs placed on this page');
await w.say(0, { v: 1, epoch: E, n: 1, vals: [{ id: IDX, prop: 'names', value: ['a', 'b'] }] });
const asked = w.posts.at(-1).body.subs.map((s) => s.join('.'));
assert.deepEqual(asked.sort(), [`${P('a')}.text`, `${P('b')}.text`], 'then for the text of each name');
// b imports a and arrives first: it does not load yet; a arrives and both load
await w.say(w.posts.length - 1, { v: 1, epoch: E, n: 2, vals: [
  { id: P('b'), prop: 'text', value: 'PACK b 1\nIMPORT a 1\n' },
  { id: P('a'), prop: 'text', value: 'PACK a 1\nIMPORT dom 1\n' }] });
assert.deepEqual([...w.loaded].sort(), ['a', 'b'], 'a pack whose import arrives later is loaded when it does');
// a name that leaves the list is unloaded and unsubscribed
await w.say(w.posts.length - 1, { v: 1, epoch: E, n: 3, vals: [{ id: IDX, prop: 'names', value: ['a'] }] });
assert.ok(!w.loaded.has('b') && w.loaded.has('a'), 'b left the list: it is unloaded, a stays');
assert.deepEqual(w.posts.at(-1).body.unsubs, [[P('b'), 'text']], 'and the page stops asking for it');
// a text that changes is loaded again (a replaced pack)
await w.say(w.posts.length - 1, { v: 1, epoch: E, n: 4, vals: [{ id: P('a'), prop: 'text', value: 'PACK a 1\nIMPORT dom 1\n-- v2\n' }] });
assert.equal(w.packs.at(-1)[1], 'PACK a 1\nIMPORT dom 1\n-- v2\n', 'a changed text replaces the pack');
console.log('placed follow: ok');
