/**
 * <ai-terminal-panel>: the AI dashboard -- an opencode-style web client
 * for membrane's NATIVE serve surface (the opencode-compatible REST/SSE
 * on the brain phone), THROUGH the integration's proxy view
 * (/api/ai_terminal/serve/*): HA auth per request, the serve credential
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
// THE SEAT'S NAME (standalone mode, 2026-10-12): the standalone page sets
// window.MEMBRANE_NAME from /ui/conf before this loads; HA's page is Argus
const SEAT = (typeof window !== "undefined" && window.MEMBRANE_NAME) || "Argus";
const SERVE = "/api/ai_terminal/serve";
const SPEAKER_PREFIX = "[HA user: ";
const NEW_TITLE = "New chat";
const POLL_MS = 1500;
const START_GRACE_MS = 8000;
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
  if (!t.startsWith(SPEAKER_PREFIX)) return t;
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
      st.auto = { on: !!props.on, rounds: props.rounds || 0, mode: props.mode || "explore", role: props.role || "babble" };
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

/** Membrane's role, its own switch beside auto-chat: babble (its lines go to
 *  the LLM) or predict (the LLM never hears it; it guesses each turn). */
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

function roleNext(auto) { return (auto && auto.role) === "predict" ? "babble" : "predict"; }
function roleView(auto) {
  return (auto && auto.role) === "predict"
    ? { icon: "🔮", label: "membrane predicts", title: "membrane predicts: the LLM never hears it; in auto-chat the LLM works on by itself -- click: babble" }
    : { icon: "💬", label: "membrane babbles", title: "membrane babbles: in auto-chat its lines go to the LLM -- click: predict (unheard)" };
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
      if (said.includes(SPEAKER_PREFIX)) return null;
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

const AiPanelLogic = { voiceLogItems, seatView, connLabel, phantomHtml, isMove, loadPhantoms, guessHtml, roleNext, roleView, speakText, replyToSpeak, membraneLanding, ollamaModels, modelField, md, sseState, sseFeed, stripSpeaker, chatState, applyEvent,
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
            <button class="btn icon llmbtn" title="the LLM (off: talk to membrane alone)">🤖</button>
            <button class="btn icon autobtn" title="auto-chat: membrane and the LLM talk; you can join any time">⟳</button>
            <button class="btn icon rolebtn" title="membrane's role: babble (it talks to the LLM) or predict (it guesses the LLM, unheard)">💬</button>
            <button class="btn icon jsbtn" title="let ${SEAT} run JS in this tab -- it acts as you here (the page, your HA login) -- off by default">🧩</button>
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
        for (const [cls, label] of [["agent", null], ["modelpick", null], ["voicebtn", "membrane's voice"], ["llmbtn", "LLM replies"],
                                    ["autobtn", "auto-chat"], ["rolebtn", "membrane babbles"], ["jsbtn", "let " + SEAT + " act as you here: off"], ["vlogbtn", "voice log"], ["pausebtn", "pause the seat"], ["mindsbtn", "minds"]]) {
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
      this._roleBtn = $(".rolebtn");
      this._roleBtn.addEventListener("click", () => this._toggleRole().catch(() => {}));
      this._jsBtn = $(".jsbtn");
      this._jsBtn.addEventListener("click", () => this._toggleJs().catch(() => {}));
      this._jsTab = "tab_" + Math.random().toString(36).slice(2, 10);
      this._paintJs();
      this._vlogEl = $(".vlog");
      this._vlogBtn = $(".vlogbtn");
      this._vlogBtn.addEventListener("click", () => this._toggleVoiceLog().catch(() => {}));
      this._pauseBtn = $(".pausebtn");
      this._pauseBtn.addEventListener("click", () => this._togglePause().catch(() => {}));
      this._llmBtn = $(".llmbtn");
      this._llmOn = true;                 // per page view
      this._llmBtn.addEventListener("click", () => {
        this._llmOn = !this._llmOn;
        this._llmBtn.style.opacity = this._llmOn ? "1" : ".4";
        this._llmBtn.title = this._llmOn ? "the LLM (off: talk to membrane alone)" : "LLM off: membrane answers alone";
        this._ta.placeholder = this._llmOn ? "Ask the brain…" : "Talk to membrane (no LLM)…";
        if (!this._llmOn && !this._voiceOn) this._voiceBtn.click();
      });
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
      return what + " (" + r.status + "): " + (typeof r.data === "string" ? r.data : JSON.stringify(r.data));
    }

    // ---------------------------------------------------------- boot
    async _boot() {
      this._err("");
      try {
        const conf = await this._api("/api/ai_terminal/conf");
        if (!conf.ok || !conf.data || !conf.data.serve_ok) {
          this._err("brain lane off: set ai_terminal serve_url + serve_pass in configuration.yaml");
          return;
        }
        this._foot.textContent = conf.data.serve_url;
        const health = await this._api(SERVE + "/global/health");
        if (!health.ok) { this._err(this._apiErr("brain unreachable", health)); this._paintStatus(); return; }
        this._foot.textContent = "membrane " + ((health.data && health.data.version) || "") + " · " + conf.data.serve_url;
        this._loadAgents();
        this._loadModel();
        await this._listSessions();
        const sid = this._sessions.length ? this._sessions[0].id : await this._createSession();
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
      } catch (e) { this._mindsModels = null; }
      this._paintMinds();
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
        backoff = Math.min(backoff * 2, 15000);
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
        return !String(text || "").includes(SPEAKER_PREFIX) && !/^\s*\[membrane voice\]/i.test(text || "");
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
      if (ev.type === "membrane.jsexec") this._jsRun(p.job).catch(() => {});
      if (ev.type === "membrane.listen" && sid === (this._chat && this._chat.sid)) {
        // Argus opens (or closes) the mic for an answer (voice_io.on_listen)
        this.dispatchEvent(new CustomEvent("ai-listen", { detail: { seconds: Number(p.seconds) || 0 }, bubbles: true, composed: true }));
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

    // ---------------------------------------------------------- turns
    _busy() { return !!(this._chat && this._chat.status && this._chat.status.type === "busy"); }

    /** VOICE IN (voice_io.py): a spoken phrase starts the turn like a message but
     *  lives in the voice log, not the chat (🎙 tells Argus it was spoken). */
    async sendVoice(text) {
      text = String(text || "").trim();
      if (!text || !this._sid) return;
      this.dispatchEvent(new CustomEvent("ai-sent", { bubbles: true, composed: true }));
      if (!this._busy()) { this._chat.status = { type: "busy", since: Date.now() }; this._paintStatus(); this._schedule(); }
      const r = await this._api(SERVE + "/session/" + this._sid + "/prompt_async", {
        method: "POST", body: JSON.stringify(Object.assign({ agent: this._agent, voice: true, parts: [{ type: "text", text: "🎙 " + text }] },
          this._llmOn ? {} : { membrane_only: true })),
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
            this._llmOn ? {} : { membrane_only: true },
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
        if (!line) {
          el.className = "msg user";
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
      const who = [info.agent || info.mode, info.modelID].filter(Boolean).join(" · ");
      let html = who ? '<div class="who">' + escText(who) + "</div>" : "";
      // PREDICT MODE: membrane's guess of each move, greyed, right before the move it guessed
      const ph = (this._chat && (this._chat.auto || {}).role === "predict" && (this._chat.phantoms || {})[info.id]) || null;
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
      if (!this._sid || !this._chat) return;
      const on = !(this._chat.auto && this._chat.auto.on);
      const r = await this._api(SERVE + "/session/" + this._sid + "/auto",
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

    _paintJs() {
      if (!this._jsBtn) return;
      const lbl = this._jsBtn.querySelector(".lbl");
      if (lbl) lbl.textContent = this._jsOn ? SEAT + " acts as you here: ON" : "let " + SEAT + " act as you here: off";
      this._jsBtn.style.opacity = this._jsOn ? "1" : ".5";
      this._jsBtn.title = this._jsOn ? SEAT + " runs JS in this tab and acts as you here (the page, your HA login) -- click to stop"
                                     : "let " + SEAT + " run JS in this tab -- it acts as you here (the page, your HA login) -- off by default";
    }

    /** Run a job IN THIS WINDOW (the operator's choice, 2026-10-11: "why a sandbox?
     *  why not let it access the window?"): an async function with window, the HA
     *  connection (hass) and this chat panel in reach; its console kept; a job
     *  still waiting past its timeout is answered as a timeout. */
    async _jsRun(job) {
      if (!this._jsOn || !job || job.tab !== this._jsTab) return;
      const ms = Math.max(500, Math.min(60000, (job.timeout || 15) * 1000));
      const logs = [];
      const show = (v) => { try { return typeof v === "string" ? v : JSON.stringify(v); } catch (x) { return String(v); } };
      const con = {};
      for (const k of ["log", "info", "warn", "error"]) con[k] = (...a) => { logs.push((k === "log" ? "" : k + ": ") + a.map(show).join(" ")); console[k](...a); };
      let out;
      try {
        const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
        const fn = new AsyncFunction("console", "hass", "panel", "home", String(job.code || ""));
        const home = document.querySelector("membrane-home-panel") || (this.getRootNode && this.getRootNode().host) || null;
        const v = await Promise.race([fn(con, this._hass, this, home),
                                      new Promise((_, rej) => setTimeout(() => rej(new Error("timeout after " + ms + " ms")), ms))]);
        out = { id: job.id, ok: true, value: v === undefined ? undefined : show(v).slice(0, 4000), logs: logs.slice(0, 200) };
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
    }

    async _toggleRole() {
      if (!this._sid || !this._chat) return;
      const role = roleNext(this._chat.auto);
      const r = await this._api(SERVE + "/session/" + this._sid + "/auto",
        { method: "POST", body: JSON.stringify({ role }) });
      if (r.ok && r.data) { this._chat.auto = r.data; this._paintAuto(); }
    }

    _paintAuto() {
      const a = (this._chat && this._chat.auto) || { on: false };
      if (this._roleBtn) {
        const v = roleView(a);
        const ico = this._roleBtn.querySelector(".ico"), lbl = this._roleBtn.querySelector(".lbl");
        if (ico) ico.textContent = v.icon; else this._roleBtn.textContent = v.icon;
        if (lbl) lbl.textContent = v.label;
        this._roleBtn.title = v.title;
      }
      if (!this._autoBtn) return;
      this._autoBtn.style.opacity = a.on ? "1" : ".4";
      this._autoBtn.classList.toggle("spinning", !!a.on);
      this._autoBtn.title = a.on ? "auto-chat ON: " + (a.role === "predict" ? "the LLM works on by itself, membrane predicts" : (a.mode || "explore")) + " (round " + (a.rounds || 0) + ") -- click or Stop to end"
                                 : "auto-chat: click = explore together, shift-click = teach; you can join any time";
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
