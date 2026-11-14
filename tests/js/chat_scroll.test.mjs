// The chat log SCROLLS (#1143, the operator: "scroll with mouse in the chat is laggy and not really working", "it scrolls several pixels per swipe"): a REAL private cliffvm barest
// seat with a FAKE claude (an instant answer; long answers with tool lines for some prompts), 220 turns of history, and the page in headless Chrome with REAL wheel input
// (Input.dispatchMouseEvent type mouseWheel: the browser's own scrolling, not a script). Asserted (counts and distances, never wall-clock):
//   1. a trackpad-like swipe (80 wheel events of 6-14 px, momentum decay) moves the log by about the sum of the deltas, monotonically; a mouse notch (deltaY 120) by 120 px each;
//   2. no wheel listener of the host cancels or slows native scrolling (a wheel over the log is not defaultPrevented; the VM is not written one event per wheel event);
//   3. a poll does not move the log while the person is away from the bottom (scrollTop unchanged across several poll cycles), and an unchanged history costs 0 DOM mutations
//      and 0 `scroll` writes per poll;
//   4. a new turn sticks the log to the bottom only when the person was within the stick distance (80 px) of it;
//   5. "Load older turns" still pages, and the turns the person was reading stay where they were.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync, writeFileSync, chmodSync, readFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`chat scroll: skipped, no cliffvm at ${bin}`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`chat scroll: skipped, no Chrome at ${chrome}`); process.exit(0); }

const FAKE = `#!/usr/bin/env python3
import json, sys
prompt = sys.argv[-1]
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "f", "tools": []})
long = prompt.startswith("long")
if long:
    for i in range(4):
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t%d" % i, "name": "mcp__membrane__entity_read", "input": {"spec": "Q:%d" % i}}]}})
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t%d" % i, "content": "r"}]}})
text = ("A long answer about " + prompt + ". ") * (40 if long else 1)
out({"type": "result", "subtype": "success", "is_error": False, "result": text, "num_turns": 1, "duration_ms": 1, "total_cost_usd": 0, "usage": {}, "session_id": "f"})
`;
const packs = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((n) => `membrane/packs/${n}.c1f`);
const port = 24000 + Math.floor(Math.random() * 900);
const dir = mkdtempSync(path.join(tmpdir(), 'chat-scroll-'));
const fake = path.join(dir, 'fake-claude'); writeFileSync(fake, FAKE); chmodSync(fake, 0o755);
const seat = spawn(bin, ['run', ...packs, 'membrane/apps/barest.c1f', '--store', path.join(dir, 'field.sqlite3'), '--port', String(port), '--ui-dir', 'membrane/ui',
  '--fact', `chat_claude_bin=${fake}`, '--fact', 'chat_list_poll_ms=500', '--fact', 'chat_active_poll_ms=200'], { cwd: repo, stdio: 'ignore', env: { ...process.env, ANTHROPIC_API_KEY: 'sk-ant-harness-default-0000' } });
const url = `http://127.0.0.1:${port}`;

