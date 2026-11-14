// The field engine in the page: the same Rust VM as the seat (getters, setters, `.c1f` packs, imports), compiled
// to WebAssembly (fieldvm_core.js / fieldvm_core_bg.wasm, built by native/build-web.sh).
//
// The DOM is a mount of the field: every `DomNode:<key>` entity is an element, keyed by its id.
//   tag     the element's tag (from an allow-list; `div` otherwise), set when the element is made
//   text    its text (textContent only: a pack can never write HTML)
//   parent  the DomNode it sits in (the mount's root when empty)
//   class, title, role, hidden, alt, href (http/https only), aria-label ...: attributes, from the ONE allow-list (DOM_ALLOW)
//   value, focused: a pack's write sets the element's value / focus (no echo; the user's caret is not clobbered)
// Form tags and SVG (own namespace) are in the allow-list; style, event handlers and src are refused, and a refusal is
// visible: the element carries `data-refused` and the DomNode's `refused` holds {name: reason}.
// Events are written back: pointerdown/pointermove (button held)/pointerup -> `pointer` {type, x, y, w, h, buttons, time} (x, y in the element's box); wheel -> `wheel` {dy, x, y, w, h, time}; click -> `clicked` (time); input -> `value`; submit -> `submitted` (time); keydown ->
// `key` {key, shift, ctrl, composing, time}; focus/blur -> `focused` (bool); scroll -> `scroll` {top, height, client}.
// A pack writes `scroll` {top: n} or 'end' to scroll an element (applied after the round's other writes; the measured scroll is written back).
// `before` (a sibling DomNode eid) puts a node above that sibling; writing it again moves the node. A round's DOM writes are applied together, in one frame.
// A pack's `retire = true` on any entity removes it from the engine (as the seat's does); a DomNode's element leaves the page with the elements under it.
// Explicit drivers, called by the mount: `run_clock(ms)` / `stop_clock()` tick the field; `track_hidden()` keeps `Conn:page.hidden` (bool).
//
// The page's own DOM links in too (`link`): each element of the page (open shadow roots included) is a
// `DomNode:page:#<id>` entity (`DomNode:page:<n>` without an id) with page=true and its tag, parent, id, direct text
// and allow-listed attributes, kept in step as the page changes (an element that appears is born whole, a change is a
// write, a removal leaves the field). Packs read it, place their nodes in it (parent = a page node) and write to it.
//
// Data enters as writes (`feed`): an entity is made empty, then each field is written, so the views' setters
// fire on it (a birth's own getters compute silently, as everywhere).
import init, { Field } from './fieldvm_core.js';
import { WASM_VERSION } from './fieldvm_version.js';

export const DOM_PACK = "PACK dom 1\n-- the page's DOM as entities: DomNode:<key>\nDomNode.tag getter: this.tag or 'div'\n";

export const FETCH_PACK = "PACK fetch 1\nRESIDENT Fetch\n-- THE OUTBOUND HTTP DRIVER's class (native/src/fetch.rs): `Fetch:<x>.request = {id, url, ...}` is the effect, `request_result` the receipt\n-- ({id, status, rc, head, http_status, headers, body}). A pack that makes a call IMPORTs this pack and adds its own getters/setters.\nFetch.kind getter: 'fetch'\n";

