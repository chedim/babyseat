// The WHOLE barest page as c1f, END TO END (#1143): a REAL private cliffvm barest seat (the shipped packs + the barest app + the page's ui folder, own store, own port) with a FAKE claude
// (chat_claude_bin: a script that waits for a `go.<prompt>` file, then answers "Final answer to: <prompt>"; a prompt with `boom` exits 1 with a message; no model, no login, no network),
// a second seat WITHOUT a backend, and the page in headless Chrome over the DevTools protocol. What is asserted:
//   1. the page is the bootstrap + the shell: the nav (Chat, Field, Settings), the status line, the chat by default; Settings is the placeholder; screenshots of each view;
//   2. a message goes (ONE POST for three Enters), shows, and the answer REPLACES "Waiting for a turn…": the view returns to idle (status line too);
//   3. a reload DURING a running turn: the page comes back with the pending turn and shows the answer when it finishes;
//   4. a turn that errors shows its error as text; a seat WITHOUT a backend answers with its own error and the page shows that text;
//   5. a read that hangs or fails (the stream dropped, the seat away) never wedges the view: after it the finished turn is shown (the cause of the "Waiting for a turn…" that never ended: a poll
//      with no answer held `polling` for ever; every call now carries a timeout);
//   6. ONE chat: no sessions column or New session, no /session list call, the chat has the full width;
//   7. the field view opens lazily, a click on a node slides the detail panel over the chat (id, class, properties, edges), a click outside / Esc / Close closes it, a draft typed in the chat survives;
//   8. no script error. SHOTS=<dir> saves a screenshot of each view.
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
if (!existsSync(bin)) { console.log(`shell page: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`shell page: skipped, no Chrome at ${chrome} (set CHROME)`); process.exit(0); }

const FAKE = `#!/usr/bin/env python3
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
prompt = sys.argv[-1]
tag = "".join(c if c.isalnum() else "_" for c in prompt)[:40]
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "fake-1", "tools": []})
if "boom" in prompt:
    sys.stderr.write("fake cli failure: boom"); sys.exit(1)
for _ in range(900):
    if os.path.exists(os.path.join(here, "go." + tag)): break
    time.sleep(0.1)
out({"type": "result", "subtype": "success", "is_error": False, "result": "Final answer to: " + prompt, "num_turns": 1, "duration_ms": 10, "total_cost_usd": 0, "usage": {}, "session_id": "fake-1"})
`;
const tagOf = (p) => [...p].map((c) => (/[A-Za-z0-9]/.test(c) ? c : '_')).join('').slice(0, 40);

const packs = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((n) => `membrane/packs/${n}.c1f`);
let nseat = 0;
// `apps`: more app folders after barest.c1f (the field view is the app folder membrane/apps/field.c1f: DISABLED in the default launch, operator 2026-11-14; a test of the field view loads it here)
function startSeat({ claude = true, facts = [], apps = [] } = {}) {
  const port = 22000 + Math.floor(Math.random() * 1000) + 7 * nseat++;
  const dir = mkdtempSync(path.join(tmpdir(), 'shell-page-'));
  const fake = path.join(dir, 'fake-claude');
  writeFileSync(fake, FAKE); chmodSync(fake, 0o755);
  const args = ['run', ...packs, 'membrane/apps/barest.c1f', ...apps, '--store', path.join(dir, 'field.sqlite3'), '--port', String(port), '--ui-dir', 'membrane/ui',
    ...(claude ? ['--fact', `chat_claude_bin=${fake}`] : []), ...facts.flatMap((f) => ['--fact', f])];
  const env = { ...process.env, ANTHROPIC_API_KEY: 'sk-ant-harness-default-0000' };
  const child = spawn(bin, args, { cwd: repo, stdio: 'ignore', env });
  return { port, dir, child, url: `http://127.0.0.1:${port}`, go: (prompt) => writeFileSync(path.join(dir, 'go.' + tagOf(prompt)), '1'), stop: () => { child.kill(); rmSync(dir, { recursive: true, force: true }); } };
}
async function up(seat) { for (let i = 0; i < 150; i++) { try { if ((await fetch(`${seat.url}/`)).ok) return; } catch {} await sleep(200); } throw new Error('the seat did not come up'); }

