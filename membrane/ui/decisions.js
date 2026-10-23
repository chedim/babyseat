/* THE DECISION BOARD (#470 step 2): the project decision map as a reversed
 * kanban -- "how did we get here -> what are we doing -> what do we need to do
 * next". The cards are Decision entities (GET /membrane/decisions); they move
 * right to left: open (a question) -> in_flight (the work) -> decided (the past).
 *
 * An open card asks for ONE variable, pre-filled with the agent's pick; the
 * person answers it on the card (POST /membrane/decision/answer). Clicking the
 * card itself opens its chat (no small buttons): the card's own session, or a
 * new one titled after it, with a message about the decision pre-filled.
 *
 * The pure helpers (DecisionLogic) run under node for the tests; the element
 * <membrane-decisions> is defined only in a browser. */

const BOARD_PATH = "/membrane/decisions";
const ANSWER_PATH = "/membrane/decision/answer";
const POLL_MS = 5000;               // the board re-reads the seat this often while shown
const COLS = [
  { key: "decided", title: "How did we get here", sub: "decided" },
  { key: "in_flight", title: "What are we doing", sub: "in flight" },
  { key: "open", title: "What do we need to do next", sub: "to decide" },
];

/** The cards by column. Open cards: a card that blocks another comes above it
 * (the first thing to decide on top), then by title; in flight: by title;
 * decided: the newest first. */
function columns(board) {
  const cards = (board && board.cards) || [];
  const out = { decided: [], in_flight: [], open: [] };
  for (const c of cards) (out[c.status] || out.decided).push(c);
  out.in_flight.sort((a, b) => String(a.title).localeCompare(String(b.title)));
  out.decided.sort((a, b) => String(b.set_at || "").localeCompare(String(a.set_at || "")) ||
    String(a.title).localeCompare(String(b.title)));
  out.open = blockersFirst(out.open, (board && board.edges) || []);
  return out;
}

function blockersFirst(cards, edges) {
  const ids = new Set(cards.map((c) => c.eid));
  const blockedBy = new Map(cards.map((c) => [c.eid, new Set()]));
  for (const e of edges) {
    if (e.rel === "blocks" && ids.has(e.from) && ids.has(e.to) && e.from !== e.to) blockedBy.get(e.to).add(e.from);
  }
  const byTitle = (a, b) => String(a.title).localeCompare(String(b.title));
  const left = cards.slice().sort(byTitle), out = [], placed = new Set();
  while (left.length) {
    let i = left.findIndex((c) => [...blockedBy.get(c.eid)].every((b) => placed.has(b)));
    if (i < 0) i = 0;                     // a cycle: take the next by title
    const [c] = left.splice(i, 1);
    out.push(c); placed.add(c.eid);
  }
  return out;
}

/** The edges whose both ends are cards on the board (drawn as lines); the
 * rest point at work outside the board (shown as chips on the card). */
function cardEdges(board) {
  const ids = new Set(((board && board.cards) || []).map((c) => c.eid));
  const lines = [], chips = {};
  for (const e of (board && board.edges) || []) {
    if (ids.has(e.from) && ids.has(e.to)) lines.push(e);
    else (chips[e.from] = chips[e.from] || []).push({ rel: e.rel, to: e.to });
  }
  return { lines, chips };
}

/** How an open card's one variable is entered: {kind, options?, step?}. */
function inputSpec(v) {
  const t = v && v.type;
  if (t === "bool") return { kind: "select", options: ["yes", "no"] };
  if (t === "option") return { kind: "select", options: ((v && v.options) || []).map(String) };
  if (t === "int") return { kind: "number", step: "1" };
  if (t === "float") return { kind: "number", step: "any" };
  if (t === "string") return { kind: "text" };
  return null;                            // no var: the card can't be answered here
}

/** The input's starting text: the agent's pick in the input's own terms. */
function pickText(v, pick) {
  if (pick === undefined || pick === null) return "";
  if (v && v.type === "bool") return pick === true || /^(true|yes|y|on|1)$/i.test(String(pick)) ? "yes" : "no";
  return String(pick);
}

