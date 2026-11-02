/* k6: THE FIELD VIEW -- goals, tasks and decisions are objects IN the field3d scene (field3d.js workMarkers,
 * at their entity positions). This element only fetches GET /membrane/work-graph, hands it to the scene and
 * shows the clicked item's card. The real chat stays the chat. */
const GLYPH = { goal: "🎯", task: "🔧", decision: "❓" };

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
    constructor() { super(); this._graph = null; this._open = null; this._tab = 0; this._extra = null; }
    set api(a) { this._api = a; this.refresh(); }
    /** the scene the markers live in (a Field3DView): it gets every new graph, and calls open(id) on a click */
    set scene(sc) { this._scene = sc; if (this._graph && sc) sc.setWork(this._graph, (id) => this.open(id)); }
    open(id) { this._open = id; this._tab = 0; this._extra = null; this._draw(); }
    async refresh() {
      let g = null;
      try { g = await this._api.get("/membrane/work-graph"); } catch (e) { g = null; }
      const key = JSON.stringify(g);
      if (key !== this._gkey) {
        this._gkey = key; this._graph = g;
        if (this._scene) this._scene.setWork(g, (id) => this.open(id));
        this._draw();
      }
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
    disconnectedCallback() { clearTimeout(this._t); if (this._scene) this._scene.setWork(null); }
    _draw() {
      const card = this._open && cardInfo(this._graph, this._open);
      const list = (l) => (l && l.length ? l.map(esc).join(", ") : "–");
      const html = card ? `<div class="card" style="position:absolute;right:12px;top:12px;max-width:300px;padding:12px;border-radius:10px;background:rgba(20,20,30,.92);color:#eee;z-index:3;font:14px system-ui"><span class="x" style="float:right;cursor:pointer">✕</span><b style="display:block;margin-bottom:6px">${GLYPH[card.kind]} ${esc(card.title)}</b>
          <div>${TABS.map((t, k) => `<span class="tab" data-tab="${k}" style="cursor:pointer;margin-right:8px;${k === this._tab ? "text-decoration:underline" : ""}">${t}</span>`).join("")}</div>` +
          (this._tab === 0 ? `${esc(card.kind)} · ${esc(card.status)}<br>goal: ${esc(card.of || "–")}<br>
          blocks: ${list(card.blocks)}<br>blocked by: ${list(card.blockedBy)}` :
          this._tab === 1 ? chatRows(this._extra).map((r) => `<div>${esc(r)}</div>`).join("") + `<input class="say" placeholder="say…">` :
          logRows(this._extra).map((r) => `<div>${esc(r)}</div>`).join("")) + `</div>` : "";
      this.style.cssText = "position:absolute;inset:0;display:block;pointer-events:none";
      this.innerHTML = html;
      const say = this.querySelector && this.querySelector(".say");
      if (say) say.onkeydown = (e) => { if (e.key === "Enter") this._say(say.value.trim()); };
      this.onclick = (e) => {
        if (e.target.closest(".x")) { this._open = null; this._draw(); return; }
        const tb = e.target.closest(".tab"); if (tb) { this._tab = +tb.dataset.tab; this._draw(); this._load(); }
      };
      const c = this.querySelector && this.querySelector(".card"); if (c) c.style.pointerEvents = "auto";
    }
  }
  if (!customElements.get("membrane-fieldview")) customElements.define("membrane-fieldview", MembraneFieldView);
}

const FieldViewLogic = { cardInfo, edgeStyles, chatStep, GLYPH, TABS, chatRows, logRows };
if (typeof module !== "undefined" && module.exports) module.exports = FieldViewLogic;
