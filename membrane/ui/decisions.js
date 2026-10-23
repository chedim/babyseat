/* THE DECISION COCKPIT (#470): the project decision map -- "how did we get
 * here -> what are we doing -> what do we need to do next" -- as the decision
 * board turned 90 degrees. The time axis is vertical: the future above, the
 * past below, and the center input bar's line is NOW. The page is symmetric
 * about its vertical center line.
 *
 *   center: the goals we steer toward (GET /membrane/goals), a small field
 *           with the babyseat over it, the input bar (the real chat element,
 *           <ai-terminal-panel cockpit>: composer on top) and the chat below
 *           it, newest message first.
 *   sides:  FUTURE cards (open) pile up above the bar's line, the first to
 *           decide nearest it; NOW cards (in flight) sit on the line; PAST
 *           cards (decided) fall below it, the newest nearest, the oldest
 *           pushed out of view. New cards alternate left / right.
 *
 * The cards are Decision entities (GET /membrane/decisions). An open card asks
 * for ONE variable, pre-filled with the agent's pick, answered on the card
 * (POST /membrane/decision/answer). Clicking a card loads its question into
 * the center bar (never sent): the conversation happens in the center chat,
 * recorded on the card (POST /membrane/decision/chat). An answer made while
 * the card's chat is open names that chat's newest message; hovering a past
 * card then scrolls the chat to where it was decided.
 *
 * The pure helpers (DecisionLogic) run under node for the tests; the element
 * <membrane-decisions> is defined only in a browser. */

const BOARD_PATH = "/membrane/decisions";
const GOALS_PATH = "/membrane/goals";
const ANSWER_PATH = "/membrane/decision/answer";
const CHAT_PATH = "/membrane/decision/chat";
const POLL_MS = 5000;               // the cockpit re-reads the seat this often while shown
const PAST_ROWS = 5;                // past rows kept in view; each new decision pushes the oldest out
const GOALS_MAX = 4;                // goals listed above the field; the rest are counted

/** The cards by band, each nearest the bar first. future: a card that blocks
 * another comes before it (the first thing to decide nearest the bar), then
 * by title; now: by title; past: the newest decision first. */
function bands(board) {
  const cards = (board && board.cards) || [];
  const out = { future: [], now: [], past: [] };
  for (const c of cards) (c.status === "open" ? out.future : c.status === "in_flight" ? out.now : out.past).push(c);
  out.now.sort((a, b) => String(a.title).localeCompare(String(b.title)));
  out.past.sort((a, b) => String(b.set_at || "").localeCompare(String(a.set_at || "")) ||
    String(a.title).localeCompare(String(b.title)));
  out.future = blockersFirst(out.future, (board && board.edges) || []);
  return out;
}

/** Where each card of a band goes: alternating left / right, two to a row
 * (row 0 on the bar's line). narrow (a phone): one column. */
function place(list, narrow) {
  return (list || []).map((card, i) => narrow ? { card, side: "left", row: i }
    : { card, side: i % 2 ? "right" : "left", row: Math.floor(i / 2) });
}

/** The band split into its two side lists, each nearest the bar first. */
function sideLists(list, narrow) {
  const out = { left: [], right: [] };
  for (const p of place(list, narrow)) out[p.side].push(p.card);
  return out;
}

/** A card's depth by its row from the bar: the future floats toward you (far
 * up = smaller, tilted back; on the line full size, straight), the past
 * floats away (falling = shrinking, tilting back the other way, fading). */
function depthStyle(row, band) {
  const r = Math.max(0, Number(row) || 0);
  if (band === "future") {
    return { scale: r ? Math.max(0.62, 1 - 0.08 * r) : 1, tilt: r ? Math.min(32, 7 * r) : 0,
             opacity: r ? Math.max(0.55, 1 - 0.09 * r) : 1 };
  }
  if (band === "past") {
    return { scale: r ? Math.max(0.55, 1 - 0.09 * r) : 1, tilt: r ? -Math.min(36, 8 * r) : 0,
             opacity: r ? Math.max(0.18, 1 - 0.17 * r) : 1 };
  }
  return { scale: 1, tilt: 0, opacity: 1 };
}

