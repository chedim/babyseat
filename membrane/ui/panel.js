/**
 * <ai-terminal-panel>: the AI dashboard -- an opencode-style web client
 * for membrane's NATIVE serve surface (the opencode-compatible REST/SSE
 * on the brain phone), THROUGH the integration's proxy view
 * (/ui/serve/*): HA auth per request, the serve credential
 * stays server-side; LAN and outside (chegate tunnel) alike.
 *
 * v2 (2026-10-08):
 *   - LIVE: the proxy's per-user /event stream drives the transcript
 *     (message.updated / message.part.updated / message.part.delta /
 *     session.status / session.idle / session.updated), token by token;
 *     REST resyncs on (re)connect; polling only as the fallback lane.
 *   - opencode-like layout: a session sidebar, a transcript that scrolls
 *     on its OWN (the host is viewport-sized -- v1's height:100% had no
 *     sized parent, the page scrolled and the stick-to-bottom never ran),
 *     sticks to the bottom unless you scroll up, "jump to latest".
 *   - parts: markdown text (code blocks + copy), collapsible reasoning,
 *     tool cards (input/output, duration; membrane's lane refusals
 *     marked), per-turn usage footer.
 *   - membrane: build/plan agent picker, the queue (send while busy: the
 *     brain queues it, the status shows the depth), the minds strip (the
 *     loop workers, thinker, dreamer from /agent).
 *
 * Sessions are per HA user and follow you across devices (the proxy
 * scopes them). The proxy prefixes every message with a
 * "[HA user: <name>]" line for the brain's memory; the panel hides it.
 * Turn completion reads the brain's own /session/status, never a guess.
 */
// THE SEAT'S NAME (2026-10-14, issue #6): the LIVE agreed name. A fresh
// boot reads it out of /ui/conf (below, _boot); an OPEN page hears a
// membrane.name event from the hub; until either lands it is nobody --
// never a birth nickname a rename could make stale
let SEAT = (typeof window !== "undefined" && window.MEMBRANE_NAME) || "the assistant";
const SERVE = "/ui/serve";
const SPEAKER_PREFIX = "[HA user: ";
const SPEAKER_RE = /\[(?:HA )?user: [^\]\n]{1,80}\]/;
const NEW_TITLE = "New chat";
const POLL_MS = 1500;
const START_GRACE_MS = 8000;
const RECONCILE_MS = 5000;                 // #238: a busy page asks the seat this often
const OUTPUT_CLIP = 4000;

// ------------------------------------------------------------ pure logic

function escText(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
function escAttr(s) {
  return escText(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function safeUrl(u) {
  return /^(https?:\/\/|mailto:|\/)/i.test(String(u || "").trim());
}

// emphasis + bare links on an ESCAPED text run
function emph(t) {
  return t
    .replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/~~([^~\n]+?)~~/g, "<del>$1</del>")
    .replace(/(^|[\s(])(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g,
      (m, pre, url) => pre + '<a href="' + url.replace(/"/g, "&quot;") +
        '" target="_blank" rel="noopener noreferrer">' + url + "</a>");
}

// [text](url) links on a RAW run, the rest escaped + emphasised
function links(s) {
  let out = "", last = 0, m;
  const re = /\[([^\]\n]+)\]\(([^)\s]+)\)/g;
  while ((m = re.exec(s))) {
    out += emph(escText(s.slice(last, m.index)));
    out += safeUrl(m[2])
      ? '<a href="' + escAttr(m[2]) + '" target="_blank" rel="noopener noreferrer">' +
        emph(escText(m[1])) + "</a>"
      : emph(escText(m[1]));
    last = re.lastIndex;
  }
  return out + emph(escText(s.slice(last)));
}

// inline: `code` is literal, everything else gets links + emphasis
function inline(s) {
  let out = "", last = 0, m;
  const re = /`([^`\n]+)`/g;
  while ((m = re.exec(s))) {
    out += links(s.slice(last, m.index)) + "<code>" + escText(m[1]) + "</code>";
    last = re.lastIndex;
  }
  return out + links(s.slice(last));
}

function tableCells(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

/** A small, SAFE markdown renderer (html is always escaped). */
function md(text) {
  const lines = String(text == null ? "" : text).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let para = [];
  const flush = () => {
    if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; }
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^\s*(```|~~~)\s*([\w+#.-]*)\s*$/);
    if (fence) {
      flush();
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      const lang = fence[2] || "";
      out.push('<pre class="code" data-lang="' + escAttr(lang) + '"><div class="codebar"><span>' +
        escText(lang || "code") + '</span><button class="copy" type="button">copy</button></div><code>' +
        escText(body.join("\n")) + "</code></pre>");
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { flush(); const n = Math.min(6, h[1].length + 2); out.push("<h" + n + ">" + inline(h[2]) + "</h" + n + ">"); continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); continue; }
    if (/^\s*>/.test(line)) {
      flush();
      const q = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
      i--;
      out.push("<blockquote>" + md(q.join("\n")) + "</blockquote>");
      continue;
    }
    const ul = /^\s*[-*+]\s+/, ol = /^\s*\d+[.)]\s+/;
    if (ul.test(line) || ol.test(line)) {
      flush();
      const isOl = ol.test(line) && !ul.test(line);
      const re = isOl ? ol : ul;
      const items = [];
      while (i < lines.length && re.test(lines[i])) items.push(lines[i++].replace(re, ""));
      i--;
      const tag = isOl ? "ol" : "ul";
      out.push("<" + tag + ">" + items.map((t) => "<li>" + inline(t) + "</li>").join("") + "</" + tag + ">");
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush();
      const head = tableCells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(tableCells(lines[i++]));
      i--;
      out.push('<div class="tablewrap"><table><thead><tr>' + head.map((c) => "<th>" + inline(c) + "</th>").join("") +
        "</tr></thead><tbody>" + rows.map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("") +
        "</tbody></table></div>");
      continue;
    }
    para.push(line);
  }
  flush();
  return out.join("");
}

/** SSE: an incremental frame parser over text chunks. */
function sseState() { return { buf: "" }; }
function sseFeed(st, chunk) {
  st.buf += String(chunk).replace(/\r\n/g, "\n");
  const out = [];
  let k;
  while ((k = st.buf.indexOf("\n\n")) >= 0) {
    const raw = st.buf.slice(0, k);
    st.buf = st.buf.slice(k + 2);
    const data = raw.split("\n").filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""));
    if (!data.length) continue;
    try {
      const ev = JSON.parse(data.join("\n"));
      if (ev && typeof ev === "object") out.push(ev);
    } catch (e) { /* undecodable frame: dropped */ }
  }
  return out;
}

