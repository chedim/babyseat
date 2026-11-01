/* k6: THE FIELD VIEW -- goals, tasks and decisions as icons on the field. It draws ONLY
 * what GET /membrane/work-graph answers (k4). Hover grows an icon; click opens a card. */
const GLYPH = { goal: "🎯", task: "🔧", decision: "❓" };
const RING = { goal: 0.0, task: 0.55, decision: 0.9 };

/** hovered icons grow */
function scale(hover) { return hover ? 1.6 : 1; }

/** icons from the graph: goals at the centre, tasks on a middle ring, decisions outside it. */
function layout(graph, w, h) {
  const cx = w / 2, cy = h / 2, r = Math.min(w, h) * 0.42;
  const by = { goal: [], task: [], decision: [] };
  for (const n of (graph && graph.nodes) || []) if (by[n.kind]) by[n.kind].push(n);
  const out = [];
  for (const kind of ["goal", "task", "decision"]) {
    const ns = by[kind];
    ns.forEach((n, i) => {
      let x = cx, y = cy;
      const rad = r * RING[kind];
      if (kind === "goal" && ns.length === 1) { /* centre */ }
      else {
        const rr = kind === "goal" ? r * 0.25 : rad;
        const a = (2 * Math.PI * i) / ns.length + (kind === "decision" ? Math.PI / ns.length : 0);
        x = cx + rr * Math.cos(a); y = cy + rr * Math.sin(a);
      }
      out.push({ id: n.id, kind, glyph: GLYPH[kind], title: n.title, status: n.status,
        x: Math.max(0, Math.min(w, x)), y: Math.max(0, Math.min(h, y)) });
    });
  }
  return out;
}

/** what a clicked icon's card shows */
function cardInfo(graph, id) {
  const nodes = (graph && graph.nodes) || [], edges = (graph && graph.edges) || [];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const n = byId[id]; if (!n) return null;
  const t = (e) => (byId[e] ? byId[e].title : e);
  const of = edges.find((e) => e.from === id && e.rel === "of");
  return { id, kind: n.kind, title: n.title, status: n.status,
    of: of ? t(of.to) : null,
    blocks: edges.filter((e) => e.from === id && e.rel === "blocks").map((e) => t(e.to)),
    blockedBy: edges.filter((e) => e.to === id && e.rel === "blocks").map((e) => t(e.from)) };
}

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

if (typeof customElements !== "undefined" && typeof HTMLElement !== "undefined") {
  class MembraneFieldView extends HTMLElement {
    constructor() { super(); this.attachShadow({ mode: "open" }); this._graph = null; this._open = null; }
    set api(a) { this._api = a; this.refresh(); }
    async refresh() {
      try { this._graph = await this._api.get("/membrane/work-graph"); } catch (e) { this._graph = null; }
      this._draw();
      clearTimeout(this._t); this._t = setTimeout(() => this.refresh(), 10000);
    }
    disconnectedCallback() { clearTimeout(this._t); }
    _draw() {
      const w = this.clientWidth || 800, h = this.clientHeight || 600;
      const icons = layout(this._graph, w, h);
      const card = this._open && cardInfo(this._graph, this._open);
      const list = (l) => (l && l.length ? l.map(esc).join(", ") : "–");
      this.shadowRoot.innerHTML = `<style>
        :host{position:absolute;inset:0;display:block;font:14px system-ui}
        .i{position:absolute;transform:translate(-50%,-50%) scale(1);font-size:26px;cursor:pointer;
           transition:transform .15s;user-select:none}
        .i:hover{transform:translate(-50%,-50%) scale(${scale(true)});z-index:2}
        .card{position:absolute;right:12px;top:12px;max-width:300px;padding:12px;border-radius:10px;
           background:rgba(20,20,30,.92);color:#eee;z-index:3}
        .card b{display:block;margin-bottom:6px}.x{float:right;cursor:pointer}
      </style>` + icons.map((i) => `<div class="i" data-id="${esc(i.id)}" title="${esc(i.title)}"
        style="left:${i.x}px;top:${i.y}px">${i.glyph}</div>`).join("") +
        (card ? `<div class="card"><span class="x">✕</span><b>${GLYPH[card.kind]} ${esc(card.title)}</b>
          ${esc(card.kind)} · ${esc(card.status)}<br>goal: ${esc(card.of || "–")}<br>
          blocks: ${list(card.blocks)}<br>blocked by: ${list(card.blockedBy)}</div>` : "");
      this.shadowRoot.onclick = (e) => {
        if (e.target.closest(".x")) { this._open = null; this._draw(); return; }
        const el = e.target.closest(".i"); if (el) { this._open = el.dataset.id; this._draw(); }
      };
    }
  }
  if (!customElements.get("membrane-fieldview")) customElements.define("membrane-fieldview", MembraneFieldView);
}

const FieldViewLogic = { scale, layout, cardInfo, GLYPH };
if (typeof module !== "undefined" && module.exports) module.exports = FieldViewLogic;
