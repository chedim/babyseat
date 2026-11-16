// DOM v2 of the browser VM (fieldvm.js), in headless Chrome over the DevTools protocol: form and SVG tags, the attribute
// allow-list (one table, DOM_ALLOW), refused names with a visible reason, events written back as entity writes, and a pack's
// writes onto the element (value, disabled, focused) without an echo loop.
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
  await send('Page.navigate', { url: `http://127.0.0.1:${port}/tests/js/fieldvm_shell.html` });
  let r;
  for (let i = 0; i < 150; i++) {
    await sleep(200);
    const v = await send('Runtime.evaluate', { expression: "document.getElementById('result')?.textContent || ''", returnByValue: true });
    const t = v.result?.result?.value || '';
    if (t.startsWith('{') && JSON.parse(t).done) { r = JSON.parse(t); break; }
  }
  ws.close();
  if (process.env.SHOW) console.log(JSON.stringify(r, null, 1));
  assert.ok(r, 'the page never finished');
  assert.equal(r.error, undefined, r.error);
  assert.deepEqual(r.errors, {});

  // 6. the clock driver
  assert.deepEqual(r.clock, { before: 0, after: [700], ticks: 2, restarted: [100], stopped: 0 }, 'run_clock starts ONE timer (a second start replaces it), each fire is a Field.tick, stop_clock clears it');
  // 7. the scroll write
  assert.ok(r.scroll.canScroll, 'the log overflows');
  assert.equal(r.scroll.start, 0);
  assert.equal(r.scroll.top10, 10, "a pack's {top: n}");
  assert.ok(r.scroll.end, "a pack's 'end' puts the log at the bottom");
  assert.ok(r.scroll.field && r.scroll.field.height > r.scroll.field.client && r.scroll.field.top > 0, 'the field holds the measured scroll after the write');
  assert.ok(r.scroll.batch, 'a scroll write in the same batch as the rows lands after them (at the bottom)');
  // 8. ordered children
  assert.deepEqual(r.before.start, ['a', 'b']);
  assert.deepEqual(r.before.batch, ['c', 'd', 'a', 'b'], 'two nodes made in one batch with before=a land in order above a');
  assert.deepEqual(r.before.moved, ['b', 'c', 'd', 'a'], 'a later write of before moves a node')
  assert.deepEqual(r.before.stable, ['b', 'c', 'd', 'a'], 'an unrelated write does not reorder');
  // 9. composing
  assert.deepEqual(r.keys, [true, false], 'the key event carries composing');
  // 10. hidden
  assert.deepEqual(r.hidden, [false, true, false], 'Conn:page.hidden follows the page');
  console.log('fieldvm_shell ok');
} finally { br.kill(); srv.kill(); await sleep(300); rmSync(prof, { recursive: true, force: true }); }