/** The past cards in view: the newest `rows` rows (two cards a row); the
 * older ones are pushed out at the bottom. */
function pushOut(past, rows) {
  const keep = Math.max(0, Number(rows) || 0) * 2, list = past || [];
  return { shown: list.slice(0, keep), gone: Math.max(0, list.length - keep) };
}

/** The goals listed: the seat's active set first, `max` in all, the rest counted. */
function goalsShown(goals, max) {
  const list = (goals || []).slice().sort((a, b) => (b.active ? 1 : 0) - (a.active ? 1 : 0));
  return { shown: list.slice(0, max), more: Math.max(0, list.length - max) };
}

/** A card clicked: what the center chat does. switchTo: the card's own chat
 * (another session than the one open); record: the open session, put on a
 * card that has no chat yet; text: the question, pre-filled (never sent). */
function loadCard(card, chatSid) {
  const own = (card && card.chat) || "";
  return { switchTo: own && own !== chatSid ? own : null, record: !own && chatSid ? chatSid : null,
           text: askText(card) };
}

/** An answer's link to its chat: conv = the card's chat; message = the open
 * chat's newest message, only when that chat IS the card's chat. */
function answerLink(card, chatSid, lastMid) {
  const conv = (card && card.chat) || "";
  return { conv, message: conv && conv === chatSid && lastMid ? String(lastMid) : "" };
}

/** A past card's way back to where it was decided. hover: "scroll" (its
 * message is in the open chat) or "highlight" (the card only); open: the
 * session a click opens (its chat, when another is open); message: the
 * message to scroll to. */
