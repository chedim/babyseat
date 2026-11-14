// RELEASE VERIFICATION, the browser side (#1143, docs/release-verification.md items 2 and 3): the page of a STRICT seat (the release launcher scripts/seat: the door secret, the owner cookie), in
// headless Chrome over the DevTools protocol, against a FAKE claude (no model, no login, no network), a free port, its own data folder. Asserted:
//   1. the page fetched from loopback is given its owner cookie (HttpOnly, SameSite=Strict, Path=/, this seat's name, a token of the owner signed with the seat's secret, no script can read it) and THEN works:
//      the chat (a message, its answer, the history after a reload) and the settings (read, write one, the seat holds it); no secret and no token is on the page;
//   2. the same page through the machine's LAN address (a seat started with --bind 0.0.0.0: scripts/seat has no --bind) gets NO cookie; the protected routes are 401 with the plain text reason;
//      the page says in text that it could not start (never blank); it offers NO sign-in screen (REPORTED as a gap, not asserted away); the door's way in, POST /ui/login, works from the page and then
//      the page runs;
//   3. a second person (bob, a minted non-owner token) who logs in the same way: the chat works and is THE chat (his message shows in the owner's history), settings read, a settings write is refused
//      and the refusal is shown in text; the seat did not change.
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHmac } from 'node:crypto';
import { networkInterfaces } from 'node:os';
import { mkdtempSync, rmSync, existsSync, writeFileSync, chmodSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`release verify: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`release verify: skipped, no Chrome at ${chrome} (set CHROME)`); process.exit(0); }

const FAKE = `#!/usr/bin/env python3
import json, sys
prompt = sys.argv[-1]
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "f", "tools": []})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Final answer to: " + prompt, "num_turns": 1, "duration_ms": 1, "total_cost_usd": 0, "usage": {}, "session_id": "f"})
`;
const lan = Object.values(networkInterfaces()).flat().find((i) => i && i.family === 'IPv4' && !i.internal)?.address;
const dirs = []; const children = [];
let nseat = 0;
function folder() { const d = mkdtempSync(path.join(tmpdir(), 'release-verify-')); dirs.push(d); const f = path.join(d, 'fake-claude'); writeFileSync(f, FAKE); chmodSync(f, 0o755); return [d, f]; }
// the release launcher: strict door, loopback only
function launcher() {
  const [dir, fake] = folder(); const port = 25000 + Math.floor(Math.random() * 900) + 11 * nseat++;
  const child = spawn('sh', [path.join(repo, 'scripts/seat'), '--data-dir', dir, '--port', String(port)], { cwd: repo, stdio: 'ignore', env: { ...process.env, CLIFFVM: bin, CLAUDE_BIN: fake, ANTHROPIC_API_KEY: 'sk-ant-harness-default-0000' } });
  children.push(child);
  return { dir, port, child };
}
// the same seat on every interface (what `cliffvm run --bind 0.0.0.0` does; scripts/seat passes no --bind)
function lanSeat() {
  const [dir, fake] = folder(); const port = 25000 + Math.floor(Math.random() * 900) + 11 * nseat++;
  const packs = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((n) => `membrane/packs/${n}.c1f`);
  const child = spawn(bin, ['run', ...packs, 'membrane/apps/barest.c1f', '--store', path.join(dir, 'seat.sqlite'), '--port', String(port), '--ui-dir', 'membrane/ui', '--bind', '0.0.0.0',
    '--fact', `door_secret_file=${path.join(dir, 'door.secret')}`, '--fact', `seat_data_dir=${dir}`, '--fact', `chat_claude_bin=${fake}`], { cwd: repo, stdio: 'ignore', env: { ...process.env, ANTHROPIC_API_KEY: 'sk-ant-harness-default-0000' } });
  children.push(child);
  return { dir, port, child };
}
const owner = (s) => readFileSync(path.join(s.dir, 'owner.token'), 'utf8').trim();
const secret = (s) => readFileSync(path.join(s.dir, 'door.secret'), 'utf8').trim();
const mint = (s, sub, name) => spawnSync(bin, ['token', 'mint', '--sub', sub, '--name', name, '--ttl', '1h', '--secret-file', path.join(s.dir, 'door.secret')], { encoding: 'utf8' }).stdout.trim();
async function up(s, host = '127.0.0.1') { for (let i = 0; i < 300; i++) { try { if ((await fetch(`http://${host}:${s.port}/`)).ok) return; } catch {} await sleep(200); } throw new Error('the seat did not come up'); }
const rpc = async (s, tok, tool, args) => (await (await fetch(`http://127.0.0.1:${s.port}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${tok}` }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }) })).json())?.result?.content?.[0]?.text;

