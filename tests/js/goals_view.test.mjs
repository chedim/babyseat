// The seat's Goals tab (membrane/apps/barest.c1f/goals_view.c1f) in the page's field engine: a tab that lists the goals and draws the selected goal's graph
// (tasks, waits, cards) read from the seat by the door's `run_cliff`. The page's other packs are stubs (`dom`, `fetch`, `shell_view`): what is under test is this
// pack's own writes and its door call, driven by writing the entities the shell and the Fetch driver would write.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/apps/barest.c1f/ui/fieldvm_core.js';

const ui = new URL('../../membrane/apps/barest.c1f/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const GOALS = readFileSync(fileURLToPath(new URL('../../membrane/apps/barest.c1f/goals_view.c1f', import.meta.url)), 'utf8');

assert.ok(!/membrama|@NS@/.test(GOALS.replace(/--.*$/gm, '')), 'the code names no peer');

const f = new Field();
f.set_pack('dom', "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n");
f.set_pack('fetch', 'PACK fetch 1\n');
f.set_pack('shell_view', 'PACK shell_view 1\nShell.tick getter: clock\n');
f.set_pack('goals_view', GOALS);
assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'the goals view loads');

const get = (e) => JSON.parse(f.get(e));
const w = (e, k, v, now = 1000) => { f.write(e, k, JSON.stringify(v), now); f.drain(); };
const post = (e) => { const r = get(e).request; return r && JSON.parse(r.body); };
let rid = 0;
const receipt = (value) => ({ id: 'goals-' + (rid + 1), status: 'ok', http_status: 200, body: JSON.stringify({ jsonrpc: '2.0', id: ++rid, result: { content: [{ type: 'text', text: JSON.stringify(value) }] } }) });
// the text of the numbered nodes under a prefix, in order, while they are on the page (a retired node has no parent)
const nodeTexts = (prefix) => {
  const out = [];
  for (let n = 0; n < 80; n++) { const d = get(prefix + n); if (d && d.parent && d.text !== undefined) out.push(d.text); }
  return out;
};

// the shell opens: the tab registers between Chat (10) and Log (85), and draws into its slot when it is shown first
f.load('Shell:main', 'Shell', '{}');
w('Shell:main', 'status_ms', 1000);
w('Shell:main', 'open', true);
assert.equal(get('Nav:goals').title, 'Goals');
assert.ok(get('Nav:goals').order > 10 && get('Nav:goals').order < 85, 'Goals sits between Chat and Log');
w('Nav:goals', 'active', true);
assert.equal(get('DomNode:goals:root').parent, 'DomNode:shell:slot:goals');
assert.equal(get('Nav:goals').filled, true);

// the tick asks the seat for the goals: ONE run_cliff call to the door that reads every Goal, and only reads
w('Shell:main', 'tick', 100, 100000);
const ask = post('Fetch:goals:ask');
assert.equal(get('Fetch:goals:ask').request.url, '/mcp');
assert.equal(ask.method, 'tools/call');
assert.equal(ask.params.name, 'run_cliff');
assert.match(ask.params.arguments.code, /instances\('Goal'\)/);
assert.ok(!/entity_write|\.tell\s*=/.test(ask.params.arguments.code), 'the tab only reads');

// the answer: the goals are listed (the one that is moving first) and that one is shown
const nul = { $null: 'absent' };
const view = {
  tasks: [
    { eid: 'Task:a:k1', title: 'write the pack', status: 'done', parent: nul, waits_on: [], open_blockers: 0, result: 'Result:a:k1:1', receipt: 'tests green' },
    { eid: 'Task:a:k2', title: 'open the PR', status: 'running', parent: nul, waits_on: ['Task:a:k1'], open_blockers: 0, result: nul, receipt: nul },
    { eid: 'Task:a:k3', title: 'dropped idea', status: 'dropped', parent: nul, waits_on: [], open_blockers: 0, result: nul, receipt: nul },
  ],
  waits: [{ eid: 'Wait:a:w1', title: 'CI green', status: 'armed', condition: { method: 'gh.ci' }, blocks: ['Task:a:k4'], waits_on: [], last_check: nul, receipt: nul }],
  cards: [{ eid: 'Decision:a:c1', name: 'Which bug', status: 'open', blocks: ['Task:a:k4'], value: nul }],
  running: ['Task:a:k2'], ready: [],
};
const rowsList = [
  { e: 'Goal:old', s: 'done', ti: nul, ls: 'an old goal', n: 1, mv: false, view: null },
  { e: 'Goal:a', s: nul, ti: 'Ship it', ls: 'ship the thing', n: 3, mv: true, view },
];
w('Fetch:goals:ask', 'request_result', receipt(rowsList), 100001);
assert.equal(get('GoalsView:main').cur, 'Goal:a', 'the goal that is moving is shown first');
const list = nodeTexts('DomNode:goals:g:');
assert.equal(list.length, 2);
assert.match(list[0], /Goal:a/, 'the moving goal is first in the list');
assert.match(list[1], /Goal:old/);
const detail = nodeTexts('DomNode:goals:i:').join('\n');
assert.match(detail, /Ship it/);
assert.match(detail, /open the PR/);
assert.match(detail, /running/);
assert.match(detail, /tests green/, 'a done task shows its receipt');
assert.match(detail, /CI green/, 'the wait is drawn');
assert.match(detail, /Which bug/, 'the card the operator must answer is drawn');
assert.ok(!/dropped idea/.test(detail), 'a dropped task is counted, not drawn');
assert.match(detail, /1 dropped/);

// the next ask names the selected goal (its graph is read for that one only)
w('Shell:main', 'tick', 200, 200000);
assert.match(post('Fetch:goals:ask').params.arguments.code, /Goal:a/);
w('Fetch:goals:ask', 'request_result', receipt(rowsList), 200001);

// a click on another goal selects it, and its graph is asked for at the next tick
w('DomNode:goals:g:1', 'clicked', { at: 1 }, 250000);
assert.equal(get('GoalsView:main').cur, 'Goal:old');
w('Shell:main', 'tick', 300, 300000);
assert.match(post('Fetch:goals:ask').params.arguments.code, /Goal:old/);

// a failed call is said in the tab, and the last list stays
w('Fetch:goals:ask', 'request_result', { id: 'goals-x', status: 'ok', http_status: 500, body: 'broken' }, 300001);
assert.match(get('DomNode:goals:note').text, /did not answer/);
assert.equal(nodeTexts('DomNode:goals:g:').length, 2, 'the list stays');

// a view that is not in front asks for nothing
w('Nav:goals', 'active', false);
const before = get('GoalsView:main').seq;
w('Shell:main', 'tick', 900, 900000);
assert.equal(get('GoalsView:main').seq, before);
console.log('goals_view: ok');
