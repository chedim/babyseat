// A pack's `retire = true` in the browser VM (fieldvm.js), in headless Chrome: the entity leaves the page's engine and, for a DomNode, its element and its
// children's elements leave the page (the seat's engine removes a retired entity; the page's host must too).
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 20000 + Math.floor(Math.random() * 1000), dbg = port + 1000;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const prof = mkdtempSync(path.join(tmpdir(), 'fieldvm-'));
const srv = spawn('python3', ['-m', 'http.server', String(port), '--bind', '127.0.0.1'], { cwd: repo, stdio: 'ignore' });
const br = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${prof}`,
  `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
try {
  let tabs;
  for (let i = 0; i < 50 && !tabs; i++) { await sleep(200); try { tabs = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch {} }
  const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r, { once: true }));
  let id = 0; const waiting = new Map();
  ws.addEventListener('message', (m) => { const d = JSON.parse(m.data); if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); } });
  const send = (method, params = {}) => new Promise(r => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Page.navigate', { url: `http://127.0.0.1:${port}/tests/js/fieldvm_retire.html` });
  let r;
  for (let i = 0; i < 150; i++) {
    await sleep(200);
    const v = await send('Runtime.evaluate', { expression: "document.getElementById('result')?.textContent || ''", returnByValue: true });
    const t = v.result?.result?.value || '';
    if (t.startsWith('{') && JSON.parse(t).done) { r = JSON.parse(t); break; }
  }
  ws.close();
  assert.ok(r, 'the page never finished');
  assert.equal(r.error, undefined, r.error);
  assert.deepEqual(r.errors, {});
  assert.deepEqual(r.before, { a: true, kid: true, thing: true });
  assert.deepEqual(r.after.thing, null, 'a retired entity leaves the engine');
  assert.deepEqual(r.after.aEntity, null, 'a retired DomNode leaves the engine');
  assert.equal(r.after.aElement, false, 'its element leaves the page');
  assert.equal(r.after.kidElement, false, 'and the elements under it');
  assert.equal(r.after.bElement, true, 'a sibling stays');
  assert.deepEqual(r.after.nodes, ['DomNode:t:b'], 'the host forgets the removed nodes');
  console.log('fieldvm retire: ok');
} finally { br.kill(); srv.kill(); await sleep(200); rmSync(prof, { recursive: true, force: true }); }