function stripSpeaker(text) {
  const t = String(text || "");
  if (!/^\s*\[(?:HA )?user: /.test(t)) return t;
  const nl = t.indexOf("\n");
  return nl >= 0 ? t.slice(nl + 1) : "";
}

/** One chat's live state: ordered rows {info, parts[]} + the status. */
function chatState(sid) {
  return { sid, rows: [], byId: {}, status: { type: "idle" }, dirty: new Set(), all: true, voice: {} };
}

function eventSid(props) {
  return props.sessionID || (props.info && props.info.sessionID) ||
         (props.part && props.part.sessionID) || null;
}

function created(row) { return ((row.info || {}).time || {}).created || 0; }

function ensureRow(st, mid, role) {
  let row = st.byId[mid];
  if (!row) {
    row = { info: { id: mid, role: role || "assistant", time: { created: Date.now() } }, parts: [] };
    st.byId[mid] = row;
    st.rows.push(row);
  }
  return row;
}

function sortRows(st) {
  st.rows.sort((a, b) => created(a) - created(b));
}

/** #238: the page's busy against the seat's /session/status answer for this
 *  session (the seat is the truth; an idle can go missing on the stream). An
 *  idle page is left alone; the seat's busy is adopted with the page's own
 *  clock kept (the elapsed timer runs on); the seat's idle -- or no entry --
 *  wins unless the page sent a moment ago (the seat may not have the turn yet). */
/** #243: the page loaded with build `loaded`; the seat now serves `current`.
 *  "none" (same, or either unknown: an older seat, HA's own conf), "reload",
 *  or "chip" -- an unsent draft is never thrown away, and a reload already
 *  made for this very build in the last minute is not made again (no loop). */
const RELOAD_GUARD_MS = 60000;
function reloadDecision(loaded, current, draft, tried, now) {
  if (!loaded || !current || loaded === current) return "none";
  if (tried && tried.build === current && now - (tried.at || 0) < RELOAD_GUARD_MS) return "chip";
  if (String(draft || "").trim()) return "chip";
  return "reload";
}

function reconcileStatus(local, seat, now) {
  if (!local || local.type !== "busy") return local;
  if (seat && seat.type === "busy") {
    const out = Object.assign({}, seat);
    delete out.elapsed_ms;
    out.since = local.since || (now - (seat.elapsed_ms || 0));
    return out;
  }
  if (local.since && now - local.since < START_GRACE_MS) return local;
  return { type: "idle" };
}

/** Apply one SSE frame; true when it touched this chat. */
function applyEvent(st, ev) {
  if (!ev || typeof ev !== "object") return false;
  const props = ev.properties || {};
  if (eventSid(props) !== st.sid) return false;
  switch (ev.type) {
    case "message.updated": {
      const info = props.info || {};
      if (!info.id) return false;
      const row = st.byId[info.id];
      if (row) row.info = Object.assign({}, row.info, info);
      else { st.byId[info.id] = { info: Object.assign({}, info), parts: [] }; st.rows.push(st.byId[info.id]); }
      sortRows(st);
      st.dirty.add(info.id);
      return true;
    }
    case "message.part.updated": {
      const p = props.part || {};
      if (!p.id || !p.messageID) return false;
      const row = ensureRow(st, p.messageID);
      const i = row.parts.findIndex((x) => x.id === p.id);
      if (i >= 0) row.parts[i] = Object.assign({}, p);
      else row.parts.push(Object.assign({}, p));
      st.dirty.add(p.messageID);
      return true;
    }
    case "message.part.delta": {
      if (!props.messageID || !props.partID) return false;
      const row = ensureRow(st, props.messageID);
      let part = row.parts.find((x) => x.id === props.partID);
      if (!part) { part = { id: props.partID, messageID: props.messageID, type: "text", text: "" }; row.parts.push(part); }
      const f = props.field || "text";
      part[f] = (part[f] || "") + (props.delta || "");
      st.dirty.add(props.messageID);
      return true;
    }
    case "session.status":
      st.status = Object.assign({}, props.status || { type: "idle" });
      if (st.status.type === "busy" && !st.status.since)
        st.status.since = Date.now() - (st.status.elapsed_ms || 0);
      return true;
    case "session.idle":
      st.status = { type: "idle" };
      return true;
    case "membrane.auto":
      st.auto = { on: !!props.on, rounds: props.rounds || 0, mode: props.mode || "explore" };
      return true;
    case "membrane.say": {
      // Argus speaking (voice:out): the voice log keeps it; the page says it
      const ln = props.line || {};
      if (!ln.text) return false;
      st.said = (st.said || []).concat([Object.assign({ who: "argus" }, ln)]).slice(-80);
      return true;
    }
    case "membrane.predict": {
      // membrane's guess of one of Argus's moves: a phantom in its place
      const it = props.item || {};
      if (!it.mid || typeof it.n !== "number") return false;
      st.phantoms = st.phantoms || {};
      const m = st.phantoms[it.mid] = st.phantoms[it.mid] || {};
      m[it.n] = Object.assign({}, m[it.n] || {}, it);
      st.dirty.add(it.mid);
      return true;
    }
    case "membrane.voice": {
      // membrane's own voice: a line beside the user message it answers
      const line = props.line || {};
      if (!line.id || !line.mid) return false;
      st.voice = st.voice || {};
      st.voice[line.mid] = Object.assign({}, st.voice[line.mid] || {}, line);
      st.dirty.add(line.mid);
      return true;
    }
    default:
      return false;
  }
}

/** Replace the transcript with a REST history load. */
const MIND_POLL_MS = 3000;

/** One line of a mind's view as a chat row (the panel's own renderer). */
function mindRow(ts, text) {
  const id = "mind-" + ts + "-" + String(text).length;
  return { info: { id, role: "assistant", time: { created: Math.round((ts || 0) * 1000) } },
           parts: [{ id: id + "-p", type: "text", text: String(text || "") }] };
}

/** A thinker / dreamer entry in words. */
function mindLine(kind, e) {
  e = e || {};
  if (kind === "dreamer") return "dreaming toward " + (e.title || e.goal || "?") + (e.title && e.goal ? "  (" + e.goal + ")" : "");
  const rest = Object.keys(e).filter((k) => k !== "ts" && k !== "kind")
    .map((k) => k + ": " + (typeof e[k] === "string" ? e[k] : JSON.stringify(e[k]))).join(" · ");
  return (e.kind ? "[" + e.kind + "] " : "") + rest;
}

function loadRows(st, rows) {
  st.rows = [];
  st.byId = {};
  for (const r of rows || []) {
    const info = (r && (r.info || r.message)) || {};
    if (!info.id) continue;
    const row = { info: Object.assign({}, info), parts: ((r && r.parts) || []).map((p) => Object.assign({}, p)) };
    st.byId[info.id] = row;
    st.rows.push(row);
  }
  sortRows(st);
  st.all = true;
}

/** The voice's line inside an auto-chat turn's framed text. */
function voiceTurnText(text) {
  const m = /\u00ab([\s\S]*?)\u00bb/.exec(text || "");
  return m ? m[1] : String(text || "").replace(/^\[membrane voice\]\s*/, "");
}

/** The session's voice lines (GET /session/{sid}/voice), keyed by the
 *  user message each one answered. */
function loadVoice(st, lines) {
  st.voice = {};
  for (const l of lines || []) if (l && l.mid) st.voice[l.mid] = Object.assign({}, l);
  st.all = true;
}

/** Membrane's guesses of Argus's moves (GET /session/{sid}/predict), by message and place. */
function loadPhantoms(st, items) {
  st.phantoms = {};
  for (const it of items || []) {
    if (!it || !it.mid || typeof it.n !== "number") continue;
    (st.phantoms[it.mid] = st.phantoms[it.mid] || {})[it.n] = Object.assign({}, it);
  }
}

/** Is this part one of Argus's moves (a text message or a tool call he made)? */
function isMove(p) {
  if (!p || typeof p !== "object") return false;
  if (p.type === "text") return !!(p.text && !p.synthetic);
  return p.type === "tool" && toolView(p).name !== "membrane";
}

/** A phantom: membrane's guess of a move, greyed -- a phrase it expected him
 *  to write, or a tool call; ✓ / ~ (in its top 3) / ✗ once the move is made. */
function phantomHtml(it) {
  if (!it || !it.label) return "";
  const mark = it.pending ? "…" : it.hit ? "✓" : it.top3 ? "~" : "✗";
  const close = typeof it.closeness === "number" ? " " + Math.round(it.closeness * 100) + "%" : "";
  const title = it.pending ? "membrane expects this next" : "membrane guessed this; " + SEAT + " did: " + (it.actual || "?");
  return '<div class="phantom ' + (it.kind === "tool" ? "ptool" : "ptext") + (it.pending ? " pending" : "") +
    '" title="' + escAttr(title) + '"><span class="pmark">' + mark + close + "</span> " +
    (it.kind === "tool" ? "🔧 " : "") + escText(it.label) + "</div>";
}

/** The voice's bubble: its line, its closeness to the LLM's reply, and
 *  the teaching controls (👍 / 👎 / say it better). */
function voiceHtml(line, editing) {
  if (!line || !line.text) return "";
  const c = typeof line.closeness === "number" ? '<span class="vclose" title="closeness to the LLM\'s reply">' +
    Math.round(line.closeness * 100) + "%</span>" : "";
  const up = line.vote === "up" ? " on" : "", down = line.vote === "down" ? " on" : "";
  const id = escAttr(line.id);
  const mode = line.mode ? '<span class="vmode">· ' + escText(line.mode) + "</span> " : "";
  let h = '<div class="vox" data-vid="' + id + '"><div class="vwho">membrane ' + mode + c + "</div>" +
    '<div class="vtext">' + escText(line.text) + "</div>" +
    '<div class="vbar"><button class="vbtn' + up + '" data-vvote="up" title="good">👍</button>' +
    '<button class="vbtn' + down + '" data-vvote="down" title="not like that">👎</button>' +
    '<button class="vbtn" data-vedit="1" title="say it better">✎</button></div>';
  if (line.rewrite) h += '<div class="vre">you taught: ' + escText(line.rewrite) + "</div>";
  h += guessHtml(line);
  if (editing) h += '<div class="vedit"><textarea rows="2" placeholder="how membrane should have said it"></textarea>' +
    '<button class="btn primary vteach">teach</button></div>';
  return h + "</div>";
}

/** The connection's word in the status pill (2026-10-11, the operator: "can it
 *  say 'loading' while loading?"): nothing while live; "loading…" until the
 *  first live stream; "connecting…" for RECONNECT_GRACE_MS after a drop;
 *  only then "offline" (or "polling", when the fallback poll runs). */
const RECONNECT_GRACE_MS = 15000;
function connLabel(live, everLive, since, now, polling) {
  if (live) return "";
  if (!everLive) return "loading…";
  if (since && now - since < RECONNECT_GRACE_MS) return "connecting…";
  return polling ? "polling" : "offline";
}

/** The voice log: what was said aloud, oldest first -- the person's spoken
 *  messages (voiceIn rows), Argus's voice:out lines, membrane's own lines. */
function voiceLogItems(rows, said, voiceLines) {
  const out = [];
  for (const r of rows || []) {
    const i = (r && r.info) || {};
    if (!i.voiceIn) continue;
    const t = (r.parts || []).filter((p) => p.type === "text" && !p.synthetic).map((p) => p.text || "").join(" ");
    out.push({ ts: ((i.time || {}).created || 0), who: "you", label: "🎙 you", text: stripSpeaker(t).replace(/^\s*🎙\s*/, "") });
  }
  const seen = new Set();
  for (const x of said || []) {
    const key = Math.round((x.ts || 0)) + ":" + x.text;
    if (!x.text || seen.has(key)) continue;
    seen.add(key);
    out.push({ ts: (x.ts || 0) * 1000, who: "argus", label: "🔊 " + SEAT, text: x.text });
  }
  for (const v of voiceLines || []) {
    if (v && v.text) out.push({ ts: v.time || 0, who: "membrane", label: "membrane", text: v.text });
  }
  return out.sort((a, b) => a.ts - b.ts);
}

/** The seat's pause, for its button: ▶ to resume when paused. */
function seatView(st) {
  const paused = !!(st && st.paused);
  const mem = st && st.seat_rss_mb ? " · seat " + st.seat_rss_mb + " MB" + (st.mem_available_mb != null ? ", " + st.mem_available_mb + " MB free" : "") : "";
  const b = st && st.battery;
  const bat = b && b.percentage != null ? " · phone " + b.percentage + "% " + String(b.status || "").toLowerCase() : "";
  return paused
    ? { paused, icon: "▶", label: "seat paused: resume", title: "the seat is PAUSED: loops, dreamer, thinker and night stand down -- click to resume" + mem + bat }
    : { paused, icon: "⏸", label: "pause the seat", title: "pause the seat: every acting lane stands down (loops, dreamer, thinker, night)" + mem + bat };
}

// #277: the operator named self Blurp. The seat owns the name (self_sheet.NAME:
// the sheet's header, the dock state) and the page says it wherever it shows
// self to a person; ids, record fields and the code's own words stay "self"
let SELF_NAME = "Blurp";
function selfName() { return SELF_NAME; }
/** The seat's name for self (the sheet / dock carry it); an empty one changes nothing. */
function setSelfName(n) {
  n = String(n == null ? "" : n).trim();
  if (n) SELF_NAME = n;
  return SELF_NAME;
}

/** #161 / #299: self's sleep and wake -- a plain on/off toggle (the 🌱
 *  growth-limit chooser it was gave way to acceptance: an earned achievement
 *  opens what it leads to once the operator accepts it) -- and its rung +
 *  exp bar. `on`: awake; `icon` / `label` / `title`: the toggle's face. */
function selfView(st) {
  const s = (st && st.self) || { mode: "off" };
  const loaded = !!(st && st.self && Array.isArray(st.self.ladder));   // the seat's real state, not the placeholder
  const wait = Array.isArray(s.awaiting) && s.awaiting.length ? " · awaiting your accept: " + s.awaiting.join(", ") : "";
  if (s.mode === "off") return { loaded, on: false, icon: "💤", label: SELF_NAME + " asleep", status: "🌱 asleep", exp: selfExp(st),
                                 title: SELF_NAME + " (#161) is asleep: nothing runs, nothing is erased -- tap to wake it" + wait };
  const pct = Math.round(100 * (s.bar || 0));
  const rung = "rung " + (s.rung || 0) + (s.rung_name ? " (" + s.rung_name + ")" : "");
  const why = s.held ? " · HELD (3 failed cycles; switch it off and on to clear)" : s.waiting_on ? " · waiting on: " + s.waiting_on : "";
  const nxt = s.next == null && s.bar >= 1 ? " · the next rung is not built yet" : "";
  const slip = s.slipping ? " · slipping" : "";
  const nx = Array.isArray(s.next) ? s.next.join(", ") : s.next;
  const goal = s.next == null || nx === "" ? "top built rung" : "→ " + nx;
  const status = s.held ? "🥀 held: switch it off and on to clear"
    : "rung " + (s.rung || 0) + " ✓ · " + pct + "% " + goal + (s.waiting_on ? " (" + s.waiting_on + ")" : "") + (s.slipping ? " · slipping" : "");
  // #258: rung 7's numbers -- the window self learned, how often its lines
  // land there against chance, and the replies its timing gains
  const f2 = (x) => (x == null ? "–" : Number(x).toFixed(2));
  const r7 = s.rung7 || {};
  const r7s = r7.checked ? " · rung 7: " + r7.checked + " timed lines checked"
    + (r7.best ? ", best window " + r7.best : "")
    + (r7.share != null ? ", in it " + f2(r7.share) + " vs chance " + f2(r7.chance) : "")
    + (r7.payoff != null ? ", replies " + skillSigned(r7.payoff) + " over timing-blind" : "") : "";
  // #217 / #254: rung 8's diagnostics (rung 7 until #258) -- how many asked
  // pushes the model answered, and self's Brier against the running rate's
  // (lower is better; self must beat the rate), with the model's alone and
  // the weight self gives it
  const r8 = s.rung8 || {};
  const r8s = (r8.asked || r8.brier_self != null) ? " · rung 8: " + (r8.paired || 0) + " of " + (r8.checked || 0) + " asked pushes paired"
    + (r8.brier_mix != null ? ", Brier " + SELF_NAME + " " + f2(r8.brier_mix) + " vs rate " + f2(r8.brier_rate) : "")
    + (r8.brier_model != null ? ", model alone " + f2(r8.brier_model) + " (weight " + f2(r8.advisor_weight) + ")" : "")
    + (r8.base_rate != null ? ", base rate " + f2(r8.base_rate) : "") : "";
  return { loaded, on: true, icon: "🌱", label: SELF_NAME + " awake", status, exp: selfExp(st),
           title: SELF_NAME + " (#161): " + s.mode + " · tap to put it to sleep · " + rung + " · " + pct + "% toward the next" + why + slip + nxt
                  + wait + " · " + (s.cycles || 0) + " cycles" + r7s + r8s };
}

// ---- #230: SELF IN THE UI -- the exp bar, the activity lines, the report card.
// Everything here only reads status() and self's record: nothing is claimed
// about self that the record doesn't measure, and nothing here changes it
// (the 🌱 sleep toggle and the operator's accept, #299, are the only switches).
const SELF_RUNGS = { 0: "sleep and wake", 1: "feel itself", 2: "first reach", 3: "tell things apart", 4: "patience",
  5: "notice the world changed", 6: "meet an other", 7: "time its turn", 8: "expect a reply",
  9: "recognize itself", 10: "change itself", 11: "speak" };

function rungName(s, n) {
  const r = ((s && s.ladder) || []).find((x) => x.rung === n);
  return (r && r.name) || SELF_RUNGS[n] || "";
}

/** membrane::Component:toy:dice -> toy:dice (the name a thing goes by). */
function shortEid(eid) {
  return String(eid == null ? "" : eid).replace(/^membrane::(?:[A-Z][A-Za-z]*:)?/, "");
}

/** The banner as a game's experience bar: the level is the rung, the bar
 *  fills toward the next from status().bar (the staged test, never effort). */
/** A skill as the bars show it: its sign always written (−0.13, +0.15). */
function skillSigned(v) {
  v = Number(v);
  return (v < 0 ? "−" : "+") + Math.abs(v).toFixed(2);
}

function selfExp(st) {
  const s = (st && st.self) || { mode: "off" };
  const n = s.rung || 0;
  const fill = Math.max(0, Math.min(1, Number(s.bar) || 0));
  const pct = Math.round(100 * fill);
  const level = "Lv " + n + " · " + (s.rung_name || rungName(s, n));
  const nextName = s.next != null ? rungName(s, s.next) : "";
  const goal = s.next == null ? "top built rung (the next is not built yet)" : "→ " + s.next + (nextName ? " · " + nextName : "");
  let state = "growing";
  if (s.mode === "off") state = "asleep";
  else if (s.held) state = "held";
  else if (s.slipping) state = "slipping";
  else if (s.waiting_on === "your accept") state = "capped";             // #299: an earned one waits for the operator
  else if (s.next == null) state = "top";
  const caption = state === "asleep" ? "asleep: nothing runs, nothing is erased"
    : state === "held" ? "held: 3 failed cycles in a row (switch it off and on to clear)"
    : state === "capped" ? "earned, awaiting your accept: accept it to open what it leads to"
    : (state === "slipping" ? "slipping: rung " + n + "'s test fails now" + (s.waiting_on ? " · " : "") : "")
      + (s.waiting_on ? "waiting on: " + s.waiting_on : "");
  const label = state === "asleep" ? "asleep" : state === "held" ? "held" : state === "capped" ? "accept?" : pct + "%";
  // #249 (the operator: "I don't see the skill level anywhere"): the skill
  // the bar's test reads, against the line it has to beat
  const sk = s.mode !== "off" && s.skill != null && isFinite(Number(s.skill))
    ? "skill " + (s.skill_measured === false ? "not measured yet" : skillSigned(s.skill)) + (s.skill_pass != null ? " / " + skillSigned(s.skill_pass) : "") : "";
  const cap = caption + (sk ? (caption ? " · " : "") + sk : "");
  // #267: the rung just reached unlocked a toy -- the level-up says so
  const u = s.unlock;
  const newToy = u && u.rung === n && n > 0 && !u.late && Array.isArray(u.toys) && u.toys.length
    ? unlockText({ toys: u.toys.map((t) => (t && t.kind) || t) }) : "";
  return { state, rung: n, level: state === "asleep" ? "🌱 asleep" : level, goal, pct, fill, label, caption: cap, skill: sk, newToy,
           title: SELF_NAME + " (#161): " + level + " · " + label + " " + goal + (cap ? " · " + cap : "") };
}

/** The level-up moment's caption (the exp bar's gold): the level, and the new toy it unlocked (#267). */
function levelUpText(x) {
  return "⬆ level up: " + ((x && x.level) || "") + (x && x.newToy ? " · " + x.newToy : "");
}

// #267: toys unlock as self grows (membrane/toy_unlocks.py ICONS)
const TOY_ICONS = { body: "🫧", fast: "⚡", slow: "🐢", tower: "🗼", burst: "✦", die: "🎲", drift: "〰️",
                    ball: "⚽", mirror: "🪞", blocks: "🧱", rattle: "🔊" };
/** "new toy: 🪞 mirror" / "new toys: 🗼 tower, ⚽ ball" from an unlock line. */
function unlockText(r) {
  const ks = ((r && r.toys) || []).map(String);
  return (ks.length === 1 ? "new toy: " : "new toys: ") + ks.map((k) => (TOY_ICONS[k] ? TOY_ICONS[k] + " " : "") + k).join(", ");
}

const XP_CSS = `
  .xp { --xp-gold:#d4a017; display:inline-grid; grid-template-columns:auto minmax(56px,110px) auto; column-gap:6px; row-gap:1px;
        align-items:center; font-size:12px; line-height:1.2; vertical-align:middle; max-width:100%; }
  .xp-lv { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:14em; }
  .xp-track { position:relative; height:8px; border-radius:999px; overflow:hidden;
              background:color-mix(in srgb, var(--divider-color,#888) 70%, transparent);
              box-shadow:inset 0 0 0 1px color-mix(in srgb, var(--primary-text-color,#ddd) 15%, transparent); }
  .xp-fill { position:absolute; left:0; top:0; bottom:0; border-radius:inherit; transition:width .6s ease;
             background:linear-gradient(90deg, color-mix(in srgb, var(--primary-color,#3477d6) 65%, #2e9d5b), var(--primary-color,#3477d6)); }
  .xp-pct { font-variant-numeric:tabular-nums; opacity:.85; white-space:nowrap; }
  .xp-sk { grid-column:1 / -1; font-size:11px; font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }
  .xp-sk[hidden] { display:none; }
  .xp-sub { grid-column:1 / -1; font-size:10.5px; opacity:.7; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .xp-slipping .xp-fill { background:var(--warning-color,#ffa600); }
  .xp-slipping .xp-pct, .xp-slipping .xp-sub { color:var(--warning-color,#ffa600); opacity:1; }
  .xp-held { opacity:.6; }
  .xp-held .xp-fill { background:color-mix(in srgb, var(--primary-text-color,#888) 45%, transparent); }
  .xp-capped .xp-fill { background:repeating-linear-gradient(45deg, color-mix(in srgb, var(--primary-color,#3477d6) 60%, transparent) 0 4px,
                                    color-mix(in srgb, var(--primary-color,#3477d6) 25%, transparent) 4px 8px); }
  .xp-asleep .xp-track, .xp-asleep .xp-pct { display:none; }
  .xp.lvup .xp-lv { color:var(--xp-gold); }
  .xp.lvup .xp-track { animation:xplvup 1.6s ease-out; }
  @keyframes xplvup { 0% { box-shadow:0 0 0 0 color-mix(in srgb, var(--xp-gold) 80%, transparent); }
                      40% { box-shadow:0 0 0 6px color-mix(in srgb, var(--xp-gold) 30%, transparent); }
                      100% { box-shadow:0 0 0 0 transparent; } }
  @media (prefers-reduced-motion: reduce) {
    .xp-fill { transition:none; }
    .xp.lvup .xp-track { animation:none; box-shadow:0 0 0 2px var(--xp-gold); }
  }`;

/** Paint the exp bar into el (built once, then updated in place so the fill
 *  glides). A new rung gets a brief level-up moment (still under reduced motion:
 *  the gold and the "level up" caption stay, the glow doesn't pulse). */
function paintExp(el, x) {
  if (!el || !x) return;
  const root = el.getRootNode ? el.getRootNode() : null;
  if (root && root.querySelector && !root.querySelector("style[data-xp]")) {
    const st = document.createElement("style");
    st.setAttribute("data-xp", "");
    st.textContent = XP_CSS;
    (root.head || root).appendChild(st);
  }
  let box = el.querySelector(":scope > .xp");
  if (!box) {
    el.textContent = "";
    box = document.createElement("span");
    box.innerHTML = '<span class="xp-lv"></span><span class="xp-track" role="progressbar" aria-valuemin="0" aria-valuemax="100">' +
      '<span class="xp-fill"></span></span><span class="xp-pct"></span><span class="xp-sk"></span><span class="xp-sub"></span>';
    el.appendChild(box);
  }
  const prev = el._xpRung;
  el._xpRung = x.state === "asleep" ? null : x.rung;
  const up = prev != null && el._xpRung != null && el._xpRung > prev;
  if (up) {
    el._xpUpUntil = Date.now() + 2500;
    clearTimeout(el._xpUpT);
    el._xpUpT = setTimeout(() => { el._xpUpUntil = 0; box.classList.remove("lvup"); paintExp(el, el._xpLast); }, 2600);
  }
  el._xpLast = x;
  const lvup = (el._xpUpUntil || 0) > Date.now();
  box.className = "xp xp-" + x.state + (lvup ? " lvup" : "");
  box.title = x.title;
  box.querySelector(".xp-lv").textContent = x.level;
  const tr = box.querySelector(".xp-track");
  tr.setAttribute("aria-valuenow", String(x.pct));
  tr.setAttribute("aria-label", x.state === "asleep" ? SELF_NAME + " is asleep" : "toward " + x.goal.replace(/^→ /, "rung "));
  box.querySelector(".xp-fill").style.width = (x.state === "capped" ? 100 : x.pct) + "%";
  box.querySelector(".xp-pct").textContent = x.label;
  const sk = box.querySelector(".xp-sk");                  // #249: the skill at a glance
  if (sk) { sk.textContent = x.skill || ""; sk.hidden = !x.skill; }
  const sub = x.skill ? x.caption.replace(new RegExp("( · )?" + x.skill.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"), "") : x.caption;
  box.querySelector(".xp-sub").textContent = lvup ? levelUpText(x)
    : (x.state === "asleep" ? "" : x.goal + (sub ? " · " + sub : ""));
}

const f2s = (x) => String(Math.round(Number(x) * 100) / 100);

/** One record line, plainly: what self did and what came of it. */
function selfLine(r) {
  r = r || {};
  const k = r.kind;
  if (k === "reach") {
    const t = "reach " + shortEid(r.eid) + " → ";
    const tail = (r.audit ? " · audit" : "") + (r.reply ? " · reply check" : "") +
      (Array.isArray(r.by) && r.by.length ? " · by " + r.by.join(", ") : "");   // #244: who changed the toy (ground truth)
    if (r.answer !== "changed" && r.answer !== "unchanged") return t + "no answer" + (r.why ? " (" + r.why + ")" : "") + tail;
    return t + r.answer + " (p " + f2s(r.p) + (r.p_self != null ? ", " + SELF_NAME + " " + f2s(r.p_mix != null ? r.p_mix : r.p_self) : "") +
      (r.p_llm != null ? ", model " + f2s(r.p_llm) : "") + ")" + tail;
  }
  if (k === "rung_void") return "rung " + r.rung + "'s earlier pass voided (" + (r.why || "") + ")";
  if (k === "rung7_restart") return "the old rung 7 (understand an other, now rung 8) restarted: now grades " + SELF_NAME + ", not the model";
  if (k === "push") return "push → " + shortEid(r.to) +
    (r.aim != null ? (r.why === "overdue" ? " · its wait ran out" : " · its moment: " + turnWindow(r)) : "") +
    (r.asked ? " (asked the model)" : "");
  if (k === "shift") return "shift noticed on " + shortEid(r.eid) + (r.was != null && r.now != null ? " (" + f2s(r.was) + " → " + f2s(r.now) + ")" : "");
  if (k === "wait") return "wait";
  if (k === "cycle") return (r.act || "move") + (r.still ? " (still world)" : r.gap != null ? " (gap " + Number(r.gap).toFixed(3) + ")" : "");
  if (k === "rung") return "rung " + r.rung + " passed · " + (SELF_RUNGS[r.rung] || "");
  if (k === "error") return "error" + (r.act ? " in " + r.act : "") + (r.why ? ": " + r.why : "");
  if (k === "birth") return "born";
  if (k === "unlock") return unlockText(r) + (r.rung ? " (rung " + r.rung + " passed)" : " (born with it)");
  if (k === "audit") return "audit " + (r.phase || "") + (r.eid ? " on " + shortEid(r.eid) : "");
  return String(k || "?");
}

// #258: rung 7's windows -- how long since the other last spoke (self_loop.TURN_NAMES)
const TURN_NAMES = ["0-2m", "2-10m", "10-30m", "30m+"];
// #278: the ball's own clock -- seconds since the other last played (self_loop.TOY_TURN_NAMES)
const TOY_TURN_NAMES = ["0-20s", "20-60s", "1-3m", "3m+"];

/** A timed turn's window, named on its own clock (#278: a turn with `clock: 2` is the ball's). */
function turnWindow(r) {
  if (r.medium === "toy" && r.clock === 2) return (TOY_TURN_NAMES[r.bin] || r.bin) + " after they played";
  return (TURN_NAMES[r.bin] || r.bin) + " after they " + (r.medium === "toy" ? "played" : "spoke");
}

function agoText(s) {
  s = Math.max(0, Math.round(s));
  return s < 60 ? s + "s" : s < 3600 ? Math.round(s / 60) + "m" : Math.round(s / 3600) + "h";
}

/** The activity feed (newest first), escaped. nowS: the page's clock, seconds. */
function selfLinesHtml(lines, nowS) {
  if (!lines || !lines.length) return '<li class="sl none">nothing on the record yet</li>';
  const now = nowS == null ? Date.now() / 1000 : nowS;
  return lines.map((r) => '<li class="sl k-' + escAttr(r.kind || "") + '"><span class="ago">' +
    (r.ts ? agoText(now - r.ts) : "") + "</span> " + escText(selfLine(r)) + "</li>").join("");
}

/** #244: the play buttons a toy row offers -- [{action, arg, label, title}].
 *  A tower BLOCK: knock / rebuild (the whole tower takes none: its blocks do);
 *  die: roll; drift / burst: nudge; body (self's alone), the clocks and the
 *  script toys: none. */
const TOY_PLAY = {
  die: [["roll", "🎲 roll", "roll the die: a fresh face, tagged as yours"]],
  drift: [["nudge", "↕ nudge", "nudge it one step, tagged as yours"]],
  burst: [["nudge", "✦ nudge", "nudge it to a fresh value, tagged as yours"]],
  ball: [["roll", "roll it back", "roll the ball back to {self}: your turn (rung 7), tagged as yours"]],
};
function toyActions(t) {
  t = t || {};
  if (!t.toy || t.locked) return [];
  if (t.kind === "tower") {
    if (!t.block) return [];
    return [{ action: "knock", arg: t.block, label: "knock", title: "knock block " + t.block + " (and the ones above it) down" },
            { action: "rebuild", arg: t.block, label: "rebuild", title: "stand block " + t.block + " back up" }];
  }
  return (TOY_PLAY[t.kind] || []).map(([action, label, title]) => ({ action, arg: null, label, title: title.replace("{self}", SELF_NAME) }));
}

function toyButtonsHtml(t) {
  const acts = toyActions(t);
  if (!acts.length) return "";
  return ' <span class="play">' + acts.map((a) => '<button type="button" class="pk" data-poke-toy="' + escAttr(t.toy) +
    '" data-poke-action="' + escAttr(a.action) + '"' + (a.arg != null ? ' data-poke-arg="' + escAttr(a.arg) + '"' : "") +
    ' title="' + escAttr(a.title) + '">' + escText(a.label) + "</button>").join("") + "</span>";
}

/** The report card: the ladder, rungs 7 and 8's numbers, curiosity, the world, the toys. */
function selfCardHtml(st) {
  const s = st && st.self;
  if (!s || !Array.isArray(s.ladder)) return '<p class="muted">no status yet</p>';
  const x = selfExp(st);
  const out = [];
  out.push('<p class="head">' + escText(x.level) + " · " + escText(x.label) +
    (s.mode === "off" ? " · asleep: nothing runs (its record is kept)" : " · " + escText(s.mode)) +
    (Array.isArray(s.awaiting) && s.awaiting.length ? " · awaiting your accept: " + escText(s.awaiting.join(", ")) : "") +
    " · " + (s.cycles || 0) + " cycles</p>");
  const mark = { passed: "✓ passed", current: "◀ current", capped: "awaiting your accept", locked: "locked", unbuilt: "not built" };
  out.push('<h4>the ladder</h4><ol class="ladder">' + s.ladder.map((r) => {
    const st2 = r.state === "passed" && r.rung === s.rung && s.slipping ? "slipping" : r.state;
    const lbl = st2 === "slipping" ? "⚠ slipping" : (mark[st2] || st2);
    return '<li class="st-' + escAttr(st2) + '"><span class="rn">' + r.rung + "</span> " + escText(r.name) +
      ' <span class="rs">' + escText(lbl) + "</span></li>";
  }).join("") + "</ol>");
  const r7 = s.rung7 || {};
  const ball = r7.medium === "toy";                    // #264: the turns are with the ball
  out.push("<h4>rung 7: taking turns" + (ball ? " with the ball" : "") + "</h4>" + (r7.checked
    ? "<p><b>" + r7.checked + "</b> " + (ball ? "turns with the ball" : "timed lines") + " with a reply check" +
      (r7.best ? " · best window <b>" + escText(r7.best) + "</b> after they " + (ball ? "played" : "spoke") : "") +
      "<br>" + (r7.bins || []).map((b) => escText(b.bin) + " " + (b.rate == null ? "–" : f2s(b.rate)) + ' <span class="muted">(' + b.replied + "/" + b.pushes + ")</span>").join(" · ") +
      (r7.share != null ? "<br>" + (ball ? "turns" : "lines") + " in its window <b>" + f2s(r7.share) + "</b> vs chance " + f2s(r7.chance) + " (z " + Number(r7.z || 0).toFixed(1) + ")" +
        "<br>replied <b>" + f2s(r7.reply_rate) + "</b> vs timing-blind " + f2s(r7.blind_rate) + " (t " + Number(r7.payoff_z || 0).toFixed(1) + ")" : "") + "</p>"
    : '<p class="muted">' + (ball ? "no turns with the ball checked yet" : "no timed lines checked yet") + "</p>"));
  const r8 = s.rung8 || {};
  out.push("<h4>rung 8: the other</h4>" + (r8.asked || r8.brier_self != null
    ? "<p><b>" + (r8.paired || 0) + " of " + (r8.checked || 0) + "</b> asked pushes paired (" + (r8.asked || 0) + " asked)" +
      (r8.brier_mix != null ? "<br>Brier " + escText(SELF_NAME) + " <b>" + f2s(r8.brier_mix) + "</b> vs running rate <b>" + f2s(r8.brier_rate) + "</b> (lower is better)" +
        " · " + escText(SELF_NAME) + " alone " + f2s(r8.brier_self) : "") +
      (r8.brier_model != null ? "<br>the model alone " + f2s(r8.brier_model) + " · its weight " + f2s(r8.advisor_weight) +
        (r8.advisor_model ? " (" + escText(r8.advisor_model) + ")" : "") : "") +
      (r8.base_rate != null ? "<br>base rate " + f2s(r8.base_rate) : "") + "</p>"
    : '<p class="muted">no reply checks yet</p>'));
  const cur = s.curiosity || [];
  out.push("<h4>curiosity</h4>" + (cur.length
    ? "<ol>" + cur.map((c) => "<li>" + escText(shortEid(c.eid)) + ' <span class="muted">progress ' + f2s(c.progress) +
        " · error " + f2s(c.error) + "</span></li>").join("") + "</ol>"
    : '<p class="muted">nothing yet (a thing needs 60 looks)</p>'));
  const world = s.world || {}, we = Object.keys(world).sort();
  const counts = {};
  for (const e of we) counts[world[e]] = (counts[world[e]] || 0) + 1;
  out.push("<h4>the world</h4>" + (we.length
    ? "<p>" + Object.keys(counts).sort().map((k) => escText(k) + " " + counts[k]).join(" · ") + "</p><ul>" +
      we.map((e) => "<li>" + escText(shortEid(e)) + ' <span class="muted">' + escText(world[e]) + "</span></li>").join("") + "</ul>"
    : '<p class="muted">not sorted yet</p>'));
  const split = s.split || {}, se = Object.keys(split).sort();
  const side = (k) => se.filter((e) => split[e] === k).map((e) => escText(shortEid(e))).join(", ") || "–";
  out.push("<h4>body / other</h4>" + (se.length
    ? '<p><span class="muted">body:</span> ' + side("body") + '<br><span class="muted">other:</span> ' + side("other") + "</p>"
    : '<p class="muted">no split yet (rung 6)</p>'));
  const toys = s.toys || [];
  const dot = { ok: "ok", empty: "warn", unread: "idle", "no getter": "bad" };
  out.push("<h4>toys</h4>" + (toys.length
    ? '<ul class="toys">' + toys.map((t) => '<li title="' + escAttr(t.health) + '"><span class="dot ' + (dot[t.health] || "idle") + '"></span>' +
        escText(shortEid(t.eid)) + ' <span class="muted">' + (t.getter ? "getter ✓" : "no getter") +
        (t.reads ? " · " + t.answered + " of last " + t.reads + " reads answered" : t.getter ? " · not read yet" : "") + "</span>" +
        toyButtonsHtml(t) + "</li>").join("") + "</ul>"
    : '<p class="muted">no toys (toy: / cradle: things, or words with a getter)</p>'));
  return out.join("");
}

// ---- #249: SELF'S CHARACTER SHEET -- the 👶 panel laid out like an RPG
// character's (the structure, not the styling): the nameplate, attributes, the
// quest log, achievements, the growth curve, where the time went, the
// inventory and the relationships. Every number comes from GET
// /membrane/self/sheet (membrane/self_sheet.py); nothing here computes a
// claim the record doesn't hold. No chart library: inline SVG and divs.
// #300: the attributes are traits of self as a whole, measured from its record
// (never a skill's test score, never a gate); "Learning now" is the skills it is
// learning (open and allowed), side by side; earned skills live in the Skill
// tree tab (the section key stays "quest" so a remembered open/closed holds).
const SHEET_SECTIONS = [
  ["attributes", "Attributes"], ["quest", "Learning now"],
  ["growth", "Growth curve"], ["time", "Where the time went"], ["inventory", "Inventory"], ["toys", "Toys"],
  ["relationships", "Relationships"]];
const SHEET_OPEN = { attributes: true, quest: true };     // the defaults; a remembered choice wins

const r1 = (x) => Math.round(x * 10) / 10;
const clamp01 = (x) => Math.max(0, Math.min(1, x));
const fixed2 = (v) => (v == null || !isFinite(Number(v)) ? "–" : Number(v).toFixed(2));
const signed2 = (v) => (Number(v) < 0 ? "−" : "") + Math.abs(Number(v)).toFixed(2);

/** One attribute's bar, as percents of its track: a skill can go negative
 *  (its track is then −1..1 with a zero line), a share is 0..1, an error is
 *  0..0.5 and lower is better. `mark` is the pass line. */
function attrBar(a) {
  a = a || {};
  const v = a.value == null || !isFinite(Number(a.value)) ? null : Number(a.value);
  const kind = a.kind || "skill";
  const lo = kind === "error" ? 0 : kind === "skill" && v != null && v < 0 ? -1 : 0;
  const hi = kind === "error" ? 0.5 : 1;
  const pos = (x) => r1(100 * clamp01((x - lo) / (hi - lo)));
  const better = a.better || (kind === "error" ? "lower" : "higher");
  const pass = Number(a.pass);
  const zero = pos(0);
  if (v == null) return { lo, hi, zero, from: zero, to: zero, mark: pos(pass), met: false, neg: false, none: true, text: "not measured yet" };
  const p = pos(v);
  const met = a.met != null ? !!a.met : (better === "lower" ? v <= pass : v >= pass);
  return { lo, hi, zero, from: Math.min(zero, p), to: Math.max(zero, p), mark: pos(pass), met, neg: v < 0, none: false,
           text: v < 0 ? signed2(v) : v.toFixed(2) };
}

/** Hours as a person reads them. */
function hoursText(h) {
  if (h == null || !isFinite(Number(h))) return "–";
  h = Number(h);
  return h < 1 ? Math.round(h * 60) + " min" : h.toFixed(1) + " h";
}

const CURVE_TICKS = [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 24, 48, 96, 168, 336, 720];

/** The growth curve's geometry: x is hours on a log scale, y the rung.
 *  Steps for the fastest possible (fast), working hours (work) and the wall
 *  clock (wall); the child-scaled open points (kids); where it is now. */
function curveGeom(g, W, H) {
  g = g || {};
  const rows = (g.rows || []).filter((r) => r && r.rung != null);
  const m = { t: 12, r: 14, b: 24, l: 26 };
  const vals = [g.now_working_h, g.now_wall_h];
  for (const r of rows) vals.push(r.fastest_h, r.working_h, r.wall_h, r.child_h);
  const pos = vals.filter((v) => v != null && isFinite(v) && v > 0).map(Number);
  const lo = Math.min(0.03, pos.length ? Math.min(...pos) / 1.5 : 0.03);
  const hi = Math.max(1, pos.length ? Math.max(...pos) * 1.15 : 1);
  const top = Math.max(1, ...rows.map((r) => r.rung));
  const L = Math.log10;
  const x = (h) => r1(m.l + (W - m.l - m.r) * (L(Math.max(lo, Math.min(hi, h))) - L(lo)) / (L(hi) - L(lo)));
  const y = (n) => r1(H - m.b - (H - m.t - m.b) * n / top);
  const steps = (key, end) => {
    let d = "M" + x(lo) + "," + y(0), last = 0;
    for (const r of rows) {
      if (r[key] == null) continue;
      d += " L" + x(r[key]) + "," + y(r.rung - 1) + " L" + x(r[key]) + "," + y(r.rung);
      last = r.rung;
    }
    if (end != null) d += " L" + x(end) + "," + y(last);
    return { d, last };
  };
  const fast = steps("fastest_h", hi), work = steps("working_h", g.now_working_h || lo), wall = steps("wall_h", g.now_wall_h || lo);
  const ticks = CURVE_TICKS.filter((h) => h >= lo && h <= hi).map((h) => ({ h, x: x(h), label: hoursText(h) }));
  const xOf = {}, yOf = {};
  for (const t of ticks) xOf[String(t.h)] = t.x;
  for (let n = 0; n <= top; n++) yOf[String(n)] = y(n);
  const kids = rows.filter((r) => r.child_h != null && r.child_h > 0)
    .map((r) => ({ rung: r.rung, x: x(r.child_h), y: y(r.rung), h: r.child_h, months: r.child_months, what: r.child_milestone || "" }));
  return { m, lo, hi, top, x0: x(lo), xOf, yOf, ticks, fast: fast.d, work: work.d, wall: wall.d, kids,
           now: { x: x(g.now_working_h || lo), y: y(work.last), rung: work.last } };
}

function curveSvg(g, W, H) {
  const c = curveGeom(g, W, H);
  const grid = Object.keys(c.yOf).map((n) => '<line x1="' + c.m.l + '" x2="' + (W - c.m.r) + '" y1="' + c.yOf[n] + '" y2="' + c.yOf[n] +
    '" class="cg"/><text x="' + (c.m.l - 6) + '" y="' + (c.yOf[n] + 3.5) + '" text-anchor="end" class="ct">' + n + "</text>").join("");
  const ticks = c.ticks.filter((t, i) => W >= 420 || i % 2 === 0).map((t) => '<text x="' + t.x + '" y="' + (H - 6) +
    '" text-anchor="middle" class="ct">' + escText(t.label) + "</text>").join("");
  const kids = c.kids.map((k) => '<circle cx="' + k.x + '" cy="' + k.y + '" r="3.5" class="ck"><title>' +
    escText("rung " + k.rung + ": a child at " + (k.months < 1 ? "birth" : k.months + " months") + " " + k.what +
      " · scaled to " + hoursText(k.h)) + "</title></circle>").join("");
  return '<svg class="curve" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" aria-label="' +
    escAttr("rungs passed over working hours: rung " + c.now.rung + " at " + hoursText(g && g.now_working_h) +
      " of working time; the dashed line is the fastest possible, the open points a child rescaled") + '">' +
    grid + ticks +
    '<path d="' + c.fast + '" class="cf" stroke-dasharray="5 4"/>' +
    '<path d="' + c.wall + '" class="cw"/>' +
    '<path d="' + c.work + '" class="cs"/>' + kids +
    '<circle cx="' + c.now.x + '" cy="' + c.now.y + '" r="4" class="cn"><title>now</title></circle></svg>';
}

/** Where the time went: alive = working + blocked by a known bug + at a wall
 *  (#305: no built skill to learn -- we hadn't built the next one; a seat
 *  that sends no walls_h has none to show) + not running. */
function timeParts(t) {
  t = t || {};
  const alive = Number(t.alive_h) || 0;
  const part = (key, label, h) => ({ key, label, h: Number(h) || 0, pct: alive > 0 ? Math.round(100 * (Number(h) || 0) / alive) : 0 });
  return [part("working", "working", t.working_h), part("bugs", "blocked by a known bug", t.bugs_h)]
    .concat(t.walls_h == null ? [] : [part("walls", "at a wall: its next skill not built yet", t.walls_h)])
    .concat([part("down", "not running (seat down, restarting or asleep)", t.down_h)]);
}

const utcShort = (s) => {
  const d = new Date(s);
  if (isNaN(d)) return "";
  const p2 = (n) => String(n).padStart(2, "0");
  return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()] + " " + d.getUTCDate() +
    " " + p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes());          // UTC (the headers say so)
};
const changesText = (c) => {
  if (!c) return "–";
  const order = ["self", "world", "model"].filter((k) => k in c).concat(Object.keys(c).filter((k) => !["self", "world", "model"].includes(k)).sort());
  return order.map((k) => escText(k === "self" ? SELF_NAME : k.replace(/^person:/, "")) + " " + c[k]).join(" · ") || "none yet";
};
const HEALTH_DOT = { ok: "ok", empty: "warn", unread: "idle", "no getter": "bad" };

function sheetSection(key, title, open, body, hint) {
  return '<details class="sec" data-sec="' + key + '" data-was="' + (open ? "1" : "0") + '"' + (open ? " open" : "") + "><summary>" +
    '<span class="stt">' + title + "</span>" + (hint ? '<span class="sth">' + hint + "</span>" : "") +
    '</summary><div class="secb">' + body + "</div></details>";
}

/** A trait's value as a person reads it: a share in percent, a skill signed, looks counted. */
function traitText(a) {
  const v = a && a.value != null && isFinite(Number(a.value)) ? Number(a.value) : null;
  if (v == null) return "not measured yet";
  if (a.unit === "share") return Math.round(100 * v) + "%";
  if (a.unit === "looks") return (Number.isInteger(v) ? String(v) : v.toFixed(1)) + (v === 1 ? " look" : " looks");
  return skillSigned(v);
}

/** The line under a trait: the numbers its value came from (its detail). */
function traitDetail(a) {
  const d = (a && a.detail) || {};
  const pct = (x) => Math.round(100 * Number(x)) + "%";
  switch (a && a.key) {
    case "curiosity": return d.things ? d.learning + " of " + d.things + " things it looked at are still being learned" : "";
    case "insight": return d.looks ? "over " + d.looks + " looks · change rate " + pct(d.rate) + " · calibration error " + fixed2(d.ece) : "";
    case "adaptability": return d.shifts ? "median over its last " + d.shifts + " noticed changes" : "";
    case "focus": return d.looks ? d.things + " of the " + d.known + " things it knows · the same thing twice in a row " + pct(d.repeat) : "";
    case "sociability": return (d.acts ? d.pushes + " of its last " + d.acts + " acts" : "") +
      (d.checks ? " · answered " + pct(d.replied) + " of " + d.checks + " reply checks" : "");
    default: return "";
  }
}

/** A trend's sparkline: the series' points (oldest left), an optional dashed line. */
function sparkSvg(series, cls, line) {
  const pts = (series || []).filter((p) => p && p.value != null && isFinite(Number(p.value)));
  if (pts.length < 2) return "";
  const W = 120, Hh = 34, vs = pts.map((p) => Number(p.value)).concat(line == null ? [] : [Number(line)]);
  const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
  const maxAgo = Math.max(...(series || []).map((p) => Number(p && p.ago_h) || 0)) || 1;
  const X = (a) => r1(2 + (W - 4) * (1 - a / maxAgo)), Y = (y) => r1(Hh - 3 - (Hh - 6) * (y - lo) / span);
  return '<svg class="' + cls + '" viewBox="0 0 ' + W + " " + Hh + '" width="' + W + '" height="' + Hh + '" preserveAspectRatio="none" aria-hidden="true">' +
    (line == null ? "" : '<line x1="0" x2="' + W + '" y1="' + Y(line) + '" y2="' + Y(line) + '" class="skp" stroke-dasharray="3 3"/>') +
    '<polyline points="' + pts.map((p) => X(Number(p.ago_h)) + "," + Y(Number(p.value))).join(" ") + '" class="skl"/></svg>';
}

const trendWord = (t) => (t == null ? "" : t > 0.005 ? "↗ rising" : t < -0.005 ? "↘ falling" : "→ flat");

/** #300: the attributes -- each trait, its value, what it measures, the numbers behind it, its trend. */
function sheetAttrs(s) {
  const at = s.attributes || [];
  if (!at.length) return '<p class="muted">nothing measured yet</p>';
  return '<p class="note">traits of ' + escText(SELF_NAME) + ' as a whole, each measured from its own record; they gate nothing</p>' +
    '<div class="attrs">' + at.map((a) => {
      const text = traitText(a);
      const none = text === "not measured yet";
      const bar = a.unit === "share" || a.unit === "skill" ? attrBar({ value: a.value, kind: a.unit }) : null;
      const t = a.trend_h == null ? null : Number(a.trend_h) * (a.better === "lower" ? -1 : 1);
      const det = traitDetail(a);
      return '<div class="attr trait' + (none ? " none" : "") + '" data-key="' + escAttr(a.key) + '">' +
        '<div class="al"><b>' + escText(a.label) + "</b>" + (t != null && !none ? ' <span class="rn">' + escText(trendWord(t)) + " over the last hour</span>" : "") + "</div>" +
        '<div class="av' + (bar && bar.neg ? " neg" : "") + '">' + escText(text) + "</div>" +
        (bar && !none ? '<div class="abar" role="meter" aria-label="' + escAttr(a.label + ": " + text) + '" aria-valuemin="' + bar.lo +
          '" aria-valuemax="' + bar.hi + '" aria-valuenow="' + escAttr(a.value) + '">' +
          (bar.lo < 0 ? '<span class="zero" style="left:' + bar.zero + '%"></span>' : "") +
          '<span class="fill' + (bar.neg ? " neg" : "") + '" style="left:' + bar.from + "%;width:" + r1(bar.to - bar.from) + '%"></span></div>' : "") +
        '<div class="an"><span class="what">' + escText(a.what || "") + (a.better === "lower" ? " (fewer is better)" : "") + "</span>" +
        (det ? '<br><span class="muted">' + escText(det) + "</span>" : "") + "</div>" +
        sparkSvg(a.series, "trspark") + "</div>";
    }).join("") + "</div>";
}

/** #300: the skills it is learning now, side by side -- each one's test staged,
 *  its bar, what it waits on and its skill's trend -- and a line for each earned
 *  skill whose test no longer holds. */
function sheetQuest(s) {
  const q = s.quest || {};
  // a sheet from before #300 has no `skills`: its quest (the one skill the exp bar read) stands in
  const ks = Array.isArray(s.skills) ? s.skills
    : q.rung != null ? [{ id: q.id || "", name: q.name || "", bar: q.bar, waiting_on: q.waiting_on, criteria: q.criteria || [],
                         note: q.note, trend: (s.header || {}).skill || null }] : [];
  const slip = (Array.isArray(s.slipping) ? s.slipping : []).map((x) => '<p class="note warn">⚠ ' + escText(x.text || x.name || "") + "</p>").join("") +
    (q.voided || []).map((v) => '<p class="note warn">' + escText(v) + "</p>").join("");
  if (!ks.length) {
    const why = s.header && s.header.mode === "off" ? escText(SELF_NAME) + " is asleep (💤 wakes it)" : "nothing open to practise right now";
    return '<p class="muted">learning nothing new: ' + why + "</p>" + slip;
  }
  return '<div class="learn">' + ks.map((k) => {
    const pct = Math.round(100 * clamp01(Number(k.bar) || 0));
    return '<div class="lskill' + (k.awaiting_accept ? " awaiting" : "") + '" data-id="' + escAttr(k.id) + '"><p class="qh">' + escText(k.name || k.id || "") + ' <span class="muted">' + pct + "%</span></p>" +
      '<div class="abar" role="meter" aria-label="' + escAttr((k.name || k.id) + ": " + pct + "% of its test") + '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '">' +
      '<span class="fill" style="left:0%;width:' + pct + '%"></span></div>' +
      '<ul class="crit">' + (k.criteria || []).map((c) => '<li class="' + (c.met ? "met" : "open") + '"><span class="ck">' + (c.met ? "✓" : "○") + "</span> " +
        escText(c.text) + (c.met || c.frac == null ? "" : ' <span class="muted">' + Math.round(100 * c.frac) + "%</span>") + "</li>").join("") + "</ul>" +
      (k.awaiting_accept ? '<p class="note">🎁 waiting on ' + escText(k.waiting_on || "your accept") + ": it isn't practised until you accept it (the Skill tree)</p>"
        : k.waiting_on ? '<p class="muted">waiting on: ' + escText(k.waiting_on) + "</p>" : "") +
      skillStatHtml(k.trend) +
      (k.note ? '<p class="note">' + escText(k.note) + "</p>" : "") + "</div>";
  }).join("") + "</div>" + slip;
}

/** #267: every toybox kind -- unlocked (yours, or not added), kept from
 *  before the unlocks, or locked: the rung that unlocks it and its lesson. */
function sheetToys(s) {
  const rows = s.toys || [];
  if (!rows.length) return '<p class="muted">nothing yet</p>';
  const st = (r) => r.state === "locked" ? "🔒 rung " + r.rung
    : r.state === "grandfathered" ? "kept (unlocks at rung " + r.rung + ")"
    : !r.built ? "unlocked · not built yet" : r.have ? "yours" : "unlocked · not added";
  return '<p class="note">toys unlock as ' + escText(SELF_NAME) + ' passes rungs; each one is the next lesson</p><ul class="toys">' + rows.map((r) =>
    '<li class="tu ' + escAttr(r.state) + '"><span class="ti" aria-hidden="true">' + escText(r.icon || "") + "</span><b>" + escText(r.kind) + "</b> " +
    '<span class="muted">' + escText(st(r)) + (r.at && r.state === "unlocked" && r.rung ? " · " + escText(utcShort(r.at)) + " UTC" : "") + "</span>" +
    '<div class="note">' + escText(r.teaches || "") + "</div></li>").join("") + "</ul>";
}

function sheetGrowth(s) {
  const g = s.growth || {};
  if (!(g.rows || []).some((r) => r.working_h != null)) return '<p class="muted">no rung passed yet: nothing to draw</p>';
  return curveSvg(g, 340, 190) +
    '<p class="legend"><span class="lg ls"></span>' + escText(SELF_NAME) + ' (working hours) <span class="lg lw"></span>wall clock ' +
    '<span class="lg lf"></span>fastest possible <span class="lg lk"></span>a child, rescaled</p>' +
    '<p class="note">Working hours leave out three things (#305): the time ' + escText(SELF_NAME) + " was <b>down</b> (the seat " +
    "off or restarting), the time a known <b>bug</b> held its skill shut, and the time it hit a <b>wall</b>: no built skill " +
    "to learn, because we hadn't built the next one yet. At any moment only the costliest of them counts." +
    (((s.time || {}).walls || []).length ? " Walls so far: " + hoursText((s.time || {}).walls_h != null ? s.time.walls_h
      : s.time.walls.reduce((n, w) => n + (Number(w.cost_h) || 0), 0)) + "." : "") + "</p>" +
    '<p class="note">' + (g.hours_per_child_month != null ? "<b>" + fixed2(g.hours_per_child_month) + " h</b> of " + escText(SELF_NAME) + " per month of a child. " : "") +
    escText(g.caveat || "") + "</p>";
}

/** A bug window's cost: its running time, or -- a bug that cost only part of
 *  self's time (#259) -- its share of it. */
function bugCost(b) {
  const share = b.share == null ? 1 : Number(b.share);
  if (!(share < 1)) return hoursText(b.running_h) + " running";
  return Math.round(100 * share) + "% of " + hoursText(b.running_h) + " running = " +
    hoursText(b.cost_h != null ? b.cost_h : share * (Number(b.running_h) || 0));
}

function sheetTime(s) {
  const t = s.time || {};
  const parts = timeParts(t);
  return '<div class="tbar" role="img" aria-label="' + escAttr(parts.map((p) => p.label + " " + hoursText(p.h)).join(", ")) + '">' +
    parts.map((p) => '<span class="tp tp-' + p.key + '" style="width:' + p.pct + '%"></span>').join("") + "</div>" +
    '<ul class="tl">' + parts.map((p) => '<li><span class="lg tp-' + p.key + '"></span>' + escText(p.label) + " <b>" + hoursText(p.h) + "</b> · " + p.pct + "%</li>").join("") +
    "</ul>" + ((t.bugs || []).length ? '<p class="muted">the known bugs that cost ' + escText(SELF_NAME) + ' time:</p><ul class="bugs">' + t.bugs.map((b) =>
      "<li>#" + b.issue + " " + escText(b.what) + ' <span class="muted">' + escText(utcShort(b.from)) + " → " + (b.open ? "now (open)" : escText(utcShort(b.to)) + " UTC") +
      " · " + bugCost(b) + "</span></li>").join("") + "</ul>" : "") +
    ((t.walls || []).length ? '<p class="muted">the walls -- ' + escText(SELF_NAME) + ' had no built skill to learn (#305):</p><ul class="bugs">' + t.walls.map((w) =>
      "<li>" + escText(w.why) + ' <span class="muted">' + escText(utcShort(w.from)) + " → " + (w.open ? "now (open)" : escText(utcShort(w.to)) + " UTC") +
      " · " + hoursText(w.cost_h != null ? w.cost_h : w.running_h) + "</span></li>").join("") + "</ul>" : "");
}

function sheetInventory(s) {
  const inv = s.inventory || [];
  if (!inv.length) return '<p class="muted">no toys (membrane toybox add &lt;kind&gt;)</p>';
  return '<p class="note">play with them in the 🧸 dock under the 👶 tab</p><div class="wrap"><table class="tbl"><thead><tr><th>toy</th><th class="num">looks</th>' +
    '<th class="num">error</th><th>changed by</th></tr></thead><tbody>' + inv.map((t) =>
      '<tr><td><span class="dot ' + (HEALTH_DOT[t.health] || "idle") + '" title="' + escAttr(t.health) + '"></span>' + escText(t.name) +
      ' <span class="muted">' + escText(t.kind || "") + (t.seen_as ? " · seen as " + escText(t.seen_as) : "") + "</span></td>" +
      '<td class="num">' + (t.looks || 0) + '</td><td class="num">' + fixed2(t.error_early) + " → " + fixed2(t.error_late) + "</td>" +
      "<td>" + changesText(t.changes) + "</td></tr>").join("") + "</tbody></table></div>";
}

function sheetRelationships(s) {
  const r = s.relationships || {};
  const os = r.others || [];
  const out = [];
  if (!os.length) out.push('<p class="muted">no one met yet</p>');
  else out.push('<p class="note">Brier error: 0 is perfectly predictable, 0.25 a coin flip.</p><ul class="rel">' + os.map((o) => {
    const bits = [];
    if (o.chat) bits.push("chat: " + o.chat.lines + " lines, " + o.chat.looks + " looks" +
      (o.chat.replied != null ? ", replied " + Math.round(100 * o.chat.replied) + "%" : "") +
      (o.chat.brier != null ? ", Brier " + fixed2(o.chat.brier) : "") + (o.chat.seen_as ? ", seen as " + escText(o.chat.seen_as) : ""));
    if (o.play) bits.push("play: changed toys " + o.play.changes + "×" + (o.play.looks ? ", Brier " + fixed2(o.play.brier) + " on " + o.play.looks + " looks after" : ""));
    if (o.rung8) bits.push("rung 8's advisor: Brier " + fixed2(o.rung8.brier_model) + " vs " + escText(SELF_NAME) + "'s " + fixed2(o.rung8.brier_self) +
      (o.rung8.pairs != null ? " over " + o.rung8.pairs + " answers" : "") +
      (o.rung8.weight != null ? ", " + escText(SELF_NAME) + "'s weight on it " + fixed2(o.rung8.weight) : ""));
    return "<li><b>" + escText(o.who) + "</b> " + '<span class="muted">' + escText(o.kind) + "</span>" + (o.current ? ' <span class="now">meeting now</span>' : "") +
      '<div class="note">' + bits.join("<br>") + "</div></li>";
  }).join("") + "</ul>");
  out.push('<p><span class="muted">body (it predicts them near-perfectly):</span> ' + ((r.body || []).map(escText).join(", ") || "–") + "</p>");
  out.push('<p><span class="muted">other:</span> ' + ((r.other || []).map(escText).join(", ") || "–") + "</p>");
  return out.join("");
}

/** A skill's value at a glance: the number big, the line it has to beat, its
 *  trend over the last hour, and a sparkline of the last hours. */
function skillStatHtml(sk) {
  if (!sk || sk.value == null) return "";
  const v = Number(sk.value), pass = sk.pass == null ? null : Number(sk.pass);
  const t = sk.trend_h == null ? null : Number(sk.trend_h);
  const arrow = t == null ? "" : t > 0.005 ? "↗" : t < -0.005 ? "↘" : "→";
  const met = pass != null && v >= pass;
  return '<div class="skill' + (met ? " met" : v < 0 ? " neg" : "") + '" role="group" aria-label="' +
    escAttr((sk.label || "skill") + " " + skillSigned(v) + (pass != null ? ", to beat " + skillSigned(pass) : "") +
      (t != null ? ", " + (t > 0.005 ? "rising" : t < -0.005 ? "falling" : "flat") + " over the last hour" : "")) + '">' +
    '<div class="skv">' + skillSigned(v) + "</div>" +
    '<div class="skm"><div class="skn">' + escText(sk.label || "skill") + "</div>" +
    (pass != null ? '<div class="skb">to beat <b>' + skillSigned(pass) + "</b>" + (met ? " ✓" : "") + "</div>" : '<div class="skb muted">no fixed line to beat</div>') +
    (t != null ? '<div class="skt">' + arrow + " " + skillSigned(t) + " in the last hour</div>" : "") + "</div>" + sparkSvg(sk.series, "skspark", pass) + "</div>";
}

/** The whole sheet: the nameplate, then the sections as <details> (open:
 *  the remembered choice per section, else SHEET_OPEN). nowS: unused for
 *  now (every age is the seat's). */
function selfSheetHtml(s, open, nowS) {
  if (!s || typeof s !== "object" || !s.header) return '<p class="muted">the sheet is not reachable right now</p>';
  open = open || {};
  const h = s.header;
  // #300: "Lv N · <latest earned skill>" -- skills, never rungs; then what it is learning now
  const title = h.title || "Lv " + (h.level || 0) + (h.rung_name ? " · " + h.rung_name : "");
  const learning = Array.isArray(h.learning) ? h.learning : [];
  const out = ['<div class="hdr"><div class="nm">' + escText(h.name || SELF_NAME) + '</div><div class="lvb">' + escText(title) + "</div>" +
    '<div class="rnm">' + (learning.length ? '<span class="muted">learning:</span> ' + learning.map(escText).join(", ") : "") + "</div>" +
    '<div class="xpslot"></div>' +
    '<div class="facts"><span>age <b>' + hoursText(h.age_h) + '</b></span><span>working <b>' + hoursText(h.working_h) + "</b></span>" +
    (h.cycles ? "<span>" + h.cycles + " cycles</span><span>" + (h.looks || 0) + " looks</span><span>" + (h.lines_sent || 0) + " lines sent</span>" : "") +
    "</div></div>"];
  if (s.empty) out.push('<p class="muted">' + escText(SELF_NAME) + ' has not been switched on yet: tap 💤 to wake it.</p>');
  const body = { attributes: sheetAttrs, quest: sheetQuest, growth: sheetGrowth,
                 time: sheetTime, inventory: sheetInventory, toys: sheetToys, relationships: sheetRelationships };
  const nLearn = Array.isArray(s.skills) ? s.skills.length : 0, nSlip = Array.isArray(s.slipping) ? s.slipping.length : 0;
  const hint = { attributes: "", quest: (nLearn ? nLearn + (nLearn === 1 ? " skill" : " skills") : "") + (nSlip ? (nLearn ? " · " : "") + nSlip + " slipping" : ""),
                 time: hoursText((s.time || {}).working_h) + " working",
                 inventory: (s.inventory || []).length + " toys",
                 toys: (s.toys || []).filter((r) => r.state !== "locked").length + " of " + (s.toys || []).length + " unlocked", relationships: ((s.relationships || {}).others || []).length + " met" };
  for (const [key, title] of SHEET_SECTIONS) {
    const isOpen = key in open ? !!open[key] : !!SHEET_OPEN[key];
    out.push(sheetSection(key, title, isOpen, body[key](s), escText(hint[key] || "")));
  }
  return out.join("");
}

// ---- #296: THE SKILL TREE TAB -- self's achievements as an RPG skill tree:
// lanes by branch, each node below its prerequisites, edges from them. The
// seat's sheet carries `tree` and `level` (#295); a seat from before that
// sends only the ladder, and the tab reads the ladder as a tree with the
// fixed mapping below (only the next rung is open: the strict ladder opens
// nothing else). Pure helpers, then renderSkillTree: DOM calls only, every
// data-derived string through textContent.
const TREE_BRANCHES = [["self", "Self"], ["world", "World"], ["others", "Others"]];
const TREE_STATES = ["earned", "open", "locked", "unbuilt"];   // the seat's; "awaiting" (#299) is read from `accepted`
const TREE_LADDER = [            // [id, name, branch, rung, prereqs]: the ladder read as a tree
  ["feel", "feel itself", "self", 1, []], ["reach", "first reach", "world", 2, []],
  ["kinds", "tell things apart", "world", 3, ["reach"]], ["patience", "patience", "world", 4, ["kinds"]],
  ["shift", "notice the world changed", "world", 5, ["reach"]], ["meet", "meet an other", "others", 6, ["kinds"]],
  ["turns", "time its turn", "others", 7, ["meet"]], ["expect", "expect a reply", "others", 8, ["meet"]],
  ["mirror", "recognize itself", "self", 9, []], ["change", "change itself", "self", 10, []],
  ["speak", "speak", "others", 11, []], ["invent", "invent", "self", null, []]];
const TREE_STATE_TEXT = { earned: "earned", open: "open: its test is running", locked: "locked: its prerequisites first",
                          unbuilt: "not built yet", awaiting: "earned · awaiting your accept" };
const finiteOr = (v, d) => (v == null || v === "" || !isFinite(Number(v)) ? d : Number(v));

/** A child's age as a person says it, from months. */
function childAge(m) {
  m = finiteOr(m, null);
  if (m == null) return "";
  if (m < 1) return "newborn";
  if (m < 24) return r1(m) + " months";
  return r1(m / 12) + " years";
}

/** The tab's model: {source: "seat"|"ladder", level, name, nodes}; null without a sheet. */
function skillTree(s) {
  if (!s || typeof s !== "object" || !s.header) return null;
  const h = s.header || {};
  const name = h.name || SELF_NAME;
  if (Array.isArray(s.tree)) {
    const nodes = s.tree.filter((n) => n && n.id != null).map((n) => {
      const built = n.built !== false;
      let state = !built ? "unbuilt" : TREE_STATES.includes(n.state) ? n.state : "locked";
      const accepted = n.accepted !== false;        // #299: a seat from before acceptance accepted everything
      if (state === "earned" && !accepted) state = "awaiting";
      const bar = finiteOr(n.bar, null);
      return { id: String(n.id), name: String(n.name || n.id), branch: String(n.branch || "self"),
               prereqs: (Array.isArray(n.prereqs) ? n.prereqs : []).map(String), built, state, accepted,
               earned_at: n.earned_at || null, skill: finiteOr(n.skill, null), bar: bar == null ? null : clamp01(bar),
               waiting_on: n.waiting_on || null, test: n.test || n.asks || null,
               child_age: n.child_age || "", child_ability: n.child_ability || "", why_prereqs: n.why_prereqs || "" };
    });
    const level = finiteOr(s.level, nodes.filter((n) => n.state === "earned" || n.state === "awaiting").length);
    return { source: "seat", level, name, nodes };
  }
  const level = finiteOr(h.level, 0);
  const q = s.quest || {};
  const next = finiteOr(h.next, finiteOr(q.rung, null));
  const ach = {}, rows = {};
  for (const a of s.achievements || []) if (a && a.rung != null) ach[a.rung] = a;
  for (const r of (s.growth || {}).rows || []) if (r && r.rung != null) rows[r.rung] = r;
  const names = {};
  for (const [id, nm] of TREE_LADDER) names[id] = nm;
  const nodes = TREE_LADDER.map(([id, nm, branch, rung, prereqs]) => {
    const built = rung != null && rung <= 8;
    const row = rows[rung] || {}, a = ach[rung] || {};
    const earned = built && rung <= level;
    const open = built && !earned && rung === next;
    const sk = h.skill && h.skill.rung === rung ? finiteOr(h.skill.value, null) : null;
    return { id, name: nm, branch, prereqs: prereqs.slice(), built, rung,
             state: !built ? "unbuilt" : earned ? "earned" : open ? "open" : "locked",
             earned_at: earned ? a.earned_at || a.at || null : null,
             skill: earned ? finiteOr(a.skill, null) : open ? sk : null,
             bar: open ? (finiteOr(h.bar, finiteOr(q.bar, null)) == null ? null : clamp01(finiteOr(h.bar, finiteOr(q.bar, 0)))) : null,
             waiting_on: open ? h.waiting_on || q.waiting_on || null : null,
             test: row.needs && row.needs !== "not built yet" ? row.needs : null,
             child_age: childAge(row.child_months), child_ability: row.child_milestone || "",
             why_prereqs: prereqs.length ? "the ladder's order read as a tree: " + prereqs.map((p) => names[p]).join(", ") + " comes first" : "" };
  });
  return { source: "ladder", level, name, nodes };
}

const TREE_LANE_W = 84, TREE_NODE_W = 76, TREE_NODE_H = 64, TREE_ROW_H = 94, TREE_TOP = 24, TREE_PAD = 4;

/** Lanes by branch (Self / World / Others, then any other branch as
 *  it comes), each node in its lane's next free row below all its
 *  prerequisites; the edges from every known prerequisite. Centres in px. */
function treeLayout(nodes) {
  nodes = (nodes || []).filter((n) => n && n.id != null);
  const byId = {};
  for (const n of nodes) byId[n.id] = n;
  const order = TREE_BRANCHES.map((b) => b[0]).filter((b) => nodes.some((n) => n.branch === b));
  for (const n of nodes) if (!order.includes(n.branch)) order.push(n.branch);
  const label = {};
  for (const [b, l] of TREE_BRANCHES) label[b] = l;
  const laneX = {}, lanes = order.map((b, i) => {
    laneX[b] = TREE_PAD + i * TREE_LANE_W + TREE_LANE_W / 2;
    return { branch: b, label: label[b] || b.charAt(0).toUpperCase() + b.slice(1), x: laneX[b] };
  });
  const known = (n) => (n.prereqs || []).filter((p) => p !== n.id && byId[p]);
  const row = {}, laneNext = {}, left = nodes.slice();
  while (left.length) {                  // ready nodes first, in order; a cycle takes the first left
    let i = left.findIndex((n) => known(n).every((p) => p in row));
    if (i < 0) i = 0;
    const n = left.splice(i, 1)[0];
    let r = laneNext[n.branch] || 0;
    for (const p of known(n)) if (p in row) r = Math.max(r, row[p] + 1);
    row[n.id] = r; laneNext[n.branch] = r + 1;
  }
  const yOf = (r) => TREE_TOP + r * TREE_ROW_H + TREE_NODE_H / 2;
  const out = nodes.map((n) => Object.assign({}, n, { row: row[n.id], x: laneX[n.branch], y: yOf(row[n.id]) }));
  const pos = {};
  for (const n of out) pos[n.id] = n;
  const edges = [];
  for (const n of out) for (const p of known(n)) {
    const a = pos[p], hh = TREE_NODE_H / 2, hw = TREE_NODE_W / 2;
    let d;
    if (a.x === n.x && n.row - a.row > 1) {            // around the nodes between, inside the lane
      const ex = a.x + TREE_LANE_W / 2 - 1;
      d = "M" + (a.x + hw) + "," + a.y + " C" + ex + "," + a.y + " " + ex + "," + n.y + " " + (n.x + hw) + "," + n.y;
    } else {
      const y1 = a.y + hh, y2 = n.y - hh, my = r1((y1 + y2) / 2);
      d = "M" + a.x + "," + y1 + " C" + a.x + "," + my + " " + n.x + "," + my + " " + n.x + "," + y2;
    }
    edges.push({ from: p, to: n.id, d, earned: a.state === "earned" });
  }
  const rows = Math.max(0, ...out.map((n) => n.row));
  return { W: TREE_PAD * 2 + lanes.length * TREE_LANE_W, H: TREE_TOP + rows * TREE_ROW_H + TREE_NODE_H + TREE_PAD,
           nodeW: TREE_NODE_W, nodeH: TREE_NODE_H, lanes, nodes: out, edges };
}

const treeDate = (s) => (s && utcShort(s) ? utcShort(s) + " UTC" : "");
const treePct = (b) => Math.round(100 * clamp01(Number(b))) + "%";

/** A node's state in words (its aria label and its card say it). */
function nodeStateText(n) {
  let t = TREE_STATE_TEXT[n.state] || n.state;
  if (n.state === "earned" && n.earned_at) t += " " + treeDate(n.earned_at);
  if (n.state === "awaiting" && n.earned_at) t += " (earned " + treeDate(n.earned_at) + ")";
  if (n.state === "open" && n.bar != null) t += ", " + treePct(n.bar) + " of the way";
  if (n.waiting_on && n.state !== "earned" && n.state !== "awaiting") t += " · waiting on " + n.waiting_on;
  return t;
}

/** What a hover, focus or tap shows: {title, rows: [[label, text]], accept}
 *  -- `accept` (#299): the id an Accept button on the card would accept. */
function nodeCard(n, nodes) {
  if (!n) return null;
  const byId = {};
  for (const x of nodes || []) byId[x.id] = x;
  const rows = [["state", nodeStateText(n)]];
  if (n.test) rows.push(["test", n.test]);
  if (n.skill != null || n.bar != null)
    rows.push(["skill vs bar", "skill " + (n.skill == null ? "–" : skillSigned(n.skill)) + (n.bar == null ? "" : " · bar " + treePct(n.bar))]);
  if (n.earned_at) rows.push(["earned", treeDate(n.earned_at)]);
  rows.push(["needs", (n.prereqs || []).map((p) => (byId[p] ? byId[p].name : p)).join(", ") || "nothing first"]);
  if (n.why_prereqs) rows.push(["why", n.why_prereqs]);
  if (n.child_ability) rows.push(["a child (an analogy)", "a child: " + n.child_ability + (n.child_age ? " (" + n.child_age + ")" : "")]);
  if (n.state === "awaiting") {
    const kids = (nodes || []).filter((x) => (x.prereqs || []).includes(n.id)).map((x) => x.name);
    rows.push(["your accept", "accepting it opens " + (kids.length ? kids.join(", ") : "what it leads to") + "; until then nothing it leads to runs"]);
    return { title: n.name, rows, accept: n.id };
  }
  return { title: n.name, rows };
}

/** The earned ones, newest first: {id, name, at, date, skill}. */
function achievementsList(nodes) {
  const t = (n) => { const v = Date.parse(n.earned_at || ""); return isNaN(v) ? -Infinity : v; };
  return (nodes || []).filter((n) => n && (n.state === "earned" || n.state === "awaiting")).sort((a, b) => t(b) - t(a))
    .map((n) => ({ id: n.id, name: n.name, at: n.earned_at || null, date: treeDate(n.earned_at), skill: n.skill == null ? "–" : skillSigned(n.skill),
                   awaiting: n.state === "awaiting" }));
}

/** The open node's progress ring: circumference c and the drawn length `on`. */
function ringDash(bar, r) {
  if (bar == null || !isFinite(Number(bar))) return null;
  const c = 2 * Math.PI * r;
  return { c, on: c * clamp01(Number(bar)) };
}

const TREE_ICON = { earned: "🏅", awaiting: "🎁", locked: "🔒", unbuilt: "◌" };
const SVGNS = "http://www.w3.org/2000/svg";

/** The tab, built into `root` with DOM calls (no markup strings). `sel`
 *  keeps the chosen node across repaints ({id}); the node that had focus
 *  keeps it, and the tree's box keeps its scroll. #299: `sel.onAccept(id)`
 *  (a promise of {ok, error}), when given, puts an Accept button on an
 *  awaiting node's card. */
function renderSkillTree(doc, root, sheet, sel) {
  sel = sel || {};
  const el = (tag, cls, text) => {
    const e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const svgEl = (tag, attrs) => {
    const e = doc.createElementNS(SVGNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  };
  const rn = root.getRootNode ? root.getRootNode() : null;         // the page's home lives in a shadow root
  const act = (rn && rn.activeElement) || doc.activeElement;
  const focusId = act && act.getAttribute && /\bsknode\b/.test(act.className || "") ? act.getAttribute("data-id") : null;
  const scroll = sel._box ? { l: sel._box.scrollLeft || 0, t: sel._box.scrollTop || 0 } : null;
  root.textContent = "";
  const m = skillTree(sheet);
  if (!m) { root.appendChild(el("p", "muted", "the skill tree is not reachable right now")); return; }
  const L = treeLayout(m.nodes);
  const hdr = root.appendChild(el("div", "skhdr"));
  hdr.appendChild(el("span", "nm", m.name));
  hdr.appendChild(el("span", "lvb", "Lv " + m.level));
  root.appendChild(el("p", "note", m.source === "ladder"
    ? "the ladder read as a tree: this seat doesn't send its skill tree yet, so only the next rung is open"
    : "each skill is an achievement; its test runs once its prerequisites are earned and you accepted them. Hover, focus or tap one."));
  const box = root.appendChild(el("div", "skbox"));
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", m.name + "'s skill tree");
  sel._box = box;
  const cv = box.appendChild(el("div", "skcanvas"));
  cv.setAttribute("style", "width:" + L.W + "px;height:" + L.H + "px");
  const svg = cv.appendChild(svgEl("svg", { class: "skedges", width: L.W, height: L.H, viewBox: "0 0 " + L.W + " " + L.H, "aria-hidden": "true" }));
  for (const ln of L.lanes) {
    svg.appendChild(svgEl("line", { class: "sklane", x1: ln.x, x2: ln.x, y1: TREE_TOP - 4, y2: L.H - TREE_PAD }));
    const lab = cv.appendChild(el("div", "sklanel", ln.label));
    lab.setAttribute("style", "left:" + (ln.x - TREE_LANE_W / 2) + "px;width:" + TREE_LANE_W + "px");
  }
  for (const e of L.edges) svg.appendChild(svgEl("path", { class: "skedge" + (e.earned ? " earned" : ""), d: e.d }));
  const btns = {};
  for (const n of L.nodes) {
    const b = cv.appendChild(el("button", "sknode " + n.state));
    b.setAttribute("type", "button");
    b.setAttribute("data-id", n.id);
    b.setAttribute("aria-label", n.name + ": " + nodeStateText(n));
    b.setAttribute("style", "left:" + (n.x - L.nodeW / 2) + "px;top:" + (n.y - L.nodeH / 2) + "px;width:" + L.nodeW + "px;height:" + L.nodeH + "px");
    const ic = b.appendChild(el("span", "skic"));
    ic.setAttribute("aria-hidden", "true");
    const ring = n.state === "open" ? ringDash(n.bar, 7) : null;
    if (n.state === "open") {
      const rs = ic.appendChild(svgEl("svg", { class: "skring", width: 18, height: 18, viewBox: "0 0 18 18" }));
      rs.appendChild(svgEl("circle", { class: "skrt", cx: 9, cy: 9, r: 7 }));
      if (ring) rs.appendChild(svgEl("circle", { class: "skrf", cx: 9, cy: 9, r: 7, "stroke-dasharray": r1(ring.on) + " " + r1(ring.c),
                                                  transform: "rotate(-90 9 9)" }));
    } else ic.textContent = TREE_ICON[n.state] || "";
    b.appendChild(el("span", "sknm", n.name));
    const sub = n.state === "earned" ? (utcShort(n.earned_at || "").split(" ").slice(0, 2).join(" ") || "earned")
      : n.state === "awaiting" ? "accept?"
      : n.state === "open" ? (n.bar == null ? "open" : treePct(n.bar)) : n.state === "locked" ? "locked" : "not built yet";
    b.appendChild(el("span", "sksub", sub));
    btns[n.id] = b;
  }
  const card = root.appendChild(el("div", "skcard"));
  card.setAttribute("role", "region");
  card.setAttribute("aria-label", "the chosen skill");
  const paintCard = () => {
    card.textContent = "";
    for (const id in btns) btns[id].className = "sknode " + L.nodes.find((n) => n.id === id).state + (id === sel.id ? " sel" : "");
    const c = nodeCard(L.nodes.find((n) => n.id === sel.id), m.nodes);
    if (!c) { card.appendChild(el("p", "muted", "hover, focus or tap a skill to see it")); return; }
    card.appendChild(el("p", "skct", c.title));
    const dl = card.appendChild(el("dl", "skcd"));
    for (const [k, v] of c.rows) { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); }
    if (c.accept && typeof sel.onAccept === "function") {         // #299: the operator lets it grow
      const id = c.accept;
      const ab = card.appendChild(el("button", "skaccept", "🏅 Accept"));
      ab.setAttribute("type", "button");
      ab.setAttribute("aria-label", "Accept " + c.title + ": open what it leads to");
      const msg = card.appendChild(el("p", "skamsg"));
      msg.setAttribute("role", "status");
      ab.addEventListener("click", () => {
        ab.disabled = true;
        msg.textContent = "accepting…";
        let got;
        try { got = sel.onAccept(id); } catch (e) { got = { ok: false, error: String((e && e.message) || e) }; }
        Promise.resolve(got).catch((e) => ({ ok: false, error: String((e && e.message) || e) }))
          .then((out) => {
            if (out && out.ok) msg.textContent = "accepted ✓ -- what it leads to opens now";
            else { ab.disabled = false; msg.textContent = "not accepted: " + ((out && out.error) || "the seat refused"); }
          });
      });
    }
  };
  const idOf = (t) => {
    for (let e = t; e && e !== box; e = e.parentNode || e.parent) if (e.getAttribute && e.getAttribute("data-id")) return e.getAttribute("data-id");
    return null;
  };
  const pick = (ev) => { const id = idOf(ev.target); if (id && id !== sel.id) { sel.id = id; paintCard(); } };
  box.addEventListener("click", pick);
  box.addEventListener("focusin", pick);
  box.addEventListener("mouseover", pick);
  paintCard();
  root.appendChild(el("h4", null, "Achievements"));
  const ach = achievementsList(m.nodes);
  if (!ach.length) root.appendChild(el("p", "muted", "none earned yet"));
  else {
    const ol = root.appendChild(el("ol", "skachl"));
    for (const a of ach) {
      const li = ol.appendChild(el("li", "skach"));
      li.appendChild(el("b", null, a.name));
      li.appendChild(el("span", "muted", " · 🏅 " + (a.date || "earned") + " · skill " + a.skill + (a.awaiting ? " · awaiting your accept" : "")));
    }
  }
  if (scroll) { box.scrollLeft = scroll.l; box.scrollTop = scroll.t; }
  if (focusId && btns[focusId]) btns[focusId].focus();
}

// ---- #249: THE TOY DOCK -- one icon per toybox toy under the 👶 tab, live:
// a glyph per kind, a badge naming who changed it last (fading), a health
// dot, and a popover with its stats and the play actions (the one write,
// POST /membrane/toybox/poke, through _toyPoke).
const DIE_FACES = ["⚀", "⚁", "⚂", "⚃", "⚄", "⚅"];
const BADGE_S = 6;                                     // a change's badge shows this long

/** #325: the toys the dock paints around a play button's poke -- before the
 *  seat answers (reading null), the expected result: a rolled-back ball is on
 *  self's side; once it answers, the seat's own reading. Other toys and the
 *  input are untouched. */
const POKE_GUESS = { ball: { roll: { side: "self" } } };
function toyAfterPoke(toys, name, action, reading) {
  return (toys || []).map((t) => {
    if (!t || t.name !== name) return t;
    const r = reading != null ? reading : ((POKE_GUESS[t.kind] || {})[action] || null);
    if (r == null) return t;
    const base = t.reading && typeof t.reading === "object" ? t.reading : {};
    return Object.assign({}, t, { reading: Object.assign({}, base, typeof r === "object" ? r : { value: r }) });
  });
}

function toyGlyph(t) {
  t = t || {};
  const r = t.reading || {}, v = r.value, nm = t.name || "toy", k = t.kind;
  if (t.locked) {                                      // #267: a locked toy -- its icon, greyed by the dock
    return { html: escText(t.icon || TOY_ICONS[k] || "?"),
             label: k + ", locked: unlocks when " + SELF_NAME + " passes rung " + t.unlock_rung + (t.teaches ? ": " + t.teaches : "") };
  }
  if (k === "tower") {
    const b = Array.isArray(r.blocks) ? r.blocks : [];
    const up = b.filter(Boolean).length;
    const html = '<span class="tower">' + b.map((s, i) => i).reverse().map((i) => '<i class="blk' + (b[i] ? "" : " down") + '"></i>').join("") + "</span>";
    return { html, label: nm + ", a tower: " + up + " of " + b.length + " blocks standing" };
  }
  if (k === "ball") {                                  // #264: whose side it is on (self's left, yours right)
    const yours = r.side === "other";
    return { html: '<span class="ball side-' + (yours ? "other" : "self") + '"><i></i></span>',
             label: nm + ", the ball: " + (yours ? "on your side, roll it back" : "with " + SELF_NAME + ", its turn next") };
  }
  if (k === "die") {
    const n = Number(v);
    return { html: Number.isInteger(n) && n >= 1 && n <= 6 ? DIE_FACES[n - 1] : escText(v), label: nm + ", a die showing " + v };
  }
  if (k === "drift") {
    const p = t.params || {}, lo = p.lo != null ? Number(p.lo) : 0, hi = p.hi != null ? Number(p.hi) : 100;
    const pct = Math.round(100 * clamp01((Number(v) - lo) / ((hi - lo) || 1)));
    return { html: '<span class="gauge"><i style="height:' + pct + '%"></i></span>', label: nm + ", drifting: " + v + " (of " + lo + "–" + hi + ")" };
  }
  if (k === "burst") {
    return { html: '<span class="burst ' + (t.active ? "on" : "off") + '">✦</span>',
             label: nm + ", bursty: " + (t.active ? "active now" : "quiet now") + ", value " + v };
  }
  if (k === "mirror") {                                // #312: its own moves (the glass) beside a stranger's
    const seen = t.seen_as || {}, g = r.glass, st = r.stranger;
    const verdict = seen.glass === "body" && seen.stranger === "other" ? SELF_NAME + " reads the mirror as itself and the stranger as other"
      : seen.glass ? SELF_NAME + " reads the mirror as " + (seen.glass === "body" ? "itself" : "other") + " and the stranger as " + (seen.stranger === "body" ? "itself" : "other")
      : "not enough looks to say yet";
    return { html: "🪞", label: nm + ", " + SELF_NAME + "'s mirror: its own moves " + (g == null ? "–" : g) + ", a stranger's " +
             (st == null ? "–" : st) + " (" + verdict + ")" };
  }
  if (k === "body") return { html: escText(v), label: nm + ", " + SELF_NAME + "'s body toy (" + SELF_NAME + " only): " + v };
  if (k === "fast" || k === "slow") return { html: escText(v), label: nm + ", " + k + " clock: " + v };
  return { html: "?", label: nm + (k ? ", " + k : "") };
}

/** Who changed it last, while fresh: self / world / M (the model) / a person's initial. */
function toyBadge(t, s) {
  const last = t && t.last;
  if (!last) return null;
  s = s == null ? BADGE_S : s;
  const by = String(last.by || "?"), who = by === "self" ? SELF_NAME : by.replace(/^person:/, "");   // #277: self by its name
  const text = by === "self" ? SELF_NAME : by === "world" ? by : by === "model" ? "M" : (who.charAt(0) || "?").toUpperCase();
  const age = Number(last.age_s) || 0;
  return { text, who: by === "model" ? "the seat's model" : who, fresh: age < s, age,
           title: "last change by " + (by === "model" ? "the seat's model" : who) + " " + agoText(age) + " ago" };
}

function toyDockItem(t) {
  const g = toyGlyph(t);
  if (t && t.locked) return { name: t.name || "", kind: t.kind || "", glyph: g.html, health: "locked", label: g.label, badge: null, locked: true };
  return { name: (t && t.name) || "", kind: (t && t.kind) || "", glyph: g.html, health: HEALTH_DOT[t && t.health] || "idle",
           label: g.label + " · health " + ((t && t.health) || "unknown"), badge: toyBadge(t) };
}

function toyDockHtml(toys) {
  return (toys || []).map((t) => {
    const it = toyDockItem(t);
    return '<li class="dk' + (it.locked ? " locked" : "") + '" data-dock-li="' + escAttr(it.name) + '"><button type="button" class="dkb" data-dock-toy="' + escAttr(it.name) +
      '" aria-label="' + escAttr(it.label) + '" aria-haspopup="dialog" aria-expanded="false">' +
      '<span class="g g-' + escAttr(it.kind) + '">' + it.glyph + '</span><span class="hd ' + it.health + '"></span>' +
      '<span class="bdg' + (it.badge && it.badge.fresh ? " fresh" : "") + '">' + escText(it.badge ? it.badge.text : "") + "</span>" +
      (it.locked ? '<span class="lk" aria-hidden="true">🔒</span>' : "") + "</button></li>";
  }).join("");
}

/** The popover: its stats and what you can do with it. res: the last poke's
 *  {ok, text}, shown inline. */
// #276: how well self has learned the toy -- the seat's verdict (self_sheet.learning)
const LEARN_SHORT = { new: "too new", mastered: "mastered", worse: "getting worse", learning: "learning", rate: "rate only", lost: "not understood" };
function learningHtml(t) {
  const l = t && t.learning;
  if (!l || !l.verdict) return "";
  const bl = Array.isArray(t.blocks) && t.blocks.length
    ? '<p class="muted">blocks: ' + t.blocks.map((b) => escText(b.block) + " " + escText(LEARN_SHORT[b.verdict] || b.verdict)).join(" · ") + "</p>" : "";
  return '<p class="lv ' + escAttr(l.verdict) + '">' + escText(l.text || LEARN_SHORT[l.verdict] || l.verdict) + "</p>" + bl;
}

function toyPopoverHtml(t, res) {
  t = t || {};
  if (t.locked) {                                      // #267: what it is, when it comes, what it will teach
    return '<div class="ph"><b>' + escText(t.icon || TOY_ICONS[t.kind] || "") + " " + escText(t.kind || "") + '</b> <span class="muted">🔒 locked</span></div>' +
      "<p>unlocks when " + escText(SELF_NAME) + " passes rung " + escText(t.unlock_rung) + (t.rung_name ? " (" + escText(t.rung_name) + ")" : "") + "</p>" +
      (t.teaches ? '<p class="pg">' + escText(t.teaches) + "</p>" : "");
  }
  const g = toyGlyph(t), b = toyBadge(t);
  const out = ['<div class="ph"><span class="dot ' + (HEALTH_DOT[t.health] || "idle") + '"></span><b>' + escText(t.name) + '</b> <span class="muted">' +
    escText(t.kind || "") + " · " + escText(t.health || "") + "</span></div>",
    '<p class="pg">' + escText(g.label) + "</p>",
    "<p>" + (t.looks || 0) + " looks · error " + fixed2(t.error_early) + " → " + fixed2(t.error_late) + ' <span class="muted">(earlier → later half)</span></p>',
    learningHtml(t),
    "<p>changes: " + changesText(t.changes) + "</p>",
    t.grandfathered ? '<p class="muted">kept from before the unlocks (its kind unlocks at rung ' + escText(t.unlock_rung) + ")</p>" : "",
    "<p>" + (b ? "last change by " + escText(b.who) + " " + agoText(b.age) + " ago" + (t.last.what ? ' <span class="muted">(' + escText(t.last.what) + ")</span>" : "")
      : '<span class="muted">no change yet</span>') + "</p>"];
  let acts = [];
  if (t.kind === "tower") {
    const bl = (t.reading && t.reading.blocks) || [];
    acts = bl.map((up, i) => ({ action: up ? "knock" : "rebuild", arg: i + 1, label: (up ? "knock " : "rebuild ") + (i + 1),
                                title: up ? "knock block " + (i + 1) + " (and the ones above it) down" : "stand block " + (i + 1) + " back up" }));
  } else if (t.kind === "ball" && !(t.reading && t.reading.side === "other")) {
    acts = [];                                         // #264: self has it -- its turn comes next
  } else {
    acts = toyActions({ toy: t.name, kind: t.kind });
  }
  if (acts.length) {
    out.push('<div class="pacts">' + acts.map((a) => '<button type="button" class="pk" data-poke-toy="' + escAttr(t.name) + '" data-poke-action="' +
      escAttr(a.action) + '"' + (a.arg != null ? ' data-poke-arg="' + escAttr(a.arg) + '"' : "") + ' title="' + escAttr(a.title) + '">' +
      escText(a.label) + "</button>").join("") + "</div>");
  } else {
    out.push('<p class="muted">' + (t.kind === "body" ? escText(SELF_NAME) + " only: nobody else can change it"
      : t.kind === "mirror" ? "it shows " + escText(SELF_NAME) + " its own moves, and a stranger's beside them: it takes no pokes"
      : t.kind === "ball" ? escText(SELF_NAME) + " has the ball: it rolls it to you on its turn" : "no actions: its own clock is the point") + "</p>");
  }
  out.push('<p class="pres' + (res && !res.ok ? " bad" : "") + '" aria-live="polite">' + escText(res ? res.text : "") + "</p>");
  return out.join("");
}

/** The session auto-chat acts on: the open chat, or -- while a mind's
 *  read-only view is shown (sid "mind:<name>") -- the person's own chat
 *  behind it (#318: membrane home's ▶/⏸ posted to /session/mind:…/auto). */
function autoSid(sid, mindPrev) {
  if (!sid) return null;
  return String(sid).startsWith("mind:") ? (mindPrev || null) : sid;
}

/** Membrane's guess of what Argus would DO this turn (predict.py: virtual,
 *  never run), scored against what he did: ✓ top guess, ~ in the top 3. */
function guessHtml(line) {
  const g = line && line.guess;
  if (!g) return "";
  const sc = line.guess_score || {}, did = line.did || {};
  const tools = (g.tools || []).map((x) => x[0]).join(", ");
  const ents = (g.ents || []).map((x) => x[0]).join(", ");
  const mark = (v) => (v === true ? "✓" : v === false ? "✗" : "");
  const t = sc.moves ? sc.top1 + "/" + sc.moves + " moves" + (sc.top3 > sc.top1 ? " (" + sc.top3 + " in top 3)" : "")
    : sc.tool1 ? "✓" : sc.tool3 ? "~" : "✗";
  const title = "membrane guessed " + SEAT + "'s actions (never run) -- he did: " +
    ((did.tools || []).join(", ") || "(reply)") + ((did.ents || []).length ? " on " + did.ents.join(", ") : "");
  return '<div class="vguess" title="' + escAttr(title) + '">🔮 ' + escText(tools || "(reply)") + " " + t +
    (ents ? " · " + escText(ents) + " " + mark(sc.ent3) : "") + "</div>";
}

function pretty(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
}

/** A tool part as a card model; membrane's lane refusals surface. */
function toolView(p) {
  const s = (p && p.state) || {};
  let status = s.status || "pending";
  const output = pretty(s.output != null ? s.output : (s.error != null ? s.error : ""));
  if (status === "completed" && /^Refused:/.test(output)) status = "refused";
  const t = (p && p.time) || s.time || {};
  const ms = t.start && t.end ? t.end - t.start : null;
  return { name: (p && (p.tool || p.name)) || "tool", status, title: s.title || "",
           input: s.input && Object.keys(s.input).length ? pretty(s.input) : "", output, ms };
}

function shouldStick(el, slack) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < (slack == null ? 80 : slack);
}

function fmtElapsed(ms) {
  const s = Math.max(0, Math.round((ms || 0) / 1000));
  if (s < 60) return s + "s";
  return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
}

function kfmt(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n); }