let dbgPort = 23000 + Math.floor(Math.random() * 500);
async function browser() {
  const prof = mkdtempSync(path.join(tmpdir(), 'shell-page-prof-'));
  const dbg = dbgPort++;
  const br = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${prof}`, `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
  let tabs;
  for (let i = 0; i < 50 && !tabs; i++) { await sleep(200); try { tabs = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch {} }
  const ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r, { once: true }));
  let id = 0; const waiting = new Map(); const b = { errors: [], posts: [], paused: [], calls: [], hook: null };
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    const p = d.params || {};
    if (d.method === 'Runtime.exceptionThrown') b.errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
    else if (d.method === 'Runtime.consoleAPICalled' && p.type === 'error') b.errors.push('console.error: ' + (p.args || []).map((a) => a.value ?? a.description).join(' '));
    else if (d.method === 'Fetch.requestPaused') {
      const u = new URL(p.request.url), key = `${p.request.method} ${u.pathname}`;
      b.calls.push(key);
      if (p.request.method === 'POST' && u.pathname === '/membrane/chat/message') b.posts.push(p.request.postData);
      const act = b.hook && b.hook(key, p.request);                 // 'fail' | 'hold' | undefined
      if (act === 'fail') { send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'Failed' }); return; }
      if (act === 'hold') { b.paused.push(p.requestId); return; }
      send('Fetch.continueRequest', { requestId: p.requestId });
    }
  });
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Runtime.enable'); await send('Page.enable');
  await send('Fetch.enable', { patterns: [{ urlPattern: '*/membrane/chat/*' }, { urlPattern: '*/session*' }] });
  b.send = send;
  b.ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true }); return r.result?.result?.value; };
  b.until = async (expr, what, ms = 20000) => { const t0 = Date.now(); for (;;) { const v = await b.ev(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what} (last: ${JSON.stringify(v)})`); await sleep(100); } };
  b.open = async (url) => { await send('Page.navigate', { url }); };
  b.type = async (text) => { await b.ev(`document.querySelector('[data-key="DomNode:chat:input"]').focus()`); await send('Input.insertText', { text }); };
  b.key = (key, code, vk, text) => send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: vk, ...(text ? { text } : {}) });
  b.enter = () => b.key('Enter', 'Enter', 13, '\r');
  b.release = async () => { for (const rid of b.paused.splice(0)) await send('Fetch.continueRequest', { requestId: rid }); };
  b.size = (w, h, mobile = false) => send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile });
  b.shot = async (name) => { if (!process.env.SHOTS) return; mkdirSync(process.env.SHOTS, { recursive: true }); await sleep(450); writeFileSync(path.join(process.env.SHOTS, name + '.png'), Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).result.data, 'base64')); };
  b.close = async () => { ws.close(); br.kill(); await sleep(200); rmSync(prof, { recursive: true, force: true }); };
  return b;
}
const K = (k) => `[data-key="${k}"]`;
const Q = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const textOf = (sel) => `((${Q(sel)} || {}).textContent || '')`;
const click = (sel) => `${Q(sel)}.dispatchEvent(new MouseEvent('click', {bubbles: true}))`;
const answers = `[...document.querySelectorAll('[data-key$=":ans"]')].map((e) => e.textContent)`;

const seat = startSeat({ facts: ['chat_poll_timeout_s=2', 'chat_chat_unused=1', 'field_window=40'], apps: ['membrane/apps/field.c1f'] }), bare = startSeat({ claude: false });
const b = await browser();
const call = async (s, tool, args) => (await (await fetch(`${s.url}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }) })).json());
const status = textOf(K('DomNode:shell:status'));
try {
  await Promise.all([up(seat), up(bare)]);
  await b.size(1100, 680);

  // 1. the bootstrap and the shell
  const html = await (await fetch(`${seat.url}/`)).text();
  assert.ok(html.includes('id="app"') && html.includes('boot.js') && !html.includes('<canvas') && html.length < 700, 'index.html is a bootstrap');
  await b.open(`${seat.url}/`);
  await b.until(Q(K('DomNode:chat:input')), 'the chat composer (the c1f chat is the default)');
  assert.equal(await b.ev(`!!document.getElementById('boot')`), false, 'the boot text is gone');
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('.nav button')].map((e) => e.textContent)`), ['Chat', 'Field', 'Settings']);
  assert.equal(await b.ev(`document.querySelector('.nav [aria-current="page"]').textContent`), 'Chat');
  await b.until(`/^connected · idle$/.test(${status})`, 'the status line says connected and idle');
  assert.equal(await b.ev(`window.MEMBRANE_PAGE.clockId !== null`), true, 'the page runs the clock');
  await b.shot('chat-empty');

  // 2. a message: ONE POST for three Enters; the answer replaces "Waiting for a turn…"; idle again
  await b.type('hello e2e');
  await b.enter(); await b.enter(); await b.enter();
  await b.until(`${textOf('[data-key$=":me"]')} === 'hello e2e'`, 'the user bubble');
  await sleep(300);
  assert.equal(b.posts.length, 1, 'ONE POST');
  assert.deepEqual(JSON.parse(b.posts[0]), { text: 'hello e2e' }, 'a send names no session');
  await b.until(`/^(Waiting for a turn…|Working on it…)$/.test(${textOf('[data-key$=":ans"]')})`, 'pending');
  await b.until(`/a turn is running/.test(${status})`, 'the status line says a turn is running');
  seat.go('hello e2e');
  await b.until(`${textOf('[data-key$=":ans"]')} === 'Final answer to: hello e2e'`, 'the answer replaces the waiting line');
  await b.until(`/^connected · idle$/.test(${status})`, 'idle again');
  assert.match(await b.ev(textOf('[data-key$=":who"]')), /^seat( · \d+ s)?$/);
  await b.shot('chat-answered');

  // 3. a reload DURING a running turn
  await b.type('slow one'); await b.enter();
  await b.until(`${answers}.length === 2`, 'the second turn is shown');
  await b.open(`${seat.url}/`);
  await b.until(`${answers}.length === 2 && /^(Waiting for a turn…|Working on it…)$/.test(${answers}[1])`, 'after the reload the running turn is shown as pending');
  seat.go('slow one');
  await b.until(`${answers}[1] === 'Final answer to: slow one'`, 'the answer after the reload');
  await b.until(`/^connected · idle$/.test(${status})`, 'idle after the reload');

  // 4. a turn that errors: the error as text, and the view is idle
  await b.type('boom now'); await b.enter();
  await b.until(`${answers}.length === 3 && !/^(Waiting for a turn…|Working on it…)$/.test(${answers}[2]) && ${answers}[2].length > 0`, 'the errored turn leaves the waiting state');
  assert.match(await b.ev(`${answers}[2]`), /boom/, 'the error text names the failure');
  assert.equal(await b.ev(`${Q('[data-key$=":ans"]:last-of-type')} && [...document.querySelectorAll('[data-key$=":ans"]')].pop().className`), 'bubble note');
  await b.until(`/^connected · idle$/.test(${status})`, 'idle after an error');

  // 5. the stream dropping / a read that never answers cannot wedge the view
  //    (a) reads fail while the turn finishes; when they come back the answer is shown
  b.hook = (key) => (key.startsWith('GET /membrane/chat/messages') ? 'fail' : undefined);
  await b.type('while the seat was away'); await b.enter();
  await b.until(`${answers}.length === 4`, 'the fourth turn is shown');
  seat.go('while the seat was away');
  await b.until(`/did not answer the last read of the turns/.test(${textOf('.pollnote')})`, 'a failed read is said in text');
  b.hook = null;
  await b.until(`${answers}[3] === 'Final answer to: while the seat was away'`, 'the finished turn is shown once the reads come back');
  await b.until(`${Q('.pollnote')}.hidden`, 'the failure line goes');
  //    (b) a read that HANGS (never answered): the host's timeout aborts it, the poll is armed again, the answer is shown
  b.hook = (key) => (key.startsWith('GET /membrane/chat/messages') ? 'hold' : undefined);
  await b.type('hung read'); await b.enter();
  await b.until(`${answers}.length === 5`, 'the fifth turn is shown');
  seat.go('hung read');
  await sleep(500);
  b.hook = null; await b.release();                                  // the held reads go out as they are; the next poll is a fresh one
  await b.until(`${answers}[4] === 'Final answer to: hung read'`, 'the turn the seat finished is shown after a hung read', 25000);
  await b.until(`/^connected · idle$/.test(${status})`, 'idle');
  b.hook = (key) => (key.startsWith('GET /membrane/chat/messages') ? 'hold' : undefined);
  await sleep(2500);                                                 // polls are held now: each is aborted by its 2 s timeout, and the line says so
  await b.until(`/timeout after 2 s/.test(${textOf('.pollnote')})`, 'a hung read is aborted by its timeout and said in text', 15000);
  b.hook = null; await b.release();

  // 6. ONE chat: no sessions column, no New session; the history is every turn of the seat, the thread keeps its 5 turns
  assert.equal(await b.ev(`!!document.querySelector('.sessions') || !!${Q(K('DomNode:shell:newsession'))}`), false, 'no sessions UI');
  assert.equal(await b.ev(`document.querySelectorAll('[data-key$=":me"]').length`), 5);
  assert.ok(!b.calls.some((c) => c.startsWith('GET /session') && !c.startsWith('GET /session/status')), `the page lists no sessions (${[...new Set(b.calls)].join(', ')})`);
  assert.equal(await b.ev(`${Q('.chatview')}.parentElement.getBoundingClientRect().width > 900`), true, 'the chat has the full width');

  // 7. the field: lazy, the detail panel over the chat, closed by click-out / Esc / Close; the chat is covered, not unmounted
  for (const e of ['Zed:a', 'Zed:a:x', 'Zed:b']) await call(seat, 'entity_write', { spec: e, prop: 'note', value: 'x'.repeat(450) });
  await b.until(`document.querySelectorAll('[data-key$=":me"]').length === 5`, 'back in the main chat');
  await b.type('a draft that must survive');
  assert.equal(await b.ev(`!!${Q(K('DomNode:f:root'))}`), false, 'the field view is not drawn until it is shown');
  await b.ev(click(K('DomNode:shell:nav:field')));
  await b.until(`document.querySelectorAll('.fdots circle').length >= 3`, 'the field draws');
  assert.equal(await b.ev(`${Q(K('DomNode:shell:slot:chat'))}.hidden`), true);
  await b.shot('field');
  assert.equal(await b.ev(`${Q('.panel')}.getAttribute('data-open')`), 'false');
  //   a REAL mouse press and release on a dot selects it (the press must not be captured by the scene), and a drag that moved does not select the dot it ends on
  const mouse = (type, x, y, extra = {}) => b.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1, ...extra });
  const hitOf = async (id) => b.ev(`(() => { const r = ${Q(K('DomNode:f:h:' + id))}.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  const topAt = (p) => b.ev(`document.elementFromPoint(${p[0]}, ${p[1]}).getAttribute('data-key').replace(/^DomNode:f:[ch]:/, '')`);
  const sel = () => b.ev(`window.MEMBRANE_PAGE.get('Scene:field').selected`);
  let hp = await hitOf('Zed:a'); const top = await topAt(hp);
  await mouse('mousePressed', hp[0], hp[1], { buttons: 1 }); await mouse('mouseMoved', hp[0] + 40, hp[1] + 5, { buttons: 1 }); await mouse('mouseReleased', hp[0] + 40, hp[1] + 5);
  await sleep(300);
  assert.ok((await sel()) == null && (await b.ev(`${Q('.panel')}.getAttribute('data-open')`)) === 'false', 'a drag that moved selects nothing');
  //   the field is live (a write of the seat moves or adds a dot between the measure and the press): the press is made on the dot found at that very moment, and the check is that the SELECTED dot is the one that was under the mouse at the press; a press that lands while a dot moved is repeated (a bounded number of times)
  let clicked = false;
  for (let tries = 0; tries < 6 && !clicked; tries++) {
    hp = await hitOf(top);
    const under = await topAt(hp);
    await mouse('mousePressed', hp[0], hp[1], { buttons: 1 }); await mouse('mouseReleased', hp[0], hp[1]);
    await b.until(`${Q('.panel')}.getAttribute('data-open') === 'true'`, 'a real click on a dot opens the panel');
    const got = await sel();
    if (got === under || got === top) clicked = true;
    else { await b.key('Escape', 'Escape', 27); await b.until(`${Q('.panel')}.getAttribute('data-open') === 'false'`, 'a missed press is closed again'); }
  }
  assert.ok(clicked, 'the dot under the mouse is the selected one');
  await b.key('Escape', 'Escape', 27);
  await b.until(`${Q('.panel')}.getAttribute('data-open') === 'false'`, 'Esc closes it');
  await b.ev(click(K('DomNode:f:c:Zed:a')));
  await b.until(`${Q('.panel')}.getAttribute('data-open') === 'true'`, 'the panel opens on a click on a node');
  assert.equal(await b.ev(`${Q('.panel h2')}.textContent`), 'Zed:a');
  await b.until(`/^Zed · Zed:a/.test(${textOf('.panelsub')})`, 'the class in the sub line');
  await b.until(`[...document.querySelectorAll('.panelrows li.link')].some((e) => e.textContent === 'child · Zed:a:x')`, 'the edges: a child link');
  await b.until(`!!${Q('.panelrows li.prop .v')}`, 'the properties');
  assert.ok((await b.ev(textOf('.panelrows li.prop .v'))).length < 450 && /…$/.test(await b.ev(textOf('.panelrows li.prop .v'))), 'a long value is cut');
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('.panelrows li')].map((l) => l.className)`), ['head', 'prop', 'prop', 'head', 'link'], 'properties first, then the edges, whatever answered first');
  await b.ev(click('.panelrows li.prop .more'));
  await b.until(`${textOf('.panelrows li.prop .v')} === 'x'.repeat(450)`, 'Show all expands the whole value');
  assert.equal(await b.ev(`document.activeElement === ${Q('.panel')} || ${Q('.panel')}.contains(document.activeElement)`), true, 'the panel takes the focus');
  await b.shot('field-panel');
  await b.ev(click('.panelrows li.link'));                           // the first link row: re-selects that entity
  await b.until(`/^Zed:/.test(${textOf('.panel h2')}) && ${textOf('.panel h2')} !== 'Zed:a'`, 'a click on an edge row re-selects');
  //   close by a click OUTSIDE (the scrim), by Esc, by Close
  await b.ev(click('.scrim'));
  await b.until(`${Q('.panel')}.getAttribute('data-open') === 'false'`, 'the scrim closes the panel');
  await b.ev(click(K('DomNode:f:c:Zed:b')));
  await b.until(`${Q('.panel')}.getAttribute('data-open') === 'true'`, 'opens again');
  await b.key('Escape', 'Escape', 27);
  await b.until(`${Q('.panel')}.getAttribute('data-open') === 'false'`, 'Esc closes the panel');
  await b.ev(click(K('DomNode:f:c:Zed:b')));
  await b.until(`${Q('.panel')}.getAttribute('data-open') === 'true'`, 'opens a third time');
  await b.ev(click(K('DomNode:shell:panelclose')));
  await b.until(`${Q('.panel')}.getAttribute('data-open') === 'false'`, 'Close closes the panel');
  assert.ok((await b.ev(`window.MEMBRANE_PAGE.get('Scene:field').selected`)) == null, 'closing clears the selection');
  //   the panel over the CHAT: the chat is covered, not unmounted; the draft survives
  await b.ev(click(K('DomNode:shell:nav:chat')));
  await b.until(`${Q(K('DomNode:shell:slot:chat'))}.hidden === false`, 'back in the chat');
  assert.equal(await b.ev(`${Q(K('DomNode:chat:input'))}.value`), 'a draft that must survive', 'the draft survived the panel and the view switches');
  await b.ev(`window.MEMBRANE_PAGE.write('Panel:detail', 'open', true)`);
  await b.until(`document.getAnimations().length === 0 && ['none', 'matrix(1, 0, 0, 1, 0, 0)'].includes(getComputedStyle(${Q('.panel')}).transform)`, 'the slide-over transition has ended');
  assert.equal(await b.ev(`document.elementFromPoint(300, 300) === ${Q('.scrim')} || ${Q('.panel')}.contains(document.elementFromPoint(300, 300))`), true, 'the panel and its scrim are over the chat');
  assert.equal(await b.ev(`!!${Q(K('DomNode:chat:input'))}`), true, 'the chat is still mounted');
  await b.ev(click('.scrim'));
  assert.equal(await b.ev(`${Q(K('DomNode:chat:input'))}.value`), 'a draft that must survive');

  // Settings: the placeholder
  await b.ev(click(K('DomNode:shell:nav:settings')));
  await b.until(`${textOf(K('DomNode:shell:slot:settings:empty'))} === 'Settings' && !${Q(K('DomNode:shell:slot:settings'))}.hidden`, 'the Settings placeholder');
  await b.shot('settings');
  await b.ev(click(K('DomNode:shell:theme')));
  await b.until(`document.documentElement.getAttribute('data-theme') === 'light'`, 'the theme button sets data-theme on <html>');
  await b.shot('settings-light');
  await b.size(390, 760, true);
  await b.ev(click(K('DomNode:shell:nav:chat')));
  await b.shot('chat-mobile');
  assert.equal(await b.ev(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'no horizontal scroll at phone width');

  // 4b. a seat WITHOUT a backend: its own error is shown as text
  await b.size(1100, 680);
  await b.open(`${bare.url}/`);
  await b.until(Q(K('DomNode:chat:input')), 'the bare seat chat');
  //   the default launch has NO field view (operator 2026-11-14): the nav is Chat and Settings, no Field button, no slot of it, no request of its feed, no script error
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('.nav button')].map((e) => e.textContent)`), ['Chat', 'Settings'], 'no Field tab');
  assert.equal(await b.ev(`!!${Q(K('DomNode:shell:nav:field'))} || !!${Q(K('DomNode:shell:slot:field'))} || !!${Q(K('DomNode:f:root'))}`), false, 'no Field node, no empty slot');
  assert.equal(await b.ev(`performance.getEntriesByType('resource').filter((r) => /fieldvm\\/(active|events|edges|entity)/.test(r.name)).length`), 0, 'no request of the field feed');
  await b.type('anyone there'); await b.enter();
  await b.until(`${answers}.length === 1 && ${answers}[0].length > 10 && !/^(Waiting for a turn…|Working on it…)$/.test(${answers}[0])`, 'the error of a seat with no backend is shown');
  console.log('  the backend-less seat said:', JSON.stringify(await b.ev(`${answers}[0]`)).slice(0, 160));
  await b.until(`/^connected · idle$/.test(${status})`, 'idle');
  await b.shot('chat-error');
  assert.deepEqual(b.errors, [], 'no script error in the page');
  console.log('shell page: ok');
} finally { await b.close(); seat.stop(); bare.stop(); }