// THE ALLOW-LIST: the one table of what a pack may put in the page. Changing it is a one-line edit here.
export const DOM_ALLOW = {
  tags: 'div span p ul ol li h1 h2 h3 h4 button a section header footer small strong em code pre table thead tbody tr td th label details summary input textarea select option form',
  svgTags: 'svg g circle line path text rect',                       // made in the SVG namespace
  attrs: 'class title role hidden alt href aria-label value placeholder disabled type name for tabindex rows checked selected multiple',
  svgAttrs: 'cx cy r x y x1 y1 x2 y2 d width height viewBox points fill stroke stroke-width transform',
  prefixes: ['aria-', 'data-'],
  // never written to the element, each with its reason (a decision: `style`, `href`'s scheme, `src`)
  refused: {
    style: 'style is not in the allow-list (decision: inline style)',
    src: 'src is not in the allow-list (decision: embedded sources)',
    srcdoc: 'srcdoc is never allowed',
    formaction: 'formaction is never allowed',
  },
  refusedPrefixes: { on: 'event-handler attributes are never allowed' },
  hrefSchemes: /^https?:\/\//i,                                       // href is kept (the About link): http(s) only
};
const words = (t) => new Set(t.split(' '));
const TAGS = words(DOM_ALLOW.tags), SVG_TAGS = words(DOM_ALLOW.svgTags);
const ATTRS = new Set([...words(DOM_ALLOW.attrs), ...words(DOM_ALLOW.svgAttrs)]);
const SVG_NS = 'http://www.w3.org/2000/svg';
const VALUE_TAGS = new Set(['input', 'textarea', 'select']);          // `value` is a property there
const allowed = (name) => ATTRS.has(name) || DOM_ALLOW.prefixes.some(p => name.startsWith(p) && name.length > p.length);
const reason = (name, v) => {
  if (DOM_ALLOW.refused[name]) return DOM_ALLOW.refused[name];
  for (const [p, why] of Object.entries(DOM_ALLOW.refusedPrefixes)) if (name.startsWith(p) && name !== 'on') return why;
  if (name === 'href' && v != null && v !== false && !DOM_ALLOW.hrefSchemes.test(String(v))) return 'href must be http(s)';
  return null;
};
const NODE = 'DomNode:';
const EVENTS = ['input', 'change', 'submit', 'keydown', 'focus', 'blur', 'scroll', 'pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'wheel'];
const FETCH = 'Fetch:';
const FETCH_VERBS = new Set(['GET', 'POST', 'PATCH', 'DELETE']);
const FETCH_HEADERS = new Set(['content-type', 'accept']);

export async function start(root, { packs = {}, wasm } = {}) {
  // by its content hash: the seat caches .wasm for a week, so a rebuilt loader never meets an old binary
  await init({ module_or_path: wasm || new URL(`./fieldvm_core_bg.wasm?v=${WASM_VERSION}`, import.meta.url) });
  const f = new Field();
  f.set_pack('dom', DOM_PACK);
  f.set_pack('fetch', FETCH_PACK);
  const errors = {};
  for (const [name, src] of Object.entries(packs)) {
    try { f.set_pack(name, src); } catch (e) { errors[name] = String(e); }
  }
  return new Host(f, root, errors);
}

class Host {
  constructor(f, root, errors) {
    this.f = f; this.root = root; this.nodes = new Map(); this.pending = []; this.frame = 0;
    this.errors = errors;
    this.out = new Map();                 // W2: the Fetch in flight (or last made) per entity: {key, ctl, why}
    this.els = new WeakMap();             // element -> its entity (the page's and the packs')
    this.page = new Map();                // the page's own elements: entity -> element
    this.schedule = (fn) => requestAnimationFrame(fn);           // one batch per frame (a test may use a timer)
    this.timers = { set: (fn, ms) => setInterval(fn, ms), clear: (id) => clearInterval(id) };   // the clock's timer (a test may fake it)
    this.clockId = null;
  }

  /** The clock driver: ONE explicit timer that ticks the field every `ms` (the pack's `clock` rules own the periods). A second start replaces it. */
  run_clock(ms) {
    this.stop_clock();
    this.clockId = this.timers.set(() => { this.f.tick(this.now()); this.flush(); }, ms);
  }

  stop_clock() {
    if (this.clockId !== null) this.timers.clear(this.clockId);
    this.clockId = null;
  }

  /** The page's visibility as a fact: `Conn:page.hidden` (bool), written now and on every `visibilitychange`. Returns the function that stops it. */
  track_hidden(doc = document) {
    const put = () => { const h = !!doc.hidden; if ((this.get('Conn:page') || {}).hidden !== h) this.feed('Conn:page', 'Conn', { hidden: h }, true); };
    put();
    doc.addEventListener('visibilitychange', put);
    return () => doc.removeEventListener('visibilitychange', put);
  }

  now() { return Date.now() / 1000; }

  /** An entity arrives: made empty, then each field written, so the views' setters see it. */
  feed(eid, cls, props = {}, mirror = false) {
    if (this.f.get(eid) === 'null') this.f.load(eid, cls, '{}');
    for (const [k, v] of Object.entries(props)) this.f.write(eid, k, JSON.stringify(v ?? null), this.now());
    if (!mirror) this.fetches(Object.entries(props).map(([prop, v]) => ({ eid, prop, new: v })));   // W2: what the seat did is not asked again here
    this.flush();
  }

  /** An entity the seat removed (#1054): it leaves the page's field; what read or referenced it recalculates. */
  drop(eid) {
    this.f.remove(eid, this.now());
    this.flush();
  }

  /** An outside write (a click, a value from the seat). */
  write(eid, prop, value) {
    this.f.write(eid, prop, JSON.stringify(value ?? null), this.now());
    this.fetches([{ eid, prop, new: value }]);                    // W2: an outside write is not in drain(); a pack's is
    this.flush();
  }

  /** Load or replace a pack while the page runs. */
  pack(name, src) {
    try { this.f.set_pack(name, src); delete this.errors[name]; return null; }
    catch (e) { this.errors[name] = String(e); return this.errors[name]; }
  }

  get(eid) { return JSON.parse(this.f.get(eid)); }

  flush() {
    for (;;) {
      const out = JSON.parse(this.f.drain());
      this.pending.push(...out.writes);
      this.fetches(out.writes);                                   // W2: a write of Fetch:<x>.request / .abort
      const gone = out.writes.filter(w => w.prop === 'retire' && w.new === true).map(w => w.eid);
      for (const eid of gone) this.retire(eid);                   // the seat's engine removes a retired entity (docs/cliffvm.md): so does the page's; what that changes is drained again
      if (!gone.length) break;
    }
    if (!this.frame) this.frame = this.schedule(() => this.apply());
  }

  /** `retire = true` (any writer): the entity leaves the engine; a DomNode's element leaves the page, and the nodes under it are forgotten (their entities are retired by the pack that made them). */
  retire(eid) {
    this.f.remove(eid, this.now());
    this.pending = this.pending.filter(w => w.eid !== eid);       // a write the element was still owed is moot
    const el = this.nodes.get(eid);
    if (!el) return;
    el.remove();
    for (const [k, e] of this.nodes) if (!e.isConnected) this.nodes.delete(k);
  }

  /** One frame: make the new elements first, then set fields (a parent may be made in the same round). */
  apply() {
    this.frame = 0;
    const ws = this.pending; this.pending = [];
    const touched = new Map();
    for (const w of ws) {
      if (!w.eid.startsWith(NODE)) continue;
      if (!touched.has(w.eid)) touched.set(w.eid, {});
      if (!w.born) touched.get(w.eid)[w.prop] = w.new;
    }
    this.made = new Set();
    for (const eid of touched.keys()) this.node(eid);
    const scrolls = [];
    for (const [eid, props] of touched) {
      const el = this.node(eid);
      for (const [k, v] of Object.entries(props)) {
        if (k === 'scroll') scrolls.push([el, eid, v]);          // after the round's other writes: the children it scrolls over are there
        else this.set(el, eid, k, v);
      }
    }
    for (const [el, eid, v] of scrolls) this.scroll(el, eid, v);
    this.made = null;
  }

  /** A pack's scroll write: `{top: n}` or `'end'`. The measured scroll is written back (as the scroll event would), so the field holds what is true. */
  scroll(el, eid, v) {
    const top = v === 'end' ? el.scrollHeight : (v && typeof v === 'object' && typeof v.top === 'number' ? v.top : null);
    if (top === null) return;                                     // an event's own write ({top, height, client}) lands here too: it is not a command
    if (v && typeof v === 'object' && 'height' in v) return;
    el.scrollTop = top;
    this.event(eid, { type: 'scroll', target: el }, el);
  }

  node(eid) {
    const own = this.page.get(eid);
    if (own) return own;                                          // the page's element: the page owns its tag
    let el = this.nodes.get(eid);
    const e = this.get(eid) || {};
    const want = String(e.tag), ok = TAGS.has(want) || SVG_TAGS.has(want), tag = ok ? want : 'div';
    if (!ok && e.tag != null) this.refuseDom(el, eid, 'tag', `tag '${want}' is not in the allow-list (made a div)`);
    if (el && el.tagName.toLowerCase() !== tag) {                 // its tag changed: a new element in its place
      const nu = this.make(tag);
      for (const a of [...el.attributes]) nu.setAttribute(a.name, a.value);
      nu.append(...el.childNodes);
      this.els.set(nu, eid);
      el.replaceWith(nu);
      this.listen(nu, eid);
      this.nodes.set(eid, nu);
      return nu;
    }
    if (!el) {
      el = this.make(tag);
      el.setAttribute('data-key', eid);
      this.els.set(el, eid);
      this.listen(el, eid);
      this.nodes.set(eid, el);
      if (this.made) this.made.add(eid);
      this.attach(el, e.parent, e.before);
    }
    return el;
  }

  make(tag) { return SVG_TAGS.has(tag) ? document.createElementNS(SVG_NS, tag) : document.createElement(tag); }

  listen(el, eid) {
    el.addEventListener('click', ev => { ev.stopPropagation(); this.write(eid, 'clicked', this.now()); });
    for (const t of EVENTS) el.addEventListener(t, ev => this.event(eid, ev, el));
  }

  /** A DOM event of an element becomes a write on its DomNode (an unchanged value or flag is no write: no echo loop). */
  event(eid, ev, el) {
    if (ev.type === 'scroll' && ev.target !== el) return;
    const e = this.get(eid) || {}, same = (k, v) => JSON.stringify(e[k] ?? null) === JSON.stringify(v);
    const put = (k, v) => { if (!same(k, v)) this.write(eid, k, v); };
    if (ev.type === 'input' || ev.type === 'change') { if (ev.target === el) put('value', el.value); }
    else if (ev.type === 'submit') { ev.preventDefault(); this.write(eid, 'submitted', this.now()); }
    else if (ev.type === 'keydown') {
      if (ev.key === 'Enter' && !ev.shiftKey && el.tagName === 'TEXTAREA') ev.preventDefault();   // Enter sends; Shift+Enter is a newline
      this.write(eid, 'key', { key: ev.key, shift: ev.shiftKey, ctrl: ev.ctrlKey, composing: !!ev.isComposing, time: this.now() });
    }
    else if (ev.type === 'focus') put('focused', true);
    else if (ev.type === 'blur') put('focused', false);
    else if (ev.type === 'scroll') put('scroll', { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight });
    else if (ev.type === 'wheel') { ev.preventDefault(); const b = el.getBoundingClientRect(); this.write(eid, 'wheel', { dy: ev.deltaY, x: ev.clientX - b.left, y: ev.clientY - b.top, w: b.width, h: b.height, time: this.now() }); }
    else if (ev.type.startsWith('pointer')) {                       // a press, a move WITH a button held, a release: a hover writes nothing (the field is not flooded)
      if (ev.type === 'pointermove' && !ev.buttons) return;
      if (ev.type === 'pointerdown' && ev.target === el && el.setPointerCapture) { try { el.setPointerCapture(ev.pointerId); } catch {} }   // only a press ON the element itself captures: a press on a child (a dot in the scene) must still become that child's click; the drag goes on outside the element
      const b = el.getBoundingClientRect();
      this.write(eid, 'pointer', { type: ev.type, x: ev.clientX - b.left, y: ev.clientY - b.top, w: b.width, h: b.height, buttons: ev.buttons, time: this.now() });
    }
  }

  /** A name or tag the allow-list refuses: the reason is written on the DomNode and marked on the element. */
  refuseDom(el, eid, name, why) {
    const cur = (this.get(eid) || {}).refused || {};
    if (cur[name] === why) return;
    const next = { ...cur, [name]: why };
    this.f.write(eid, 'refused', JSON.stringify(next), this.now());
    if (el) el.setAttribute('data-refused', Object.keys(next).join(' ') + ': ' + Object.values(next).join('; '));
  }

  set(el, eid, prop, v) {
    if (prop === 'text') el.textContent = v == null ? '' : String(v);
    else if (prop === 'parent') this.attach(el, v, (this.get(eid) || {}).before);
    else if (prop === 'before') { if (!(this.made && this.made.has(eid))) this.attach(el, (this.get(eid) || {}).parent, v, true); }   // a node made this round is already in place
    else if (prop === 'focused') {                                // a pack's focus(): the event that follows is no write (same value)
      if (v && document.activeElement !== el) el.focus(); else if (!v && document.activeElement === el) el.blur();
    }
    else if (prop === 'value' && VALUE_TAGS.has(el.tagName.toLowerCase())) {
      const s = v == null ? '' : String(v);
      if (el.value !== s) el.value = s;                           // the user's own text is left alone: the caret stays
    }
    else if (allowed(prop) || reason(prop, v)) {
      const why = reason(prop, v);
      if (why) { this.refuseDom(el, eid, prop, why); el.removeAttribute(prop); return; }
      if (v == null || v === false) { el.removeAttribute(prop); return; }
      const sv = v === true ? '' : String(v);
      if (el.getAttribute(prop) !== sv) el.setAttribute(prop, sv);          // an unchanged value is no DOM mutation
    }
  }

  /** Put `el` under `parent` (the mount's root when empty). `before` (a sibling DomNode eid) orders it: above that sibling. A node already in
   *  place moves only when `move` (its `before` was written), so a later write of another property never reorders the list. */
  attach(el, parent, before, move = false) {
    const p = typeof parent === 'string' && parent.startsWith(NODE) ? this.node(parent) : this.root;
    const sib = typeof before === 'string' && before.startsWith(NODE) && before !== this.els.get(el) ? this.node(before) : null;
    const there = sib && sib.parentNode === p;
    if (el.parentNode !== p) { if (there) p.insertBefore(el, sib); else p.appendChild(el); }
    else if (move && there && el.nextSibling !== sib) p.insertBefore(el, sib);
  }

  // ---- W2: the page's `Fetch` entity (#1143). A pack writes `Fetch:<x>.request = {id, url, method?, headers?, body?, timeout_s?}`; this
  // makes ONE fetch() and writes `request_result` back in the seat's shape (native/src/drivers.rs + fetch.rs: {id, status, rc, head,
  // http_status, headers, body, started, finished, ms}; a failure is {id, status:'error', rc, head: the reason, started, finished, ms}).
  // Same origin only, credentials same-origin (the owner cookie), never authorization or cookie. `abort = true` aborts the one in flight.
  // No timeout unless the request carries `timeout_s`. W3: with `stream: true` the body is read as SSE; each frame's `data` is a page-local
  // entity `Chunk:<fetch eid>:<n>` ({fetch, id, n, data, done}, the seat's Fetch Chunk), n from 1, a last `done: true` Chunk (n+1, `error` on
  // a failure or abort), then the receipt without a body. Chunks are fed as mirrors: never fetched, never sent anywhere.

  fetches(writes) {
    for (const w of writes) {
      if (!w.eid || !w.eid.startsWith(FETCH) || w.born) continue;
      if (w.prop === 'request' && w.new && typeof w.new === 'object') this.ask(w.eid, w.new);
      else if (w.prop === 'abort' && w.new === true) { const cur = this.out.get(w.eid); if (cur) cur.ctl.abort(); }
    }
  }

  /** Why a request may not go (a visible reason), or null. */
  refuse(req, origin) {
    if (typeof req.url !== 'string' || !req.url) return 'refused: the request has no url';
    let u;
    try { u = new URL(req.url, origin || undefined); } catch { return `refused: not a url: ${JSON.stringify(req.url)}`; }
    if (!origin || u.origin !== origin) return `refused: ${u.origin} is not this page's origin: the page's Fetch is same origin only`;
    const m = String(req.method ?? 'GET').toUpperCase();
    if (!FETCH_VERBS.has(m)) return `refused: method ${m} is not one of ${[...FETCH_VERBS].join(', ')}`;
    for (const h of Object.keys(req.headers || {})) if (!FETCH_HEADERS.has(h.toLowerCase())) return `refused: header ${h} is not one of ${[...FETCH_HEADERS].join(', ')}`;
    if (req.body != null && typeof req.body !== 'string') return 'refused: body is text only for now';
    if (req.timeout_s != null && !(typeof req.timeout_s === 'number' && req.timeout_s > 0)) return 'refused: timeout_s must be a positive number';
    return null;
  }

  ask(eid, req) {
    const id = req.id ?? null;
    const key = JSON.stringify(id !== null ? id : req);
    const cur = this.out.get(eid);
    if (cur && cur.key === key) return;                           // the same request again: not made twice
    const started = this.now(), t0 = Date.now();
    const round3 = (x) => Math.round(x * 1000) / 1000;
    const done = (r) => this.write(eid, 'request_result', { id, ...r, started: round3(started), finished: round3(this.now()), ms: Date.now() - t0 });
    const mine = { key, ctl: new AbortController(), why: null };
    this.out.set(eid, mine);
    const origin = this.origin !== undefined ? this.origin : (globalThis.location && globalThis.location.origin);
    const bad = this.refuse(req, origin);
    if (bad) { done({ status: 'error', rc: 2, head: bad }); return; }
    const timer = req.timeout_s != null ? setTimeout(() => { mine.why = `timeout after ${req.timeout_s} s`; mine.ctl.abort(); }, req.timeout_s * 1000) : null;
    mine.ctl.signal.addEventListener('abort', () => { mine.why = mine.why || 'aborted'; });
    const headers = {};
    for (const [k, v] of Object.entries(req.headers || {})) headers[k.toLowerCase()] = String(v);
    const init = { method: String(req.method ?? 'GET').toUpperCase(), headers, credentials: 'same-origin', signal: mine.ctl.signal };
    if (req.body != null) init.body = req.body;
    (async () => {
      try {
        const resp = await fetch(new URL(req.url, origin).href, init);
        const stream = req.stream === true && resp.body;
        const body = stream ? null : await resp.text();
        const hs = {};
        for (const [k, v] of resp.headers) hs[String(k).toLowerCase()] = v;
        const r = { status: 'ok', rc: 0, head: `${resp.status} ${resp.statusText || ''}`.trim(), http_status: resp.status, headers: hs };
        if (stream) { mine.n = 0; await this.sse(eid, id, resp.body, mine); this.chunk(eid, id, mine, '', true); }
        else r.body = body;
        done(r);
      } catch (e) {
        const why = mine.why || String((e && e.message) || e);
        if (mine.n !== undefined) this.chunk(eid, id, mine, '', true, why);
        done({ status: 'error', rc: 1, head: why });
      } finally { if (timer) clearTimeout(timer); }
    })();
  }

  /** One Chunk of a stream: a page-local entity (a mirror feed: not fetched again). `mine.n` counts them. */
  chunk(eid, id, mine, data, last = false, error) {
    const n = ++mine.n;
    const p = { fetch: eid, id, n, data, done: last };
    if (error !== undefined) p.error = error;
    this.feed(`Chunk:${eid}:${n}`, 'Chunk', p, true);
  }

  /** Read an SSE body: split on the blank line across reads (bytes decoded as a stream), one Chunk per frame that has `data`. */
  async sse(eid, id, body, mine) {
    const rd = body.getReader(), dec = new TextDecoder();
    let buf = '';
    const frames = () => {
      let m;
      while ((m = /\r\n\r\n|\n\n|\r\r/.exec(buf))) {
        const raw = buf.slice(0, m.index); buf = buf.slice(m.index + m[0].length);
        const data = [];
        for (const line of raw.split(/\r\n|\n|\r/)) {
          if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
        }
        if (data.length) this.chunk(eid, id, mine, data.join('\n'));
      }
    };
    for (;;) {
      const { value, done } = await rd.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      frames();
    }
  }

  /** Link the page's own DOM under `top` into the field, and keep it in step (one batch per frame). */
  link(top = document.body) {
    if (this.top) return this;
    this.top = top; this.n = 0; this.added = new Set(); this.fresh = new Set(); this.removed = false; this.linkFrame = 0;
    this.watched = new WeakSet();
    this.mo = new MutationObserver(ms => this.mutated(ms));
    const host = this, attach = Element.prototype.attachShadow;
    if (!attach.fieldvm) {                       // a shadow root made later is linked too (open ones: closed are private)
      Element.prototype.attachShadow = function (...a) { const r = attach.apply(this, a); host.later(this, true); return r; };
      Element.prototype.attachShadow.fieldvm = true;
    }
    document.addEventListener('click', ev => {   // capture: recorded even when the page stops the click
      for (const t of ev.composedPath()) {
        const eid = this.els.get(t);
        if (!eid) continue;
        if (this.page.has(eid)) this.write(eid, 'clicked', this.now());
        break;                                   // a pack's node records its own click
      }
    }, true);
    for (const t of EVENTS) document.addEventListener(t, ev => {   // the page's own form elements: the same events as writes
      const el = ev.composedPath()[0], eid = this.els.get(el);
      if (eid && this.page.has(eid)) this.event(eid, ev, el);
    }, true);
    this.watch(top.getRootNode() instanceof ShadowRoot ? top.getRootNode() : top);
    const now = this.now();
    this.adopt(top, now);
    this.f.settle(now);
    this.flush();
    return this;
  }

  watch(root) {
    if (this.watched.has(root)) return;
    this.watched.add(root);
    this.mo.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['id', ...ATTRS] });
  }

  later(el, subtree) {
    if (!(el instanceof Element)) return;
    (subtree ? this.added : this.fresh).add(el);
    if (!this.linkFrame) this.linkFrame = this.schedule(() => this.linked());
  }

  mutated(ms) {
    for (const m of ms) {
      if (m.type === 'childList') {
        if (m.removedNodes.length) this.removed = true;
        for (const n of m.addedNodes) this.later(n, true);
        this.later(m.target instanceof ShadowRoot ? m.target.host : m.target, false);   // its text may have changed
      } else this.later(m.type === 'characterData' ? m.target.parentElement : m.target, false);
    }
    if (this.removed && !this.linkFrame) this.linkFrame = this.schedule(() => this.linked());
  }

  /** A frame's page changes: removals leave the field, new elements are born whole, changes are written. */
  linked() {
    this.linkFrame = 0;
    const now = this.now();
    if (this.removed) {
      this.removed = false;
      for (const [eid, el] of this.page) if (!el.isConnected) { this.page.delete(eid); this.els.delete(el); this.f.remove(eid, now); }
    }
    const added = [...this.added], fresh = [...this.fresh];
    this.added.clear(); this.fresh.clear();
    let born = false;
    for (const el of added) if (el.isConnected) born = this.adopt(el, now) || born;
    for (const el of fresh) if (el.isConnected && this.page.has(this.els.get(el))) this.refresh(el, now);
    if (born) this.f.settle(now);
    this.flush();
  }

  /** An element and what is under it (its shadow root too) into the field; returns whether any was born. */
  adopt(el, now) {
    let eid = this.els.get(el);
    if (eid && !this.page.has(eid)) return false;                 // a pack's own node: the pack keeps it
    if (el !== this.top && !this.page.has(this.els.get(this.up(el)))) return false;   // outside the linked tree
    let born = false;
    if (!eid) {
      eid = this.key(el);
      this.els.set(el, eid); this.page.set(eid, el);
      this.f.load(eid, 'DomNode', JSON.stringify(this.read(el)));
      born = true;
    } else this.refresh(el, now);
    for (const c of el.children) born = this.adopt(c, now) || born;
    if (el.shadowRoot) {
      this.watch(el.shadowRoot);
      for (const c of el.shadowRoot.children) born = this.adopt(c, now) || born;
    }
    return born;
  }

  /** A known element changed: what differs from the field is written (so a pack's own write never echoes). */
  refresh(el, now) {
    const eid = this.els.get(el), m = this.get(eid) || {}, p = this.read(el);
    for (const [k, v] of Object.entries(p)) if (JSON.stringify(m[k] ?? null) !== JSON.stringify(v ?? null)) this.f.write(eid, k, JSON.stringify(v ?? null), now);
  }

  up(el) { const p = el.parentNode; return p instanceof ShadowRoot ? p.host : p; }

  read(el) {
    const up = el === this.top ? null : this.els.get(this.up(el));
    const text = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.data).join('').trim();
    const p = { page: true, tag: el.tagName.toLowerCase(), parent: up || null, id: el.id || null, text: text || null };
    for (const a of ATTRS) p[a] = a === 'hidden' ? (el.hasAttribute('hidden') || null)
      : a === 'value' && VALUE_TAGS.has(p.tag) ? el.value : a === 'disabled' ? (el.hasAttribute('disabled') || null) : el.getAttribute(a);
    return p;
  }

  key(el) {
    if (el.id) {
      const k = `${NODE}page:#${el.id}`;
      if (!this.page.has(k) && this.f.get(k) === 'null') return k;
      return `${k}~${++this.n}`;                                 // ids repeat across shadow roots
    }
    let k;
    do k = `${NODE}page:${++this.n}`; while (this.f.get(k) !== 'null');
    return k;
  }
}

