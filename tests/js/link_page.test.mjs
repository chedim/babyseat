// The graph link, slice 1, END TO END (#1143, docs/graph-link.md "Slice 1: built"): a REAL private cliffvm barest seat (the shipped packs + the barest app, own store, own port, no model) and the page in
// headless Chrome over the DevTools protocol. What is asserted (counts and content, never time):
//   1. a page load tells the seat: ONE POST /mcp for `Vm:<ns>.caps` and ONE for `<ns>::Page:main.report` (no other POST), and the model's own door reads them back: the packs it runs, the DOM tag list, its version;
//   2. an idle page sends ZERO more POSTs (no timer, no heartbeat); the shell's own GET poll is not a write;
//   3. a change of the view, of hidden/visible and of the width class is ONE report each (and the seat reads it);
//   4. two tabs are two roots (`kind:Vm` lists two), each with its own name; the names are `p` + 8 hex digits;
//   5. a deliberately broken pack's load error reaches the seat as text, and that pack is not in `provides`;
//   6. no script error on the page.
// (the graph link's frames, `POST /ui/link`, are counted apart: `b.link`; `b.posts` is every OTHER POST, so the slice-1 counts below still mean what they said.)
// SLICE 2, the phase at the end (section 7): subscriptions of the seat's values into the open settings window, counted in requests: a write by another client shows with no GET of the page; an idle page sends only the link's
// keepalives; a hidden tab sends one frame and then nothing; a seat that restarts is found again within the jitter and the page subscribes again.
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
function startSeat({ apps = [], facts = [], port: at, dir: reuse } = {}) {
  const port = at || 24000 + Math.floor(Math.random() * 1000) + 7 * nseat++;
  const dir = reuse || mkdtempSync(path.join(tmpdir(), 'link-page-'));
  const args = ['run', ...packs, 'membrane/apps/barest.c1f', ...apps, '--store', path.join(dir, 'field.sqlite3'), '--port', String(port), '--fact', 'minds={"loops":"claude","cortex":"claude","workers":"claude","seat_default":"claude"}', '--fact', 'seat_name=Rosie', ...facts.flatMap((f) => ['--fact', f])];
  const child = spawn(bin, args, { cwd: repo, stdio: 'ignore', env: { ...process.env } });
  return { port, dir, url: `http://127.0.0.1:${port}`, down: () => { child.kill(); }, stop: () => { child.kill(); rmSync(dir, { recursive: true, force: true }); } };
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
  let id = 0; const waiting = new Map(); const b = { errors: [], posts: [], link: [], gets: [] };
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); return; }
    const p = d.params || {};
    if (d.method === 'Runtime.exceptionThrown') b.errors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text);
    else if (d.method === 'Network.requestWillBeSent' && p.request.method === 'POST') { const x = { path: new URL(p.request.url).pathname, body: p.request.postData }; (x.path === '/ui/link' ? b.link : b.posts).push(x); }
    else if (d.method === 'Network.requestWillBeSent' && p.request.method === 'GET') b.gets.push(new URL(p.request.url).pathname);
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

  // 7. SLICE 2: the settings window follows the seat's own values through the link (a seat with a short hold: the held poll is released after 2 s)
  const seat3 = startSeat({ facts: ['link_hold_s=2'] });
  await up(seat3);
  const d = await tab(`${seat3.url}/`);
  const nsd = await d.until(`(window.MEMBRANE_PAGE && window.MEMBRANE_PAGE.get('Link:page') || {}).ns`, 'the link page knows its name');
  await d.until(`window.MEMBRANE_PAGE.get('Shell:main').open === true`, 'the shell is open');
  await d.ev(`document.querySelector('[data-key="DomNode:shell:nav:settings"]').dispatchEvent(new MouseEvent('click', {bubbles: true}))`);
  const row = (id) => `(window.MEMBRANE_PAGE.get('DomNode:set:${id}:now') || {}).text`;
  await d.until(`${row('chat.max_steps')} === 'In force: 2048 steps'`, 'the settings rows are drawn');
  await d.until(`(window.MEMBRANE_PAGE.get('Link:page') || {}).up === true`, 'the link is up (the engine has the hooks and the seat answered)');
  const frames = () => d.link.map((x) => JSON.parse(x.body));
  const subsOf = () => frames().flatMap((f) => f.subs);
  await d.until(`window.MEMBRANE_PAGE.get('membrane::Setting:chat.max_steps') && window.MEMBRANE_PAGE.get('membrane::Setting:chat.max_steps').current !== undefined`, 'a replica of the seat\'s value arrived');
  assert.ok(subsOf().some((x) => x[0] === 'membrane::Setting:chat.max_steps' && x[1] === 'current'), 'the engine named the value the row reads, and the page told the seat');
  assert.ok(subsOf().some((x) => x[0] === 'membrane::Doorbell:chat' && x[1] === 'at'), 'and the chat\'s doorbell');
  assert.ok(!subsOf().some((x) => /Chat:(main|message|session)/.test(x[0])), 'never the chat');
  assert.equal(frames().filter((f) => f.subs.length > 0).length <= 3, true, 'the window\'s 27 rows are announced in a handful of frames (one per flush), not one per row');
  // the other client writes: the row follows with NO request of the page
  await sleep(1500);
  const before = { settings: d.gets.filter((g) => g === '/ui/settings').length, chat: d.gets.filter((g) => g === '/membrane/chat/messages').length, posts: d.posts.length };
  await call(seat3, 'entity_write', { spec: 'Setting:chat.max_steps', prop: 'set', value: 777 });
  await d.until(`${row('chat.max_steps')} === 'In force: 777 steps'`, 'the open window shows the new value');
  assert.equal(d.gets.filter((g) => g === '/ui/settings').length, before.settings, 'with no read of /ui/settings');
  assert.equal(d.posts.length, before.posts, 'and no write');
  assert.ok(before.chat <= 2, `the chat history was read at most twice (the load and the first ring), not on a timer: ${before.chat}`);

  // idle: the link's keepalives only. Three held polls come back empty; during them the page sends nothing else and reads nothing
  const idle0 = { link: d.link.length, gets: d.gets.filter((g) => g !== '/session/status').length, posts: d.posts.length };
  for (let i = 0; i < 100 && d.link.length < idle0.link + 3; i++) await sleep(200);
  assert.ok(d.link.length >= idle0.link + 3, 'the keepalives came');
  const idleFrames = d.link.slice(idle0.link).map((x) => JSON.parse(x.body));
  assert.ok(idleFrames.every((f) => f.subs.length === 0 && f.unsubs.length === 0 && !f.resync), 'an idle frame is a bare keepalive');
  assert.ok(d.link.slice(idle0.link).every((x) => x.body.length < 200), 'under 200 bytes each');
  assert.equal(d.gets.filter((g) => g !== '/session/status').length, idle0.gets, 'an idle page reads nothing (the shell\'s status line is the one other poll, not the link\'s)');
  assert.equal(d.posts.length, idle0.posts, 'and writes nothing');

  // a hidden tab: ONE frame, then nothing, however long and whatever changes; visible: ONE frame with resync and the new value
  await d.ev(`Object.defineProperty(document, 'hidden', {value: true, configurable: true}); document.dispatchEvent(new Event('visibilitychange'))`);
  for (let i = 0; i < 50 && !d.link.some((x) => JSON.parse(x.body).hidden === true); i++) await sleep(100);
  await d.until(`window.MEMBRANE_PAGE.graph.inflight === 0`, 'the hidden frame is answered and the released poll is back (nothing is in flight)');
  const hid = d.link.length;
  assert.equal(d.link.filter((x) => JSON.parse(x.body).hidden === true).length, 1, 'one frame says hidden');
  await call(seat3, 'entity_write', { spec: 'Setting:chat.max_steps', prop: 'set', value: 888 });
  await sleep(5000);                                                              // two holds and more: a hidden page sends nothing
  assert.equal(d.link.length, hid, 'a hidden tab sends no frame');
  assert.equal(await d.ev(row('chat.max_steps')), 'In force: 777 steps', 'and shows what it had');
  await d.ev(`Object.defineProperty(document, 'hidden', {value: false, configurable: true}); document.dispatchEvent(new Event('visibilitychange'))`);
  await d.until(`${row('chat.max_steps')} === 'In force: 888 steps'`, 'visible again: the value that changed meanwhile arrives');
  const back = d.link.slice(hid).map((x) => JSON.parse(x.body));
  assert.equal(back[0].hidden, false); assert.equal(back[0].resync, true, 'one frame, asking for a resync');

  // the seat restarts (same port, same store): the page finds it again within the jitter and subscribes again
  const port3 = seat3.port;
  seat3.down();
  await d.until(`(window.MEMBRANE_PAGE.get('Link:page') || {}).up === false`, 'the page notices the seat is gone', 30000);
  const failedAt = d.link.length;
  await sleep(1500);
  const seat3b = startSeat({ facts: ['link_hold_s=2'], port: port3, dir: seat3.dir });
  await up(seat3b);
  await d.until(`(window.MEMBRANE_PAGE.get('Link:page') || {}).up === true`, 'the page is connected to the new seat', 30000);
  const after = d.link.slice(failedAt).map((x) => JSON.parse(x.body));
  const resub = after.find((f) => f.subs.some((x) => x[0] === 'membrane::Setting:chat.max_steps'));
  assert.ok(resub, 'after the restart the page announced its subscriptions again');
  assert.ok(resub.subs.length >= 25, `all of them, in ONE frame (${resub.subs.length})`);
  await call(seat3b, 'entity_write', { spec: 'Setting:chat.max_steps', prop: 'set', value: 999 });
  await d.until(`${row('chat.max_steps')} === 'In force: 999 steps'`, 'and the row follows the NEW seat');
  assert.deepEqual(d.errors, [], 'no script error on the link page');
  seat3b.stop();

  // 6. no script error
  for (const t of [a, b, c]) assert.deepEqual(t.errors, [], 'no script error');
  console.log('link page: ok');
} catch (e) { console.error(e); code = 1; }
for (const w of open) try { w.close(); } catch {}
br.kill(); seat.stop(); seat2.stop();
await sleep(200); rmSync(prof, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
process.exit(code);
