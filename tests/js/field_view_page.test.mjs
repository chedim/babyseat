// The field view pack END TO END (#1143): a REAL cliffvm barest seat (the shipped packs + the barest app + the page's ui folder) and the page in headless Chrome over the DevTools
// protocol. What is asserted:
//   1. the DEFAULT is the legacy graph (no fact, no `?ui`): the canvas draws, no pack mount; the seat's `/ui/fieldvm` carries the field_view pack and `views.field == legacy`;
//   2. the seat fact `ui_views={"field":"pack"}` (or `?ui=pack`) mounts the view: an SVG scene, a circle per entity of the active window, the legacy canvas hidden and not polled;
//   3. the graph GROWS: entities written while the page is open appear (the poll), next to their owner, with the layout moving;
//   4. the window FOLLOWS the field's activity: with `field_window=8` the page never holds more than 8 dots; the least recently active leave the page and the page's engine;
//   5. a click on a dot selects it; the camera spins; no script error. SHOT=<png path> saves a screenshot of the grown graph.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync, writeFileSync, chmodSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`chat view page: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`chat view page: skipped, no Chrome at ${chrome} (set CHROME)`); process.exit(0); }

const FAKE = `#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.argv[-1]
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "fake-1", "tools": []})
for _ in range(600):
    if os.path.exists(os.path.join(here, "go")): break
    time.sleep(0.1)
out({"type": "result", "subtype": "success", "is_error": False, "result": "Final answer to: " + prompt, "num_turns": 1, "duration_ms": 10, "total_cost_usd": 0, "usage": {}, "session_id": "fake-1"})
`;

const packs = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((n) => `membrane/packs/${n}.c1f`);
let nseat = 0;
function startSeat(facts = []) {
  const port = 22000 + Math.floor(Math.random() * 1000) + 7 * nseat++;
  const dir = mkdtempSync(path.join(tmpdir(), 'field-view-'));
  const claude = path.join(dir, 'fake-claude');
  writeFileSync(claude, FAKE); chmodSync(claude, 0o755);
  const args = ['run', ...packs, 'membrane/apps/barest.c1f', '--store', path.join(dir, 'field.sqlite3'), '--port', String(port), '--ui-dir', 'membrane/ui',
    '--fact', `chat_claude_bin=${claude}`, ...facts.flatMap((f) => ['--fact', f])];
  const env = { ...process.env, ANTHROPIC_API_KEY: 'sk-ant-harness-default-0000' };
  const child = spawn(bin, args, { cwd: repo, stdio: 'ignore', env });
  return { port, dir, child, go: () => writeFileSync(path.join(dir, 'go'), '1'), stop: () => { child.kill(); rmSync(dir, { recursive: true, force: true }); } };
}
async function up(seat) { for (let i = 0; i < 150; i++) { try { if ((await fetch(`http://127.0.0.1:${seat.port}/`)).ok) return; } catch {} await sleep(200); } throw new Error('the seat did not come up'); }

