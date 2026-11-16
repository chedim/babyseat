// W3 (#1143): a page `Fetch` with `request.stream = true` reads the body as a stream and writes each SSE frame as `Chunk:<fetch>:<n>`
// ({fetch, id, n, data, done}: the seat's Fetch Chunk, native/src/fetch.rs), then the receipt. A fake fetch with a ReadableStream, node, no browser.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { start } from '../../membrane/apps/barest.c1f/ui/fieldvm.js';

const ui = new URL('../../membrane/apps/barest.c1f/ui/', import.meta.url);
const wasm = readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui)));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ORIGIN = 'http://seat.test';
const enc = new TextEncoder();

/** A fake fetch: each call waits for the test, which then answers with a stream it feeds by hand. */
async function world() {
  const calls = [];
  globalThis.fetch = (url, init = {}) => new Promise((resolve, reject) => {
    calls.push({ url: String(url), init, resolve, reject });
    if (init.signal) init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
  });
  const host = await start(null, { wasm });
  host.schedule = (fn) => setTimeout(fn, 0);
  host.origin = ORIGIN;
  return { host, calls };
}
/** Answer a call with a streaming body; returns {push(bytes|string), end(), fail(err)}. A signal abort errors the stream, as a real fetch does. */
function answer(call, status = 200) {
  let ctl;
  const body = new ReadableStream({ start(c) { ctl = c; } });
  if (call.init.signal) call.init.signal.addEventListener('abort', () => { try { ctl.error(Object.assign(new Error('aborted'), { name: 'AbortError' })); } catch {} });
  call.resolve({ status, statusText: 'OK', ok: true, headers: new Map([['Content-Type', 'text/event-stream']]), body,
    text: async () => { throw new Error('the stream is not read as text'); } });
  return { push: (b) => ctl.enqueue(typeof b === 'string' ? enc.encode(b) : b), end: () => ctl.close(), fail: (e) => ctl.error(e) };
}
const put = (host, eid, req) => { if (host.f.get(eid) === 'null') host.f.load(eid, 'Fetch', '{}'); host.write(eid, 'request', req); };
const rec = (host, id = 'a') => host.get(`Fetch:${id}`).request_result;
const chunk = (host, id, n) => { const c = host.get(`Chunk:Fetch:${id}:${n}`); return c && c.n !== undefined ? c : null; };
const datas = (host, id) => { const out = []; for (let n = 1; chunk(host, id, n); n++) out.push(chunk(host, id, n)); return out; };

// 1. frames become Chunks in order, in the seat's shape; then a done Chunk and the receipt (no body)
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's1', url: '/membrane/chat/message/1/events', stream: true });
  await sleep(5);
  assert.equal(calls.length, 1);
  const s = answer(calls[0]);
  s.push('event: message\ndata: {"a":1}\n\n'); await sleep(5);
  assert.deepEqual(chunk(host, 'a', 1), { fetch: 'Fetch:a', id: 's1', n: 1, data: '{"a":1}', done: false });
  assert.equal(rec(host), undefined, 'no receipt while the stream is open');
  s.push('data: two\n\ndata: l1\ndata: l2\n\n'); await sleep(5);
  assert.equal(chunk(host, 'a', 2).data, 'two'); assert.equal(chunk(host, 'a', 3).data, 'l1\nl2', 'multi-line data joined with a newline');
  s.end(); await sleep(10);
  const c = datas(host, 'a');
  assert.equal(c.length, 4); assert.equal(c[3].done, true); assert.equal(c[3].data, ''); assert.equal(c[3].n, 4, 'the done Chunk is n+1, as the seat');
  const r = rec(host);
  assert.equal(r.status, 'ok'); assert.equal(r.rc, 0); assert.equal(r.http_status, 200); assert.equal(r.id, 's1');
  assert.equal('body' in r, false, 'the body is not repeated'); assert.equal('note' in r, false);
  assert.ok(Number.isInteger(r.ms) && r.started > 1e9);
}

// 2. a frame split between two reads is reassembled (also a split across the blank-line boundary)
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's2', url: '/ev', stream: true });
  await sleep(5);
  const s = answer(calls[0]);
  s.push('data: hel'); await sleep(5);
  assert.equal(chunk(host, 'a', 1), null, 'half a frame is not a Chunk');
  s.push('lo\n'); await sleep(5);
  assert.equal(chunk(host, 'a', 1), null, 'one newline is not the boundary');
  s.push('\ndata: next\n\n'); await sleep(5);
  assert.equal(chunk(host, 'a', 1).data, 'hello'); assert.equal(chunk(host, 'a', 2).data, 'next');
  s.end(); await sleep(5);
}

