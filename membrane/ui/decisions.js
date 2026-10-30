/* THE DECISION COCKPIT (#470): the project decision map -- "how did we get
 * here -> what are we doing -> what do we need to do next" -- as the decision
 * board turned 90 degrees. The time axis is vertical: the future above, the
 * past below, and the center input bar's line is NOW. The page is symmetric
 * about its vertical center line.
 *
 *   behind:  the page's 3D field, the full-page background (no input).
 *   center: the goals we steer toward (GET /membrane/goals), the babyseat,
 *           the input bar (the real chat element,
 *           <ai-terminal-panel cockpit>: composer on top) and the chat below
 *           it, newest message first.
 *   sides:  FUTURE cards (open) pile up above the bar's line, the first to
 *           decide nearest it; NOW cards (in flight) sit on the line; PAST
 *           cards (decided) fall below it, the newest nearest, the oldest
 *           pushed out of view. New cards alternate left / right.
 *
 * The cards are Decision entities (GET /membrane/decisions). An open card asks
 * for ONE variable, pre-filled with the agent's pick. A card always fits its
 * lane (long text cut off with an ellipsis, no options on it, #736); clicking an
 * open or in-flight card opens it in place as a floating panel over everything:
 * the whole text, every option, Decide (POST /membrane/decision/answer), Drop,
 * and Chat about it, which loads its question into the center bar (never sent),
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
const PAST_ROWS = 5;                // (unused by the cockpit now: the past is virtualized, every card kept)
const PAST_EST = 84;                // a past card's height before it's measured (px, its margin included)
const PAST_GAP = 10;                // the margin under each past card
const PAST_OVERSCAN = 700;          // past cards stay rendered this far (px) beyond the view
const STUB_PX = 44;                 // a line to an unrendered card: this long a stub toward it                // past rows kept in view; each new decision pushes the oldest out
const GOALS_MAX = 4;                // goals listed above the field; the rest are counted

/** The cards by band, each nearest the bar first. future: a card that blocks
 * another comes before it (the first thing to decide nearest the bar), then
 * by title; now: by title; past: the newest decision first. */
/** A card in the work band: in flight, or decided and waiting for its work. */
function inNow(c) { return c.status === "in_flight" || (c.status === "decided" && c.work_needed === true); }