let dbgPort = 26000 + Math.floor(Math.random() * 500);
async function browser() {
  const prof = mkdtempSync(path.join(tmpdir(), 'release-verify-prof-')); dirs.push(prof);
  const dbg = dbgPort++;
  const br = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${prof}`, `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
  children.push(br);
  let tabs;
  for (let i = 0; i < 50 && !tabs; i++) { await sleep(200); try { tabs = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch {} }
  const ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r, { once: true }));
  let id = 0; const waiting = new Map(); const b = { errors: [], statuses: [] };
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    const p = d.params || {};
    if (d.method === 'Runtime.exceptionThrown') b.errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
    else if (d.method === 'Network.responseReceived') b.statuses.push(`${p.response.status} ${new URL(p.response.url).pathname}`);
  });
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  b.send = send;
  b.ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true }); return r.result?.result?.value; };
  b.until = async (expr, what, ms = 25000) => { const t0 = Date.now(); for (;;) { const v = await b.ev(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what} (last: ${JSON.stringify(v)}; responses: ${b.statuses.slice(-8).join(', ')})`); await sleep(100); } };
  b.open = async (u) => { b.statuses.length = 0; await send('Page.navigate', { url: u }); };
  b.cookies = async (url) => (await send('Network.getCookies', { urls: [url] })).result.cookies;
  b.fill = async (sel, text) => { await b.ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.focus(); e.select(); })()`); await send('Input.insertText', { text }); };
  b.type = async (text) => { await b.ev(`document.querySelector('[data-key="DomNode:chat:input"]').focus()`); await send('Input.insertText', { text }); };
  b.key = (key, code, vk, text) => send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: vk, ...(text ? { text } : {}) });
  b.enter = () => b.key('Enter', 'Enter', 13, '\r');
  b.press = async (sel) => {
    await b.ev(`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({ block: 'center' })`);
    const [x, y] = await b.ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1, buttons: type === 'mousePressed' ? 1 : 0 });
  };
  b.close = async () => { ws.close(); br.kill(); await sleep(200); };
  return b;
}
const Q = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const K = (rid, part) => `[data-key="DomNode:set:${rid}:${part}"]`;
const textOf = (sel) => `((${Q(sel)} || {}).textContent || '')`;
const answers = `[...document.querySelectorAll('[data-key$=":ans"]')].map((e) => e.textContent)`;
const b64u = (buf) => Buffer.from(buf).toString('base64url');
const claimsOf = (jwt) => JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString());
const signedBy = (jwt, key) => b64u(createHmac('sha256', key).update(jwt.split('.').slice(0, 2).join('.')).digest()) === jwt.split('.')[2];

