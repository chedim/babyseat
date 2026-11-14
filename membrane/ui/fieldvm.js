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
// Events are written back: click -> `clicked` (time); input -> `value`; submit -> `submitted` (time); keydown ->
// `key` {key, shift, ctrl, time}; focus/blur -> `focused` (bool); scroll -> `scroll` {top, height, client}.
// A round's DOM writes are applied together, in one frame.
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

// THE ALLOW-LIST: the one table of what a pack may put in the page. Changing it is a one-line edit here.
export const DOM_ALLOW = {
  tags: 'div span p ul ol li h1 h2 h3 h4 button a section header footer small strong em code pre table thead tbody tr td th label details summary input textarea select option form',
  svgTags: 'svg g circle line path text rect',                       // made in the SVG namespace
  attrs: 'class title role hidden alt href aria-label value placeholder disabled type name for tabindex rows',
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
const EVENTS = ['input', 'change', 'submit', 'keydown', 'focus', 'blur', 'scroll'];

export async function start(root, { packs = {}, wasm } = {}) {
  // by its content hash: the seat caches .wasm for a week, so a rebuilt loader never meets an old binary
  await init({ module_or_path: wasm || new URL(`./fieldvm_core_bg.wasm?v=${WASM_VERSION}`, import.meta.url) });
  const f = new Field();
  f.set_pack('dom', DOM_PACK);
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
    this.els = new WeakMap();             // element -> its entity (the page's and the packs')
    this.page = new Map();                // the page's own elements: entity -> element
    this.schedule = (fn) => requestAnimationFrame(fn);           // one batch per frame (a test may use a timer)
  }

  now() { return Date.now() / 1000; }

  /** An entity arrives: made empty, then each field written, so the views' setters see it. */
  feed(eid, cls, props = {}) {
    if (this.f.get(eid) === 'null') this.f.load(eid, cls, '{}');
    for (const [k, v] of Object.entries(props)) this.f.write(eid, k, JSON.stringify(v ?? null), this.now());
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
    this.flush();
  }

  /** Load or replace a pack while the page runs. */
  pack(name, src) {
    try { this.f.set_pack(name, src); delete this.errors[name]; return null; }
    catch (e) { this.errors[name] = String(e); return this.errors[name]; }
  }

  get(eid) { return JSON.parse(this.f.get(eid)); }

  flush() {
    const out = JSON.parse(this.f.drain());
    this.pending.push(...out.writes);
    if (!this.frame) this.frame = this.schedule(() => this.apply());
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
    for (const eid of touched.keys()) this.node(eid);
    for (const [eid, props] of touched) {
      const el = this.node(eid);
      for (const [k, v] of Object.entries(props)) this.set(el, eid, k, v);
    }
  }

  node(eid) {
    const own = this.page.get(eid);
    if (own) return own;                                          // the page's element: the page owns its tag
    let el = this.nodes.get(eid);
    const e = this.get(eid) || {};
    const want = String(e.tag), ok = TAGS.has(want) || SVG_TAGS.has(want), tag = ok ? want : 'div';
    if (!ok && e.tag != null) this.refuse(el, eid, 'tag', `tag '${want}' is not in the allow-list (made a div)`);
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
      this.attach(el, e.parent);
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
      this.write(eid, 'key', { key: ev.key, shift: ev.shiftKey, ctrl: ev.ctrlKey, time: this.now() });
    }
    else if (ev.type === 'focus') put('focused', true);
    else if (ev.type === 'blur') put('focused', false);
    else if (ev.type === 'scroll') put('scroll', { top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight });
  }

  /** A name or tag the allow-list refuses: the reason is written on the DomNode and marked on the element. */
  refuse(el, eid, name, why) {
    const cur = (this.get(eid) || {}).refused || {};
    if (cur[name] === why) return;
    const next = { ...cur, [name]: why };
    this.f.write(eid, 'refused', JSON.stringify(next), this.now());
    if (el) el.setAttribute('data-refused', Object.keys(next).join(' ') + ': ' + Object.values(next).join('; '));
  }

  set(el, eid, prop, v) {
    if (prop === 'text') el.textContent = v == null ? '' : String(v);
    else if (prop === 'parent') this.attach(el, v);
    else if (prop === 'focused') {                                // a pack's focus(): the event that follows is no write (same value)
      if (v && document.activeElement !== el) el.focus(); else if (!v && document.activeElement === el) el.blur();
    }
    else if (prop === 'value' && VALUE_TAGS.has(el.tagName.toLowerCase())) {
      const s = v == null ? '' : String(v);
      if (el.value !== s) el.value = s;                           // the user's own text is left alone: the caret stays
    }
    else if (allowed(prop) || reason(prop, v)) {
      const why = reason(prop, v);
      if (why) { this.refuse(el, eid, prop, why); el.removeAttribute(prop); return; }
      if (v == null || v === false) { el.removeAttribute(prop); return; }
      el.setAttribute(prop, v === true ? '' : String(v));
    }
  }

  attach(el, parent) {
    const p = typeof parent === 'string' && parent.startsWith(NODE) ? this.node(parent) : this.root;
    if (el.parentNode !== p) p.appendChild(el);
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
    for (const e of [...snap.entities].sort((a, b) => order(a) - order(b))) host.feed(e.eid, e.cls, e.props);
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
        host.feed(e.eid, e.cls, e.props || { [e.prop]: e.new });
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
