// W2 (#1143): the page's `Fetch` entity. A pack writes `Fetch:<id>.request = {id, url, method, headers, body}`, the JS shell makes ONE fetch()
// (same origin only, the owner cookie goes with it) and writes the receipt back in the seat's shape (native/src/drivers.rs, fetch.rs). A fake fetch, node, no browser.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { start, FETCH_PACK } from '../../membrane/ui/fieldvm.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
const wasm = readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui)));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ORIGIN = 'http://seat.test';

/** A fake fetch: every call is recorded and waits for the test to answer it (or to abort it). */
function fake() {
  const calls = [];
  const fn = (url, init = {}) => new Promise((resolve, reject) => {
    const c = { url: String(url), init, resolve, reject };
    calls.push(c);
    if (init.signal) init.signal.addEventListener('abort', () => reject(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' })));
  });
  return { calls, fn };
}
const reply = (status, text, statusText = '', headers = {}) => ({ status, statusText, ok: status >= 200 && status < 300,
  headers: new Map(Object.entries(headers)), text: async () => text });

async function world() {
  const f = fake();
  globalThis.fetch = f.fn;
  const host = await start(null, { wasm });
  host.schedule = (fn) => setTimeout(fn, 0);
  host.origin = ORIGIN;
  return { host, ...f };
}
/** An entity the pack made (a write to one that does not exist is dropped by the engine). */
const put = (host, eid, req) => { if (host.f.get(eid) === 'null') host.f.load(eid, 'Fetch', '{}'); host.write(eid, 'request', req); };
const rec = (host, id = 'a') => host.get(`Fetch:${id}`).request_result;

// the seat's receipt keys on a good answer (drivers.rs: effect() adds started/finished/ms to fetch.rs's {id,status,rc,head,http_status,headers,body})
const SEAT_KEYS = ['body', 'finished', 'head', 'headers', 'http_status', 'id', 'ms', 'rc', 'started', 'status'];

// 0. the page's Fetch class is the seat's pack (membrane/packs/fetch.c1f), so a pack can import it
assert.equal(FETCH_PACK, readFileSync(fileURLToPath(new URL('../../membrane/packs/fetch.c1f', import.meta.url)), 'utf8'), 'FETCH_PACK is fetch.c1f');

// 1. a request becomes ONE fetch call with the right url, method, body; the receipt has the seat's shape
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'r1', url: '/membrane/chat/message', method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"text":"hi"}' });
  await sleep(5);
  assert.equal(calls.length, 1, 'one fetch');
  assert.equal(calls[0].url, `${ORIGIN}/membrane/chat/message`);
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.body, '{"text":"hi"}');
  assert.equal(calls[0].init.credentials, 'same-origin', 'the owner cookie goes with it');
  assert.deepEqual(calls[0].init.headers, { 'content-type': 'application/json' });
  assert.equal(rec(host), undefined, 'no receipt before the answer');
  calls[0].resolve(reply(201, '{"id":"1-1"}', 'Created', { 'Content-Type': 'application/json' }));
  await sleep(5);
  const r = rec(host);
  assert.deepEqual(Object.keys(r).sort(), SEAT_KEYS, 'the seat\'s key names');
  assert.equal(r.id, 'r1'); assert.equal(r.status, 'ok'); assert.equal(r.rc, 0);
  assert.equal(r.http_status, 201); assert.equal(r.body, '{"id":"1-1"}');
  assert.equal(r.headers['content-type'], 'application/json', 'header names lower-case, as the seat');
  assert.ok(r.head.includes('201'));
  assert.ok(Number.isInteger(r.ms) && r.ms >= 0);
  assert.ok(r.started > 1e9 && r.finished >= r.started, 'epoch seconds');
}

// 1b. a GET with no method; a pack's setter can write the request (the view pack's way)
{
  const { host, calls } = await world();
  const PACK = "PACK q 1\nIMPORT fetch 1\nThing.go on-setter ask:\n  ALWAYS:\n    ('Fetch:q').request = {'id': 'g1', 'url': '/membrane/chat/messages?limit=5'}\n";
  assert.equal(host.pack('q', PACK), null);
  host.feed('Thing:1', 'Thing', { go: true });
  await sleep(5);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.method, 'GET', 'GET when no method');
  assert.equal(calls[0].url, `${ORIGIN}/membrane/chat/messages?limit=5`);
  calls[0].resolve(reply(200, '{"messages":[]}'));
  await sleep(5);
  assert.equal(rec(host, 'q').body, '{"messages":[]}');
}

// 2. cross-origin refused with a visible reason, no fetch
for (const url of ['http://evil.test/x', 'https://seat.test/x', '//evil.test/x', 'http://seat.test:81/x']) {
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'x', url });
  await sleep(5);
  assert.equal(calls.length, 0, `no fetch for ${url}`);
  const r = rec(host);
  assert.equal(r.status, 'error'); assert.equal(r.rc, 2);
  assert.match(r.head, /^refused: .*same origin/, 'a visible reason');
  assert.equal(r.id, 'x');
}