/** The value shown on a decided card. */
function valueText(card) {
  const v = card && card.value;
  if (v === undefined || v === null) return "";
  const s = typeof v === "boolean" ? (v ? "yes" : "no") : String(v);
  const u = card.var && card.var.unit ? " " + card.var.unit : "";
  return s + u;
}

/** The message pre-filled (never sent) in the card's chat. */
function askText(card) {
  const lines = ["About the decision \"" + card.title + "\":"];
  if (card.description) lines.push(card.description);
  if (card.status === "open") {
    const p = pickText(card.var, card.pick);
    lines.push("It asks for one " + ((card.var && card.var.type) || "value") +
      (card.var && card.var.unit ? " (" + card.var.unit + ")" : "") + (p ? "; the pick is " + p : "") + ".");
  } else if (card.status === "decided") {
    lines.push("Decided: " + valueText(card) + ".");
  }
  lines.push("");
  return lines.join("\n");
}

/** A relative age for a set_at stamp. */
function age(iso, now) {
  const t = Date.parse(iso || "");
  if (!isFinite(t)) return "";
  const s = Math.max(0, ((now || Date.now()) - t) / 1000);
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 129600) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

const DecisionLogic = { columns, blockersFirst, cardEdges, inputSpec, pickText, valueText, askText, age, COLS, POLL_MS };
if (typeof module !== "undefined" && module.exports) module.exports = DecisionLogic;
if (typeof window !== "undefined") window.DecisionLogic = DecisionLogic;   // home.js builds the chat's message with it