function fmtUsage(info) {
  const out = [];
  const tk = (info && info.tokens) || {};
  if (tk.input) out.push("in " + kfmt(tk.input));
  if (tk.output) out.push("out " + kfmt(tk.output));
  if (tk.reasoning) out.push("think " + kfmt(tk.reasoning));
  const t = (info && info.time) || {};
  if (t.created && t.completed) out.push(((t.completed - t.created) / 1000).toFixed(1) + "s");
  return out.join(" · ");
}

/** The models membrane sees on Ollama (the /v1/models rows it owns as
 * 'membrane->ollama'), in its own order; the router's catalogue stays out. */
function ollamaModels(rows) {
  // ('@explore' rides the same owner: membrane's lane sentinel, not a model)
  return (rows || []).filter((r) => r && r.owned_by === "membrane->ollama" && r.id && !String(r.id).startsWith("@"))
    .map((r) => String(r.id));
}

/** The message body's model: a pick rides as {providerID, modelID}; no pick
 * (the seat's default) sends nothing -- unless the session holds another
 * pick, which the seat's own model then replaces. */
function modelField(pick, sessionModel, seatModel) {
  if (pick) return { providerID: "membrane", modelID: pick };
  if (sessionModel && seatModel && sessionModel !== seatModel) return { providerID: "membrane", modelID: seatModel };
  return null;
}

