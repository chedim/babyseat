// The barest page's SHELL as a c1f view pack (membrane/apps/barest.c1f/shell_view.c1f, #1143), with the chat and field views plugged into it, on the wasm engine in Node:
// the page's VM with the packs `dom`, `fetch`, `shell_view`, `chat_view`, `field_view`. The test plays the HOST (a DomNode event is a write; a `Fetch:<x>.request` is answered by a
// write of `request_result`; a `retire` write removes the entity) and the CLOCK (`tick`), as ui/boot.js and fieldvm.js do. Asserted: the page's skeleton, the nav (Chat, Field, Settings),
// the Settings placeholder and the view-pack contract (a stub pack plugs into the slot), the status line, errors as text (a 403 with its
// reason), the theme, and the work per tick (a bound on the writes of an idle page).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { initSync, Field } from '../../membrane/ui/fieldvm_core.js';

const ui = new URL('../../membrane/ui/', import.meta.url);
initSync({ module: readFileSync(fileURLToPath(new URL('fieldvm_core_bg.wasm', ui))) });
const read = (p) => readFileSync(fileURLToPath(new URL(p, import.meta.url)), 'utf8');
const DOM = "PACK dom 1\nDomNode.tag getter: this.tag or 'div'\n";
const FETCH = read('../../membrane/packs/fetch.c1f');
const PACKS = { shell_view: read('../../membrane/apps/barest.c1f/shell_view.c1f'), chat_view: read('../../membrane/apps/barest.c1f/chat_view.c1f'), field_view: read('../../membrane/apps/field.c1f/field_view.c1f') };

const T0 = 1_000_000;
function world(extra = {}) {
  const f = new Field();
  f.set_pack('dom', DOM); f.set_pack('fetch', FETCH);
  for (const [n, s] of Object.entries({ ...PACKS, ...extra })) f.set_pack(n, s);
  assert.deepEqual(JSON.parse(f.pack_errors()), {}, 'the packs load');
  const w = { f, t: T0, log: [] };
  w.flush = () => {
    const all = [];
    for (;;) {                                                           // the host's rule: a retire write removes the entity, and what that changes is drained again
      const o = JSON.parse(f.drain()); w.log.push(...o.writes); all.push(...o.writes);
      const gone = o.writes.filter((x) => x.prop === 'retire' && x.new === true);
      for (const x of gone) f.remove(x.eid, w.t);
      if (!gone.length) return all;
    }
  };
  w.get = (e) => JSON.parse(f.get(e));
  w.write = (e, p, v) => { f.write(e, p, JSON.stringify(v ?? null), w.t); return w.flush(); };
  w.feed = (e, cls, props) => { if (f.get(e) === 'null') f.load(e, cls, '{}'); for (const [k, v] of Object.entries(props)) f.write(e, k, JSON.stringify(v ?? null), w.t); w.flush(); };
  w.tick = (dt = 0.1) => { w.t += dt; f.tick(w.t); return w.flush(); };
  w.asked = (x) => (w.get('Fetch:' + x) || {}).request;
  w.reqs = (x) => w.log.filter((e) => e.eid === 'Fetch:' + x && e.prop === 'request' && !e.born);
  w.reply = (x, status, body) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'ok', rc: 0, head: String(status), http_status: status, headers: {}, body: typeof body === 'string' ? body : JSON.stringify(body), started: w.t, finished: w.t, ms: 1 });
  w.fail = (x, why) => w.write('Fetch:' + x, 'request_result', { id: w.asked(x).id, status: 'error', rc: 1, head: why, started: w.t, finished: w.t, ms: 1 });
  w.n = (k) => w.get('DomNode:shell:' + k) || {};
  w.click = (eid) => w.write(eid, 'clicked', w.t);
  // the bootstrap: the page's html element is a page node; the shell's entities are fed view and sid first, `open` last
  w.boot = (props = {}) => { w.feed('Shell:main', 'Shell', { view: 'chat', ...props }); w.feed('Shell:main', 'Shell', { open: true }); };
  return w;
}
const SESSIONS = [{ id: 'ses_1', title: 'Plan the trip', time: { created: 1, updated: 2 } }, { id: 'ses_2', title: 'Ada notes', time: { created: 3, updated: 4 } }];
const MSG = (id, text, state = 'done', response = 'ok') => ({ id, text, state, response, turn: { started: 1, ended: 2 }, tools: [], tool_calls: [] });
let n = 0;
const scenario = (name, fn) => { fn(); n++; console.log('  ok', name); };