// 3. a multi-byte character split between two reads is not broken
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's3', url: '/ev', stream: true });
  await sleep(5);
  const s = answer(calls[0]);
  const bytes = enc.encode('data: hé€\u{1F600}\n\n');   // 2-, 3-, 4-byte characters
  for (let i = 0; i < bytes.length; i += 1) { s.push(bytes.slice(i, i + 1)); }   // one byte per read: every character is split
  await sleep(10);
  assert.equal(chunk(host, 'a', 1).data, 'hé€\u{1F600}');
  s.end(); await sleep(5);
}

// 4. an empty frame, a comment line and a frame with no data are ignored; CRLF boundaries work
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's4', url: '/ev', stream: true });
  await sleep(5);
  const s = answer(calls[0]);
  s.push(': ping\n\n\n\nevent: x\n\ndata: kept\r\n\r\n: c\ndata: also\n\n'); await sleep(5);
  s.end(); await sleep(10);
  const c = datas(host, 'a');
  assert.deepEqual(c.map((x) => x.data), ['kept', 'also', ''], 'only data frames, then done');
  assert.deepEqual(c.map((x) => x.n), [1, 2, 3], 'numbering has no gaps');
}

// 5. the unterminated tail at the end of the stream is not a frame (SSE: an incomplete frame is dropped)
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's5', url: '/ev', stream: true });
  await sleep(5);
  const s = answer(calls[0]);
  s.push('data: whole\n\ndata: cut'); s.end(); await sleep(10);
  assert.deepEqual(datas(host, 'a').map((x) => x.data), ['whole', '']);
  assert.equal(rec(host).status, 'ok');
}

// 6. abort = true ends the stream; the receipt says aborted; the done Chunk carries the reason
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's6', url: '/ev', stream: true });
  await sleep(5);
  const s = answer(calls[0]);
  s.push('data: one\n\n'); await sleep(5);
  host.write('Fetch:a', 'abort', true); await sleep(10);
  assert.equal(calls[0].init.signal.aborted, true);
  const r = rec(host);
  assert.equal(r.status, 'error'); assert.match(r.head, /aborted/);
  const c = datas(host, 'a');
  assert.deepEqual(c.map((x) => x.data), ['one', '']); assert.equal(c[1].done, true); assert.match(c[1].error, /aborted/);
}

// 7. a failure mid-stream: a failure receipt (status error), and a done Chunk with the error, as the seat's end_failed_stream
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's7', url: '/ev', stream: true });
  await sleep(5);
  const s = answer(calls[0]);
  s.push('data: one\n\n'); await sleep(5);
  s.fail(new TypeError('network error')); await sleep(10);
  const r = rec(host);
  assert.equal(r.status, 'error'); assert.equal(r.rc, 1); assert.match(r.head, /network error/);
  const c = datas(host, 'a');
  assert.equal(c.length, 2); assert.equal(c[1].done, true); assert.match(c[1].error, /network error/);
}

// 8. two streams at once: each has its own numbering, Chunks and receipt
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'A', url: '/a', stream: true });
  put(host, 'Fetch:b', { id: 'B', url: '/b', stream: true });
  await sleep(5);
  const sa = answer(calls[0]), sb = answer(calls[1]);
  sa.push('data: a1\n\n'); sb.push('data: b1\n\ndata: b2\n\n'); sa.push('data: a2\n\n'); await sleep(5);
  sb.end(); await sleep(10);
  assert.deepEqual(datas(host, 'a').map((x) => x.data), ['a1', 'a2']);
  assert.deepEqual(datas(host, 'b').map((x) => x.data), ['b1', 'b2', '']);
  assert.equal(rec(host, 'b').status, 'ok'); assert.equal(rec(host, 'a'), undefined, 'the other is still open');
  sa.end(); await sleep(10);
  assert.equal(rec(host, 'a').status, 'ok'); assert.equal(datas(host, 'a').length, 3);
}

// 9. the same-origin guard still applies to a stream: no fetch, a refusal receipt, no Chunk
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 's9', url: 'http://evil.test/ev', stream: true });
  await sleep(5);
  assert.equal(calls.length, 0); assert.equal(rec(host).rc, 2); assert.match(rec(host).head, /same origin/);
  assert.equal(chunk(host, 'a', 1), null);
}

// 10. Chunks are page-local: what the seat mirrored is never fetched, and a Chunk's own write starts no fetch; the count stays observable
{
  const { host, calls } = await world();
  host.feed('Fetch:seat', 'Fetch', { request: { id: 'm', url: '/ev', stream: true } }, true);
  await sleep(5);
  assert.equal(calls.length, 0, 'a mirrored stream request is not made');
  put(host, 'Fetch:a', { id: 's10', url: '/ev', stream: true });
  await sleep(5);
  const s = answer(calls[0]);
  for (let i = 0; i < 50; i++) s.push(`data: ${i}\n\n`);
  await sleep(20); s.end(); await sleep(10);
  assert.equal(calls.length, 1, 'chunks started no fetch');
  assert.equal(datas(host, 'a').length, 51, 'all chunks kept (retention is a decision)');
}
console.log('fieldvm stream: ok');
