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
  await send('Page.navigate', { url: `http://127.0.0.1:${port}/tests/js/fieldvm_dom2.html` });
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
  const HTML = 'http://www.w3.org/1999/xhtml', SVG = 'http://www.w3.org/2000/svg';

  // each new tag is an element of its own tag, SVG in the SVG namespace; an unknown one is a div with a visible reason
  for (const t of ['input', 'textarea', 'select', 'option', 'form', 'label', 'button']) assert.deepEqual(r.tags[t], [t, HTML], t);
  for (const t of ['svg', 'g', 'circle', 'line', 'path', 'text', 'rect']) assert.deepEqual(r.tags[t], [t, SVG], t);
  assert.equal(r.script.tag, 'div');
  assert.match(r.script.refused.tag, /allow-list/);

  // each new attribute lands
  assert.deepEqual(r.attrs, { placeholder: 'ph', disabled: true, type: 'text', name: 'n', for: 'x', tabindex: '3', rows: '4',
    live: 'polite', data: 'dk', value: 'v' });
  assert.deepEqual(r.geo, { cx: '1', cy: '2', r: '3', x: '4', y: '5', x1: '6', y1: '7', x2: '8', y2: '9', d: 'M0 0L1 1', width: '10',
    height: '11', viewBox: '0 0 10 10', points: '0,0 1,1', fill: 'red', stroke: 'blue', 'stroke-width': '2', transform: 'rotate(5)' });

  // style, an event handler, src and a javascript: href are refused, and the refusal is visible (on the entity and on the element)
  assert.deepEqual([r.bad.style, r.bad.onclick, r.bad.src, r.bad.href], [null, null, null, null]);
  for (const k of ['style', 'onclick', 'src', 'href']) assert.ok(r.bad.refused[k], `${k} has a reason`);
  assert.match(r.bad.marked, /style/);

  // events as writes on the DomNode
  assert.equal(r.input, 'hello');
  assert.equal(r.round.out, 'saw hello', 'a textarea round trip: user text -> value -> pack -> another node');
  assert.equal(r.round.valueWrites, 1);
  assert.deepEqual(r.round.caret, [2, 2]);
  assert.deepEqual(r.same, { sets: 0, caret: [2, 2], writes: 0 }, 'a pack writing the value the user just produced touches nothing');
  assert.deepEqual(r.pack, { value: 'other', sets: 1, writes: 0 }, 'a different pack value is set once, with no echo write');
  assert.equal(r.enterPrevented, true, 'Enter without Shift in a textarea sends, not a newline');
  assert.equal(r.key.key, 'Enter'); assert.equal(r.key.shift, false); assert.equal(r.key.ctrl, false); assert.equal(typeof r.key.time, 'number');
  assert.equal(r.shiftPrevented, false); assert.equal(r.keyShift.shift, true);
  assert.equal(r.keyCtrl.ctrl, true);
  assert.equal(r.submitted, 'number'); assert.equal(r.submitPrevented, true);
  assert.equal(r.cleared, '', 'the pack cleared the textarea on submit');
  assert.equal(r.clicked, 'number');
  assert.equal(r.focused1, true); assert.equal(r.focused2, false);
  assert.deepEqual(r.focusCall, { active: true, writes: 0 }, 'a focus() from a pack does not echo (the host writes nothing back)');
  assert.equal(r.blurCall, false);
  assert.equal(r.disabledOn, true); assert.equal(r.disabledOff, false);
  assert.equal(r.scroll, true);

  // the page's own form elements work the same
  assert.equal(r.pageInput, 'page text'); assert.equal(r.pageMirror, 'saw page text');
  assert.equal(r.pagePack, 'from pack'); assert.equal(r.pageDisabled, true);
  assert.equal(r.pageKey, 'Enter'); assert.equal(r.pageSubmit, 'number');
  console.log('fieldvm dom2: ok');
} finally { br.kill(); srv.kill(); await sleep(300); rmSync(prof, { recursive: true, force: true }); }
