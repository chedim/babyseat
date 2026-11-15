// THE SIGN-IN SCREEN (membrane/apps/barest.c1f/login_view.c1f, #1143) on the wasm engine in Node. The test plays the HOST (a DomNode event is a write; a `Fetch:<x>.request`
// is answered by a write of `request_result`) as ui/fieldvm.js does. Asserted: the card (name, labelled password box, button, help line), typing, submit (Enter = the form's
// submit), the busy state, the token in the request only and cleared from the box and the entity, success asks the host to reload, a wrong token shows the seat's reason and
// the page stays, no answer says 'The seat did not answer', an empty box sends nothing, the shell's Sign out (only when /ui/conf says who).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/apps/barest.c1f/ui/fieldvm_core.js';

const ui = new URL('../../membrane/apps/barest.c1f/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');
const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";
const FETCH = read('../../membrane/packs/fetch.c1f');
const LOGIN = read('../../membrane/apps/barest.c1f/login_view.c1f');
const T0 = 1_000_000;
let n = 0;
const scenario = (name, fn) => { fn(); n++; console.log('  ok', name); };

function world(packs) {
  const f = new Field();
  f.set_pack('dom', DOM); f.set_pack('fetch', FETCH);
  for (const [k, s] of Object.entries(packs)) f.set_pack(k, s);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'the packs load');
  const w = { f, t: T0, log: [] };
  w.flush = () => { const all = []; for (;;) { const o = JSON.parse(f.drain()); w.log.push(...o.writes); all.push(...o.writes); const gone = o.writes.filter((x) => x.prop === 'retire' && x.new === true); for (const x of gone) f.remove(x.eid, w.t); if (!gone.length) return all; } };
  w.get = (e) => JSON.parse(f.get(e));
  w.write = (e, p, v) => { f.write(e, p, JSON.stringify(v ?? null), w.t); return w.flush(); };
  w.feed = (e, cls, props) => { if (f.get(e) === 'null') f.load(e, cls, '{}'); for (const [k, v] of Object.entries(props)) f.write(e, k, JSON.stringify(v ?? null), w.t); w.flush(); };
  w.asked = (x) => (w.get('Fetch:' + x) || {}).request;
  w.reply = (x, status, body) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'ok', rc: 0, head: String(status), http_status: status, headers: {}, body: typeof body === 'string' ? body : JSON.stringify(body), started: w.t, finished: w.t, ms: 1 });
  w.fail = (x, why) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'error', rc: 1, head: why, started: w.t, finished: w.t, ms: 1 });
  w.n = (k) => w.get('DomNode:login:' + k) || {};
  w.type = (s) => w.write('DomNode:login:input', 'value', s);
  w.submit = () => w.write('DomNode:login:form', 'submitted', w.t);
  return w;
}
const login = () => { const w = world({ login_view: LOGIN }); w.feed('Login:main', 'Login', { open: true }); return w; };
const WRONG = { error: { message: 'missing or invalid token', type: 'unauthorized' } };

scenario('the card: the product name, a labelled password box, a Sign in button, a help line, an alert region, focus on the box', () => {
  const w = login();
  assert.equal(w.n('root').class, 'login'); assert.equal(w.n('form').tag, 'form'); assert.equal(w.n('title').text, 'babyseat');
  assert.equal(w.n('label').tag, 'label'); assert.equal(w.n('labeltext').text, 'Access token'); assert.equal(w.n('labeltext').parent, 'DomNode:login:label'); assert.equal(w.n('input').parent, 'DomNode:login:label');
  const i = w.n('input');
  assert.equal(i.tag, 'input'); assert.equal(i.type, 'password'); assert.equal(i.name, 'token'); assert.equal(i['aria-label'], 'Access token');
  assert.equal(i.autocomplete, 'off'); assert.equal(i.spellcheck, 'false'); assert.equal(i.inputmode, 'none'); assert.equal(i.focused, true);
  assert.equal(w.n('send').tag, 'button'); assert.equal(w.n('send').type, 'submit'); assert.equal(w.n('send').text, 'Sign in');
  assert.match(w.n('help').text, /admin token printed when the seat started.*admin\.token.*data folder.*cliffvm token mint/);
  assert.equal(w.n('err').role, 'alert'); assert.equal(w.n('err')['aria-live'], 'assertive'); assert.equal(w.n('err').text, '');
});

