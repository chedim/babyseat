// The SETTINGS VIEW in a real browser (#1143): a REAL private cliffvm barest seat (the shipped packs + the barest app + the page's ui folder, own store, own port; the barest door is open: everyone who
// reaches the page is the owner, as in the view; the owner check on a person's token is proved in native/tests/settings_1143.rs) and the page in headless Chrome over the DevTools protocol. Asserted:
//   1. Settings opens: one section per group in the operator's order (Model, Chat, Home, Import, Access, Limits), no Map group, no Field tab, no dead setting, a restart row has no control;
//   2. the chat step limit is changed with the keyboard (type, Enter): the row says Applied, "In force" shows the new value, and the SEAT holds it (read back through its own door);
//   3. 0 is refused: the seat's reason is shown next to the row, the value in force is unchanged;
//   4. a text row (the chat model) saves with the Save button (a real mouse click);
//   5. the allow-list editor: add an entry, type its fields, Save: the seat holds the entry; remove it, Save: it is gone;
//   6. no secret is on the page (the API key, a provider url's password, the door secret, the owner key), only the NAME of the variable;
//   7. no script error. SHOTS=<dir> saves the screenshots: settings-dark, settings-light, settings-phone.
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
if (!existsSync(bin)) { console.log(`settings page: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`settings page: skipped, no Chrome at ${chrome} (set CHROME)`); process.exit(0); }

const KEY = 'sk-ant-settings-page-sentinel-4417';
const FAKE = `#!/usr/bin/env python3
import json, sys
sys.stdout.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "num_turns": 1, "duration_ms": 1, "total_cost_usd": 0, "usage": {}, "session_id": "f"}) + "\\n")
`;
const packs = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((n) => `membrane/packs/${n}.c1f`);
const dir = mkdtempSync(path.join(tmpdir(), 'settings-page-'));
const port = 24000 + Math.floor(Math.random() * 900);
const fake = path.join(dir, 'fake-claude');
writeFileSync(fake, FAKE); chmodSync(fake, 0o755);
const cloud = JSON.stringify([{ name: 'gpt', backend: 'openai', base_url: 'https://user:hunter2pw@example.test/v1', api_key_env: 'OTHER_PROVIDER_KEY' }]);
const child = spawn(bin, ['run', ...packs, 'membrane/apps/barest.c1f', '--store', path.join(dir, 'field.sqlite3'), '--port', String(port),
  '--fact', `chat_claude_bin=${fake}`, '--fact', `cloud_models=${cloud}`],
  { cwd: repo, stdio: 'ignore', env: { ...process.env, ANTHROPIC_API_KEY: KEY } });
const url = `http://127.0.0.1:${port}`;
async function up() { for (let i = 0; i < 150; i++) { try { if ((await fetch(`${url}/`)).ok) return; } catch {} await sleep(200); } throw new Error('the seat did not come up'); }

