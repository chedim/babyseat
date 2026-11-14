// The graph link, slice 1, END TO END (#1143, docs/graph-link.md "Slice 1: built"): a REAL private cliffvm barest seat (the shipped packs + the barest app, own store, own port, no model) and the page in
// headless Chrome over the DevTools protocol. What is asserted (counts and content, never time):
//   1. a page load tells the seat: ONE POST /mcp for `Vm:<ns>.caps` and ONE for `<ns>::Page:main.report` (no other POST), and the model's own door reads them back: the packs it runs, the DOM tag list, its version;
//   2. an idle page sends ZERO more POSTs (no timer, no heartbeat); the shell's own GET poll is not a write;
//   3. a change of the view, of hidden/visible and of the width class is ONE report each (and the seat reads it);
//   4. two tabs are two roots (`kind:Vm` lists two), each with its own name; the names are `p` + 8 hex digits;
//   5. a deliberately broken pack's load error reaches the seat as text, and that pack is not in `provides`;
//   6. no script error on the page.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync, writeFileSync, readFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const bin = process.env.CLIFFVM || path.join(repo, 'native/target/release/cliffvm');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`link page: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`link page: skipped, no Chrome at ${chrome} (set CHROME)`); process.exit(0); }

const packs = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((n) => `membrane/packs/${n}.c1f`);
let nseat = 0;
function startSeat({ apps = [] } = {}) {
  const port = 24000 + Math.floor(Math.random() * 1000) + 7 * nseat++;
  const dir = mkdtempSync(path.join(tmpdir(), 'link-page-'));
  const args = ['run', ...packs, 'membrane/apps/barest.c1f', ...apps, '--store', path.join(dir, 'field.sqlite3'), '--port', String(port)];
  const child = spawn(bin, args, { cwd: repo, stdio: 'ignore', env: { ...process.env } });
  return { port, dir, url: `http://127.0.0.1:${port}`, stop: () => { child.kill(); rmSync(dir, { recursive: true, force: true }); } };
}
async function up(seat) { for (let i = 0; i < 150; i++) { try { if ((await fetch(`${seat.url}/`)).ok) return; } catch {} await sleep(200); } throw new Error('the seat did not come up'); }
const call = async (s, tool, args) => (await (await fetch(`${s.url}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }) })).json()).result.content[0].text;

const dbg = 25000 + Math.floor(Math.random() * 500);
const prof = mkdtempSync(path.join(tmpdir(), 'link-page-prof-'));
const br = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', `--user-data-dir=${prof}`, `--remote-debugging-port=${dbg}`, 'about:blank'], { stdio: 'ignore' });
async function tabs() { for (let i = 0; i < 50; i++) { try { return await (await fetch(`http://127.0.0.1:${dbg}/json`)).json(); } catch { await sleep(200); } } throw new Error('no browser'); }
const open = [];
async function tab(url) {
  const t = url ? await (await fetch(`http://127.0.0.1:${dbg}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' })).json() : (await tabs()).find((x) => x.type === 'page');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r, { once: true }));
  let id = 0; const waiting = new Map(); const b = { errors: [], posts: [] };
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    const p = d.params || {};
    if (d.method === 'Runtime.exceptionThrown') b.errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
    else if (d.method === 'Network.requestWillBeSent' && p.request.method === 'POST') b.posts.push({ path: new URL(p.request.url).pathname, body: p.request.postData });
  });
  const send = (method, params = {}) => new Promise((r) => { waiting.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
  await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
  b.send = send;
  b.ev = async (expr) => (await send('Runtime.evaluate', { expression: expr, returnByValue: true })).result?.result?.value;
  b.until = async (expr, what, ms = 20000) => { const t0 = Date.now(); for (;;) { const v = await b.ev(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error(`timed out waiting for ${what}`); await sleep(100); } };
  b.go = (u) => send('Page.navigate', { url: u });
  // what the page wrote to the seat: [{spec, prop, value}] in order
  b.writes = () => b.posts.filter((p) => p.path === '/mcp').map((p) => { const a = JSON.parse(p.body).params.arguments; return { spec: a.spec, prop: a.prop, value: a.value }; });
  open.push(ws);
  if (url) await b.go(url);
  return b;
}

const seat = startSeat();
const brokenDir = mkdtempSync(path.join(tmpdir(), 'link-broken-')) + '.c1f';
mkdirSync(brokenDir);
writeFileSync(path.join(brokenDir, 'broken_view.c1f'), 'PACK broken_view 1\nIMPORT dom 1\nBox.x getter: this.y +* (\n');
const seat2 = startSeat({ apps: [brokenDir] });
let code = 0;
try {
  await Promise.all([up(seat), up(seat2)]);
  const a = await tab();
  await a.go(`${seat.url}/`);
  const ns = await a.until(`(window.MEMBRANE_PAGE && window.MEMBRANE_PAGE.get('Link:page') || {}).ns`, 'the page knows its name');
  assert.match(ns, /^p[0-9a-f]{8}$/, 'the page is named by the seat: p + 8 hex digits');
  await a.until(`window.MEMBRANE_PAGE.get('Shell:main').open === true`, 'the shell is open');
  const seen = async (n) => { for (let i = 0; i < 100; i++) { const w = a.writes(); if (w.length >= n) return w; await sleep(100); } return a.writes(); };

  // 1. ONE caps and ONE report, nothing else; the model's door reads them back
  const w = await seen(2); await sleep(700);
  assert.deepEqual(a.writes().map((x) => `${x.spec} ${x.prop}`).sort(), [`${ns}::Page:main report`, `Vm:${ns} caps`].sort(), 'the load: one caps write and one report write');
  assert.equal(a.posts.length, 2, 'no other POST of any kind');
  const caps = await call(seat, 'entity_read', { spec: `Vm:${ns}`, prop: 'caps' });
  for (const must of ['shell_view', 'chat_view', 'settings_view', 'link_view', 'dom', 'fetch', 'closure', 'button', 'svgTags', 'hash', 'protocol', 'json', 'desktop', 'write-request', 'subscriptions']) assert.ok(caps.includes(must), `caps tell ${must}: ${caps.slice(0, 300)}`);
  const rep = await call(seat, 'entity_read', { spec: `${ns}::Page:main`, prop: 'report' });
  assert.ok(/chat/.test(rep) && /cliffvm-/.test(rep) && /shell_view/.test(rep) && /wide/.test(rep), `the report: ${rep.slice(0, 300)}`);
  assert.equal(await call(seat, 'entity_read', { spec: `Vm:${ns}`, prop: 'state' }), 'connected');
  const reportOf = (n) => a.writes().filter((x) => x.prop === 'report')[n].value;
  assert.deepEqual(reportOf(0).load_errors, {}, 'nothing failed to load');
  assert.equal(reportOf(0).view, 'chat'); assert.equal(reportOf(0).hidden, false);

  // 2. an idle page sends ZERO more (the shell's GET poll runs meanwhile: a read, not a write)
  const gets = await a.ev(`performance.getEntriesByType('resource').filter((r) => r.name.includes('/session/status')).length`);
  await sleep(4500);
  assert.ok((await a.ev(`performance.getEntriesByType('resource').filter((r) => r.name.includes('/session/status')).length`)) > gets, 'the status poll went on (so the idle window was real)');
  assert.equal(a.posts.length, 2, 'an idle page sends zero POSTs');

  // 3. one report for each change: the view, hidden, visible, the width class; the seat reads each
  await a.ev(`document.querySelector('[data-key="DomNode:shell:nav:settings"]').dispatchEvent(new MouseEvent('click', {bubbles: true}))`);
  await seen(3); await sleep(500);
  assert.equal(a.writes().length, 3); assert.equal(reportOf(1).view, 'settings');
  await a.ev(`Object.defineProperty(document, 'hidden', {value: true, configurable: true}); document.dispatchEvent(new Event('visibilitychange'))`);
  await seen(4); await sleep(500);
  assert.equal(a.writes().length, 4); assert.equal(reportOf(2).hidden, true, 'a hidden tab reports hidden');
  assert.match(await call(seat, 'entity_read', { spec: `${ns}::Page:main`, prop: 'report' }), /hidden['"]?: ?(true|True)/);
  await a.ev(`Object.defineProperty(document, 'hidden', {value: false, configurable: true}); document.dispatchEvent(new Event('visibilitychange'))`);
  await seen(5); await sleep(500);
  assert.equal(a.writes().length, 5); assert.equal(reportOf(3).hidden, false);
  await a.send('Emulation.setDeviceMetricsOverride', { width: 500, height: 700, deviceScaleFactor: 1, mobile: false });
  await seen(6); await sleep(500);
  assert.equal(a.writes().length, 6); assert.equal(reportOf(4).width, 'narrow', 'the width class crossed');
  assert.ok(a.writes().slice(2).every((x) => x.prop === 'report'), 'caps are told once');
  assert.equal(a.posts.length, 6, 'one POST per change, nothing else');

  // 4. a second tab is a second root
  const b = await tab(`${seat.url}/`);
  const ns2 = await b.until(`(window.MEMBRANE_PAGE && window.MEMBRANE_PAGE.get('Link:page') || {}).ns`, 'the second page knows its name');
  assert.notEqual(ns2, ns); assert.match(ns2, /^p[0-9a-f]{8}$/);
  await b.until(`window.MEMBRANE_PAGE.get('Shell:main').open === true`, 'the second shell is open');
  for (let i = 0; i < 100 && b.writes().length < 2; i++) await sleep(100);
  await sleep(500);
  assert.equal(b.writes().length, 2, 'the second tab told its own caps and report');
  const q = (await call(seat, 'entity_query', { q: 'kind:Vm' })).split('\n').map((l) => l.split(' ')[0]).sort();
  assert.deepEqual(q, [`Vm:${ns}`, `Vm:${ns2}`].sort(), 'two tabs, two roots');
  assert.equal(a.posts.length, 6, 'the first tab sent nothing for the second');

  // 5. a broken pack: its load error reaches the seat as text, and it is not in `provides`
  const c = await tab(`${seat2.url}/`);
  const ns3 = await c.until(`(window.MEMBRANE_PAGE && window.MEMBRANE_PAGE.get('Link:page') || {}).ns`, 'the page of the second seat');
  for (let i = 0; i < 100 && c.writes().length < 2; i++) await sleep(100);
  await sleep(500);
  const rep3 = await call(seat2, 'entity_read', { spec: `${ns3}::Page:main`, prop: 'report' });
  assert.ok(rep3.includes('broken_view'), `the load error names the pack: ${rep3.slice(0, 400)}`);
  const lerr = c.writes().find((x) => x.prop === 'report').value.load_errors;
  assert.deepEqual(Object.keys(lerr), ['broken_view']); assert.ok(String(lerr.broken_view).length > 0, 'with the loader\'s own message');
  const caps3 = c.writes().find((x) => x.prop === 'caps').value;
  assert.ok(!caps3.provides.some((p) => p.pack === 'broken_view'), 'a pack that did not load is not provided');
  assert.ok(caps3.provides.some((p) => p.pack === 'link_view'));

  // 6. no script error
  for (const t of [a, b, c]) assert.deepEqual(t.errors, [], 'no script error');
  console.log('link page: ok');
} catch (e) { console.error(e); code = 1; }
for (const w of open) try { w.close(); } catch {}
br.kill(); seat.stop(); seat2.stop();
await sleep(200); rmSync(prof, { recursive: true, force: true });
process.exit(code);