function pastLink(card, chatSid) {
  const chat = (card && card.chat) || "", message = (card && card.message) || "";
  return { hover: chat && chat === chatSid && message ? "scroll" : "highlight",
           open: chat && chat !== chatSid ? chat : null, message };
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

const DecisionLogic = { bands, place, sideLists, depthStyle, pushOut, goalsShown, loadCard, answerLink, pastLink,
  blockersFirst, cardEdges, inputSpec, pickText, valueText, askText, age, POLL_MS, PAST_ROWS, GOALS_MAX };
if (typeof module !== "undefined" && module.exports) module.exports = DecisionLogic;
if (typeof window !== "undefined") window.DecisionLogic = DecisionLogic;   // home.js builds the chat's message with it

// ---------------------------------------------------------------- the element
if (typeof customElements !== "undefined" && !customElements.get("membrane-decisions")) {
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const NARROW = "(max-width: 760px)";
  // the cockpit is dark (a HUD over the field): its own colours, the page's font
  const STYLE = `
    :host { position:absolute; inset:58px 0 0 0; display:block; overflow:hidden; color:#dfe8f1;
            --amber:#ffb040; --cyan:#5fd6ff; --line:rgba(255,176,64,.5); --dim:rgba(255,176,64,.18);
            --panel:rgba(8,13,20,.84); --cockpit-line:rgba(255,176,64,.55); --cockpit-accent:#5fd6ff;
            background:radial-gradient(ellipse at 50% 46%, #0d1a28 0%, #060a10 62%, #04060a 100%); }
    .ck { position:relative; height:100%; display:grid; gap:0 18px; padding:8px 44px 0;
          grid-template-columns:minmax(0,1fr) minmax(0,55%) minmax(0,1fr);
          grid-template-rows:minmax(0,1fr) auto minmax(0,1fr);
          grid-template-areas:"fl ct fr" "nl cc nr" "pl cc pr"; }
    .fl { grid-area:fl; } .fr { grid-area:fr; } .nl { grid-area:nl; } .nr { grid-area:nr; }
    .pl { grid-area:pl; } .pr { grid-area:pr; } .ct { grid-area:ct; } .cc { grid-area:cc; }
    /* the NOW line: across the page at the bar's height */
    .nowline { grid-row:2; grid-column:1 / -1; align-self:start; margin-top:28px; height:1px; pointer-events:none;
               background:linear-gradient(90deg, transparent, var(--line) 12%, var(--line) 88%, transparent); }
    .band { position:relative; min-height:0; display:flex; flex-direction:column; gap:10px; padding:6px 2px;
            perspective:900px; }
    .band.future { flex-direction:column-reverse; overflow-y:auto; scrollbar-width:thin; padding-bottom:10px; }
    .band.now { padding:4px 2px 10px; min-height:64px; }
    .band.past { overflow:hidden; padding-top:10px; }
    .lbl { font-size:10.5px; letter-spacing:.16em; text-transform:uppercase; color:var(--amber); opacity:.7; }
    .fr .lbl, .nr .lbl, .pr .lbl { text-align:right; }
    .gone { font-size:11px; opacity:.45; text-align:center; letter-spacing:.08em; }
    /* a card: a chamfered HUD panel */
    .dc { position:relative; flex:none; cursor:pointer; padding:9px 12px; outline:none; background:var(--panel);
          --edge:var(--line); border:1px solid; border-color:var(--edge);
          clip-path:polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px);
          transition:transform .45s ease, opacity .45s ease, border-color .2s ease; }
    /* the clip cuts the border at the two chamfers: each draws its own diagonal */
    .dc::before, .dc::after { content:""; position:absolute; width:14.2px; height:2px; margin-top:-1px;
          background:var(--edge); transform:rotate(-45deg); transform-origin:0 50%; pointer-events:none; }
    .dc::before { left:0; top:10px; }
    .dc::after { left:calc(100% - 10px); top:100%; }
    .future .dc { transform-origin:50% 100%; } .past .dc { transform-origin:50% 0; }
    .dc:hover, .dc.lit { --edge:var(--cyan); }
    .dc:focus-visible { --edge:var(--cyan); box-shadow:inset 0 0 0 1px var(--cyan); }
    .now .dc { --edge:var(--cyan); box-shadow:inset 3px 0 0 var(--cyan); }
    .nr .dc { box-shadow:inset -3px 0 0 var(--cyan); }
    .dc .t { font-weight:600; overflow-wrap:anywhere; line-height:1.3; }
    .dc .d { font-size:12.5px; opacity:.75; margin-top:3px; overflow-wrap:anywhere;
             display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
    .dc .m { font-size:12px; opacity:.7; margin-top:5px; font-variant-numeric:tabular-nums; }
    .dc .val { font-weight:600; color:var(--amber); opacity:1; }
    .chips { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
    .chip { font-size:10.5px; padding:0 7px; border:1px solid var(--dim); color:#b9c6d3; }
    form.ans { display:flex; gap:6px; margin-top:8px; align-items:center; cursor:default; }
    form.ans label { font-size:12px; opacity:.7; }
    form.ans input, form.ans select { flex:1; min-width:0; font:inherit; padding:4px 8px; color:inherit;
          border:1px solid var(--dim); background:rgba(0,0,0,.35); border-radius:2px; }
    form.ans input:focus, form.ans select:focus { outline:none; border-color:var(--cyan); }
    form.ans button { font:inherit; border:1px solid var(--amber); color:var(--amber); background:transparent;
          padding:3px 12px; cursor:pointer; border-radius:2px; }
    form.ans button:hover { background:rgba(255,176,64,.16); }
    .sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
    .err { font-size:12px; color:#ff7a66; margin-top:4px; }
    .empty { font-size:12px; opacity:.4; letter-spacing:.04em; }
    .fr .empty, .nr .empty, .pr .empty { text-align:right; }
    /* the center column */
    .ct { display:flex; flex-direction:column; min-height:0; }
    .goals { flex:none; text-align:center; padding:2px 0 4px; }
    .goals ul { list-style:none; margin:4px 0 0; padding:0; display:flex; flex-direction:column; gap:2px; align-items:center; }
    .goals li { font-size:13px; max-width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; opacity:.8; }
    .goals li.on { opacity:1; color:#eaf6ff; } .goals li.on::before { content:"◆ "; color:var(--cyan); }
    .goals li:not(.on)::before { content:"◇ "; color:var(--amber); opacity:.7; }
    .goals .more, .goals .none { font-size:11px; opacity:.45; }
    .fieldbox { position:relative; flex:1; min-height:110px; margin:0 auto; width:min(100%, 520px);
                -webkit-mask-image:radial-gradient(ellipse at center, #000 52%, transparent 74%);
                mask-image:radial-gradient(ellipse at center, #000 52%, transparent 74%); }
    .logo { position:absolute; left:50%; top:50%; width:56px; height:56px; transform:translate(-50%,-50%);
            pointer-events:none; filter:drop-shadow(0 0 10px rgba(95,214,255,.45)); opacity:.95; }
    .cc { position:relative; min-height:0; display:flex; flex-direction:column; }
    .cc ::slotted(ai-terminal-panel) { flex:1; min-height:0; height:auto; }
    .msg { position:absolute; left:0; right:0; bottom:4px; text-align:center; font-size:12px; opacity:.6; pointer-events:none; }
    @media (prefers-reduced-motion: reduce) { .dc { transition:none; } }
    /* a phone: one column -- goals, the future, now and the bar, the chat, the past */
    @media ${NARROW} {
      :host { overflow-y:auto; }
      .ck { display:flex; flex-direction:column; height:auto; min-height:100%; padding:8px 12px 16px; gap:8px; }
      .fr, .nr, .pr, .nowline { display:none; }
      .band.future, .band.past { overflow:visible; }
      .fl { order:2; } .ct { order:1; } .nl { order:3; } .cc { order:4; height:72vh; height:72dvh; } .pl { order:5; }
      .fieldbox { flex:none; height:130px; }
    }
  `;

  class MembraneDecisions extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._board = null; this._goals = null; this._timer = null; this._errs = {};
    }

    /** api: get(path) -> JSON, post(path, body) -> JSON (throws the reason),
     * chat -> the center <ai-terminal-panel cockpit>, logo -> the babyseat's URL. */
    set api(a) {
      this._api = a;
      if (this._logo && a && a.logo) this._logo.src = a.logo;
      if (this.isConnected) this._load();
    }

    connectedCallback() {
      if (!this._root) {
        this.shadowRoot.innerHTML = `<style>${STYLE}</style><div class="ck">
          <div class="nowline" aria-hidden="true"></div>
          <section class="band future fl" aria-label="to decide, left"></section>
          <div class="ct"><div class="goals" aria-label="goals"></div>
            <div class="fieldbox"><slot name="field"></slot><img class="logo" alt="" hidden></div></div>
          <section class="band future fr" aria-label="to decide, right"></section>
          <section class="band now nl" aria-label="in flight, left"></section>
          <div class="cc"><slot name="chat"></slot></div>
          <section class="band now nr" aria-label="in flight, right"></section>
          <section class="band past pl" aria-label="decided, left"></section>
          <section class="band past pr" aria-label="decided, right"></section>
        </div><div class="msg" role="status"></div>`;
        this._root = this.shadowRoot.querySelector(".ck");
        this._msg = this.shadowRoot.querySelector(".msg");
        this._logo = this.shadowRoot.querySelector(".logo");
        this._logo.addEventListener("load", () => { this._logo.hidden = false; });
        if (this._api && this._api.logo) this._logo.src = this._api.logo;
        this._root.addEventListener("click", (e) => this._click(e));
        this._root.addEventListener("keydown", (e) => {
          const c = e.target.closest && e.target.closest(".dc");
          if (c && e.target === c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); this._activate(c); }
        });
        this._root.addEventListener("submit", (e) => { e.preventDefault(); this._answer(e.target); });
        // a past card: hover (or focus) finds where it was decided
        this._root.addEventListener("pointerover", (e) => this._hover(e.target.closest && e.target.closest(".past .dc")));
        this._root.addEventListener("focusin", (e) => this._hover(e.target.closest && e.target.closest(".past .dc")));
        this._root.addEventListener("pointerleave", () => this._hover(null));
        this._mq = window.matchMedia(NARROW);
        this._onMq = () => this._paint();
        if (this._mq.addEventListener) this._mq.addEventListener("change", this._onMq);
      }
      if (this._api) this._load();
      clearInterval(this._timer);
      this._timer = setInterval(() => { if (!document.hidden && !this._editing()) this._load(); }, POLL_MS);
    }

    disconnectedCallback() { clearInterval(this._timer); }

    _chat() { return (this._api && this._api.chat) || null; }
    _sid() { const c = this._chat(); return (c && c.sessionId) || ""; }

    _editing() {
      const a = this.shadowRoot.activeElement;
      return !!(a && a.closest && a.closest("form.ans"));
    }

    async _load() {
      if (!this._api) return;
      try {
        const [b, g] = await Promise.all([this._api.get(BOARD_PATH),
          this._api.get(GOALS_PATH).catch(() => null)]);
        this._board = b; this._goals = g;
        this._msg.textContent = "";
        this._paint();
      } catch (e) { this._msg.textContent = "can't read the decisions: " + (e && e.message || e); }
    }

    _byEid(eid) { return ((this._board && this._board.cards) || []).find((c) => c.eid === eid); }

    _card(c, band, row, chips, titles) {
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
          body = `<div class="m">no variable set: talk it over in the chat</div>`;
        }
      } else if (c.status === "decided") {
        const v = valueText(c);
        body = `<div class="m">${v ? `<span class="val">${esc(v)}</span> · ` : ""}${esc(c.set_by || "decided")}` +
          `${c.set_at ? " · " + esc(age(c.set_at)) : ""}</div>`;
      } else {
        body = `<div class="m">in flight</div>`;
      }
      const ch = (chips[c.eid] || []).map((x) => `<span class="chip">${esc(x.rel.replace("_", " "))}: ${esc(titles[x.to] || x.to)}</span>`).join("");
      const err = this._errs[c.eid] ? `<div class="err" role="alert">${esc(this._errs[c.eid])}</div>` : "";
      const d = depthStyle(row, band);
      const tf = `transform:rotateX(${d.tilt}deg) scale(${d.scale});opacity:${d.opacity}`;
      const what = band === "past" ? "show where it was decided" : "talk it over in the chat";
      return `<article class="dc" tabindex="0" role="button" style="${tf}" data-eid="${esc(c.eid)}" aria-label="${esc(c.title)}: ${what}">` +
        `<div class="t">${esc(c.title)}</div>${c.description && band !== "now" ? `<div class="d">${esc(c.description)}</div>` : ""}` +
        `${body}${ch ? `<div class="chips">${ch}</div>` : ""}${err}</article>`;
    }

    _paint() {
      if (!this._root) return;
      const narrow = !!(this._mq && this._mq.matches);
      const b = bands(this._board), titles = {};
      for (const c of (this._board && this._board.cards) || []) titles[c.eid] = c.title;
      // every edge is a chip on its card (to another card: its title)
      const ce = cardEdges(this._board), chips = ce.chips;
      for (const e of ce.lines) (chips[e.from] = chips[e.from] || []).push({ rel: e.rel, to: e.to });
      const past = pushOut(b.past, narrow ? PAST_ROWS * 2 : PAST_ROWS);
      const lists = { future: sideLists(b.future, narrow), now: sideLists(b.now, narrow), past: sideLists(past.shown, narrow) };
      const label = { future: "to decide ▲", now: "in flight", past: "decided ▼" };
      const empty = { future: "no open questions", now: "nothing in flight", past: "nothing decided yet" };
      const keepFocus = this.shadowRoot.activeElement && this.shadowRoot.activeElement.closest &&
        this.shadowRoot.activeElement.closest(".dc");
      const focusEid = keepFocus ? keepFocus.dataset.eid : "";
      for (const band of ["future", "now", "past"]) {
        for (const side of ["left", "right"]) {
          const el = this._root.querySelector(`.band.${band}.${side === "left" ? band[0] + "l" : band[0] + "r"}`);
          const cards = lists[band][side];
          // rows count from the bar: the card's index in its side list (a phone's
          // one column ages two cards a row, as the two sides do)
          let html = cards.map((c, i) => this._card(c, band, narrow ? Math.floor(i / 2) : i, chips, titles)).join("");
          const lbl = `<div class="lbl">${label[band]}${side === "left" || narrow ? ` · ${band === "past" ? b.past.length : b[band].length}` : ""}</div>`;
          if (!cards.length && side === "left") html = `<div class="empty">${empty[band]}</div>`;
          if (band === "past" && past.gone && (side === "left" || narrow)) html += `<div class="gone">${past.gone} older out of view</div>`;
          // the future pile grows upward (column-reverse): its label is last in the DOM, on top
          el.innerHTML = band === "future" ? html + lbl : lbl + html;
        }
      }
      this._paintGoals();
      if (focusEid) {
        const again = this._root.querySelector(`.dc[data-eid="${CSS.escape(focusEid)}"]`);
        if (again) again.focus({ preventScroll: true });
      }
    }

    _paintGoals() {
      const box = this._root.querySelector(".goals");
      const goals = (this._goals && this._goals.goals) || [];
      const g = goalsShown(goals, GOALS_MAX);
      box.innerHTML = `<div class="lbl">goals</div>` + (g.shown.length
        ? `<ul>${g.shown.map((x) => `<li class="${x.active ? "on" : ""}" title="${esc(x.summary || x.title)}${x.active ? " (steering now)" : ""}">${esc(x.title)}</li>`).join("")}</ul>` +
          (g.more ? `<div class="more">+${g.more} more open</div>` : "")
        : `<div class="none">${this._goals ? "no open goals" : "goals unavailable"}</div>`);
    }

    _click(e) {
      if (e.target.closest("form.ans")) return;         // the value is edited in place
      const c = e.target.closest(".dc");
      if (c) this._activate(c);
    }

    _activate(el) {
      const card = this._byEid(el.dataset.eid);
      if (!card) return;
      if (card.status === "decided") this._openPast(card, el); else this._loadIntoBar(card);
    }

    /** An open / in-flight card: its question into the center bar, its chat recorded. */
    async _loadIntoBar(card) {
      const chat = this._chat();
      if (!chat) return;
      const l = loadCard(card, this._sid());
      try {
        if (l.switchTo) await chat.openSession(l.switchTo);
        if (l.record) {
          card.chat = l.record;                         // the answer links to it at once
          try { await this._api.post(CHAT_PATH, { card: card.eid, chat: l.record }); } catch (e) { /* the bar still loads */ }
        }
      } finally { chat.prefill(l.text); }
    }

    /** A past card's hover: scroll the chat to where it was decided (its chat is open), or just light it. */
    _hover(el) {
      if (el === this._lit) return;
      if (this._lit) this._lit.classList.remove("lit");
      this._lit = el || null;
      const chat = this._chat();
      if (!el) { if (chat && chat.clearFound) chat.clearFound(); return; }
      el.classList.add("lit");
      const card = this._byEid(el.dataset.eid);
      if (!card || !chat) return;
      const l = pastLink(card, this._sid());
      if (l.hover === "scroll") chat.scrollToMessage(l.message);
      else if (chat.clearFound) chat.clearFound();
    }

    /** A past card's click: open its chat (another session) and scroll to its message. */
    async _openPast(card, el) {
      const chat = this._chat();
      if (!chat) return;
      const l = pastLink(card, this._sid());
      if (l.open) await chat.openSession(l.open);
      if (!l.message) return;
      // the session paints on the next frames
      for (let i = 0; i < 20 && !chat.scrollToMessage(l.message); i++) await new Promise((r) => setTimeout(r, 100));
      if (el) el.classList.add("lit");
    }

    async _answer(form) {
      const eid = form.dataset.eid;
      const card = this._byEid(eid);
      if (!card) return;
      let value = form.elements.v.value;
      if (card.var && card.var.type === "bool") value = value === "yes";
      const btn = form.querySelector("button");
      btn.disabled = true;
      const chat = this._chat();
      const link = answerLink(card, this._sid(), chat && chat.lastMessageId ? chat.lastMessageId() : "");
      try {
        await this._api.post(ANSWER_PATH, { card: eid, value, conv: link.conv, message: link.message });
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
