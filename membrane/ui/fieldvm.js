// The field engine in the page: the same Rust VM as the seat (getters, setters, `.c1f` packs, imports), compiled
// to WebAssembly (fieldvm_core.js / fieldvm_core_bg.wasm, built by native/build-web.sh).
//
// The DOM is a mount of the field: every `DomNode:<key>` entity is an element, keyed by its id.
//   tag     the element's tag (from an allow-list; `div` otherwise), set when the element is made
//   text    its text (textContent only: a pack can never write HTML)
//   parent  the DomNode it sits in (the mount's root when empty)
//   class, title, role, hidden, alt, href (http/https only), aria-label: attributes, from an allow-list
// A click on an element writes its `clicked` (the time). A round's DOM writes are applied together, in one frame.
//
// Data enters as writes (`feed`): an entity is made empty, then each field is written, so the views' setters
// fire on it (a birth's own getters compute silently, as everywhere).
import init, { Field } from './fieldvm_core.js';

export const DOM_PACK = "PACK dom 1\n-- the page's DOM as entities: DomNode:<key>\nDomNode.tag getter: this.tag or 'div'\n";

const TAGS = new Set(['div', 'span', 'p', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'button', 'a', 'section', 'header',
  'footer', 'small', 'strong', 'em', 'code', 'pre', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'label', 'details', 'summary']);
const ATTRS = new Set(['class', 'title', 'role', 'hidden', 'alt', 'href', 'aria-label']);
const NODE = 'DomNode:';

export async function start(root, { packs = {}, wasm } = {}) {
  await init(wasm ? { module_or_path: wasm } : undefined);
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
  }

  now() { return Date.now() / 1000; }

  /** An entity arrives: made empty, then each field written, so the views' setters see it. */
  feed(eid, cls, props = {}) {
    if (this.f.get(eid) === 'null') this.f.load(eid, cls, '{}');
    for (const [k, v] of Object.entries(props)) this.f.write(eid, k, JSON.stringify(v ?? null), this.now());
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
    if (!this.frame) this.frame = requestAnimationFrame(() => this.apply());
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
    let el = this.nodes.get(eid);
    const e = this.get(eid) || {};
    const tag = TAGS.has(String(e.tag)) ? String(e.tag) : 'div';
    if (el && el.tagName.toLowerCase() !== tag) {                 // its tag changed: a new element in its place
      const nu = document.createElement(tag);
      for (const a of [...el.attributes]) nu.setAttribute(a.name, a.value);
      nu.append(...el.childNodes);
      el.replaceWith(nu);
      this.listen(nu, eid);
      this.nodes.set(eid, nu);
      return nu;
    }
    if (!el) {
      el = document.createElement(tag);
      el.dataset.key = eid;
      this.listen(el, eid);
      this.nodes.set(eid, el);
      this.attach(el, e.parent);
    }
    return el;
  }

  listen(el, eid) { el.addEventListener('click', ev => { ev.stopPropagation(); this.write(eid, 'clicked', this.now()); }); }

  set(el, eid, prop, v) {
    if (prop === 'text') el.textContent = v == null ? '' : String(v);
    else if (prop === 'parent') this.attach(el, v);
    else if (ATTRS.has(prop)) {
      if (v == null || v === false) { el.removeAttribute(prop); return; }
      if (prop === 'href' && !/^https?:\/\//i.test(String(v))) return;
      el.setAttribute(prop, v === true ? '' : String(v));
    }
  }

  attach(el, parent) {
    const p = typeof parent === 'string' && parent.startsWith(NODE) ? this.node(parent) : this.root;
    if (el.parentNode !== p) p.appendChild(el);
  }
}
