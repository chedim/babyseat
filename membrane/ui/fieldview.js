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

const PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45"];
const DASHES = ["", "6 4", "2 3", "10 3 2 3"];

/** k8: one edge style per goal ({color, dash}), stable by the goal's order in the graph. */
function edgeStyles(graph) {
  const out = {};
  ((graph && graph.nodes) || []).filter((n) => n.kind === "goal").forEach((g, i) => {
    out[g.id] = { color: PALETTE[i % PALETTE.length], dash: DASHES[Math.floor(i / PALETTE.length) % DASHES.length] };
  });
  return out;
}

/** k8: the bottom chat bar's log. 'send' trims the text (blank sends nothing); 'reply' adds the answer. */
function chatStep(state, act, text) {
  const log = ((state && state.log) || []).slice();
  const t = String(text == null ? "" : text).trim();
  if (act === "send") {
    if (!t) return { log, send: null };
    log.push({ who: "me", text: t });
    return { log, send: t };
  }
  if (act === "reply" && t) log.push({ who: "bot", text: t });
  return { log, send: null };
}

/** k7: the card's tabs */
const TABS = ["Info", "Chat", "Logs"];

/** Chat tab rows: "who: text" for each message that refers to the item */
function chatRows(chat) {
  return ((chat && chat.messages) || []).map((m) => (m.who ? m.who + ": " : "") + m.text);
}

/** Logs tab rows from GET /membrane/entity-history: properties, changes, relations */
function logRows(h) {
  if (!h) return [];
  const rows = [];
  for (const [k, v] of Object.entries(h.properties || {})) {
    rows.push(k + ": " + (typeof v === "string" ? v : JSON.stringify(v)));
  }
  for (const c of h.changes || []) rows.push("#" + c.seq + " " + (c.stream || "") + " " + (c.text || ""));
  for (const r of h.relations || []) rows.push(r.subj + " -" + r.rel + "-> " + r.obj + (r.superseded ? " (past)" : ""));
  return rows;
}

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

if (typeof customElements !== "undefined" && typeof HTMLElement !== "undefined") {
  class MembraneFieldView extends HTMLElement {
    constructor() { super(); this.attachShadow({ mode: "open" }); this._graph = null; this._open = null; this._tab = 0; this._extra = null; this._chat = { log: [] }; this._draft = ""; }
    /** the answer to a chat-send lands in the response window */
    reply(text) { this._chat = chatStep(this._chat, "reply", text); this._draw(); }
    set api(a) { this._api = a; this.refresh(); }
    async refresh() {
      try { this._graph = await this._api.get("/membrane/work-graph"); } catch (e) { this._graph = null; }
      this._draw();
      clearTimeout(this._t); this._t = setTimeout(() => this.refresh(), 10000);
    }
    async _load() {
      const id = this._open, tab = TABS[this._tab];
      this._extra = null;
      if (!id || tab === "Info") return;
      const url = tab === "Chat" ? "/membrane/item-chat?spec=" : "/membrane/entity-history?spec=";
      try { this._extra = await this._api.get(url + encodeURIComponent(id)); } catch (e) { this._extra = null; }
      this._draw();
    }
    async _say(text) {
      if (!text || !this._open) return;
      try { await this._api.post("/membrane/item-chat/say", { spec: this._open, text }); } catch (e) { /* shown by reload */ }
      this._load();
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
          <div>${TABS.map((t, k) => `<span class="tab" data-tab="${k}" style="cursor:pointer;margin-right:8px;${k === this._tab ? "text-decoration:underline" : ""}">${t}</span>`).join("")}</div>` +
          (this._tab === 0 ? `${esc(card.kind)} · ${esc(card.status)}<br>goal: ${esc(card.of || "–")}<br>
          blocks: ${list(card.blocks)}<br>blocked by: ${list(card.blockedBy)}` :
          this._tab === 1 ? chatRows(this._extra).map((r) => `<div>${esc(r)}</div>`).join("") + `<input class="say" placeholder="say…">` :
          logRows(this._extra).map((r) => `<div>${esc(r)}</div>`).join("")) + `</div>` : "");
      const say = this.shadowRoot.querySelector(".say");
      if (say) say.onkeydown = (e) => { if (e.key === "Enter") this._say(say.value.trim()); };
      const sr = this.shadowRoot, pos = Object.fromEntries(icons.map((i) => [i.id, i]));
      const st = edgeStyles(this._graph), goalOf = Object.fromEntries(((this._graph && this._graph.nodes) || []).map((n) => [n.id, n.kind === "goal" ? n.id : n.goal]));
      const lines = (((this._graph && this._graph.edges) || [])).filter((e) => pos[e.from] && pos[e.to]).map((e) => {
        const s = st[goalOf[e.from]] || { color: "#888", dash: "" };
        return `<line x1="${pos[e.from].x}" y1="${pos[e.from].y}" x2="${pos[e.to].x}" y2="${pos[e.to].y}" stroke="${s.color}" stroke-width="2" stroke-dasharray="${s.dash}"/>`;
      }).join("");
      sr.innerHTML += `<svg style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0">${lines}</svg>` +
        `<div class="chatbar" style="position:absolute;left:0;right:0;bottom:0;z-index:4;background:rgba(20,20,30,.92);color:#eee;padding:6px">` +
        `<div class="reply-window" style="max-height:30vh;overflow:auto">${(this._chat.log).map((m) => `<div>${m.who === "me" ? "▸" : "◂"} ${esc(m.text)}</div>`).join("")}</div>` +
        `<input class="ci" style="width:100%;box-sizing:border-box" placeholder="ask…" value="${esc(this._draft || "")}"></div>`;
      const ci = sr.querySelector(".ci");
      ci.oninput = () => { this._draft = ci.value; };
      ci.onkeydown = (e) => {
        if (e.key !== "Enter") return;
        this._chat = chatStep(this._chat, "send", ci.value);
        if (this._chat.send) { const t = this._chat.send; this._draft = ""; this.dispatchEvent(new CustomEvent("chat-send", { detail: t })); }
        this._draw();
      };
      this.shadowRoot.onclick = (e) => {
        if (e.target.closest(".x")) { this._open = null; this._draw(); return; }
        const tb = e.target.closest(".tab"); if (tb) { this._tab = +tb.dataset.tab; this._draw(); this._load(); return; }
        const el = e.target.closest(".i"); if (el) { this._open = el.dataset.id; this._tab = 0; this._extra = null; this._draw(); }
      };
    }
  }
  if (!customElements.get("membrane-fieldview")) customElements.define("membrane-fieldview", MembraneFieldView);
}

const FieldViewLogic = { scale, layout, cardInfo, edgeStyles, chatStep, GLYPH, TABS, chatRows, logRows };
if (typeof module !== "undefined" && module.exports) module.exports = FieldViewLogic;