async function browser() {
  const prof = mkdtempSync(path.join(tmpdir(), 'settings-page-prof-'));
  const dbg = 24900 + Math.floor(Math.random() * 90);
  const br = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${prof}`, `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
  let tabs;
  for (let i = 0; i < 50 && !tabs; i++) { await sleep(200); try { tabs = await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch {} }
  const ws = new WebSocket(tabs.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r, { once: true }));
  let id = 0; const waiting = new Map(); const b = { errors: [], requests: [] };
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    const p = d.params || {};
    if (d.method === 'Runtime.exceptionThrown') b.errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
    else if (d.method === 'Runtime.consoleAPICalled' && p.type === 'error') b.errors.push('console.error: ' + (p.args || []).map((a) => a.value ?? a.description).join(' '));
    else if (d.method === 'Network.requestWillBeSent') b.requests.push(`${p.request.method} ${new URL(p.request.url).pathname}${p.request.postData ? ' ' + p.request.postData : ''}`);
  });
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  b.send = send;
  b.ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true }); return r.result?.result?.value; };
  b.until = async (expr, what, ms = 20000) => { const t0 = Date.now(); for (;;) { const v = await b.ev(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what} (last: ${JSON.stringify(v)}; the page's last requests: ${JSON.stringify(b.requests.slice(-3))}; errors: ${JSON.stringify(b.errors)})`); await sleep(100); } };
  b.open = async (u) => { await send('Page.navigate', { url: u }); };
  b.key = (key, code, vk, text) => send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: vk, ...(text ? { text } : {}) });
  b.enter = () => b.key('Enter', 'Enter', 13, '\r');
  b.size = (w, h, mobile = false) => send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile });
  // a person types into a box: focus it, select what is there, insert the text (an `input` event, as a keyboard does)
  b.fill = async (sel, text) => { await b.ev(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); e.focus(); e.select(); })()`); await send('Input.insertText', { text }); };
  // a real mouse click at the middle of an element
  b.press = async (sel) => {
    await b.ev(`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({ block: 'center' })`);
    const [x, y] = await b.ev(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
    for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1, buttons: type === 'mousePressed' ? 1 : 0 });
  };
  b.shot = async (name) => { if (!process.env.SHOTS) return; mkdirSync(process.env.SHOTS, { recursive: true }); await sleep(450); const f = path.join(process.env.SHOTS, name + '.png'); writeFileSync(f, Buffer.from((await send('Page.captureScreenshot', { format: 'png' })).result.data, 'base64')); console.log('  screenshot', f); };
  b.close = async () => { ws.close(); br.kill(); await sleep(200); rmSync(prof, { recursive: true, force: true }); };
  return b;
}
const Q = (sel) => `document.querySelector(${JSON.stringify(sel)})`;
const K = (rid, part) => `[data-key="DomNode:set:${rid}:${part}"]`;
const textOf = (sel) => `((${Q(sel)} || {}).textContent || '')`;
const b = await browser();
const rpc = async (tool, args) => { const r = await (await fetch(`${url}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }) })).json(); return r.result.content[0].text; };
const feedRow = async (id) => (await (await fetch(`${url}/ui/settings`)).json()).settings.find((r) => r.id === id);
try {
  await up();
  await b.size(1100, 760);
  await b.open(`${url}/`);
  await b.until(Q('.nav button'), 'the page');

  // 1. Settings: groups in order, no Map, no Field tab, a restart row has no control
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('.nav button')].map((e) => e.textContent)`), ['Chat', 'Settings'], 'no Field tab in this release');
  assert.equal(await b.ev(`!!${Q('.setview')}`), false, 'the view is drawn when the tab is first shown');
  await b.press('[data-key="DomNode:shell:nav:settings"]');
  await b.until(`document.querySelectorAll('.setrow').length >= 20`, 'the rows');
  assert.equal(await b.ev(`${Q('[data-key="DomNode:shell:slot:settings:empty"]')}.hidden`), true, 'the placeholder is gone');
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('.setgroup > h3')].map((e) => e.textContent)`), ['Model', 'Chat', 'Home', 'Import', 'Access', 'Limits'], 'the groups, in the operator\'s order, no Map');
  const labels = await b.ev(`[...document.querySelectorAll('.setrow .setlabel')].map((e) => e.textContent)`);
  assert.deepEqual(labels.slice(0, 8), ['Model', 'Fallback model', 'Available models', 'Other providers', 'Sign-in', 'Answering program', 'Most steps per answer', 'Answer time limit'], 'rows in the seat\'s order');
  assert.ok(!labels.some((l) => /Hidden kinds|Things shown|Tile size|Refresh every|Recent activity/.test(l)), 'no Map row');
  assert.ok(!labels.some((l) => /cortex|workers|think|loops/i.test(l)), 'no dead setting');
  assert.equal(await b.ev(`document.querySelectorAll('.setrow[data-key="DomNode:set:access.port"] input, .setrow[data-key="DomNode:set:access.port"] button, .setrow[data-key="DomNode:set:access.port"] form').length`), 0, 'a restart row has no control');
  assert.match(await b.ev(textOf(K('access.port', 'how'))), /^How to change it: start the seat with --port/);
  assert.equal(await b.ev(textOf(K('access.port', 'badge'))), 'Needs a restart'); assert.equal(await b.ev(textOf(K('chat.max_steps', 'badge'))), 'Applies now');
  assert.equal(await b.ev(textOf(K('chat.max_steps', 'now'))), 'In force: 2048 steps');
  assert.equal(await b.ev(textOf(K('chat.max_steps', 'limits'))), 'Allowed: 1 to 100000 steps, whole numbers. Default: 2048');
  assert.equal(await b.ev(textOf(K('model.cloud', 'value'))), 'gpt · openai · key in OTHER_PROVIDER_KEY');
  await b.shot('settings-dark-top');

  // 2. the chat step limit: type 25, Enter
  await b.fill(K('chat.max_steps', 'in'), '25'); await b.enter();
  await b.until(`${textOf(K('chat.max_steps', 'state'))} === 'Applied.' && ${textOf(K('chat.max_steps', 'now'))} === 'In force: 25 steps'`, 'applied, and in force');
  assert.equal(await b.ev(`${Q(K('chat.max_steps', 'state'))}.getAttribute('data-kind')`), 'ok');
  assert.equal((await feedRow('chat.max_steps')).current, 25, 'the seat holds it');
  assert.equal(await rpc('entity_read', { spec: 'Setting:chat.max_steps', prop: 'current' }), '25');
  const posts = b.requests.filter((r) => r.startsWith('POST /mcp'));
  assert.equal(posts.length, 1, 'ONE POST for the write'); assert.match(posts[0], /"entity_write".*"Setting:chat\.max_steps".*"set".*25/);

  // 3. 0 is refused, with the seat's reason, in text; nothing changed
  await b.fill(K('chat.max_steps', 'in'), '0'); await b.press(K('chat.max_steps', 'save'));
  await b.until(`/^Refused: Most steps per answer must be at least 1/.test(${textOf(K('chat.max_steps', 'state'))})`, 'the refusal and its reason');
  assert.equal(await b.ev(`${Q(K('chat.max_steps', 'state'))}.getAttribute('data-kind')`), 'bad');
  assert.equal(await b.ev(textOf(K('chat.max_steps', 'now'))), 'In force: 25 steps', 'the value in force did not change');
  assert.equal((await feedRow('chat.max_steps')).current, 25); assert.equal(await b.ev(`${Q(K('chat.max_steps', 'in'))}.value`), '0', 'what was typed stays');
  await b.shot('settings-refused');
  // a value that is not a number: the seat's reason names it
  await b.fill(K('chat.max_steps', 'in'), '2.5'); await b.enter();
  await b.until(`/whole number/.test(${textOf(K('chat.max_steps', 'state'))})`, 'a step count is whole');

  // 4. a text row with the Save button: the chat model
  await b.fill(K('model.chat', 'in'), 'claude-agent:sonnet'); await b.press(K('model.chat', 'save'));
  await b.until(`${textOf(K('model.chat', 'state'))} === 'Applied.' && ${textOf(K('model.chat', 'now'))} === 'In force: claude-agent:sonnet'`, 'the model applied');
  assert.equal(await rpc('entity_read', { spec: 'Minds:seat', prop: 'loops' }), 'claude-agent:sonnet', 'the chat\'s own reader holds it');
  await b.fill(K('model.chat', 'in'), 'not a model'); await b.enter();
  await b.until(`/not a model name/.test(${textOf(K('model.chat', 'state'))})`, 'a bad model name is refused with its reason');

  // 5. the allow-list editor
  await b.press(K('home.allow', 'add'));
  await b.until(`!!${Q(K('home.allow', 'en1:a'))}`, 'an empty entry');
  await b.fill(K('home.allow', 'en1:a'), 'kitchen'); await b.fill(K('home.allow', 'en1:b'), 'light.turn_on, light.turn_off'); await b.fill(K('home.allow', 'en1:c'), 'light.kitchen');
  await b.press(K('home.allow', 'save'));
  await b.until(`${textOf(K('home.allow', 'state'))} === 'Applied.' && /kitchen/.test(${textOf(K('home.allow', 'now'))})`, 'the entry applied');
  assert.equal(await rpc('entity_read', { spec: 'HaAllow:kitchen', prop: 'services' }), '["light.turn_on","light.turn_off"]');
  assert.equal(await rpc('entity_read', { spec: 'HaAllow:kitchen', prop: 'entities' }), '["light.kitchen"]');
  assert.equal(await b.ev(`document.querySelectorAll('.setview ${K('home.allow', 'lines')} .setentry').length`), 1, 'the applied entry is drawn again INSIDE its editor (the whole page holds one entry editor)');
  assert.equal(await b.ev(`document.querySelectorAll('.setentry').length`), 1);
  await b.shot('settings-allowlist');
  await b.press(K('home.allow', 'eikitchen:x'));
  await b.until(`!${Q(K('home.allow', 'eikitchen:a'))}`, 'the entry is gone from the editor');
  await b.press(K('home.allow', 'save'));
  await b.until(`/no entry/.test(${textOf(K('home.allow', 'now'))})`, 'the list is empty again');
  assert.deepEqual((await feedRow('home.allow')).current, [], 'the seat holds no entry');

  // 6. no secret on the page; the NAME of the variable is
  const page = await b.ev(`document.documentElement.outerHTML + ' ' + document.body.innerText`);
  for (const s of [KEY, 'hunter2pw']) assert.ok(!page.includes(s), `the page shows a secret (${s.slice(0, 6)}...)`);
  assert.ok(page.includes('OTHER_PROVIDER_KEY'));
  assert.ok(!b.requests.some((r) => r.includes(KEY) || r.includes('hunter2pw')), 'no request carries one');

  // the same page: dark, light, phone width (the theme button: system, light, dark)
  await b.ev(`window.scrollTo(0, 0); document.querySelector('.setview').scrollTop = 0`);
  await b.press('[data-key="DomNode:shell:theme"]');
  await b.until(`document.documentElement.getAttribute('data-theme') === 'light'`, 'light');
  await b.shot('settings-light');
  await b.press('[data-key="DomNode:shell:theme"]');
  await b.until(`document.documentElement.getAttribute('data-theme') === 'dark'`, 'dark');
  await b.shot('settings-dark');
  await b.size(390, 800, true);
  await b.ev(`document.querySelector('.setview').scrollTop = 0`);
  await b.shot('settings-phone');
  assert.equal(await b.ev(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'no horizontal scroll at phone width');
  await b.ev(`document.querySelector('${K('home.allow', 'add')}').scrollIntoView({ block: 'center' })`);
  await b.shot('settings-phone-allowlist');
  assert.deepEqual(b.errors, [], 'no script error in the page');
  console.log('settings page: ok');
} finally { await b.close(); child.kill(); rmSync(dir, { recursive: true, force: true }); }