/**
 * Follow the seat's field: start from its snapshot (/ui/fieldvm: the seat's packs and every entity of a class
 * they declare on), then apply each `field.write` from the seat's /event stream. When the stream ends, it
 * reconnects and starts from a fresh snapshot (nothing missed). Read-only toward the seat: what the page's own
 * packs write stays in the page. `opts.packs` adds page packs (views) on top of the seat's.
 */
export async function connect(root, opts = {}) {
  const host = await start(root, { packs: {}, wasm: opts.wasm });
  if (opts.link) host.link(opts.link);                       // the page's own DOM in the field too
  const status = opts.onStatus || (() => {});
  let stopped = false;
  host.stop = () => { stopped = true; };
  const order = (e) => (e.cls === 'Goal' ? 0 : 1);           // a goal's list exists before its tasks' rows
  async function snapshot() {
    const r = await fetch('/ui/fieldvm', { credentials: 'same-origin' });
    if (!r.ok) throw new Error(`snapshot: ${r.status}`);
    const snap = await r.json();
    for (const [name, src] of Object.entries(snap.packs || {})) if (name !== 'goalgraph') host.pack(name, src);
    for (const [name, src] of Object.entries(opts.packs || {})) host.pack(name, src);
    for (const e of [...snap.entities].sort((a, b) => order(a) - order(b))) host.feed(e.eid, e.cls, e.props, true);
    status(`following ${snap.entities.length} entities`);
  }
  async function follow() {
    const r = await fetch('/ui/serve/event', { credentials: 'same-origin', headers: { Accept: 'text/event-stream' } });
    if (!r.ok || !r.body) throw new Error(`stream: ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (!stopped) {
      const { value, done } = await reader.read();
      if (done) return;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const data = block.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).join('\n');
        if (!data) continue;
        let ev; try { ev = JSON.parse(data); } catch { continue; }
        const w = ev && ev.payload && typeof ev.payload === 'object' ? ev.payload : ev;
        const e = w && { ...w, ...(w.properties || {}) };     // the seat's /event puts an event's fields in `properties`
        if (!e) continue;
        if (e.type === 'field.pack') { if (e.name !== 'goalgraph') host.pack(e.name, e.c1f); status(`pack ${e.name} reloaded`); continue; }
        if (e.type === 'field.remove') { host.drop(e.eid); continue; }   // #1054
        if (e.type !== 'field.write') continue;
        host.feed(e.eid, e.cls, e.props || { [e.prop]: e.new }, true);
      }
    }
  }
  (async () => {
    let wait = 1000;
    while (!stopped) {
      try { await snapshot(); wait = 1000; await follow(); status('stream ended: resyncing'); }
      catch (err) { status(String(err)); }
      await new Promise(res => setTimeout(res, wait));
      wait = Math.min(wait * 2, 30000);
    }
  })();
  return host;
}