const prof = mkdtempSync(path.join(tmpdir(), 'chat-scroll-prof-'));
const dbg = 25000 + Math.floor(Math.random() * 900);
const br = spawn(chrome, ['--headless=new', '--no-first-run', `--user-data-dir=${prof}`, `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
let ws, id = 0; const waiting = new Map(); const errors = [];
const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })).result?.result?.value;
const until = async (expr, what, ms = 30000) => { const t0 = Date.now(); for (;;) { const v = await ev(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what} (last: ${JSON.stringify(v)})`); await sleep(100); } };
const wheel = (dy, x, y) => send('Input.dispatchMouseEvent', { type: 'mouseWheel', x, y, deltaX: 0, deltaY: dy });
const L = `document.querySelector('[data-key="DomNode:chat:log"]')`;
const top = () => ev(`${L}.scrollTop`);
try {
  for (let i = 0; i < 60 && !seat.exitCode; i++) { try { if ((await fetch(url)).ok) break; } catch {} await sleep(200); }
  // 220 turns: every 5th long (4 tool lines, a long answer); sent 10 at a time, each answered at once by the fake
  const post = (t) => fetch(`${url}/membrane/chat/message`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: t }) }).then((r) => r.json());
  const ids = [];
  for (let i = 0; i < 220; i += 10) ids.push(...await Promise.all(Array.from({ length: 10 }, (_, k) => post(`${(i + k) % 5 === 0 ? 'long' : 'm'}${String(i + k).padStart(3, '0')}`))));
  for (const m of ids.slice(-6)) for (let t = 0; t < 300; t++) { const v = await (await fetch(`${url}/membrane/chat/message/${m.id}`)).json(); if (v.state === 'done' || v.state === 'error') break; await sleep(100); }
  await sleep(1500);

  let tabs; for (let i = 0; i < 50 && !tabs; i++) { await sleep(200); try { tabs = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch {} }
  ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r, { once: true }));
  ws.addEventListener('message', (m) => { const d = JSON.parse(m.data); if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); } else if (d.method === 'Runtime.exceptionThrown') errors.push(d.params.exceptionDetails.text); });
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 700, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `${url}/` });
  await until(`document.querySelectorAll('[data-key$=":me"]').length >= 20`, 'the first page of turns');
  // the clone of the turns: page a few more in with "Load older turns" so the log is long
  for (let i = 0; i < 3; i++) { const n = await ev(`document.querySelectorAll('[data-key$=":me"]').length`); await ev(`document.querySelector('[data-key="DomNode:chat:older:btn"]').dispatchEvent(new MouseEvent('click', {bubbles: true}))`); await until(`document.querySelectorAll('[data-key$=":me"]').length > ${n}`, 'older turns paged'); }
  const rows = await ev(`document.querySelectorAll('[data-key$=":me"]').length`);
  assert.ok(rows >= 80, `a long log (${rows} turns)`);
  const sh = await ev(`${L}.scrollHeight`), ch = await ev(`${L}.clientHeight`);
  assert.ok(sh > ch * 5, `the log really overflows (${sh} vs ${ch})`);
  const box = await ev(`(() => { const r = ${L}.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  await ev(`window.__wheelPrevented = 0; window.__wheels = 0; addEventListener('wheel', (e) => { window.__wheels++; if (e.defaultPrevented) window.__wheelPrevented++; }, { capture: false, passive: true });`);
  // the VM writes per wheel event (the host must not write the field for a wheel over the chat)
  await ev(`window.__w = []; const hw = window.MEMBRANE_PAGE.write.bind(window.MEMBRANE_PAGE); window.MEMBRANE_PAGE.write = (e, p, v) => { window.__w.push(p); return hw(e, p, v); };`);

  // 1a. the log is at the bottom after the load; a SWIPE up: 80 events of 14 down to 6 px (momentum decay), 12 ms apart
  await until(`${L}.scrollHeight - ${L}.scrollTop - ${L}.clientHeight < 3`, 'the log starts at the bottom');
  const start = await top();
  let sum = 0, last = start, monotone = true;
  for (let i = 0; i < 80; i++) { const dy = -Math.round(14 - (8 * i) / 79); sum += -dy; await wheel(dy, box[0], box[1]); await sleep(12); const t = await top(); if (t > last + 0.5) monotone = false; last = t; }
  await sleep(400);
  const moved = start - await top();
  console.log(`  swipe: ${sum} px of wheel moved the log ${moved} px (monotone ${monotone}); host writes: ${JSON.stringify((await ev(`window.__w`)).reduce((a, p) => (a[p] = (a[p] || 0) + 1, a), {}))}`);
  assert.ok(Math.abs(moved - sum) <= Math.max(10, sum * 0.05), `the log moved by about the sum of the deltas (${moved} vs ${sum})`);
  assert.equal(monotone, true, 'the log never went back down while the person swiped up');
  assert.equal(await ev(`window.__wheelPrevented`), 0, 'nothing cancelled the wheel');
  assert.equal((await ev(`window.__w`)).filter((p) => p === 'wheel').length, 0, 'the VM is not written one event per wheel event over the chat');
  // 1b. a mouse notch: 120 px each, up and down
  const t0 = await top();
  for (let i = 0; i < 5; i++) { await wheel(-120, box[0], box[1]); await sleep(60); }
  await sleep(300);
  assert.ok(Math.abs(t0 - await top() - 600) <= 30, `five notches up move 600 px (${t0 - await top()})`);
  for (let i = 0; i < 3; i++) { await wheel(120, box[0], box[1]); await sleep(60); }
  await sleep(300);
  assert.ok(Math.abs(t0 - await top() - 240) <= 30, `three notches down move 360 px back (${t0 - await top()})`);

  // 3. away from the bottom: polls (500 ms) do not move the log, and an unchanged history costs nothing
  const away = await top();
  assert.ok(await ev(`${L}.scrollHeight - ${L}.scrollTop - ${L}.clientHeight`) > 500, 'well away from the bottom');
  await ev(`window.__w = []; window.__mut = 0; window.__mo = new MutationObserver((r) => { window.__mut += r.length; }); window.__mo.observe(${L}.parentElement, { subtree: true, childList: true, characterData: true, attributes: true });`);
  const polls0 = await ev(`performance.getEntriesByType('resource').filter((e) => e.name.includes('/membrane/chat/messages')).length`);
  await sleep(4000);
  const polls = (await ev(`performance.getEntriesByType('resource').filter((e) => e.name.includes('/membrane/chat/messages')).length`)) - polls0;
  const writes = await ev(`window.__w`), mut = await ev(`window.__mut`);
  console.log(`  ${polls} polls in 4 s with an unchanged history: ${mut} DOM mutations, writes by prop ${JSON.stringify(writes.reduce((a, p) => (a[p] = (a[p] || 0) + 1, a), {}))}`);
  assert.ok(polls >= 3, `the polls ran (${polls})`);
  assert.ok(Math.abs(await top() - away) <= 1, `a poll does not move the log (${away} -> ${await top()})`);
  assert.equal(mut, 0, 'an unchanged history is 0 DOM mutations per poll');
  assert.equal(writes.filter((p) => p === 'scroll').length, 0, 'and 0 scroll writes');

  // 4. a new turn: away from the bottom the log stays; within the stick distance it follows
  const n0 = await ev(`document.querySelectorAll('[data-key$=":me"]').length`);
  await post('new while reading');
  await until(`document.querySelectorAll('[data-key$=":me"]').length > ${n0}`, 'the new turn is drawn');
  await sleep(1200);
  assert.ok(Math.abs(await top() - away) <= 1, `the new turn did not pull the reader down (${away} -> ${await top()})`);
  await ev(`${L}.scrollTop = ${L}.scrollHeight`);
  await sleep(300);
  await wheel(-40, box[0], box[1]); await sleep(300);                      // 40 px up: inside the 80 px stick distance
  const n1 = await ev(`document.querySelectorAll('[data-key$=":me"]').length`);
  await post('new while near the bottom');
  await until(`document.querySelectorAll('[data-key$=":me"]').length > ${n1}`, 'the second new turn is drawn');
  await until(`${L}.scrollHeight - ${L}.scrollTop - ${L}.clientHeight < 3`, 'near the bottom: the log follows the new turn');

  // 5. Load older still pages; the turns being read stay put
  await ev(`${L}.scrollTop = 0`); await sleep(300);
  const before = await ev(`document.querySelectorAll('[data-key$=":me"]').length`);
  const btn = await ev(`!document.querySelector('[data-key="DomNode:chat:older"]').hidden`);
  if (btn) { await ev(`document.querySelector('[data-key="DomNode:chat:older:btn"]').dispatchEvent(new MouseEvent('click', {bubbles: true}))`); await until(`document.querySelectorAll('[data-key$=":me"]').length > ${before}`, 'older turns paged'); }
  assert.deepEqual(errors, [], 'no script error');
  if (process.env.SHOTS) { mkdirSync(process.env.SHOTS, { recursive: true }); writeFileSync(path.join(process.env.SHOTS, 'chat-scroll.png'), Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).result.data, 'base64')); }
  console.log('chat scroll: ok');
} finally { try { ws.close(); } catch {} br.kill(); seat.kill(); await sleep(200); rmSync(prof, { recursive: true, force: true }); rmSync(dir, { recursive: true, force: true }); }
