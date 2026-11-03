/* k6: THE FIELD VIEW -- goals, tasks and decisions are objects IN the field3d scene (field3d.js workMarkers,
 * at their entity positions). This element only fetches GET /membrane/work-graph, hands it to the scene and
 * shows the clicked item's card. The real chat stays the chat. */
const GLYPH = { goal: "🎯", task: "🔧", decision: "❓", wait: "⏳" };

/** what a clicked icon's card shows */
function cardInfo(graph, id) {
  const nodes = (graph && graph.nodes) || [], edges = (graph && graph.edges) || [];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const n = byId[id]; if (!n) return null;
  const t = (e) => (byId[e] ? byId[e].title : e);
  const of = edges.find((e) => e.from === id && e.rel === "of");
  return { id, kind: n.kind, title: n.title, status: n.status,
    of: of ? t(of.to) : null,
    waitingOn: (n.waiting_on || []).map((w) => w.title + " (" + w.reason + ")"),
    blocks: edges.filter((e) => e.from === id && e.rel === "blocks").map((e) => t(e.to)),
    blockedBy: edges.filter((e) => e.to === id && e.rel === "blocks").map((e) => t(e.from)) };
}

const PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#008080", "#9a6324", "#800000"];
const DASHES = ["", "6 4", "2 3", "10 3 2 3"];
const GOAL_DONE = new Set(["fulfilled", "done", "dropped", "cancelled"]);

