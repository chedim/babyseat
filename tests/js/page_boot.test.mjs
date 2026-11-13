// The current page against the full-set cliffvm seat (scripts/seat-full), checked in headless Chrome over the DevTools protocol:
// the page is loaded from loopback (so it is given its owner cookie) and every request it makes on boot is recorded. What is asserted:
//   1. nothing hangs: every request the page made got an answer (an event stream counts when its headers came);
//   2. nothing is refused: no 401 (the cookie the page was given is accepted on every /ui/serve/ call);
//   3. every call the page makes (its files under /ui/www/ aside) is answered by a route (not 404), EXCEPT the ones listed in MISSING below:
//      the backlog of the swap (docs/release-parity.md). The lists are checked both ways: a route that now answers must leave its list.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const port = 19000 + Math.floor(Math.random() * 1000), dbg = port + 1000;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
// THE BACKLOG, measured on the first full-set boot (docs/release-parity.md section 6). Matched on the path without the query, METHOD included for
// the hangs. Each list is checked both ways: a call that is answered now must leave its list (the test says so), a new gap fails the test.
//   MISSING (404): `map/locate` is a REASONED 404, the released answer "<eid> is not in this projection" (the empty field has no entity; home.js `_trackNb` catches it and tries the next candidate: it stays here, tolerated).
//   Also the session routes the chat view tolerates (<sid>/voice and <sid>/predict: `.catch` in panel.js), /agent and /config (opencode probes); /ui/fieldvm (the browser VM's start snapshot), /membrane/options and the
//   map routes are used routes not built yet (docs/release-parity.md section 1, rows 4, 5, 8, 61-68).
//   HANGS (no answer): none (POST /membrane/voice/state used to wait on the Python voice table a fresh store does not have; it reads the seat's own table now).
//   NOT_BUILT (501, a deliberate "not built" with its reason): informational, never a failure.
const MISSING = new Set(process.env.PAGE_BOOT_MISSING ? process.env.PAGE_BOOT_MISSING.split(',') : [
  '/ui/serve/session/<sid>/voice', '/ui/serve/session/<sid>/predict',
  '/ui/fieldvm', '/ui/serve/membrane/map/locate',
]);
const HANGS = new Set(process.env.PAGE_BOOT_HANGS ? process.env.PAGE_BOOT_HANGS.split(',') : []);
if (!existsSync(bin)) { console.log(`page boot: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`page boot: skipped, no Chrome at ${chrome} (set CHROME)`); process.exit(0); }
const data = mkdtempSync(path.join(tmpdir(), 'seat-full-'));
const prof = mkdtempSync(path.join(tmpdir(), 'page-boot-'));
const seat = spawn('sh', [path.join(repo, 'scripts/seat-full'), '--data-dir', path.join(data, 'd'), '--port', String(port)],
  { cwd: repo, stdio: 'ignore', env: { ...process.env, CLIFFVM: bin, CLAUDE_BIN: '/usr/bin/true' } });
const br = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${prof}`,
  `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
try {
  for (let i = 0; i < 100; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/`)).ok) break; } catch {} await sleep(200); }
  let tabs;
  for (let i = 0; i < 50 && !tabs; i++) { await sleep(200); try { tabs = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch {} }
  const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const waiting = new Map(); const seen = new Map(); const errors = [];
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    const p = d.params || {};
    if (d.method === 'Network.requestWillBeSent') seen.set(p.requestId, { url: p.request.url, method: p.request.method, status: null, failed: null });
    else if (d.method === 'Network.responseReceived' && seen.has(p.requestId)) seen.get(p.requestId).status = p.response.status;
    else if (d.method === 'Network.loadingFailed' && seen.has(p.requestId)) seen.get(p.requestId).failed = p.errorText;
    else if (d.method === 'Runtime.exceptionThrown') errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
  });
  const send = (method, params = {}) => new Promise(r => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Network.enable'); await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: `http://127.0.0.1:${port}/` });
  await sleep(Number(process.env.PAGE_BOOT_WAIT_MS || 8000));
  const rows = [...seen.values()].filter(r => r.url.startsWith(`http://127.0.0.1:${port}/`))
    .map(r => ({ ...r, path: new URL(r.url).pathname.replace(/\/session\/ses_\d+/, '/session/<sid>') }));   // a session id is made at run time
  const served = rows.filter(r => r.path.startsWith('/ui/serve/'));
  if (process.env.SHOW) { for (const r of rows) console.log(r.status ?? (r.failed || 'HANG'), r.method, r.path); console.log('exceptions:', errors); }
  const calls = rows.filter(r => !r.path.startsWith('/ui/www/') && r.path !== '/');
  const hung = new Set(rows.filter(r => r.status === null && !r.failed).map(r => `${r.method} ${r.path}`));
  assert.deepEqual([...hung].filter(h => !HANGS.has(h)), [], 'requests with no answer (a hang) that are not on the backlog list');
  assert.deepEqual([...HANGS].filter(h => !hung.has(h) && rows.some(r => `${r.method} ${r.path}` === h)), [], 'hangs on the backlog list that now answer: take them off HANGS');
  assert.deepEqual(rows.filter(r => r.status === 401).map(r => `${r.method} ${r.path}`), [], 'refused with 401');
  assert.ok(rows.some(r => r.path === '/' && r.status === 200), 'the page itself was served');
  assert.ok(served.length > 0, 'the page made no /ui/serve/ call on boot');
  assert.deepEqual(rows.filter(r => r.path.startsWith('/ui/www/') && r.status !== 200).map(r => r.path), [], 'the page files all load');
  assert.deepEqual(errors, [], 'script exceptions on boot');
  const notFound = new Set(calls.filter(r => r.status === 404).map(r => r.path));
  assert.deepEqual([...notFound].filter(p => !MISSING.has(p)), [], 'boot calls with no route that are not on the backlog list');
  assert.deepEqual([...MISSING].filter(p => calls.some(r => r.path === p) && !notFound.has(p)), [], 'routes on the backlog list that now answer: take them off MISSING');
  const notBuilt = [...new Set(calls.filter(r => r.status === 501).map(r => r.path))].sort();
  console.log(`page boot: ok (${served.length} /ui/serve calls; 404: ${[...notFound].sort().join(' ')}; hang: ${[...hung].join(' ')}; 501: ${notBuilt.join(' ')})`);
  ws.close();
} finally { br.kill(); seat.kill(); await sleep(300); rmSync(prof, { recursive: true, force: true }); rmSync(data, { recursive: true, force: true }); }
