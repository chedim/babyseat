// The eval-time allowance reads the host clock (Date.now) in the wasm build: a getter long enough to reach the time check
// (every 256 steps) must evaluate, not trap (std's Instant panics on wasm32-unknown-unknown).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });

const LONG = 'PACK long 1\nThing.big getter: ' + Array(600).fill('1').join(' + ') + '\n';
const g = new Field();
g.set_pack('long', LONG);
assert.deepEqual(JSON.parse(g.pack_errors()), {});
g.load('x1', 'Thing', '{}');
g.write('x1', 'name', '"x"', 5);
assert.equal(JSON.parse(g.get('x1')).big, 600, 'a long getter evaluates with the time check active');
console.log('fieldvm_clock ok');