scenario('submit: one POST /ui/login {"token"} as JSON; the box is blanked and the entity forgets the token; busy while it runs', () => {
  const w = login();
  w.type('  s3cret"tok  '); w.submit();
  const r = w.asked('login');
  assert.equal(r.url, '/ui/login'); assert.equal(r.method, 'POST'); assert.equal(r.headers['content-type'], 'application/json');
  assert.deepEqual(JSON.parse(r.body), { token: 's3cret"tok' }, 'the token as typed (trimmed), valid JSON even with a quote');
  assert.equal(w.n('input').value, ''); assert.ok(w.get('Login:main').token == null, 'the page keeps no copy');
  assert.equal(w.n('input').disabled, true); assert.equal(w.n('send').disabled, true); assert.equal(w.n('send').text, 'Signing in…'); assert.equal(w.n('form')['aria-busy'], 'true');
  w.type('again'); w.submit();                                       // a second Enter while busy sends nothing
  assert.equal(w.asked('login').id, r.id);
  const seen = w.log.filter((e) => e.eid.startsWith('DomNode:') && String(JSON.stringify(e.new)).includes('s3cret'));
  assert.deepEqual(seen, [], 'the token never lands on a DomNode (the DOM)');
});

scenario('success: the seat answered 2xx, so the page is asked to reload; the request (and the token in it) is cleared', () => {
  const w = login(); w.type('good'); w.submit();
  w.reply('login', 200, { ok: true, who: { sub: 'owner', name: 'owner' } });
  assert.equal(w.n('root').reload, true, 'the host reloads the page: the cookie is the seat\'s');
  assert.ok(w.asked('login') == null, 'no token left in the request');
  assert.equal(w.n('err').text, ''); assert.equal(w.get('Login:main').busy, false);
});

scenario('a wrong token: the seat\'s own reason beside the form, the page stays, the box is empty and usable again', () => {
  const w = login(); w.type('bad'); w.submit();
  w.reply('login', 401, WRONG);
  assert.equal(w.n('err').text, 'missing or invalid token');
  assert.notEqual(w.n('root').reload, true, 'no reload');
  assert.equal(w.n('input').disabled, false); assert.equal(w.n('send').text, 'Sign in'); assert.equal(w.n('input').value, ''); assert.equal(w.n('input').focused, true);
  assert.ok(w.asked('login') == null, 'the wrong token is not kept either');
  w.type('good'); w.submit();                                        // a second try is a new request, and clears the reason while it runs
  assert.equal(w.n('err').text, ''); assert.equal(w.asked('login').id, 'login-2');
});

scenario('no answer: "The seat did not answer"; an answer with no reason names the status', () => {
  const w = login(); w.type('x'); w.submit(); w.fail('login', 'Failed to fetch');
  assert.equal(w.n('err').text, 'The seat did not answer'); assert.equal(w.get('Login:main').busy, false);
  w.type('y'); w.submit(); w.reply('login', 415, 'nope');
  assert.equal(w.n('err').text, 'the seat answered 415');
});

scenario('an empty box sends nothing and says what is missing', () => {
  const w = login(); w.submit();
  assert.ok(w.asked('login') == null); assert.equal(w.n('err').text, 'Enter the access token first');
  w.type('   '); w.submit(); assert.ok(w.asked('login') == null);
});

scenario('the shell: Sign out is shown only when /ui/conf says who; a click POSTs /ui/logout then reloads; a refusal is shown as text', () => {
  const SHELL = { shell_view: read('../../membrane/apps/barest.c1f/shell_view.c1f') };
  const boot = (who) => { const w = world(SHELL); w.feed('Shell:main', 'Shell', { view: 'chat' }); w.feed('Shell:main', 'Shell', { open: true }); assert.equal(w.asked('conf').url, '/ui/conf'); w.reply('conf', 200, { build: 'x', who }); return w; };
  let w = boot(null);
  assert.equal(w.get('DomNode:shell:signout').hidden, true, 'a seat with no auth: no button');
  w = boot({ sub: 'owner', name: 'owner' });
  assert.equal(w.get('DomNode:shell:signout').hidden, false); assert.equal(w.get('DomNode:shell:signout').text, 'Sign out'); assert.equal(w.get('DomNode:shell:signout').parent, 'DomNode:shell:bar');
  w.write('DomNode:shell:signout', 'clicked', w.t);
  const r = w.asked('logout'); assert.equal(r.url, '/ui/logout'); assert.equal(r.method, 'POST');
  w.reply('logout', 200, { ok: true });
  assert.equal(w.get('DomNode:shell:root').reload, true);
  w = boot({ sub: 'owner' }); w.write('DomNode:shell:signout', 'clicked', w.t); w.reply('logout', 500, { error: { message: 'boom' } });
  assert.notEqual(w.get('DomNode:shell:root').reload, true); assert.match(w.get('DomNode:shell:error').text, /Sign out did not work: boom/); assert.equal(w.get('DomNode:shell:error').hidden, false);
});
console.log(`login_view: ${n} scenarios ok`);