// MEMBRANE HOME IS THE LANDING (2026-10-10, the operator: "make the
// /membrane renderer default ui for everyone unless they switch to HA thru
// the button"): a fresh HA tab that opens on the default dashboard goes to
// /membrane -- unless the user last LEFT membrane through its HA menu
// (localStorage 'membrane.home.left' = '1'; opening membrane clears it).
// Deep links are never redirected.
const LANDING_RE = /^\/(?:lovelace(?:\/[^/]*)?|home)?\/?$/;
function membraneLanding(path, haChosen) { return !haChosen && LANDING_RE.test(String(path || "")); }

/** The newest finished assistant reply, speakable: its text parts (no tool
 * output, no reasoning), without 'membrane:' frame lines or markdown marks
 * -- membrane home reads it aloud (2026-10-10). null when nothing new. */
/** Text made fit for speaking: no 'membrane:' frame lines, no markdown. */
function speakText(text) {
  return String(text || "").split("\n").filter((l) => !/^\s*[*_`>]*\s*membrane\s*:/i.test(l)).join("\n")
    .replace(/```[\s\S]*?```/g, " ").replace(/[*_`#>|]/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s*\[membrane voice\]\s*/i, "").replace(/\s+/g, " ").trim();
}

function replyToSpeak(rows) {
  for (let i = (rows || []).length - 1; i >= 0; i--) {
    const r = rows[i];
    if (!r || !r.info) continue;
    if (r.info.role === "user") {
      // only a PERSON's row ends the search: membrane's own voice lines and
      // auto-chat frames land as user rows DURING Argus's turn (the reply
      // was never spoken, 2026-10-10)
      const said = (r.parts || []).map((p) => p.text || "").join("\n");
      if (SPEAKER_RE.test(said)) return null;
      continue;
    }
    if (r.info.role !== "assistant") continue;
    if (!(r.info.time || {}).completed) return null;
    const text = (r.parts || []).filter((p) => p.type === "text" && !p.synthetic).map((p) => p.text || "").join("\n")
      .split("\n").filter((l) => !/^\s*[*_`>]*\s*membrane\s*:/i.test(l)).join("\n")
      .replace(/```[\s\S]*?```/g, " ").replace(/[*_`#>|]/g, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\s+/g, " ").trim();
    return text ? { id: r.info.id, text } : null;
  }
  return null;
}

// THE JOB'S VALUE (issue #153): a seat's JS job (jsexec.py) hands back what a
// console would -- the final expression's value, or an explicit return; a
// Promise awaited. The code used to be an async function BODY as written, so
// a bare '1+1' was a statement whose value fell on the floor ('=> undefined').
// Compiling only parses (nothing runs), so the shapes are tried in order:
// the whole code as one expression; then the code with its last statement
// returned (split at a ';' or line end, the part before it whole
// statements on its own); then the body as written (its own return, or no value).
const JS_ASYNC_FN = Object.getPrototypeOf(async function () {}).constructor;

function jsCompile(code, names) {
  const src = String(code == null ? "" : code);
  const asWritten = new JS_ASYNC_FN(...names, src);   // a SyntaxError here is the honest answer
  const make = (body) => { try { return new JS_ASYNC_FN(...names, body); } catch (e) { return null; } };
  const lone = src.replace(/[\s;]+$/, "");
  if (!lone.trim()) return asWritten;
  const whole = make("return (\n" + lone + "\n);");
  if (whole) return whole;
  let tries = 0;
  for (let i = lone.length - 1; i > 0 && tries < 400; i--) {
    const c = lone[i - 1];
    if (c !== ";" && c !== "\n") continue;
    const head = lone.slice(0, i), tail = lone.slice(i);
    if (!tail.trim()) continue;
    tries++;
    if (!make("return (\n" + tail + "\n);") || !make(head)) continue;   // the head a whole statement list
    const fn = make(head + "\nreturn (\n" + tail + "\n);");
    if (fn) return fn;
  }
  return asWritten;                                    // its own return, or no value
}

// a value as text: a string as itself, JSON when JSON can carry it, else
// what the console would print (a function, a BigInt, a Symbol, a cycle, a
// Map/Set, an element, an Error)
function jsShow(v) {
  if (typeof v === "string") return v;
  if (typeof v === "function") return "[function " + (v.name || "anonymous") + "] " + String(v).slice(0, 400);
  if (typeof v === "bigint") return String(v) + "n";
  if (typeof v === "symbol") return v.toString();
  if (v instanceof Error) return String(v.stack || v);
  if (typeof Element !== "undefined" && v instanceof Element) return String(v.outerHTML || v).slice(0, 4000);
  if (v instanceof Map) return "Map(" + v.size + ") " + jsShow(Object.fromEntries(v));
  if (v instanceof Set) return "Set(" + v.size + ") " + jsShow([...v]);
  try {
    const seen = new WeakSet();
    const j = JSON.stringify(v, (k, x) => {
      if (typeof x === "bigint") return String(x) + "n";
      if (typeof x === "function") return "[function " + (x.name || "anonymous") + "]";
      if (typeof x === "symbol") return x.toString();
      if (x && typeof x === "object") { if (seen.has(x)) return "[circular]"; seen.add(x); }
      return x;
    });
    if (j !== undefined) return j;
  } catch (e) { /* fall through */ }
  return String(v);
}

// AN OFFLINE SEAT (#283, the zoo): the relay answers a seat it can't reach
// with {error: "seat_offline", seat, message} -- words, never the raw
// ClientConnectorError; the chat says "<name> is offline" and, when another
// seat of the house answers (GET /api/membrane/zoo), offers to switch to it.
const ZOO_URL = "/ui/zoo";

/** {text, switchTo: {id, name} | null} for a relay error, null when it is not an offline seat. */
function offlineNotice(data, zoo) {
  if (!data || typeof data !== "object" || data.error !== "seat_offline") return null;
  const name = data.seat || "the seat";
  const other = (zoo || []).find((s) => s && s.online && !s.active && s.id !== data.seat_id);
  return other ? { text: name + " is offline — switch to " + other.name + "?", switchTo: { id: other.id, name: other.name } }
    : { text: name + " is offline", switchTo: null };
}

const AiPanelLogic = { offlineNotice, jsCompile, jsShow, voiceLogItems, seatView, selfView, selfExp, selfLine, selfName, setSelfName, selfLinesHtml, selfCardHtml, shortEid, connLabel, phantomHtml, isMove, loadPhantoms, guessHtml, autoSid, speakText, replyToSpeak, membraneLanding, ollamaModels, modelField, md, sseState, sseFeed, stripSpeaker, chatState, applyEvent, reconcileStatus, reloadDecision, toyActions, toyButtonsHtml,
                       attrBar, hoursText, curveGeom, curveSvg, timeParts, sheetGrowth, sheetTime, selfSheetHtml, SHEET_SECTIONS, SHEET_OPEN,
                       toyGlyph, toyAfterPoke, toyBadge, toyDockItem, toyDockHtml, toyPopoverHtml, BADGE_S, skillStatHtml, skillSigned,
                       levelUpText, unlockText, sheetToys, TOY_ICONS,
                       skillTree, treeLayout, nodeCard, nodeStateText, achievementsList, ringDash, childAge, renderSkillTree, TREE_LADDER,
                       loadRows, loadVoice, voiceHtml, voiceTurnText, toolView, shouldStick, fmtElapsed, fmtUsage };
if (typeof module !== "undefined" && module.exports) module.exports = AiPanelLogic;

// ------------------------------------------------------------ the element

const OVERLAY_ROWS = 200;

const STYLE = `
  :host { display:block; height:100vh; height:100dvh; overflow:hidden;
          background:var(--primary-background-color,#111); color:var(--primary-text-color,#ddd);
          font:14px/1.5 system-ui,-apple-system,sans-serif; }
  * { box-sizing:border-box; }
  button { font:inherit; color:inherit; cursor:pointer; }
  .app { display:flex; height:100%; position:relative; }
  .side { width:260px; flex:none; display:flex; flex-direction:column;
          border-right:1px solid var(--divider-color,rgba(128,128,128,.25));
          background:var(--sidebar-background-color,var(--card-background-color,#161616)); }
  .side .head { display:flex; align-items:center; gap:6px; padding:10px; }
  .side .head .brand { flex:1; font-weight:600; opacity:.85; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .btn { border:0; border-radius:8px; padding:6px 10px; background:var(--secondary-background-color,#2a2a2a); }
  .btn.primary { background:var(--primary-color,#3477d6); color:var(--text-primary-color,#fff); }
  .btn.danger { background:var(--error-color,#c0392b); color:#fff; }
  .btn.icon { padding:4px 8px; background:transparent; opacity:.75; }
  .btn.icon:hover { opacity:1; background:rgba(128,128,128,.15); }
  .list { flex:1; overflow-y:auto; padding:4px 6px 10px; }
  .ses { display:flex; align-items:center; gap:6px; padding:7px 8px; border-radius:8px; cursor:pointer; }
  .ses:hover { background:rgba(128,128,128,.12); }
  .ses.on { background:rgba(128,128,128,.22); }
  .ses .t { flex:1; min-width:0; }
  .ses .t b { display:block; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .ses .t small { opacity:.55; font-size:11px; }
  .ses .del { visibility:hidden; }
  .ses:hover .del { visibility:visible; }
  .dot { width:8px; height:8px; border-radius:50%; flex:none; background:transparent; }
  .dot.busy { background:var(--warning-color,#e6a23c); animation:pulse 1s infinite; }
  .side .foot { padding:8px 12px; font-size:11px; opacity:.55; border-top:1px solid var(--divider-color,rgba(128,128,128,.25));
                white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .main { flex:1; min-width:0; display:flex; flex-direction:column; }
  .bar { display:flex; align-items:center; gap:8px; padding:8px 12px; min-height:48px;
         border-bottom:1px solid var(--divider-color,rgba(128,128,128,.25)); }
  .bar .title { flex:1; min-width:0; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; cursor:text; }
  .bar select { border-radius:8px; padding:4px 8px; border:1px solid rgba(128,128,128,.35); color:inherit;
                background:var(--secondary-background-color,#222); font:inherit; }
  .pill { font-size:12px; padding:2px 10px; border-radius:999px; background:rgba(128,128,128,.18); white-space:nowrap; }
  .pill.busy { background:rgba(230,162,60,.22); }
  .pill.off { background:rgba(192,57,43,.25); }
  .minds { display:none; flex-wrap:wrap; gap:6px; padding:6px 12px; font-size:12px;
           border-bottom:1px solid var(--divider-color,rgba(128,128,128,.25)); }
  .minds.open { display:flex; }
  .mind { padding:2px 8px; border-radius:8px; background:rgba(128,128,128,.14); max-width:100%;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .mind b { font-weight:600; }
  .err { color:var(--error-color,#f66); font:13px monospace; white-space:pre-wrap; padding:0 12px; }
  .err .zswitch { font:inherit; margin-left:6px; padding:2px 10px; border-radius:999px; cursor:pointer;
                  border:1px solid currentColor; background:transparent; color:var(--primary-text-color,#ddd); }
  .err:not(:empty) { padding:8px 12px; }
  .scroller { flex:1; min-height:0; overflow-y:auto; position:relative; overscroll-behavior:contain; }
  .msgs { max-width:920px; margin:0 auto; padding:16px 18px 24px; }
  .empty { opacity:.5; text-align:center; margin-top:18vh; }
  .msg { margin:14px 0; overflow-wrap:anywhere; }
  .mmrow { flex-basis:100%; display:flex; flex-wrap:wrap; gap:10px; align-items:center; font-size:12px; margin-top:4px; }
  .mmrow select { font:inherit; border-radius:6px; padding:1px 4px; }
  .msg.user { display:flex; justify-content:flex-end; }
  .msg.user .bub { background:var(--primary-color,#3477d6); color:var(--text-primary-color,#fff);
                   border-radius:14px 14px 4px 14px; padding:8px 12px; max-width:85%; }
  .msg.user .bub a { color:inherit; }
  /* a prompt membrane's own loops wrote (a watch's pulse, a goal's nudge) -- not a person: no bubble */
  .msg.user.machine .bub { background:transparent; color:inherit; border:1px dashed rgba(128,128,128,.35);
                           opacity:.7; font-size:.92em; }
  .msg.ai { border-left:2px solid rgba(128,128,128,.35); padding-left:12px; }
  .vox { margin:8px 0 0; max-width:85%; border:1px dashed rgba(156,110,220,.6); border-radius:14px 14px 14px 4px;
         padding:6px 12px; background:rgba(156,110,220,.08); }
  .vwho { font-size:11px; opacity:.65; margin-bottom:2px; } .vclose { margin-left:6px; opacity:.9; }
  .vtext { font-style:italic; } .vmode { opacity:.7; } .vbar { margin-top:4px; display:flex; gap:4px; }
  .vbtn { border:0; background:transparent; cursor:pointer; opacity:.45; font-size:14px; padding:0 4px; }
  .vbtn:hover, .vbtn.on { opacity:1; } .vre { font-size:12px; opacity:.7; margin-top:4px; }
  .vguess { font-size:11px; opacity:.6; margin-top:3px; }
  .vlog { max-height:40%; overflow:auto; border-bottom:1px solid var(--divider-color, #444); padding:6px 10px; font-size:13px; }
  .vlog .vlhead { font-weight:600; opacity:.7; margin-bottom:4px; } .vlog .vli { margin:3px 0; }
  .vlog .vlwho { opacity:.6; font-size:11px; } .vlog .vli.argus .vlwho { color:var(--primary-color, #4af); }
  .phantom { opacity:.45; font-style:italic; border:1px dashed var(--divider-color, #888); border-radius:10px;
             padding:4px 9px; margin:4px 0; font-size:13px; white-space:pre-wrap; }
  .phantom.ptool { font-style:normal; font-family:var(--code-font-family, monospace); font-size:12px; }
  .phantom.pending { opacity:.3; } .phantom .pmark { font-style:normal; font-size:11px; }
  .autobtn.spinning { animation:aspin 2.4s linear infinite; } @keyframes aspin { to { transform:rotate(360deg); } }
  .vedit { display:flex; gap:6px; margin-top:6px; } .vedit textarea { flex:1; font:inherit; border-radius:8px; padding:4px 8px; }
  .msg.ai.live { border-left-color:var(--primary-color,#3477d6); }
  .who { font-size:11px; opacity:.55; margin-bottom:2px; }
  .md p { margin:.35em 0; } .md h3,.md h4,.md h5,.md h6 { margin:.7em 0 .3em; }
  .md ul,.md ol { margin:.3em 0; padding-left:1.4em; }
  .md blockquote { margin:.4em 0; padding-left:10px; border-left:3px solid rgba(128,128,128,.4); opacity:.85; }
  .md code { font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace; background:rgba(128,128,128,.18);
             border-radius:4px; padding:0 4px; }
  .md pre.code { margin:.5em 0; border-radius:8px; background:rgba(0,0,0,.35); overflow:hidden; }
  .md pre.code code { display:block; padding:10px 12px; background:none; overflow-x:auto; white-space:pre; }
  .codebar { display:flex; justify-content:space-between; align-items:center; padding:3px 10px; font-size:11px;
             opacity:.7; background:rgba(128,128,128,.15); }
  .codebar .copy { border:0; background:none; font-size:11px; padding:0; opacity:.8; }
  .tablewrap { overflow-x:auto; } .md table { border-collapse:collapse; margin:.4em 0; font-size:13px; }
  .md th,.md td { border:1px solid rgba(128,128,128,.3); padding:3px 8px; text-align:left; }
  .md a { color:var(--primary-color,#4a90e2); }
  details.tool, details.think { margin:6px 0; border-radius:8px; background:rgba(128,128,128,.10);
                                border:1px solid rgba(128,128,128,.18); }
  details > summary { cursor:pointer; padding:5px 10px; font-size:12.5px; list-style:none; display:flex; gap:8px;
                      align-items:center; white-space:nowrap; overflow:hidden; }
  details > summary::-webkit-details-marker { display:none; }
  summary .nm { font-family:ui-monospace,Menlo,monospace; font-weight:600; }
  summary .ti { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; opacity:.7; }
  summary .ms { opacity:.55; font-size:11px; }
  .badge { font-size:10.5px; padding:0 6px; border-radius:6px; background:rgba(128,128,128,.25); }
  .st-running .badge, .st-pending .badge { background:rgba(230,162,60,.3); }
  .st-error .badge { background:rgba(192,57,43,.45); }
  .st-refused { border-color:rgba(192,57,43,.5) !important; }
  .st-refused .badge { background:rgba(192,57,43,.6); color:#fff; }
  .io { padding:4px 10px 8px; }
  .io .lbl { font-size:10.5px; text-transform:uppercase; letter-spacing:.06em; opacity:.5; margin-top:4px; }
  .io pre { margin:2px 0; max-height:360px; overflow:auto; font:12px ui-monospace,Menlo,monospace;
            white-space:pre-wrap; background:rgba(0,0,0,.25); border-radius:6px; padding:6px 8px; }
  .more { font-size:11px; border:0; background:none; color:var(--primary-color,#4a90e2); padding:0; }
  details.think .md { padding:2px 10px 8px; opacity:.75; font-size:13px; }
  .usage { font-size:11px; opacity:.45; margin-top:4px; }
  .working { opacity:.6; font-style:italic; margin:10px 0; display:flex; gap:8px; align-items:center; }
  .spin { width:10px; height:10px; border-radius:50%; border:2px solid currentColor; border-right-color:transparent;
          animation:spin .8s linear infinite; }
  .jump { position:sticky; bottom:12px; float:right; margin-right:16px; display:none; z-index:2;
          border:0; border-radius:999px; padding:6px 12px; font-size:12px;
          background:var(--primary-color,#3477d6); color:var(--text-primary-color,#fff); box-shadow:0 2px 8px rgba(0,0,0,.3); }
  .jump.show { display:block; }
  .composer { border-top:1px solid var(--divider-color,rgba(128,128,128,.25)); padding:10px 12px 12px; }
  .mindbar { display:none; border-top:1px solid var(--divider-color,rgba(128,128,128,.25)); padding:8px 12px; font-size:12px; opacity:.75; text-align:center; }
  :host([mind]) .mindbar { display:block; }
  :host([mind]) .composer { display:none; }
  .composer .box { max-width:920px; margin:0 auto; display:flex; gap:8px; align-items:flex-end; }
  .composer textarea { flex:1; resize:none; min-height:44px; max-height:40vh; border-radius:10px;
                       border:1px solid rgba(128,128,128,.35); background:var(--card-background-color,#111);
                       color:inherit; padding:10px 12px; font:inherit; }
  .newver { max-width:920px; margin:0 auto 8px; text-align:center; }
  .newver button { font-size:12px; padding:4px 12px; border-radius:999px; }
  .composer .hint { max-width:920px; margin:4px auto 0; font-size:11px; opacity:.45; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @keyframes pulse { 50% { opacity:.35; } }
  .scrim { display:none; }
  @media (max-width: 760px) {
    .side { position:absolute; z-index:5; top:0; bottom:0; left:0; transform:translateX(-100%);
            transition:transform .18s ease; box-shadow:2px 0 12px rgba(0,0,0,.4); }
    .app.sideopen .side { transform:none; }
    .app.sideopen .scrim { display:block; position:absolute; inset:0; z-index:4; background:rgba(0,0,0,.35); }
  }
  @media (min-width: 761px) { .sidetoggle { display:none; } .app.sidehidden .side { display:none; }
                              .app.sidehidden .sidetoggle { display:inline-block; } }
  /* THE OVERLAY (2026-10-10, membrane home): the chat floats over the 3D
     field -- glass surfaces, the graph visible through the transcript */
  :host([overlay]) { height:100%; background:transparent; }
  :host([overlay]) .app.sidehidden .side { display:none; }
  :host([overlay]) .app.sidehidden .sidetoggle { display:inline-block; }
  :host([overlay]) .menu { display:none !important; }   /* membrane home has HA's menu in its own bar */
  /* a 4" phone (2026-10-11): the menu is taller than the chat sheet -- it scrolls */
  .side { overflow-y:auto; overscroll-behavior:contain; }
  :host([overlay]) .side { background:color-mix(in srgb, var(--card-background-color,#111) 92%, transparent); }
  :host([overlay]) .bar, :host([overlay]) .composer {
    background:color-mix(in srgb, var(--card-background-color,#111) 80%, transparent); }
  :host([overlay]) .scroller { background:color-mix(in srgb, var(--primary-background-color,#111) 30%, transparent); }
  :host([overlay]) .msgs { padding:10px 12px 16px; }
  /* the card is narrower than a page: the bar wraps instead of clipping its
     buttons (membrane / LLM / auto-chat / minds) off the edge */
  :host([overlay]) .bar { flex-wrap:wrap; row-gap:4px; padding:6px 10px; }
  /* OVERLAY ONLY (2026-10-13, the operator's ask): auto-chat moved out --
     the home page's top bar drives it (a ▶/⏸ beside 💬); the panel's own
     node stays only for its state/listener, hidden */
  :host([overlay]) .autobtn { display:none; }
  :host([overlay]) .side { position:absolute; z-index:5; top:0; bottom:0; left:0; width:min(260px, 80%);
                           box-shadow:2px 0 14px rgba(0,0,0,.35); }
  :host([overlay]) .ctl { display:flex; flex-direction:column; gap:2px; padding:2px 8px 8px;
                          border-bottom:1px solid var(--divider-color,rgba(128,128,128,.25)); }
  :host([overlay]) .ctl select { width:100%; margin:0 0 4px; border-radius:8px; padding:4px 8px; color:inherit;
                                       border:1px solid rgba(128,128,128,.35); background:var(--secondary-background-color,#222); font:inherit; }
  :host([overlay]) .ctl .btn.icon { display:flex; align-items:center; gap:10px; width:100%; text-align:left; padding:6px 8px; }
  :host([overlay]) .ctl .ico { width:1.4em; text-align:center; display:inline-block; }
  :host([overlay]) .ctl .autobtn.spinning { animation:none; }
  :host([overlay]) .ctl .autobtn.spinning .ico { animation:aspin 2.4s linear infinite; }
  :host([overlay]) .bar .title { flex:1 1 120px; }
  :host([overlay]) .bar select.agent { max-width:120px; }
  :host([overlay]) .hint { display:none; }
  :host([overlay]) .msg:not(.user) .bub, :host([overlay]) .msg:not(.user) > .md {
    text-shadow:0 1px 2px color-mix(in srgb, var(--primary-background-color,#000) 85%, transparent); }
`;

function definePanel() {
  class AiTerminalPanel extends HTMLElement {
    constructor() {
      super();
      // made before this class was defined (membrane home creates the chat while
      // panel.js still loads): the hass / narrow it was given are own properties
      // that would shadow the setters forever -- the chat never booted. Take them over.
      const own = (k) => Object.prototype.hasOwnProperty.call(this, k);
      const early = { hass: own("hass") ? this.hass : undefined, narrow: own("narrow") ? this.narrow : undefined };
      if (own("hass")) delete this.hass;
      if (own("narrow")) delete this.narrow;
      if (early.hass) this._hass = early.hass;
      if (early.narrow != null) this._narrow = !!early.narrow;
      this.attachShadow({ mode: "open" });
      this._sid = null;
      this._sessions = [];
      this._statusBy = {};
      this._chat = null;
      this._agent = "build";
      this._stick = true;
      this._live = false;
      this._booted = false;
      this._raf = 0;
      this._expanded = new Set();     // open tool/think details survive re-renders
    }

    set hass(h) {
      this._hass = h;
      if (!this._booted && this.isConnected && this.shadowRoot.firstChild) { this._booted = true; this._boot(); }
    }
    get hass() { return this._hass; }
    set narrow(n) { this._narrow = !!n; if (this._menuBtn) this._menuBtn.style.display = n ? "" : "none"; }

    connectedCallback() {
      if (!this.shadowRoot.firstChild) this._render();
      if (!this._booted && this._hass) { this._booted = true; this._boot(); }
    }

    disconnectedCallback() {
      this._booted = false;
      this._closeStream();
      this._stopPoll();
      this._stopReconcile();
      clearInterval(this._tick);
      clearInterval(this._mindsTimer);
    }

    // ---------------------------------------------------------- dom
    _render() {
      const s = document.createElement("style");
      s.textContent = STYLE;
      const app = document.createElement("div");
      app.className = "app";
      app.innerHTML = `
        <aside class="side">
          <div class="head">
            <span class="brand">AI Dashboard</span>
            <button class="btn primary new" title="New chat (/new)">＋ New</button>
            <button class="btn icon hide" title="Hide sidebar">⟨</button>
          </div>
          <div class="list"></div>
          <div class="foot"></div>
        </aside>
        <div class="scrim"></div>
        <section class="main">
          <div class="bar">
            <button class="btn icon menu" title="Home Assistant menu" style="display:none">☰</button>
            <button class="btn icon sidetoggle" title="Chats">☷</button>
            <span class="title" title="Double-click to rename"></span>
            <select class="agent" title="Agent (/build, /plan)"></select>
            <select class="modelpick" title="Model (what membrane sees on Ollama)"><option value="">model…</option></select>
            <span class="pill state">…</span>
            <button class="btn icon voicebtn" title="membrane's own voice (show / hide)">🗣</button>
            <button class="btn icon autobtn" title="auto-chat: the LLM works on by itself; you can join any time">⟳</button>
            <button class="btn icon selfsleep" aria-pressed="false" title="${escAttr(SELF_NAME)} (#161): asleep or awake -- tap to switch">💤</button>
            <span class="selfstat" title="where ${escAttr(SELF_NAME)} is now: its rung, the bar toward the next, what it waits on"></span>
            <button class="btn icon thinkbtn" title="the think dial: the reasoning the cards do (auto = forward verbatim)">💭</button>
            <button class="btn icon jsbtn" title="allow JS scripting in this window -- the page runs it with your HA login -- off by default">🧩</button>
            <button class="btn icon vlogbtn" title="the voice log: what was said aloud (hidden from the chat)">🗒</button>
            <button class="btn icon pausebtn" title="pause the seat: every acting lane stands down (loops, dreamer, thinker, night)">⏸</button>
            <button class="btn icon mindsbtn" title="membrane's minds">🧠</button>
          </div>
          <div class="minds"></div>
          <div class="vlog" hidden></div>
          <div class="err"></div>
          <div class="scroller"><div class="msgs"></div><button class="jump">↓ latest</button></div>
          <div class="mindbar"></div>
          <div class="composer">
            <div class="newver" hidden><button class="btn">new version · click to reload</button></div>
            <div class="box">
              <textarea rows="1" placeholder="Ask the brain…"></textarea>
              <button class="btn primary send">Send</button>
              <button class="btn danger stop" style="display:none" title="Stop (Esc)">Stop</button>
            </div>
            <div class="hint">Enter send · Shift+Enter newline · Esc stop · /new /build /plan /rename</div>
          </div>
        </section>`;
      this.shadowRoot.append(s, app);
      if (this.hasAttribute("overlay")) {
        app.classList.add("sidehidden");
        // the card is narrow: the controls live in the hideable side menu,
        // above the conversations (the same nodes -- their state and
        // listeners come along)
        const ctl = document.createElement("div");
        ctl.className = "ctl";
        // the auto-chat button does NOT live here any more (2026-10-13,
        // the operator's ask): home.js's top bar carries it beside the
        // chat button as a ▶/⏸; the panel's own node stays (hidden --
        // never listed) so its listener and state keep working.
        for (const [cls, label] of [["agent", null], ["modelpick", null], ["voicebtn", "membrane's voice"],
                                    ["selfsleep", SELF_NAME + " asleep"], ["selfstat", null], ["thinkbtn", "think"], ["jsbtn", "JS scripting: off"], ["vlogbtn", "voice log"], ["pausebtn", "pause the seat"], ["mindsbtn", "minds"]]) {
          const el = app.querySelector("." + cls);
          if (!el) continue;
          if (label) {
            const ico = document.createElement("span");
            ico.className = "ico"; ico.textContent = el.textContent; el.textContent = "";
            const lbl = document.createElement("span");
            lbl.className = "lbl"; lbl.textContent = label;
            el.append(ico, lbl);
          }
          ctl.appendChild(el);
        }
        app.querySelector(".side .head").after(ctl);
      }
      const $ = (q) => app.querySelector(q);
      this._app = app;
      this._list = $(".list");
      this._foot = $(".foot");
      this._titleEl = $(".title");
      this._agentSel = $(".agent");
      this._pill = $(".state");
      this._mindsEl = $(".minds");
      this._errEl = $(".err");
      this._scroller = $(".scroller");
      this._msgs = $(".msgs");
      this._jump = $(".jump");
      this._ta = $("textarea");
      this._newVer = $(".newver");
      this._newVer.querySelector("button").addEventListener("click", () => this._reloadNow());
      this._sendBtn = $(".send");
      this._stopBtn = $(".stop");
      this._menuBtn = $(".menu");
      if (this._narrow) this._menuBtn.style.display = "";

      $(".new").addEventListener("click", () => this._newChat());
      $(".hide").addEventListener("click", () => this._toggleSide(false));
      $(".sidetoggle").addEventListener("click", () => this._toggleSide(true));
      $(".scrim").addEventListener("click", () => this._toggleSide(false));
      this._menuBtn.addEventListener("click", () => this.dispatchEvent(
        new Event("hass-toggle-menu", { bubbles: true, composed: true })));
      $(".mindsbtn").addEventListener("click", () => this._toggleMinds());
      this._voiceBtn = $(".voicebtn");
      this._voiceOn = true;               // per page view (sessions stay server-side)
      this._voiceBtn.style.opacity = this._voiceOn ? "1" : ".4";
      this._voiceBtn.addEventListener("click", () => {
        this._voiceOn = !this._voiceOn;
        this._voiceBtn.style.opacity = this._voiceOn ? "1" : ".4";
        if (this._chat) { this._chat.all = true; this._schedule(); }
      });
      this._autoBtn = $(".autobtn");
      this._autoBtn.addEventListener("click", (ev) => this._toggleAuto(ev && ev.shiftKey ? "teach" : "explore").catch(() => {}));
      this._jsBtn = $(".jsbtn");
      this._jsBtn.addEventListener("click", () => this._toggleJs().catch(() => {}));
      this._jsTab = "tab_" + Math.random().toString(36).slice(2, 10);
      this._paintJs();
      this._vlogEl = $(".vlog");
      this._vlogBtn = $(".vlogbtn");
      this._vlogBtn.addEventListener("click", () => this._toggleVoiceLog().catch(() => {}));
      this._pauseBtn = $(".pausebtn");
      this._pauseBtn.addEventListener("click", () => this._togglePause().catch(() => {}));
      this._selfSleep = $(".selfsleep");                           // #299: sleep / wake (the 🌱 chooser is gone)
      this._selfStat = $(".selfstat");
      this._selfSleep.addEventListener("click", () => this._setSelfMode(!selfView(this._seat).on).catch(() => {}));
      this._thinkBtn = $(".thinkbtn");
      this._think = "";                   // the dial as the seat holds it ("" until the models GET)
      this._paintThink();
      this._thinkBtn.addEventListener("click", () => this._toggleThink().catch(() => {}));
      this._titleEl.addEventListener("dblclick", () => this._rename());
      this._agentSel.addEventListener("change", () => { this._agent = this._agentSel.value; });
      this._modelSel = this.shadowRoot.querySelector(".modelpick");
      this._modelSel.addEventListener("change", () => { this._pick = this._modelSel.value || ""; });
      this._sendBtn.addEventListener("click", () => this._submit());
      this._stopBtn.addEventListener("click", () => this._abort());
      this._jump.addEventListener("click", () => { this._stick = true; this._toBottom(); });
      this._scroller.addEventListener("scroll", () => {
        this._stick = shouldStick(this._scroller);
        this._jump.classList.toggle("show", !this._stick);
      }, { passive: true });
      this._msgs.addEventListener("click", (ev) => this._onMsgClick(ev));
      this._msgs.addEventListener("toggle", (ev) => {
        const d = ev.target;
        if (d && d.dataset && d.dataset.key) {
          if (d.open) this._expanded.add(d.dataset.key); else this._expanded.delete(d.dataset.key);
        }
      }, true);
      this._ta.addEventListener("input", () => this._grow());
      this._ta.addEventListener("keydown", (ev) => {
        ev.stopPropagation();                 // keep HA's global shortcuts out
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); this._submit(); }
        else if (ev.key === "Escape" && this._busy()) { ev.preventDefault(); this._abort(); }
      });
      this._list.addEventListener("click", (ev) => {
        const del = ev.target.closest(".del");
        const row = ev.target.closest(".ses");
        if (!row) return;
        if (del) { ev.stopPropagation(); this._delete(row.dataset.sid); return; }
        this._switchTo(row.dataset.sid);
        this._toggleSide(false);
      });
      this._tick = setInterval(() => this._paintStatus(), 1000);
    }

    _toggleSide(open) {
      const mobile = window.matchMedia("(max-width: 760px)").matches;
      if (this.hasAttribute("overlay")) {
        // membrane home starts the menu hidden: on a phone the slide-in needs
        // 'hidden' gone too (it stayed shut on Android, 2026-10-11)
        this._app.classList.toggle("sidehidden", !open);
        this._app.classList.toggle("sideopen", !!open && mobile);
        return;
      }
      if (mobile) this._app.classList.toggle("sideopen", open);
      else this._app.classList.toggle("sidehidden", !open);
    }

    _grow() {
      this._ta.style.height = "auto";
      this._ta.style.height = Math.min(this._ta.scrollHeight + 2, window.innerHeight * 0.4) + "px";
    }

    _err(msg) { this._errEl.textContent = msg || ""; }

    /** #283: "<name> is offline — switch to <other>?" with the button that
     * switches the house (an admin's write; the page reloads onto it). */
    async _offline(data) {
      let zoo = [];
      try { const r = await this._api(ZOO_URL); if (r.ok && Array.isArray(r.data)) zoo = r.data; } catch (e) { /* no zoo: words only */ }
      const n = offlineNotice(data, zoo);
      this._err(n.text);
      if (!n.switchTo) return;
      const b = document.createElement("button");
      b.type = "button"; b.className = "zswitch"; b.textContent = "switch to " + n.switchTo.name;
      b.addEventListener("click", async () => {
        const r = await this._api(ZOO_URL + "/active", { method: "POST", body: JSON.stringify({ id: n.switchTo.id }) });
        if (r.ok) location.reload();
        else this._err(r.status === 403 ? n.text + " (only an admin switches the seat)" : n.text + " (the switch failed: " + r.status + ")");
      });
      this._errEl.append(" ", b);
    }

    // ---------------------------------------------------------- api
    async _api(path, opts) {
      const o = Object.assign({}, opts || {});
      if (o.body !== undefined) o.headers = Object.assign({ "Content-Type": "application/json" }, o.headers || {});
      const r = await this._hass.fetchWithAuth(path, o);
      let data = null;
      if (r.status !== 204) {
        const txt = await r.text();
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = txt; }
      }
      return { status: r.status, ok: r.ok, data };
    }

    _apiErr(what, r) {
      if (r.data && r.data.error === "seat_offline") return r.data.message || "the seat is offline";   // #283
      return what + " (" + r.status + "): " + (typeof r.data === "string" ? r.data : JSON.stringify(r.data));
    }

    // ---------------------------------------------------------- boot
    async _boot() {
      this._err("");
      try {
        const conf = await this._api("/ui/conf");
        if (!conf.ok || !conf.data || !conf.data.serve_ok) {
          this._err("brain lane off: set ai_terminal serve_url + serve_pass in configuration.yaml");
          return;
        }
        this._foot.textContent = conf.data.serve_url;
        this._applySeatName(conf.data.name);      // issue #6: the conf carries it
        this._build = conf.data.build || "";       // #243: the page code this tab runs
        const health = await this._api(SERVE + "/global/health");
        if (!health.ok) {
          if (health.data && health.data.error === "seat_offline") await this._offline(health.data);   // #283
          else this._err(this._apiErr("brain unreachable", health));
          this._paintStatus(); return;
        }
        this._foot.textContent = "membrane " + ((health.data && health.data.version) || "") + " · " + conf.data.serve_url;
        this._loadAgents();
        this._loadModel();
        this._loadThink();                 // the THINK switch's state (one GET)
        await this._listSessions();
        // #243: the chat this tab had open comes back after a reload
        let savedSid = "";
        try { savedSid = localStorage.getItem("membrane.chat.sid") || ""; } catch (e) { /* private mode */ }
        const sid = savedSid && this._sessions.some((s) => s.id === savedSid) ? savedSid
          : this._sessions.length ? this._sessions[0].id : await this._createSession();
        await this._open(sid);
        this._openStream();
        this._takeAsk();
      } catch (e) {
        this._err("chat boot failed: " + (e && e.message || e));
      }
    }

    // the field map's "Ask the brain about this" lands as ?ask=<question>:
    // prefilled (never auto-sent), then the param is dropped from the URL
    _takeAsk() {
      const u = new URL(window.location.href);
      const ask = u.searchParams.get("ask");
      if (!ask) return;
      this._ta.value = ask;
      this._grow();
      this._ta.focus();
      u.searchParams.delete("ask");
      history.replaceState(history.state, "", u.pathname + (u.search || ""));
    }

    async _loadAgents() {
      try {
        const r = await this._api(SERVE + "/agent");
        const rows = r.ok && Array.isArray(r.data) ? r.data : [];
        const prim = rows.filter((a) => a.mode === "primary" && !a.hidden).map((a) => a.name);
        const names = prim.length ? prim : ["build", "plan"];
        this._agentSel.innerHTML = names.map((n) => '<option value="' + escAttr(n) + '">' + escText(n) + "</option>").join("");
        this._agentSel.value = names.includes(this._agent) ? this._agent : names[0];
        this._agent = this._agentSel.value;
        this._minds = rows.filter((a) => a.hidden || /^(loop-\d+|thinker|dreamer)$/.test(a.name || ""));
        this._paintMinds();
      } catch (e) { /* the picker keeps its defaults */ }
    }

    async _loadModel() {
      try {
        const r = await this._api(SERVE + "/config");
        const m = r.ok && r.data && (r.data.model || (r.data.agent && r.data.agent.build && r.data.agent.build.model));
        if (m) { this._model = String(m); this._foot.textContent = this._model + " · " + this._foot.textContent; }
      } catch (e) { /* cosmetic */ }
      // THE MODEL PICKER (2026-10-08): what membrane sees on Ollama
      try {
        const r = await this._api(SERVE + "/v1/models");
        this._models = ollamaModels(r.ok && r.data ? r.data.data : []);
      } catch (e) { this._models = []; }
      this._paintModels();
    }

    _seatModel() { return String(this._model || "").replace(/^membrane\//, ""); }

    _paintModels() {
      if (!this._modelSel) return;
      const seat = this._seatModel();
      const opts = ['<option value="">' + escText(seat ? seat + " (seat default)" : "seat default") + "</option>"]
        .concat((this._models || []).filter((m) => m !== seat).map((m) =>
          '<option value="' + escAttr(m) + '">' + escText(m) + (/:cloud$|-cloud$/.test(m) ? " ☁" : "") + "</option>"));
      this._modelSel.innerHTML = opts.join("");
      const cur = this._pick || (this._sessionModel && this._sessionModel !== seat ? this._sessionModel : "");
      if (cur && !(this._models || []).includes(cur)) {
        this._modelSel.insertAdjacentHTML("beforeend", '<option value="' + escAttr(cur) + '">' + escText(cur) + " (session)</option>");
      }
      this._modelSel.value = cur;
    }

    async _loadSessionModel(sid) {
      try {
        const r = await this._api(SERVE + "/session/" + sid);
        const m = r.ok && r.data && r.data.model;
        this._sessionModel = m ? String(m.id || m.modelID || "") : "";
      } catch (e) { this._sessionModel = ""; }
      if (sid === this._sid) { this._pick = ""; this._paintModels(); }
    }

    // THE MINDS' MODELS: the watch loops' and the background cortex's
    // models (the bar's picker sets only this chat's)
    async _loadMindsModels() {
      try {
        const [cur, list] = await Promise.all([this._api(SERVE + "/membrane/models"),
          this._models ? Promise.resolve(null) : this._api(SERVE + "/v1/models")]);
        if (list && list.ok && list.data) this._models = ollamaModels(list.data.data);
        this._mindsModels = cur.ok ? cur.data : null;
        this._paintThink(cur.ok && cur.data ? cur.data.think : this._think);
      } catch (e) { this._mindsModels = null; }
      this._paintMinds();
    }

    // THE THINK DIAL (2026-10-15, the switch in the chat menu): the same
    // GET the minds picker makes carries the seat's dial; a click cycles
    // auto -> on -> off and POSTs it; the seat's own response repaints
    // (a refusal names its why and the label reverts).
    async _loadThink() {
      const r = await this._api(SERVE + "/membrane/models");
      if (r.ok && r.data) this._paintThink(r.data.think);
    }

    _paintThink(v) {
      if (v) this._think = v;
      const t = this._think || "auto";
      const ico = this._thinkBtn.querySelector(".ico");
      const lbl = this._thinkBtn.querySelector(".lbl");
      if (ico) ico.textContent = "💭 " + t;
      else this._thinkBtn.textContent = "💭 " + t;
      if (lbl) lbl.textContent = "think " + t;
      this._thinkBtn.style.opacity = t === "off" ? ".4" : "1";
      this._thinkBtn.title = "the think dial (" + t + "): the reasoning injected into the seats' calls"
                             + (t === "auto" ? " — auto = forward verbatim" : t === "on" ? " — off turns the reasoning down" : "");
    }

    async _toggleThink() {
      const NCY = { auto: "on", on: "off", off: "auto" };
      if (!NCY[this._think || "auto"]) await this._loadThink();   // state unknown: read first
      const next = NCY[this._think || "auto"];
      this._paintThink(next);
      const r = await this._api(SERVE + "/membrane/models",
                                { method: "POST", body: JSON.stringify({ think: next }) });
      if (r.ok && r.data) this._paintThink(r.data.think);
      else {
        this._err("think switch refused: " + (typeof r.data === "string" ? r.data : JSON.stringify(r.data)).slice(0, 160));
        await this._loadThink();          // the seat's truth repaints the label
      }
    }

    _mindsModelsHtml() {
      const mm = this._mindsModels;
      if (!mm || !(this._models || []).length) return "";
      const admin = !!(this._hass && this._hass.user && this._hass.user.is_admin);
      const sel = (mind, label, title) => {
        const cur = mm[mind] || "";
        const opts = (this._models || []).slice();
        if (cur && !opts.includes(cur)) opts.unshift(cur);
        return '<label class="mm" title="' + escAttr(title) + '">' + escText(label) + ' <select data-mind="' + mind + '"' +
          (admin ? "" : " disabled") + ">" + opts.map((m) => '<option value="' + escAttr(m) + '"' + (m === cur ? " selected" : "") + ">" +
          escText(m) + (/:cloud$|-cloud$/.test(m) ? " ☁" : "") + "</option>").join("") + "</select></label>";
      };
      return '<div class="mmrow">' + sel("loops", "watch loops", "the model the goal / watch loops run on") + " " +
        sel("cortex", "cortex", "the background cortex: thinker, dreamer, claim judges (keep it local: it runs often)") +
        (admin ? "" : ' <span style="opacity:.6">(admins switch these)</span>') + "</div>";
    }

    async _setMindModel(mind, model) {
      const r = await this._api(SERVE + "/membrane/models", { method: "POST", body: JSON.stringify({ [mind]: model }) });
      if (r.ok) this._mindsModels = r.data;
      else this._err("model switch refused: " + (typeof r.data === "string" ? r.data : JSON.stringify(r.data)).slice(0, 160));
      this._paintMinds();
    }

    _toggleMinds() {
      const open = !this._mindsEl.classList.contains("open");
      this._mindsEl.classList.toggle("open", open);
      clearInterval(this._mindsTimer);
      if (open) { this._loadAgents(); this._loadMindsModels(); this._mindsTimer = setInterval(() => this._loadAgents(), 15000); }
    }

    _paintMinds() {
      const ms = this._minds || [];
      this._mindsEl.innerHTML = ms.length
        ? ms.map((a) => '<span class="mind" title="' + escAttr(a.description || "") + '"><b>' + escText(a.name) +
            "</b> " + escText((a.description || "").replace(/^A thinking-loop worker /, "").slice(0, 90)) + "</span>").join("")
        : '<span class="mind">no live minds reported</span>';
      this._mindsEl.insertAdjacentHTML("beforeend", this._mindsModelsHtml());
      this._mindsEl.querySelectorAll("select[data-mind]").forEach((sel) =>
        sel.addEventListener("change", () => this._setMindModel(sel.dataset.mind, sel.value)));
    }

    // ---------------------------------------------------------- sessions
    async _listSessions() {
      const r = await this._api(SERVE + "/session");
      if (!r.ok || !Array.isArray(r.data)) throw new Error(this._apiErr("session list failed", r));
      this._sessions = r.data;                // the proxy sorts: newest first
      this._paintSessions();
      return r.data;
    }

    _paintSessions() {
      const fmt = (t) => {
        if (!t) return "";
        const d = new Date(t), now = new Date();
        return d.toDateString() === now.toDateString()
          ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
          : d.toLocaleDateString([], { month: "short", day: "numeric" });
      };
      this._list.innerHTML = this._sessions.map((s) => {
        const busy = (this._statusBy[s.id] || {}).type === "busy";
        const t = s.time && (s.time.updated || s.time.created);
        return '<div class="ses' + (s.id === this._sid ? " on" : "") + '" data-sid="' + escAttr(s.id) + '">' +
          '<span class="dot' + (busy ? " busy" : "") + '"></span>' +
          '<span class="t"><b>' + escText(s.title || NEW_TITLE) + "</b><small>" + escText(fmt(t)) + "</small></span>" +
          '<button class="btn icon del" title="Delete chat">🗑</button></div>';
      }).join("") || '<div class="empty" style="margin-top:20px">no chats yet</div>';
      const cur = this._sessions.find((s) => s.id === this._sid);
      this._titleEl.textContent = (cur && cur.title) || NEW_TITLE;
    }

    async _createSession() {
      const r = await this._api(SERVE + "/session", { method: "POST", body: JSON.stringify({ title: NEW_TITLE }) });
      if (!r.ok || !r.data || !r.data.id) throw new Error(this._apiErr("session create failed", r));
      await this._listSessions();
      return r.data.id;
    }

    async _newChat() {
      this._err("");
      try {
        const sid = await this._createSession();
        await this._open(sid);
        this._toggleSide(false);
        this._ta.focus();
      } catch (e) { this._err(e.message || String(e)); }
    }

    async _switchTo(sid) {
      if (!sid || sid === this._sid) return;
      this._err("");
      await this._open(sid);
    }

    async _open(sid) {
      this._stopPoll();
      this._sid = sid;
      try { localStorage.setItem("membrane.chat.sid", sid); } catch (e) { /* private mode */ }
      this._loadSessionModel(sid);
      this._chat = chatState(sid);
      this._rowEls = null;
      this._stick = true;
      this._paintSessions();
      await this._resync();
      this._toBottom();
    }

    // ---------------------------------------------------------- a mind's view
    // (2026-10-11, membrane home's orbs): the chat shows a loop's session, the
    // thinker's inner speech or the dreamer's goals -- read-only, polled --
    // and goes back to the person's own chat when the orb is let go
    viewMind(m) {
      if (!m || !m.name) return this.endMind();
      if (!this._mind) this._mindPrev = this._sid;
      this._mind = m;
      this.setAttribute("mind", "");
      this._stopPoll();
      clearInterval(this._mindTimer);
      this._sid = "mind:" + m.name;
      this._chat = chatState(this._sid);
      this._rowEls = null; this._stick = true;
      const bar = this.shadowRoot && this.shadowRoot.querySelector(".mindbar");
      if (bar) bar.textContent = "watching " + m.name + " · read-only · tap the orb again to come back";
      this._mindTick();
      this._mindTimer = setInterval(() => this._mindTick(), MIND_POLL_MS);
    }

    async _mindTick() {
      const m = this._mind; if (!m) return;
      let rows = [];
      if (m.kind === "loop") {
        if (!m.sid) rows = [mindRow(0, "no session for " + m.name + " yet -- it shows once the loop takes its next event")];
        else {
          const r = await this._api(SERVE + "/session/" + m.sid + "/message");
          rows = r.ok && Array.isArray(r.data) ? r.data : [mindRow(0, "can't read " + m.name + "'s session (" + (r.status || "?") + ")")];
        }
      } else {
        const r = await this._api(SERVE + "/membrane/minds");
        const list = r.ok && r.data ? (r.data[m.kind] || []) : null;
        rows = list ? list.map((e, i) => mindRow(e.ts || i, mindLine(m.kind, e)))
          : [mindRow(0, "can't read the " + m.kind + " (" + (r.status || "?") + (r.status === 403 ? ": admins only" : "") + ")")];
        if (list && !list.length) rows = [mindRow(0, "the " + m.kind + " has said nothing yet")];
      }
      if (this._mind !== m) return;
      loadRows(this._chat, rows);
      this._schedule();
      if (this._stick) this._toBottom();
    }

    endMind() {
      if (!this._mind) return;
      clearInterval(this._mindTimer); this._mindTimer = 0;
      this._mind = null;
      this.removeAttribute("mind");
      const prev = this._mindPrev; this._mindPrev = null;
      if (prev) this._open(prev);
    }

    async _resync() {
      if (!this._chat) return;
      if (this._mind) return this._mindTick();
      const sid = this._sid;
      const [hist, st, vox, auto, phs] = await Promise.all([
        this._api(SERVE + "/session/" + sid + "/message"),
        this._api(SERVE + "/session/status"),
        this._api(SERVE + "/session/" + sid + "/voice").catch(() => ({ ok: false })),
        this._api(SERVE + "/session/" + sid + "/auto").catch(() => ({ ok: false })),
        this._api(SERVE + "/session/" + sid + "/predict").catch(() => ({ ok: false })),
      ]);
      if (sid !== this._sid) return;
      loadRows(this._chat, hist.ok && Array.isArray(hist.data) ? hist.data : []);
      // what was already said is not announced again (only replies from now on)
      { const r0 = replyToSpeak(this._chat.rows); this._spokenId = r0 ? r0.id : null; this._repliesArmed = true; }
      loadVoice(this._chat, vox && vox.ok && Array.isArray(vox.data) ? vox.data : []);
      loadPhantoms(this._chat, phs && phs.ok && Array.isArray(phs.data) ? phs.data : []);
      this._loadSeat();
      this._chat.auto = auto && auto.ok && auto.data ? auto.data : { on: false };
      this._paintAuto();
      if (st.ok && st.data && typeof st.data === "object") {
        this._statusBy = st.data;
        const mine = st.data[sid];
        this._chat.status = Object.assign({ type: "idle" }, mine || {});
        if (this._chat.status.type === "busy") this._chat.status.since = Date.now() - (this._chat.status.elapsed_ms || 0);
      }
      this._schedule();
      this._paintSessions();
      this._paintStatus();
      if (this._busy() && !this._live) this._startPoll();
    }

    async _rename() {
      if (!this._sid) return;
      const cur = this._sessions.find((s) => s.id === this._sid);
      const title = window.prompt("Rename chat", (cur && cur.title) || "");
      if (title == null || !title.trim()) return;
      await this._api(SERVE + "/session/" + this._sid, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) });
      await this._listSessions();
    }

    async _delete(sid) {
      const s = this._sessions.find((x) => x.id === sid);
      if (!window.confirm('Delete "' + ((s && s.title) || NEW_TITLE) + '"?')) return;
      const r = await this._api(SERVE + "/session/" + sid, { method: "DELETE" });
      if (!r.ok) { this._err(this._apiErr("delete failed", r)); return; }
      await this._listSessions();
      if (sid === this._sid) {
        const next = this._sessions.length ? this._sessions[0].id : await this._createSession();
        await this._open(next);
      }
    }

    async _autoTitle(text) {
      const cur = this._sessions.find((s) => s.id === this._sid);
      if (cur && cur.title && cur.title !== NEW_TITLE) return;
      const title = text.replace(/\s+/g, " ").slice(0, 48) + (text.length > 48 ? "…" : "");
      await this._api(SERVE + "/session/" + this._sid, { method: "PATCH", body: JSON.stringify({ title }) });
      this._listSessions().catch(() => {});
    }

    // ---------------------------------------------------------- new page code
    // #243: a restart reuses this tab (no new one opens), so on every
    // reconnect the tab asks which build the seat serves and reloads into a
    // new one -- the chat, the 👶 panel and the camera mode are remembered
    // in localStorage; an unsent draft gets a chip instead of a reload.
    async _checkBuild() {
      if (!this._build) return;
      let r = null;
      try { r = await this._api("/ui/conf"); } catch (e) { return; }
      const cur = r && r.ok && r.data ? r.data.build : null;
      let tried = null;
      try { tried = JSON.parse(sessionStorage.getItem("membrane.reload.tried") || "null"); } catch (e) { /* none */ }
      const d = reloadDecision(this._build, cur, this._ta ? this._ta.value : "", tried, Date.now());
      if (this._newVer) this._newVer.hidden = d !== "chip";
      if (d !== "reload") return;
      try { sessionStorage.setItem("membrane.reload.tried", JSON.stringify({ build: cur, at: Date.now() })); } catch (e) { /* guard lost: still one reload */ }
      location.reload();
    }

    _reloadNow() {
      try { sessionStorage.removeItem("membrane.reload.tried"); } catch (e) { /* none */ }
      location.reload();
    }

    // ---------------------------------------------------------- live stream
    async _openStream() {
      this._closeStream();
      const ctl = new AbortController();
      this._streamCtl = ctl;
      let backoff = 1000;
      while (!ctl.signal.aborted && this.isConnected) {
        if (!this._connSince) this._connSince = Date.now();     // connecting since (cleared once live)
        this._paintStatus();
        try {
          const r = await this._hass.fetchWithAuth(SERVE + "/event", { signal: ctl.signal, headers: { Accept: "text/event-stream" } });
          if (!r.ok || !r.body) throw new Error("stream " + r.status);
          this._live = true;
          this._everLive = true;
          this._connSince = null;
          this._stopPoll();
          this._paintStatus();
          await this._resync();                  // whatever happened while we were away
          this._checkBuild();                    // #243: back on new page code? reload into it
          backoff = 1000;
          const reader = r.body.getReader();
          const dec = new TextDecoder();
          const st = sseState();
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            for (const ev of sseFeed(st, dec.decode(value, { stream: true }))) this._onEvent(ev);
          }
        } catch (e) {
          if (ctl.signal.aborted) return;
        }
        this._live = false;
        if (!this._connSince) this._connSince = Date.now();     // dropped: "connecting…" first, not "offline"
        this._paintStatus();
        if (this._busy()) this._startPoll();
        await new Promise((res) => setTimeout(res, backoff));
        // capped at 5 s (#243): a booting seat waits 8 s for an open tab to
        // reconnect before it opens a new one -- the retry must land inside that
        backoff = Math.min(backoff * 2, 5000);
      }
    }

    _announceSpeech(ev, p) {
      // SENTENCE BY SENTENCE AS IT STREAMS (2026-10-10, the operator: "voice
      // text messages as they appear"): a text part's finished sentences are
      // said while the rest is still coming; the remainder when it closes.
      // A part whose message isn't known yet (streaming sends parts first) is
      // Argus's unless it carries a person's speaker line.
      this._spokenParts = this._spokenParts || new Set();
      // its own buffer (2026-10-11): it shared the name this._live with the
      // stream's live flag -- `true[partId] = ...` threw in strict mode while
      // the stream was up, so every one of Argus's parts died unannounced
      // and only membrane's lines (another path) were spoken
      this._speechParts = this._speechParts || {};
      const emit = (kind, id, text) => {
        const t = speakText(text);
        if (!t || this._spokenParts.has(id)) return;
        this._spokenParts.add(id);
        if (this._spokenParts.size > 800) this._spokenParts = new Set([...this._spokenParts].slice(-400));
        this.dispatchEvent(new CustomEvent("ai-speak", { detail: { kind, id, text: t }, bubbles: true, composed: true }));
      };
      const roleOf = (mid) => { const r = this._chat.byId && this._chat.byId[mid]; return r && r.info && r.info.role; };
      const isArgus = (mid, text) => {
        const role = roleOf(mid);
        if (role) return role === "assistant";
        return !SPEAKER_RE.test(String(text || "")) && !/^\s*\[membrane voice\]/i.test(text || "");
      };
      const flush = (st, partId, upto) => {                // say whole sentences up to `upto` (exclusive)
        const chunk = st.buf.slice(st.said, upto);
        if ((chunk.match(/```/g) || []).length % 2) return; // inside a code block: wait for its end
        if (chunk.trim()) emit("argus", partId + ":" + st.said, chunk);
        st.said = upto;
      };
      if (ev.type === "message.part.updated") {
        const part = p.part || {};
        if (part.type) this._speechParts[part.id] = Object.assign(this._speechParts[part.id] || { buf: "", said: 0 }, { type: part.type, mid: part.messageID });
        if (part.type !== "text" || part.synthetic) return;
        if (!(part.time || {}).end) return;
        const st = this._speechParts[part.id] || { buf: "", said: 0 };
        delete this._speechParts[part.id];
        if (/^\s*\[membrane voice\]/i.test(part.text || "")) {
          if (roleOf(part.messageID) !== "assistant") {
            const line = voiceTurnText(part.text);
            emit("membrane", "mv:" + speakText(line), line);
          }
          return;
        }
        if (!isArgus(part.messageID, part.text)) return;
        const full = String(part.text || "");
        st.buf = full.length >= st.buf.length ? full : st.buf;
        if (st.said < st.buf.length) { const rest = st.buf.slice(st.said); if (rest.trim()) emit("argus", part.id + ":" + st.said, rest); }
      } else if (ev.type === "message.part.delta") {
        if (p.field && p.field !== "text") return;
        const st = this._speechParts[p.partID] || (this._speechParts[p.partID] = { buf: "", said: 0, mid: p.messageID });
        if (st.type && st.type !== "text") return;         // reasoning streams the same way: never said
        st.buf += String(p.delta || "");
        if (!isArgus(st.mid || p.messageID, st.buf)) return;
        const re = /[.!?…](?=\s|$)|\n/g;
        let m, upto = -1;
        re.lastIndex = st.said;
        while ((m = re.exec(st.buf))) upto = m.index + m[0].length;
        if (upto > st.said && st.buf.slice(st.said, upto).split(/\s+/).filter(Boolean).length >= 3) flush(st, p.partID, upto);
      } else if (ev.type === "membrane.voice") {
        const line = p.line || {};
        if (line.id && line.text) emit("membrane", "mv:" + speakText(line.text), line.text);
      }
    }

    _closeStream() {
      if (this._streamCtl) { this._streamCtl.abort(); this._streamCtl = null; }
      this._live = false;
    }

    _onEvent(ev) {
      const p = ev.properties || {};
      const sid = p.sessionID || (p.info && p.info.sessionID) || (p.part && p.part.sessionID);
      if (ev.type === "session.status" && sid) {
        this._statusBy[sid] = p.status || { type: "idle" };
        this._paintSessions();
      } else if (ev.type === "session.idle" && sid) {
        this._statusBy[sid] = { type: "idle" };
        this._paintSessions();
      } else if ((ev.type === "session.updated" || ev.type === "session.deleted") && sid) {
        clearTimeout(this._sesTimer);
        this._sesTimer = setTimeout(() => this._listSessions().catch(() => {}), 400);
      }
      if (ev.type === "membrane.seat") this._loadSeat();          // the seat's own events: its state moved
      if (ev.type === "self.cycle" && this._seat) {               // #161: the bar moves with each cycle
        const s = Object.assign({}, this._seat.self || {});
        const moved = ("rung" in p && p.rung !== s.rung) || ("limit" in p && p.limit !== s.limit)
          || ("awaiting" in p && JSON.stringify(p.awaiting) !== JSON.stringify(s.awaiting));     // #299: an accept is news too
        for (const k of ["rung", "rung_name", "bar", "skill", "mode", "slipping", "waiting_on", "next", "cycles", "held", "limit", "awaiting"]) if (k in p) s[k] = p[k];
        s.born = true;
        if (moved) this._loadSeat().catch(() => {});                 // a rung opened: the ladder's marks refresh
        this._seat = Object.assign({}, this._seat, { self: s });
        this._paintSeat();
      }
      // #331: a cycle is when self takes its turn with the ball -- membrane home
      // checks the toys at once (even with its tab hidden) instead of on its next poll
      if (ev.type === "self.cycle") this.dispatchEvent(new CustomEvent("ai-self-cycle", { detail: { n: p.n }, bubbles: true, composed: true }));
      if (ev.type === "membrane.name") this._applySeatName(p.name);   // issue #6: the name was adopted
      if (ev.type === "membrane.jsexec") this._jsRun(p.job).catch(() => {});
      if (ev.type === "membrane.listen" && sid === (this._chat && this._chat.sid)) {
        // Argus opens (or closes) the mic for an answer (voice_io.on_listen);
        // pause = the ear holds while he speaks, resume comes as seconds again
        this.dispatchEvent(new CustomEvent("ai-listen", { detail: { seconds: Number(p.seconds) || 0, pause: !!p.pause }, bubbles: true, composed: true }));
      }
      if (ev.type === "membrane.stream" && sid === (this._chat && this._chat.sid)) {
        // the person's speech streams in (Phase 2): "more is coming" until final
        this.dispatchEvent(new CustomEvent("ai-stream", { detail: { text: p.text || "", final: !!p.final, stream: p.stream || "" }, bubbles: true, composed: true }));
      }
      if (ev.type === "membrane.say" && sid === (this._chat && this._chat.sid) && this._repliesArmed) {
        const ln = p.line || {};
        if (ln.text) this.dispatchEvent(new CustomEvent("ai-say", { detail: { text: ln.text }, bubbles: true, composed: true }));
        if (this._vlogOpen) this._paintVoiceLog();
      }
      if (this._chat && applyEvent(this._chat, ev)) {
        this._schedule();
        this._paintStatus();
        if (ev.type === "membrane.auto") this._paintAuto();
      }
      if (this._chat && sid === this._chat.sid && (ev.type === "session.idle" ||
          (ev.type === "session.status" && (p.status || {}).type === "idle"))) {
        this.dispatchEvent(new CustomEvent("ai-idle", { bubbles: true, composed: true }));
      }
      // SPEECH AS IT COMES (2026-10-10, the operator: "why does it voice
      // messages MUCH after they appear?"): each of Argus's text parts the
      // moment it closes, and each of membrane's own lines, once
      if (this._chat && sid === this._chat.sid && this._repliesArmed) this._announceSpeech(ev, p);
      // a finished reply, announced once (membrane home speaks it)
      if (this._chat && sid === this._chat.sid && (ev.type === "session.idle" || ev.type === "message.updated")) {
        const r = replyToSpeak(this._chat.rows);
        if (r && r.id !== this._spokenId) {
          this._spokenId = r.id;
          if (this._repliesArmed) this.dispatchEvent(new CustomEvent("ai-reply", { detail: r, bubbles: true, composed: true }));
        }
      }
    }

    // ---------------------------------------------------------- fallback poll
    _startPoll() {
      if (this._pollTimer || this._live) return;
      this._pollStarted = Date.now();
      this._pollTimer = setInterval(() => this._pollOnce(), POLL_MS);
    }

    _stopPoll() {
      if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
    }

    async _pollOnce() {
      if (this._polling || this._live) { if (this._live) this._stopPoll(); return; }
      this._polling = true;
      try {
        await this._resync();
        const grace = Date.now() - (this._pollStarted || 0) < START_GRACE_MS;
        if (!this._busy() && !grace) this._stopPoll();
      } catch (e) { /* transient: keep polling */ } finally { this._polling = false; }
    }

    // ---------------------------------------------------------- reconcile (#238)
    // While the page believes its chat is busy it asks the seat every
    // RECONCILE_MS and adopts the answer: a missed idle (a dropped frame, a
    // path that never said it) no longer leaves "working" up forever. Only
    // while busy; one request at a time; the fallback poll already resyncs.
    _syncReconcile() {
      if (this._busy() && !this._mind) {
        if (!this._reconcileTimer) this._reconcileTimer = setInterval(() => this._reconcileOnce(), RECONCILE_MS);
      } else this._stopReconcile();
    }

    _stopReconcile() {
      if (this._reconcileTimer) { clearInterval(this._reconcileTimer); this._reconcileTimer = null; }
    }

    async _reconcileOnce() {
      if (this._reconciling || this._pollTimer || !this._busy() || !this._chat) return;
      this._reconciling = true;
      try {
        const sid = this._sid;
        const r = await this._api(SERVE + "/session/status");
        if (!r.ok || !r.data || typeof r.data !== "object" || sid !== this._sid || !this._busy()) return;
        this._statusBy = r.data;
        const next = reconcileStatus(this._chat.status, r.data[sid], Date.now());
        if (next === this._chat.status) return;
        if (next.type === "idle") {
          await this._resync();                  // the idle's frames went missing: the rows may have too
          if (!this._busy()) this.dispatchEvent(new CustomEvent("ai-idle", { bubbles: true, composed: true }));
        } else {
          this._chat.status = next;
          this._paintStatus();
        }
        this._paintSessions();
      } catch (e) { /* transient: the next tick asks again */ } finally { this._reconciling = false; }
    }

    // ---------------------------------------------------------- turns
    _busy() { return !!(this._chat && this._chat.status && this._chat.status.type === "busy"); }

    /** VOICE IN (voice_io.py): a spoken phrase starts the turn like a message but
     *  lives in the voice log, not the chat (🎙 tells Argus it was spoken).
     *  CHUNKED STT (Phase 2): opts.stream + opts.final stream the person's speech
     *  -- interim (final:false) coalesces; final (final:true) starts the turn. */
    async sendVoice(text, opts = {}) {
      text = String(text || "").trim();
      if (!text || !this._sid) return;
      this.dispatchEvent(new CustomEvent("ai-sent", { bubbles: true, composed: true }));
      if (!this._busy()) { this._chat.status = { type: "busy", since: Date.now() }; this._paintStatus(); this._schedule(); }
      const body = { agent: this._agent, voice: true, parts: [{ type: "text", text: "🎙 " + text }] };
      if (opts.stream) { body.stream = opts.stream; body.final = !!opts.final; }
      const r = await this._api(SERVE + "/session/" + this._sid + "/prompt_async", {
        method: "POST", body: JSON.stringify(body),
      });
      if (!r.ok) this._err(this._apiErr("voice send failed", r));
      if (!this._live) { await this._resync(); this._startPoll(); }
    }

    async _submit() {
      const text = (this._ta.value || "").trim();
      if (!text || !this._sid) return;
      if (await this._slash(text)) { this._ta.value = ""; this._grow(); return; }
      this._err("");
      this._ta.value = "";
      this._grow();
      this._stick = true;
      // a message sent: whatever is being read aloud stops (membrane home)
      this.dispatchEvent(new CustomEvent("ai-sent", { bubbles: true, composed: true }));
      if (!this._busy()) {
        this._chat.status = { type: "busy", since: Date.now() };
        this._paintStatus();
        this._schedule();
      }
      try {
        const r = await this._api(SERVE + "/session/" + this._sid + "/prompt_async", {
          method: "POST", body: JSON.stringify(Object.assign({ agent: this._agent, parts: [{ type: "text", text }] },
            (() => { const mf = modelField(this._pick, this._sessionModel, this._seatModel());
                     if (mf) this._sessionModel = mf.modelID; return mf ? { model: mf } : {}; })())),
        });
        if (!r.ok) throw new Error(this._apiErr("send failed", r));
      } catch (e) {
        this._err(e.message || String(e));
        this._ta.value = text;
        this._grow();
        await this._resync();
        return;
      }
      this._autoTitle(text).catch(() => {});
      if (!this._live) { await this._resync(); this._chat.status = { type: "busy", since: Date.now() }; this._startPoll(); }
    }

    async _slash(text) {
      const m = text.match(/^\/(\w+)\s*([\s\S]*)$/);
      if (!m) return false;
      const cmd = m[1], arg = m[2];
      if (cmd === "new") { await this._newChat(); return true; }
      if (cmd === "build" || cmd === "plan") { this._agent = cmd; this._agentSel.value = cmd; return true; }
      if (cmd === "rename" && arg.trim()) {
        await this._api(SERVE + "/session/" + this._sid, { method: "PATCH", body: JSON.stringify({ title: arg.trim() }) });
        await this._listSessions();
        return true;
      }
      return false;                              // anything else goes to the brain verbatim
    }

    async _abort() {
      if (!this._sid) return;
      try { await this._api(SERVE + "/session/" + this._sid + "/abort", { method: "POST", body: "{}" }); } catch (e) {}
      await this._resync();
    }

    // ---------------------------------------------------------- painting
    _paintStatus() {
      if (!this._pill) return;
      const busy = this._busy();
      this._sendBtn.textContent = busy ? "Queue" : "Send";
      this._sendBtn.title = busy ? "The brain is working: this message waits in its queue" : "Send (Enter)";
      this._stopBtn.style.display = busy ? "" : "none";
      let txt, cls = "pill state";
      if (busy) {
        const st = this._chat.status;
        txt = "working " + fmtElapsed(Date.now() - (st.since || Date.now()));
        if (st.depth) txt += " · " + st.depth + " queued";
        cls += " busy";
      } else txt = "idle";
      const conn = connLabel(this._live, this._everLive, this._connSince, Date.now(), !!this._pollTimer);
      if (conn) { txt += " · " + conn; if (conn === "offline") cls += " off"; }
      this._pill.className = cls;
      this._pill.textContent = txt;
      this._pill.title = this._live ? "live (streaming)" : "not streaming: polling fallback";
      const w = this._msgs.querySelector(".working .lbl");
      if (w && busy) w.textContent = "working… " + fmtElapsed(Date.now() - (this._chat.status.since || Date.now()));
      this._syncReconcile();
    }

    _schedule() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = 0; this._paint(); });
    }

    _paint() {
      const st = this._chat;
      if (!st) return;
      const stick = this._stick;
      // the overlay keeps only the newest OVERLAY_ROWS in the DOM: an endless
      // auto-chat grew the transcript without bound (2026-10-10)
      const cap = this.hasAttribute("overlay") ? OVERLAY_ROWS : 0;
      if (st.all || !this._rowEls) {
        this._msgs.innerHTML = "";
        this._rowEls = {};
        for (const row of cap ? st.rows.slice(-cap) : st.rows) { const el = this._rowEl(row); this._msgs.appendChild(el); this._rowEls[row.info.id] = el; }
        st.all = false;
        st.dirty.clear();
      } else {
        for (const mid of st.dirty) {
          const row = st.byId[mid];
          if (!row) continue;
          const el = this._rowEl(row);
          const old = this._rowEls[mid];
          if (old && old.parentNode) { old.replaceWith(el); this._rowEls[mid] = el; }
          else this._insertRow(row, el);
        }
        st.dirty.clear();
      }
      if (cap) {
        let rows = this._msgs.querySelectorAll(":scope > .msg");
        for (let i = 0; i < rows.length - cap; i++) {
          const el = rows[i];
          for (const k in this._rowEls) if (this._rowEls[k] === el) { delete this._rowEls[k]; break; }
          el.remove();
        }
      }
      const tail = this._msgs.querySelector(".working, .empty");
      if (tail) tail.remove();
      if (!st.rows.length && !this._busy()) {
        const e = document.createElement("div");
        e.className = "empty";
        e.textContent = "New conversation with the brain.";
        this._msgs.appendChild(e);
      }
      if (this._busy()) {
        const last = st.rows[st.rows.length - 1];
        const streaming = last && last.info.role === "assistant" && !(last.info.time || {}).completed &&
          last.parts.some((p) => (p.text || "").length || p.type === "tool");
        if (!streaming) {
          const w = document.createElement("div");
          w.className = "working";
          w.innerHTML = '<span class="spin"></span><span class="lbl">working…</span>';
          this._msgs.appendChild(w);
        }
      }
      if (stick) this._toBottom();
    }

    _insertRow(row, el) {
      const st = this._chat;
      const idx = st.rows.indexOf(row);
      const next = st.rows.slice(idx + 1).map((r) => this._rowEls[r.info.id]).find((x) => x && x.parentNode);
      if (next) this._msgs.insertBefore(el, next);
      else this._msgs.insertBefore(el, this._msgs.querySelector(".working, .empty"));
      this._rowEls[row.info.id] = el;
    }

    _rowEl(row) {
      const info = row.info || {};
      const el = document.createElement("div");
      if (info.voiceIn) { el.className = "msg vin-hidden"; el.style.display = "none"; return el; }   // the voice log has it
      if (info.role === "user") {
        const text = row.parts.filter((p) => p.type === "text" && !p.synthetic).map((p) => p.text || "").join("\n");
        const line = this._voiceOn && this._chat && (this._chat.voice || {})[info.id];
        if (info.voiceTurn) {
          // auto-chat: the voice's own turn -- drawn as the voice, not as you
          el.className = "msg";
          el.innerHTML = line ? voiceHtml(line, this._expanded.has("vox:" + line.id))
                              : '<div class="vox"><div class="vwho">membrane</div><div class="vtext">' + escText(voiceTurnText(text)) + "</div></div>";
          return el;
        }
        // a person's message carries the speaker line ('[HA user: X]' / '[user: X]'); the loops' don't
        const person = row.parts.some((p) => p.type === "text" && /^\s*\[(?:HA )?user: /.test(p.text || ""));
        if (!line) {
          el.className = "msg user" + (person ? "" : " machine");
          el.innerHTML = '<div class="bub md">' + md(stripSpeaker(text)) + "</div>";
          return el;
        }
        // the user's message, then membrane's own line answering it
        el.innerHTML = '<div class="msg user"><div class="bub md">' + md(stripSpeaker(text)) + "</div></div>" +
          voiceHtml(line, this._expanded.has("vox:" + line.id));
        el.dataset.mid = info.id;
        return el;
      }
      const live = this._busy() && !(info.time || {}).completed;
      el.className = "msg ai" + (live ? " live" : "");
      // a line self pushed (#208, "from": "self") is named as self's, not the seat's agent
      const who = info.from === "self" ? "🌱 " + SELF_NAME
        : [info.agent || info.mode, info.modelID].filter(Boolean).join(" · ");
      let html = who ? '<div class="who">' + escText(who) + "</div>" : "";
      // membrane's guess of each move (predict.py), greyed, right before the move it guessed
      const ph = (this._chat && (this._chat.phantoms || {})[info.id]) || null;
      let k = 0;
      for (const p of row.parts) {
        if (!p || typeof p !== "object") continue;
        if (ph && isMove(p)) { html += phantomHtml(ph[k]); k++; }
        if (p.type === "text") {
          if (p.text) html += '<div class="md">' + md(p.text) + "</div>";
        } else if (p.type === "reasoning") {
          if (!p.text) continue;
          const key = "think:" + p.id;
          const t = p.time || {};
          const label = t.end ? "Thought" + (t.start ? " for " + fmtElapsed(t.end - t.start) : "") : "Thinking…";
          html += '<details class="think" data-key="' + escAttr(key) + '"' + (this._expanded.has(key) ? " open" : "") +
            "><summary>💭 " + escText(label) + '</summary><div class="md">' + md(p.text) + "</div></details>";
        } else if (p.type === "tool") {
          html += this._toolHtml(p);
        } else if (p.type === "file") {
          html += '<div class="usage">📎 ' + escText(p.filename || p.url || "file") + "</div>";
        }
      }
      if (ph) for (const n of Object.keys(ph).map(Number).filter((n) => n >= k).sort((a, b) => a - b)) html += phantomHtml(ph[n]);
      const usage = fmtUsage(info);
      if (usage && (info.time || {}).completed) html += '<div class="usage">' + escText(usage) + "</div>";
      if (info.error) html += '<div class="err">' + escText(pretty(info.error.data || info.error)) + "</div>";
      el.innerHTML = html || '<div class="usage">…</div>';
      return el;
    }

    _toolHtml(p) {
      const v = toolView(p);
      const key = "tool:" + (p.callID || p.id);
      // the 'membrane' card is the turn's injection (what membrane added
      // to the model's input), not a tool the model called
      const inj = v.name === "membrane";
      const icon = inj ? "⟐" : ({ completed: "✓", error: "✕", refused: "⛔", running: "⋯", pending: "⋯" }[v.status] || "•");
      const badge = inj ? "injected" : (v.status === "refused" ? "lane refused" : v.status);
      let out = v.output;
      let more = "";
      if (out.length > OUTPUT_CLIP && !this._expanded.has(key + ":full")) {
        more = '<button class="more" data-full="' + escAttr(key) + '">show all ' + out.length + " chars</button>";
        out = out.slice(0, OUTPUT_CLIP) + "\n…";
      }
      return '<details class="tool st-' + escAttr(v.status) + '" data-key="' + escAttr(key) + '"' +
        (this._expanded.has(key) ? " open" : "") + "><summary>" + icon + ' <span class="nm">' + escText(v.name) +
        '</span><span class="ti">' + escText(v.title) + '</span><span class="badge">' + escText(badge) + "</span>" +
        (v.ms != null ? '<span class="ms">' + fmtElapsed(v.ms) + "</span>" : "") + '</summary><div class="io">' +
        (v.input ? '<div class="lbl">input</div><pre>' + escText(v.input) + "</pre>" : "") +
        (out ? '<div class="lbl">output</div><pre>' + escText(out) + "</pre>" + more : "") + "</div></details>";
    }

    async _toggleAuto(mode) {
      // the person's chat, even while a mind's read-only view is shown (#318)
      const sid = autoSid(this._sid, this._mind ? this._mindPrev : null);
      if (!sid || !this._chat) return;
      let cur = sid === this._sid ? this._chat.auto : null;
      if (sid !== this._sid) {
        const g = await this._api(SERVE + "/session/" + sid + "/auto").catch(() => null);
        cur = g && g.ok ? g.data : null;
      }
      const on = !(cur && cur.on);
      const r = await this._api(SERVE + "/session/" + sid + "/auto",
        { method: "POST", body: JSON.stringify({ on, mode: mode || "explore" }) });
      if (r.ok && r.data) { this._chat.auto = r.data; this._paintAuto(); }
    }

    /** THE SEAT'S JS (jsexec.py): opt in per tab; Argus's code then runs in this
     *  window, as you -- the switch is the consent. */
    async _toggleJs() {
      this._jsOn = !this._jsOn;
      this._paintJs();
      clearInterval(this._jsBeat);
      await this._jsRegister();
      if (this._jsOn) this._jsBeat = setInterval(() => this._jsRegister().catch(() => {}), 20000);

    }

    async _jsRegister() {
      if (!this._sid) return;
      await this._api(SERVE + "/membrane/jsexec/register", { method: "POST", body: JSON.stringify({
        tab: this._jsTab, sid: this._sid, on: !!this._jsOn,
        device: (navigator.maxTouchPoints > 0 ? "phone/tablet" : "computer") + " · " + (navigator.platform || "") }) });
    }

    /** THE LIVE NAME (issue #6): rename and the page reads it -- a fresh
     *  boot out of /ui/conf, an open page from the hub's membrane.name. */
    _applySeatName(name) {
      name = String(name || "").trim();
      if (!name || name === SEAT) return;
      window.MEMBRANE_NAME = name;
      SEAT = name;
      this._paintJs();
    }

    _paintJs() {
      if (!this._jsBtn) return;
      const lbl = this._jsBtn.querySelector(".lbl");
      if (lbl) lbl.textContent = this._jsOn ? "JS scripting: ON" : "JS scripting: off";
      this._jsBtn.style.opacity = this._jsOn ? "1" : ".5";
      this._jsBtn.title = this._jsOn ? "JS scripting is on in this window: the page runs it with your HA login -- click to stop"
                                     : "allow JS scripting in this window: the page runs it with your HA login -- off by default";
    }

    /** Run a job IN THIS WINDOW (the operator's choice, 2026-10-11: "why a sandbox?
     *  why not let it access the window?"): an async function with window, the HA
     *  connection (hass) and this chat panel in reach; its console kept; a job
     *  still waiting past its timeout is answered as a timeout. */
    async _jsRun(job) {
      if (!this._jsOn || !job || job.tab !== this._jsTab) return;
      const ms = Math.max(500, Math.min(60000, (job.timeout || 15) * 1000));
      const logs = [];
      const con = {};
      for (const k of ["log", "info", "warn", "error"]) con[k] = (...a) => { logs.push((k === "log" ? "" : k + ": ") + a.map(jsShow).join(" ")); console[k](...a); };
      let out;
      try {
        // the final expression's value, or the code's own return (issue #153)
        const fn = jsCompile(job.code, ["console", "hass", "panel", "home"]);
        const home = document.querySelector("membrane-home-panel") || (this.getRootNode && this.getRootNode().host) || null;
        const v = await Promise.race([fn(con, this._hass, this, home),
                                      new Promise((_, rej) => setTimeout(() => rej(new Error("timeout after " + ms + " ms")), ms))]);
        out = { id: job.id, ok: true, value: v === undefined ? undefined : jsShow(v).slice(0, 4000), logs: logs.slice(0, 200) };
      } catch (err) {
        out = { id: job.id, ok: false, error: String((err && err.stack) || err).slice(0, 2000), logs: logs.slice(0, 200) };
      }
      await this._api(SERVE + "/membrane/jsexec/result", { method: "POST", body: JSON.stringify(out) }).catch(() => {});
    }

    async _toggleVoiceLog() {
      this._vlogOpen = !this._vlogOpen;
      this._vlogEl.hidden = !this._vlogOpen;
      this._vlogBtn.style.opacity = this._vlogOpen ? "1" : ".6";
      if (!this._vlogOpen) return;
      const r = await this._api(SERVE + "/membrane/voice", { method: "POST", body: "{}" }).catch(() => null);
      this._vlogSeat = r && r.ok && r.data ? r.data : null;
      this._paintVoiceLog();
    }

    _paintVoiceLog() {
      if (!this._vlogEl || !this._chat) return;
      const seat = this._vlogSeat || {};
      const items = voiceLogItems(this._chat.rows, (this._chat.said || []).concat(((seat.out || {}).log || []).map((x) => Object.assign({ who: "argus" }, x))),
                                  Object.values(this._chat.voice || {}));
      this._vlogEl.innerHTML = '<div class="vlhead">voice log' + (seat.in ? ' · mic ' + escText(seat.in.mode || "off") + ' · 🔊 ' + (seat.out && seat.out.on ? "on" : "off") : "") +
        "</div>" + (items.length ? items.map((x) => '<div class="vli ' + x.who + '"><span class="vlwho">' + escText(x.label) + "</span> " + escText(x.text) + "</div>").join("")
                                 : '<div class="vli">nothing said aloud yet</div>');
    }

    /** THE SEAT'S ONE PAUSE (seat_panel.py): every acting lane stands down; HA admins only. */
    async _togglePause() {
      const paused = !(this._seat && this._seat.paused);
      const r = await this._api(SERVE + "/membrane/seat/control", { method: "POST", body: JSON.stringify({ paused }) });
      if (r.ok && r.data && r.data.state) { this._seat = r.data.state; this._paintSeat(); }
      else this._err(this._apiErr("pause failed (HA admins only)", r));
    }

    /** The 🌱 sleep toggle's face (home.js's 👶 panel paints its own from here). */
    _selfView() { return selfView(this._seat); }

    /** #299: sleep or wake -- the seat's mode, on or off (never before its state loads). */
    async _setSelfMode(on) {
      const v = selfView(this._seat);
      if (!v.loaded || !!on === v.on) return { ok: false, skipped: true };   // nothing before the state loads, nothing for no change
      if (this._selfSleep) this._selfSleep.disabled = true;           // in flight
      const r = await this._api(SERVE + "/membrane/seat/control", { method: "POST", body: JSON.stringify({ self: on ? "on" : "off" }) });
      let out;
      if (r.ok && r.data && r.data.state) {
        this._seat = r.data.state;
        const got = selfView(this._seat);                          // the seat's answer, not what was clicked
        out = { ok: true, on: got.on, text: got.on ? "✓ " + SELF_NAME + " is awake" : "✓ " + SELF_NAME + " is asleep (nothing erased)" };
      } else {
        out = { ok: false, text: this._apiErr("switching " + SELF_NAME + " failed (HA admins only)", r) };
        this._err(out.text);
      }
      this._flashSelf(out.text);
      return out;
    }

    /** #299: accept an achievement self earned -- it opens what it leads to.
     *  -> {ok, id, name, already} or {ok: false, error} (the seat's own words). */
    async _acceptSelf(id) {
      const r = await this._api(SERVE + "/membrane/seat/control", { method: "POST", body: JSON.stringify({ accept: id }) }).catch(() => null);
      const got = r && r.ok && r.data && r.data.applied && r.data.applied.accept;
      if (r && r.ok && r.data && r.data.state) { this._seat = r.data.state; this._paintSeat(); }
      if (got && typeof got === "object") return got;
      return { ok: false, error: r ? this._apiErr("accepting failed (HA admins only)", r) : "the seat is not reachable" };
    }

    _flashSelf(text) {
      this._selfFlash = text;
      if (this._selfStat) this._selfStat.textContent = text;
      clearTimeout(this._selfFlashT);
      this._selfFlashT = setTimeout(() => { this._selfFlash = null; this._paintSeat(); }, 4000);
      this._paintSeat();
    }

    async _loadSeat() {
      const r = await this._api(SERVE + "/membrane/seat").catch(() => null);
      if (r && r.ok && r.data && r.data.state) { this._seat = r.data.state; this._paintSeat(); }
    }

    _paintSeat() {
      if (!this._pauseBtn) return;
      const v = seatView(this._seat);
      const ico = this._pauseBtn.querySelector(".ico"), lbl = this._pauseBtn.querySelector(".lbl");
      if (ico) ico.textContent = v.icon; else this._pauseBtn.textContent = v.icon;
      if (lbl) lbl.textContent = v.label;
      this._pauseBtn.title = v.title;
      this._pauseBtn.style.opacity = v.paused ? "1" : ".6";
      if (this._selfSleep) {                                       // #299: the sleep toggle
        const v = selfView(this._seat);
        const sico = this._selfSleep.querySelector(".ico"), slbl = this._selfSleep.querySelector(".lbl");
        if (sico) sico.textContent = v.icon; else this._selfSleep.textContent = v.icon;
        if (slbl) slbl.textContent = v.label;
        this._selfSleep.title = v.title;
        this._selfSleep.setAttribute("aria-pressed", v.on ? "true" : "false");
        this._selfSleep.setAttribute("aria-label", v.label + " -- tap to " + (v.on ? "put it to sleep" : "wake it"));
        this._selfSleep.disabled = !v.loaded;
      }
      if (this._selfStat && !this._selfFlash) paintExp(this._selfStat, selfExp(this._seat));   // #230: the exp bar
    }

    // #230: self in the UI -- home.js's top bar and its 👶 panel paint from
    // here (the panel owns the routes). All read-only.
    _paintExp(el) { paintExp(el, selfExp(this._seat)); }
    _selfCardHtml() { return selfCardHtml(this._seat); }
    _selfLinesHtml(lines) { return selfLinesHtml(lines); }
    _selfLine(r) { return selfLine(r); }                        // #237: the brain caption's cite
    /** #244: play with self -- the 👶 panel's one write: a toybox action, tagged
     *  by the seat with this page's person (the speaker line / the token / the HA user). */
    async _toyPoke(toy, action, arg) {
      const body = { toy, action };
      if (arg != null && arg !== "") body.arg = arg;
      const r = await this._api(SERVE + "/membrane/toybox/poke", { method: "POST", body: JSON.stringify(body) }).catch(() => null);
      if (!r) return { ok: false, error: "the seat is not reachable" };
      return r.data && typeof r.data === "object" ? r.data : { ok: false, error: "the poke failed (" + r.status + ")" };
    }

    // #249: the character sheet and the toy dock (read-only GETs; the dock's
    // play actions post through _toyPoke above)
    _selfSheetHtml(sheet, open) { return selfSheetHtml(sheet, open); }
    _skillTree(root, sheet, sel) { return renderSkillTree(document, root, sheet, sel); }   // #296: the 👶 panel's Skill tree tab
    _skillTreeSig(sheet) { return JSON.stringify(skillTree(sheet)); }
    _toyDockItem(t) { return toyDockItem(t); }
    _toyAfterPoke(toys, name, action, reading) { return toyAfterPoke(toys, name, action, reading); }   // #325
    _toyDockHtml(toys) { return toyDockHtml(toys); }
    _toyPopoverHtml(t, res) { return toyPopoverHtml(t, res); }
    _selfName() { return selfName(); }                // #277: home.js reads the seat's name for self here
    async _selfSheet() {
      const r = await this._api(SERVE + "/membrane/self/sheet").catch(() => null);
      if (r && r.ok && r.data && r.data.header) setSelfName(r.data.header.name);   // #277: the seat owns the name
      return r && r.ok && r.data && typeof r.data === "object" && r.data.header ? r.data : null;
    }
    async _toyState() {
      const r = await this._api(SERVE + "/membrane/toybox/state").catch(() => null);
      if (r && r.ok && r.data) setSelfName(r.data.name);                          // #277
      // #267: the next locked toys ride along, greyed (toyDockItem)
      return r && r.ok && r.data && Array.isArray(r.data.toys) ? r.data.toys.concat(Array.isArray(r.data.locked) ? r.data.locked : []) : null;
    }

    async _selfRecent(n) {
      const r = await this._api(SERVE + "/membrane/self/recent?n=" + encodeURIComponent(n || 15)).catch(() => null);
      return r && r.ok && r.data && Array.isArray(r.data.lines) ? r.data.lines : null;
    }

    _paintAuto() {
      const a = (this._chat && this._chat.auto) || { on: false };
      if (!this._autoBtn) return;
      this._autoBtn.style.opacity = a.on ? "1" : ".4";
      this._autoBtn.classList.toggle("spinning", !!a.on);
      this._autoBtn.title = a.on ? "auto-chat ON: the LLM works on by itself (round " + (a.rounds || 0) + ") -- click or Stop to end"
                                 : "auto-chat: the LLM works on by itself, round after round; you can join any time";
    }

    async _voiceTeach(vid, body) {
      const r = await this._api(SERVE + "/session/" + this._sid + "/voice",
        { method: "POST", body: JSON.stringify(Object.assign({ id: vid }, body)) });
      if (r.ok && r.data && r.data.mid && this._chat) {
        this._chat.voice[r.data.mid] = Object.assign({}, this._chat.voice[r.data.mid] || {}, r.data);
        this._chat.dirty.add(r.data.mid);
        this._schedule();
      }
    }

    _onMsgClick(ev) {
      const vox = ev.target.closest(".vox");
      if (vox) {
        const vid = vox.dataset.vid;
        const vote = ev.target.closest("[data-vvote]");
        if (vote) { this._voiceTeach(vid, { vote: vote.dataset.vvote }).catch(() => {}); return; }
        if (ev.target.closest("[data-vedit]")) {
          const k = "vox:" + vid;
          if (this._expanded.has(k)) this._expanded.delete(k); else this._expanded.add(k);
          this._chat.all = true; this._schedule(); return;
        }
        if (ev.target.closest(".vteach")) {
          const ta = vox.querySelector(".vedit textarea");
          const t = ta && ta.value.trim();
          if (t) { this._expanded.delete("vox:" + vid); this._voiceTeach(vid, { rewrite: t }).catch(() => {}); }
          return;
        }
      }
      const copy = ev.target.closest(".copy");
      if (copy) {
        const code = copy.closest("pre").querySelector("code");
        if (code && navigator.clipboard) {
          navigator.clipboard.writeText(code.textContent).then(() => {
            copy.textContent = "copied";
            setTimeout(() => { copy.textContent = "copy"; }, 1200);
          }).catch(() => {});
        }
        return;
      }
      const more = ev.target.closest(".more");
      if (more) {
        this._expanded.add(more.dataset.full + ":full");
        this._chat.all = true;
        this._schedule();
      }
    }

    _toBottom() {
      this._scroller.scrollTop = this._scroller.scrollHeight;
      this._jump.classList.remove("show");
    }
  }
  customElements.define("ai-terminal-panel", AiTerminalPanel);
}

if (typeof window !== "undefined" && typeof history !== "undefined" && window.location && !window.__membraneLanding) {
  window.__membraneLanding = true;
  const chosen = () => { try { return localStorage.getItem("membrane.home.left") === "1"; } catch (e) { return false; } };
  if (membraneLanding(location.pathname, chosen())) {
    // HA's router may still be settling its own '/' -> default-dashboard hop
    const go = () => {
      if (!membraneLanding(location.pathname, chosen())) return;
      history.replaceState(null, "", "/membrane" + location.search);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: true } }));
    };
    go(); setTimeout(go, 400); setTimeout(go, 1500);
  }
}

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" &&
    !customElements.get("ai-terminal-panel")) {
  definePanel();
}
