// The chat page's send box (membrane/apps/barest.c1f/ui/chat.js) against a seat that answers LATE: two sends in a row (an Enter and a click, or two Enters) make ONE POST,
// the input is disabled while the POST is out, and the guard is cleared when the POST answers or fails (the next send goes out). A fake DOM and a fake fetch, no browser.
import { mkdtempSync, copyFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const d = mkdtempSync(path.join(tmpdir(), 'chat-submit-'));
copyFileSync(path.join(repo, 'membrane/apps/barest.c1f/ui/chat.js'), path.join(d, 'chat.mjs'));
writeFileSync(path.join(d, 'package.json'), '{"type":"module"}');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const posts = [];
let answerPost = null;   // the pending POST's resolver: the seat answers when the test says so
globalThis.fetch = (url, opts) => {
  const u = new URL(String(url), 'http://x');
  if (u.pathname === '/membrane/chat/messages') return Promise.resolve({ ok: true, status: 200, json: async () => ({ messages: [], more: false, next: null }) });
  if (u.pathname === '/membrane/chat/message' && opts && opts.method === 'POST') {
    posts.push(JSON.parse(opts.body).text);
    return new Promise((res) => { answerPost = (r) => res(r); });
  }
  return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
};
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.parent = null; this.dataset = {}; this.className = ''; this.listeners = {}; this._text = ''; this.scrollTop = 0; this.scrollHeight = 0; this.value = ''; this.disabled = false; this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } }; this.style = {}; }
  get nextSibling() { if (!this.parent) return null; const c = this.parent.children; return c[c.indexOf(this) + 1] || null; }
  get firstChild() { return this.children[0] || null; }
  set textContent(t) { this._text = t; } get textContent() { return this._text; }
  adopt(c) { for (const x of c) { x.remove && x.remove(); x.parent = this; } }
  append(...c) { this.adopt(c); this.children.push(...c); }
  prepend(...c) { this.adopt(c); this.children.unshift(...c); }
  insertBefore(n, ref) { this.adopt([n]); const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(n); else this.children.splice(i, 0, n); }
  replaceChildren(...c) { for (const x of this.children) x.parent = null; this.children = []; this.append(...c); }
  addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); }
  setAttribute() {}
  remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } }
  focus() {}
}
globalThis.document = { hidden: false, createElement: (t) => new El(t) };
try {
  const { startChat } = await import(path.join(d, 'chat.mjs'));
  const ui = { log: new El('ol'), form: new El('form'), input: new El('textarea'), send: new El('button') };
  startChat(ui);
  await sleep(100);
  const enter = () => ui.input.listeners.keydown[0]({ key: 'Enter', shiftKey: false, isComposing: false, preventDefault() {} });
  const click = () => ui.form.listeners.submit[0]({ preventDefault() {} });

  ui.input.value = 'hello';
  enter(); click(); enter(); click();
  await sleep(50);
  assert.deepEqual(posts, ['hello'], 'a slow seat gets ONE send for an Enter and three more presses');
  assert.equal(ui.input.disabled, true, 'the input is disabled while the POST is out');
  assert.equal(ui.send.disabled, true, 'the send button is disabled while the POST is out');
  answerPost({ ok: true, status: 200, json: async () => ({ id: '1-1' }) });
  await sleep(50);
  assert.equal(ui.input.disabled, false, 'the input is enabled when the POST answered');
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.input.value, '', 'a sent message leaves the box');

  ui.input.value = 'again';
  click(); click();
  await sleep(20);
  assert.deepEqual(posts, ['hello', 'again'], 'the next send goes out once the guard is cleared');
  answerPost({ ok: false, status: 503, json: async () => ({ error: 'busy' }) });
  await sleep(50);
  assert.equal(ui.input.value, 'again', 'a failed send stays in the box');
  assert.equal(ui.input.disabled, false, 'a failed POST clears the guard and the disabled input');
  click();
  await sleep(20);
  assert.deepEqual(posts, ['hello', 'again', 'again'], 'after a failure the person can send again');
  answerPost({ ok: true, status: 200, json: async () => ({ id: '1-2' }) });
  await sleep(30);
  console.log('chat submit: ok (one POST per slow send, the guard clears on answer and on failure)');
  process.exit(0);
} finally { rmSync(d, { recursive: true, force: true }); }
