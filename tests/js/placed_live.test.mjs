// A view pack the model rewrites reaches the open page and is drawn again, with no reload (docs/graph-link.md section 16, "Placement by imports"): a REAL private cliffvm barest seat and the page
// in headless Chrome over the DevTools protocol. A pack that imports `dom` and is written through the door as `Pack:<name>.text` is placed on the page (`Placed:<ns>:<name>`), the page's link loads it,
// and when its text changes the page's engine replaces it. What is asserted: (1) a first text is loaded by the page and draws; (2) the same pack written again with other text draws the new text on the SAME
// page (a marker on `window` proves the page was not reloaded), with nothing else done to the page: no click, no write. (Counts and content, never time.)
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, rmSync, existsSync, writeFileSync, readFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const chrome = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const bin = process.env.CLIFFVM || path.join(process.env.CLIFFVM_DIST || '', 'bin', 'cliffvm');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
if (!existsSync(bin)) { console.log(`placed live: skipped, no cliffvm at ${bin} (build it, or set CLIFFVM)`); process.exit(0); }
if (!existsSync(chrome)) { console.log(`placed live: skipped, no Chrome at ${chrome} (set CHROME)`); process.exit(0); }

const packs = readFileSync(path.join(repo, 'membrane/apps/barest.packs'), 'utf8').split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('#')).map((n) => `membrane/packs/${n}.c1f`);
let nseat = 0;
function startSeat({ apps = [], facts = [], port: at, dir: reuse } = {}) {
  const port = at || 24000 + Math.floor(Math.random() * 1000) + 7 * nseat++;
  const dir = reuse || mkdtempSync(path.join(tmpdir(), 'placed-live-'));
  const args = ['run', ...packs, 'membrane/apps/barest.c1f', ...apps, '--store', path.join(dir, 'field.sqlite3'), '--port', String(port), '--fact', 'minds={"loops":"claude","cortex":"claude","workers":"claude","seat_default":"claude"}', '--fact', 'seat_name=Rosie', ...facts.flatMap((f) => ['--fact', f])];
  const child = spawn(bin, args, { cwd: repo, stdio: 'ignore', env: { ...process.env } });
  return { port, dir, url: `http://127.0.0.1:${port}`, down: () => { child.kill(); }, stop: () => { child.kill(); rmSync(dir, { recursive: true, force: true }); } };
}
async function up(seat) { for (let i = 0; i < 150; i++) { try { if ((await fetch(`${seat.url}/`)).ok) return; } catch {} await sleep(200); } throw new Error('the seat did not come up'); }
const call = async (s, tool, args) => (await (await fetch(`${s.url}/mcp`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: tool, arguments: args } }) })).json()).result.content[0].text;

const dbg = 25000 + Math.floor(Math.random() * 500);
const prof = mkdtempSync(path.join(tmpdir(), 'placed-live-prof-'));
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


const pack = (v) => `PACK livev 1\nIMPORT dom 1\nIMPORT shell_view 1\nShell.open on-setter draw:\n  ALWAYS:\n    ('DomNode:livev:a').tag = 'span'\n    ('DomNode:livev:a').text = '${v}'\n    ('DomNode:livev:a').parent = 'DomNode:shell:slot:chat'\n`;
const seat = startSeat();
let code = 0;
try {
  await up(seat);
  const a = await tab();
  await a.go(`${seat.url}/`);
  await a.until(`window.MEMBRANE_PAGE && window.MEMBRANE_PAGE.get('Shell:main') && window.MEMBRANE_PAGE.get('Shell:main').open === true`, 'the shell is open');
  await a.ev(`window.__same_page = 'yes'`);
  const text = `(document.querySelector('[data-key="DomNode:livev:a"]') || {}).textContent`;

  // 1. the pack is written once: the seat places it on the page, the page loads it
  await call(seat, 'entity_write', { spec: 'Pack:livev', prop: 'text', value: pack('first') });
  for (let i = 0; i < 100 && (await a.ev(text)) !== 'first'; i++) await sleep(150);
  assert.equal(await a.ev(text), 'first', 'the page loaded the placed pack and it drew with nothing done to the page');

  // 2. the model writes the pack again: the page draws the new text, with nothing done to the page
  await call(seat, 'entity_write', { spec: 'Pack:livev', prop: 'text', value: pack('second') });
  let drawn = null;
  for (let i = 0; i < 100; i++) { drawn = await a.ev(text); if (drawn === 'second') break; await sleep(150); }
  assert.equal(drawn, 'second', `the page still draws the first text after the pack was rewritten (it shows ${JSON.stringify(drawn)}): a rewritten view pack is not drawn again without a reload or a poke`);
  assert.equal(await a.ev(`window.__same_page`), 'yes', 'the page was not reloaded');
  assert.deepEqual(a.errors, [], 'no script error on the page');
  console.log('placed live: ok');
} catch (e) { console.error(e); code = 1; }
finally { for (const ws of open) try { ws.close(); } catch {} br.kill(); seat.stop(); try { rmSync(prof, { recursive: true, force: true }); } catch {} process.exit(code); }