function slotHash(id) {
  let h = 2166136261;
  for (const c of String(id)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
  return h;
}

/** #772: one edge style per LIVE goal ({color, dash}); finished goals take no slot. A goal's slot comes from its id (probing past
 *  a taken one), so colours stay put when other goals appear or finish. */
function edgeStyles(graph) {
  const out = {}, taken = new Set(), N = PALETTE.length * DASHES.length;
  const live = ((graph && graph.nodes) || []).filter((n) => n.kind === "goal" && !GOAL_DONE.has(n.status))
    .map((g) => [slotHash(g.id), g.id]).sort((x, y) => x[0] - y[0] || (x[1] < y[1] ? -1 : 1));
  for (const [h, id] of live) {
    let k = h % N;
    while (taken.has(k) && taken.size < N) k = (k + 1) % N;
    taken.add(k);
    out[id] = { color: PALETTE[k % PALETTE.length], dash: DASHES[Math.floor(k / PALETTE.length) % DASHES.length] };
  }
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

/** #772: the goal window's size: larger than the old 300px card, one scrollable view */
const WINDOW = { width: 560, scroll: true };

/** escape, then turn http(s) URLs into clickable links */
function linkify(text) {
  return esc(text).replace(/https?:\/\/[^\s<]+?(?=[.,;:!?)]*(?:\s|$|&lt;))/g,
    (u) => `<a href="${u}" target="_blank" rel="noopener" style="color:#8ab4ff">${u}</a>`);
}

/** #772: entity ids in free text become clickable titles (data-eid); ids the graph does not know stay as escaped text */
const EID_RE = /membrane::[A-Za-z0-9_.:-]*[A-Za-z0-9_]/g;
function mentionHtml(graph, text, selId) {
  const byId = Object.fromEntries((((graph && graph.nodes) || [])).map((n) => [n.id, n]));
  const src = String(text == null ? "" : text);
  let out = "", at = 0, m;
  EID_RE.lastIndex = 0;
  while ((m = EID_RE.exec(src))) {
    const n = byId[m[0]];
    out += linkify(src.slice(at, m.index));
    out += n && n.id === selId ? esc(n.title)
      : n ? `<a class="ment" data-eid="${esc(n.id)}" href="#" style="color:#8ab4ff;cursor:pointer">${esc(n.title)}</a>` : esc(m[0]);
    at = m.index + m[0].length;
  }
  return out + linkify(src.slice(at));
}

/** #772: blocks / blocked by / waiting on as .ment links (data-eid); the selected entity stays plain title text */
function relHtml(graph, id) {
  const nodes = (graph && graph.nodes) || [], edges = (graph && graph.edges) || [];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const n = byId[id];
  const one = (eid, title, extra) => {
    const t = esc(title != null ? title : (byId[eid] ? byId[eid].title : eid));
    const x = extra ? " (" + esc(extra) + ")" : "";
    return eid === id ? t + x : `<a class="ment" data-eid="${esc(eid)}" href="#" style="color:#8ab4ff;cursor:pointer">${t}</a>${x}`;
  };
  const join = (a) => a.join(", ");
  return {
    blocks: join(edges.filter((e) => e.from === id && e.rel === "blocks").map((e) => one(e.to))),
    blockedBy: join(edges.filter((e) => e.to === id && e.rel === "blocks").map((e) => one(e.from))),
    goal: join(edges.filter((e) => e.from === id && e.rel === "of").slice(0, 1).map((e) => one(e.to))),
    waitingOn: join(((n && n.waiting_on) || []).map((w) => one(w.id, w.title, w.reason))),
  };
}

/** #772: everything the window shows: full text (links), info, chat rows, log rows */
function windowInfo(graph, id, history, chat) {
  const info = cardInfo(graph, id); if (!info) return null;
  const p = (history && history.properties) || {};
  const text = String(p.summary || p.text || p.description || info.title || "");
  return { info, rel: relHtml(graph, id), text, textHtml: mentionHtml(graph, text, id), chat: chatRows(chat), logs: logRows(history),
    chatHtml: chatRows(chat).map((r) => mentionHtml(graph, r, id)), logHtml: logRows(history).map((r) => mentionHtml(graph, r, id)) };
}

if (typeof customElements !== "undefined" && typeof HTMLElement !== "undefined") {
  class MembraneFieldView extends HTMLElement {
    constructor() { super(); this._graph = null; this._open = null; this._tab = 0; this._extra = null; }
    set api(a) { this._api = a; this.refresh(); }
    /** the scene the markers live in (a Field3DView): it gets every new graph, and calls open(id) on a click */
    set scene(sc) { this._scene = sc; if (this._graph && sc) sc.setWork(this._graph, (id) => this.open(id)); }
    open(id) {
      if (id && typeof id === "object") { this._run = id; this._open = null; this._draw(); return; }   // #843: a flown run: its code, rc, output
      this._run = null; this._open = id; this._hist = null; this._chat = null; this._draw(); this._load();
    }
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
      const id = this._open; if (!id) return;
      const q = encodeURIComponent(id);
      try { this._hist = await this._api.get("/membrane/entity-history?spec=" + q); } catch (e) { this._hist = null; }
      try { this._chat = await this._api.get("/membrane/item-chat?spec=" + q); } catch (e) { this._chat = null; }
      this._draw();
    }
    async _say(text) {
      if (!text || !this._open) return;
      try { await this._api.post("/membrane/item-chat/say", { spec: this._open, text }); } catch (e) { /* shown by reload */ }
      this._load();
    }
    disconnectedCallback() { clearTimeout(this._t); if (this._scene) this._scene.setWork(null); }
    _draw() {
      const w = this._open && windowInfo(this._graph, this._open, this._hist, this._chat);
      const list = (h) => h || "–";
      const sec = (h) => `<b style="display:block;margin:10px 0 4px;opacity:.7">${h}</b>`;
      const html = w ? `<div class="fvwin" style="position:absolute;right:12px;top:12px;bottom:12px;width:${WINDOW.width}px;max-width:calc(100% - 24px);overflow-y:auto;padding:14px;border-radius:10px;background:rgba(20,20,30,.95);color:#eee;z-index:3;font:14px system-ui"><span class="x" style="float:right;cursor:pointer">✕</span><b style="display:block;margin-bottom:6px">${GLYPH[w.info.kind]} ${esc(w.info.title)}</b>
          <div style="white-space:pre-wrap">${w.textHtml}</div>` +
          sec("Info") + `${esc(w.info.kind)} · ${esc(w.info.status)}<br>goal: ${list(w.rel.goal)}<br>${w.rel.waitingOn ? `<b style="color:#ff6b6b">blocked — waiting on: ${list(w.rel.waitingOn)}</b><br>` : ""}blocks: ${list(w.rel.blocks)}<br>blocked by: ${list(w.rel.blockedBy)}` +
          sec("Chat") + w.chatHtml.map((r) => `<div>${r}</div>`).join("") + `<input class="say" placeholder="say…" style="width:100%">` +
          sec("Logs") + w.logHtml.map((r) => `<div style="font-size:12px">${r}</div>`).join("") + `</div>` : "";
      const r = this._run;
      const rhtml = r ? `<div class="fvwin" style="position:absolute;right:12px;top:12px;bottom:12px;width:${WINDOW.width}px;max-width:calc(100% - 24px);overflow-y:auto;padding:14px;border-radius:10px;background:rgba(20,20,30,.95);color:#eee;z-index:3;font:14px system-ui"><span class="x" style="float:right;cursor:pointer">✕</span><b>rc ${esc(String(r.rc))}</b>${sec("Code")}<pre style="white-space:pre-wrap">${esc(r.code || "")}</pre>${sec("Output")}<pre style="white-space:pre-wrap">${esc(r.output || "")}</pre></div>` : "";
      this.style.cssText = "position:absolute;inset:0;display:block;pointer-events:none";
      this.innerHTML = html || rhtml;
      const say = this.querySelector && this.querySelector(".say");
      if (say) say.onkeydown = (e) => { if (e.key === "Enter") this._say(say.value.trim()); };
      this.onclick = (e) => {
        const a = e.target.closest && e.target.closest(".ment");
        if (a) {
          if (e.preventDefault) e.preventDefault();
          const eid = a.getAttribute("data-eid"); this.open(eid);
          const sc = this._scene; if (sc) { sc.select(eid); if (sc.api && sc.api.onSelect) sc.api.onSelect(eid); }
          return;
        }
        if (e.target.closest(".x")) { this._open = null; this._run = null; this._draw(); const sc = this._scene; if (sc) { sc.select(null); if (sc.api && sc.api.onSelect) sc.api.onSelect(null); } return; }
      };
      const c = this.querySelector && this.querySelector(".fvwin"); if (c) c.style.pointerEvents = "auto";
    }
  }
  if (!customElements.get("membrane-fieldview")) customElements.define("membrane-fieldview", MembraneFieldView);
}

const FieldViewLogic = { cardInfo, linkify, mentionHtml, relHtml, windowInfo, WINDOW, edgeStyles, chatStep, GLYPH, TABS, chatRows, logRows };
if (typeof window !== "undefined") window.FieldViewLogic = FieldViewLogic;
if (typeof module !== "undefined" && module.exports) module.exports = FieldViewLogic;