// 2b. verbs and headers outside the allow-list are refused with a reason; authorization and cookie never go
for (const [req, why] of [[{ method: 'PUT' }, /method/], [{ headers: { authorization: 'x' } }, /authorization/], [{ headers: { Cookie: 'a=b' } }, /cookie/i], [{ headers: { 'x-other': '1' } }, /x-other/]]) {
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'x', url: '/a', ...req });
  await sleep(5);
  assert.equal(calls.length, 0);
  assert.equal(rec(host).rc, 2); assert.match(rec(host).head, why);
}
{ // allowed: all four verbs, accept + content-type (any case)
  const { host, calls } = await world();
  for (const m of ['GET', 'POST', 'PATCH', 'DELETE']) put(host, `Fetch:${m}`, { id: m, url: '/a', method: m.toLowerCase(), headers: { Accept: 'text/plain' } });
  await sleep(5);
  assert.deepEqual(calls.map(c => c.init.method), ['GET', 'POST', 'PATCH', 'DELETE']);
  assert.deepEqual(calls[0].init.headers, { accept: 'text/plain' });
}

// 3. a non-2xx is a NORMAL result with its status
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'n', url: '/missing' });
  await sleep(5);
  calls[0].resolve(reply(404, 'nope', 'Not Found'));
  await sleep(5);
  const r = rec(host);
  assert.equal(r.status, 'ok'); assert.equal(r.rc, 0); assert.equal(r.http_status, 404); assert.equal(r.body, 'nope');
}

// 4. a network failure carries its reason (the seat's failure receipt: status error, rc 1, head = reason; started/finished/ms too)
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'f', url: '/down' });
  await sleep(5);
  calls[0].reject(new TypeError('Failed to fetch'));
  await sleep(5);
  const r = rec(host);
  assert.equal(r.status, 'error'); assert.equal(r.rc, 1); assert.equal(r.id, 'f');
  assert.match(r.head, /Failed to fetch/);
  assert.equal(r.http_status, undefined, 'no http status for a failure');
  assert.ok('started' in r && 'finished' in r && 'ms' in r);
}

// 5. abort: `abort = true` aborts the request in flight, the receipt says so
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'ab', url: '/slow' });
  await sleep(5);
  assert.equal(calls[0].init.signal.aborted, false);
  host.write('Fetch:a', 'abort', true);
  await sleep(5);
  assert.equal(calls[0].init.signal.aborted, true);
  const r = rec(host);
  assert.equal(r.status, 'error'); assert.equal(r.rc, 1); assert.match(r.head, /aborted/); assert.equal(r.id, 'ab');
  host.write('Fetch:zz', 'abort', true);   // nothing in flight: nothing happens
  await sleep(5);
  assert.equal(host.get('Fetch:zz'), null);
}

// 5b. timeout_s aborts; with no timeout_s there is no page-side timeout
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 't', url: '/slow', timeout_s: 0.02 });
  put(host, 'Fetch:b', { id: 'u', url: '/slow' });
  await sleep(80);
  assert.equal(calls[0].init.signal.aborted, true);
  assert.match(rec(host).head, /timeout/);
  assert.equal(calls[1].init.signal.aborted, false, 'no timeout_s, no page-side timeout');
  assert.equal(rec(host, 'b'), undefined);
}

// 6. two concurrent fetches are independent (each entity its own receipt, answered in any order)
{
  const { host, calls } = await world();
  put(host, 'Fetch:a', { id: 'one', url: '/one' });
  put(host, 'Fetch:b', { id: 'two', url: '/two' });
  await sleep(5);
  assert.equal(calls.length, 2);
  calls[1].resolve(reply(200, 'two'));
  await sleep(5);
  assert.equal(rec(host, 'b').body, 'two'); assert.equal(rec(host, 'a'), undefined, 'the other is still out');
  calls[0].reject(new TypeError('boom'));
  await sleep(5);
  assert.equal(rec(host, 'a').status, 'error'); assert.equal(rec(host, 'b').status, 'ok', 'a failure of one leaves the other');
}

// 7. no double fetch: the same request written again (same id) is not made twice, in flight or after the answer; a new id is a new fetch
{
  const { host, calls } = await world();
  const req = { id: 'd', url: '/once' };
  put(host, 'Fetch:a', req);
  put(host, 'Fetch:a', { ...req });
  await sleep(5);
  assert.equal(calls.length, 1, 'in flight');
  calls[0].resolve(reply(200, 'x'));
  await sleep(5);
  put(host, 'Fetch:a', { ...req });
  await sleep(5);
  assert.equal(calls.length, 1, 'after the answer');
  put(host, 'Fetch:a', { id: 'd2', url: '/once' });
  await sleep(5);
  assert.equal(calls.length, 2, 'a new id is a new request');
}

// 8. (W3) a stream request is read as a stream: see fieldvm_stream.test.mjs

// 9. a body that is not text is refused (text only for now); a request with no url is refused
for (const req of [{ id: 'b', url: '/a', method: 'POST', body: { a: 1 } }, { id: 'c' }]) {
  const { host, calls } = await world();
  put(host, 'Fetch:a', req);
  await sleep(5);
  assert.equal(calls.length, 0); assert.equal(rec(host).rc, 2);
}
// 10. what the SEAT did (its snapshot and events, fed as a mirror) is never fetched again by the page
{
  const { host, calls } = await world();
  host.feed('Fetch:seat', 'Fetch', { request: { id: 'm', url: '/ui/fieldvm' } }, true);
  await sleep(5);
  assert.equal(calls.length, 0);
  assert.equal(host.get('Fetch:seat').request.id, 'm', 'the mirror still holds it');
}
console.log('fieldvm fetch: ok');
