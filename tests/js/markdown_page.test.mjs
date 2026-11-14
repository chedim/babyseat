// MARKDOWN, STOP and TOOL ROWS in the REAL page (#1143): a private cliffvm barest seat (own store, own port) with a FAKE claude (it answers a message with a markdown document holding every construct and an attack on
// the page; `hang` says something and waits for ever; `tools` runs two tools), and the page in headless Chrome over the DevTools protocol. What is asserted (counts and structure, never time):
//   1. the answer is DOM: headings, a code block with its language and a Copy button, a quote in a quote, a list nested three deep, an ordered list, a table, bold/italic/strike/code/links; the person's own message too;
//   2. the attack is inert text: no script or img element in the answer, `window.__pwned` stays undefined, no javascript: href, every link is http(s) and opens in a new tab with noopener;
//   3. Copy puts the code body on the clipboard (the browser's writeText is stubbed to record it); 4. Stop: shown while a turn runs, ends it (the answer so far is kept and says "stopped"), the child process is gone, the button goes;
//   5. the tool rows: one collapsible row per turn, "2 tools", each with its name and shortened input; 6. no script error. SHOTS=<dir> saves screenshots (dark, light, phone width).
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
def out(o):
    sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
out({"type": "system", "subtype": "init", "session_id": "fake-1", "tools": []})
if prompt == "hang":
    open(os.path.join(here, "pid.hang"), "w").write(str(os.getpid()))
    out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "starting the work"}}})
    time.sleep(3600)
if prompt == "tools":
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."}, {"type": "tool_use", "id": "tu1", "name": "mcp__membrane__entity_read", "input": {"spec": "Command:probe"}}, {"type": "tool_use", "id": "tu2", "name": "Bash", "input": "ls -la /tmp"}]}})
    out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": "the result"}]}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": "Used two tools.", "num_turns": 2, "duration_ms": 10, "total_cost_usd": 0, "usage": {}, "session_id": "fake-1"})
    sys.exit(0)