// ---------------------------------------------------------------- the element
if (typeof customElements !== "undefined" && !customElements.get("membrane-decisions")) {
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const STYLE = `
    :host { position:absolute; inset:58px 12px 12px 12px; display:block; color:var(--primary-text-color,#e8e8e8); }
    .board { position:relative; height:100%; display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:12px; }
    .col { position:relative; z-index:1; display:flex; flex-direction:column; min-height:0; border-radius:16px; padding:10px;
           background:color-mix(in srgb, var(--card-background-color,#1c1c1c) 78%, transparent); backdrop-filter:blur(8px); }
    .col h2 { margin:0 0 2px; font-size:15px; } .col .sub { font-size:12px; opacity:.6; margin-bottom:8px; }
    .list { flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:10px; padding:2px; }
    .empty { font-size:13px; opacity:.55; padding:8px 2px; }
    .dc { position:relative; cursor:pointer; border-radius:12px; padding:10px 12px; outline:none;
          background:color-mix(in srgb, var(--secondary-background-color,#2a2a2a) 85%, transparent);
          border:1px solid color-mix(in srgb, var(--divider-color,#888) 35%, transparent); }
    .dc:hover { border-color:color-mix(in srgb, var(--primary-color,#3477d6) 60%, transparent); }
    .dc:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
    .dc .t { font-weight:600; overflow-wrap:anywhere; } .dc .d { font-size:13px; opacity:.8; margin-top:3px; overflow-wrap:anywhere; }
    .dc .m { font-size:12px; opacity:.65; margin-top:6px; font-variant-numeric:tabular-nums; }
    .dc .val { font-weight:600; opacity:1; }
    .chips { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
    .chip { font-size:11px; padding:1px 8px; border-radius:999px; background:color-mix(in srgb, var(--primary-color,#3477d6) 22%, transparent); }
    form.ans { display:flex; gap:6px; margin-top:8px; align-items:center; cursor:default; }
    form.ans label { font-size:12px; opacity:.7; }
    form.ans input, form.ans select { flex:1; min-width:0; font:inherit; padding:5px 8px; border-radius:8px; color:inherit;
          border:1px solid color-mix(in srgb, var(--divider-color,#888) 50%, transparent); background:transparent; }
    form.ans button { font:inherit; border:0; border-radius:8px; padding:5px 12px; cursor:pointer; color:inherit;
          background:color-mix(in srgb, var(--primary-color,#3477d6) 45%, transparent); }
    .sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
    .err { font-size:12px; color:var(--error-color,#e06c5c); margin-top:4px; }
    svg.edges { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; z-index:2; overflow:visible; }
    svg.edges marker path { fill:var(--primary-color,#3477d6); stroke:none; opacity:.8; }
    svg.edges > path { fill:none; stroke:var(--primary-color,#3477d6); stroke-width:1.5; opacity:.55; }
    .msg { position:absolute; left:0; right:0; bottom:-2px; text-align:center; font-size:12px; opacity:.6; }
    @media (max-width: 760px) { .board { grid-template-columns:minmax(0, 1fr); overflow-y:auto; } .col { min-height:auto; } .list { overflow:visible; } svg.edges { display:none; } }
  `;

  class MembraneDecisions extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._board = null; this._timer = null; this._errs = {};
    }

    /** api: get(path) -> JSON, post(path, body) -> JSON (throws the reason),
     * openChat(card) -> opens the card's chat. */
    set api(a) { this._api = a; if (this.isConnected) this._load(); }

    connectedCallback() {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="board"></div><div class="msg" role="status"></div>`;
      this._root = this.shadowRoot.querySelector(".board");
      this._msg = this.shadowRoot.querySelector(".msg");
      this._root.addEventListener("click", (e) => this._click(e));
      this._root.addEventListener("keydown", (e) => {
        const c = e.target.closest && e.target.closest(".dc");
        if (c && e.target === c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); this._open(c.dataset.eid); }
      });
      this._root.addEventListener("submit", (e) => { e.preventDefault(); this._answer(e.target); });
      this._onResize = () => this._drawEdges();
      window.addEventListener("resize", this._onResize);
      this._root.addEventListener("scroll", this._onResize, true);
      if (this._api) this._load();
      this._timer = setInterval(() => { if (!document.hidden && !this._editing()) this._load(); }, POLL_MS);
    }

    disconnectedCallback() {
      clearInterval(this._timer);
      window.removeEventListener("resize", this._onResize);
    }

    _editing() {
      const a = this.shadowRoot.activeElement;
      return !!(a && a.closest && a.closest("form.ans"));
    }

    async _load() {
      if (!this._api) return;
      try {
        this._board = await this._api.get(BOARD_PATH);
        this._msg.textContent = "";
        this._paint();
      } catch (e) { this._msg.textContent = "can't read the decisions: " + (e && e.message || e); }
    }

    _card(c, chips) {
      let body = "";
      if (c.status === "open") {
        const sp = inputSpec(c.var), p = pickText(c.var, c.pick);
        if (sp) {
          const id = "v-" + c.eid.replace(/[^a-z0-9]+/gi, "-");
          const unit = c.var && c.var.unit ? esc(c.var.unit) : "";
          const field = sp.kind === "select"
            ? `<select id="${id}" name="v">${sp.options.map((o) => `<option${o === p ? " selected" : ""}>${esc(o)}</option>`).join("")}</select>`
            : `<input id="${id}" name="v" type="${sp.kind}"${sp.step ? ` step="${sp.step}"` : ""} value="${esc(p)}" autocomplete="off">`;
          body = `<form class="ans" data-eid="${esc(c.eid)}"><label for="${id}"${unit ? "" : ' class="sr"'}>${unit || esc(c.title)}</label>${field}` +
            `<button type="submit" aria-label="decide ${esc(c.title)}" title="Decide">✓</button></form>`;
        } else {
          body = `<div class="m">no variable set: open it to decide in the chat</div>`;
        }
      } else if (c.status === "decided") {
        const v = valueText(c);
        body = `<div class="m">${v ? `<span class="val">${esc(v)}</span> · ` : ""}${esc(c.set_by || "decided")}` +
          `${c.set_at ? " · " + esc(age(c.set_at)) : ""}</div>`;
      }
      const ch = (chips[c.eid] || []).map((x) => `<span class="chip">${esc(x.rel.replace("_", " "))}: ${esc(x.to)}</span>`).join("");
      const err = this._errs[c.eid] ? `<div class="err" role="alert">${esc(this._errs[c.eid])}</div>` : "";
      return `<article class="dc" tabindex="0" role="button" data-eid="${esc(c.eid)}" aria-label="${esc(c.title)}: open its chat">` +
        `<div class="t">${esc(c.title)}</div>${c.description ? `<div class="d">${esc(c.description)}</div>` : ""}` +
        `${body}${ch ? `<div class="chips">${ch}</div>` : ""}${err}</article>`;
    }

    _paint() {
      const cols = columns(this._board), { chips } = cardEdges(this._board);
      this._root.innerHTML = COLS.map((k) => `<section class="col" aria-label="${esc(k.title)}"><h2>${esc(k.title)}</h2>` +
        `<div class="sub">${esc(k.sub)} · ${cols[k.key].length}</div><div class="list">` +
        (cols[k.key].map((c) => this._card(c, chips)).join("") || `<div class="empty">nothing here yet</div>`) +
        `</div></section>`).join("") + `<svg class="edges" aria-hidden="true"></svg>`;
      this._drawEdges();
    }

    /** Lines between cards: from the card that blocks / led to, to the other one. */
    _drawEdges() {
      const svg = this._root && this._root.querySelector("svg.edges");
      if (!svg || !this._board) return;
      const box = this._root.getBoundingClientRect();
      const at = (eid) => {
        const el = this._root.querySelector(`.dc[data-eid="${CSS.escape(eid)}"]`);
        return el ? el.getBoundingClientRect() : null;
      };
      const paths = [];
      for (const e of cardEdges(this._board).lines) {
        const a = at(e.from), b = at(e.to);
        if (!a || !b) continue;
        let d;
        if (Math.abs(a.left - b.left) < 4) {                      // the same column: a loop on the right
          const x = a.right - box.left, y1 = a.top + a.height / 2 - box.top, y2 = b.top + b.height / 2 - box.top;
          d = `M${x},${y1} C${x + 14},${y1} ${x + 14},${y2} ${x},${y2}`;
        } else {
          const fwd = a.left < b.left;                           // from a's facing side to b's
          const x1 = (fwd ? a.right : a.left) - box.left, y1 = a.top + a.height / 2 - box.top;
          const x2 = (fwd ? b.left : b.right) - box.left, y2 = b.top + b.height / 2 - box.top;
          const mx = (x1 + x2) / 2;
          d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
        }
        paths.push(`<path d="${d}" marker-end="url(#dm-arrow)"><title>${esc(e.rel.replace("_", " "))}</title></path>`);
      }
      svg.innerHTML = '<defs><marker id="dm-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">' +
        '<path d="M0,0 L8,4 L0,8 z"></path></marker></defs>' + paths.join("");
    }

    _click(e) {
      if (e.target.closest("form.ans")) return;         // the value is edited in place
      const c = e.target.closest(".dc");
      if (c) this._open(c.dataset.eid);
    }

    _open(eid) {
      const card = ((this._board && this._board.cards) || []).find((c) => c.eid === eid);
      if (card && this._api.openChat) this._api.openChat(card);
    }

    async _answer(form) {
      const eid = form.dataset.eid;
      const card = ((this._board && this._board.cards) || []).find((c) => c.eid === eid);
      if (!card) return;
      let value = form.elements.v.value;
      if (card.var && card.var.type === "bool") value = value === "yes";
      const btn = form.querySelector("button");
      btn.disabled = true;
      try {
        await this._api.post(ANSWER_PATH, { card: eid, value, conv: card.chat || "" });
        delete this._errs[eid];
        if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
        await this._load();
      } catch (err) {
        this._errs[eid] = String(err && err.message || err);
        btn.disabled = false;
        this._paint();
      }
    }
  }
  customElements.define("membrane-decisions", MembraneDecisions);
}
