// The field engine compiled to WebAssembly (membrane/ui/fieldvm_core*), run in Node: the same build the browser loads.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });

const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";
const DEMO = [
  "PACK demo 1", "IMPORT goalgraph 1", "IMPORT dom 1",
  "Task.status on-setter row:",
  "  ALWAYS:",
  "    ('DomNode:demo:' + this.eid).task = this.eid",
  "    ('DomNode:demo:' + this.eid).text = this.name + ': ' + this.status",
  "DomNode.clicked on-setter finish:",
  "  WHEN this.task: this.task.status = 'done'",
].join('\n') + '\n';

const f = new Field();
assert.throws(() => f.set_pack('demo', DEMO), /imports dom 1, which isn't loaded/);
f.set_pack('dom', DOM);
assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'demo activates once dom arrives');

// data arrives as writes, so the view's setters fire (a birth's getters compute silently)
const now = 1000;
f.load('g1', 'Goal', '{}'); f.write('g1', 'status', '"open"', now);
f.load('t1', 'Task', '{}');
for (const [k, v] of [['goal', 'g1'], ['name', 'ship it'], ['status', 'open']]) f.write('t1', k, JSON.stringify(v), now);
let out = JSON.parse(f.drain());
assert.ok(out.writes.some(w => w.born === 'DomNode' && w.eid === 'DomNode:demo:t1'), 'the row was created');
assert.equal(JSON.parse(f.get('DomNode:demo:t1')).text, 'ship it: owed', 'no blockers: the goal graph made it owed');
assert.equal(JSON.parse(f.get('DomNode:demo:t1')).tag, 'div', "dom's getter ran on the new node");

// a click is a write; the view's setter finishes the task through the goal graph
f.write('DomNode:demo:t1', 'clicked', String(now + 1), now + 1);
assert.equal(JSON.parse(f.get('t1')).status, 'done');
assert.equal(JSON.parse(f.get('DomNode:demo:t1')).text, 'ship it: done');
assert.equal(JSON.parse(f.get('g1')).replan, 'task t1 done', 'the goal graph replanned, as on the seat');
console.log('fieldvm wasm: ok');