function bands(board) {
  const cards = (board && board.cards) || [];
  const out = { future: [], now: [], past: [] };
  for (const c of cards) (c.status === "open" ? out.future : inNow(c) ? out.now : out.past).push(c);
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

// THE OPEN PILE STACKS: the oldest card of each side is the stack front; newer (farther)
// cards hide behind older ones, each showing a strip of its top edge, so the pile
// never pushes the bar off the page. These numbers are flagged for the operator.
const STACK_FLOW_ROWS = 0;           // rows per side kept out of the stack (0: the oldest card is the stack front)
const STACK_STRIP = 26;              // px of a stacked card that shows above the one in front
const STACK_SHOWN = 6;               // strips drawn; deeper cards hide under the last one

/** The room a full stack reserves above its cards (px). */
function stackPad() { return (STACK_SHOWN - 1) * STACK_STRIP; }

/** Where a future card sits by its row from the bar: in flow, or stacked --
 * shift: px up from the stack's base; z: its depth (older in front). */
function stackSlot(row) {
  const r = Math.max(0, Number(row) || 0);
  if (r < STACK_FLOW_ROWS) return { stacked: false, shift: 0, z: 0 };
  const k = r - STACK_FLOW_ROWS + 1;
  return { stacked: true, shift: (Math.min(k, STACK_SHOWN) - 1) * STACK_STRIP, z: Math.max(1, 30 - k) };
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

/** THE WINDOW (#470, virtualized lists): which of `count` stacked items to
 * render for a view [viewTop, viewBottom) given in the list's own coordinates
 * (0 = its first item's top), widened by `overscan` px each way. heights:
 * the measured ones by index; the rest count as `estimate`. Returns
 * {start, end (exclusive), before, after (the spacers' px), total}. */
function windowRange(o) {
  const n = Math.max(0, o.count | 0), est = Number(o.estimate) || 0, hs = o.heights || {};
  const h = (i) => (hs[i] != null ? Number(hs[i]) : est);
  const top = Number(o.viewTop) - (Number(o.overscan) || 0), bottom = Number(o.viewBottom) + (Number(o.overscan) || 0);
  let y = 0, start = n, end = n, total = 0;
  const offs = new Array(n + 1);
  for (let i = 0; i < n; i++) { offs[i] = y; y += h(i); }
  offs[n] = total = y;
  for (let i = 0; i < n; i++) if (offs[i + 1] > top) { start = i; break; }
  end = start;
  while (end < n && offs[end] < bottom) end++;
  return { start, end, before: offs[start], after: total - offs[end], total };
}

/** An item's offset from its list's top (heights: measured by index, else `estimate`). */
function itemOffset(i, heights, estimate) {
  let y = 0;
  for (let k = 0; k < i; k++) y += heights && heights[k] != null ? Number(heights[k]) : Number(estimate) || 0;
  return y;
}

/** A line to a card that isn't rendered: a stub of `len` px from the rendered
 * end toward where the other one is. */
function stubLine(from, toward, len) {
  const dx = toward.x - from.x, dy = toward.y - from.y, d = Math.hypot(dx, dy) || 1, k = Math.min(1, len / d);
  return { x1: from.x, y1: from.y, x2: Math.round(from.x + dx * k), y2: Math.round(from.y + dy * k) };
}

/** Where the page scrolls on load: the bar's line a little above the middle
 * of the view (barTop: the bar's offset in the page; never above the top). */
function barScrollTop(barTop, viewH) {
  return Math.max(0, barTop - 0.4 * viewH);
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

// ---------------------------------------------------------------- the graph
// (#470 round 2, "chomping through uncertainty by making decisions"): past
// decisions led_to the work in flight, which the open decisions above block.
// The bands keep their own order (the first to decide / the newest nearest
// the bar); the graph picks each card's SIDE, and orders the in-flight band.

/** The card->card adjacency: eid -> sorted neighbour eids (either direction). */
function adjacency(board) {
  const ids = new Set(((board && board.cards) || []).map((c) => c.eid)), adj = new Map();
  for (const id of ids) adj.set(id, new Set());
  for (const e of (board && board.edges) || []) {
    if (ids.has(e.from) && ids.has(e.to) && e.from !== e.to) { adj.get(e.from).add(e.to); adj.get(e.to).add(e.from); }
  }
  return adj;
}

/** The cards a card is linked to (for the hover), sorted. */
function graphNeighbors(board, eid) {
  const n = adjacency(board).get(eid);
  return n ? [...n].sort() : [];
}

/** Sides for one band: each card goes where its placed neighbours are (left
 * counts +1, right -1); the card with the strongest pull is placed first (ties
 * in the band's order), so a card linked only within its band follows the
 * neighbour once that one is placed; no pull: the emptier side; no side takes
 * more than half (rounded up). */
function assignSides(list, adj, sideOf) {
  const cap = Math.ceil(list.length / 2), n = { left: 0, right: 0 };
  const score = (c) => {
    let v = 0;
    for (const nb of adj.get(c.eid) || []) v += sideOf[nb] === "left" ? 1 : sideOf[nb] === "right" ? -1 : 0;
    return v;
  };
  const left = list.slice();
  while (left.length) {
    let best = 0, bs = score(left[0]);
    for (let i = 1; i < left.length; i++) { const v = score(left[i]); if (Math.abs(v) > Math.abs(bs)) { best = i; bs = v; } }
    const [c] = left.splice(best, 1);
    let side = bs > 0 ? "left" : bs < 0 ? "right" : (n.left <= n.right ? "left" : "right");
    if (n[side] >= cap) side = side === "left" ? "right" : "left";
    n[side]++; sideOf[c.eid] = side;
  }
}

/** The cockpit's graph layout: {future, now, past: {left, right}, gone}, each
 * side list nearest the bar first. opts: narrow (one column, the bands' own
 * order), pastRows (the past rows kept in view, PAST_ROWS by default). */
function graphLayout(board, opts) {
  const o = opts || {}, b = bands(board);
  // every decided card (the list is virtualized); pastRows pushes the oldest out when given
  const past = pushOut(b.past, o.pastRows == null ? Infinity : o.pastRows);
  if (o.narrow) {
    return { future: { left: b.future, right: [] }, now: { left: b.now, right: [] },
             past: { left: past.shown, right: [] }, gone: past.gone };
  }
  const adj = adjacency(board), sideOf = {};
  const split = (list) => ({ left: list.filter((c) => sideOf[c.eid] === "left"), right: list.filter((c) => sideOf[c.eid] === "right") });
  b.now.forEach((c, i) => { sideOf[c.eid] = i % 2 ? "right" : "left"; });    // a first guess: alternation
  const outer = () => {
    for (const c of b.future.concat(past.shown)) delete sideOf[c.eid];
    assignSides(b.future, adj, sideOf);
    assignSides(past.shown, adj, sideOf);
  };
  outer();
  // the work in flight follows its decisions and questions...
  for (const c of b.now) delete sideOf[c.eid];
  assignSides(b.now, adj, sideOf);
  outer();                                                                    // the outer bands again, against the final work
  const fut = split(b.future), pst = split(past.shown), row = {};
  for (const l of [fut.left, fut.right, pst.left, pst.right]) l.forEach((c, i) => { row[c.eid] = i; });
  // ...ordered by its neighbours' rows (a card with none after the linked ones), then by title
  const bary = (c) => {
    const r = [...(adj.get(c.eid) || [])].filter((x) => x in row).map((x) => row[x]);
    return r.length ? r.reduce((a, x) => a + x, 0) / r.length : Infinity;
  };
  const nowOrder = b.now.slice().sort((x, y) => (bary(x) - bary(y)) || String(x.title).localeCompare(String(y.title)));
  const now = { left: nowOrder.filter((c) => sideOf[c.eid] === "left"), right: nowOrder.filter((c) => sideOf[c.eid] === "right") };
  return { future: fut, now, past: pst, gone: past.gone };
}

/** An edge's HUD style: amber when it touches the past (what led here), cyan
 * between the work and the questions; dashed for blocks. */
function edgeStyle(e, bandOf) {
  const past = bandOf[e.from] === "past" || bandOf[e.to] === "past";
  return { tone: past ? "amber" : "cyan", dashed: e.rel === "blocks" };
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

const DecisionLogic = { inNow, STACK_FLOW_ROWS, STACK_STRIP, STACK_SHOWN, stackPad, stackSlot, bands, place, sideLists, depthStyle, pushOut, windowRange, itemOffset, stubLine, barScrollTop, goalsShown, graphLayout, graphNeighbors, edgeStyle, adjacency, loadCard, answerLink, pastLink,
  blockersFirst, cardEdges, inputSpec, pickText, valueText, askText, age, POLL_MS, PAST_ROWS, GOALS_MAX };
if (typeof module !== "undefined" && module.exports) module.exports = DecisionLogic;
if (typeof window !== "undefined") window.DecisionLogic = DecisionLogic;   // home.js builds the chat's message with it

// ---------------------------------------------------------------- the element
if (typeof customElements !== "undefined" && !customElements.get("membrane-decisions")) {
  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const NARROW = "(max-width: 760px)";
  // the cockpit is dark (a HUD over the field): its own colours, the page's font
  const STYLE = `
    /* ONE DOCUMENT: the cockpit element is the page's one scroll container --
       the goals, the open pile, the bar's line, the chat and the past scroll
       together; the field behind stays put (the page's own fixed host) */
    :host { position:absolute; inset:58px 0 0 0; display:block; overflow-y:auto; overflow-x:hidden; color:#dfe8f1;
            --amber:#ffb040; --cyan:#5fd6ff; --line:rgba(255,176,64,.5); --dim:rgba(255,176,64,.18);
            --panel:rgba(8,13,20,.86); --cockpit-line:rgba(255,176,64,.55); --cockpit-accent:#5fd6ff; --cockpit-amber:#ffb040;
            --ck-backing:rgba(6,10,16,.74);
            background:transparent; }
    .ck { position:relative; min-height:100%; display:grid; gap:0 18px; padding:8px 66px 0;
          grid-template-columns:minmax(0,1fr) minmax(0,55%) minmax(0,1fr);
          /* the chat spans the bar's row and the past's: the flexible last row takes
             its height, so the bar's row stays the in-flight cards' height */
          grid-template-rows:auto auto 1fr;
          align-items:start;
          grid-template-areas:"fl ct fr" "nl cc nr" "pl cc pr"; }
    .fl { grid-area:fl; } .fr { grid-area:fr; } .nl { grid-area:nl; } .nr { grid-area:nr; }
    .pl { grid-area:pl; } .pr { grid-area:pr; } .ct { grid-area:ct; } .cc { grid-area:cc; }
    /* the NOW line: across the page at the bar's height */
    .nowline { grid-row:2; grid-column:1 / -1; align-self:start; margin-top:28px; height:1px; pointer-events:none;
               background:linear-gradient(90deg, transparent, var(--line) 12%, var(--line) 88%, transparent); }
    .band { position:relative; z-index:1; min-height:0; display:flex; flex-direction:column; gap:10px; padding:6px 2px;
            perspective:900px; }
    /* THE GRAPH: the card->card edges, under the cards and the center column,
       over the background field; faded where they pass behind the bar and chat */
    svg.graph { position:absolute; inset:0; width:100%; height:100%; z-index:0; pointer-events:none; overflow:visible; }
    svg.graph path.e { fill:none; stroke-width:1.4; opacity:.7; transition:opacity .2s ease, stroke-width .2s ease; }
    svg.graph path.e.amber { stroke:var(--amber); } svg.graph path.e.cyan { stroke:var(--cyan); }
    svg.graph path.e.dashed { stroke-dasharray:5 4; }
    svg.graph marker path { stroke:none; } svg.graph #ck-arrow-amber path { fill:var(--amber); } svg.graph #ck-arrow-cyan path { fill:var(--cyan); }
    svg.graph circle.stubend.amber { fill:var(--amber); } svg.graph circle.stubend.cyan { fill:var(--cyan); }
    .vlist { display:flex; flex-direction:column; } .vlist .spacer { flex:none; }
    .vrows { display:flex; flex-direction:column; } .vrows > .dc { margin-bottom:${PAST_GAP}px; }
    svg.graph.hl path.e { opacity:.12; } svg.graph.hl path.e.on { opacity:1; stroke-width:2.2; }
    .dc.nb { border-color:var(--cyan); box-shadow:0 0 12px rgba(95,214,255,.35); }
    .band.future { flex-direction:column-reverse; padding-bottom:10px; align-self:end; }   /* the pile sits on the bar's line */
    .band.now { padding:4px 2px 10px; min-height:64px; }
    /* the stack: every newer card in ONE grid cell, top-aligned and lifted by its own
       shift, so each shows a strip above the older card in front of it; hover (or
       focus) pops it over all the others */
    .stack { display:grid; grid-template-columns:minmax(0,1fr); flex:none; }   /* #736: never wider than the lane */
    .stack > .dc { grid-area:1 / 1; align-self:start; transform-origin:50% 0; }
    .stack .dc:hover, .stack .dc:focus-visible, .stack .dc:focus-within { z-index:60 !important; opacity:1 !important; }
    .band.past { overflow:hidden; padding-top:10px; }
    .lbl { font-size:10.5px; letter-spacing:.16em; text-transform:uppercase; color:var(--amber); opacity:.8;
           text-shadow:0 1px 2px #000, 0 0 6px #000; }
    .fr .lbl, .nr .lbl, .pr .lbl { text-align:right; }
    .gone { font-size:11px; opacity:.45; text-align:center; letter-spacing:.08em; }
    /* a card: a chamfered HUD panel */
    .dc { position:relative; flex:none; min-width:0; max-width:100%; box-sizing:border-box; cursor:pointer;
          padding:9px 12px; outline:none; background:var(--panel);
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
    /* #736: a card fits its lane -- long text is cut off with an ellipsis; the whole card is in its panel */
    .dc .t { font-weight:600; line-height:1.3; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .dc .d { font-size:12.5px; opacity:.75; margin-top:3px; overflow-wrap:anywhere;
             display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
    .dc .m { font-size:12px; opacity:.7; margin-top:5px; font-variant-numeric:tabular-nums;
             white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .dc .chip { max-width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; box-sizing:border-box; }
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
    .ct { position:relative; z-index:1; display:flex; flex-direction:column; min-height:0; align-self:stretch; }
    .goals { flex:none; text-align:center; padding:4px 12px 6px; margin:0 auto; max-width:100%; border-radius:4px;
             background:var(--ck-backing); text-shadow:0 1px 2px #000; }
    .goals ul { list-style:none; margin:4px 0 0; padding:0; display:flex; flex-direction:column; gap:2px; align-items:center; }
    .goals li { font-size:13px; max-width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; opacity:.8; }
    .goals li.on { opacity:1; color:#eaf6ff; } .goals li.on::before { content:"◆ "; color:var(--cyan); }
    .goals li:not(.on)::before { content:"◇ "; color:var(--amber); opacity:.7; }
    .goals .more, .goals .none { font-size:11px; opacity:.45; }
    .mark { position:relative; flex:1; min-height:80px; pointer-events:none; }
    .logo { position:absolute; left:50%; top:50%; width:56px; height:56px; transform:translate(-50%,-50%);
            pointer-events:none; filter:drop-shadow(0 0 10px rgba(95,214,255,.45)); opacity:.95; }
    .cc { position:relative; z-index:1; min-height:0; display:flex; flex-direction:column; }
    .cc ::slotted(ai-terminal-panel) { flex:1; min-height:0; height:auto; }
    .msg { position:absolute; left:0; right:0; bottom:4px; text-align:center; font-size:12px; opacity:.6; pointer-events:none; }
    /* THE CARD PANEL (#736): a click opens the card in place, floating over everything */
    .dscrim { position:fixed; inset:0; z-index:1000; background:rgba(0,0,0,.45); }
    .dpanel { position:fixed; z-index:1001; top:50%; left:50%; transform:translate(-50%,-50%);
              width:min(460px, calc(100vw - 32px)); max-height:calc(100vh - 64px); overflow-y:auto; box-sizing:border-box;
              padding:14px 16px; background:rgba(8,13,20,.97); border:1px solid var(--cyan); outline:none;
              box-shadow:0 0 24px rgba(95,214,255,.25); }
    .dscrim[hidden], .dpanel[hidden] { display:none; }
    .dpanel .ph { display:flex; gap:10px; align-items:flex-start; }
    .dpanel .pt { flex:1; min-width:0; font-weight:600; line-height:1.35; overflow-wrap:anywhere; }
    .dpanel .pd { font-size:13px; opacity:.8; margin-top:6px; overflow-wrap:anywhere; white-space:pre-wrap; }
    .dpanel .m { font-size:12.5px; opacity:.75; margin-top:8px; overflow-wrap:anywhere; }
    .dpanel .chip { overflow-wrap:anywhere; }
    .dpanel form.ans { display:block; margin-top:10px; }
    .dpanel fieldset.opts { border:0; margin:0; padding:0; display:flex; flex-direction:column; gap:4px; }
    .dpanel label.opt { display:flex; gap:8px; align-items:flex-start; padding:5px 8px; cursor:pointer; opacity:1;
              font-size:13px; border:1px solid var(--dim); overflow-wrap:anywhere; min-width:0; }
    .dpanel label.opt span { min-width:0; overflow-wrap:anywhere; }
    .dpanel label.opt:has(input:checked) { border-color:var(--cyan); }
    .dpanel form.ans input:not([type=radio]) { width:100%; box-sizing:border-box; }
    .dpanel .pa { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; justify-content:flex-end; }
    .dpanel button { font:inherit; border:1px solid var(--amber); color:var(--amber); background:transparent;
              padding:3px 12px; cursor:pointer; border-radius:2px; }
    .dpanel button:hover { background:rgba(255,176,64,.16); }
    .dpanel button.close { border-color:transparent; color:inherit; opacity:.7; padding:0 6px; }
    @media (prefers-reduced-motion: reduce) { .dc { transition:none; } }
    /* a phone: one column -- goals, the future, now and the bar, the chat, the past */
    @media ${NARROW} {
      :host { overflow-y:auto; }
      .ck { display:flex; flex-direction:column; min-height:100%; padding:8px 12px 16px; gap:8px; align-items:stretch; }
      .band, .band.future, .ct, .cc { align-self:stretch; }
      .fr, .nr, .pr, .nowline, svg.graph { display:none; }   /* a phone: no lines, the links are chips */
      .band.future, .band.past { overflow:visible; }
      .fl { order:2; } .ct { order:1; } .nl { order:3; } .cc { order:4; } .pl { order:5; }
      .mark { flex:none; height:84px; }
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
          <svg class="graph" aria-hidden="true"></svg>
          <div class="nowline" aria-hidden="true"></div>
          <section class="band future fl" aria-label="to decide, left"></section>
          <div class="ct"><div class="goals" aria-label="goals"></div>
            <div class="mark"><img class="logo" alt="" hidden></div></div>
          <section class="band future fr" aria-label="to decide, right"></section>
          <section class="band now nl" aria-label="in flight, left"></section>
          <div class="cc"><slot name="chat"></slot></div>
          <section class="band now nr" aria-label="in flight, right"></section>
          <section class="band past pl" aria-label="decided, left"></section>
          <section class="band past pr" aria-label="decided, right"></section>
        </div><div class="msg" role="status"></div>
        <div class="dscrim" hidden></div><div class="dpanel" role="dialog" aria-modal="true" tabindex="-1" hidden></div>`;
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
        // the card panel (#736): its own clicks, its answer, Escape and the scrim close it
        this._panel = this.shadowRoot.querySelector(".dpanel");
        this._scrim = this.shadowRoot.querySelector(".dscrim");
        this._scrim.addEventListener("click", () => this._closePanel());
        this._panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); this._closePanel(); } });
        this._panel.addEventListener("submit", async (e) => {
          e.preventDefault();
          if (await this._answer(e.target)) this._closePanel();
        });
        this._panel.addEventListener("click", (e) => {
          const b = e.target.closest && e.target.closest("button");
          if (!b || b.type === "submit") return;
          const card = this._byEid(this._panel.dataset.eid);
          if (b.classList.contains("close")) this._closePanel();
          else if (b.classList.contains("chat") && card) { this._closePanel(); this._loadIntoBar(card); }
          else if (b.classList.contains("drop") && card) { this._closePanel(); this._drop({ dataset: { eid: card.eid } }); }
        });
        // a past card: hover (or focus) finds where it was decided
        // any card: hover (or focus) lights its edges and its neighbours
        const over = (e) => {
          const c = e.target.closest && e.target.closest(".dc");
          this._hover(c && c.closest(".past") ? c : null);
          this._graphHover(c || null);
        };
        this._root.addEventListener("pointerover", over);
        this._root.addEventListener("focusin", over);
        this._root.addEventListener("pointerleave", () => { this._hover(null); this._graphHover(null); });
        // the lines live in the scrolling layer, so a scroll moves them with the cards;
        // they are recomputed when the layout changes: a resize, the chat or a band
        // growing (a ResizeObserver), a card's depth transition
        this._redraw = () => { if (!this._rafG) this._rafG = requestAnimationFrame(() => { this._rafG = 0; this._drawGraph(); }); };
        window.addEventListener("resize", this._redraw);
        this._root.addEventListener("transitionend", this._redraw);
        if (typeof ResizeObserver !== "undefined") {
          this._ro = new ResizeObserver(() => this._redraw());
          this._ro.observe(this._root);
          for (const el of this._root.querySelectorAll(".band, .cc, .ct")) this._ro.observe(el);
        }
        for (const ev of ["wheel", "touchstart", "keydown"]) this.addEventListener(ev, () => { this._touched = true; }, { passive: true });
        // the windows follow the page's scroll: one re-window a frame (the past here, the chat's in the panel)
        this._onScroll = () => {
          if (this._rafS) return;
          this._rafS = requestAnimationFrame(() => {
            this._rafS = 0;
            this._paintPast(false);
            const c = this._chat(); if (c && c.onViewport) c.onViewport();
          });
        };
        this.addEventListener("scroll", this._onScroll, { passive: true });
        window.addEventListener("resize", this._onScroll);
        this._mq = window.matchMedia(NARROW);
        this._onMq = () => this._paint();
        if (this._mq.addEventListener) this._mq.addEventListener("change", this._onMq);
      }
      if (this._api) this._load();
      clearInterval(this._timer);
      this._timer = setInterval(() => { if (!document.hidden && !this._editing()) this._load(); }, POLL_MS);
    }

    disconnectedCallback() {
      clearInterval(this._timer); window.removeEventListener("resize", this._redraw);
      if (this._ro) this._ro.disconnect();
    }

    /** On the first paint with cards, once: the input bar into view, the open
     * pile above it (scroll up = the future), the chat and the past below. */
    _showBar() {
      if (this._barShown || this._barT || !this._board) return;
      // after the layout settles (two frames: the piles, the goals, the logo); and only
      // while the person hasn't scrolled yet -- their own scroll wins
      this._barT = requestAnimationFrame(() => requestAnimationFrame(() => {
        this._barT = 0;
        const cc = this._root.querySelector(".cc");
        if (!cc || !this.clientHeight || this._barShown) return;
        this._barShown = true;
        if (this._touched) return;
        const top = cc.getBoundingClientRect().top - this.getBoundingClientRect().top + this.scrollTop;
        this.scrollTop = barScrollTop(top, this.clientHeight);
      }));
    }

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
          // #736: the collapsed card shows no options -- the pick, cut to the lane; the panel answers
          body = `<div class="m">${p ? `pick: <span class="val">${esc(p)}</span>` : "open"}${c.var && c.var.unit ? " " + esc(c.var.unit) : ""}</div>`;
        } else {
          body = `<div class="m">no variable set: talk it over in the chat</div>`;
        }
      } else if (c.status === "dropped") {
        body = `<div class="m">dropped${c.set_at ? " · " + esc(age(c.set_at)) : ""}</div>`;
      } else if (c.status === "decided" && c.work_needed === true) {
        const v = valueText(c);
        body = `<div class="m">${v ? `<span class="val">${esc(v)}</span> · ` : ""}decided · waiting for work</div>`;
      } else if (c.status === "decided" || c.status === "closed") {
        const v = valueText(c);
        body = `<div class="m">${v ? `<span class="val">${esc(v)}</span> · ` : ""}${esc(c.set_by || "decided")}` +
          `${c.set_at ? " · " + esc(age(c.set_at)) : ""}</div>`;
      } else {
        body = `<div class="m">in flight</div>`;
      }
      const ch = (chips[c.eid] || []).map((x) => `<span class="chip">${esc(x.rel.replace("_", " "))}: ${esc(titles[x.to] || x.to)}</span>`).join("");
      const err = this._errs[c.eid] ? `<div class="err" role="alert">${esc(this._errs[c.eid])}</div>` : "";
      const d = depthStyle(row, band);
      const sl = band === "future" && !this._narrow ? stackSlot(row) : { stacked: false };
      const tf = sl.stacked ? `transform:translateY(-${sl.shift}px);z-index:${sl.z};opacity:.94`
        : `transform:rotateX(${d.tilt}deg) scale(${d.scale});opacity:${d.opacity}`;
      const what = band === "past" ? "show where it was decided" : "open it";
      return `<article class="dc" tabindex="0" role="button" style="${tf}" data-eid="${esc(c.eid)}" aria-label="${esc(c.title)}: ${what}">` +
        `<div class="t">${esc(c.title)}</div>${c.description && band !== "now" ? `<div class="d">${esc(c.description)}</div>` : ""}` +
        `${body}${c.status === "open" ? `<button type="button" class="drop" aria-label="drop ${esc(c.title)}" title="Drop this card without answering">Drop</button>` : ""}` +
        `${ch ? `<div class="chips">${ch}</div>` : ""}${err}</article>`;
    }

    _paint() {
      if (!this._root) return;
      const narrow = !!(this._mq && this._mq.matches);
      const b = bands(this._board), titles = {};
      for (const c of (this._board && this._board.cards) || []) titles[c.eid] = c.title;
      // the graph: card->card edges are lines (on a phone, chips naming the other card);
      // an edge to work outside the board (carried_by: PR #468) is a chip either way
      const ce = cardEdges(this._board), chips = ce.chips;
      if (narrow) for (const e of ce.lines) (chips[e.from] = chips[e.from] || []).push({ rel: e.rel, to: e.to });
      const lay = graphLayout(this._board, { narrow });                     // every decided card
      const lists = { future: lay.future, now: lay.now, past: lay.past };
      const label = { future: "to decide ▲", now: "in flight", past: "decided ▼" };
      const empty = { future: "no open questions", now: "nothing in flight", past: "nothing decided yet" };
      const keepFocus = this.shadowRoot.activeElement && this.shadowRoot.activeElement.closest &&
        this.shadowRoot.activeElement.closest(".dc");
      const focusEid = keepFocus ? keepFocus.dataset.eid : "";
      for (const band of ["future", "now", "past"]) {
        if (band === "past") {
          // THE PAST, VIRTUALIZED: every decided card, only the rows near the view in the DOM
          this._past = { lists: lists.past, chips, titles, narrow, count: b.past.length };
          this._paintPast(true);
          continue;
        }
        for (const side of ["left", "right"]) {
          const el = this._root.querySelector(`.band.${band}.${side === "left" ? band[0] + "l" : band[0] + "r"}`);
          const cards = lists[band][side];
          // rows count from the bar: the card's index in its side list (a phone's
          // one column ages two cards a row, as the two sides do)
          this._narrow = narrow;
          const cardsHtml = cards.map((c, i) => this._card(c, band, narrow ? Math.floor(i / 2) : i, chips, titles));
          let html;
          if (band === "future" && !narrow && cards.length > STACK_FLOW_ROWS) {
            const nShown = Math.min(cards.length - STACK_FLOW_ROWS, STACK_SHOWN);
            html = cardsHtml.slice(0, STACK_FLOW_ROWS).join("") +
              `<div class="stack" style="padding-top:${(nShown - 1) * STACK_STRIP}px">${cardsHtml.slice(STACK_FLOW_ROWS).join("")}</div>`;
          } else html = cardsHtml.join("");
          const lbl = `<div class="lbl">${label[band]}${side === "left" || narrow ? ` · ${band === "past" ? b.past.length : b[band].length}` : ""}</div>`;
          if (!cards.length && side === "left") html = `<div class="empty">${empty[band]}</div>`;
          // the future pile grows upward (column-reverse): its label is last in the DOM, on top
          el.innerHTML = band === "future" ? html + lbl : lbl + html;
        }
      }
      this._paintGoals();
      if (this._panel && !this._panel.hidden) {
        const pc = this._byEid(this._panel.dataset.eid);
        if (!pc || !(pc.status === "open" || inNow(pc))) this._closePanel();
      }
      this._lines = narrow ? [] : ce.lines;
      this._bandOf = {};
      for (const k of ["future", "now", "past"]) for (const c of b[k]) this._bandOf[c.eid] = k;
      this._redraw();
      this._showBar();
      if (focusEid) {
        const again = this._root.querySelector(`.dc[data-eid="${CSS.escape(focusEid)}"]`);
        if (again) again.focus({ preventScroll: true });
      }
    }

    /** The edges as lines between the cards' rects: the same side, a loop out
     * into the page margin; across, from inner edge to inner edge, through
     * (behind) the center column, which a mask fades them under. */
    _drawGraph() {
      const svg = this._root && this._root.querySelector("svg.graph");
      if (!svg) return;
      const lines = this._lines || [];
      if (!lines.length) { svg.innerHTML = ""; return; }
      const box = this._root.getBoundingClientRect();
      const rect = (eid) => {
        const el = this._root.querySelector(`.dc[data-eid="${CSS.escape(eid)}"]`);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { l: r.left - box.left, r: r.right - box.left, t: r.top - box.top, b: r.bottom - box.top,
                 cx: (r.left + r.right) / 2 - box.left, cy: (r.top + r.bottom) / 2 - box.top };
      };
      const mid = box.width / 2, paths = [];
      for (const e of lines) {
        const a = rect(e.from), z = rect(e.to);
        if (!a !== !z) {                                                    // one end not rendered: a stub toward it
          const real = a || z, spot = this._pastSpot(a ? e.to : e.from, box);
          if (!spot) continue;
          const from = { x: real.cx, y: spot.y > real.cy ? real.b : real.t };
          const s = stubLine(from, spot, STUB_PX), st = edgeStyle(e, this._bandOf || {});
          paths.push(`<path class="e stub ${st.tone}${st.dashed ? " dashed" : ""}" data-from="${esc(e.from)}" data-to="${esc(e.to)}" ` +
            `d="M${s.x1},${s.y1} L${s.x2},${s.y2}" mask="url(#ck-fade)"><title>${esc(e.rel.replace("_", " "))} (scroll to see)</title></path>` +
            `<circle class="stubend ${st.tone}" cx="${s.x2}" cy="${s.y2}" r="2.5"></circle>`);
          continue;
        }
        if (!a || !z) continue;
        const aLeft = a.cx < mid, zLeft = z.cx < mid;
        let d;
        if (aLeft === zLeft) {                                              // one side: a loop in the margin
          const x1 = aLeft ? a.l : a.r, x2 = aLeft ? z.l : z.r, bulge = aLeft ? -26 : 26;
          const x = (aLeft ? Math.min(x1, x2) : Math.max(x1, x2)) + bulge;
          d = `M${x1},${a.cy} C${x},${a.cy} ${x},${z.cy} ${x2},${z.cy}`;
        } else {                                                            // across the center
          const x1 = aLeft ? a.r : a.l, x2 = zLeft ? z.r : z.l, mx = (x1 + x2) / 2;
          d = `M${x1},${a.cy} C${mx},${a.cy} ${mx},${z.cy} ${x2},${z.cy}`;
        }
        const st = edgeStyle(e, this._bandOf || {});
        paths.push(`<path class="e ${st.tone}${st.dashed ? " dashed" : ""}" data-from="${esc(e.from)}" data-to="${esc(e.to)}" d="${d}" ` +
          `marker-end="url(#ck-arrow-${st.tone})" mask="url(#ck-fade)"><title>${esc(e.rel.replace("_", " "))}</title></path>`);
      }
      // the mask: full strength everywhere but the center column (the goals, the bar, the chat)
      const cc = this._root.querySelector(".cc").getBoundingClientRect();
      const fx = cc.left - box.left - 4, fw = cc.width + 8;
      svg.innerHTML = `<defs><mask id="ck-fade" maskUnits="userSpaceOnUse" x="0" y="0" width="${box.width}" height="${box.height}">` +
        `<rect x="0" y="0" width="${box.width}" height="${box.height}" fill="#fff"></rect>` +
        `<rect x="${fx}" y="0" width="${fw}" height="${box.height}" fill="#383838"></rect></mask>` +
        ["amber", "cyan"].map((t) => `<marker id="ck-arrow-${t}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L8,4 L0,8 z"></path></marker>`).join("") +
        `</defs>` + paths.join("");
      if (this._hl) this._graphHover(this._hl, true);
    }

    /** A hovered card lights its edges and its neighbours (null: all lines again). */
    _graphHover(el, again) {
      const eid = el && el.dataset ? el.dataset.eid : null;
      if (!again && (this._hl ? this._hl.dataset.eid : null) === eid) return;
      this._hl = el || null;
      const svg = this._root.querySelector("svg.graph");
      for (const n of this._root.querySelectorAll(".dc.nb")) n.classList.remove("nb");
      if (svg) for (const p of svg.querySelectorAll("path.e.on")) p.classList.remove("on");
      if (!eid) { if (svg) svg.classList.remove("hl"); return; }
      const nbs = graphNeighbors(this._board, eid);
      if (svg) {
        svg.classList.toggle("hl", nbs.length > 0);
        for (const p of svg.querySelectorAll("path.e")) if (p.dataset.from === eid || p.dataset.to === eid) p.classList.add("on");
      }
      for (const n of nbs) { const c = this._root.querySelector(`.dc[data-eid="${CSS.escape(n)}"]`); if (c) c.classList.add("nb"); }
    }

    /** The past band's two lists, windowed (windowRange): a spacer above and
     * below the rendered cards keeps the page height. force: re-render even if
     * the window didn't move (new cards). */
    _paintPast(force) {
      const P = this._past;
      if (!P || !this._root) return;
      this._ph = this._ph || {};
      const host = this.getBoundingClientRect();
      let changed = false;
      for (const side of ["left", "right"]) {
        const el = this._root.querySelector(`.band.past.${side === "left" ? "pl" : "pr"}`);
        const cards = P.lists[side];
        if (force || !el.querySelector(".vlist")) {
          const lbl = `<div class="lbl">decided ▼${side === "left" || P.narrow ? ` · ${P.count}` : ""}</div>`;
          el.innerHTML = lbl + (cards.length ? `<div class="vlist"><div class="spacer" data-s="before"></div><div class="vrows"></div><div class="spacer" data-s="after"></div></div>`
            : side === "left" ? `<div class="empty">nothing decided yet</div>` : "");
          el._win = null;
        }
        const vl = el.querySelector(".vlist");
        if (!vl) continue;
        const origin = vl.getBoundingClientRect().top;
        const heights = {};
        cards.forEach((c, i) => { if (this._ph[c.eid] != null) heights[i] = this._ph[c.eid]; });
        const w = windowRange({ count: cards.length, heights, estimate: PAST_EST, viewTop: host.top - origin,
                                viewBottom: host.bottom - origin, overscan: PAST_OVERSCAN });
        vl.querySelector('[data-s="before"]').style.height = w.before + "px";
        vl.querySelector('[data-s="after"]').style.height = w.after + "px";
        if (!el._win || el._win.start !== w.start || el._win.end !== w.end) {
          vl.querySelector(".vrows").innerHTML = cards.slice(w.start, w.end)
            .map((c, k) => this._card(c, "past", w.start + k, P.chips, P.titles)).join("");
          el._win = { start: w.start, end: w.end };
          changed = true;
          for (const d of vl.querySelectorAll(".vrows > .dc")) this._ph[d.dataset.eid] = d.offsetHeight + PAST_GAP;
        }
      }
      if (changed && !force) { if (this._hl) this._graphHover(this._hl, true); this._redraw(); }
    }

    /** Where an unrendered past card is (its estimated box), for a stub line. */
    _pastSpot(eid, box) {
      const P = this._past;
      if (!P) return null;
      for (const side of ["left", "right"]) {
        const i = P.lists[side].findIndex((c) => c.eid === eid);
        if (i < 0) continue;
        const vl = this._root.querySelector(`.band.past.${side === "left" ? "pl" : "pr"} .vlist`);
        if (!vl) return null;
        const r = vl.getBoundingClientRect(), heights = {};
        P.lists[side].forEach((c, k) => { if (this._ph[c.eid] != null) heights[k] = this._ph[c.eid]; });
        const y = r.top - box.top + itemOffset(i, heights, PAST_EST) + PAST_EST / 2;
        return { x: (r.left + r.right) / 2 - box.left, y };
      }
      return null;
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
      const dr = e.target.closest && e.target.closest("button.drop");
      if (dr) { e.stopPropagation(); this._drop(dr.closest(".dc")); return; }
      if (e.target.closest("form.ans")) return;         // the value is edited in place
      const c = e.target.closest(".dc");
      if (c) this._activate(c);
    }

    _activate(el) {
      const card = this._byEid(el.dataset.eid);
      if (!card) return;
      if (card.status === "open" || inNow(card)) this._openPanel(card); else this._openPast(card, el);
    }

    /** The card panel (#736): the whole card -- its full title and description, every option
     * (wrapped, so a long one is read whole), Decide, Chat about it, Drop -- over everything. */
    _openPanel(card) {
      const pn = this._panel;
      if (!pn) return;
      let body = "";
      if (card.status === "open") {
        const sp = inputSpec(card.var), p = pickText(card.var, card.pick);
        if (sp) {
          const unit = card.var && card.var.unit ? esc(card.var.unit) : "";
          const field = sp.kind === "select"
            ? `<fieldset class="opts"><legend class="sr">${esc(card.title)}</legend>${sp.options.map((o) =>
                `<label class="opt"><input type="radio" name="v" value="${esc(o)}"${o === p ? " checked" : ""}><span>${esc(o)}</span></label>`).join("")}</fieldset>`
            : `<label class="m" for="pv">${unit || "value"}</label><input id="pv" name="v" type="${sp.kind}"` +
              `${sp.step ? ` step="${sp.step}"` : ""} value="${esc(p)}" autocomplete="off">`;
          body = `<form class="ans" data-eid="${esc(card.eid)}">${field}` +
            `<div class="pa"><button type="submit" aria-label="decide ${esc(card.title)}">Decide</button></div></form>`;
        } else body = `<div class="m">no variable set: talk it over in the chat</div>`;
      } else {
        const v = valueText(card);
        body = `<div class="m">${v ? `<span class="val">${esc(v)}</span> · ` : ""}${card.status === "decided" ? "decided · waiting for work" : "in flight"}</div>`;
      }
      const titles = {};
      for (const c of (this._board && this._board.cards) || []) titles[c.eid] = c.title;
      const chips = cardEdges(this._board).chips, lines = cardEdges(this._board).lines;
      const mine = (chips[card.eid] || []).concat(lines.filter((e) => e.from === card.eid).map((e) => ({ rel: e.rel, to: e.to })));
      const ch = mine.map((x) => `<span class="chip">${esc(x.rel.replace("_", " "))}: ${esc(titles[x.to] || x.to)}</span>`).join("");
      pn.dataset.eid = card.eid;
      pn.setAttribute("aria-label", card.title);
      pn.innerHTML = `<div class="ph"><div class="pt">${esc(card.title)}</div>` +
        `<button type="button" class="close" aria-label="close" title="Close (Esc)">✕</button></div>` +
        `${card.description ? `<div class="pd">${esc(card.description)}</div>` : ""}${body}` +
        `${ch ? `<div class="chips">${ch}</div>` : ""}<div class="err" role="alert"></div>` +
        `<div class="pa"><button type="button" class="chat">Chat about it</button>` +
        `${card.status === "open" ? `<button type="button" class="drop" title="Drop this card without answering">Drop</button>` : ""}</div>`;
      this._scrim.hidden = false; pn.hidden = false;
      pn.focus({ preventScroll: true });
    }

    _closePanel() {
      if (!this._panel || this._panel.hidden) return;
      this._panel.hidden = true; this._scrim.hidden = true;
      this._panel.innerHTML = ""; delete this._panel.dataset.eid;
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

    /** A past card's hover: mark where it was decided (its chat is open) and light it -- never scroll:
     * the cockpit is one scrolling page (#496), so a scroll would move the page under the pointer.
     * Clicking the card is what scrolls to it (_openPast). */
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
      if (l.hover === "scroll" && chat.markMessage) chat.markMessage(l.message);
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

    async _drop(el) {
      const eid = el && el.dataset.eid;
      if (!eid) return;
      try {
        await this._api.post("/membrane/decision/drop", { card: eid });
        delete this._errs[eid];
        await this._load();
      } catch (err) {
        this._errs[eid] = String(err && err.message || err);
        this._paint();
      }
    }

    async _answer(form) {
      const eid = form.dataset.eid;
      const card = this._byEid(eid);
      if (!card) return false;
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
        return true;
      } catch (err) {
        this._errs[eid] = String(err && err.message || err);
        btn.disabled = false;
        const pe = this._panel && !this._panel.hidden && this._panel.querySelector(".err");
        if (pe) pe.textContent = this._errs[eid]; else this._paint();
        return false;
      }
    }
  }
  customElements.define("membrane-decisions", MembraneDecisions);
}