scenario('the skeleton: header, nav, status line, theme, one slot per view', () => {
  const w = world(); w.boot();
  assert.equal(w.n('root').class, 'shell'); assert.equal(w.n('bar').tag, 'header'); assert.equal(w.n('title').text, 'Membrane seat');
  assert.equal(w.n('nav').tag, 'nav'); assert.equal(w.n('nav')['aria-label'], 'Views');
  assert.equal(w.n('status').role, 'status'); assert.equal(w.n('status')['aria-live'], 'polite');
  assert.deepEqual(['chat', 'field', 'settings'].map((k) => w.get('DomNode:shell:nav:' + k).text), ['Chat', 'Field', 'Settings']);
  for (const k of ['chat', 'field', 'settings']) assert.equal(w.get('DomNode:shell:slot:' + k).parent, 'DomNode:shell:views');
  assert.equal(w.n('error').role, 'alert'); assert.equal(w.n('error').hidden, true, 'no error, no alert');
});

scenario('the chat is the default view; Field and Settings slots are hidden; the Settings slot says Settings', () => {
  const w = world(); w.boot();
  assert.equal(w.get('DomNode:shell:slot:chat').hidden, false);
  assert.equal(w.get('DomNode:shell:slot:field').hidden, true);
  assert.equal(w.get('DomNode:shell:slot:settings').hidden, true);
  assert.equal(w.get('DomNode:shell:slot:settings:empty').text, 'Settings');
  assert.equal(w.get('DomNode:chat:root').parent, 'DomNode:shell:slot:chat', 'the chat view draws into its slot');
  assert.equal(w.get('Nav:chat').filled, true);
  assert.equal(w.get('Scene:field'), null, 'the field view is not drawn until it is shown');
});

scenario('navigation: Field opens its view lazily; Settings shows the placeholder; the hidden view stops its work', () => {
  const w = world(); w.boot();
  w.click('DomNode:shell:nav:field');
  assert.equal(w.get('Shell:main').view, 'field');
  assert.equal(w.get('DomNode:shell:slot:field').hidden, false); assert.equal(w.get('DomNode:shell:slot:chat').hidden, true);
  assert.equal(w.get('DomNode:shell:nav:field')['aria-current'], 'page'); assert.equal(w.get('DomNode:shell:nav:chat')['aria-current'], undefined);
  assert.equal(w.get('Scene:field').open, true); assert.equal(w.get('DomNode:f:root').parent, 'DomNode:shell:slot:field');
  assert.equal(w.get('Nav:field').filled, true);
  w.click('DomNode:shell:nav:settings');
  assert.equal(w.get('DomNode:shell:slot:settings').hidden, false); assert.equal(w.get('DomNode:shell:slot:settings:empty').hidden, false);
  assert.equal(w.get('Nav:field').active, false);
  // the field's layout does not step while it is not shown
  w.feed('Boot:seat', 'Boot', {});
  const before = w.get('Scene:field').heat;
  for (let i = 0; i < 20; i++) w.tick(0.1);
  assert.equal(w.get('Scene:field').heat, before, 'the layout is paused');
  w.click('DomNode:shell:nav:chat');
  assert.equal(w.get('DomNode:shell:slot:chat').hidden, false);
});

scenario('the view-pack contract: a pack registers a Nav, draws into its slot and the placeholder goes', () => {
  const stub = `PACK settings_stub 1
IMPORT dom 1
IMPORT shell_view 1
Shell.open on-setter stub-register:
  WHEN this.open:
    ('Nav:settings').title = 'Settings'
    ('Nav:settings').order = 90
Nav.active on-setter stub-draw:
  WHEN this.eid == 'Nav:settings' and this.active and prop('Stub:x', 'drawn') != True:
    ('DomNode:stub:root').parent = 'DomNode:shell:slot:settings'
    ('DomNode:stub:root').tag = 'div'
    ('DomNode:stub:root').text = 'the settings view'
    ('Stub:x').drawn = True
    ('Nav:settings').filled = True
`;
  const w = world({ settings_stub: stub }); w.boot();
  w.click('DomNode:shell:nav:settings');
  assert.equal(w.get('DomNode:stub:root').parent, 'DomNode:shell:slot:settings');
  assert.equal(w.get('DomNode:shell:slot:settings:empty').hidden, true, 'the placeholder is hidden once the slot is filled');
  assert.equal(w.get('DomNode:shell:slot:settings').hidden, false);
});

