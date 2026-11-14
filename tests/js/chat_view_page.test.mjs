// The chat as a view pack, END TO END (#1143): a REAL cliffvm barest seat (the shipped packs of membrane/apps/barest.packs + the barest app + the page's ui folder) with a
// FAKE claude binary (chat_claude_bin: a script that waits for a `go` file, then answers "Final answer to: <prompt>"; no model, no login, no network), and the page in headless
// Chrome over the DevTools protocol. What is asserted:
//   1. the DEFAULT is the legacy chat (no fact, no `?ui`): chat.js draws, no pack mount; the seat's `/ui/fieldvm` carries the chat_view pack, `views.chat == legacy`
//      and the Boot facts the view reads;
//   2. `?ui=pack`: the pack view mounts (the legacy box is hidden), the person types and presses Enter, ONE POST /membrane/chat/message goes (a second Enter while it is out
//      does nothing), the user bubble appears, then the answer when the poll sees it (no streaming); the polls are GETs of /membrane/chat/messages;
//   3. a failed POST shows the note and keeps the draft; the next send goes through;
//   4. the seat fact `ui_views={"chat":"pack"}` mounts the pack view with no `?ui`; `?ui=legacy` still forces the legacy chat.
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
  const dir = mkdtempSync(path.join(tmpdir(), 'chat-view-'));
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
  const prof = mkdtempSync(path.join(tmpdir(), 'chat-view-prof-'));
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