const A = launcher(), B = lan ? lanSeat() : null;
const b = await browser();
let gaps = [];
try {
  await Promise.all([up(A), B ? up(B, lan) : null]);
  await b.send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 760, deviceScaleFactor: 1, mobile: false });
  const urlA = `http://127.0.0.1:${A.port}`;

  // 1. the loopback page: the owner cookie, then a working page
  assert.equal((await b.cookies(urlA)).length, 0, 'a fresh browser holds no cookie');
  await b.open(`${urlA}/`);
  await b.until(Q('[data-key="DomNode:chat:input"]'), 'the chat composer');
  const cks = (await b.cookies(urlA)).filter((c) => c.name.startsWith('membrane_jwt_'));
  assert.equal(cks.length, 1, 'the page was given ONE cookie');
  const ck = cks[0];
  assert.match(ck.name, /^membrane_jwt_[0-9a-f]{8}$/);
  assert.equal(ck.httpOnly, true, 'HttpOnly'); assert.equal(ck.sameSite, 'Strict', 'SameSite=Strict'); assert.equal(ck.path, '/'); assert.equal(ck.secure, false, 'plain http: not Secure');
  assert.ok(ck.expires > Date.now() / 1000 + 6 * 86400 && ck.expires < Date.now() / 1000 + 8 * 86400, 'seven days');
  assert.ok(signedBy(ck.value, secret(A)), 'a token signed with this seat\'s secret'); assert.equal(claimsOf(ck.value).sub, 'owner');
  assert.equal(await b.ev(`document.cookie`), '', 'no script of the page can read it');
  // chat: a message and its answer, then the history after a reload
  await b.type('hello release'); await b.enter();
  await b.until(`${answers}.includes('Final answer to: hello release')`, 'the answer');
  await b.open(`${urlA}/`);
  await b.until(`${answers}.includes('Final answer to: hello release')`, 'the history after a reload (the cookie, not a new one, opens it)');
  assert.ok(b.statuses.every((s) => !s.startsWith('401 ')), `no 401 on the page's own calls: ${b.statuses.filter((s) => s.startsWith('401'))}`);
  // settings: read, write one, the seat holds it
  await b.press('[data-key="DomNode:shell:nav:settings"]');
  await b.until(`document.querySelectorAll('.setrow').length >= 20`, 'the settings rows');
  assert.equal(await b.ev(textOf(K('chat.max_steps', 'now'))), 'In force: 2048 steps', 'read');
  await b.fill(K('chat.max_steps', 'in'), '41'); await b.enter();
  await b.until(`${textOf(K('chat.max_steps', 'state'))} === 'Applied.' && ${textOf(K('chat.max_steps', 'now'))} === 'In force: 41 steps'`, 'the write applied');
  assert.equal(await rpc(A, owner(A), 'entity_read', { spec: 'Setting:chat.max_steps', prop: 'current' }), '41', 'the seat holds it');
  const html = await b.ev(`document.documentElement.outerHTML + ' ' + document.body.innerText`);
  for (const s of [owner(A), secret(A), ck.value]) assert.ok(!html.includes(s), 'the page shows no token and no secret');
  assert.deepEqual(b.errors, [], 'no script error (loopback)');

  if (!B) { console.log('release verify: the LAN part is skipped (no non-loopback IPv4 address on this machine)'); }
  else {
    // 2. the LAN page: no cookie, 401 in plain text, no sign-in screen, the login route is the way in
    const urlB = `http://${lan}:${B.port}`;
    await b.open(`${urlB}/`);
    await b.until(`!!document.getElementById('boot') && /could not start/.test(document.getElementById('boot').textContent)`, 'the page says why it could not start');
    const said = await b.ev(`document.getElementById('boot').textContent`);
    assert.match(said, /could not start.*401/, 'plain text, with the status');
    assert.equal(await b.ev(`document.getElementById('boot').getAttribute('role')`), 'alert');
    assert.ok((await b.ev(`document.body.innerText.trim().length`)) > 20, 'the page is not blank');
    assert.equal((await b.cookies(urlB)).filter((c) => c.name.startsWith('membrane_jwt')).length, 0, 'the page over the LAN address got NO cookie');
    for (const p of ['/session', '/ui/fieldvm', '/membrane/chat/messages', '/ui/settings', '/mcp']) {
      assert.equal(await b.ev(`fetch(${JSON.stringify(p)}).then((r) => r.status)`), 401, `${p} from the page`);
    }
    assert.equal(await b.ev(`fetch('/session').then((r) => r.text())`), '{"error":{"message":"missing or invalid token","type":"unauthorized"}}', 'the reason, in text');
    const fields = await b.ev(`document.querySelectorAll('input, textarea, form, [contenteditable], a[href*="login"], button').length`);
    if (fields === 0) gaps.push('the page offers NO sign-in screen: a person with only a token has no way in from the page (POST /ui/login exists)');
    // the way in the door has: POST /ui/login with a token, from the page's own origin
    const tok = owner(B);
    assert.equal(await b.ev(`fetch('/ui/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token: ${JSON.stringify(tok)} }) }).then((r) => r.status)`), 200, 'login');
    const lc = (await b.cookies(urlB)).filter((c) => c.name.startsWith('membrane_jwt'));
    assert.equal(lc.length, 1); assert.equal(lc[0].httpOnly, true); assert.equal(lc[0].sameSite, 'Strict');
    await b.open(`${urlB}/`);
    await b.until(Q('[data-key="DomNode:chat:input"]'), 'the page runs once the cookie is there');
    assert.equal(await b.ev(`document.getElementById('boot')`), null, 'the error text is gone');

    // 3. a second person: his token through the same login; the page is THE chat
    const bob = mint(B, 'bob', 'Bob');
    assert.equal(await b.ev(`fetch('/ui/logout', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }).then((r) => r.status)`), 200);
    assert.equal(await b.ev(`fetch('/ui/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ token: ${JSON.stringify(bob)} }) }).then((r) => r.status)`), 200);
    await b.open(`${urlB}/`);
    await b.until(Q('[data-key="DomNode:chat:input"]'), 'the chat for bob');
    assert.equal(await b.ev(`fetch('/ui/conf').then((r) => r.json()).then((j) => j.who.sub)`), 'bob', 'the page is bob\'s');
    await b.type('hello from bob'); await b.enter();
    await b.until(`${answers}.includes('Final answer to: hello from bob')`, 'bob\'s answer');
    assert.match(await rpc(B, owner(B), 'entity_query', { q: 'kind:Person' }), /Person:bob/, 'the seat knows bob');
    const hist = await (await fetch(`http://127.0.0.1:${B.port}/membrane/chat/messages`, { headers: { authorization: `Bearer ${owner(B)}` } })).text();
    assert.ok(hist.includes('hello from bob'), 'his message is in THE chat the owner reads');
    await b.press('[data-key="DomNode:shell:nav:settings"]');
    await b.until(`document.querySelectorAll('.setrow').length >= 20`, 'settings read for bob');
    assert.equal(await b.ev(textOf(K('chat.max_steps', 'now'))), 'In force: 2048 steps');
    await b.fill(K('chat.max_steps', 'in'), '9'); await b.enter();
    await b.until(`/\\S/.test(${textOf(K('chat.max_steps', 'state'))}) && ${textOf(K('chat.max_steps', 'state'))} !== 'Applied.'`, 'a refusal is shown in text');
    const refusal = await b.ev(textOf(K('chat.max_steps', 'state')));
    console.log(`release verify: bob's refused settings write shows: ${JSON.stringify(refusal)}`);
    assert.match(refusal, /owner|refus|forbid|403/i, 'the text says why');
    assert.equal(await b.ev(textOf(K('chat.max_steps', 'now'))), 'In force: 2048 steps', 'the value in force did not change');
    assert.equal(await rpc(B, owner(B), 'entity_read', { spec: 'Setting:chat.max_steps', prop: 'current' }), '2048', 'the seat did not change');
  }
  for (const g of gaps) console.log(`release verify: GAP: ${g}`);
  console.log('release verify: ok');
} finally {
  await b.close();
  for (const c of children) { try { c.kill(); } catch {} }
  for (const d of dirs) rmSync(d, { recursive: true, force: true });
}