answer = open(os.path.join(here, "answer.md")).read()
out({"type": "result", "subtype": "success", "is_error": False, "result": answer, "num_turns": 1, "duration_ms": 10, "total_cost_usd": 0, "usage": {}, "session_id": "fake-1"})
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
  const args = ['run', ...packs, 'membrane/apps/barest.c1f', ...apps, '--store', path.join(dir, 'field.sqlite3'), '--port', String(port),
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


const MD_ANSWER = `# Heading one
## Heading two
### Heading three
#### Heading four

A paragraph with **bold**, *italic*, ~~struck~~, \`inline code\`, a [link](https://example.com/page) and a bare https://example.org/x. Second line of the same paragraph.

> a quote with **bold**
> > a quote inside a quote

---

- bullet one
- bullet two
  - nested a
    - nested deeper
  - nested b
- bullet three

1. first
2. second
   - mixed inside

| name | value |
|------|:-----:|
| one  | \`1\`   |
| two  | **2** |

\`\`\`python
def hello():
    return "world"
\`\`\`

~~~js
console.log(1)
~~~

<script>window.__pwned = 1</script>
<img src=x onerror="window.__pwned = 2">
[evil](javascript:window.__pwned=3) done`;
const seat = startSeat({});
writeFileSync(path.join(seat.dir, 'answer.md'), MD_ANSWER);
const b = await browser();
const ans = '[data-key$=":ans:md"]';
const count = (sel) => b.ev(`document.querySelectorAll(${JSON.stringify(sel)}).length`);
try {
  await up(seat);
  await b.size(1000, 1400);
  await b.open(`${seat.url}/`);
  await b.until(Q(K('DomNode:chat:input')), 'the chat composer');
  await b.type('## show me everything'); await b.enter();
  await b.until(`${Q(ans + ' pre')}`, 'the answer is markdown (a code block is there)');
  await b.until(`${Q('[data-key$=":me:md"] h4')}`, 'the person\'s own message is markdown too (the old page rendered both)');

  // 1. structure
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('${ans} h3, ${ans} h4, ${ans} h5, ${ans} h6')].map((e) => e.tagName + ':' + e.textContent)`), ['H3:Heading one', 'H4:Heading two', 'H5:Heading three', 'H6:Heading four']);
  assert.equal(await count(ans + ' pre'), 2, 'a ``` and a ~~~ block');
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('${ans} pre')].map((p) => [p.getAttribute('data-lang'), p.querySelector('.md-lang').textContent, p.querySelector('.md-copy').textContent, p.querySelector('code.md-codebody').textContent])`),
    [['python', 'python', 'Copy', 'def hello():\n    return "world"'], ['js', 'js', 'Copy', 'console.log(1)']]);
  assert.equal(await count(ans + ' blockquote blockquote'), 1, 'a quote in a quote');
  assert.equal(await count(ans + ' hr'), 1);
  assert.equal(await count(ans + ' ul ul ul'), 1, 'a list nested three deep');
  assert.equal(await count(ans + ' ol'), 1); assert.equal(await count(ans + ' ol ul'), 1, 'a bullet inside a numbered item');
  assert.equal(await count(ans + ' table th'), 2); assert.equal(await count(ans + ' table td'), 4);
  assert.equal(await count(ans + ' strong, ' + ans + ' .md-b'), 3, 'bold: in the quote, the paragraph and a cell');
  assert.equal(await count(ans + ' .md-i'), 1); assert.equal(await count(ans + ' .md-s'), 1);
  assert.equal(await count(ans + ' code.md-code'), 2, 'inline code: the paragraph and a cell');
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('${ans} a')].map((a) => [a.textContent, a.getAttribute('href'), a.getAttribute('target'), a.getAttribute('rel')])`),
    [['link', 'https://example.com/page', '_blank', 'noopener noreferrer'], ['https://example.org/x', 'https://example.org/x', '_blank', 'noopener noreferrer']]);

  assert.deepEqual(await b.ev(`[...document.querySelector('${ans}').querySelectorAll('h3,h4,h5,h6,p,ul,ol,table,blockquote,hr,pre')].filter((e) => !e.parentElement.closest('li.md-li, blockquote, table')).map((e) => e.tagName)`),
    ['H3', 'H4', 'H5', 'H6', 'P', 'BLOCKQUOTE', 'HR', 'UL', 'OL', 'TABLE', 'PRE', 'PRE', 'P'], 'the blocks are in the order of the text');

  // 2. the attack is text
  assert.equal(await b.ev(`typeof window.__pwned`), 'undefined', 'no script ran');
  assert.equal(await count(ans + ' script, ' + ans + ' img, ' + ans + ' iframe'), 0);
  assert.equal(await count(ans + ' [href^="javascript"], ' + ans + ' [onerror]'), 0);
  const txt = await b.ev(`${Q(ans)}.textContent`);
  assert.ok(txt.includes('<script>window.__pwned = 1</script>') && txt.includes('<img src=x onerror="window.__pwned = 2">') && txt.includes('[evil](javascript:window.__pwned=3) done'), 'the attack is shown as text');

  // screenshots: dark, light, phone
  await b.ev(`document.documentElement.setAttribute('data-theme', 'dark')`); await b.shot('markdown-dark');
  await b.ev(`document.documentElement.setAttribute('data-theme', 'light')`); await b.shot('markdown-light');
  await b.size(390, 900, true); await b.shot('markdown-phone');
  assert.equal(await b.ev(`document.documentElement.scrollWidth <= window.innerWidth`), true, 'no horizontal scroll at phone width');
  await b.size(1000, 900);

  // 3. Copy: the browser's writeText is stubbed to record the text
  await b.ev(`window.__copied = []; Object.defineProperty(navigator, 'clipboard', { value: { writeText: (t) => { window.__copied.push(t); return Promise.resolve(); } }, configurable: true })`);
  await b.ev(`document.querySelectorAll('${ans} .md-copy')[0].dispatchEvent(new MouseEvent('click', {bubbles: true}))`);
  await b.until(`window.__copied.length === 1`, 'the clipboard got the text');
  assert.deepEqual(await b.ev(`window.__copied`), ['def hello():\n    return "world"']);
  assert.equal(await b.ev(`document.querySelectorAll('${ans} .md-copy')[0].textContent`), 'Copied');
  assert.equal(await b.ev(`document.querySelectorAll('${ans} .md-copy')[1].textContent`), 'Copy', 'only that block');

  // 4. Stop
  assert.equal(await b.ev(`${Q(K('DomNode:chat:stop'))}.hidden`), true, 'idle: no Stop');
  await b.type('hang'); await b.enter();
  await b.until(`!${Q(K('DomNode:chat:stop'))}.hidden`, 'Stop shows while the turn runs');
  const pid = Number(await (async () => { for (let i = 0; i < 100; i++) { try { return readFileSync(path.join(seat.dir, 'pid.hang'), 'utf8'); } catch { await sleep(100); } } })());
  const alive = (p) => { try { process.kill(p, 0); return true; } catch { return false; } };
  assert.equal(alive(pid), true, 'the child runs');
  await b.shot('stop-visible');
  const stopPosts0 = b.calls.filter((c) => /POST \/membrane\/chat\/message\/.*\/abort/.test(c)).length;
  await b.ev(click(K('DomNode:chat:stop')));
  await b.until(`${Q(K('DomNode:chat:stop'))}.hidden`, 'Stop goes when the turn has ended', 30000);
  assert.equal(b.calls.filter((c) => /POST \/membrane\/chat\/message\/.*\/abort/.test(c)).length - stopPosts0, 1, 'one stop');
  const last = await b.ev(`[...document.querySelectorAll('[data-key$=":ans"]')].pop().textContent`);
  assert.ok(last.includes('starting the work') && last.includes('(stopped from the page)'), `the answer so far is kept and says stopped: ${last}`);
  assert.equal(await b.ev(`[...document.querySelectorAll('[data-key$=":ans"]')].pop().className`), 'bubble note');
  for (let i = 0; i < 50 && alive(pid); i++) await sleep(100);
  assert.equal(alive(pid), false, 'the child process is gone');
  await b.shot('stopped');

  // 5. the tool rows
  await b.type('tools'); await b.enter();
  await b.until(`[...document.querySelectorAll('details.tools-run')].some((d) => !d.hidden)`, 'a turn with tools shows its row');
  assert.equal(await b.ev(`[...document.querySelectorAll('details.tools-run')].filter((d) => !d.hidden).map((d) => d.querySelector('summary').textContent)`).then((x) => x.join('|')), '2 tools');
  assert.deepEqual(await b.ev(`[...document.querySelectorAll('details.tools-run:not([hidden]) li')].map((l) => l.textContent.trim())`), ['entity_read {"spec":"Command:probe"}', 'Bash ls -la /tmp']);
  assert.equal(await b.ev(`document.querySelector('details.tools-run:not([hidden])').open`), false, 'collapsed by default');
  await b.shot('tools');

  assert.deepEqual(b.errors, [], 'no script error in the page');
  console.log('markdown page: ok');
} finally { await b.close(); seat.stop(); }
