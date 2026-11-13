// A small force-directed graph on a canvas: charge repulsion, spring edges, a pull to the centre, pan, zoom, node drag, click to select.
// Node positions live in the node objects, so a refresh that keeps a node keeps where it is. No dependencies.

const TAU = Math.PI * 2;

export function classColor(cls, dark) {
  let h = 0;
  for (let i = 0; i < cls.length; i++) h = (h * 31 + cls.charCodeAt(i)) % 360;
  return `hsl(${h} ${dark ? 60 : 55}% ${dark ? 62 : 42}%)`;
}

export class Graph {
  constructor(canvas, { onSelect }) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.onSelect = onSelect;
    this.nodes = new Map();   // eid -> {id, cls, x, y, vx, vy, deg}
    this.edges = [];          // [{a, b, kind}] kind: 'own' | 'ref'
    this.selected = null;
    this.hidden = new Set();  // eids filtered out of view
    this.view = { x: 0, y: 0, k: 1 };
    this.alpha = 1;
    this.drag = null;
    this.raf = 0;
    this.dark = matchMedia('(prefers-color-scheme: dark)');
    this.dark.addEventListener('change', () => this.draw());
    this.resize();
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.bind();
  }

  /** rows: [{eid, cls}]; edges: [{a, b, kind}] by eid. Existing nodes keep their positions; new ones start near a neighbour. */
  setData(rows, edges) {
    const seen = new Set();
    for (const r of rows) {
      seen.add(r.eid);
      let n = this.nodes.get(r.eid);
      if (!n) {
        const near = edges.find((e) => (e.a === r.eid && this.nodes.has(e.b)) || (e.b === r.eid && this.nodes.has(e.a)));
        const o = near ? this.nodes.get(near.a === r.eid ? near.b : near.a) : null;
        n = { id: r.eid, cls: r.cls, x: (o ? o.x : 0) + (Math.random() - 0.5) * 40, y: (o ? o.y : 0) + (Math.random() - 0.5) * 40, vx: 0, vy: 0, deg: 0 };
        this.nodes.set(r.eid, n);
        this.alpha = Math.max(this.alpha, 0.5);
      }
      n.cls = r.cls;
    }
    for (const id of [...this.nodes.keys()]) if (!seen.has(id)) { this.nodes.delete(id); if (this.selected === id) this.select(null); }
    const same = edges.length === this.edges.length && edges.every((e, i) => e.a === this.edges[i].a && e.b === this.edges[i].b);
    this.edges = edges.filter((e) => this.nodes.has(e.a) && this.nodes.has(e.b));
    for (const n of this.nodes.values()) n.deg = 0;
    for (const e of this.edges) { this.nodes.get(e.a).deg++; this.nodes.get(e.b).deg++; }
    if (!same) this.alpha = Math.max(this.alpha, 0.3);
    this.kick();
  }

  setHidden(set) { this.hidden = set; this.draw(); }
  select(id) { this.selected = id; this.onSelect(id); this.draw(); }
  kick() { if (!this.raf) this.raf = requestAnimationFrame(() => this.frame()); }

  resize() {
    const r = this.cv.getBoundingClientRect(), d = devicePixelRatio || 1;
    this.w = r.width; this.h = r.height;
    this.cv.width = Math.max(1, Math.round(r.width * d));
    this.cv.height = Math.max(1, Math.round(r.height * d));
    this.draw();
  }

  // ---- the layout: one step of charge, springs and centring ----
  step() {
    const ns = [...this.nodes.values()].filter((n) => !this.hidden.has(n.id));
    const a = this.alpha;
    for (let i = 0; i < ns.length; i++) {
      const p = ns[i];
      for (let j = i + 1; j < ns.length; j++) {
        const q = ns[j];
        let dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        if (d2 > 90000) continue;                       // charge reaches 300 px
        const f = (1800 * a) / d2, d = Math.sqrt(d2);
        p.vx += (dx / d) * f; p.vy += (dy / d) * f; q.vx -= (dx / d) * f; q.vy -= (dy / d) * f;
      }
    }
    for (const e of this.edges) {
      const p = this.nodes.get(e.a), q = this.nodes.get(e.b);
      if (this.hidden.has(p.id) || this.hidden.has(q.id)) continue;
      const dx = q.x - p.x, dy = q.y - p.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = e.kind === 'own' ? 50 : 90, f = (d - rest) * 0.04 * a;
      p.vx += (dx / d) * f; p.vy += (dy / d) * f; q.vx -= (dx / d) * f; q.vy -= (dy / d) * f;
    }
    for (const n of ns) {
      n.vx -= n.x * 0.012 * a; n.vy -= n.y * 0.012 * a;
      if (this.drag && this.drag.node === n) { n.vx = 0; n.vy = 0; continue; }
      n.vx *= 0.6; n.vy *= 0.6; n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= 0.985;
  }

  frame() {
    this.raf = 0;
    if (this.alpha > 0.02 || this.drag) { this.step(); this.draw(); this.kick(); } else this.draw();
  }

  // ---- drawing ----
  radius(n) { return 4 + Math.min(8, Math.sqrt(n.deg) * 2); }
  toScreen(x, y) { return [x * this.view.k + this.w / 2 + this.view.x, y * this.view.k + this.h / 2 + this.view.y]; }
  toWorld(sx, sy) { return [(sx - this.w / 2 - this.view.x) / this.view.k, (sy - this.h / 2 - this.view.y) / this.view.k]; }

  draw() {
    const c = this.ctx, d = devicePixelRatio || 1, dark = this.dark.matches;
    const css = getComputedStyle(this.cv);
    const edge = css.getPropertyValue('--edge').trim() || '#888', ink = css.getPropertyValue('--ink').trim() || '#000', acc = css.getPropertyValue('--accent').trim() || '#06f';
    c.setTransform(d, 0, 0, d, 0, 0);
    c.clearRect(0, 0, this.w, this.h);
    c.lineWidth = 1;
    for (const e of this.edges) {
      const p = this.nodes.get(e.a), q = this.nodes.get(e.b);
      if (this.hidden.has(p.id) || this.hidden.has(q.id)) continue;
      const [x1, y1] = this.toScreen(p.x, p.y), [x2, y2] = this.toScreen(q.x, q.y);
      c.strokeStyle = edge; c.globalAlpha = e.kind === 'own' ? 0.55 : 0.9;
      c.setLineDash(e.kind === 'ref' ? [4, 3] : []);
      c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke();
    }
    c.setLineDash([]); c.globalAlpha = 1;
    c.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace'; c.textBaseline = 'middle';
    for (const n of this.nodes.values()) {
      if (this.hidden.has(n.id)) continue;
      const [x, y] = this.toScreen(n.x, n.y), r = this.radius(n) * Math.min(1.6, Math.max(0.6, this.view.k));
      c.fillStyle = classColor(n.cls, dark);
      c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
      if (n.id === this.selected) { c.lineWidth = 2.5; c.strokeStyle = acc; c.stroke(); c.lineWidth = 1; }
      if (this.view.k > 0.7 || n.id === this.selected) { c.fillStyle = ink; c.fillText(n.id.length > 34 ? n.id.slice(0, 33) + '…' : n.id, x + r + 4, y); }
    }
  }

  // ---- interaction ----
  hit(sx, sy) {
    const [wx, wy] = this.toWorld(sx, sy);
    let best = null, bd = Infinity;
    for (const n of this.nodes.values()) {
      if (this.hidden.has(n.id)) continue;
      const d = Math.hypot(n.x - wx, n.y - wy) * this.view.k, r = this.radius(n) + 4;
      if (d <= r && d < bd) { best = n; bd = d; }
    }
    return best;
  }

  zoomAt(sx, sy, f) {
    const [wx, wy] = this.toWorld(sx, sy), k = Math.min(6, Math.max(0.1, this.view.k * f));
    this.view.k = k;
    this.view.x = sx - this.w / 2 - wx * k; this.view.y = sy - this.h / 2 - wy * k;
    this.draw();
  }

  fit() {
    const ns = [...this.nodes.values()].filter((n) => !this.hidden.has(n.id));
    if (!ns.length) { this.view = { x: 0, y: 0, k: 1 }; return this.draw(); }
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of ns) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); }
    const k = Math.min(2, Math.max(0.1, Math.min((this.w - 80) / Math.max(40, x1 - x0), (this.h - 80) / Math.max(40, y1 - y0))));
    this.view = { k, x: -((x0 + x1) / 2) * k, y: -((y0 + y1) / 2) * k };
    this.draw();
  }

  bind() {
    const cv = this.cv, pos = (e) => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    cv.addEventListener('pointerdown', (e) => {
      const [sx, sy] = pos(e), n = this.hit(sx, sy);
      this.drag = { node: n, sx, sy, vx: this.view.x, vy: this.view.y, moved: false };
      cv.setPointerCapture(e.pointerId); cv.focus();
      if (n) { this.alpha = Math.max(this.alpha, 0.3); this.kick(); }
    });
    cv.addEventListener('pointermove', (e) => {
      const d = this.drag; if (!d) return;
      const [sx, sy] = pos(e);
      if (Math.hypot(sx - d.sx, sy - d.sy) > 3) d.moved = true;
      if (!d.moved) return;
      if (d.node) { const [wx, wy] = this.toWorld(sx, sy); d.node.x = wx; d.node.y = wy; }
      else { this.view.x = d.vx + sx - d.sx; this.view.y = d.vy + sy - d.sy; this.draw(); }
    });
    const up = () => { const d = this.drag; this.drag = null; if (d && !d.moved) this.select(d.node ? d.node.id : null); };
    cv.addEventListener('pointerup', up);
    cv.addEventListener('pointercancel', () => { this.drag = null; });
    cv.addEventListener('wheel', (e) => { e.preventDefault(); const [sx, sy] = pos(e); this.zoomAt(sx, sy, e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
    cv.addEventListener('keydown', (e) => {
      const step = 40, k = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
      if (k) { this.view.x += k[0]; this.view.y += k[1]; this.draw(); e.preventDefault(); }
      else if (e.key === '+' || e.key === '=') this.zoomAt(this.w / 2, this.h / 2, 1.2);
      else if (e.key === '-') this.zoomAt(this.w / 2, this.h / 2, 1 / 1.2);
      else if (e.key === '0') this.fit();
      else if (e.key === 'Escape') this.select(null);
    });
  }
}
