// The graph link, slice 1 (#1143), the page's CAPABILITIES as data, no browser: what `Host.describe()` reads from the loaded packs (PACK/IMPORT lines, their transitive closure), the DOM allow-list as data
// with a hash, and the placement rule "a pack may run on a VM that provides every pack of its import closure" (docs/graph-link.md, "Placement by imports").
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { pack_facts, closure, eligible, dom_allow_report, DOM_ALLOW, DOM_PACK, FETCH_PACK } from '../../membrane/apps/barest.c1f/ui/fieldvm.js';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const app = path.join(repo, 'membrane/apps/barest.c1f');
const files = (dir) => readdirSync(dir).filter((f) => f.endsWith('.c1f')).map((f) => [f.slice(0, -4), readFileSync(path.join(dir, f), 'utf8')]);

// 1. requires = the pack's own IMPORT lines, parsed here a second way (line by line), for every pack of the page and of the seat
for (const [name, src] of [...files(app), ...files(path.join(repo, 'membrane/packs')), ['dom', DOM_PACK], ['fetch', FETCH_PACK]]) {
  const want = src.split('\n').filter((l) => l.startsWith('IMPORT ')).map((l) => l.split(/\s+/)).map((w) => ({ pack: w[1], major: Number(w[2]) }));
  const f = pack_facts(src);
  assert.deepEqual(f.imports, want, `${name}: requires is the IMPORT lines`);
  assert.equal(f.pack, src.split('\n').find((l) => l.startsWith('PACK ')).split(/\s+/)[1], `${name}: the pack's own name`);
}

// 2. the closure is transitive, in discovery order, leaves the pack itself out, names a pack nobody loaded, and survives a cycle
const f = {
  a: pack_facts('PACK a 1\nIMPORT b 1\nIMPORT dom 1\n'), b: pack_facts('PACK b 2\nIMPORT c 3\n'), c: pack_facts('PACK c 3\nIMPORT a 1\n'), dom: pack_facts(DOM_PACK),
};
assert.deepEqual(closure('a', f), [{ pack: 'b', major: 1 }, { pack: 'dom', major: 1 }, { pack: 'c', major: 3 }]);
assert.deepEqual(closure('dom', f), []);
assert.deepEqual(closure('z', { z: pack_facts('PACK z 1\nIMPORT nowhere 4\n') }), [{ pack: 'nowhere', major: 4 }]);

// 3. placement: the page provides what it loads (dom, fetch and the view packs); the seat what barest.packs loads
const seatPacks = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#'));
const seat = seatPacks.map((n) => { const x = pack_facts(readFileSync(path.join(repo, 'membrane/packs', n + '.c1f'), 'utf8')); return { pack: x.pack, major: x.major }; });
const page = [DOM_PACK, FETCH_PACK].map((s) => { const x = pack_facts(s); return { pack: x.pack, major: x.major }; });
const vms = { seat, page };
const needs = (src, all) => { const g = { ...Object.fromEntries(all.map(([n, s]) => [n, pack_facts(s)])) }; const me = pack_facts(src); g.me = me; return closure('me', g); };
assert.deepEqual(eligible(needs('PACK v 1\nIMPORT dom 1\nIMPORT fetch 1\n', []), vms), { vms: ['page'], conflict: {} }, 'dom + fetch: the page');
assert.deepEqual(eligible(needs('PACK s 1\nIMPORT sql 1\n', []), vms), { vms: ['seat'], conflict: {} }, 'sql: the seat');
const mix = eligible(needs('PACK m 1\nIMPORT dom 1\nIMPORT sql 1\n', []), vms);
assert.deepEqual(mix, { vms: [], conflict: { seat: ['dom'], page: ['sql'] } }, 'dom + sql: no one VM, the conflict names the imports');
assert.deepEqual(eligible(needs('PACK p 1\n', []), vms), { vms: ['seat', 'page'], conflict: {} }, 'a pure pack: both');
assert.deepEqual(eligible([{ pack: 'dom', major: 9 }], vms).vms, [], 'a major the VM does not provide is not provided');
// a pack that imports a page-only pack, through another pack: the closure decides
const chain = [['viewlib', 'PACK viewlib 1\nIMPORT dom 1\n']];
assert.deepEqual(eligible(needs('PACK w 1\nIMPORT viewlib 1\n', chain), { seat, page: [...page, { pack: 'viewlib', major: 1 }] }).vms, ['page'], 'dom comes in through viewlib');

// 4. the DOM allow-list as data: the same words as DOM_ALLOW, and a hash that changes with it
const rep = dom_allow_report();
assert.deepEqual(rep.tags, DOM_ALLOW.tags.split(' '));
assert.deepEqual(rep.svgTags, DOM_ALLOW.svgTags.split(' '));
assert.deepEqual(rep.attrs, DOM_ALLOW.attrs.split(' '));
assert.deepEqual(rep.refused, Object.keys(DOM_ALLOW.refused));
assert.match(rep.hash, /^[0-9a-f]{8}$/);
assert.equal(dom_allow_report().hash, rep.hash, 'the hash is stable');
const was = DOM_ALLOW.tags; DOM_ALLOW.tags += ' video';
assert.notEqual(dom_allow_report().hash, rep.hash, 'a changed list is a changed hash');
assert.ok(dom_allow_report().tags.includes('video'));
DOM_ALLOW.tags = was;
console.log('link caps: ok');