let dbgPort = 23000 + Math.floor(Math.random() * 500);
async function browser() {
  const prof = mkdtempSync(path.join(tmpdir(), 'field-view-prof-'));
  const dbg = dbgPort++;
  const br = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${prof}`, `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
  let tabs;
  for (let i = 0; i < 50 && !tabs; i++) { await sleep(200); try { tabs = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch {} }
  const ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r, { once: true }));
  let id = 0; const waiting = new Map(); const b = { errors: [], posts: [], paused: [], hold: false, failPosts: false, calls: [] };
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    const p = d.params || {};
    if (d.method === 'Runtime.exceptionThrown') b.errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
    else if (d.method === 'Runtime.consoleAPICalled' && p.type === 'error') b.errors.push('console.error: ' + (p.args || []).map((a) => a.value ?? a.description).join(' '));
    else if (d.method === 'Fetch.requestPaused') {
      const u = new URL(p.request.url);
      b.calls.push(`${p.request.method} ${u.pathname}`);
      if (p.request.method === 'POST' && u.pathname === '/membrane/chat/message') {
        b.posts.push(p.request.postData);
        if (b.failPosts) { send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'Failed' }); return; }
        if (b.hold) { b.paused.push(p.requestId); return; }
      }
      send('Fetch.continueRequest', { requestId: p.requestId });
    }
  });
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Runtime.enable'); await send('Page.enable');
  await send('Fetch.enable', { patterns: [{ urlPattern: '*/membrane/chat/*' }] });
  b.send = send;
  b.ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true }); return r.result?.result?.value; };
  b.until = async (expr, what, ms = 15000) => { const t0 = Date.now(); for (;;) { const v = await b.ev(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what} (last: ${JSON.stringify(v)})`); await sleep(100); } };
  b.open = async (url) => { await send('Page.navigate', { url }); };
  b.type = async (text) => { await b.ev(`document.querySelector('[data-key="DomNode:chat:input"]').focus()`); await send('Input.insertText', { text }); };
  b.enter = () => send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, text: '\r' });
  b.close = async () => { ws.close(); br.kill(); await sleep(200); rmSync(prof, { recursive: true, force: true }); };
  return b;
}
const Q = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const textOf = (sel) => `((${Q(sel)} || {}).textContent || '')`;


const seat = startSeat(['ui_views={"field":"pack"}', 'field_window=8', 'field_poll_ms=500']), plain = startSeat();
const b = await browser();
const call = async (s, tool, args) => (await (await fetch(`http://127.0.0.1:${s.port}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }) })).json());
const put = async (s, eid) => { await call(s, 'entity_write', { spec: eid, prop: 'n', value: 1 }); await sleep(40); };
const circles = `document.querySelectorAll('#fieldmount .fdots circle[data-key^="DomNode:f:c:"]').length`;
try {
  await Promise.all([up(seat), up(plain)]);
  // 1. the default is the legacy graph; the seat reports it
  const snap = await (await fetch(`http://127.0.0.1:${plain.port}/ui/fieldvm`)).json();
  assert.ok(Object.keys(snap.packs).includes('field_view'), 'the snapshot carries the field_view pack');
  assert.equal(snap.views.field, 'legacy');
  assert.equal((await (await fetch(`http://127.0.0.1:${seat.port}/ui/fieldvm`)).json()).views.field, 'pack', 'the fact ui_views says pack');
  await b.open(`http://127.0.0.1:${plain.port}/`);
  await b.until(`document.getElementById('canvas') && !document.getElementById('canvas').closest('.stage').hidden`, 'the legacy graph');
  await sleep(1200);
  assert.equal(await b.ev(`!!document.getElementById('fieldmount')`), false, 'no pack view by default');

  // 2. the fact mounts the pack view
  for (const e of ['Zed:a', 'Zed:a:x', 'Zed:b', 'Zed:b:x', 'Zed:c']) await put(seat, e);
  await b.open(`http://127.0.0.1:${seat.port}/`);
  await b.until(`${circles} >= 5`, 'a circle for each active entity');
  assert.equal(await b.ev(`document.querySelector('.pane.graph .stage').hidden`), true, 'the legacy stage is hidden');
  assert.equal(await b.ev(`document.querySelectorAll('#fieldmount .fsvg').length`), 1, 'one SVG scene');
  assert.equal(await b.ev(`document.querySelector('#fieldmount .fsvg').getAttribute('viewBox')`), '-100 -100 200 200');
  assert.match(await b.ev(`document.querySelector('#fieldmount .fcount').textContent`), /^\d+ of \d+ entities in view \(the 8 most recently active\)$/);
  assert.equal(await b.ev(`window.MEMBRANE_FIELD_VIEW.clockId !== null`), true, 'the mount runs the clock');
  const geo = await b.ev(`[...document.querySelectorAll('#fieldmount .fdots > g')].every((g) => /^translate\\(-?[0-9.e-]+ -?[0-9.e-]+\\)$/.test(g.getAttribute('transform')) && +g.querySelector('circle').getAttribute('r') > 0)`);
  assert.equal(geo, true, 'every dot group has a place and every circle a size');
  // nothing turns by itself: the picture is still (no spin), and a drag with the mouse turns it
  const cam = () => b.ev(`JSON.stringify(window.MEMBRANE_FIELD_VIEW.get('Scene:field').rx)`); const cam0 = await cam(); await sleep(700);
  assert.equal(await cam(), cam0, 'no automatic rotation: the camera basis is the same');
  assert.equal(await b.ev(`!!document.querySelector('#fieldmount [data-key="DomNode:f:spin"]')`), false, 'no spin button');
  const mouse = (type, x, y, extra = {}) => b.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1, ...extra });
  const svgBox = await b.ev(`(() => { const r = document.querySelector('#fieldmount .fsvg').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()`);
  await mouse('mousePressed', svgBox[0] + 2, svgBox[1] + 2, { buttons: 1 }); await mouse('mouseMoved', svgBox[0] + 60, svgBox[1] + 2, { buttons: 1 }); await mouse('mouseReleased', svgBox[0] + 60, svgBox[1] + 2);
  await sleep(300);
  assert.notEqual(await cam(), cam0, 'a drag turns the camera');

  // 3. it grows: an entity written now appears (with its owner's line) within a few polls
  const before = await b.ev(circles);
  await put(seat, 'Zed:c:y');
  await b.until(`!!${Q('#fieldmount [data-key="DomNode:f:c:Zed:c:y"]')}`, 'the new entity is drawn');
  assert.ok(await b.ev(circles) >= before, 'the graph did not shrink');
  await b.until(`${Q('#fieldmount [data-key="DomNode:f:l:Zed:c:y"]')}.hasAttribute('hidden') === false`, 'its line to its owner shows');

  // 3b. LIVE: the write stream is up, so the poll has stopped: more writes appear with NO new call of the active window
  const polls = `performance.getEntriesByType('resource').filter((e) => e.name.includes('/ui/fieldvm/active')).length`;
  await sleep(1200);
  const n0 = await b.ev(polls);
  await put(seat, 'Zed:live'); await put(seat, 'Zed:live:child');
  await b.until(`!!${Q('#fieldmount [data-key="DomNode:f:c:Zed:live:child"]')}`, 'the live write is drawn');
  assert.equal(await b.ev(polls), n0, 'drawn with no poll: the active window was not asked for again');
  await b.until(`${Q('#fieldmount [data-key="DomNode:f:l:Zed:live:child"]')}.hasAttribute('hidden') === false`, 'joined to its owner by the stream');
  assert.equal(await b.ev(`window.MEMBRANE_FIELD_VIEW.get('Scene:field').wired`), true);

  // 4. the window follows the activity: write many more; the page keeps 8 dots, the oldest are gone from the page and the engine
  for (let i = 0; i < 12; i++) await put(seat, `Ant:${i}`);
  await b.until(`!${Q('#fieldmount [data-key="DomNode:f:c:Zed:a"]')} && !!${Q('#fieldmount [data-key="DomNode:f:c:Ant:11"]')}`, 'the least active left, the newest came');
  await sleep(1500);
  assert.ok(await b.ev(circles) <= 8, `at most the window (${await b.ev(circles)})`);
  assert.equal(await b.ev(`window.MEMBRANE_FIELD_VIEW.get('Dot:Zed:a')`), null, 'the dot left the page\'s engine');
  assert.equal(await b.ev(`window.MEMBRANE_FIELD_VIEW.get('DomNode:f:c:Zed:a')`), null, 'and its DomNode');

  // 4b. a REAL mouse click (press and release) on a dot's own place selects it (the hit circle is the target; a press on a dot must not be captured by the scene)
  await put(seat, 'Ant:11:leg');
  const hit = await b.ev(`(() => { const r = document.querySelector('#fieldmount [data-key="DomNode:f:h:Ant:11"]').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  await mouse('mousePressed', hit[0], hit[1], { buttons: 1 }); await mouse('mouseReleased', hit[0], hit[1]);
  const top = await b.ev(`document.elementFromPoint(${hit[0]}, ${hit[1]}).getAttribute('data-key').replace(/^DomNode:f:[ch]:/, '')`);   // the dot on top at that point (a newborn child sits next to its owner)
  await sleep(50);
  await b.until(`window.MEMBRANE_FIELD_VIEW.get('Scene:field').selected === ${JSON.stringify(top)} && !document.querySelector('#fieldmount .fside').hidden`, `a real click on the dot ${top} selects it`);
  const hitKey = `DomNode:f:h:${top}`;
  const hit2 = await b.ev(`(() => { const r = document.querySelector('#fieldmount [data-key="${hitKey}"]').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);   // it may have moved with the layout
  await mouse('mousePressed', hit2[0], hit2[1], { buttons: 1 }); await mouse('mouseReleased', hit2[0], hit2[1]);
  await b.until(`document.querySelector('#fieldmount .fside').hidden`, 'a second click deselects');

  // 5. a click selects, and the panel lists the entity's edges (owner, children, refs) with their kind
  await b.ev(`${Q('#fieldmount [data-key="DomNode:f:c:Ant:11"]')}.dispatchEvent(new MouseEvent('click', {bubbles: true}))`);
  await b.until(`/^Ant:11 /.test(document.querySelector('#fieldmount .fside').textContent) && !document.querySelector('#fieldmount .fside').hidden`, 'the selection words');
  await b.until(`[...document.querySelectorAll('#fieldmount .fedges li')].some((li) => li.textContent === 'child · Ant:11:leg')`, 'the edge list of the selection (a child)');
  if (process.env.SHOT) {
    await b.send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 620, deviceScaleFactor: 1, mobile: false }); await sleep(400);
    const shot = await b.send('Page.captureScreenshot', { format: 'png' }); writeFileSync(process.env.SHOT, Buffer.from(shot.result.data, 'base64'));
  }
  assert.deepEqual(b.errors, [], 'no script error in the page');
  console.log('field view page: ok (legacy by default, pack by the fact, grows, the window follows the activity, click selects)');
} finally { await b.close(); seat.stop(); plain.stop(); }
