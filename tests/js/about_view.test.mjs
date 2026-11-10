// The About window as a cliffhanger app (the operator, 2026-11-10): membrane/packs/about.c1f runs in the page's field
// engine (it imports `dom`); the seat only feeds About:seat {version, sha, changelog}.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const ABOUT = readFileSync(fileURLToPath(new URL('../../membrane/packs/about.c1f', import.meta.url)), 'utf8');
const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";

const f = new Field();
f.set_pack('dom', DOM);
f.set_pack('about', ABOUT);
assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'about loads once dom is there');

const log = "## v0.7.5 (2026-11-10) — the field engine\n\nIntro line.\n\n- **Packs** are behaviour.\n- A `.c1f` line.\n\n### Fixes\n\n- one fix\n\n## v0.7.4 (2026-10-30) — older\n\n- old line\n";
const now = 1000;
f.load('About:seat', 'About', '{}');
for (const [k, v] of [['version', '0.7.5'], ['sha', 'ee08067'], ['changelog', log]]) f.write('About:seat', k, JSON.stringify(v), now);
f.drain();
const get = (e) => JSON.parse(f.get(e));

const btn = get('DomNode:about:btn');
assert.equal(btn.tag, 'button'); assert.equal(btn.parent, 'DomNode:page:#topbar'); assert.equal(btn.text, 'ⓘ');
const win = get('DomNode:about:win');
assert.equal(win.hidden, true, 'the window starts closed');
assert.equal(get('DomNode:about:name').text, 'babyseat 0.7.5 · ee08067');
assert.equal(get('DomNode:about:release').text, 'v0.7.5 (2026-11-10) — the field engine');
const items = ['Intro line.', '- **Packs** are behaviour.', '- A `.c1f` line.', '### Fixes', '- one fix'];
const shown = items.map((l) => get('DomNode:about:l:' + l));
assert.ok(shown.every((n) => n && n.parent === 'DomNode:about:list'), 'every line of this release is a node');
assert.equal(shown[1].text, 'Packs are behaviour.'); assert.equal(shown[1].tag, 'li');
assert.equal(shown[3].tag, 'h4'); assert.equal(shown[3].text, 'Fixes');
assert.equal(shown[0].tag, 'p');
assert.equal(f.get('DomNode:about:l:- old line'), 'null', 'an older release is not drawn');
assert.ok(String(get('DomNode:about:full').href).startsWith('https://'), 'a link to the whole changelog');

// the button opens and closes it; the × closes it
f.write('DomNode:about:btn', 'clicked', String(now + 1), now + 1); f.drain();
assert.equal(get('DomNode:about:win').hidden, false, 'a click opens it');
f.write('DomNode:about:x', 'clicked', String(now + 2), now + 2); f.drain();
assert.equal(get('DomNode:about:win').hidden, true, 'the × closes it');
f.write('DomNode:about:btn', 'clicked', String(now + 3), now + 3); f.drain();
f.write('DomNode:about:btn', 'clicked', String(now + 4), now + 4); f.drain();
assert.equal(get('DomNode:about:win').hidden, true, 'a second click closes it');
console.log('ok');