scenario('ONE chat: no session list, no New session; the history is read with no sid and older pages with before; a send has no sid', () => {
  const w = world(); w.boot();
  assert.equal(w.get('DomNode:shell:sessions'), null, 'no sessions column'); assert.equal(w.get('DomNode:shell:newsession'), null); assert.equal(w.get('Fetch:sessions'), null, 'no sessions fetch');
  assert.equal(w.asked('history').url, '/membrane/chat/messages?limit=20', 'every turn of the seat, newest page first');
  w.reply('history', 200, { messages: [MSG('1000-1', 'in main')], more: true, next: '1000-1' });
  assert.equal(w.get('DomNode:chat:row:1000-1:me').text, 'in main'); assert.equal(w.get('DomNode:chat:older').hidden, false);
  w.click('DomNode:chat:older:btn');
  assert.equal(w.asked('older').url, '/membrane/chat/messages?limit=20&before=1000-1', 'Load older pages with before, no sid');
  w.reply('older', 200, { messages: [MSG('900-0', 'an imported turn')], more: false, next: null });
  assert.equal(w.get('DomNode:chat:row:900-0').before, 'DomNode:chat:row:1000-1', 'the older turn sits above, in the same thread');
  w.write('DomNode:chat:input', 'value', 'hello'); w.write('DomNode:chat:form', 'submitted', w.t);
  assert.deepEqual(JSON.parse(w.asked('send').body), { text: 'hello' });
  w.reply('send', 403, { error: 'for the owner' });
  assert.match(w.get('DomNode:chat:note:bubble').text, /for the owner/, 'a refusal is text');
});

scenario('the status line: connected or not, idle or a turn running; a poll every status_ms, not while hidden', () => {
  const w = world(); w.boot();
  assert.equal(w.n('status').text, 'connecting');
  w.reply('status', 200, { ses_1: { type: 'idle' } });
  assert.equal(w.n('status').text, 'connected · idle'); assert.equal(w.n('status')['data-ok'], '1');
  w.tick(3.1); assert.equal(w.reqs('status').length, 2, 'the poll goes after the period (3000 ms)');
  w.reply('status', 200, { ses_1: { type: 'busy' } });
  assert.equal(w.n('status').text, 'connected · a turn is running');
  w.feed('Conn:page', 'Conn', { hidden: true }); w.tick(10); assert.equal(w.reqs('status').length, 2, 'hidden: no poll');
  w.feed('Conn:page', 'Conn', { hidden: false }); w.tick(0.1); assert.equal(w.reqs('status').length, 3);
  w.fail('status', 'Failed to fetch');
  assert.equal(w.n('status').text, 'the seat is not answering: Failed to fetch · idle'); assert.equal(w.n('status')['data-ok'], '0');
  // a turn the chat still waits for is a running turn too
  const v = world(); v.boot(); v.reply('status', 200, {});
  v.reply('history', 200, { messages: [MSG('1-1', 'x', 'running', null)], more: false, next: null });
  assert.equal(v.n('status').text, 'connected · a turn is running');
  // the fact names the period
  const u = world(); u.feed('Boot:seat', 'Boot', { shell_status_ms: 500 }); u.boot(); u.reply('status', 200, {});
  u.tick(0.6); assert.equal(u.reqs('status').length, 2);
});

scenario('packs that did not load are shown as text', () => {
  const w = world(); w.boot({ load_errors: { chat_view: 'boom' } });
  assert.equal(w.n('error').hidden, false); assert.match(w.n('error').text, /did not load.*chat_view.*boom/);
});

scenario('the theme cycles system, light, dark on the page element', () => {
  const w = world(); w.boot();
  assert.equal(w.n('theme').text, 'Theme: system');
  w.click('DomNode:shell:theme'); assert.equal(w.get('DomNode:page:html')['data-theme'], 'light'); assert.equal(w.n('theme').text, 'Theme: light');
  w.click('DomNode:shell:theme'); assert.equal(w.get('DomNode:page:html')['data-theme'], 'dark');
  w.click('DomNode:shell:theme'); assert.equal(w.get('DomNode:page:html')['data-theme'], undefined, 'system: no attribute'); assert.equal(w.n('theme').text, 'Theme: system');
});

scenario('the work of a tick is bounded: an idle page between polls writes nothing', () => {
  const w = world(); w.boot(); w.reply('status', 200, {});
  w.reply('history', 200, { messages: [MSG('1-1', 'x'), MSG('2-2', 'y')], more: false, next: null });
  const per = [];
  for (let i = 0; i < 20; i++) { const before = w.log.length; w.tick(0.1); per.push(w.log.length - before); }
  // 20 ticks of 100 ms = 2 s: under every poll period (3 s): only the clock getters of the views move
  assert.ok(Math.max(...per) <= 4, `at most 4 writes in a tick, saw ${per}`);
  const field = world(); field.boot(); field.click('DomNode:shell:nav:settings');
  field.click('DomNode:shell:nav:chat');
  const hidden = []; for (let i = 0; i < 20; i++) { const before = field.log.length; field.tick(0.1); hidden.push(field.log.length - before); }
  assert.ok(Math.max(...hidden) <= 4, `a page whose Field view was never shown writes at most 4 per tick: ${hidden}`);
});