const seat1 = startSeat(), seat2 = startSeat(['ui_views={"chat":"pack"}']);
const b = await browser();
try {
  await Promise.all([up(seat1), up(seat2)]);
  const base = `http://127.0.0.1:${seat1.port}`;

  // 1. the default is the legacy chat; the seat reports it
  const snap = await (await fetch(`${base}/ui/fieldvm`)).json();
  assert.ok(Object.keys(snap.packs).includes('chat_view'), 'the snapshot carries the chat_view pack');
  assert.equal(snap.views.chat, 'legacy', 'no fact: legacy');
  const boot = snap.entities.find((e) => e.eid === 'Boot:seat');
  assert.ok(boot && boot.cls === 'Boot' && 'chat_page_size' in boot.props, 'the Boot facts the view reads ride in the snapshot');
  assert.equal((await (await fetch(`http://127.0.0.1:${seat2.port}/ui/fieldvm`)).json()).views.chat, 'pack', 'the fact ui_views says pack');
  await b.open(`${base}/`);
  await b.until(`document.querySelectorAll('#log').length === 1 && !document.getElementById('log').hidden`, 'the legacy chat');
  await sleep(1500);
  assert.equal(await b.ev(`!!document.getElementById('chatmount')`), false, 'no pack view by default');
  assert.equal(await b.ev(`!!document.querySelector('[data-key]')`), false, 'no DomNode was drawn');

  // 2. ?ui=pack: the pack view; type, Enter once, a second Enter does nothing; the user bubble, then the answer
  await b.open(`${base}/?ui=pack`);
  await b.until(Q('[data-key="DomNode:chat:input"]'), 'the pack composer');
  assert.equal(await b.ev(`document.getElementById('log').hidden`), true, 'the legacy log is hidden');
  assert.equal(await b.ev(`document.getElementById('composer').hidden`), true, 'the legacy box is hidden');
  assert.equal(await b.ev(`document.querySelector('#chatmount [data-key="DomNode:chat:send"]').textContent`), 'Send');
  assert.equal(await b.ev(`window.MEMBRANE_CHAT_VIEW.clockId !== null`), true, 'the mount runs the clock (run_clock)');
  assert.equal(await b.ev(`window.MEMBRANE_CHAT_VIEW.get('Conn:page').hidden`), false, 'the page says it is visible');
  b.hold = true;
  await b.type('hello e2e');
  await b.until(`${Q('[data-key="DomNode:chat:input"]')}.value === 'hello e2e'`, 'the typed text');
  await b.enter();
  await b.until(`${Q('[data-key="DomNode:chat:input"]')}.disabled`, 'the box disabled while the POST is out');
  await b.ev(`document.querySelector('[data-key="DomNode:chat:input"]').dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}))`);
  await b.enter();
  await sleep(500);
  assert.equal(b.posts.length, 1, 'ONE POST while it is out (a second Enter does nothing)');
  assert.deepEqual(JSON.parse(b.posts[0]), { text: 'hello e2e' });
  assert.equal(await b.ev(`${Q('[data-key="DomNode:chat:send"]')}.disabled`), true);
  b.hold = false;
  for (const rid of b.paused.splice(0)) await b.send('Fetch.continueRequest', { requestId: rid });
  const mine = `[data-key$=":me"]`;
  await b.until(`${textOf(mine)} === 'hello e2e'`, 'the user bubble');
  assert.equal(await b.ev(`${Q('[data-key="DomNode:chat:input"]')}.value`), '', 'the box is cleared after the 202');
  assert.equal(await b.ev(`${Q('[data-key="DomNode:chat:input"]')}.disabled`), false);
  const who = await b.until(textOf('[data-key$=":who"]'), 'the who line');
  assert.match(who, /^(queued|working)$/);
  assert.match(await b.ev(textOf('[data-key$=":ans"]')), /^(Waiting for a turn…|Working on it…)$/, 'pending until the answer');
  seat1.go();
  await b.until(`${textOf('[data-key$=":ans"]')} === 'Final answer to: hello e2e'`, 'the answer (the poll saw it)', 20000);
  assert.match(await b.ev(textOf('[data-key$=":who"]')), /^seat( · \d+ s)?$/);
  assert.equal(await b.ev(`${Q('[data-key$=":ans"]')}.className`), 'bubble seat');
  assert.ok(b.calls.filter((c) => c === 'GET /membrane/chat/messages').length >= 2, `the polls are GETs of the history (${b.calls.join(', ')})`);
  assert.equal(b.calls.filter((c) => c === 'POST /membrane/chat/message').length, 1);
  if (process.env.SHOT) { await b.send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 620, deviceScaleFactor: 1, mobile: false }); await sleep(300); writeFileSync(process.env.SHOT, Buffer.from((await b.send('Page.captureScreenshot', { format: 'png' })).result.data, 'base64')); }

  // 3. a failed POST: the note, the draft kept; the next send goes through
  b.failPosts = true;
  await b.type('second message');
  await b.enter();
  await b.until(`!${Q('[data-key="DomNode:chat:note"]')}.hidden`, 'the note');
  assert.match(await b.ev(textOf('[data-key="DomNode:chat:note:bubble"]')), /^The message did not reach the seat: .*It is still in the box; send it again when the seat is back\.$/);
  assert.equal(await b.ev(`${Q('[data-key="DomNode:chat:input"]')}.value`), 'second message', 'the draft stays in the box');
  assert.equal(await b.ev(`${Q('[data-key="DomNode:chat:input"]')}.disabled`), false);
  b.failPosts = false;
  await b.enter();
  await b.until(`document.querySelectorAll('[data-key$=":me"]').length === 2`, 'the second user bubble');
  await b.until(`${Q('[data-key="DomNode:chat:note"]')}.hidden`, 'the note gone after the next attempt');
  assert.deepEqual(b.errors, [], 'no script error in the page');

  // 4. the seat fact mounts the pack view with no ?ui; ?ui=legacy forces the legacy chat
  await b.open(`http://127.0.0.1:${seat2.port}/`);
  await b.until(Q('#chatmount [data-key="DomNode:chat:input"]'), 'the pack view by the fact');
  assert.equal(await b.ev(`document.getElementById('log').hidden`), true);
  await b.open(`http://127.0.0.1:${seat2.port}/?ui=legacy`);
  await b.until(`document.getElementById('log') && !document.getElementById('log').hidden`, 'the legacy chat by ?ui=legacy');
  await sleep(800);
  assert.equal(await b.ev(`!!document.getElementById('chatmount')`), false);
  console.log(`chat view page: ok (legacy by default, pack by ?ui=pack and by the fact, 1 POST for 3 Enters, answer seen, failure note)`);
} finally { await b.close(); seat1.stop(); seat2.stop(); }
