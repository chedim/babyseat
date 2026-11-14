// The eval stats read the host clock (Date.now) in the wasm build: a long getter must evaluate, not trap (std's Instant panics
// on wasm32-unknown-unknown). Time decides no value (#1143): the result is the same under any clock.
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
assert.equal(JSON.parse(g.get('x1')).big, 600, 'a long getter evaluates under the host clock');
console.log('fieldvm_clock ok');