scenario('the slide-over panel: any view opens it with rows; the scrim, Close and Esc close it; a link is a pick; a long value expands; the chat stays mounted with its draft', () => {
  const stub = `PACK opener 1
IMPORT dom 1
IMPORT shell_view 1
Opener.kind getter: 'x'
DomNode.clicked on-setter opener-go:
  WHEN this.eid == 'DomNode:shell:nav:settings':
    ('Panel:detail').owner = 'opener'
    ('Panel:detail').title = 'Message 7'
    ('Panel:detail').sub = 'a detail of another view'
    ('Panel:detail').note = ''
    ('Panel:detail').rows = [{'key': 'a', 'label': 'Properties', 'head': True}, {'key': 'b:a', 'label': 'a', 'text': 'short'}, {'key': 'b:long', 'label': 'long', 'text': 'yabcdefghijz'}, {'key': 'c:1', 'label': 'x', 'text': 'owner · X', 'target': 'X'}]
    ('Panel:detail').open = True
Panel.picked on-setter opener-pick:
  WHEN this.owner == 'opener' and this.picked != None:
    ('Opener:x').got = this.picked
Panel.open on-setter opener-closed:
  WHEN this.owner == 'opener' and this.open == False:
    ('Opener:x').closed = True
`;
  const w = world({ opener: stub }); w.feed('Boot:seat', 'Boot', { shell_panel_chars: 6 }); w.boot();
  const P = (k) => w.get('DomNode:shell:' + k) || {};
  assert.equal(P('panel')['data-open'], 'false'); assert.equal(P('scrim')['data-open'], 'false'); assert.equal(P('panel').role, 'dialog');
  w.write('DomNode:chat:input', 'value', 'a draft');
  w.click('DomNode:shell:nav:settings');                              // the opener stub opens the panel on this click
  assert.equal(P('panel')['data-open'], 'true'); assert.equal(P('scrim')['data-open'], 'true'); assert.equal(P('panel').focused, true, 'the panel takes the focus');
  assert.equal(P('paneltitle').text, 'Message 7'); assert.equal(P('panelsub').text, 'a detail of another view');
  assert.equal(w.get('DomNode:shell:panel:r:a').class, 'head'); assert.equal(w.get('DomNode:shell:panel:vv:b:a').text, 'short');
  assert.equal(w.get('DomNode:shell:panel:xx:b:a').hidden, true, 'a short value has no expander');
  assert.equal(w.get('DomNode:shell:panel:vv:b:long').text, 'yabcd…', 'a long value is cut (shell_panel_chars = 6)');
  w.click('DomNode:shell:panel:xx:b:long');
  assert.equal(w.get('DomNode:shell:panel:vv:b:long').text, 'yabcdefghijz'); assert.equal(w.get('DomNode:shell:panel:xx:b:long').text, 'Show less');
  w.click('DomNode:shell:panel:xx:b:long'); assert.equal(w.get('DomNode:shell:panel:vv:b:long').text, 'yabcd…');
  assert.equal(w.get('DomNode:shell:panel:r:c:1').class, 'link'); assert.equal(w.get('DomNode:shell:panel:r:c:1').text, 'owner · X');
  w.click('DomNode:shell:panel:r:c:1'); assert.equal(w.get('Panel:detail').picked, 'X'); assert.equal(w.get('Opener:x').got, 'X');
  // the chat is under the panel, not gone: its draft and nodes are untouched
  assert.equal(w.get('DomNode:chat:input').value, 'a draft');
  assert.ok(w.get('DomNode:chat:root'), 'the chat view is still mounted');
  // close: the scrim (a click outside), Close, Esc
  w.click('DomNode:shell:scrim'); assert.equal(P('panel')['data-open'], 'false'); assert.equal(P('scrim')['data-open'], 'false'); assert.equal(w.get('Opener:x').closed, true);
  w.click('DomNode:shell:nav:settings'); assert.equal(P('panel')['data-open'], 'true');
  w.click('DomNode:shell:panelclose'); assert.equal(P('panel')['data-open'], 'false');
  w.click('DomNode:shell:nav:settings'); assert.equal(P('panel')['data-open'], 'true');
  w.write('DomNode:shell:panelclose', 'key', { key: 'Escape', shift: false, ctrl: false, composing: false, time: w.t });
  assert.equal(P('panel')['data-open'], 'false', 'Esc closes it');
  w.click('DomNode:shell:nav:settings'); w.write('DomNode:shell:panel', 'key', { key: 'a', time: w.t + 1 }); assert.equal(P('panel')['data-open'], 'true', 'another key does not');
  assert.equal(w.get('Panel:detail').expanded.length, 0, 'what was expanded is forgotten with the close');
});

console.log(`shell_view: ${n} scenarios ok`);
