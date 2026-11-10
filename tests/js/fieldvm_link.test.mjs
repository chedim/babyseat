// The page's own DOM linked into the field VM (fieldvm.js `link`), checked in headless Chrome over the DevTools
// protocol: elements become DomNode:page:* entities (shadow roots too), a pack reacts to a page change and writes
// another page element (without echo), a new element is born, a removed one leaves the field.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const port = 18000 + Math.floor(Math.random() * 1000), dbg = port + 1000;
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
  await send('Page.navigate', { url: `http://127.0.0.1:${port}/tests/js/fieldvm_link.html` });
  let r;
  for (let i = 0; i < 100; i++) {
    await sleep(200);
    const v = await send('Runtime.evaluate', { expression: "document.getElementById('result')?.textContent || ''", returnByValue: true });
    const t = v.result?.result?.value || '';
    if (t.startsWith('{') && JSON.parse(t).done) { r = JSON.parse(t); break; }
  }
  ws.close();
  if (process.env.SHOW) console.log(r);
  assert.ok(r, 'the page never finished');
  assert.equal(r.error, undefined, r.error);
  assert.deepEqual(r.errors, {});
  assert.equal(r.linked, true);
  assert.match(r.parent, /^DomNode:page:/);
  assert.equal(r.shadow, 'DomNode:page:#sh');
  assert.equal(r.reacted, 'saw hello');
  assert.equal(r.mirror_out, 'saw hello');
  assert.equal(r.born.tag, 'li'); assert.equal(r.born.class, 'new'); assert.equal(r.born.parent, 'DomNode:page:#list');
  assert.equal(r.gone, null);
  console.log('fieldvm link: ok');
} finally { br.kill(); srv.kill(); await sleep(300); rmSync(prof, { recursive: true, force: true }); }
