// The 📊 stats window draws the empty answer of a seat that counted nothing (tests/golden/boot/stats.json, the released shape; the cliffvm seat's own answer is
// shape-compared to it in native/tests/boot_routes_1143.rs): the page's own `statsRows` (membrane/ui/home.js) must give its zero rows and the "no Claude answers" line.
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const H = createRequire(import.meta.url)(path.join(repo, 'membrane/ui/home.js'));
const body = JSON.parse(readFileSync(path.join(repo, 'tests/golden/boot/stats.json'), 'utf8')).body;
const html = H.statsRows(body);
for (const s of ['since midnight', '0 calls', 'last hour', 'By mind, today', 'no Claude answers since the seat started']) assert.ok(html.includes(s), s);
assert.ok(!html.includes('Out of tokens'), 'nothing is paused');
console.log('stats empty: ok');
