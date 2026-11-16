// Pointer and wheel events (fieldvm.js `EVENTS`, `event()`), in headless Chrome: a press, a drag move (button held) and a release are written as the DomNode's `pointer`
// {type, x, y, buttons, time} (x, y in the element's own box, so a pack can turn them into a drag); a hover with no button writes nothing; a wheel is `wheel` {dy, x, y, time} and does not scroll the page.
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
  await send('Page.navigate', { url: `http://127.0.0.1:${port}/tests/js/fieldvm_pointer.html` });
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
  assert.equal(r.hover, null, 'a hover with no button is not written');
  const p = (o, type, x, y, b) => { assert.equal(o.type, type); assert.equal(Math.round(o.x), x); assert.equal(Math.round(o.y), y); assert.equal(o.buttons, b); assert.equal(typeof o.time, 'number'); };
  p(r.down, 'pointerdown', 10, 20, 1); p(r.move, 'pointermove', 30, 25, 1); p(r.up, 'pointerup', 30, 25, 0);
  assert.equal(r.wheel.dy, 120); assert.equal(Math.round(r.wheel.x), 7); assert.equal(Math.round(r.wheel.y), 8); assert.equal(typeof r.wheel.time, 'number');
  assert.equal(r.wheelPrevented, true, 'a wheel over the element does not scroll the page');
  console.log('fieldvm pointer: ok');
} finally { br.kill(); srv.kill(); await sleep(200); rmSync(prof, { recursive: true, force: true }); }
