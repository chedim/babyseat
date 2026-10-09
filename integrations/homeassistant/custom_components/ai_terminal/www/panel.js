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

/** The session's voice lines (GET /session/{sid}/voice), keyed by the
 *  user message each one answered. */
function loadVoice(st, lines) {
  st.voice = {};
  for (const l of lines || []) if (l && l.mid) st.voice[l.mid] = Object.assign({}, l);
  st.all = true;
}

/** The voice's bubble: its line, its closeness to the LLM's reply, and
 *  the teaching controls (👍 / 👎 / say it better). */
function voiceHtml(line, editing) {
  if (!line || !line.text) return "";
  const c = typeof line.closeness === "number" ? '<span class="vclose" title="closeness to the LLM\'s reply">' +
    Math.round(line.closeness * 100) + "%</span>" : "";
  const up = line.vote === "up" ? " on" : "", down = line.vote === "down" ? " on" : "";
  const id = escAttr(line.id);
  let h = '<div class="vox" data-vid="' + id + '"><div class="vwho">membrane ' + c + "</div>" +
    '<div class="vtext">' + escText(line.text) + "</div>" +
    '<div class="vbar"><button class="vbtn' + up + '" data-vvote="up" title="good">👍</button>' +
    '<button class="vbtn' + down + '" data-vvote="down" title="not like that">👎</button>' +
    '<button class="vbtn" data-vedit="1" title="say it better">✎</button></div>';
  if (line.rewrite) h += '<div class="vre">you taught: ' + escText(line.rewrite) + "</div>";
  if (editing) h += '<div class="vedit"><textarea rows="2" placeholder="how membrane should have said it"></textarea>' +
    '<button class="btn primary vteach">teach</button></div>';
  return h + "</div>";
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

const AiPanelLogic = { ollamaModels, modelField, md, sseState, sseFeed, stripSpeaker, chatState, applyEvent,
                       loadRows, loadVoice, voiceHtml, toolView, shouldStick, fmtElapsed, fmtUsage };
if (typeof module !== "undefined" && module.exports) module.exports = AiPanelLogic;

// ------------------------------------------------------------ the element

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
  .vtext { font-style:italic; } .vbar { margin-top:4px; display:flex; gap:4px; }
  .vbtn { border:0; background:transparent; cursor:pointer; opacity:.45; font-size:14px; padding:0 4px; }
  .vbtn:hover, .vbtn.on { opacity:1; } .vre { font-size:12px; opacity:.7; margin-top:4px; }
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
            <button class="btn icon mindsbtn" title="membrane's minds">🧠</button>
          </div>
          <div class="minds"></div>
          <div class="err"></div>
          <div class="scroller"><div class="msgs"></div><button class="jump">↓ latest</button></div>
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

    async _resync() {
      if (!this._chat) return;
      const sid = this._sid;
      const [hist, st, vox] = await Promise.all([
        this._api(SERVE + "/session/" + sid + "/message"),
        this._api(SERVE + "/session/status"),
        this._api(SERVE + "/session/" + sid + "/voice").catch(() => ({ ok: false })),
      ]);
      if (sid !== this._sid) return;
      loadRows(this._chat, hist.ok && Array.isArray(hist.data) ? hist.data : []);
      loadVoice(this._chat, vox && vox.ok && Array.isArray(vox.data) ? vox.data : []);
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
        try {
          const r = await this._hass.fetchWithAuth(SERVE + "/event", { signal: ctl.signal, headers: { Accept: "text/event-stream" } });
          if (!r.ok || !r.body) throw new Error("stream " + r.status);
          this._live = true;
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
        this._paintStatus();
        if (this._busy()) this._startPoll();
        await new Promise((res) => setTimeout(res, backoff));
        backoff = Math.min(backoff * 2, 15000);
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
      if (this._chat && applyEvent(this._chat, ev)) {
        this._schedule();
        this._paintStatus();
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

    async _submit() {
      const text = (this._ta.value || "").trim();
      if (!text || !this._sid) return;
      if (await this._slash(text)) { this._ta.value = ""; this._grow(); return; }
      this._err("");
      this._ta.value = "";
      this._grow();
      this._stick = true;
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
      if (!this._live) { txt += this._pollTimer ? " · polling" : " · offline"; if (!this._pollTimer) cls += " off"; }
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
      if (st.all || !this._rowEls) {
        this._msgs.innerHTML = "";
        this._rowEls = {};
        for (const row of st.rows) { const el = this._rowEl(row); this._msgs.appendChild(el); this._rowEls[row.info.id] = el; }
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
      if (info.role === "user") {
        const text = row.parts.filter((p) => p.type === "text" && !p.synthetic).map((p) => p.text || "").join("\n");
        const line = this._voiceOn && this._chat && (this._chat.voice || {})[info.id];
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
      for (const p of row.parts) {
        if (!p || typeof p !== "object") continue;
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

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" &&
    !customElements.get("ai-terminal-panel")) {
  definePanel();
}
