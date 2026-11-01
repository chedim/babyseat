// MEMBRANE HOME (2026-10-10, the operator: "after login we get HA's UI
// hidden behind a button and instead we get the 3d-view of our seat/user on
// the graph with membrane chat overlayed over it, so that the graph is
// visible").
//
// The whole field in 3D (field3d.js) fills the screen, the camera following
// the signed-in user's own entity (person:<slug>, the speaker the proxy's
// '[HA user: <name>]' line makes); the chat (panel.js's element in overlay
// mode) floats over it on glass. HA's own UI is one button away: "HA" docks
// the sidebar back and goes to the dashboards; this page hides it again.

// THE SEAT'S NAME (standalone mode, 2026-10-12): the standalone page sets
// window.MEMBRANE_NAME (+ MEMBRANE_ALIASES, what speech hears for it) from
// /ui/conf before this loads; until one is handed over it is the seat's
// default name (naming.DEFAULT)
const SEAT = (typeof window !== "undefined" && window.MEMBRANE_NAME) || "Bob";
const SEAT_SAYS = [SEAT].concat((typeof window !== "undefined" && window.MEMBRANE_ALIASES) || [])
  .map((a) => String(a || "").trim().toLowerCase()).filter(Boolean);
const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const HSERVE = "/ui/serve";
const FOLLOW_EVERY_MS = 15000;
const IDLE_BEFORE_FOLLOW_MS = 30000;
// the menu button wears the babyseat (the tab's icon; #161, the operator: "replace the
// burger menu with our babyseat icon/logo") -- standalone serves it at /ui/www/, Home
// Assistant at /api/membrane/www/ (a fixed path: the tests require() this file as a script)
const SEAT_LOGO = (typeof window !== "undefined" && window.MEMBRANE_STANDALONE)
  ? "/ui/www/favicon.svg" : "/api/membrane/www/favicon.svg";
const LS_CHAT = "membrane.home.chat";
const LS_IMMERSIVE = "membrane.home.immersive";
const LS_FOLLOW = "membrane.home.follow";
const LS_TREESEL = "membrane.home.treesel";   // #394: the Skill tree's clicked skill
// what the camera follows (the operator, 2026-10-10: "the seat (the most
// interesting entity) or thinker/chat, whoever's active, to show what the seat
// is thinking/doing, or even just where stuff is changing")
const FOLLOW_MODES = ["activity", "seat", "you", "self", "eyes"];
const SEAT_IDS = ["seat:self", "seat"];
// #277: the operator named self Blurp -- the seat owns the name (the chat's
// sheet / dock reads carry it); the page says it wherever a person reads about
// self, while the ids and the follow mode value stay "self"
let SELF_NAME = "Blurp";
/** The seat's name for self; an empty one changes nothing. */
function setSelfName(n) {
  n = String(n == null ? "" : n).trim();
  if (n) SELF_NAME = n;
  return SELF_NAME;
}
const ACTIVITY_EVERY_MS = 1500;
// THE VIEW (2026-10-13, the operator: the standalone burger menu "just with
// links to chat-only and graph-only interfaces"): ?view= in the URL -- chat
// alone (no field), the field alone (no chat), or everything (?view=all).
// 0.7.2 (the operator): the decision cockpit (#470) is the DEFAULT -- no
// ?view=, an empty or an unknown one opens it, standalone and under Home
// Assistant alike.
const VIEWS = ["chat", "graph", "field"];
/** The view a ?view= value names: "all" is everything (""); a missing, empty
 * or unknown one is the cockpit. */
function knownView(v) { return VIEWS.includes(v) ? v : "field"; }
/** The menu's rows: the views, the current one marked ("" = everything). */
function standaloneMenu(current) {
  const here = current || "field";
  const rows = [{ v: "chat", icon: "💬", title: "Chat only" }, { v: "graph", icon: "🕸", title: "Graph only" },
    { v: "field", icon: "◎", title: "Field" }];
  return rows.map((r) => Object.assign(r, { current: r.v === here }));
}
// index.html sets window.MEMBRANE_VIEW standalone; Home Assistant has no
// index.html, so the URL is read here
const VIEW = typeof window !== "undefined"
  ? knownView(window.MEMBRANE_VIEW || new URLSearchParams(location.search).get("view")) : "";

/** The orbit angles that put the camera on the ray from the field's centre
 * through p (beyond it), so it looks at the centre THROUGH p -- the
 * activity in front, the field behind. null when p is the centre. */
function throughCenter(p) {
  const n = Math.hypot(p[0], p[1], p[2]);
  if (n < 40) return null;
  const d = [p[0] / n, p[1] / n, p[2] / n];
  return { phi: Math.max(0.05, Math.min(Math.PI - 0.05, Math.acos(d[1]))), theta: Math.atan2(d[0], d[2]) };
}

/** THE CHASE CAM (#161, the operator: "follow self from behind, from its past
 *  positions"): the orbit angles that put the camera behind self -- along the
 *  way back to where it was K steps ago -- and a little above; null while its
 *  trail is too short or it hasn't moved. */
function chaseAngles(trail, k = 4) {
  if (!trail || trail.length < 2) return null;
  const a = trail[Math.max(0, trail.length - 1 - k)], b = trail[trail.length - 1];
  const d = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
  const n = Math.hypot(d[0], d[1], d[2]);
  if (n < 1e-6) return null;
  const u = [-d[0] / n, -d[1] / n + 0.35, -d[2] / n];
  const m = Math.hypot(u[0], u[1], u[2]);
  return { phi: Math.max(0.05, Math.min(Math.PI - 0.05, Math.acos(u[1] / m))), theta: Math.atan2(u[0] / m, u[2] / m) };
}

/** a -> b the short way round (angles). */
function angleStep(a, b, k) {
  let d = b - a;
  while (d > Math.PI) d -= 2 * Math.PI;
  while (d < -Math.PI) d += 2 * Math.PI;
  return a + d * k;
}

// ---- #237: THE BRAIN ON THE SCREEN -- pure logic (node-tested) ---------------
// Everything here reads self's record lines and says only what a line holds.

/** membrane::Component:toy:dice -> toy:dice (as panel.js's shortEid). */
function bShort(eid) { return String(eid == null ? "" : eid).replace(/^membrane::(?:[A-Z][A-Za-z]*:)?/, ""); }

/** A thing's friendly name: the person self meets is "you" (or their name),
 *  `i` is the eye, a toy goes by its short name, the rest by their eid. */
function brainLabel(eid, me) {
  if (eid == null || eid === "") return "?";
  const s = bShort(eid);
  const m = /^chat:(?:(?:in|out):)?([^:]+)$/.exec(s);   // #333 part 3a: the chat thing, or its old chat:in / chat:out
  if (m) return me && m[1] === me ? "you" : m[1] === "operator" ? "the operator" : m[1];
  if (s === "i") return "the eye";
  const t = /^(?:toy|cradle):(.+)$/.exec(s);
  return t ? t[1] : s;
}

const BRAIN_RUNGS = { 0: "sleep and wake", 1: "feel itself", 2: "look", 3: "tell things apart", 4: "patience",
  5: "notice the world changed", 6: "meet an other", 7: "time its turn", 8: "expect a reply",
  9: "recognize itself", 10: "change itself", 11: "speak" };   // #258: the ladder flip
// #295, THE SKILL TREE: each achievement's id and the rung number its test kept on the
// record (membrane/self_loop.py ACHIEVEMENTS) -- names for its lines, and the old per-rung
// celebration memory read as ids
const ACH_RUNG = { feel: 1, reach: 2, kinds: 3, patience: 4, shift: 5, meet: 6, turns: 7, expect: 8,
  mirror: 9, change: 10, speak: 11 };
const bnum = (x) => String(Math.round(Number(x) * 100) / 100);
// #267: toys unlock as self grows (membrane/toy_unlocks.py ICONS)
const UNLOCK_ICONS = { body: "🫧", fast: "⚡", slow: "🐢", tower: "🗼", burst: "✦", die: "🎲", drift: "〰️",
                       ball: "⚽", mirror: "🪞", blocks: "🧱", rattle: "🔊" };
/** "new toy: 🪞 mirror" / "new toys: 🗼 tower, ⚽ ball" from an unlock line. */
function unlockText(r) {
  const ks = ((r && r.toys) || []).map(String);
  return (ks.length === 1 ? "new toy: " : "new toys: ") + ks.map((k) => (UNLOCK_ICONS[k] ? UNLOCK_ICONS[k] + " " : "") + k).join(", ");
}

/** One record line as a plain sentence for a stranger. Measured, never
 *  narrated: a number appears only when the line carries it. */
function brainCaption(r, me) {
  r = r || {};
  const k = r.kind;
  if (k === "reach") {
    const who = brainLabel(r.eid, me);
    const exp = r.p != null ? " (it expected " + bnum(r.p) + ")" : "";
    if (r.answer !== "changed" && r.answer !== "unchanged") return SELF_NAME + " reached for " + who + " — no answer" + (r.why ? " (" + r.why + ")" : "");
    if (r.reply) {
      const you = who === "you";
      return SELF_NAME + " checked whether " + who + " answered — " + (r.answer === "changed" ? (you ? "you did" : "they did") : "not yet") + exp;
    }
    return SELF_NAME + " looked at " + who + (r.timed ? " when it judged a change likely" : "") + " — "
      + (r.answer === "changed" ? "it changed" : "no change") + exp;
  }
  if (k === "push") {
    const who = brainLabel(r.to || r.eid, me);
    return SELF_NAME + " said hello to " + who + (r.asked ? " and asked the model if " + (who === "you" ? "you" : "they") + "'ll answer" : "");
  }
  if (k === "shift") return SELF_NAME + " noticed " + brainLabel(r.eid, me) + " stopped behaving like itself"
    + (r.was != null && r.now != null ? " (it changed " + bnum(r.was) + " of the time, now " + bnum(r.now) + ")" : "");
  if (k === "wait") return SELF_NAME + " waited — nothing was due";
  if (k === "rung") return "level up: rung " + r.rung + (BRAIN_RUNGS[r.rung] ? " · " + BRAIN_RUNGS[r.rung] : "");
  if (k === "achieve") return "level up: " + SELF_NAME + " earned " + (BRAIN_RUNGS[ACH_RUNG[r.id]] || r.id || "?");
  if (k === "cycle") return SELF_NAME + " " + (r.act === "move" || !r.act ? "moved" : r.act)
    + (r.still ? " — the world was still" : r.gap != null ? " — it landed " + Number(r.gap).toFixed(3) + " from where it expected" : "");
  if (k === "error") return SELF_NAME + " hit an error" + (r.act ? " in " + r.act : "") + (r.why ? ": " + r.why : "");
  if (k === "birth") return SELF_NAME + " was born";
  if (k === "unlock") return unlockText(r) + (r.rung ? " — " + SELF_NAME + " passed rung " + r.rung : " — " + SELF_NAME + "'s first toys");
  if (k === "audit") return SELF_NAME + " is auditing " + brainLabel(r.eid, me) + (r.phase ? " (" + r.phase + ")" : "");
  return SELF_NAME + ": " + String(k || "?");
}

/** #257: the amoeba acts out a fresh line only if it is of the last moments
 *  (within ACT_FRESH_S of the newest line): a page back in view catches up on
 *  the record without replaying the backlog on the body. */
const ACT_FRESH_S = 30;
function actableLine(r, newestTs) {
  const t = r && Number(r.ts);
  return !!(r && r.ts != null && isFinite(t) && t >= Number(newestTs) - ACT_FRESH_S);
}

/** A record line's identity (a shift line carries no n: its ts and eid do). */
function brainKey(r) { return [r.kind, r.ts, r.n == null ? "" : r.n, r.eid || r.to || ""].join("|"); }

/** The feed comes newest first: the lines not seen yet, oldest first, and the
 *  seen keys to keep (capped). */
function brainFresh(lines, seen) {
  const known = new Set(seen || []), fresh = [];
  for (const r of (lines || []).slice().reverse()) {
    const key = brainKey(r || {});
    if (known.has(key)) continue;
    known.add(key); fresh.push(r);
  }
  return { fresh, seen: [...known].slice(-300) };
}

const BRAIN_TRAIL_N = 8;
/** The trail of looks: a reach joins it, the oldest falls off past the cap. */
function brainTrail(trail, r, cap = BRAIN_TRAIL_N) {
  if (!r || r.kind !== "reach" || !r.eid) return trail || [];
  return (trail || []).concat([{ eid: r.eid, answer: r.answer }]).slice(-cap);
}
/** Oldest faint, newest bright. */
function trailAlpha(i, n) { return 0.15 + 0.75 * (i + 1) / Math.max(1, n); }

// ◎ self's framing (#237, the operator: "much closer ... scale with what self is doing";
// then "camera is too far and not really following/looking"): close, on what
// self looks at NOW -- not the whole trail of looks, which spans the field
const FRAME_FOV = 55, FRAME_PAD = 1.1, FRAME_MARGIN = 25, FRAME_MIN = 70, FRAME_MAX = 500;
const SELF_FRAME_STEPS = 2;
const d3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
function frameRadius(pos, pts) {
  let r = 0;
  for (const q of pts || []) if (q && pos) r = Math.max(r, d3(pos, q));
  return r;
}
/** The look self has on now, while fresh (the same freshness as 👁 self's eyes). */
function freshLook(lk) { return lk && lk.p && lk.age < EYES_FRESH_MS ? lk.p : null; }
/** The amoeba, its current look (fresh), its aim and its last couple of
 *  steps: how far they spread. The older looks of the trail don't count. */
function selfFrame(st) {
  const lp = freshLook(st.look);
  const pts = [].concat(lp ? [lp] : [], (st.steps || []).slice(-SELF_FRAME_STEPS), st.aim ? [st.aim] : []);
  return { center: st.pos, radius: frameRadius(st.pos, pts) };
}
// ◎ self follows at once: a touch holds it a few seconds, then it resumes on
// its own; the other modes still wait IDLE_BEFORE_FOLLOW_MS after a touch
const SELF_HOLD_MS = 4000;
function followsNow(mode, sinceTouchMs) {
  return sinceTouchMs >= (mode === "self" ? SELF_HOLD_MS : IDLE_BEFORE_FOLLOW_MS);
}
/** How fast the camera catches up (per second): the target, the angle, the distance. */
function followRates(mode) {
  return mode === "self" ? { target: 3.0, turn: 1.4, zoom: 1.8 } : { target: 0.9, turn: 0.5, zoom: 0.9 };
}
/** Orbit angles that put the camera behind self (and a little above), looking
 *  along self -> what it looks at; null when the look sits on self. */
function lookAngles(pos, look) {
  if (!pos || !look) return null;
  const d = [look[0] - pos[0], look[1] - pos[1], look[2] - pos[2]];
  const n = Math.hypot(d[0], d[1], d[2]);
  if (n < 1) return null;
  const u = [-d[0] / n, -d[1] / n + 0.35, -d[2] / n];
  const m = Math.hypot(u[0], u[1], u[2]);
  return { phi: Math.max(0.05, Math.min(Math.PI - 0.05, Math.acos(u[1] / m))), theta: Math.atan2(u[0] / m, u[2] / m) };
}
/** ◎ self's view angle: toward its fresh look, else the chase from behind. */
function selfCamAngles(st) {
  const lp = st && st.pos ? freshLook(st.look) : null;
  return (lp && lookAngles(st.pos, lp)) || chaseAngles(st && st.trail);
}
/** The camera distance that puts `radius` (padded 10%, plus a margin) inside
 *  the vertical half-angle of the view -- clamped to [FRAME_MIN, FRAME_MAX]. */
function frameDistance(radius, fovDeg = FRAME_FOV) {
  const d = (Math.max(0, Number(radius) || 0) * FRAME_PAD + FRAME_MARGIN) / Math.tan((fovDeg / 2) * Math.PI / 180);
  return Math.max(FRAME_MIN, Math.min(FRAME_MAX, d));
}
/** Critically damped step toward goal; reduced motion steps at once. */
function dampTo(cur, goal, dt, rate, reduced) {
  if (reduced) return goal;
  return cur + (goal - cur) * (1 - Math.exp(-dt * rate));
}

// 👁 self's eyes (#237, the operator: "PUT ME INTO self's WORLD!")
function followLabel(m) { return m === "eyes" ? "👁 " + SELF_NAME + "'s eyes" : "◎ " + (m === "self" ? SELF_NAME : m); }
/** A manual orbit / zoom leaves the eyes (back to ◎ self); other modes stay. */
function modeAfterTouch(m) { return m === "eyes" ? "self" : m; }
const EYES_FRESH_MS = 8000;
/** Where the eyes look: the thing it reaches for now (while fresh and placed),
 *  else its aim, else the way it is heading, else the field's centre. */
function eyesTarget(st) {
  const pos = st && st.pos;
  if (!pos) return null;
  const lk = st.look;
  if (lk && lk.p && lk.age < EYES_FRESH_MS && d3(pos, lk.p) > 1) return { kind: "look", p: lk.p };
  if (st.aim && d3(pos, st.aim) > 1) return { kind: "aim", p: st.aim };
  const tr = st.trail || [];
  if (tr.length >= 2) {
    const a = tr[Math.max(0, tr.length - 5)], b = tr[tr.length - 1], n = d3(a, b);
    if (n > 1e-6) return { kind: "heading", p: [0, 1, 2].map((i) => pos[i] + (b[i] - a[i]) / n * 200) };
  }
  if (Math.hypot(pos[0], pos[1], pos[2]) > 1) return { kind: "centre", p: [0, 0, 0] };
  return null;
}
function norm3(v) { const n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; }
/** Turn a unit direction toward another (k: 0 stays, 1 arrives). */
function turnToward(dir, want, k) {
  const w = norm3(want), d = norm3(dir);
  if (k >= 1) return w;
  return norm3([0, 1, 2].map((i) => d[i] + (w[i] - d[i]) * k));
}
/** The orbit that puts the camera `back` behind pos, looking along dir. */
function eyesOrbit(pos, dir, back = 6) {
  const d = norm3(dir), R = 120;
  const target = [0, 1, 2].map((i) => pos[i] + d[i] * R);
  const u = [-d[0], -d[1], -d[2]];
  return { target, r: R + back, phi: Math.max(1e-4, Math.min(Math.PI - 1e-4, Math.acos(u[1]))), theta: Math.atan2(u[0], u[2]) };
}

// HA's sidebar as a drop-down (2026-10-10, the operator: "replace the HA
// button with that sidebar ... float over the field like a drop-down menu;
// exit the field whenever the user uses it to navigate away; any click
// outside closes it; don't load membrane automatically if the user last left
// membrane thru that menu")
const LS_LEFT = "membrane.home.left";      // panel.js's landing rule reads it
const BUILTIN_TITLES = { lovelace: "Overview", energy: "Energy", map: "Map", logbook: "Logbook", history: "History",
  "media-browser": "Media", todo: "To-do lists", calendar: "Calendar", config: "Settings",
  "developer-tools": "Developer tools", profile: "Profile" };
const BUILTIN_ICONS = { lovelace: "mdi:view-dashboard", energy: "mdi:lightning-bolt", map: "mdi:tooltip-account",
  logbook: "mdi:format-list-bulleted-type", history: "mdi:chart-box", "media-browser": "mdi:play-box-multiple",
  todo: "mdi:clipboard-list", calendar: "mdi:calendar", config: "mdi:cog", "developer-tools": "mdi:hammer",
  profile: "mdi:account" };
const TAIL = ["developer-tools", "config", "profile"];

/** HA's sidebar entries, in the user's own sidebar order, minus the hidden
 * ones and this page; settings / developer tools / profile last. */
function menuEntries(panels, order, hidden, self) {
  const out = [];
  for (const p of Object.values(panels || {})) {
    if (!p || !p.url_path || p.url_path === self) continue;
    const title = p.title || BUILTIN_TITLES[p.url_path] || BUILTIN_TITLES[p.component_name];
    if (!title || (hidden || []).includes(p.url_path)) continue;
    out.push({ path: p.url_path, title, icon: p.icon || BUILTIN_ICONS[p.url_path] || BUILTIN_ICONS[p.component_name] || "mdi:circle-small" });
  }
  const rank = (e) => {
    const t = TAIL.indexOf(e.path);
    if (t >= 0) return 1e6 + t;
    const i = (order || []).indexOf(e.path);
    return i >= 0 ? i : 1e5 + (e.path === "lovelace" ? -1 : 0);
  };
  return out.sort((a, b) => rank(a) - rank(b) || a.title.localeCompare(b.title));
}

function nextMode(m) { const i = FOLLOW_MODES.indexOf(m); return FOLLOW_MODES[(i + 1) % FOLLOW_MODES.length]; }

function personSlug(name) {
  return String(name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
}

function hhash(s) {
  let h = 2166136261;
  s = String(s);
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function hKindColor(kind) { return "hsl(" + (hhash("kind:" + kind) % 360) + ", 68%, 62%)"; }

const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } };

// ---- VOICE + TILT (2026-10-10, the operator: "PTT and PTT Lock buttons that
// do stt into the chat + TTS button that makes membrane say its answers ...
// rotate the graph based on accelerometer/gyro"; laptops too)
const LS_TTS = "membrane.home.tts";
/** THE COMMENTATOR'S RULE (2026-10-11, the operator: "the tts is very much
 *  behind the chat"): speech keeps up with the chat instead of reading out a
 *  backlog. A sentence that waited CATCHUP_STALE_MS is skipped when something
 *  newer of the same speaker waits behind it; membrane's lines older than
 *  CATCHUP_BABBLE_MS go; at most CATCHUP_KEEP of the seat's sentences wait. */
const CATCHUP_STALE_MS = 15000, CATCHUP_BABBLE_MS = 20000, CATCHUP_KEEP = 6;
const CATCHUP_FAST_AT = 3, CATCHUP_RATE = 1.2;
function catchUp(queue, now) {
  const q = (queue || []).slice();
  const newer = (i) => q.slice(i + 1).some((y) => y.kind === q[i].kind);
  let out = q.filter((x, i) => !(now - x.ts > (x.kind === "membrane" ? CATCHUP_BABBLE_MS : CATCHUP_STALE_MS) &&
                                 (x.kind === "membrane" || newer(i))));
  const seatSaid = out.filter((x) => x.kind !== "membrane");
  if (seatSaid.length > CATCHUP_KEEP) {
    const drop = new Set(seatSaid.slice(0, seatSaid.length - CATCHUP_KEEP));
    out = out.filter((x) => !drop.has(x));
  }
  return out;
}

/** Speech decisions, in the console ("[membrane speech] ..."): why a reply was or wasn't said. */
function speechLog(who, what, text) {
  try { console.info("[membrane speech]", who, what, String(text || "").slice(0, 80)); } catch (e) { /* no console */ }
  // the standalone page sends its voice log to the seat (host.js): <data dir>/page-voice.log
  try { if (window.MEMBRANE_SPEECH_LOG) window.MEMBRANE_SPEECH_LOG(who, what, String(text || "").slice(0, 400)); } catch (e) { /* best effort */ }
}
const STT_PATH = "/ui/stt";
// 8 minutes (2026-10-15: 60 s cut the operator off mid-thought): the longest the
// HA path carries -- its web server refuses a body over 16 MiB (32 kB/s of WAV)
const REC_MAX_S = 480, REC_MIN_S = 0.35, STT_RATE = 16000;
const TILT_GAIN = 1.5;

/** Float32 at inRate -> Int16 at outRate (box-averaged decimation). */
/** THE MIC OFF THE MAIN THREAD (2026-10-12, the operator: "the stt sometimes is
 *  atrocious, feels like its when the animations are flying"): the ScriptProcessor
 *  ran its callback on the page's main thread -- while the 3D field and the wave
 *  drew, frames came late or were DROPPED, and the recognizer heard speech with
 *  holes in it. An AudioWorklet captures on the audio thread; its frames queue
 *  instead of dropping. Same 2048-sample AudioBuffers to the same handler; the old
 *  node only where worklets don't exist. */
const MIC_FRAME = 2048;
async function micNode(ctx, handler) {
  if (ctx.audioWorklet && window.AudioWorkletNode) {
    try {
      if (!ctx.__membraneTap) {
        const code = "class T extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array(" + MIC_FRAME +
          ");this.n=0}process(i){const c=i[0]&&i[0][0];if(c){for(let k=0;k<c.length;k++){this.b[this.n++]=c[k];" +
          "if(this.n===this.b.length){this.port.postMessage(this.b,[this.b.buffer]);this.b=new Float32Array(" + MIC_FRAME +
          ");this.n=0}}}return true}}registerProcessor('membrane-tap',T)";
        const url = URL.createObjectURL(new Blob([code], { type: "application/javascript" }));
        await ctx.audioWorklet.addModule(url);
        ctx.__membraneTap = true;
      }
      const node = new AudioWorkletNode(ctx, "membrane-tap", { numberOfInputs: 1, numberOfOutputs: 0 });
      node.port.onmessage = (e) => {
        const f = e.data, ab = new AudioBuffer({ length: f.length, numberOfChannels: 1, sampleRate: ctx.sampleRate });
        ab.copyToChannel(f, 0);
        handler({ inputBuffer: ab });
      };
      return node;
    } catch (e) { /* no worklet here: the old node */ }
  }
  const sp = ctx.createScriptProcessor(MIC_FRAME, 1, 1);
  sp.onaudioprocess = handler;
  sp._sp = true;
  return sp;
}

function downsample(f32, inRate, outRate) {
  const ratio = inRate / outRate, n = Math.floor(f32.length / ratio), out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i * ratio), b = Math.min(f32.length, Math.floor((i + 1) * ratio));
    let sum = 0; for (let j = a; j < b; j++) sum += f32[j];
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

/** 16-bit mono PCM -> WAV bytes. */
function wavBytes(i16, rate) {
  const buf = new ArrayBuffer(44 + i16.length * 2), v = new DataView(buf);
  const str = (o, t) => { for (let i = 0; i < t.length; i++) v.setUint8(o + i, t.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + i16.length * 2, true); str(8, "WAVE"); str(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data");
  v.setUint32(40, i16.length * 2, true);
  for (let i = 0; i < i16.length; i++) v.setInt16(44 + i * 2, i16[i], true);
  return new Uint8Array(buf);
}

/** Text -> chunks the TTS speaks one after another (sentences, <= max chars). */
function speechChunks(text, max = 220) {
  const out = [];
  for (const s of String(text || "").match(/[^.!?]+[.!?]*/g) || []) {
    let t = s.trim();
    while (t.length > max) { const cut = t.lastIndexOf(" ", max); const k = cut > 40 ? cut : max; out.push(t.slice(0, k)); t = t.slice(k).trim(); }
    if (t) out.push(t);
  }
  return out;
}

/** Device orientation -> (left-right, front-back) degrees for the screen's
 * current rotation (landscape swaps the axes). */
function tiltAxes(beta, gamma, angle) {
  const a = ((angle || 0) % 360 + 360) % 360;
  if (a === 90) return [beta, -gamma];
  if (a === 270) return [-beta, gamma];
  if (a === 180) return [-gamma, -beta];
  return [gamma, beta];
}

/** The orbit angles for a tilt relative to where it started. */
function tiltOrbit(base, lr, fb, gain = TILT_GAIN) {
  const d = Math.PI / 180;
  // the operator, 2026-10-10: "feels like it should be inverted" -- tilt
  // right turns the field right, tilt toward you looks from above
  return { theta: base.theta + (lr - base.lr) * d * gain,
           phi: Math.max(0.05, Math.min(Math.PI - 0.05, base.phi + (fb - base.fb) * d * gain)) };
}

// ---- THE HOTWORD (2026-10-10, the operator: "can you add hotword detection?
// 'Hey <name>'"): the browser measures loudness locally; only a burst of
// speech goes to the LAN's whisper (the PTT path), and only a transcript that
// starts with the hotword reaches the chat. Opt-in (👂), paused while the page
// is hidden, while you hold PTT and while the seat speaks; capped.
const LS_HOTWORD = "membrane.home.hotword";
// ends of speech waited out longer (2026-10-10, the operator: "it cuts off mid-sentence")
const HOT_MIN_S = 0.4, HOT_MAX_S = 12, HOT_END_MS = 900, HOT_PER_MIN = 16, HOT_FOLLOW_MS = 8000;
// THE FOLLOW-UP WINDOW, OFF (2026-10-12, the operator: "just turn it off"): a bare
// "Hey <name>" used to open 8 s of listening for whatever came next -- ambient
// speech went out as messages; now a bare wake word only chimes. Say it in one breath.
const FOLLOW_ON = false;
const HOT_EARLY_S = 1.3;          // the first stretch is checked while you are still talking
const HOT_ACKS = ["Yes?", "Uh-huh?", "Mm-hm?", "I'm listening."];
// Latin and Cyrillic (runglish, 2026-10-10: "<name>, включи свет"); JS's \b
// knows no Cyrillic, so the name ends at a space, a mark or the end
const HOT_NAMES = SEAT_SAYS.map(reEsc).join("|");
const HOT_GREETS = "hey|hi|hay|ok|okay|hello|yo|эй|хей|привет|окей|ок";
const HOT_RE = new RegExp("^(?:(?:" + HOT_GREETS + ")[\\s,.!-]+)?(?:" + HOT_NAMES + ")(?=$|[\\s,.!?-])[\\s,.!?-]*([\\s\\S]*)$", "iu");

/** What gets spoken: the message, up to the safety net. */
function speakable(text) {
  const all = speechChunks(text);
  const out = []; let n = 0;
  for (const c of all) { if (out.length >= SPEAK_MAX_SENTENCES || n + c.length > SPEAK_MAX_CHARS) break; out.push(c); n += c.length; }
  if (!out.length && all.length) out.push(all[0].slice(0, SPEAK_MAX_CHARS));
  return out;
}

/** 'Hey <name>, turn on the lights' -> {hit: true, rest: 'turn on the lights'}. */
function hotword(text) {
  const t = String(text || "").trim().replace(/^[\s\p{P}\p{S}]+/u, "");   // marks only: Cyrillic letters stay
  const m = HOT_RE.exec(t);
  if (!m) return { hit: false, rest: "" };
  return { hit: true, rest: (m[1] || "").trim() };
}

// ---- THE LOCAL VOICE (2026-10-10, the operator: "can it all be in-page?
// pleeease? and fast?"): Vosk (Apache-2.0, WebAssembly, in a worker) hears
// the wake word and transcribes ON THE DEVICE -- no audio leaves it; replies
// speak in the device's own voice. ⚙ switches transcription to the brain
// phone's whisper (more accurate, over the LAN) for whoever wants that.
const VOSK_JS = "/ui/www/vosk.js";
const VOSK_MODEL = "/ui/www/vosk-model-en.tar.gz";
const VOSK_CACHE = "membrane-voice-v1";
const LS_STT = "membrane.home.stt";          // "device" (default) | "lan"
// a greeting is required: a bare name in the grammar matched "turn on" (the
// recognizer squeezes every sound into its few phrases -- the browser test
// caught it, 2026-10-10)
const WAKE_NAMES = SEAT_SAYS;
const WAKE_GREETS = ["hey", "hay", "hi", "okay", "ok"];
const WAKE_GRAMMAR = WAKE_GREETS.flatMap((g) => WAKE_NAMES.map((n) => g + " " + n)).concat(["[unk]"]);
const WAKE_RE = new RegExp("\\b(" + WAKE_GREETS.join("|") + ") (" + WAKE_NAMES.join("|") + ")\\b");
// a safety net for very long messages, cut silently (the operator didn't want
// "the rest is in the chat"; answers are short now and he can be interrupted)
const SPEAK_MAX_SENTENCES = 12, SPEAK_MAX_CHARS = 1500;
// ADDRESSED (2026-10-10, the operator: "every time someone addresses the seat, not
// just a wake phrase"): a free on-device recognizer hears every sentence; one
// that names him anywhere goes, as audio, to whisper and becomes a message
const LS_EARMODE = "membrane.home.earmode";          // "wake" (default) | "addressed"
const NAME_ANY_RE = new RegExp("\\b(" + HOT_NAMES + ")\\b", "i");
// whisper hears the name's tail ("gus, turn on..."): what is cut off the front
const STRIP_RE = new RegExp("^\\s*(?:(?:hey|hi|okay|ok)[\\s,]+)?(?:" + HOT_NAMES + ")(?=$|[\\s,.!?-])[\\s,.!?-]*", "i");
const ADDR_MAX_S = 15;
const DICTATE_END_MS = 1100, DICTATE_WAIT_MS = 3000, LOCK_END_MS = 1600;

function loadScript(src) {
  return new Promise((res, rej) => {
    if (document.querySelector('script[data-src="' + src + '"]')) { res(); return; }
    const el = document.createElement("script");
    el.src = src; el.dataset.src = src; el.onload = () => res(); el.onerror = () => rej(new Error("could not load " + src));
    document.head.appendChild(el);
  });
}

/** The model as a blob: URL, from the browser's Cache Storage after the first time. */
async function modelBlobUrl(onProgress) {
  let resp = null, cache = null;
  try { cache = await caches.open(VOSK_CACHE); resp = await cache.match(VOSK_MODEL); } catch (e) { cache = null; }
  if (!resp) {
    const r = await fetch(VOSK_MODEL);
    if (!r.ok) throw new Error("the voice model is missing (" + r.status + ")");
    const total = +r.headers.get("Content-Length") || 41e6, reader = r.body.getReader(), parts = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value); got += value.length;
      if (onProgress) onProgress(got, total);
    }
    resp = new Response(new Blob(parts, { type: "application/gzip" }));
    if (cache) { try { await cache.put(VOSK_MODEL, resp.clone()); } catch (e) { /* quota: next time again */ } }
  }
  return URL.createObjectURL(await resp.blob());
}

/** One Vosk model per page (shared by PTT, the ear and every recognizer). */
function localVoice(onProgress) {
  if (!window.__membraneVoice) {
    window.__membraneVoice = (async () => {
      await loadScript(VOSK_JS);
      const url = await modelBlobUrl(onProgress);
      return window.Vosk.createModel(url);
    })();
    window.__membraneVoice.catch(() => { window.__membraneVoice = null; });
  }
  return window.__membraneVoice;
}

/** A Vosk recognizer that collects its results (one per pause) and, on
 * finish(), flushes the rest and answers the whole text. */
function recognizer(model, rate, grammar, onPartial) {
  const kr = grammar ? new model.KaldiRecognizer(rate, JSON.stringify(grammar)) : new model.KaldiRecognizer(rate);
  const r = { kr, texts: [], waiting: null, onResult: null };
  kr.on("result", (m) => {
    const t = ((m.result && m.result.text) || "").trim();
    if (t) r.texts.push(t);
    if (r.onResult) r.onResult(t);
    if (r.waiting) { const w = r.waiting; r.waiting = null; w(); }
  });
  kr.on("partialresult", (m) => { const t = m.result && m.result.partial; if (t && onPartial) onPartial(t); });
  r.finish = () => new Promise((res) => { r.waiting = res; kr.retrieveFinalResult(); setTimeout(res, 2500); })
    .then(() => { try { kr.remove(); } catch (e) { /* gone */ } return r.texts.join(" ").trim(); });
  r.drop = () => { try { kr.remove(); } catch (e) { /* gone */ } };
  return r;
}

// ---- WHISPER IN THE BROWSER (2026-10-10, the operator: "can u use huggingface
// models in-browser?"): transformers.js runs whisper-base.en (q8) on the
// device -- accurate with an accent, no audio leaves it; the model comes from
// Hugging Face once and stays in the browser's cache. Vosk stays for the wake word.
const VOICE_ML = "/ui/www/voice-ml.mjs";
const ASR_MODEL = "onnx-community/whisper-base.en";
function voiceML() {
  if (!window.__membraneML) {
    window.__membraneML = (async () => {
      const ML = await import(VOICE_ML);
      ML.env.allowLocalModels = false;
      ML.env.backends.onnx.wasm.wasmPaths = "/ui/www/";
      return ML;
    })();
    window.__membraneML.catch(() => { window.__membraneML = null; });
  }
  return window.__membraneML;
}
function localASR(onProgress) {
  if (!window.__membraneASR) {
    window.__membraneASR = (async () => {
      const ML = await voiceML();
      return ML.pipeline("automatic-speech-recognition", ASR_MODEL, { dtype: "q8", device: "wasm",
        progress_callback: (p) => { if (p && p.status === "progress" && onProgress) onProgress(p.loaded || 0, p.total || 0, p.file || ""); } });
    })();
    window.__membraneASR.catch(() => { window.__membraneASR = null; });
  }
  return window.__membraneASR;
}

/** Float32 at inRate -> Float32 at 16 kHz (whisper's rate). */
function toWhisperRate(f32, inRate) {
  const i16 = downsample(f32, inRate, STT_RATE), out = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 32768;
  return out;
}

/** whisper's non-speech tags and its phrases-from-silence out (the server does the same). */
function cleanTranscript(text) {
  let t = String(text || "").replace(/\[[^\]]*\]|\([^)]*\)/g, " ").replace(/\s+/g, " ").trim();
  // a short line in subtitle style ("- Anywhere, guys.") is whisper making words out of
  // noise (2026-10-11: "not even close to what I said"); a long one keeps its words
  if (/^[-–—]\s*/.test(t)) {
    if (t.replace(/^[-–—]\s*/, "").split(/\s+/).filter(Boolean).length <= 4) return "";
    t = t.replace(/^[-–—]\s*/, "");
  }
  return /^(you|thank you|thanks for watching|thank you for watching|bye|okay)[.!]?$/i.test(t) ? "" : t;
}

/** THE IDLE CUBE (2026-10-11, the operator: "when voice features are enabled and
 *  not speaking, turn the graph-y animation into a cube that rotates with the
 *  same algorithm as the camera ... when it speaks, it unfolds into a line"):
 *  the wave's nodes sit along a path round a cube's edges, turned by the field
 *  camera's own orbit angles; projected to [-1, 1] screen units. */
const CUBE_PATH = [[-1, -1, -1], [-1, -1, 1], [-1, 1, 1], [-1, 1, -1], [1, 1, -1], [1, 1, 1], [1, -1, 1], [1, -1, -1]];
function cubePoints(N, yaw, pitch, view) {
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
  // `view`: the field camera's own world->view matrix (three.js elements, column-major) --
  // the mini-cube is the field's box seen exactly as the camera sees it (the rotation only)
  const rot = view ? ([x, y, z]) => [view[0] * x + view[4] * y + view[8] * z, view[1] * x + view[5] * y + view[9] * z]
    : ([x, y, z]) => { const x1 = x * cy + z * sy, z1 = -x * sy + z * cy; return [x1, y * cp - z1 * sp]; };
  const nodes = [];
  for (let i = 0; i < N; i++) {
    const t = (CUBE_PATH.length - 1) * i / (N - 1), k = Math.min(CUBE_PATH.length - 2, Math.floor(t)), f = t - k;
    const a = CUBE_PATH[k], b = CUBE_PATH[k + 1];
    nodes.push(rot([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]));
  }
  const edges = [];
  const corners = [];
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) corners.push([x, y, z]);
  for (let i = 0; i < 8; i++) for (let j = i + 1; j < 8; j++) {
    const d = corners[i].reduce((n, v, q) => n + (v !== corners[j][q] ? 1 : 0), 0);
    if (d === 1) edges.push([rot(corners[i]), rot(corners[j])]);
  }
  return { nodes, edges };
}

/** The device's own voice: English, on-device, not a Google one when there is a choice. */
function pickVoice(voices, lang = "en", chosen = "") {
  const en = (voices || []).filter((v) => String(v.lang || "").toLowerCase().startsWith(lang));
  const local = en.filter((v) => v.localService);
  if (chosen) { const c = local.find((v) => v.name === chosen); if (c) return c; }
  // the better downloaded voices first (Apple's Enhanced / Premium), never a Google one when there is a choice
  const good = local.filter((v) => !/google/i.test(v.name));
  return good.find((v) => /premium/i.test(v.name)) || good.find((v) => /enhanced/i.test(v.name)) ||
         good[0] || local[0] || null;
}
const LS_VOICE = "membrane.home.voice";
const IS_CHROME = typeof navigator !== "undefined" && /Chrome\//.test(navigator.userAgent || "") && !/Firefox\//.test(navigator.userAgent || "");

// THE BLURP ZOO (#283, the operator: "several seats in one Home Assistant, each
// with its own Blurp"): the 🦠 button lists this house's seats -- a card per
// seat from the integration's GET /api/membrane/zoo (every seat's Blurp read
// live, a few seconds each); a tap makes that seat the house's (an admin's write to
// .../zoo/active) and the page reloads onto it. Standalone is one seat:
// no button there.
const HZOO = "/ui/zoo";
const ZOO_MS = 30000;
const zooEsc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/** "5 min ago" from a time (epoch seconds, or an ISO string); "" for none. */
function zooAgo(ts, now) {
  if (ts == null || ts === "") return "";
  const t = typeof ts === "number" ? ts : Date.parse(ts) / 1000;
  if (!isFinite(t)) return "";
  const d = Math.max(0, (now == null ? Date.now() / 1000 : now) - t);
  if (d < 60) return "just now";
  if (d < 3600) return Math.round(d / 60) + " min ago";
  if (d < 86400) return Math.round(d / 3600) + " h ago";
  return Math.round(d / 86400) + " d ago";
}

/** One seat's card: its Blurp's name, Lv, rung, awake/asleep -- or offline --
 * the reachability dot, the seat's own name and host, its last activity. */
/** The 👶 panel's Code tab: Blurp's cliffhanger code -- its own methods
 *  (the sheet's `code`), each a name, its command and its steps; read-only. */
function selfCodeHtml(sheet) {
  // #576 D2: Blurp's code is its triggers -- WHEN ... WHERE ...: target = expr --
  // each with its state (shadow / proposal), balance, hits and firings
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const code = (sheet && Array.isArray(sheet.code)) ? sheet.code : [];
  if (!code.length) return '<p class="cdnone">no code yet</p>';
  return code.map((m) => {
    const head = m.line != null ? m.line : (m.command || "");
    const meta = m.state ? `${esc(m.state)} · ${m.balance > 0 ? "+" : ""}${esc(m.balance ?? 0)} · ${esc(m.hits ?? 0)} hits · ${esc(m.fired ?? 0)} fired`
      : (m.runs ? esc(m.runs) + " runs" : "");
    return `<section class="cdm ${esc(m.state || "")}"><h4><code>${esc(m.branch || m.name || "")}</code><span class="cdruns">${meta}</span></h4>` +
      `<pre class="cdcmd"><code>${esc(head)}</code></pre></section>`;
  }).join("");
}

function zooCard(row, now) {
  const r = row || {}, c = r.card || {};
  const on = !!r.online;
  const cls = "zcard" + (r.active ? " active" : "") + (on ? "" : " off");
  const who = on && c.name ? c.name : r.name;
  const lv = on && c.level != null ? `<span class="zlv">Lv ${zooEsc(c.level)}</span>` : "";
  const sub = on ? [c.rung_name, c.state].filter(Boolean).map(zooEsc).join(" · ") : "offline";
  const seat = [r.name, r.host && r.host !== r.name ? r.host : "", on ? zooAgo(c.last_activity, now) : ""].filter(Boolean).map(zooEsc).join(" · ");
  const title = r.active ? "the house talks to this seat" : on ? "talk to this seat (the whole house switches)" : "offline -- the house can still switch to it";
  // #358: an online seat's Blurp drawn from its own card (asleep, energy when the card has it)
  const pz = on ? blurpPose(c) : null;
  const body = pz ? `<canvas class="zbody" width="48" height="48" aria-hidden="true" data-asleep="${pz.asleep ? 1 : 0}" data-off="${pz.off ? 1 : 0}"` +
    (typeof c.energy === "number" && isFinite(c.energy) ? ` data-energy="${zooEsc(c.energy)}"` : "") + "></canvas>" : "";
  return `<button type="button" class="${cls}" data-id="${zooEsc(r.id)}"${r.active ? ' aria-current="true"' : ""} title="${zooEsc(title)}">` +
    `<span class="zrow"><span class="dot ${on ? "on" : "off"}" aria-hidden="true"></span>${body}<span class="zname">${zooEsc(who)}</span>${lv}</span>` +
    `<span class="zsub">${sub}</span><span class="zseat">${seat}</span></button>`;
}

/** The zoo's list (rows from GET /api/membrane/zoo). */
function zooCards(rows, now) {
  return (rows || []).length ? rows.map((r) => zooCard(r, now)).join("")
    : '<p class="znone">no seats yet -- add one in the integration\'s options</p>';
}

// THE OPTIONS WINDOW (#344, decided 2026-10-21): the ⚙ in the top bar opens one
// window for the seat-wide, long-term settings -- read with GET /membrane/options
// when it opens, each change saved at once with a post to the same path
// (membrane/options.py validates; a bad value comes back as the reason, shown
// in the window). They live in the seat's
// settings.json; per-device state (the chat tab, the camera) stays in the browser.
// The 🔔 and ▶ stay in the top bar. OptionsWindow takes its elements and the two
// calls, so node drives it with stubs (tests/test_options_window_344.py).
const OPT_PATH = "/membrane/options";
const OPT_SHARE_MIN = 10, OPT_SHARE_MAX = 100;           // the slider, in %

/** Blurp's CPU share (0..1) as the slider's whole percent. */
function optSharePct(share) {
  const v = Number(share);
  return Math.round((isFinite(v) && v > 0 ? v : 0.5) * 100);
}

/** The slider's percent as the share the seat takes: clamped to 10-100 %,
 * two decimals; not a number -> the default 0.5. */
function optShareFromPct(pct) {
  const v = Number(pct);
  if (pct === "" || pct == null || !isFinite(v)) return 0.5;
  return Math.round(Math.min(OPT_SHARE_MAX, Math.max(OPT_SHARE_MIN, v))) / 100;
}

// BACKGROUND WORK (2026-10-23, the operator): the two minds' models, moved here
// from the chat's 🧠 strip ("Minds -> background loops"). The seat routes each
// key to minds_models -- the same state GET /membrane/models reads. A mind on a
// claude-agent rung also picks its credentials; a local model needs none.
const BG_MINDS = [
  ["loops", "Goal worker", "the model that works your open goals"],
  ["cortex", "Idle thinker", "thinks ahead between your messages"],
];
const BG_AUTH = [["chat", "Same as the chat"], ["api-key", "API key"], ["login", "CLI login"]];

/** The Background work section from GET /membrane/options' `background`
 * (null: no seat -> ""). */
function backgroundRows(bg) {
  if (!bg) return "";
  const claude = new Set(bg.claude || []);
  const chatNow = bg.chat_auth === "login" ? "CLI login" : bg.chat_auth === "api-key" ? "API key" : "";
  const opt = (v, label, cur) => `<option value="${zooEsc(v)}"${v === cur ? " selected" : ""}>${zooEsc(label)}</option>`;
  let html = '<h3 class="osec">Background work</h3>';
  for (const [mind, label, hint] of BG_MINDS) {
    const cur = String(bg[mind] || "");
    const models = (bg.models || []).slice();
    if (cur && !models.includes(cur)) models.unshift(cur);
    html += `<div class="orow"><label class="olab" for="opt-${mind}">${zooEsc(label)}</label>` +
      `<select id="opt-${mind}" class="osel" data-opt="${mind}" aria-describedby="opt-${mind}-h">` +
      models.map((m) => opt(m, m + (/:cloud$|-cloud$/.test(m) ? " ☁" : ""), cur)).join("") + "</select>" +
      `<p class="ohint" id="opt-${mind}-h">${zooEsc(hint)}</p>`;
    if (claude.has(cur)) {
      const a = String(bg[mind + "_auth"] || "chat");
      html += `<label class="olab osub" for="opt-${mind}-auth">credentials</label>` +
        `<select id="opt-${mind}-auth" class="osel" data-opt="${mind}_auth">` +
        BG_AUTH.map(([v, l]) => opt(v, v === "chat" && chatNow ? l + " (" + chatNow + ")" : l, a)).join("") + "</select>" +
        '<p class="ohint">a Claude rung here makes background turns call Claude, billed to these credentials</p>';
    }
    html += "</div>";
  }
  return html;
}

/** The window's rows from GET /membrane/options. */
function optionsRows(o) {
  o = o || {};
  const pct = optSharePct(o.self_cpu_share), def = optSharePct((o.defaults || {}).self_cpu_share);
  const tau = o.current_tau_s != null ? zooEsc(o.current_tau_s) + " s" : "?";
  const en = o.energy && isFinite(Number(o.energy.energy)) ? o.energy : null;     // #363: energy replaced health
  const energy = en ? Math.round(Number(en.energy) * 100) + "% · " + (en.asleep ? "asleep" : "awake") : "not running yet";
  const keepDef = (o.defaults || {}).code_runs_keep || 30, keep = o.code_runs_keep || keepDef;
  return `<div class="orow"><label class="olab" for="opt-share">${zooEsc(SELF_NAME)}'s CPU share</label>` +
      `<output class="oval" for="opt-share">${pct}%</output>` +
      `<input id="opt-share" class="oshare" type="range" min="${OPT_SHARE_MIN}" max="${OPT_SHARE_MAX}" step="5" value="${pct}" ` +
      `aria-describedby="opt-share-h"><p class="ohint" id="opt-share-h">how much of one CPU its cycles may use (default ${def}%)</p></div>` +
    `<div class="orow"><span class="olab">the field current's fade</span><span class="oval">${tau}</span>` +
      `<p class="ohint">read-only: ${zooEsc(o.current_tau_note || "changing it needs the operator's approval")}</p></div>` +
    `<div class="orow"><span class="olab">${zooEsc(SELF_NAME)}'s energy</span><span class="oval">${zooEsc(energy)}</span></div>` +
    `<div class="orow"><label class="olab" for="opt-keep">Code runs kept</label>` +
      `<input id="opt-keep" class="onum" type="number" min="1" step="1" value="${zooEsc(keep)}" data-opt="code_runs_keep" ` +
      `aria-describedby="opt-keep-h"><p class="ohint" id="opt-keep-h">per code tree, method and executor; ` +
      `older runs are deleted each night (default ${zooEsc(keepDef)})</p></div>` +
    backgroundRows(o.background);
}

class OptionsWindow {
  /** els: btn (the ⚙), panel, body (the rows), msg (the status line);
   * get(path) -> the JSON; post(path, body) -> the JSON, or throws (the reason). */
  constructor({ btn, panel, body, msg, get, post }) {
    Object.assign(this, { btn, panel, body, msg, get, post });
    this.state = null;
  }

  get open() { return !!this.panel && !this.panel.hidden; }

  /** Open (re-reading the seat) or close; null = the other way round. */
  async toggle(on) {
    on = on == null ? !this.open : !!on;
    this.panel.hidden = !on;
    this.btn.classList.toggle("on", on);
    this.btn.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) await this.load();
  }

  /** The window's keys: Escape closes it and gives the ⚙ the focus back. */
  key(e) {
    if (!e || e.key !== "Escape" || !this.open) return;
    if (e.stopPropagation) e.stopPropagation();
    this.toggle(false);
    if (this.btn.focus) this.btn.focus();
  }

  async load() {
    this.msg.textContent = "reading…";
    try {
      this.paint(await this.get(OPT_PATH));
      this.msg.textContent = "";
    } catch (e) { this.msg.textContent = "the seat did not answer (" + (e && e.message || e) + ")"; }
  }

  paint(o) {
    this.state = o || this.state;
    this.body.innerHTML = optionsRows(this.state);
  }

  /** A Background work pick (`key`: loops | cortex | loops_auth | cortex_auth):
   * post that one key, repaint with what the seat saved (or its refusal, the
   * rows back at the seat's value). -> the saved options, or null. */
  async setOption(key, value) {
    this.msg.textContent = "saving…";
    try {
      const o = await this.post(OPT_PATH, { [key]: value });
      this.paint(o);
      this.msg.textContent = "saved";
      return o;
    } catch (e) {
      this.paint(this.state);
      this.msg.textContent = "not saved: " + (e && e.message || e);
      return null;
    }
  }

  /** The slider let go at `pct`: post the share, show what the seat saved
   * (or its refusal, the rows back at its value). -> the saved options, or null. */
  async setShare(pct) {
    const share = optShareFromPct(pct);
    this.msg.textContent = "saving…";
    try {
      const o = await this.post(OPT_PATH, { self_cpu_share: share });
      this.paint(o);
      this.msg.textContent = "saved: " + optSharePct(o.self_cpu_share) + "%";
      return o;
    } catch (e) {
      this.paint(this.state);
      this.msg.textContent = "not saved: " + (e && e.message || e);
      return null;
    }
  }
}

// THE STATS WINDOW (📊, beside the ⚙): the seat's tokenomics, read-only -- one
// read, GET /membrane/stats on the serve port (membrane/tokenomics.py), on open
// and every STATS_EVERY_MS while open. Today and the last hour by mind and by
// model (a runaway loop stands out in the last hour), and the Claude rung's
// cache split since the seat started. StatsWindow takes its elements and the
// calls, so node drives it with stubs (tests/test_stats_window.py).
const STATS_PATH = "/membrane/stats";
const STATS_EVERY_MS = 10000;
const STATS_MINDS = [["chat", "Chat"], ["loops", "Goal worker"], ["cortex", "Idle thinker"], ["other", "Other background"]];

/** A token count, short: 999, 1.2k, 123k, 2.5M. */
function tokShort(n) {
  const v = Math.max(0, Number(n) || 0);
  const s = (x, u) => (x < 10 ? String(Math.round(x * 10) / 10) : String(Math.round(x))) + u;
  if (v < 1000) return String(Math.round(v));
  if (v < 1e6) return s(v / 1e3, "k");
  return s(v / 1e6, "M");
}

/** One split (minds or models) as a table: calls, in, out today, and the last
 * hour's in+out -- the busiest of the last hour marked hot (when there are two
 * or more rows to tell apart). */
function statsTable(head, names, today, hour) {
  const t = today || {}, h = hour || {};
  const lastH = (k) => { const r = h[k]; return r ? (r.input || 0) + (r.output || 0) : 0; };
  let hot = null, top = 0;
  for (const [k] of names) if (lastH(k) > top) { top = lastH(k); hot = k; }
  const shown = names.filter(([k]) => t[k] || h[k]);
  if (shown.length < 2) hot = null;
  const rows = shown.map(([k, label]) => {
    const r = t[k] || { calls: 0, input: 0, output: 0 };
    return `<tr><th scope="row">${zooEsc(label)}</th><td>${zooEsc(r.calls || 0)}</td><td>${tokShort(r.input)}</td>` +
      `<td>${tokShort(r.output)}</td><td${k === hot ? ' class="hot"' : ""}>${tokShort(lastH(k))}</td></tr>`;
  });
  if (!rows.length) return '<p class="ohint">nothing yet today</p>';
  return `<table class="stbl"><thead><tr><th scope="col">${zooEsc(head)}</th><th scope="col">calls</th>` +
    '<th scope="col">in</th><th scope="col">out</th><th scope="col" title="in + out in the last hour">last h</th></tr></thead>' +
    `<tbody>${rows.join("")}</tbody></table>`;
}

/** The window's rows from GET /membrane/stats. */
function statsRows(s) {
  s = s || {};
  const today = s.today || {}, hour = s.hour || {};
  const line = (w) => { const t = w.total || {}; return `${zooEsc(t.calls || 0)} calls · in ${tokShort(t.input)} · out ${tokShort(t.output)}`; };
  const td = today.models || {}, hd = hour.models || {};
  const io = (r) => ((r || {}).input || 0) + ((r || {}).output || 0);
  const models = Array.from(new Set(Object.keys(td).concat(Object.keys(hd))))
    .sort((a, b) => io(td[b]) - io(td[a])).map((m) => [m, m]);
  // out of tokens (quota.py): the paused models first, one line each
  const paused = (s.paused || []).map((p) => `<div class="orow"><span class="olab">${zooEsc(p.model)}</span>` +
    `<span class="oval">resumes at ${zooEsc(quotaWhen(p.resumes_at, Date.now()).at)}</span></div>`).join("");
  let html = (paused ? '<h3 class="osec">⚠ Out of tokens</h3>' + paused : "") + '<h3 class="osec">Today</h3>' +
    `<div class="orow"><span class="olab">since midnight</span><span class="oval">${line(today)}</span></div>` +
    `<div class="orow"><span class="olab">last hour</span><span class="oval">${line(hour)}</span>` +
    '<p class="ohint">every model call the seat made: your chats and its background work</p></div>' +
    '<h3 class="osec">By mind, today</h3>' + statsTable("mind", STATS_MINDS, today.minds, hour.minds) +
    '<h3 class="osec">By model, today</h3>' + statsTable("model", models, today.models, hour.models);
  const c = s.claude || {};
  html += '<h3 class="osec">Claude rung, since the seat started</h3>';
  if (!c.answers) return html + '<p class="ohint">no Claude answers since the seat started</p>';
  const row = (label, r) => { r = r || {};
    return `<tr><th scope="row">${zooEsc(label)}</th><td>${tokShort(r.cache_read)}</td><td>${tokShort(r.cache_write)}</td>` +
      `<td>${tokShort(r.input)}</td><td>${tokShort(r.output)}</td><td>${tokShort(r.effective)}</td></tr>`; };
  return html + `<div class="orow"><span class="olab">${zooEsc(c.answers)} answers · ${zooEsc(c.steps || 0)} steps</span></div>` +
    '<table class="stbl"><thead><tr><th scope="col"></th><th scope="col">read</th><th scope="col">write</th>' +
    '<th scope="col">uncached</th><th scope="col">out</th><th scope="col">effective</th></tr></thead><tbody>' +
    row("per answer", c.per_answer) + row("first step", c.first_step) + row("later steps", c.later_step) + row("all", c.total) +
    '</tbody></table><p class="ohint">effective input = 0.1 × read + 1.25 × write + uncached: what the input bills as, in uncached tokens. ' +
    "An answer's first step writes the fresh prompt to the cache; its later steps mostly read it.</p>";
}

class StatsWindow {
  /** els: btn (the 📊), panel, body, msg; get(path) -> the JSON;
   * every(fn, ms) / stop(id): the refresh timer (setInterval by default). */
  constructor({ btn, panel, body, msg, get, every, stop }) {
    Object.assign(this, { btn, panel, body, msg, get });
    this.every = every || ((fn, ms) => setInterval(fn, ms));
    this.stop = stop || ((id) => clearInterval(id));
    this.state = null; this._t = null;
  }

  get open() { return !!this.panel && !this.panel.hidden; }

  /** Open (read now, then every STATS_EVERY_MS) or close; null = the other way round. */
  async toggle(on) {
    on = on == null ? !this.open : !!on;
    this.panel.hidden = !on;
    this.btn.classList.toggle("on", on);
    this.btn.setAttribute("aria-expanded", on ? "true" : "false");
    if (this._t != null) { this.stop(this._t); this._t = null; }
    if (!on) return;
    this._t = this.every(() => this.load(), STATS_EVERY_MS);
    await this.load();
  }

  /** Escape closes it and gives the 📊 the focus back. */
  key(e) {
    if (!e || e.key !== "Escape" || !this.open) return;
    if (e.stopPropagation) e.stopPropagation();
    this.toggle(false);
    if (this.btn.focus) this.btn.focus();
  }

  async load() {
    if (!this.state) this.msg.textContent = "reading…";
    try {
      this.state = await this.get(STATS_PATH);
      this.body.innerHTML = statsRows(this.state);
      this.msg.textContent = "";
    } catch (e) { this.msg.textContent = "the seat did not answer (" + (e && e.message || e) + ")"; }
  }
}

// THE RUNG CELEBRATION (#289, the operator: "a rung is a milestone the
// operator waits hours for; it should be unmissable"): a pass gets ~3 s over
// the field (the amoeba bounces and glows, a burst in its colours, a caption;
// a still banner under reduced motion), a short synthesized chime and a
// browser notification -- once per rung per viewer. The 🔔 in the top bar
// mutes both, remembered per viewer; its first tap asks for notifications.
const LS_BELL = "membrane.home.bell";
const CELEBRATE_MS = 3200;
// the amoeba's own colours (field3d.js: its mint body, white core, gold halo)
const BLURP_COLOURS = ["#7cf0c8", "#ffffff", "#d4a017", "#ffe28c", "#5ad17a"];

/** Where this viewer remembers the last rung it celebrated: per seat, per self. */
function rungKey(seat, self) { return "membrane.home.rung." + String(seat || "") + "." + String(self || ""); }

/** A pass of rung n that a later rung_void line takes back (#254), by the record's own lines. */
function rungVoided(lines, n) {
  let pass = -Infinity, nul = -Infinity;
  for (const r of lines || []) {
    if (!r || Number(r.rung) !== n) continue;
    const t = Number(r.ts) || 0;
    if (r.kind === "rung") pass = Math.max(pass, t);
    else if (r.kind === "rung_void") nul = Math.max(nul, t);
  }
  return nul > -Infinity && nul >= pass;
}

/** THE ONCE-PER-RUNG RULE: `level` is the seat's passed rung (status().rung,
 *  void-aware), `last` the last one this viewer celebrated (null: never here).
 *  A first visit remembers the level and celebrates no backlog; a higher level
 *  celebrates once (the highest: a pass reached while away shows on the next
 *  visit) unless the record voids it; a lower one (a void applied) is followed
 *  down quietly, so a later real pass of that rung is news again. */
function rungToCelebrate(level, last, lines) {
  const prev = last == null || last === "" || !isFinite(Number(last)) ? null : Number(last);
  if (level == null || level === "" || !isFinite(Number(level))) return { rung: null, last: prev };
  const n = Number(level);
  if (prev == null || n < prev) return { rung: null, last: n };
  if (n === prev || rungVoided(lines, n)) return { rung: null, last: prev };
  return { rung: n, last: n };
}

/** Where this viewer remembers the achievements it has celebrated (#295): per seat, per self. */
function achieveKey(seat, self) { return "membrane.home.achieve." + String(seat || "") + "." + String(self || ""); }

/** THE ONCE-PER-ACHIEVEMENT RULE (#295): `earned` is the seat's earned list
 *  (status().earned, void-aware; ids or {id}), `last` what this viewer
 *  remembers (a JSON list of ids; null: never here), `oldLevel` the old
 *  per-rung memory (rungToCelebrate's, under rungKey) for a viewer from
 *  before the tree. Each id earned and not remembered celebrates once; a
 *  first visit remembers the backlog and celebrates none -- and a viewer that
 *  celebrated up to rung N before the switch remembers rungs 1..N as seen, so
 *  the rungs it already celebrated never re-fire. An id that leaves the list
 *  (a void) is dropped quietly, so earning it again is news. */
function achievementsToCelebrate(earned, last, oldLevel) {
  let prev = null;
  if (Array.isArray(last)) prev = last.map(String);
  else if (typeof last === "string" && last) {
    try { const v = JSON.parse(last); if (Array.isArray(v)) prev = v.map(String); } catch (e) { prev = null; }
  }
  if (!Array.isArray(earned)) return { ids: [], last: prev };
  const now = earned.filter((e) => e != null).map((e) => String(typeof e === "object" ? e.id : e));
  if (prev == null) {
    const n = oldLevel == null || oldLevel === "" ? NaN : Number(oldLevel);
    if (!isFinite(n)) return { ids: [], last: now };
    prev = Object.keys(ACH_RUNG).filter((k) => ACH_RUNG[k] <= n);
  }
  const seen = new Set(prev);
  return { ids: now.filter((i) => !seen.has(i)), last: now };
}

/** An achievement's words: the notification's; the caption adds "!". */
function achieveText(name, achName) { return (name || SELF_NAME) + " earned " + (achName || ""); }

/** #299: the celebration's question -- an earned achievement opens what it
 *  leads to only once the operator accepts it. */
function acceptAskText(name, achName) {
  return "🏅 " + (name || SELF_NAME) + " earned " + (achName || "") + " -- accept, and open what it leads to?";
}

/** #299: which awaiting achievement to ask about now: the first one this page
 *  was not told "later" about (null: none). */
function acceptToAsk(awaiting, dismissed) {
  if (!Array.isArray(awaiting)) return null;
  const no = new Set((dismissed || []).map(String));
  const id = awaiting.map(String).find((i) => !no.has(i));
  return id == null ? null : id;
}

/** #299: the notification's body for a newly earned achievement. */
function achieveNotifyBody(awaiting) {
  return awaiting ? "it awaits your accept -- open membrane home to accept it and open what it leads to"
                  : "a new achievement -- open membrane home to see it";
}

/** The celebration's caption. */
function celebrateText(name, n, rungName) {
  return rungNotifyText(name, n, rungName) + "!";
}

/** The notification's words. */
function rungNotifyText(name, n, rungName) {
  return (name || SELF_NAME) + " reached rung " + n + ": " + (rungName || BRAIN_RUNGS[n] || "");
}

/** The chime: the bell on, and the page has had a user gesture (autoplay rules). */
function chimeOk(s) { return !!(s && s.on && s.gesture); }

/** The chime's notes (seconds from its start, Hz): a rising major arpeggio, ~1.3 s. */
function chimeNotes() {
  return [{ t: 0, f: 784, dur: 0.5 }, { t: 0.11, f: 988, dur: 0.5 }, { t: 0.22, f: 1175, dur: 0.6 }, { t: 0.36, f: 1568, dur: 0.9 }];
}

/** A notification: the bell on, a secure context, the API, the person's permission. */
function notifyReady(s) {
  return !!(s && s.on && s.secure && s.api && s.permission === "granted");
}

/** The 🔔 toggle: its icon, its title (why notifications can't work here,
 *  when they can't) and whether its next tap asks for permission instead of
 *  muting (a tap is the user gesture the browser wants for asking) -- once:
 *  after that a tap mutes. */
function bellView(s) {
  s = s || {};
  if (!s.on) return { icon: "🔕", on: false, ask: false, title: "chime and notifications (rungs, the ball) off · tap to turn on" };
  const why = !s.api ? "this browser has no notifications"
    : !s.secure ? "notifications need https or localhost"
    : s.permission === "denied" ? "notifications are blocked for this site (the browser's site settings)"
    : s.permission === "granted" ? "notifications on"
    : s.asked ? "notifications not allowed (asked once: the browser's site settings allow them)" : "";
  const ask = !why;
  return { icon: "🔔", on: true, ask,
           title: "chime on (rungs, the ball) · " + (ask ? "tap to allow notifications too" : why + " · tap to mute") };
}

// THE BALL'S ALERT (#331, the operator: "it's just really hard to notice the
// ball move"): self rolling the ball to you raises a toast with a Roll it
// back button, the dock's ball glowing while it is on your side, the bell's
// chime (a short one of its own), the tab title flashing and an OS
// notification while the tab is hidden -- once per roll of self's, never for
// your own roll-back, never loudly for a ball already on your side when the
// page opened (it only glows). Display-only: what self measures is untouched.

/** THE ONCE-PER-ROLL RULE: `toys` the dock's toys, `prev` what this page saw
 *  last ({name: {n, side}}; null: the page's first read). A roll of self's is
 *  its change count on the ball (`changes.self`) going up with the ball on
 *  your side and self its last mover -- and the dock's optimistic roll-back
 *  (#325: the counts untouched) and its undo are none. #340: only a roll that
 *  reaches a person who played since the last one is news -- a fetch-and-re-roll
 *  of a ball you never rolled back stays quiet (it keeps glowing); a real
 *  exchange between two reads (your roll-back, then self's) shows in the other
 *  players' counts. A seat without counts: the side turning to yours.
 *  -> {alert: [names to alert], yours: [names on your side], seen}. */
function ballAlert(prev, toys) {
  const out = { alert: [], yours: [], seen: {} };
  for (const t of Array.isArray(toys) ? toys : []) {
    if (!t || t.kind !== "ball" || t.locked || !t.name) continue;
    const side = t.reading && typeof t.reading === "object" ? t.reading.side : t.reading;
    const c = t.changes && t.changes.self, n = typeof c === "number" && isFinite(c) ? c : null;
    let o = 0;                                       // #340: the people's rolls of it (toybox.NOT_PEOPLE aside)
    for (const [k, v] of Object.entries(t.changes || {}))
      if (!["self", "world", "model"].includes(k) && typeof v === "number" && isFinite(v)) o += v;
    const was = prev && prev[t.name], yours = side === "other";
    out.seen[t.name] = { n, o, side: side == null ? null : String(side) };
    if (!yours) continue;
    out.yours.push(t.name);
    if (!was || !(t.last && t.last.by === "self")) continue;
    const played = was.side !== "other" || o > (was.o || 0);      // #340: you played since the last roll
    if (n != null && was.n != null ? n > was.n && played : was.side !== "other") out.alert.push(t.name);
  }
  return out;
}

/** The toast's, the title's and the notification's words. */
function ballAlertText(name) { return "⚽ " + (name || SELF_NAME) + " rolled you the ball"; }
function ballNotifyBody() { return "your turn: roll it back (membrane home)"; }

/** The ball's chime: two quick notes, a bounce (the rung's arpeggio is longer). */
function ballChimeNotes() {
  return [{ t: 0, f: 1046.5, dur: 0.22 }, { t: 0.16, f: 1568, dur: 0.4 }];
}

/** The hidden tab's title, flashing: the alert on even ticks, the page's own title on odd. */
function titleFlash(base, text, tick) { return (Number(tick) || 0) % 2 ? base : text; }

// THE SEAT'S PAUSE (#348, decided 2026-10-21): the ▶ in the top bar is the
// nerve pause (lanes.nerve_paused, the operator's stable point) -- ▶ while the
// seat runs, ⏸ while it is paused. GET /membrane/seat/pause reads it (on load,
// every PAUSE_POLL_MS -- the typed `pause` / `resume` and the battery move it
// too -- and after every change); a post of {paused} sets it behind the owner's
// door. Never on disk: a requested restart keeps the person's pause (#395, the
// re-exec carries it), a cold start runs. The battery's pause re-derives itself;
// it lifts only above 20% while charging, and a resume over it disarms the
// battery guard until the phone is unplugged again (#405).
// PauseButton takes its button and the two calls, so node drives it with
// stubs (tests/test_nerve_pause_348.py).
const PAUSE_PATH = "/membrane/seat/pause";
const PAUSE_POLL_MS = 15000;

/** The button's face from GET /membrane/seat/pause ({paused, enabled}; null:
 *  not read yet): its icon, title, aria-label and aria-pressed. What a pause
 *  stops is what the seat's lanes do while lanes.nerve_paused() holds. The
 *  icon is what a click does (#382): ⏸ while the seat runs, ▶ while paused. */
function pauseView(st) {
  if (!st) return { icon: "⏸", pressed: "false", label: "the seat's pause",
                    title: "the seat's pause -- reading its state…" };
  if (st.enabled === false) return { icon: "⏸", pressed: "false", label: "the seat's pause (switched off)",
    title: "the pause is switched off on this seat (MEMBRANE_NERVE_PAUSE=0): pausing changes nothing" };
  if (st.paused) return { icon: "▶", pressed: "true", label: "the seat is paused: resume it",
    title: "the seat is paused -- click to resume: its acting lanes wake and what they held runs " +
           "(the loops' queued events, the night's deferred pass, the autonode's backlog). A restart keeps it." };
  return { icon: "⏸", pressed: "false", label: "the seat is running: pause it",
    title: "the seat is running -- click to pause its acting lanes: the loops, the dreamer, the thinker's ticks, " +
           "the night pass, the autonode, the idle judges, speech, " + SELF_NAME + "'s cycle and the scripts' model calls " +
           "stand down, and the LLM's entity and file writes are refused. Reads, memory, intend and your chat stay. " +
           "A restart keeps it; a cold start runs." };
}

class PauseButton {
  /** btn: the top bar's ▶ / ⏸; get(path) -> the JSON; post(path, body) -> the JSON, or throws (the reason). */
  constructor({ btn, get, post }) {
    Object.assign(this, { btn, get, post });
    this.state = null;
    this.busy = false;
    this.paint();
  }

  paint(note) {
    const v = pauseView(this.state), b = this.btn;
    b.textContent = v.icon;
    b.title = note ? v.title + " -- " + note : v.title;
    b.setAttribute("aria-label", v.label);
    b.setAttribute("aria-pressed", v.pressed);
    if (b.classList) b.classList.toggle("on", v.pressed === "true");
  }

  /** Read the seat's pause and show it. -> the state, or null (the reason in the title). */
  async load() {
    try {
      this.state = await this.get(PAUSE_PATH);
      this.paint();
    } catch (e) { this.paint("the seat did not answer (" + (e && e.message || e) + ")"); }
    return this.state;
  }

  /** Pause a running seat, resume a paused one (read first when the state is
   *  unknown), then show what the seat says. -> the new state, or null. */
  async click() {
    if (this.busy) return null;
    this.busy = true;
    try {
      if (!this.state && !(await this.load())) return null;
      try {
        this.state = await this.post(PAUSE_PATH, { paused: !this.state.paused });
        this.paint();
        return this.state;
      } catch (e) {
        this.paint("not changed: " + (e && e.message || e));
        return null;
      }
    } finally { this.busy = false; }
  }
}

// OUT OF TOKENS (membrane/quota.py): a ⚠ beside the ▶/⏸ while any model is
// paused because its quota is spent. Its hover (or a tap) names each paused
// mind -- Chat (a held message), the Goal worker, the Idle thinker -- its
// model, when it resumes, the goals parked on it, and Resume now (a post of
// {model} to /membrane/quota/resume: the pause is cleared and what waited goes;
// a new failure pauses it again with the new time). Gone when all resume.
// One read, GET /membrane/quota, every PAUSE_POLL_MS and on the chat's
// ai-quota event. QuotaBadge takes its elements and the calls, so node
// drives it with stubs (tests/test_quota_ui.py).
const QUOTA_PATH = "/membrane/quota";

/** A resume time (epoch s) against now (ms): {at: "HH:MM", in: "2 h 5 m"} (panel.js's quotaClock). */
function quotaWhen(ts, nowMs) {
  const t = Number(ts) || 0, d = new Date(t * 1000), p2 = (n) => String(n).padStart(2, "0");
  const at = t ? p2(d.getHours()) + ":" + p2(d.getMinutes()) : "?";
  const left = t * 1000 - (Number(nowMs) || 0);
  if (!t || left <= 0) return { at, in: "any moment" };
  const mins = Math.ceil(left / 60000), h = Math.floor(mins / 60), m = mins % 60;
  return { at, in: h ? (m ? h + " h " + m + " m" : h + " h") : m + " m" };
}

/** The ⚠'s face and hover from GET /membrane/quota: {shown, label, html}. */
function quotaBadgeView(st, nowMs) {
  const ps = (st && st.paused) || [];
  if (!ps.length) return { shown: false, label: "", html: "" };
  const label = ps.length === 1 ? ps[0].model + " is out of tokens" : ps.length + " models are out of tokens";
  const rows = ps.map((p) => {
    const w = quotaWhen(p.resumes_at, nowMs);
    const minds = (p.minds || []).length ? p.minds : ["nothing waits on it"];
    const goals = (p.goals || []).map((g) => zooEsc(g.name || g.eid));
    return '<div class="qrow">' + minds.map((m) => '<div class="qmind"><b>' + zooEsc(m) + "</b> · " +
      zooEsc(p.model) + " · resumes at " + zooEsc(w.at) + ' <span class="qin">(in ' + zooEsc(w.in) + ")</span></div>").join("") +
      (goals.length ? '<div class="qgoals">parked: ' + goals.join(", ") + "</div>" : "") +
      ((p.chats || []).length ? '<div class="qgoals">' + zooEsc(p.chats.length) + " chat message" + (p.chats.length > 1 ? "s wait" : " waits") + "</div>" : "") +
      '<button class="qres" data-qresume="' + zooEsc(p.model) + '">Resume now</button></div>';
  });
  return { shown: true, label, html: '<div class="qtitle">⚠ out of tokens</div>' + rows.join("") + '<p class="qmsg" aria-live="polite"></p>' };
}

class QuotaBadge {
  /** wrap: the ⚠'s box (hidden while nothing is paused); btn: the ⚠; pop: its
   *  hover; get(path) / post(path, body) -> the JSON; now() -> ms. */
  constructor({ wrap, btn, pop, get, post, now }) {
    Object.assign(this, { wrap, btn, pop, get, post });
    this.now = now || (() => Date.now());
    this.state = null;
  }

  paint() {
    const v = quotaBadgeView(this.state, this.now());
    this.wrap.hidden = !v.shown;
    if (!v.shown) { this.pop.innerHTML = ""; return; }
    this.btn.title = v.label;
    this.btn.setAttribute("aria-label", v.label + ": what waits");
    this.pop.innerHTML = v.html;
  }

  async load() {
    try { this.state = await this.get(QUOTA_PATH); } catch (e) { return this.state; }
    this.paint();
    return this.state;
  }

  /** Resume now on one model; the answer is the new state. */
  async resume(model) {
    try {
      this.state = await this.post(QUOTA_PATH + "/resume", { model });
      this.paint();
    } catch (e) {
      const m = this.pop.querySelector && this.pop.querySelector(".qmsg");
      if (m) m.textContent = "not resumed: " + (e && e.message || e);
    }
    return this.state;
  }
}

// BLURP'S BODY (#358, the operator liked the film's Blurp): where the page drew
// self as a 👶 -- the tab at the left edge, the panel's header -- and on each
// zoo card, a small canvas draws the film's body: a soft gold ellipse with a
// glow, a slight wobble, two eyes offset toward what it looks at, a blink now
// and then, closed-eye arcs and drifting z's when asleep, a small smile.
// Driven only by what the page already knows: the seat's card (GET
// /membrane/self/card, #283/#345: awake, asleep, energy), else the sleep
// toggle's state, and the look the 3D field shows (its direction on the
// screen; no fresh look: the eyes look forward). The glow scales with energy.
// Reduced motion: one still frame per change. One animation loop for every
// body, running only while one is shown and the tab is visible.
// Visual sizes and speeds only -- nothing here shapes Blurp's behaviour.
const BODY_GOLD = "#d4a017", BODY_LIGHT = "#ffe28c", BODY_INK = "#1b1404";   // the page's gold (the exp bar, the celebration)
const BODY_GLOW_MIN = 0.3;          // the glow at energy 0, as a share of the film's
const BODY_EYE_K = 1.25;            // the eyes a little bigger than the film's: they are read at icon size
const BODY_CARD_MS = 15000;         // how often the page re-reads the card while visible

/** The pose from the seat's card (or, before it loads, the sleep toggle's
 *  view) and a look angle on the screen (null: forward). asleep: real sleep
 *  (the card's awake false with the mode on, else the zoo's state); off (#516):
 *  switched off (mode off), drawn blank and dim, never as sleep; glow: 1 at full
 *  energy down to BODY_GLOW_MIN at none; no energy on the card: the film's 1. */
function blurpPose(card, view, look) {
  let asleep = false, off = false, glow = 1;
  if (card && typeof card === "object") {
    // #516: switched off (mode off) is its own pose, not sleep; real sleep keeps the closed eyes
    if (typeof card.awake === "boolean") { off = !card.awake && card.mode === "off"; asleep = !card.awake && !off; }
    else if (card.state) { off = card.state === "off"; asleep = card.state === "asleep"; }
    else { off = card.mode === "off"; asleep = !off && card.asleep === true; }
    if (typeof card.energy === "number" && isFinite(card.energy)) {
      const e = Math.max(0, Math.min(1, card.energy));
      glow = BODY_GLOW_MIN + (1 - BODY_GLOW_MIN) * e;
    }
  } else if (view && view.loaded) off = !view.on;
  return { asleep, off, glow, look: typeof look === "number" && isFinite(look) ? look : null };
}

/** The eyes' offset toward a look angle (the film's s*.1 across, s*.08 down); none: forward. */
function blurpEyes(look, s) {
  if (typeof look !== "number" || !isFinite(look)) return { dx: 0, dy: 0 };
  return { dx: Math.cos(look) * s * 0.1, dy: Math.sin(look) * s * 0.08 };
}

/** The screen direction from self to what it looks at (radians), or null
 *  (either off screen, or the look on top of self). */
function blurpLookAngle(from, to) {
  if (!from || !to) return null;
  const dx = to[0] - from[0], dy = to[1] - from[1];
  if (Math.hypot(dx, dy) < 1) return null;
  return Math.atan2(dy, dx);
}

/** The film's blink: an eye's height factor, 0.15 for a moment now and then. */
function blurpBlink(t, side) { return Math.sin(t * 1.3 + side) > 0.985 ? 0.15 : 1; }

/** The z's over a sleeping body, in body units from its centre: three rising
 *  and fading on a 3 s cycle; still: one, half way up. */
function blurpZs(t, still) {
  const at = (p) => ({ dx: 0.9 + p * 0.5, dy: -0.6 - p * 1.1, a: Math.sin(p * Math.PI), k: 0.45 + 0.35 * p });
  if (still) return [at(0.5)];
  return [0, 1, 2].map((k) => at(((t / 3 + k / 3) % 1 + 1) % 1));
}

/** Animate only while a body is shown, the tab visible and motion allowed. */
function blurpRuns(docHidden, shown, still) { return !docHidden && shown > 0 && !still; }

/** Draw the body on a 2D context w x h (css px), at time t (s). o: {still,
 *  z (the z's colour: the page's text colour)}. The film's blurp(), sized to the box. */
function drawBlurp(g, w, h, t, pose, o) {
  o = o || {};
  const still = !!o.still, p = pose || {}, sleepy = p.asleep ? 1 : 0, off = !!p.off;
  const glow = typeof p.glow === "number" ? p.glow : 1;
  const tt = still ? 0 : t;
  const s = Math.min(w, h) * 0.34, x = w / 2, y = h * 0.57;
  const wob = still || off ? 0 : Math.sin(tt * 2.2) * (1 - sleepy * 0.7);
  g.save();
  if (off) g.globalAlpha = 0.45;                  // #516: switched off: dim, no glow, no face
  const grd = g.createRadialGradient(x - s * 0.3, y - s * 0.4, s * 0.1, x, y, s * 1.1);
  grd.addColorStop(0, BODY_LIGHT); grd.addColorStop(1, BODY_GOLD);
  g.shadowColor = BODY_GOLD; g.shadowBlur = off ? 0 : s * 0.4 * glow * (1 - sleepy * 0.6);
  g.beginPath(); g.ellipse(x, y + s * 0.012 * wob, s * (1 + 0.016 * wob), s * (0.92 - 0.016 * wob), 0, 0, Math.PI * 2);
  g.fillStyle = grd; g.fill();
  g.shadowBlur = 0;
  if (off) { g.restore(); return; }
  const e = blurpEyes(sleepy ? null : p.look, s);
  for (const sx of [-1, 1]) {
    const cx = x + sx * s * 0.32 + e.dx, cy = y - s * 0.12 + e.dy;
    if (sleepy) {
      g.beginPath(); g.arc(cx, cy, s * 0.11 * BODY_EYE_K, 0.15 * Math.PI, 0.85 * Math.PI);
      g.lineWidth = Math.max(1, s * 0.05); g.strokeStyle = BODY_INK; g.stroke();
    } else {
      const blink = still ? 1 : blurpBlink(tt, sx);
      g.beginPath(); g.ellipse(cx, cy, s * 0.1 * BODY_EYE_K, s * 0.13 * BODY_EYE_K * blink, 0, 0, Math.PI * 2);
      g.fillStyle = BODY_INK; g.fill();
    }
  }
  g.beginPath(); g.arc(x, y + s * 0.22, s * 0.12 * BODY_EYE_K, 0.15 * Math.PI, 0.85 * Math.PI);
  g.lineWidth = Math.max(1, s * 0.045); g.strokeStyle = BODY_INK; g.stroke();
  if (sleepy) {
    g.fillStyle = o.z || BODY_GOLD; g.textAlign = "center"; g.textBaseline = "middle";
    for (const z of blurpZs(tt, still)) {
      g.globalAlpha = Math.max(0.15, z.a);
      g.font = "600 " + Math.max(6, s * z.k * 1.4).toFixed(1) + "px sans-serif";
      g.fillText("z", x + z.dx * s, y + z.dy * s);
    }
    g.globalAlpha = 1;
  }
  g.restore();
}

/** Every body on the page and the one loop that draws them. env (the page's,
 *  or stubs under node): raf / caf, hidden() (the tab), still() (reduced
 *  motion), now() (ms), observe(el, cb(shown)) -> unobserve, colours(el) -> {z}. */
class BlurpBodies {
  constructor(env) {
    this.env = env;
    this.items = new Map();
    this._raf = 0;
  }

  add(cv, pose) {
    if (!cv || this.items.has(cv)) { if (cv) this.set(cv, pose); return; }
    const it = { pose: pose || blurpPose(null), shown: false, off: null };
    this.items.set(cv, it);
    it.off = this.env.observe(cv, (shown) => { it.shown = !!shown; if (shown) this._paint(cv, it); this.kick(); });
  }

  /** A new pose: a still page redraws it at once (a moving one on its next frame). */
  set(cv, pose) {
    const it = this.items.get(cv); if (!it) return;
    pose = pose || blurpPose(null);
    const same = it.pose && it.pose.asleep === pose.asleep && it.pose.off === pose.off && it.pose.glow === pose.glow && it.pose.look === pose.look;
    it.pose = pose;
    if (!same && it.shown && !this._raf) this._paint(cv, it);
  }

  drop(cv) {
    const it = this.items.get(cv); if (!it) return;
    if (it.off) it.off();
    this.items.delete(cv);
    this.kick();
  }

  /** Start or stop the loop for the page as it is now (the tab shown / hidden, a body shown / hidden). */
  kick() {
    let shown = 0;
    for (const it of this.items.values()) if (it.shown) shown++;
    const run = !this._paused && blurpRuns(this.env.hidden(), shown, this.env.still());
    if (run && !this._raf) this._raf = this.env.raf((ts) => this._frame(ts));
    else if (!run && this._raf) { this.env.caf(this._raf); this._raf = 0; }
    if (!run) for (const [cv, it] of this.items) if (it.shown) this._paint(cv, it);
  }

  /** The page element left the document (true) or came back (false). */
  pause(on) { this._paused = !!on; this.kick(); }

  _frame() {
    this._raf = 0;
    for (const [cv, it] of this.items) if (it.shown) this._paint(cv, it);
    this.kick();
  }

  _paint(cv, it) {
    const g = cv.getContext && cv.getContext("2d"); if (!g) return;
    const w = cv.clientWidth || cv.width, h = cv.clientHeight || cv.height;
    const dpr = cv.width && w ? cv.width / w : 1;
    if (g.setTransform) g.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (g.clearRect) g.clearRect(0, 0, w, h);
    drawBlurp(g, w, h, this.env.now() / 1000, it.pose, Object.assign({ still: this.env.still() }, this.env.colours(cv)));
  }
}

const HomeLogic = { knownView, standaloneMenu, pauseView, PauseButton, PAUSE_PATH, quotaWhen, quotaBadgeView, QuotaBadge, cubePoints, catchUp, personSlug, menuEntries, hotword, pickVoice, speakable, cleanTranscript, WAKE_RE, NAME_ANY_RE, downsample, wavBytes, speechChunks, tiltAxes, tiltOrbit, nextMode, FOLLOW_MODES, throughCenter, chaseAngles, angleStep,
  brainLabel, brainCaption, unlockText, setSelfName, brainKey, brainFresh, actableLine, ACT_FRESH_S, brainTrail, trailAlpha, frameRadius, selfFrame, frameDistance, dampTo,
  freshLook, followsNow, followRates, SELF_HOLD_MS, lookAngles, selfCamAngles,
  followLabel, modeAfterTouch, eyesTarget, turnToward, eyesOrbit, zooAgo, zooCard, zooCards, selfCodeHtml,
  rungKey, rungVoided, rungToCelebrate, celebrateText, rungNotifyText, chimeOk, chimeNotes, notifyReady, bellView,
  achieveKey, achievementsToCelebrate, achieveText, acceptAskText, acceptToAsk, achieveNotifyBody,
  ballAlert, ballAlertText, ballNotifyBody, ballChimeNotes, titleFlash,
  optSharePct, optShareFromPct, optionsRows, backgroundRows, OptionsWindow,
  tokShort, statsTable, statsRows, StatsWindow,
  blurpPose, blurpEyes, blurpLookAngle, blurpBlink, blurpZs, blurpRuns, drawBlurp, BlurpBodies };
if (typeof module !== "undefined" && module.exports) module.exports = HomeLogic;

const HSTYLE = `
  :host { display:block; position:relative; height:100vh; height:100dvh; overflow:hidden;
          background:var(--primary-background-color,#0b0e14); color:var(--primary-text-color,#ddd);
          font:14px/1.5 system-ui,-apple-system,sans-serif; }
  * { box-sizing:border-box; }
  button { font:inherit; color:inherit; cursor:pointer; }
  .field { position:absolute; inset:0; }
  /* no backdrop-filter: a blur over a live WebGL canvas is re-computed every
     frame for every glass box and saturated the Mac's compositor (2026-10-10) */
  .glass { background:color-mix(in srgb, var(--card-background-color,#111) 80%, transparent);
           border:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); }
  .top { position:absolute; top:12px; left:12px; z-index:3; display:flex; gap:6px; align-items:center;
         padding:4px; border-radius:999px; max-width:calc(100% - 24px); }
  .top button { border:0; background:transparent; border-radius:999px; padding:6px 12px; white-space:nowrap; }
  .top button.on { background:color-mix(in srgb, var(--primary-color,#3477d6) 28%, transparent); }
  /* hover only where there is one: a phone keeps :hover on the last tapped
     button, which looked like 📱 stayed on after it was switched off */
  @media (hover: hover) { .top button:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 28%, transparent); } }
  .top .ha { font-size:18px; line-height:1; padding:3px 7px; display:inline-flex; align-items:center; }
  .top .ha .seat-logo { width:26px; height:26px; display:block; pointer-events:none; }
  .hamenu { position:absolute; z-index:6; top:58px; left:12px; min-width:220px; max-width:calc(100% - 24px);
            max-height:70vh; max-height:70dvh; overflow:auto; border-radius:14px; padding:6px; display:none; }
  .hamenu.open { display:block; }
  .hamenu a { display:flex; align-items:center; gap:12px; padding:8px 12px; border-radius:10px; color:inherit;
              text-decoration:none; white-space:nowrap; }
  @media (hover: hover) { .hamenu a:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 25%, transparent); } }
  .hamenu a:focus-visible { background:color-mix(in srgb, var(--primary-color,#3477d6) 25%, transparent); outline:none; }
  .hamenu ha-icon { --mdc-icon-size:20px; opacity:.85; }
  .hamenu .mi { width:20px; text-align:center; font-size:15px; opacity:.9; }
  .hamenu hr { border:0; border-top:1px solid color-mix(in srgb, var(--divider-color,#888) 50%, transparent); margin:4px 6px; }
  .sbox { position:relative; }
  .sbox input { width:180px; border:0; border-radius:999px; padding:6px 12px; font:inherit; color:inherit; outline:none;
                background:color-mix(in srgb, var(--secondary-background-color,#222) 70%, transparent); }
  .hits { position:absolute; top:36px; left:0; width:300px; max-height:50vh; overflow:auto; display:none; z-index:5;
          border-radius:12px; padding:4px; }
  .hits.open { display:block; }
  .hit { padding:5px 8px; border-radius:8px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .hit:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 25%, transparent); }
  .hit .sw { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
  .hit .k { opacity:.5; font-size:12px; margin-left:4px; }
  .status { font-size:12px; opacity:.7; padding:0 10px 0 4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; min-width:0; }
  .chat { position:absolute; z-index:2; top:12px; right:12px; bottom:12px; width:min(460px, 44vw);
          border-radius:16px; overflow:hidden; display:flex; flex-direction:column;
          transition:transform .2s ease, opacity .2s ease; }
  .chat.hidden { transform:translateX(calc(100% + 24px)); opacity:0; pointer-events:none; }
  .chat ai-terminal-panel { flex:1; min-height:0; height:auto; }
  /* THE VIEW (2026-10-13, the operator's burger-menu ask): chat-only fills
   * the screen with the conversation -- nothing but field chrome left */
  .view-chat .chat { left:12px; width:auto; }
  .view-chat .sbox, .view-chat .me, .view-chat .chatbtn,
  .view-chat .card, .view-chat .tip, .view-chat .field { display:none; }
  /* #470: the decision cockpit's view -- the cockpit under the top bar, the
   * chat inside it (moved there, the same element); the field stays the
   * full-page background behind it all, and takes no input there */
  .view-field .chat, .view-field .sbox, .view-field .me, .view-field .chatbtn,
  .view-field .card, .view-field .tip, .view-field .brainline { display:none; }
  .view-field .msg { display:none !important; }
  .view-field .field { pointer-events:none; background:radial-gradient(ellipse at 50% 46%, #0d1a28 0%, #060a10 62%, #04060a 100%); }   /* the 3D view's camera hints mean nothing here */
  .tip { position:absolute; z-index:4; pointer-events:none; display:none; padding:4px 8px; border-radius:8px;
         font-size:12px; max-width:320px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .card { position:absolute; z-index:3; left:12px; bottom:12px; max-width:min(420px, calc(100% - 24px));
          padding:10px 12px; border-radius:12px; display:none; }
  .card.open { display:block; }
  .card .t { font-weight:600; overflow-wrap:anywhere; }
  .card .k { font-size:12px; opacity:.65; }
  .card .row { display:flex; gap:6px; margin-top:8px; flex-wrap:wrap; }
  .card button { border:0; border-radius:8px; padding:4px 10px;
                 background:color-mix(in srgb, var(--primary-color,#3477d6) 30%, transparent); }
  .voice { position:absolute; z-index:3; left:12px; bottom:12px; display:flex; gap:6px; padding:5px; border-radius:999px; }
  .voice button { width:46px; height:46px; border-radius:50%; border:0; font-size:20px; line-height:1; touch-action:none;
                  user-select:none; -webkit-user-select:none; -webkit-touch-callout:none;
                  background:color-mix(in srgb, var(--secondary-background-color,#222) 70%, transparent); }
  .voice button.on { background:color-mix(in srgb, var(--primary-color,#3477d6) 55%, transparent); }
  .voice button.rec { background:var(--error-color,#c0392b); color:#fff; animation:recpulse 1.1s ease-in-out infinite; }
  .voice button.busy { opacity:.6; }
  .vmenu { position:absolute; bottom:calc(100% + 8px); right:0; left:auto; top:auto; max-height:50vh; }
  .voice .vsep { width:1px; align-self:stretch; margin:6px 2px; background:color-mix(in srgb, var(--divider-color,#888) 60%, transparent); }
  .voice button.ear.on { background:color-mix(in srgb, #2e9d5b 60%, transparent); }
  .voice button.ear.hearing { box-shadow:0 0 0 3px color-mix(in srgb, #2e9d5b 55%, transparent); }
  .vsay { position:absolute; left:0; bottom:calc(100% + 8px); max-width:min(360px, calc(100vw - 32px)); padding:6px 10px;
          border-radius:12px; font-size:13px; line-height:1.35; white-space:normal; display:none; pointer-events:none; }
  .vsay.show { display:block; }
  @keyframes recpulse { 50% { box-shadow:0 0 0 8px color-mix(in srgb, var(--error-color,#c0392b) 30%, transparent); } }
  .card { bottom:76px !important; }
  .top .tilt[hidden] { display:none; }
  /* OUT OF TOKENS: the ⚠ beside the pause and its hover */
  .top .qwarn { position:relative; display:inline-flex; }
  .top .qwarn[hidden] { display:none; }
  .top .qbtn { color:var(--warning-color,#e0a030); font-size:16px; padding:4px 9px; }
  .top .qpop { display:none; position:absolute; top:calc(100% + 8px); left:50%; transform:translateX(-50%); z-index:8;
               width:max-content; max-width:min(360px, calc(100vw - 24px)); border-radius:14px; padding:10px 12px;
               background:color-mix(in srgb, var(--card-background-color,#111) 96%, transparent); font-size:13px;
               white-space:normal; box-shadow:0 8px 28px rgba(0,0,0,.4); }
  .top .qwarn::after { content:""; position:absolute; left:0; right:0; top:100%; height:10px; }  /* the hover's bridge */
  .top .qwarn:hover .qpop, .top .qwarn:focus-within .qpop, .top .qwarn.open .qpop { display:block; }
  .top .qpop .qtitle { font-weight:600; margin-bottom:6px; color:var(--warning-color,#e0a030); }
  .top .qpop .qrow { padding:6px 0; border-top:1px solid color-mix(in srgb, var(--divider-color,#888) 35%, transparent); }
  .top .qpop .qmind { line-height:1.45; overflow-wrap:anywhere; }
  .top .qpop .qin, .top .qpop .qgoals { opacity:.75; font-size:12px; }
  .top .qpop .qres { margin-top:6px; padding:4px 12px; background:color-mix(in srgb, var(--primary-color,#3477d6) 30%, transparent); }
  .top .qpop .qmsg { margin:4px 0 0; font-size:12px; opacity:.75; }
  .top .qpop .qmsg:empty { display:none; }
  .top .selfstat { font-size:12px; opacity:.85; white-space:nowrap; padding-right:6px; }
  .view-chat .selfstat { display:none; }
  /* THE WAVE (2026-10-10, the operator: "a wavy animation like Siri, but
     angly and graph-y"): a chain of nodes joined by straight edges -- a strip
     of the field -- that moves with the voice: your mic level while
     listening, syllable bursts while he speaks, a slow ripple while he thinks */
  .orb { position:absolute; z-index:4; left:50%; bottom:22px; transform:translateX(-50%); width:280px; height:70px;
         pointer-events:none; opacity:0; transition:opacity .35s ease; }
  .orb.on { opacity:1; }
  .orb canvas { width:100%; height:100%; display:block; }
  /* centred in the field that shows (left of the chat card on wide screens) */
  @media (min-width: 761px) {
    .orb, .orbtext { left:calc((100% - min(460px, 44vw) - 24px) / 2) !important; }
    .chat.hidden ~ .orb, .chat.hidden ~ .orbtext { left:50% !important; }
    .orb { bottom:84px !important; }                     /* above the voice buttons */
    .orbtext { bottom:162px !important; max-width:calc(100% - min(460px, 44vw) - 72px); }
  }
  .orbtext { position:absolute; z-index:4; left:50%; bottom:100px; transform:translateX(-50%); max-width:min(640px, calc(100% - 32px));
             text-align:center; font-size:19px; line-height:1.35; font-weight:500; pointer-events:none; opacity:0;
             transition:opacity .4s ease; text-shadow:0 1px 6px color-mix(in srgb, var(--primary-background-color,#000) 80%, transparent); }
  .orbtext.on { opacity:1; }
  @media (max-width: 760px) { .orb { bottom:calc(56dvh + 70px); width:220px; height:56px; } .orbtext { bottom:calc(56dvh + 132px); font-size:17px; }
                              .chat.hidden ~ .orb { bottom:84px; } }
  .msg { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; z-index:1;
         pointer-events:none; opacity:.6; white-space:pre-line; text-align:center; padding:24px; }
  @media (max-width: 760px) {
    .chat { top:auto; left:8px; right:8px; bottom:8px; width:auto; height:56vh; height:56dvh; }
    .voice { bottom:calc(56dvh + 16px); }
    .chat.hidden ~ .voice { bottom:12px; }
    .view-field .voice { bottom:12px; }   /* #470: the cockpit's chat is inside it, no sheet to clear */
    .card { bottom:calc(56dvh + 80px) !important; }
    .chat.hidden ~ .card { bottom:76px !important; }
    .status { display:none; }
    .sbox input { width:110px; }
    .top { max-width:calc(100% - 24px) !important; }
  }
  .top { max-width:calc(100% - min(460px, 44vw) - 36px); flex-wrap:wrap; }
  .top:has(~ .chat.hidden) { max-width:calc(100% - 24px); }
  /* the 👶 panel (#230): a tab at the left edge (clear of the notch) opens
     self's side panel -- a floating column on wide screens, an overlay on narrow */
  .babybtn { position:absolute; z-index:8; left:max(0px, env(safe-area-inset-left)); top:calc(50% - 24px);
             width:40px; height:48px; padding:0; font-size:22px; line-height:1; border-radius:0 14px 14px 0;
             border:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); border-left:0;
             background:color-mix(in srgb, var(--card-background-color,#111) 80%, transparent); }
  /* #358: Blurp's body (a canvas) where the 👶 was; z's take the page's text colour */
  .bbody { display:block; width:36px; height:36px; margin:auto; color:var(--primary-text-color,#e6e6e6); pointer-events:none; }
  .bbody.sm { display:inline-block; width:26px; height:26px; margin:-6px 2px -8px -4px; vertical-align:middle; }
  .zoopanel .zbody { flex:none; width:24px; height:24px; margin:-4px 0; color:var(--primary-text-color,#e6e6e6); }
  .babybtn.on { background:color-mix(in srgb, var(--primary-color,#3477d6) 35%, var(--card-background-color,#111)); }
  @media (hover: hover) { .babybtn:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 28%, var(--card-background-color,#111)); } }
  .babybtn:focus-visible, .selfpanel .spx:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
  .selfpanel { position:absolute; z-index:5; top:64px; bottom:76px; left:calc(max(0px, env(safe-area-inset-left)) + 48px);
               width:min(420px, calc(100% - 72px)); border-radius:16px; padding:10px 14px 14px; overflow:auto;
               overscroll-behavior:contain; font-size:13px; line-height:1.45;
               background:color-mix(in srgb, var(--card-background-color,#111) 95%, transparent); }
  .selfpanel[hidden], .view-chat .babybtn, .view-chat .selfpanel { display:none; }
  @media (prefers-reduced-motion: no-preference) {
    .selfpanel { animation:spin .18s ease-out; }
    @keyframes spin { from { opacity:0; transform:translateX(-10px); } }
  }
  @media (max-width: 760px) {
    .babybtn { top:26vh; top:26dvh; }
    .selfpanel { z-index:7; inset:0; width:auto; border-radius:0; border:0; background:var(--card-background-color,#111);
                 padding:calc(12px + env(safe-area-inset-top)) calc(14px + env(safe-area-inset-right))
                         calc(16px + env(safe-area-inset-bottom)) calc(52px + env(safe-area-inset-left)); }
    .top .selfstat .xp-sub { display:none; }
    .top .selfstat .xp-lv { max-width:7em; }
  }
  /* the 🦠 zoo (#283): this house's seats, a card each, under the top bar */
  .zoopanel { position:absolute; z-index:6; top:64px; left:12px; width:min(360px, calc(100% - 24px));
              max-height:calc(100% - 150px); overflow:auto; overscroll-behavior:contain; border-radius:16px; padding:8px 10px 10px;
              background:color-mix(in srgb, var(--card-background-color,#111) 95%, transparent); font-size:13px; }
  .zoopanel[hidden], .top .zoobtn[hidden] { display:none; }
  /* the ⚙ options window (#344): the seat's long-term settings, a window in the
     middle of the view; the page behind it dims (a shadow, never a blur: see .glass) */
  .optpanel { position:absolute; z-index:6; top:50%; left:50%; transform:translate(-50%, -50%);
              width:min(460px, calc(100% - 32px)); max-height:calc(100% - 96px); overflow:auto; overscroll-behavior:contain;
              border-radius:18px; padding:14px 18px 16px; font-size:13.5px; line-height:1.4;
              background:color-mix(in srgb, var(--card-background-color,#111) 97%, transparent);
              box-shadow:0 0 0 100vmax rgba(0,0,0,.38), 0 18px 48px rgba(0,0,0,.45); }
  .optpanel[hidden] { display:none; }
  .optpanel .ohead { display:flex; align-items:center; gap:8px; padding:0 0 10px; font-size:16px; font-weight:650;
                     border-bottom:1px solid color-mix(in srgb, var(--divider-color,#888) 35%, transparent); }
  .optpanel .ox, .optpanel .stx { margin-left:auto; border:0; background:transparent; font-size:22px; line-height:1; padding:2px 8px;
                                  border-radius:10px; opacity:.8; }
  @media (hover: hover) { .optpanel .ox:hover, .optpanel .stx:hover { opacity:1; background:color-mix(in srgb, var(--divider-color,#888) 22%, transparent); } }
  .optpanel .orow { display:grid; grid-template-columns:1fr auto; gap:4px 12px; align-items:center; padding:12px 2px; }
  .optpanel .orow + .orow { border-top:1px solid color-mix(in srgb, var(--divider-color,#888) 22%, transparent); }
  .optpanel .olab { font-weight:600; min-width:0; overflow-wrap:anywhere; }
  .optpanel .oval { font-variant-numeric:tabular-nums; white-space:nowrap; padding:2px 8px; border-radius:999px;
                    background:color-mix(in srgb, var(--divider-color,#888) 18%, transparent); }
  .optpanel .oshare { grid-column:1 / -1; width:100%; min-width:0; margin:6px 0 2px; accent-color:var(--primary-color,#3477d6); }
  .optpanel .ohint { grid-column:1 / -1; margin:0; font-size:12px; opacity:.68; }
  .optpanel .osec { margin:18px 2px 2px; padding-top:12px; font-size:11.5px; font-weight:650; text-transform:uppercase;
                    letter-spacing:.06em; opacity:.7;
                    border-top:1px solid color-mix(in srgb, var(--divider-color,#888) 35%, transparent); }
  .optpanel .osel { min-width:0; max-width:220px; font:inherit; color:inherit; padding:5px 8px; border-radius:8px;
                    background:color-mix(in srgb, var(--divider-color,#888) 14%, transparent);
                    border:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); }
  .optpanel .osub { font-weight:400; opacity:.85; margin-top:4px; }
  .optpanel .onum { width:84px; font:inherit; color:inherit; padding:5px 8px; border-radius:8px; text-align:right;
                    font-variant-numeric:tabular-nums; background:color-mix(in srgb, var(--divider-color,#888) 14%, transparent);
                    border:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); }
  .optpanel .osel:focus-visible, .optpanel .onum:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
  .optpanel .omsg, .optpanel .stmsg { margin:4px 2px 0; font-size:12px; opacity:.85; }
  .optpanel .omsg:empty, .optpanel .stmsg:empty { display:none; }
  /* the 📊 stats window: the options window's frame, with tables */
  .statpanel { width:min(420px, calc(100% - 32px)); }
  .statpanel .stbl { width:100%; border-collapse:collapse; margin:4px 0 2px; font-size:12px; font-variant-numeric:tabular-nums; }
  .statpanel .stbl th, .statpanel .stbl td { padding:3px 4px; text-align:right; white-space:nowrap; }
  .statpanel .stbl th[scope="row"], .statpanel .stbl thead th:first-child { text-align:left; font-weight:600;
                                       max-width:150px; overflow:hidden; text-overflow:ellipsis; }
  .statpanel .stbl thead th { font-weight:400; opacity:.7; }
  .statpanel .stbl tbody tr { border-top:1px solid color-mix(in srgb, var(--divider-color,#888) 30%, transparent); }
  .statpanel .stbl td.hot { font-weight:700; color:var(--warning-color,#e8a33d); }
  .statpanel .ohint { margin:2px 2px 6px; }
  .optpanel .stx:focus-visible, .optpanel .ox:focus-visible, .optpanel .oshare:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
  .zoopanel .zhead { display:flex; align-items:center; gap:8px; padding:2px 2px 6px; font-weight:600; }
  .zoopanel .zx { margin-left:auto; border:0; background:transparent; font-size:20px; line-height:1; padding:2px 8px; border-radius:8px; }
  .zoopanel .zcard { display:flex; flex-direction:column; gap:2px; width:100%; text-align:left; margin:0 0 6px; padding:8px 10px;
                     border-radius:12px; border:1px solid color-mix(in srgb, var(--divider-color,#888) 40%, transparent);
                     background:transparent; }
  .zoopanel .zcard.active { border-color:var(--primary-color,#3477d6);
                            background:color-mix(in srgb, var(--primary-color,#3477d6) 18%, transparent); cursor:default; }
  .zoopanel .zcard.off { opacity:.65; }
  @media (hover: hover) { .zoopanel .zcard:not(.active):hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 12%, transparent); } }
  .zoopanel .zcard:focus-visible, .zoopanel .zx:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
  .zoopanel .zrow { display:flex; align-items:center; gap:8px; }
  .zoopanel .zname { font-weight:600; font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .zoopanel .zlv { margin-left:auto; font-size:12px; opacity:.85; white-space:nowrap; }
  .zoopanel .dot { width:8px; height:8px; border-radius:50%; flex:none; background:#8a8f98; }
  .zoopanel .dot.on { background:#2e9d5b; }
  .zoopanel .zsub, .zoopanel .zseat { font-size:12px; opacity:.8; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .zoopanel .zseat { opacity:.6; }
  .zoopanel .zmsg { margin:4px 2px 0; font-size:12px; opacity:.8; }
  .zoopanel .zmsg:empty, .zoopanel .znone:empty { display:none; }
  @media (max-width: 760px) {
    .optpanel { z-index:7; inset:0; transform:none; width:auto; max-height:none; border-radius:0; border:0; box-shadow:none;
                background:var(--card-background-color,#111);
                padding:calc(12px + env(safe-area-inset-top)) calc(16px + env(safe-area-inset-right))
                        calc(16px + env(safe-area-inset-bottom)) calc(16px + env(safe-area-inset-left)); }
    .zoopanel { z-index:7; inset:0; width:auto; max-height:none; border-radius:0; border:0; background:var(--card-background-color,#111);
                padding:calc(12px + env(safe-area-inset-top)) calc(14px + env(safe-area-inset-right))
                        calc(16px + env(safe-area-inset-bottom)) calc(14px + env(safe-area-inset-left)); }
  }
  .selfpanel .sphead { display:flex; align-items:center; gap:8px; position:sticky; top:-10px; padding:4px 0 6px;
                       background:inherit; z-index:1; }
  .selfpanel .sptitle { font-weight:600; font-size:14px; }
  .selfpanel .spx { margin-left:auto; border:0; background:transparent; font-size:20px; line-height:1; padding:2px 8px; border-radius:8px; }
  /* #299 / #460: Blurp's on/off switch, a plain toggle (the 🌱 growth-limit chooser gave way to acceptance) */
  .selfpanel .spsleep { border:1px solid color-mix(in srgb, var(--divider-color,#888) 60%, transparent); background:transparent; color:inherit;
                        font:inherit; font-size:12px; border-radius:999px; padding:3px 10px; cursor:pointer; white-space:nowrap; }
  .selfpanel .spsleep[aria-pressed="true"] { border-color:#5ad17a; }
  .selfpanel .spsleep:disabled { opacity:.5; cursor:default; }
  .selfpanel .spsleep:focus-visible, .celebrate .cb-acc button:focus-visible, .sktree .skaccept:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
  .sktree .sknode.awaiting { border-color:var(--sk-earned); border-style:dashed;
                             background:color-mix(in srgb, var(--sk-earned) 12%, var(--card-background-color,#111)); }
  .sktree .skaccept { margin-top:8px; border:0; border-radius:999px; padding:6px 14px; font:inherit; font-weight:600; cursor:pointer;
                      color:#1b1404; background:linear-gradient(135deg, #ffe28c, #d4a017); }
  .sktree .skaccept:disabled { opacity:.6; cursor:default; }
  .sktree .skamsg { margin-top:4px; font-size:12px; }
  .selfpanel .spxp .xp { display:grid; grid-template-columns:auto 1fr auto; width:100%; }
  .selfpanel .spxp .xp-track { height:10px; }
  .selfpanel .spxp .xp-lv { max-width:none; }
  .selfpanel .spxp .xp-sub { white-space:normal; font-size:11.5px; }
  .selfpanel h3 { font-size:11px; letter-spacing:.06em; text-transform:uppercase; opacity:.6; margin:16px 0 6px; font-weight:600; }
  .selfpanel h4 { font-size:12px; margin:12px 0 4px; font-weight:600; opacity:.85; }
  .selfpanel p { margin:0; }
  .selfpanel .muted { opacity:.6; }
  .selfpanel ol, .selfpanel ul { margin:0; padding:0 0 0 1.4em; }
  .selfpanel .spact { list-style:none; padding:0; font:12px/1.45 var(--code-font-family, ui-monospace, Menlo, monospace); }
  .selfpanel .spact .sl { padding:2px 0; border-bottom:1px solid color-mix(in srgb, var(--divider-color,#888) 35%, transparent); overflow-wrap:anywhere; }
  .selfpanel .spact .ago { display:inline-block; min-width:3.2em; opacity:.5; }
  .selfpanel .spact .k-rung { color:#d4a017; font-weight:600; }
  .selfpanel .spact .k-error { color:var(--error-color,#db4437); }
  .selfpanel .spact .k-shift { color:var(--warning-color,#ffa600); }
  .selfpanel .spcard .head { opacity:.75; font-size:12px; }
  .selfpanel .ladder { list-style:none; padding:0; }
  .selfpanel .ladder li { display:flex; gap:8px; padding:1px 0; }
  .selfpanel .ladder .rn { width:1.6em; text-align:right; opacity:.55; font-variant-numeric:tabular-nums; }
  .selfpanel .ladder .rs { margin-left:auto; font-size:11px; opacity:.75; white-space:nowrap; }
  .selfpanel .ladder .st-passed .rs { color:#2e9d5b; opacity:1; }
  .selfpanel .ladder .st-current { font-weight:600; }
  .selfpanel .ladder .st-slipping .rs { color:var(--warning-color,#ffa600); opacity:1; }
  .selfpanel .ladder .st-locked, .selfpanel .ladder .st-unbuilt { opacity:.45; }
  .selfpanel .toys { list-style:none; padding:0; }
  .selfpanel .toys li { padding:1px 0; }
  .selfpanel .play { display:inline-flex; gap:4px; margin-left:4px; vertical-align:middle; }
  .selfpanel .pk { font-size:11px; line-height:1.2; padding:2px 7px; border-radius:999px;
                   border:1px solid color-mix(in srgb, var(--divider-color,#888) 70%, transparent);
                   background:color-mix(in srgb, var(--primary-color,#3477d6) 14%, transparent); }
  .selfpanel .pk:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 30%, transparent); }
  .selfpanel .pk:disabled { opacity:.5; cursor:progress; }
  .selfpanel .pk:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:1px; }
  .selfpanel .spplay { position:sticky; bottom:-10px; z-index:1; margin:8px 0 0; font-size:12px; padding:4px 8px; border-radius:8px;
                       box-shadow:0 2px 8px rgba(0,0,0,.25); overflow-wrap:anywhere;
                       background:color-mix(in srgb, #2e9d5b 22%, var(--card-background-color,#111)); }
  .selfpanel .spplay.bad { background:color-mix(in srgb, var(--error-color,#db4437) 22%, var(--card-background-color,#111)); }
  .selfpanel .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle; }
  .selfpanel .dot.ok { background:#2e9d5b; }
  .selfpanel .dot.warn { background:var(--warning-color,#ffa600); }
  .selfpanel .dot.bad { background:var(--error-color,#db4437); }
  .selfpanel .dot.idle { background:color-mix(in srgb, var(--primary-text-color,#ddd) 35%, transparent); }
  /* the character sheet (#249): an RPG sheet's structure in membrane's own
     look -- a nameplate, then collapsible sections */
  .selfpanel { --sheet-child:#8e5bd6; --sheet-ok:#2e9d5b; }
  .selfpanel .sheet .hdr { display:grid; grid-template-columns:1fr auto; align-items:center; gap:3px 10px; padding:10px 12px;
                           margin:2px 0 8px; border-radius:12px;
                           border:1px solid color-mix(in srgb, var(--divider-color,#888) 60%, transparent);
                           background:color-mix(in srgb, var(--primary-color,#3477d6) 8%, transparent); }
  .selfpanel .sheet .hdr .nm { font-size:20px; font-weight:650; }
  .selfpanel .sheet .hdr .lvb { font-weight:700; font-variant-numeric:tabular-nums; padding:1px 10px; border-radius:999px;
                                background:var(--primary-color,#3477d6); color:var(--text-primary-color,#fff); }
  .selfpanel .sheet .hdr .rnm, .selfpanel .sheet .hdr .xpslot, .selfpanel .sheet .hdr .facts { grid-column:1 / -1; }
  .selfpanel .sheet .hdr .rnm { font-size:12.5px; }
  .selfpanel .sheet .hdr .xpslot .xp-lv, .selfpanel .sheet .hdr .xpslot .xp-sk { display:none; }   /* the nameplate says them */
  .selfpanel .sheet .hdr .xpslot .xp { grid-template-columns:1fr auto; }
  .selfpanel .sheet .hdr .facts { display:flex; flex-wrap:wrap; gap:2px 12px; font-size:12px; opacity:.85; font-variant-numeric:tabular-nums; }
  /* #359: the Sleep block -- the film's sleep scene: rounded bars, energy in
     the page's gold, the others the field (primary) colour, the lowest in the
     warning colour */
  .selfpanel .sheet .sleep { margin:0 0 8px; padding:8px 12px 10px; border-radius:12px;
                             border:1px solid color-mix(in srgb, var(--divider-color,#888) 60%, transparent); }
  .selfpanel .sheet .sleep .sleh { display:flex; align-items:baseline; gap:8px; margin-bottom:4px; }
  .selfpanel .sheet .sleep .slt { font-size:11.5px; letter-spacing:.06em; text-transform:uppercase; font-weight:650; }
  .selfpanel .sheet .sleep .sls { margin-left:auto; font-size:12px; font-weight:600; }
  .selfpanel .sheet .sleep .sls.asleep { opacity:.75; }
  .selfpanel .sheet .sleep .srow { display:grid; grid-template-columns:1fr auto; gap:2px 8px; padding:3px 0; font-size:12px; }
  .selfpanel .sheet .sleep .sval { font-variant-numeric:tabular-nums; font-weight:600; }
  .selfpanel .sheet .sleep .sbar { grid-column:1 / -1; position:relative; height:10px; border-radius:999px; overflow:hidden;
                                   background:color-mix(in srgb, var(--divider-color,#888) 55%, transparent); }
  .selfpanel .sheet .sleep .sfill { position:absolute; left:0; top:0; bottom:0; border-radius:inherit;
                                    background:color-mix(in srgb, var(--primary-color,#3477d6) 75%, transparent); }
  .selfpanel .sheet .sleep .sfill.s-energy { background:var(--xp-gold,#d4a017); }
  .selfpanel .sheet .sleep .sbar.low .sfill { background:var(--warning-color,#ffa600); }
  .selfpanel .sheet .sleep .slow { font-size:10.5px; padding:0 6px; border-radius:999px; margin-left:4px;
                                   color:var(--warning-color,#ffa600); border:1px solid currentColor; }
  .selfpanel .sheet .sleep .spull { margin-top:5px; font-size:12px; font-variant-numeric:tabular-nums; font-weight:600; }
  .selfpanel .sheet .skill { grid-column:1 / -1; display:grid; grid-template-columns:auto 1fr auto; align-items:center; gap:2px 10px;
                             padding:6px 8px; border-radius:10px; background:color-mix(in srgb, var(--primary-text-color,#ddd) 6%, transparent); }
  .selfpanel .sheet .skill .skv { font-size:26px; font-weight:700; font-variant-numeric:tabular-nums; line-height:1.1; }
  .selfpanel .sheet .skill.neg .skv { color:var(--warning-color,#ffa600); }
  .selfpanel .sheet .skill.met .skv { color:var(--sheet-ok); }
  .selfpanel .sheet .skill .skm { font-size:11.5px; line-height:1.35; min-width:0; }
  .selfpanel .sheet .skill .skn { opacity:.7; }
  .selfpanel .sheet .skill .skspark .skl { fill:none; stroke:var(--primary-color,#3477d6); stroke-width:1.8; stroke-linejoin:round; vector-effect:non-scaling-stroke; }
  .selfpanel .sheet .skill .skspark .skp { stroke:currentColor; stroke-opacity:.55; vector-effect:non-scaling-stroke; }
  @media (max-width: 560px) {
    .selfpanel .sheet .skill { grid-template-columns:auto 1fr; }
    .selfpanel .sheet .skill .skspark { grid-column:1 / -1; width:100%; height:30px; }
  }
  .selfpanel .sec { border-top:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); }
  .selfpanel .sec > summary { cursor:pointer; list-style:none; display:flex; align-items:baseline; gap:8px; padding:9px 2px;
                              font-size:11.5px; letter-spacing:.06em; text-transform:uppercase; font-weight:650; }
  .selfpanel .sec > summary::-webkit-details-marker { display:none; }
  .selfpanel .sec > summary::before { content:"▸"; opacity:.6; display:inline-block; transition:transform .15s; }
  .selfpanel .sec[open] > summary::before { transform:rotate(90deg); }
  .selfpanel .sec > summary .sth { margin-left:auto; text-transform:none; letter-spacing:0; font-weight:400; opacity:.6; font-size:11.5px; }
  .selfpanel .sec > summary:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; border-radius:6px; }
  .selfpanel .secb { padding:0 2px 12px; }
  .selfpanel .note { font-size:11px; opacity:.72; margin:2px 0 6px; }
  .selfpanel .attr { display:grid; grid-template-columns:1fr auto; gap:3px 8px; padding:5px 0; }
  .selfpanel .attr .rn { font-size:11px; opacity:.55; }
  .selfpanel .attr .av { font-variant-numeric:tabular-nums; font-weight:600; }
  .selfpanel .attr .av.neg { color:var(--warning-color,#ffa600); }
  .selfpanel .attr .abar { grid-column:1 / -1; position:relative; height:8px; border-radius:999px;
                           background:color-mix(in srgb, var(--divider-color,#888) 55%, transparent); }
  .selfpanel .abar .fill { position:absolute; top:0; bottom:0; border-radius:999px; background:color-mix(in srgb, var(--primary-color,#3477d6) 75%, transparent); }
  .selfpanel .abar .fill.met { background:var(--sheet-ok); }
  .selfpanel .abar .fill.neg { background:var(--warning-color,#ffa600); }
  .selfpanel .abar .zero { position:absolute; top:-2px; bottom:-2px; width:1px; background:currentColor; opacity:.5; }
  .selfpanel .abar .pass { position:absolute; top:-3px; bottom:-3px; width:2px; margin-left:-1px; border-radius:1px;
                           background:var(--primary-text-color,#ddd); opacity:.8; }
  .selfpanel .attr .an { grid-column:1 / -1; font-size:11px; opacity:.72; }
  .selfpanel .attr.st-slipping .an { color:var(--warning-color,#ffa600); opacity:1; }
  .selfpanel .attr.st-locked, .selfpanel .attr.none { opacity:.65; }
  /* #300: the traits' trend lines and the skills it is learning now, side by side */
  .selfpanel .attr .trspark { grid-column:1 / -1; width:100%; height:22px; }
  .selfpanel .trspark .skl { fill:none; stroke:var(--primary-color,#3477d6); stroke-width:1.5; stroke-linejoin:round; vector-effect:non-scaling-stroke; opacity:.8; }
  .selfpanel .learn { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:10px; }
  .selfpanel .lskill { min-width:0; padding:8px 10px; border-radius:10px;
                       border:1px solid color-mix(in srgb, var(--divider-color,#888) 55%, transparent); }
  .selfpanel .lskill .abar { position:relative; height:8px; border-radius:999px; margin:2px 0 6px;
                             background:color-mix(in srgb, var(--divider-color,#888) 55%, transparent); }
  .selfpanel .lskill .skill { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:2px 10px; margin-top:6px;
                              padding:6px 8px; border-radius:10px; background:color-mix(in srgb, var(--primary-text-color,#ddd) 6%, transparent); }
  .selfpanel .lskill .skill .skv { font-size:20px; font-weight:700; font-variant-numeric:tabular-nums; }
  .selfpanel .lskill .skill.neg .skv { color:var(--warning-color,#ffa600); }
  .selfpanel .lskill .skill.met .skv { color:var(--sheet-ok); }
  .selfpanel .lskill .skill .skm { font-size:11.5px; line-height:1.35; min-width:0; }
  .selfpanel .lskill .skill .skspark { grid-column:1 / -1; width:100%; height:28px; }
  .selfpanel .lskill .skspark .skl { fill:none; stroke:var(--primary-color,#3477d6); stroke-width:1.8; stroke-linejoin:round; vector-effect:non-scaling-stroke; }
  .selfpanel .lskill .skspark .skp { stroke:currentColor; stroke-opacity:.55; vector-effect:non-scaling-stroke; }
  .selfpanel .qh { font-weight:600; margin-bottom:4px; }
  .selfpanel .crit { list-style:none; padding:0; }
  .selfpanel .crit li { padding:2px 0; }
  .selfpanel .crit .met .ck { color:var(--sheet-ok); }
  .selfpanel .warn { color:var(--warning-color,#ffa600); }
  .selfpanel .wrap { overflow-x:auto; }
  .selfpanel .tbl { width:100%; border-collapse:collapse; font-size:12px; font-variant-numeric:tabular-nums; }
  .selfpanel .tbl th, .selfpanel .tbl td { padding:3px 4px; text-align:left; vertical-align:top;
                                           border-bottom:1px solid color-mix(in srgb, var(--divider-color,#888) 35%, transparent); }
  .selfpanel .tbl th { font-weight:600; font-size:11px; opacity:.65; }
  .selfpanel .tbl .num, .selfpanel .tbl .dt { text-align:right; white-space:nowrap; }
  .selfpanel .tbl .dt { text-align:left; }
  .selfpanel .curve { display:block; max-width:100%; height:auto; }
  .selfpanel .curve .cg { stroke:color-mix(in srgb, var(--divider-color,#888) 60%, transparent); }
  .selfpanel .curve .ct { fill:currentColor; opacity:.6; font-size:10px; }
  .selfpanel .curve .cf { fill:none; stroke:currentColor; stroke-opacity:.5; stroke-width:1.5; }
  .selfpanel .curve .cw { fill:none; stroke:var(--primary-color,#3477d6); stroke-opacity:.3; stroke-width:1.5; }
  .selfpanel .curve .cs { fill:none; stroke:var(--primary-color,#3477d6); stroke-width:2.2; stroke-linejoin:round; }
  .selfpanel .curve .ck { fill:var(--card-background-color,#111); stroke:var(--sheet-child); stroke-width:1.5; }
  .selfpanel .curve .cn { fill:var(--primary-color,#3477d6); }
  .selfpanel .legend { display:flex; flex-wrap:wrap; align-items:center; gap:4px 10px; font-size:11px; margin:4px 0; }
  .selfpanel .lg { display:inline-block; width:14px; height:3px; margin-right:4px; vertical-align:middle; border-radius:2px; }
  .selfpanel .lg.ls { background:var(--primary-color,#3477d6); }
  .selfpanel .lg.lw { background:color-mix(in srgb, var(--primary-color,#3477d6) 35%, transparent); }
  .selfpanel .lg.lf { background:repeating-linear-gradient(90deg, currentColor 0 4px, transparent 4px 7px); opacity:.6; }
  .selfpanel .lg.lk { width:8px; height:8px; border-radius:50%; border:1.5px solid var(--sheet-child); }
  .selfpanel .tbar { display:flex; gap:2px; height:14px; border-radius:7px; overflow:hidden; }
  .selfpanel .tp-working { background:var(--primary-color,#3477d6); }
  .selfpanel .tp-bugs { background:var(--error-color,#db4437); }
  .selfpanel .tp-walls { background:var(--warning-color,#f4b400); }
  .selfpanel .tp-down { background:color-mix(in srgb, var(--primary-text-color,#ddd) 30%, transparent); }
  .selfpanel .tl { list-style:none; padding:0; margin:6px 0; font-size:12px; }
  .selfpanel .tl .lg { width:10px; height:10px; border-radius:3px; }
  .selfpanel .bugs { font-size:12px; }
  .selfpanel .rel { list-style:none; padding:0; }
  .selfpanel .rel li { padding:4px 0; }
  .selfpanel .now { font-size:10.5px; padding:0 6px; border-radius:999px; background:color-mix(in srgb, var(--sheet-ok) 25%, transparent); }
  @media (prefers-reduced-motion: reduce) { .selfpanel .sec > summary::before { transition:none; } }
  /* the Skill tree tab (#296): the panel's tabs, then self's achievements as
     an RPG skill tree -- lanes by branch, edges from the prerequisites */
  .selfpanel .sptabs { display:flex; gap:4px; margin:0 0 8px; border-bottom:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); }
  .selfpanel .sptab { border:0; background:transparent; color:inherit; font:inherit; font-weight:600; padding:6px 10px;
                      border-bottom:2px solid transparent; margin-bottom:-1px; opacity:.65; cursor:pointer; }
  .selfpanel .sptab[aria-selected="true"] { opacity:1; border-bottom-color:var(--primary-color,#3477d6); }
  .selfpanel .sptab:focus-visible, .sktree .sknode:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
  .selfpanel .sppane[hidden] { display:none; }
  .spcode .cdm { margin:0 0 12px; }
  .spcode h4 { display:flex; align-items:baseline; gap:8px; margin:0 0 4px; font-size:14px; }
  .spcode .cdruns, .spcode .cdnone { opacity:.65; font-size:12px; font-weight:400; }
  .spcode pre { margin:0; padding:6px 8px; border-radius:8px; overflow-x:auto; white-space:pre-wrap; word-break:break-word;
    background:color-mix(in srgb, var(--primary-text-color,#ddd) 8%, transparent); font-size:12px; }
  .spcode .cdsteps { margin:4px 0 0; padding-left:20px; font-size:12px; }
  /* #361: the film's look -- its gold, leaning dark on a light theme and bright on a dark one */
  .sktree { --sk-earned:color-mix(in srgb, #f2c45a 72%, var(--primary-text-color,#ddd)); }
  .sktree .skhdr { display:flex; align-items:center; gap:10px; padding:8px 12px; margin:2px 0 6px; border-radius:12px;
                   border:1px solid color-mix(in srgb, var(--divider-color,#888) 60%, transparent);
                   background:color-mix(in srgb, var(--primary-color,#3477d6) 8%, transparent); }
  .sktree .skhdr .nm { font-size:18px; font-weight:650; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sktree .skhdr .lvb { margin-left:auto; font-weight:700; font-variant-numeric:tabular-nums; padding:1px 10px; border-radius:999px;
                        background:color-mix(in srgb, var(--sk-earned) 30%, transparent); }
  .sktree .note { font-size:11.5px; opacity:.75; margin:0 0 6px; }
  .sktree .skbox { max-width:100%; overflow:auto; overscroll-behavior:contain auto; border-radius:12px; margin:0 0 8px;
                   border:1px solid color-mix(in srgb, var(--divider-color,#888) 35%, transparent); }
  .sktree .skcanvas { position:relative; margin:0 auto; }
  .sktree .skedges { position:absolute; inset:0; overflow:visible; }
  .sktree .sklane { stroke:color-mix(in srgb, var(--divider-color,#888) 25%, transparent); stroke-width:1; }
  .sktree .sklanel { position:absolute; top:4px; text-align:center; font-size:10.5px; letter-spacing:.06em; text-transform:uppercase; opacity:.6; }
  .sktree .skedge { fill:none; stroke:color-mix(in srgb, var(--primary-text-color,#ddd) 30%, transparent); stroke-width:2; stroke-dasharray:5 5; }
  .sktree .skedge.earned { stroke:var(--sk-earned); stroke-dasharray:none; opacity:.9; }
  .sktree .skedge.awaiting { stroke:var(--sk-earned); opacity:.7; }
  .sktree .sknode { position:absolute; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:1px;
                    padding:3px 4px; border-radius:12px; font:inherit; font-size:11px; line-height:1.15; color:inherit; text-align:center;
                    cursor:pointer; overflow:hidden; border:1.5px solid color-mix(in srgb, var(--divider-color,#888) 60%, transparent);
                    background:var(--card-background-color,#111); }
  .sktree .sknode .skic { font-size:14px; line-height:1; height:18px; display:grid; place-items:center; }
  .sktree .sknode .sknm { font-weight:600; overflow-wrap:anywhere; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .sktree .sknode .sksub { font-size:10px; opacity:.75; font-variant-numeric:tabular-nums; }
  .sktree .sknode.earned { border-color:var(--sk-earned); border-width:2px; background:color-mix(in srgb, var(--sk-earned) 22%, var(--card-background-color,#111)); }
  .sktree .sknode.open { border-color:var(--primary-color,#3477d6);
                         box-shadow:0 0 0 2px color-mix(in srgb, var(--primary-color,#3477d6) 30%, transparent),
                                    0 0 12px color-mix(in srgb, var(--primary-color,#3477d6) 45%, transparent); }
  .sktree .sknode.locked { opacity:.55; color:var(--secondary-text-color,inherit);
                           background:color-mix(in srgb, var(--primary-text-color,#ddd) 5%, var(--card-background-color,#111)); }
  .sktree .sknode.locked .skic { opacity:.7; }
  .sktree .sknode.unbuilt { opacity:.55; border-style:dashed; background:transparent; }
  .sktree .sknode.sel { outline:2px solid color-mix(in srgb, var(--primary-text-color,#ddd) 60%, transparent); outline-offset:1px; }
  .sktree .skring .skrt { fill:none; stroke:color-mix(in srgb, var(--primary-text-color,#ddd) 20%, transparent); stroke-width:2.5; }
  .sktree .skring .skrf { fill:none; stroke:var(--primary-color,#3477d6); stroke-width:2.5; stroke-linecap:round; }
  @media (prefers-reduced-motion: no-preference) {
    .sktree .sknode.open { animation:skglow 2.6s ease-in-out infinite alternate; }
    @keyframes skglow { to { box-shadow:0 0 0 2px color-mix(in srgb, var(--primary-color,#3477d6) 45%, transparent),
                                       0 0 18px color-mix(in srgb, var(--primary-color,#3477d6) 60%, transparent); } }
    /* #361: a skill earned while the page is open lights up once */
    .sktree .sknode.lit { animation:skearn 1.6s ease-out 1; }
    .sktree .sknode.lit .skic { animation:skearnic 1.6s ease-out 1; }
    .sktree .skedge.lit { animation:skearned 1.6s ease-out 1; }
    @keyframes skearn { 0% { box-shadow:0 0 0 0 transparent; }
                        25% { box-shadow:0 0 0 4px color-mix(in srgb, var(--sk-earned) 55%, transparent), 0 0 24px var(--sk-earned);
                              background:color-mix(in srgb, var(--sk-earned) 55%, var(--card-background-color,#111)); }
                        100% { box-shadow:0 0 0 0 transparent; } }
    @keyframes skearnic { 0% { transform:scale(.6); } 30% { transform:scale(1.35); } 100% { transform:scale(1); } }
    @keyframes skearned { 0% { stroke-dashoffset:0; opacity:.3; stroke-width:2; } 30% { opacity:1; stroke-width:4; } 100% { opacity:.9; stroke-width:2; } }
  }
  .sktree .skcard { padding:8px 10px; border-radius:12px; margin:0 0 8px; font-size:12px;
                    background:color-mix(in srgb, var(--primary-color,#3477d6) 6%, transparent); }
  .sktree .skct { font-weight:650; font-size:13px; margin:0 0 4px; overflow-wrap:anywhere; }
  .sktree .skcd { display:grid; grid-template-columns:auto 1fr; gap:2px 10px; margin:0; }
  .sktree .skcd dt { opacity:.6; }
  .sktree .skcd dd { margin:0; overflow-wrap:anywhere; }
  .sktree .skachl { list-style:none; padding:0; margin:0; font-size:12px; }
  .sktree .skach { padding:3px 0; border-bottom:1px solid color-mix(in srgb, var(--divider-color,#888) 30%, transparent); overflow-wrap:anywhere; }
  /* the toy dock (#249): self's toys as icons stacked under the 👶 tab,
     live, whether or not the panel is open; hover or tap one to play */
  .toydock { position:absolute; z-index:8; left:max(0px, env(safe-area-inset-left)); top:calc(50% + 32px);
             display:flex; flex-direction:column; gap:4px; max-height:calc(50% - 120px); }
  .toydock[hidden], .view-chat .toydock, .view-chat .dkpop { display:none; }
  .toydock .dkl { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:4px; overflow-y:auto; overscroll-behavior:contain; }
  .toydock .dkb, .toydock .dockbtn { position:relative; width:40px; height:40px; padding:0; display:grid; place-items:center;
             font-size:20px; line-height:1; font-variant-numeric:tabular-nums; border-radius:0 12px 12px 0;
             border:1px solid color-mix(in srgb, var(--divider-color,#888) 45%, transparent); border-left:0;
             background:color-mix(in srgb, var(--card-background-color,#111) 80%, transparent); }
  .toydock .dockbtn { display:none; }
  .toydock .dkb[aria-expanded="true"], .toydock .dockbtn[aria-expanded="true"] {
             background:color-mix(in srgb, var(--primary-color,#3477d6) 35%, var(--card-background-color,#111)); }
  @media (hover: hover) { .toydock .dkb:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 25%, var(--card-background-color,#111)); } }
  .toydock .dkb:focus-visible, .toydock .dockbtn:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:2px; }
  .toydock .g-fast, .toydock .g-slow, .toydock .g-body { font-size:13px; font-weight:650; }
  .toydock .tower { display:flex; flex-direction:column; gap:1px; }
  .toydock .tower .blk { display:block; width:15px; height:4px; border-radius:1px; background:var(--primary-text-color,#ddd); opacity:.85; }
  .toydock .tower .blk.down { opacity:.16; }
  .toydock .gauge { display:block; position:relative; width:9px; height:24px; border-radius:3px; overflow:hidden;
                    background:color-mix(in srgb, var(--primary-text-color,#ddd) 18%, transparent); }
  .toydock .gauge i { position:absolute; left:0; right:0; bottom:0; background:var(--primary-color,#3477d6); }
  .toydock .burst.on { color:var(--warning-color,#ffa600); }
  .toydock .burst.off { opacity:.3; }
  /* #264: the ball -- a court split by a line, the ball on self's half (left) or yours (right) */
  .toydock .ball { display:block; position:relative; width:26px; height:14px; border-radius:7px;
                   background:color-mix(in srgb, var(--primary-text-color,#ddd) 12%, transparent); }
  .toydock .ball::before { content:""; position:absolute; left:50%; top:2px; bottom:2px; width:1px;
                           background:color-mix(in srgb, var(--primary-text-color,#ddd) 40%, transparent); }
  .toydock .ball i { position:absolute; top:2px; width:10px; height:10px; border-radius:50%;
                     background:var(--warning-color,#ffa600); transition:left .45s cubic-bezier(.3,1.4,.5,1); }
  .toydock .ball.side-self i { left:2px; }
  .toydock .ball.side-other i { left:14px; box-shadow:0 0 0 2px color-mix(in srgb, var(--warning-color,#ffa600) 35%, transparent); }
  .toydock .hd { position:absolute; top:4px; right:4px; width:7px; height:7px; border-radius:50%; }
  .toydock .hd.ok, .dkpop .dot.ok { background:#2e9d5b; }
  .toydock .hd.warn, .dkpop .dot.warn { background:var(--warning-color,#ffa600); }
  .toydock .hd.bad, .dkpop .dot.bad { background:var(--error-color,#db4437); }
  .toydock .hd.idle, .dkpop .dot.idle { background:color-mix(in srgb, var(--primary-text-color,#ddd) 35%, transparent); }
  .toydock .bdg { position:absolute; right:1px; bottom:1px; max-width:38px; overflow:hidden; padding:1px 3px; border-radius:6px;
                  font-size:9px; line-height:1.1; font-weight:600; pointer-events:none;
                  background:var(--primary-color,#3477d6); color:var(--text-primary-color,#fff); opacity:0; transition:opacity 2.5s ease-in; }
  .toydock .bdg.fresh { opacity:1; transition:none; }
  .dkpop { position:fixed; z-index:9; width:min(270px, calc(100vw - 64px)); padding:10px 12px; border-radius:12px;
           font-size:12.5px; line-height:1.4; box-shadow:0 6px 24px rgba(0,0,0,.3);
           background:color-mix(in srgb, var(--card-background-color,#111) 96%, transparent); }
  .dkpop[hidden] { display:none; }
  .dkpop p { margin:4px 0; }
  .dkpop .muted, .dkpop .pg { opacity:.65; }
  .dkpop .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle; }
  .dkpop .pacts { display:flex; flex-wrap:wrap; gap:4px; margin-top:6px; }
  .dkpop .pk { font-size:11.5px; line-height:1.2; padding:3px 8px; border-radius:999px;
               border:1px solid color-mix(in srgb, var(--divider-color,#888) 70%, transparent);
               background:color-mix(in srgb, var(--primary-color,#3477d6) 14%, transparent); }
  .dkpop .pk:hover { background:color-mix(in srgb, var(--primary-color,#3477d6) 30%, transparent); }
  .dkpop .pk:disabled { opacity:.5; cursor:progress; }
  .dkpop .pk:focus-visible { outline:2px solid var(--primary-color,#3477d6); outline-offset:1px; }
  .dkpop .pres { padding:3px 6px; border-radius:6px; overflow-wrap:anywhere; background:color-mix(in srgb, #2e9d5b 22%, transparent); }
  .dkpop .pres.bad { background:color-mix(in srgb, var(--error-color,#db4437) 22%, transparent); }
  .dkpop .pres:empty { display:none; }
  .dkpop .lv { font-weight:600; }                    /* #276: how well self has learned it */
  .dkpop .lv.mastered { color:#2e9d5b; }
  .dkpop .lv.worse, .dkpop .lv.lost { color:var(--error-color,#db4437); }
  /* #267: a locked toy -- greyed, a lock on it; its tooltip says when it unlocks */
  .toydock .dk.locked .dkb { border-style:dashed; background:color-mix(in srgb, var(--card-background-color,#111) 55%, transparent); }
  .toydock .dk.locked .g { filter:grayscale(1); opacity:.45; }
  .toydock .lk { position:absolute; right:2px; bottom:1px; font-size:10px; line-height:1; pointer-events:none; }
  .toydock .hd.locked { display:none; }
  .selfpanel .toys { list-style:none; padding:0; margin:4px 0; }
  .selfpanel .toys .tu { padding:3px 0; }
  .selfpanel .toys .ti { display:inline-block; width:1.6em; }
  .selfpanel .toys .tu.locked .ti { filter:grayscale(1); opacity:.5; }
  .selfpanel .toys .tu.locked b { opacity:.6; }
  @media (prefers-reduced-motion: reduce) { .toydock .bdg, .toydock .ball i { transition:none; } }
  @media (max-width: 760px) {
    .toydock { top:calc(26vh + 56px); top:calc(26dvh + 56px); max-height:calc(74dvh - 140px); }
    .toydock .dockbtn { display:grid; }
    .toydock:not(.open) .dkl { display:none; }
  }
  /* the brain's caption (#237): one plain line about self's newest record line,
     at the bottom of the field (left of the chat on a wide screen) */
  .brainline { position:absolute; z-index:3; left:calc((100% - min(460px, 44vw) - 24px) / 2); bottom:64px; transform:translateX(-50%);
               width:max-content; max-width:calc(100% - min(460px, 44vw) - 56px); display:flex; align-items:baseline; gap:8px;
               padding:5px 12px; border-radius:12px; font-size:13px; line-height:1.35; pointer-events:auto; }
  .brainline[hidden], .view-chat .brainline { display:none; }
  .chat.hidden ~ .brainline { left:50%; max-width:calc(100% - 32px); }
  .brainline .bl-t { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; }
  .brainline .bl-c { flex:none; font:11px var(--code-font-family, ui-monospace, Menlo, monospace); opacity:.55; white-space:nowrap; }
  @media (prefers-reduced-motion: no-preference) {
    .brainline.fresh { animation:blin .5s ease-out; }
    @keyframes blin { from { opacity:.25; } }
  }
  @media (max-width: 760px) {
    .brainline { left:50%; bottom:calc(56dvh + 82px); max-width:calc(100% - 24px); font-size:12px; }
    .chat.hidden ~ .brainline { bottom:64px; }
    .brainline .bl-t { white-space:normal; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
  }
  /* #289: a rung passed -- over everything, never in the way (a click anywhere dismisses it) */
  .top .rungbell { padding:6px 8px; }
  .top .rungbell.off { opacity:.55; }
  .celebrate { position:absolute; inset:0; z-index:20; pointer-events:none; display:flex; align-items:flex-start; justify-content:center; }
  .celebrate[hidden] { display:none; }
  .celebrate canvas { position:absolute; inset:0; width:100%; height:100%; }
  .celebrate .cb-t { position:relative; margin-top:18vh; max-width:calc(100% - 32px); padding:12px 22px; border-radius:16px;
                     font-size:22px; font-weight:700; text-align:center; color:#1b1404;
                     background:linear-gradient(135deg, #ffe28c, #d4a017 55%, #7cf0c8);
                     box-shadow:0 0 0 2px rgba(255,255,255,.55), 0 8px 40px rgba(212,160,23,.55); }
  @media (prefers-reduced-motion: no-preference) {
    .celebrate:not(.still) .cb-t { animation:cbpop 3.2s ease-out both; }
    @keyframes cbpop { 0% { opacity:0; transform:scale(.6); } 12% { opacity:1; transform:scale(1.08); }
                       20% { transform:scale(1); } 85% { opacity:1; } 100% { opacity:0; transform:translateY(-12px); } }
  }
  @media (max-width: 760px) { .celebrate .cb-t { font-size:17px; margin-top:12vh; } }
  /* #299: the celebration asks the operator to accept -- the overlay's one clickable part, kept until answered */
  .celebrate .cb-acc { position:absolute; left:50%; bottom:max(24px, env(safe-area-inset-bottom)); transform:translateX(-50%);
                       pointer-events:auto; width:min(440px, calc(100% - 32px)); box-sizing:border-box; padding:12px 14px; border-radius:16px;
                       background:var(--card-background-color,#111); color:var(--primary-text-color,#ddd);
                       box-shadow:0 0 0 2px #d4a017, 0 8px 32px rgba(0,0,0,.45); text-align:center; }
  .celebrate .cb-acc[hidden] { display:none; }
  .celebrate .cb-acc .cb-q { margin:0 0 10px; font-size:14px; font-weight:600; overflow-wrap:anywhere; }
  .celebrate .cb-acc .cb-row { display:flex; gap:8px; justify-content:center; flex-wrap:wrap; }
  .celebrate .cb-acc button { border:0; border-radius:999px; padding:7px 16px; font:inherit; font-weight:600; cursor:pointer; }
  .celebrate .cb-acc .cb-yes { color:#1b1404; background:linear-gradient(135deg, #ffe28c, #d4a017); }
  .celebrate .cb-acc .cb-no { background:transparent; color:inherit; border:1px solid color-mix(in srgb, var(--divider-color,#888) 70%, transparent); }
  .celebrate .cb-acc button:disabled { opacity:.6; cursor:default; }
  .celebrate .cb-acc .cb-msg { margin:8px 0 0; font-size:12px; opacity:.85; }
  .celebrate .cb-acc .cb-msg:empty { display:none; }
  /* #331: the ball on your side -- its dock icon (and the phone's 🧸) glows; a pulse unless reduced motion */
  .toydock .dkb.yours, .toydock .dockbtn.yours {
    border-color:var(--warning-color,#ffa600);
    box-shadow:0 0 0 2px var(--warning-color,#ffa600), 0 0 14px 2px color-mix(in srgb, var(--warning-color,#ffa600) 70%, transparent); }
  @media (prefers-reduced-motion: no-preference) {
    .toydock .dkb.yours, .toydock .dockbtn.yours { animation:ballglow 1.2s ease-in-out infinite; }
    @keyframes ballglow { 50% { box-shadow:0 0 0 3px var(--warning-color,#ffa600), 0 0 26px 8px color-mix(in srgb, var(--warning-color,#ffa600) 55%, transparent); } }
    .ballnote:not([hidden]) { animation:ballin .5s cubic-bezier(.3,1.4,.5,1) both; }
    @keyframes ballin { from { opacity:0; transform:translate(-50%, -24px) scale(.9); } }
  }
  /* #331: self rolled you the ball -- a toast over everything, its button rolls it back */
  .ballnote { position:absolute; z-index:21; left:50%; top:max(64px, calc(env(safe-area-inset-top) + 56px)); transform:translateX(-50%);
              width:max-content; max-width:calc(100% - 32px); display:flex; align-items:center; gap:12px; flex-wrap:wrap; justify-content:center;
              padding:12px 14px 12px 18px; border-radius:16px; color:#1b1404;
              background:linear-gradient(135deg, #ffe28c, var(--warning-color,#ffa600));
              box-shadow:0 0 0 2px rgba(255,255,255,.6), 0 8px 36px rgba(255,166,0,.55); }
  .ballnote[hidden] { display:none; }
  .ballnote .bn-t { font-size:18px; font-weight:700; overflow-wrap:anywhere; }
  .ballnote .bn-roll { border:0; border-radius:999px; padding:9px 20px; font-size:16px; font-weight:700; cursor:pointer;
                       color:#fff; background:#1b1404; }
  .ballnote .bn-roll:disabled { opacity:.6; cursor:progress; }
  .ballnote .bn-x { border:0; background:transparent; color:inherit; font-size:18px; line-height:1; padding:4px 6px; opacity:.7; }
  .ballnote button:focus-visible { outline:2px solid #1b1404; outline-offset:2px; }
  .ballnote .bn-msg { flex-basis:100%; margin:0; font-size:12.5px; text-align:center; }
  .ballnote .bn-msg:empty { display:none; }
  @media (max-width: 760px) { .ballnote .bn-t { font-size:15px; } }
`;
const BRAIN_MS = 3000;                            // #237: the brain's record poll, only while the page is visible
const LS_SELFPANEL = "membrane.home.selfpanel";   // #230: the 👶 panel open ("1") or closed, per browser
const SELFPANEL_MS = 4000;                        // its activity refresh, only while open
const SELFPANEL_SEAT_MS = 15000;                  // ...and its character sheet's (the whole status; the seat caches ~30 s)
const LS_SELFSHEET = "membrane.home.selfsheet";   // #249: which sheet sections are open, per browser
const LS_SELFTAB = "membrane.home.selftab";       // #296: the 👶 panel's tab (sheet, tree or code), per browser
const DOCK_MS = 3000;                             // #249: the toy dock's poll, only while the page is visible
const LS_DOCK = "membrane.home.toydock";          // the phone's 🧸: the dock expanded ("1") or not

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" &&
    !customElements.get("membrane-home-panel")) {
  class MembraneHomePanel extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._v3 = null; this._me = null; this._lastTouch = -1e9; this._hidSidebar = false;
    }

    set hass(h) {
      this._hass = h;
      if (this._chat) this._chat.hass = h;
      if (!this._started && this.isConnected && this.shadowRoot.firstChild) this._start();
    }
    get hass() { return this._hass; }
    set narrow(n) { this._narrow = !!n; if (this._chat) this._chat.narrow = n; }
    set panel(p) { this._panel = p; }

    // properties HA set before this class was defined (its panel loader can make the
    // element first): own properties that shadow the setters -- take them over
    _takeEarly() {
      for (const k of ["panel", "route", "narrow", "hass"]) {
        if (!Object.prototype.hasOwnProperty.call(this, k)) continue;
        const v = this[k]; delete this[k]; this[k] = v;
      }
    }

    connectedCallback() {
      this._takeEarly();
      lsSet(LS_LEFT, "0");               // back on membrane: fresh starts land here again
      if (!this.shadowRoot.firstChild) this._render();
      else if (this._bodies) this._bodies.pause(false);                 // #358: back on the page
      this._listenKeys(true);
      if (this._hass && !this._started) this._start();
      if (lsGet(LS_IMMERSIVE, "1") === "1") this._dock("always_hidden");
    }

    disconnectedCallback() {
      this._started = false;
      if (this._rec) this._recStop(false);
      this._hotStop();
      if (this._onVis) document.removeEventListener("visibilitychange", this._onVis);
      this._hushTts(); this._tiltStop();
      this._listenKeys(false);
      clearInterval(this._follow);
      clearInterval(this._pauseT); clearInterval(this._selfT); clearInterval(this._selfPanelT);
      clearInterval(this._brainT); clearInterval(this._dockT);
      if (this._stats) this._stats.toggle(false);                 // the 📊 window's refresh
      clearTimeout(this._cycT); this._titleStop();                // #331
      if (this._bodies) this._bodies.pause(true);                      // #358: no frames while off the page
      if (this._brainVis) { document.removeEventListener("visibilitychange", this._brainVis); this._brainVis = null; }
      if (this._v3) { this._v3.stop(); this._v3 = null; }
      if (this._hidSidebar) this._dock("restore");
    }

    _paintSelf() {
      const c = this._chat;
      if (!c || typeof c._selfView !== "function") return;
      const v = c._selfView();
      // #299 / #460: the 👶 panel's on/off switch (the 🌱 growth-limit chooser is gone:
      // an earned achievement opens what it leads to once the operator accepts it)
      const b = this._spSleep;
      if (b) {
        b.disabled = !v.loaded || !!this._sleepBusy;          // nothing before the seat's state loads
        b.textContent = v.icon + " " + (v.on ? "on" : "off");
        b.title = v.title;
        b.setAttribute("aria-pressed", v.on ? "true" : "false");
        b.setAttribute("aria-label", v.label + " -- tap to " + (v.on ? "switch it off" : "switch it on"));
      }
      if (this._selfStat && !this._selfFlash) {
        if (typeof c._paintExp === "function") c._paintExp(this._selfStat);   // #230: the exp bar
        else this._selfStat.textContent = v.status || "";
      }
    }

    // BLURP'S BODY (#358): the tab's and the panel header's canvases (and the
    // zoo's, _zooBodies), one loop for all (BlurpBodies): frames only while a
    // body is on screen and the tab visible; reduced motion: a still frame
    _wireBodies(root) {
      const mq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
      const cbs = new Map();
      const io = typeof IntersectionObserver === "function"
        ? new IntersectionObserver((es) => { for (const e of es) { const cb = cbs.get(e.target); if (cb) cb(e.isIntersecting); } }) : null;
      this._bodies = new BlurpBodies({
        raf: (f) => requestAnimationFrame(f), caf: (id) => cancelAnimationFrame(id),
        hidden: () => !!document.hidden, still: () => !!(mq && mq.matches), now: () => performance.now(),
        observe: (el, cb) => {
          if (!io) { cb(true); return () => {}; }              // no observer: drawn as shown
          cbs.set(el, cb); io.observe(el);
          return () => { io.unobserve(el); cbs.delete(el); };
        },
        colours: (el) => ({ z: getComputedStyle(el).color || BODY_GOLD }),   // the z's: the theme's text colour
      });
      const kick = () => { if (this._bodies) this._bodies.kick(); };   // for the element's life (paused while detached)
      document.addEventListener("visibilitychange", kick);
      if (mq && mq.addEventListener) mq.addEventListener("change", kick);
      this._bodyEls = Array.from(root.querySelectorAll("canvas.bbody"));
      for (const cv of this._bodyEls) this._bodies.add(cv, this._bodyPose());
      this._bodyTick();
    }

    _bodyPose() {
      const c = this._chat;
      const view = c && typeof c._selfView === "function" ? c._selfView() : null;
      return blurpPose(this._bodyCard, view, this._bodyLook);
    }

    /** Every 1.5 s while the tab is visible: the look's direction on the
     *  screen (self's fresh look on the 3D field; none: forward) and, every
     *  BODY_CARD_MS, the seat's card (asleep, energy). */
    _bodyTick() {
      if (!this._bodies || document.hidden) return;
      const v = this._v3;
      let look = null;
      if (v && typeof v.selfLookNow === "function" && typeof v.selfScreen === "function" && typeof v._project === "function") {
        const lp = freshLook(v.selfLookNow());
        if (lp) look = blurpLookAngle(v.selfScreen(), v._project(lp));
      }
      this._bodyLook = look;
      if (this._hass && Date.now() - (this._bodyCardAt || 0) > BODY_CARD_MS) {
        this._bodyCardAt = Date.now();
        this._get("/membrane/self/card")
          .then((d) => { if (d && typeof d === "object") this._bodyCard = d; this._bodyPaint(); })
          .catch(() => {});                                  // an older seat: the sleep toggle's state
      }
      this._bodyPaint();
    }

    _bodyPaint() {
      if (!this._bodies) return;
      const p = this._bodyPose();
      for (const cv of this._bodyEls || []) this._bodies.set(cv, p);
    }

    /** The zoo's cards were redrawn: their bodies replace the last ones. */
    _zooBodies() {
      if (!this._bodies) return;
      for (const cv of this._zooEls || []) this._bodies.drop(cv);
      this._zooEls = Array.from(this._zooList.querySelectorAll("canvas.zbody"));
      for (const cv of this._zooEls) {
        const en = cv.getAttribute("data-energy");
        this._bodies.add(cv, blurpPose({ state: cv.getAttribute("data-off") === "1" ? "off" : cv.getAttribute("data-asleep") === "1" ? "asleep" : "awake",
                                         energy: en == null ? null : Number(en) }));
      }
    }

    /** #299: switch on or off, from the 👶 panel's toggle; the seat's answer is shown 4 s. */
    _onSleep() {
      const c = this._chat;
      if (!c || typeof c._setSelfMode !== "function" || typeof c._selfView !== "function") return;
      const v = c._selfView();
      if (!v.loaded) return;
      this._sleepBusy = true; this._paintSelf();
      Promise.resolve(c._setSelfMode(!v.on)).catch(() => null).then((out) => {
        this._sleepBusy = false;
        if (out && !out.skipped) this._flashSelf(out.text);
        this._paintSelf();
        this._bodyCardAt = 0; this._bodyTick();               // #358: the body opens or closes its eyes now
      });
    }

    _flashSelf(text) {
      this._selfFlash = text;
      if (this._selfStat) this._selfStat.textContent = text;
      this._statusEl.textContent = "🌱 " + text;
      clearTimeout(this._selfFlashT);
      this._selfFlashT = setTimeout(() => { this._selfFlash = null; this._paintSelf(); }, 4000);
    }

    /** #299: accept an earned achievement through the seat (the panel owns the
     *  route); the sheet and the tree refresh with the answer. */
    async _acceptSelf(id) {
      const c = this._chat;
      if (!c || typeof c._acceptSelf !== "function") return { ok: false, error: "accepting is not available on this page" };
      const out = await Promise.resolve(c._acceptSelf(id)).catch((e) => ({ ok: false, error: String((e && e.message) || e) }));
      if (out && out.ok) {
        this._flashSelf("✓ accepted " + (out.name || id) + ": what it leads to opens now");
        this._spSheetAt = 0;                                    // the sheet and the tree show it at once
        if (this._selfPanel && !this._selfPanel.hidden) this._paintSelfPanel();
      }
      return out || { ok: false, error: "no answer" };
    }

    // THE 👶 PANEL (#230, the operator's ask 2026-10-19): self's side panel --
    // what it is doing now (its record, newest first) and its report card.
    // READ-ONLY but for the operator's switches: the ⏻ on/off switch in its
    // header and the Skill tree's Accept (#299) -- nothing else here changes self (the toys' play buttons, #244, are the one write -- they change
    // a toy in self's world, never self; _onToyPoke below). Open or closed is remembered per browser; it refreshes only
    // while open.
    _toggleSelfPanel(on) {
      const p = this._selfPanel, b = this._babyBtn; if (!p || !b) return;
      on = on == null ? p.hidden : !!on;
      p.hidden = !on;
      b.classList.toggle("on", on);
      b.setAttribute("aria-expanded", on ? "true" : "false");
      lsSet(LS_SELFPANEL, on ? "1" : "0");
      clearInterval(this._selfPanelT); this._selfPanelT = null;
      if (on) {
        this._spSeatAt = 0;
        this._paintSelfPanel();
        this._selfPanelT = setInterval(() => this._paintSelfPanel(), SELFPANEL_MS);
      }
    }

    async _paintSelfPanel() {
      const c = this._chat, p = this._selfPanel;
      if (!c || !p || p.hidden || typeof c._selfCardHtml !== "function") return;
      if (Date.now() - (this._spSeatAt || 0) > SELFPANEL_SEAT_MS && typeof c._loadSeat === "function") {
        this._spSeatAt = Date.now();                       // the card's numbers (the event stream moves only the bar)
        await c._loadSeat().catch(() => {});
        if (!c._seat) this._spSeatAt = 0;                  // not loaded yet (a fresh page): retry next tick
      }
      c._paintExp(this._spXp);
      // #249: the character sheet (GET /membrane/self/sheet, every
      // SELFPANEL_SEAT_MS while open); an older seat without it gets the
      // report card it had
      if (typeof c._selfSheet === "function" && Date.now() - (this._spSheetAt || 0) > SELFPANEL_SEAT_MS) {
        this._spSheetAt = Date.now();
        const sh = await c._selfSheet().catch(() => null);
        if (sh) this._sheet = sh;
        else if (!this._sheet) this._spSheetAt = 0;        // not there yet: retry next tick
        this._takeName(c);                                 // #277: the sheet carries the name
      }
      this._paintTree();                                   // #296: the Skill tree tab, when it shows
      this._paintCode();                                   // the Code tab, when it shows
      const card = this._sheet && typeof c._selfSheetHtml === "function"
        ? c._selfSheetHtml(this._sheet, this._sheetOpen) : "<h3>report card</h3>" + c._selfCardHtml();
      if (card !== this._spCardHtml) {
        this._spCard.innerHTML = card; this._spCardHtml = card;
        const slot = this._spCard.querySelector(".xpslot");   // the live exp bar sits in the nameplate
        if (slot) slot.appendChild(this._spXp);
      }
      if (this._spBusy || !this._spActSec.open) return;     // the feed is fetched only while its section is open
      this._spBusy = true;
      try {
        const lines = await c._selfRecent(15);
        if (lines && !p.hidden) this._spAct.innerHTML = c._selfLinesHtml(lines);
        else if (!lines) this._spAct.innerHTML = '<li class="sl none">the record is not reachable right now</li>';
      } finally { this._spBusy = false; }
    }
    /** #296: the panel's tabs -- the sheet, the Skill tree or the Code; remembered per browser. */
    _selfTab(tab, focus) {
      tab = tab === "tree" || tab === "code" ? tab : "sheet";
      this._spTabNow = tab;
      for (const b of this._spTabs) {
        const on = b.dataset.tab === tab;
        b.setAttribute("aria-selected", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
        if (on && focus) b.focus();
      }
      this._spPaneSheet.hidden = tab !== "sheet";
      this._spPaneTree.hidden = tab !== "tree";
      this._spPaneCode.hidden = tab !== "code";
      lsSet(LS_SELFTAB, tab);
      this._paintTree();
      this._paintCode();
    }
    /** The Code tab: Blurp's methods, from the sheet the panel already read. */
    _paintCode() {
      if (!this._spCode || this._spPaneCode.hidden) return;
      const h = selfCodeHtml(this._sheet);
      if (h !== this._spCodeHtml) this._spCode.innerHTML = this._spCodeHtml = h;
    }
    /** #296: the Skill tree, from the sheet the panel already read; rebuilt only when it changed. */
    _paintTree() {
      const c = this._chat;
      if (!this._spTree || this._spPaneTree.hidden || !c || typeof c._skillTree !== "function") return;
      const sig = this._sheet ? c._skillTreeSig(this._sheet) : "";
      if (sig === this._spTreeSig && this._spTree.firstChild) return;
      this._spTreeSig = sig;
      c._skillTree(this._spTree, this._sheet, this._spTreeSel);
    }
    /** A sheet section opened or closed: remembered per browser. */
    _onSheetToggle(e) {
      const d = e.target;
      if (!d || !d.matches || !d.matches("details[data-sec]")) return;
      // a section inserted open also fires `toggle`: only a change from how it
      // was rendered (data-was) is the person's choice
      if (d.open === (d.getAttribute("data-was") === "1")) return;
      d.setAttribute("data-was", d.open ? "1" : "0");
      const key = d.getAttribute("data-sec");
      this._sheetOpen[key] = d.open;
      lsSet(LS_SELFSHEET, JSON.stringify(this._sheetOpen));
      if (key === "activity" && d.open) this._paintSelfPanel();
    }
    // ---- end of the 👶 panel


    // THE TOY DOCK (#249, the operator: the toys' buttons out of the panel):
    // one icon per toybox toy stacked under the 👶 tab, live whether or not
    // the panel is open -- a glyph per kind, a fading badge naming who changed
    // it last, a health dot -- and a popover (hover, or tap on a phone) with
    // its stats and the play actions. GET /membrane/toybox/state every DOCK_MS
    // while the page is visible; the actions go through _onToyPoke.
    _dockStart() {
      clearInterval(this._dockT);
      this._dockT = setInterval(() => this._dockTick(), DOCK_MS);
      this._dockTick();
    }

    // #373: each read carries the poke count it started under (_dockSeq, bumped
    // by every poke); a read that started before the latest poke never paints
    // over the poke's result, and one asked for while such a stale read is in
    // flight runs as soon as it lands.
    async _dockTick(force) {
      const c = this._chat;
      if ((document.hidden && !force) || !c || typeof c._toyState !== "function") return;
      const seq = this._dockSeq || 0;
      if (this._dockBusy) { if (this._dockBusySeq !== seq) this._dockAgain = true; return; }
      this._dockBusy = true; this._dockBusySeq = seq;
      try {
        const toys = await c._toyState();
        this._takeName(c);                                 // #277: so does the dock
        if (toys && seq === (this._dockSeq || 0)) this._paintDock(toys);
      } finally {
        this._dockBusy = false;
        if (this._dockAgain) { this._dockAgain = false; this._dockTick(true); }
      }
    }

    /** #277: the seat's name for self, from the chat's last sheet / dock
     *  read; the field's tag on the amoeba says it too. */
    _takeName(c) {
      if (c && typeof c._selfName === "function") setSelfName(c._selfName());
      if (this._v3) this._v3.selfName = SELF_NAME;
    }

    _paintDock(toys) {
      const c = this._chat, d = this._toyDock, ul = this._toyDockL;
      if (!d || !ul || typeof c._toyDockItem !== "function") return;
      this._dockToys = toys;
      const ball = ballAlert(this._ballSeen || null, toys);      // #331: once per roll of self's
      this._ballSeen = ball.seen;
      d.hidden = !toys.length;
      if (!toys.length) { this._dockClose(); this._ballTake(ball); return; }
      const sig = toys.map((t) => t.name + "|" + t.kind).join("\n");
      if (sig !== this._dockSig) { ul.innerHTML = c._toyDockHtml(toys); this._dockSig = sig; }
      for (const t of toys) {
        const btn = ul.querySelector('button[data-dock-toy="' + CSS.escape(t.name) + '"]');
        if (!btn) continue;
        const it = c._toyDockItem(t);
        const g = btn.querySelector(".g");
        if (g.innerHTML !== it.glyph) g.innerHTML = it.glyph;
        btn.setAttribute("aria-label", it.label);
        btn.title = it.label + (it.badge ? " · " + it.badge.title : "");
        btn.querySelector(".hd").className = "hd " + it.health;
        const b = btn.querySelector(".bdg");
        if (it.badge && it.badge.fresh) b.textContent = it.badge.text;
        b.classList.toggle("fresh", !!(it.badge && it.badge.fresh));
        btn.classList.toggle("yours", ball.yours.includes(t.name));   // #331: it glows while on your side
      }
      if (this._dockBtn) this._dockBtn.classList.toggle("yours", ball.yours.length > 0);
      if (this._dockCur) this._dockPaintPop();
      this._ballTake(ball);
    }

    // THE BALL'S ALERT (#331): ballAlert decides in _paintDock (every read of
    // the toys, the optimistic roll-back's included); a roll of self's raises
    // the toast, the chime, and -- with the tab hidden -- the notification and
    // the flashing title; the ball leaving your side takes them all down.
    _ballTake(ball) {
      if (this._ballName && !ball.yours.includes(this._ballName)) this._ballHide();
      if (!ball.alert.length) return;
      const name = ball.alert[0], text = ballAlertText(SELF_NAME);
      this._ballShow(name, text, "");
      this._chime(ballChimeNotes());
      if (document.hidden) {
        this._notify(text, "membrane.home.ball." + SEAT + "." + name, ballNotifyBody());
        this._titleStart(text);
      }
    }

    _ballShow(name, text, msg) {
      const el = this._ballNote; if (!el) return;
      this._ballName = name;
      this._ballNoteT.textContent = text;
      this._ballNoteMsg.textContent = msg || "";
      const b = el.querySelector(".bn-roll");
      b.setAttribute("data-poke-toy", name); b.disabled = false;
      b.title = "roll the ball back to " + SELF_NAME + ": your turn (rung 7), tagged as yours";
      el.hidden = false;
    }

    _ballHide() {
      this._ballName = null;
      if (this._ballNote) this._ballNote.hidden = true;
      this._titleStop();
    }

    /** The toast's buttons: Roll it back (the dock's own poke, painted at once), or ✕. */
    async _onBallNote(e) {
      const t = e.target && e.target.closest ? e.target.closest("button") : null;
      if (!t) return;
      if (t.classList.contains("bn-x")) { this._ballHide(); return; }
      if (!t.classList.contains("bn-roll")) return;
      const name = t.getAttribute("data-poke-toy"), text = this._ballNoteT.textContent;
      const out = await this._onToyPoke(e);
      if (out && !out.ok) this._ballShow(name, text, "✗ " + (out.error || "the roll failed"));
    }

    /** The hidden tab's title flashes until the page is looked at or the ball leaves your side. */
    _titleStart(text) {
      this._titleStop();
      this._titleBase = document.title;
      let tick = 0;
      document.title = titleFlash(this._titleBase, text, tick);
      this._titleT = setInterval(() => { document.title = titleFlash(this._titleBase, text, ++tick); }, 1000);
    }

    _titleStop() {
      if (this._titleT == null) return;
      clearInterval(this._titleT); this._titleT = null;
      document.title = this._titleBase;
    }

    /** Self's cycle (the chat's event stream): its turn with the ball happens in
     *  one, so read the toys now (at most once a second, hidden or not) and once
     *  more after the seat's dock cache (2 s) has turned over. */
    _onSelfCycle() {
      const now = Date.now();
      if (now - (this._cycAt || 0) >= 1000) { this._cycAt = now; this._dockTick(true); }
      clearTimeout(this._cycT);
      this._cycT = setTimeout(() => this._dockTick(true), 2300);
    }

    _dockToy(name) { return (this._dockToys || []).find((t) => t.name === name) || null; }

    /** Open the popover on a toy (from its icon). */
    _dockOpen(name, btn) {
      if (!name || !btn) return;
      clearTimeout(this._dockHideT);
      if (this._dockCur !== name) {
        for (const b of this._toyDockL.querySelectorAll("button[data-dock-toy]")) b.setAttribute("aria-expanded", "false");
        this._dockCur = name;
        this._dockPopHtml = null;
      }
      btn.setAttribute("aria-expanded", "true");
      this._dockBtnEl = btn;
      this._dockPop.hidden = false;
      this._dockPaintPop();
      this._dockPlace();
    }

    /** Beside its icon, kept on screen (its height changes with a poke's result). */
    _dockPlace() {
      const btn = this._dockBtnEl, p = this._dockPop;
      if (!btn || !p || p.hidden) return;
      const r = btn.getBoundingClientRect();
      p.style.left = Math.round(r.right + 8) + "px";
      p.style.top = Math.round(Math.max(8, Math.min(r.top - 6, innerHeight - p.offsetHeight - 8))) + "px";
    }

    _dockPaintPop() {
      const t = this._dockToy(this._dockCur), p = this._dockPop, c = this._chat;
      if (!t) { this._dockClose(); return; }
      const html = c._toyPopoverHtml(t, (this._dockRes || {})[t.name]);
      if (html === this._dockPopHtml) return;
      const focused = this.shadowRoot.activeElement;
      if (focused && p.contains(focused) && this._dockPopHtml) return;   // never yank a button from under the keyboard
      p.innerHTML = html; this._dockPopHtml = html;
      p.setAttribute("aria-label", t.name);
      this._dockPlace();
    }

    _dockClose(focus) {
      clearTimeout(this._dockHideT);
      if (!this._dockPop || this._dockPop.hidden) { this._dockCur = null; return; }
      this._dockPop.hidden = true;
      if (this._dockBtnEl) {
        this._dockBtnEl.setAttribute("aria-expanded", "false");
        if (focus) this._dockBtnEl.focus();
      }
      this._dockCur = null; this._dockPopHtml = null;
    }

    _dockHideSoon() {
      clearTimeout(this._dockHideT);
      this._dockHideT = setTimeout(() => {
        const a = this.shadowRoot.activeElement;
        if (a && this._dockPop.contains(a)) return;          // the keyboard is in it
        this._dockClose();
      }, 350);
    }

    /** The phone's 🧸: expand or collapse the dock (remembered per browser). */
    _toggleDock(on) {
      const d = this._toyDock; if (!d) return;
      on = on == null ? !d.classList.contains("open") : !!on;
      d.classList.toggle("open", on);
      this._dockBtn.setAttribute("aria-expanded", on ? "true" : "false");
      lsSet(LS_DOCK, on ? "1" : "0");
      if (!on) this._dockClose();
    }

    // PLAY WITH SELF (#244): a toy's play button (data-poke-toy / -action /
    // -arg, panel.js toyButtonsHtml) -> the chat's _toyPoke, the panel's ONE
    // write (toybox actions only; the seat tags it with this page's person).
    // Self sees the change on its next look as one it did not cause.
    async _onToyPoke(e) {
      const btn = e.target && e.target.closest ? e.target.closest("button[data-poke-toy]") : null;
      const c = this._chat;
      if (!btn || btn.disabled || !c || typeof c._toyPoke !== "function") return;
      const toy = btn.getAttribute("data-poke-toy"), action = btn.getAttribute("data-poke-action");
      const arg = btn.getAttribute("data-poke-arg");
      btn.disabled = true;
      this._dockSeq = (this._dockSeq || 0) + 1;            // #373: a read from before this poke is stale
      // #325: paint the expected result at once; the seat's reading replaces it, a failure puts it back
      const before = this._dockToys, guess = typeof c._toyAfterPoke === "function" && before;
      if (guess) this._paintDock(c._toyAfterPoke(before, toy, action, null));
      let out;
      try { out = await c._toyPoke(toy, action, arg); } catch (err) { out = { ok: false, error: String(err) }; }
      btn.disabled = false;
      this._dockSeq++;                                     // ...and so is one that started during it
      if (guess && !out.ok) this._paintDock(before);
      else if (guess && out.reading != null && this._dockToys) this._paintDock(c._toyAfterPoke(this._dockToys, toy, action, out.reading));
      if (out.ok) this._dockTick(true);                    // #373: every successful poke reads the toys again
      const say = out.ok
        ? "✓ " + action + " " + toy + (out.block ? " block " + out.block : "") + " → " + JSON.stringify(out.reading || {}) + " (by " + out.by + ")"
        : "✗ " + (out.error || "the poke failed");
      if (this._dockPop && this._dockPop.contains(btn)) {   // #249: the dock's popover shows it inline
        this._dockRes = this._dockRes || {};
        this._dockRes[toy] = { ok: !!out.ok, text: say };
        this._dockPopHtml = null;
        this._dockPaintPop();
        return out;
      }
      const el = this._spPlay;
      if (el) {
        el.hidden = false;
        el.classList.toggle("bad", !out.ok);
        el.textContent = out.ok
          ? "✓ " + action + " " + toy + (out.block ? " block " + out.block : "") + " → " + JSON.stringify(out.reading || {}) + " (by " + out.by + ")"
          : "✗ " + (out.error || "the poke failed");
        clearTimeout(this._spPlayT);
        this._spPlayT = setTimeout(() => { el.hidden = true; }, 6000);
      }
      if (out.ok) { this._spSeatAt = 0; this._paintSelfPanel(); }
      return out;
    }

    // THE BRAIN ON THE SCREEN (#237, the operator: a stranger opens membrane
    // home and watches a mind at work). Self's record (the 👶 panel's feed,
    // GET /membrane/self/recent) is polled every BRAIN_MS while the page is
    // visible; each new line, oldest first, goes onto the field (field3d.js:
    // the look's beam + label, the trail, a shift's pulse, a wait's dim, a
    // level-up's gold) and the newest one into the caption, in plain words,
    // citing its line. READ-ONLY: nothing here posts or changes self.
    _brainStart() {
      clearInterval(this._brainT);
      this._brainSeen = null; this._brainOtherAt = 0;
      this._brainT = setInterval(() => this._brainTick(), BRAIN_MS);
      if (!this._brainVis) {
        this._brainVis = () => { if (!document.hidden) this._brainTick(); };   // back in view: catch up at once
        document.addEventListener("visibilitychange", this._brainVis);
      }
      this._brainTick();
    }

    _brainMe() { return String(this._me || "").replace(/^person:/, ""); }

    async _brainTick() {
      const c = this._chat, v = this._v3;
      if (document.hidden || this._brainBusy || !v || !c || typeof c._selfRecent !== "function") return;
      this._brainBusy = true;
      try {
        const lines = await c._selfRecent(30);
        if (lines) this._brainLines = lines;               // #289: the rung rule reads its void lines
        if (!lines || !this._v3) return;
        const first = this._brainSeen == null;
        const out = brainFresh(lines, this._brainSeen || []);
        this._brainSeen = out.seen;
        await this._brainOther(lines);
        const me = this._brainMe();
        // a fresh page: the trail from the last looks, no replayed pulses
        const todo = first ? out.fresh.filter((r) => r.kind === "reach").slice(-8) : out.fresh;
        const newest = lines.reduce((m, r) => Math.max(m, Number(r && r.ts) || 0), 0);
        for (const r of todo) {
          // r.fid (#244): the seat's own field id for the thing (field_id), tried first
          let at = null;
          if (r.kind === "reach") v.selfLook(r.eid, at = await v.placeOf(r.fid || r.eid).catch(() => null), brainLabel(r.eid, me), r.answer);
          else if (r.kind === "shift") v.selfShift(r.eid, at = await v.placeOf(r.eid).catch(() => null), brainLabel(r.eid, me));
          else if (r.kind === "wait") v.selfWait();
          else if (r.kind === "rung") v.selfLevelUp(r.rung);
          else if (r.kind === "unlock" && !r.late && r.rung && v.selfLevelUp) v.selfLevelUp(r.rung, unlockText(r));   // #267: the gold ring names the new toy
          // #257: the amoeba's body acts it out (fresh lines only, never on a page's first read)
          if (!first && v.selfActs && actableLine(r, newest)) v.selfActs(r, at);
        }
        if (lines.length && (first || out.fresh.length)) this._paintBrain(lines[0]);
      } catch (e) { /* the next tick */ } finally { this._brainBusy = false; }
    }

    /** The person self meets: status().meets.other (via the cycle or the seat), else the last push's. */
    async _brainOther(lines) {
      const v = this._v3, c = this._chat;
      const cyc = ((v.amoeba && v.amoeba.last) || {}).meets || {};
      const seat = (((c && c._seat) || {}).self || {}).meets || {};
      const push = (lines || []).find((r) => r.kind === "push");
      const other = cyc.other || seat.other || (push && (push.to || push.eid)) || null;
      if (!other) { v.setSelfOther(null); return; }
      if (other === this._brainOtherId && Date.now() - this._brainOtherAt < 15000) return;
      this._brainOtherId = other; this._brainOtherAt = Date.now();
      v.setSelfOther(brainLabel(other, this._brainMe()), await v.placeOf(other).catch(() => null));
    }

    _paintBrain(r) {
      const el = this._brainEl; if (!el || !r) return;
      const text = brainCaption(r, this._brainMe());
      const cite = (r.n != null ? "#" + r.n + " · " : "") + (r.kind || "?");
      if (el._t === text + cite) return;
      el._t = text + cite;
      this._brainText.textContent = text;
      this._brainCite.textContent = cite;
      const c = this._chat;
      el.title = "from " + SELF_NAME + "'s record: " + (c && typeof c._selfLine === "function" ? c._selfLine(r) : r.kind)
        + (r.ts ? " · " + new Date(r.ts * 1000).toLocaleTimeString() : "");
      el.hidden = false;
      el.classList.remove("fresh"); void el.offsetWidth; el.classList.add("fresh");
    }
    // ---- end of the brain on the screen

    // THE RUNG CELEBRATION (#289): the seat's passed rung (status().rung, the
    // panel's seat state: void-aware, kept live by its self.cycle events)
    // against the last one this viewer celebrated (localStorage, per seat and
    // per self); rungToCelebrate decides. A hidden page notifies at once and
    // keeps the overlay for when it is looked at again.
    _rungTick() {
      const c = this._chat, st = c && c._seat, s = st && st.self;
      if (!s) return;
      const self = st.self_name || "Blurp", name = st.self_name || SELF_NAME;
      if (Array.isArray(s.earned)) {
        // #295: once per newly earned achievement (the seat's earned list,
        // void-aware), remembered per seat and self; the old per-rung memory
        // marks the rungs this viewer already celebrated as seen
        const key = achieveKey(SEAT, self);
        const out = achievementsToCelebrate(s.earned, lsGet(key, null), lsGet(rungKey(SEAT, self), null));
        if (out.last != null) lsSet(key, JSON.stringify(out.last));
        if (out.ids.length) {
          const names = out.ids.map((i) => { const e = s.earned.find((x) => x && x.id === i); return (e && e.name) || i; });
          const wait = Array.isArray(s.awaiting) ? s.awaiting.map(String) : [];
          out.ids.forEach((i, k) => this._notify(achieveText(name, names[k]), key + "." + i,
            achieveNotifyBody(wait.includes(i))));                // #299: it awaits the operator's accept
          this._cbPending = { text: achieveText(name, names.join(" and ")) + "!" };
        }
        this._askTick(s, name);                                   // #299: an earned one awaits the accept
      } else if (typeof s.rung === "number") {             // a seat from before the tree
        const key = rungKey(SEAT, self);
        const out = rungToCelebrate(s.rung, lsGet(key, null), this._brainLines || []);
        if (out.last != null) lsSet(key, String(out.last));
        if (out.rung != null) {
          const rn = s.rung === out.rung && s.rung_name ? s.rung_name : BRAIN_RUNGS[out.rung];
          this._notify(rungNotifyText(name, out.rung, rn), key + "." + out.rung,
            "a new rung on the ladder -- open membrane home to see it");
          this._cbPending = { text: celebrateText(name, out.rung, rn) };
        }
      }
      if (this._cbPending && !document.hidden) {
        const p = this._cbPending; this._cbPending = null;
        this._celebrate(p.text);
      }
    }

    _notifyState() {
      const api = typeof Notification !== "undefined";
      return { on: !!this._bellOn, secure: !!window.isSecureContext, api, permission: api ? Notification.permission : "default",
               asked: lsGet(LS_BELL + ".asked", "0") === "1",
               gesture: !!(this._gesture || (navigator.userActivation && navigator.userActivation.hasBeenActive)) };
    }

    _paintBell() {
      const b = this._bell; if (!b) return;
      const v = bellView(this._notifyState());
      b.textContent = v.icon;
      b.title = SELF_NAME + "'s rungs and ball: " + v.title;
      b.classList.toggle("off", !v.on);
      b.setAttribute("aria-pressed", v.on ? "true" : "false");
    }

    /** The 🔔: on with notifications still askable -> ask (this click is the
     *  gesture the browser wants); otherwise mute / unmute. */
    _onBell() {
      this._gesture = true;
      const v = bellView(this._notifyState());
      if (v.ask) {
        lsSet(LS_BELL + ".asked", "1");                     // once: a dismissed prompt is an answer too
        Promise.resolve(Notification.requestPermission()).catch(() => null).then(() => this._paintBell());
      } else {
        this._bellOn = !this._bellOn;
        lsSet(LS_BELL, this._bellOn ? "1" : "0");
      }
      if (this._bellOn) this._audio();                     // unlocked by this tap, for the chime later
      this._paintBell();
    }

    _audio() {
      try {
        if (!this._actx) { const C = window.AudioContext || window.webkitAudioContext; if (!C) return null; this._actx = new C(); }
        if (this._actx.state === "suspended") this._actx.resume().catch(() => {});
        return this._actx;
      } catch (e) { return null; }
    }

    /** A short chime, synthesized (no asset files): only with the bell on and after a gesture. */
    _chime(notes) {
      if (!chimeOk(this._notifyState())) return;
      const ctx = this._audio(); if (!ctx) return;
      const t0 = ctx.currentTime + 0.03, out = ctx.createGain();
      out.gain.value = 0.18; out.connect(ctx.destination);
      for (const n of notes || chimeNotes()) {           // #331: the ball has its own
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = "triangle"; o.frequency.value = n.f;
        g.gain.setValueAtTime(0.0001, t0 + n.t);
        g.gain.exponentialRampToValueAtTime(1, t0 + n.t + 0.015);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + n.t + n.dur);
        o.connect(g); g.connect(out);
        o.start(t0 + n.t); o.stop(t0 + n.t + n.dur + 0.05);
      }
    }

    _notify(text, tag, body) {
      if (!notifyReady(this._notifyState())) return;
      try {
        // a tag per seat and achievement: the browser shows one, however many tabs see it
        const n = new Notification(text, { tag: tag, icon: SEAT_LOGO, body: body });
        n.onclick = () => { try { window.focus(); n.close(); } catch (e) { /* gone */ } };   // #331: a click brings the page
      } catch (e) { /* a browser that only notifies from a service worker */ }
    }

    /** ~3 s over the field: the caption, a burst in the amoeba's colours, the
     *  amoeba bouncing and glowing; a still banner under reduced motion. Never
     *  in the way (pointer-events none); any click ends it. */
    _celebrate(text) {
      const el = this._cb; if (!el) return;
      const still = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      this._cbText.textContent = text;
      this._cbText.hidden = false;
      el.classList.toggle("still", still);
      el.hidden = false;
      const t = this._cbText; t.style.animation = "none"; void t.offsetWidth; t.style.animation = "";   // restart the pop
      this._chime();
      if (!still && this._v3 && typeof this._v3.selfCelebrate === "function") this._v3.selfCelebrate();
      if (!still) this._burst();
      clearTimeout(this._cbT);
      this._cbT = setTimeout(() => this._celebrateEnd(), still ? 6000 : CELEBRATE_MS);
    }

    _celebrateEnd() {
      if (!this._cb || this._cb.hidden) return;
      clearTimeout(this._cbT);
      cancelAnimationFrame(this._cbRaf);
      if (this._askId) {                                       // #299: the question stays until it is answered
        this._cbText.hidden = true;
        const g = this._cbCanvas && this._cbCanvas.getContext && this._cbCanvas.getContext("2d");
        if (g) g.clearRect(0, 0, this._cbCanvas.width, this._cbCanvas.height);
        return;
      }
      this._cb.hidden = true;
    }

    /** #299: ask the operator to accept the first awaiting achievement this
     *  page was not told "later" about -- with the burst when it is new, alone
     *  (over the field, no burst) when it was earned while away. */
    _askTick(s, name) {
      if (this._askId || document.hidden || !this._cbAcc) return;
      const id = acceptToAsk(s.awaiting, this._askNo);
      if (id == null) return;
      const e = (s.earned || []).find((x) => x && x.id === id);
      this._askId = id;
      this._cbQ.textContent = acceptAskText(name, (e && e.name) || id);
      this._cbMsg.textContent = "";
      this._cbYes.disabled = false; this._cbNo.disabled = false;
      this._cbAcc.hidden = false;
      if (this._cb.hidden) { this._cbText.hidden = true; this._cb.classList.add("still"); this._cb.hidden = false; }
    }

    _onAskYes() {
      const id = this._askId; if (!id) return;
      this._cbYes.disabled = true; this._cbNo.disabled = true;
      this._cbMsg.textContent = "accepting…";
      this._acceptSelf(id).then((out) => {
        if (out && out.ok) { this._askDone(false); return; }
        this._cbYes.disabled = false; this._cbNo.disabled = false;
        this._cbMsg.textContent = "not accepted: " + ((out && out.error) || "the seat refused");
      });
    }

    /** The question answered (accepted, or "later": not asked again on this page view). */
    _askDone(later) {
      if (later && this._askId) this._askNo.push(this._askId);
      this._askId = null;
      if (this._cbAcc) this._cbAcc.hidden = true;
      if (this._cbText) this._cbText.hidden = false;
      if (this._cb) { this._cb.hidden = true; this._cb.classList.remove("still"); }
      clearTimeout(this._cbT);
      cancelAnimationFrame(this._cbRaf);
    }

    /** The particle burst: from the amoeba when it is on the screen, else the middle. */
    _burst() {
      const cv = this._cbCanvas; if (!cv) return;
      const W = cv.clientWidth || innerWidth, H = cv.clientHeight || innerHeight, dpr = Math.min(2, window.devicePixelRatio || 1);
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      const g = cv.getContext("2d"); if (!g) return;
      let ox = W / 2, oy = H * 0.45;
      const v = this._v3, sp = v && typeof v.selfScreen === "function" ? v.selfScreen() : null;
      if (sp && sp[0] >= 0 && sp[0] <= W && sp[1] >= 0 && sp[1] <= H) { ox = sp[0]; oy = sp[1]; }
      const ps = [];
      for (let i = 0; i < 140; i++) {
        const a = Math.random() * 2 * Math.PI, sp2 = 2 + Math.random() * 7;
        ps.push({ x: ox, y: oy, vx: Math.cos(a) * sp2, vy: Math.sin(a) * sp2 - 2.5, r: 2 + Math.random() * 3.5,
                  c: BLURP_COLOURS[i % BLURP_COLOURS.length], spin: Math.random() * 6 });
      }
      const t0 = performance.now();
      cancelAnimationFrame(this._cbRaf);
      const step = (now) => {
        const u = (now - t0) / CELEBRATE_MS;
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.clearRect(0, 0, W, H);
        if (u >= 1 || this._cb.hidden) return;
        g.globalAlpha = Math.max(0, 1 - u * u);
        for (const p of ps) {
          p.vy += 0.12; p.vx *= 0.99; p.x += p.vx; p.y += p.vy;
          g.fillStyle = p.c;
          g.beginPath(); g.arc(p.x, p.y, p.r * (1 + 0.25 * Math.sin(p.spin + now / 90)), 0, 2 * Math.PI); g.fill();
        }
        this._cbRaf = requestAnimationFrame(step);
      };
      this._cbRaf = requestAnimationFrame(step);
    }
    // ---- end of the rung celebration

    // THE 🦠 ZOO (#283): the cards refresh only while it is open
    _toggleZoo(on) {
      const p = this._zoo, b = this._zooBtn; if (!p || !b) return;
      on = on == null ? p.hidden : !!on;
      p.hidden = !on;
      b.classList.toggle("on", on);
      b.setAttribute("aria-expanded", on ? "true" : "false");
      clearInterval(this._zooT); this._zooT = null;
      if (on) {
        this._zooMsg.textContent = "";
        this._paintZoo();
        this._zooT = setInterval(() => this._paintZoo(), ZOO_MS);
      }
    }

    async _paintZoo() {
      if (!this._hass || !this._zoo || this._zoo.hidden) return;
      try {
        const r = await this._hass.fetchWithAuth(HZOO);
        if (!r.ok) throw new Error(String(r.status));
        this._zooList.innerHTML = zooCards(await r.json());
        this._zooBodies();                                    // #358: each seat's Blurp
      } catch (e) { this._zooMsg.textContent = "the zoo did not answer (" + (e && e.message || e) + ")"; }
    }

    /** Make seat `id` the house's: an admin's write; the page reloads onto it. */
    async _zooSwitch(id) {
      this._zooMsg.textContent = "switching…";
      try {
        const r = await this._hass.fetchWithAuth(HZOO + "/active", { method: "POST",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) });
        if (r.ok) { location.reload(); return; }
        this._zooMsg.textContent = r.status === 403 ? "only an admin switches the seat" : "the switch failed (" + r.status + ")";
      } catch (e) { this._zooMsg.textContent = "the switch failed (" + (e && e.message || e) + ")"; }
    }

    // ---------------------------------------------------------- HA's own UI, one button away
    _dock(mode) {
      // HA's frontend listens for this (the sidebar's docked / auto /
      // always_hidden setting, which it keeps in localStorage 'dockedSidebar')
      if (mode === "always_hidden" && !this._hidSidebar) {
        let was = "docked";
        try { was = JSON.parse(lsGet("dockedSidebar", '"docked"')) || "docked"; } catch (e) { /* keep docked */ }
        this._sidebarWas = was === "always_hidden" ? "docked" : was;
      }
      if (mode === "restore") mode = this._sidebarWas || "docked";
      this.dispatchEvent(new CustomEvent("hass-dock-sidebar", { detail: { dock: mode }, bubbles: true, composed: true }));
      this._hidSidebar = mode === "always_hidden";
    }

    _toggleMenu(open) {
      open = open == null ? !this._menu.classList.contains("open") : open;
      if (open) this._paintMenu();
      this._menu.classList.toggle("open", open);
      this._haBtn.setAttribute("aria-expanded", String(open));
      this._haBtn.classList.toggle("on", open);
    }

    _paintMenu() {
      const h = this._hass || {};
      const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      if (h.standalone) {
        // THE STANDALONE BURGER (2026-10-13, the operator: "bring the burger
        // menu back (the one that works in hass mode), just with links to
        // chat-only and graph-only interfaces"): no HA sidebar here and
        // nothing to log out of -- the three views of the same page; the
        // view rides ?view= (VIEW). No <ha-icon> in standalone (HA's own
        // element): the icon is a plain span.
        const row = (r) => '<a role="menuitem" href="/' + (r.v ? "?view=" + r.v : "") + '"' +
          (r.current ? ' aria-current="page"' : "") + '><span class="mi">' + r.icon +
          '</span><span>' + esc(r.title) + "</span></a>";
        this._menu.innerHTML = standaloneMenu(VIEW)
          .map(row).join("");
        return;
      }
      let order = [], hidden = [];
      try { order = JSON.parse(lsGet("sidebarPanelOrder", "[]")) || []; } catch (e) { /* HA's own key */ }
      try { hidden = JSON.parse(lsGet("sidebarHiddenPanels", "[]")) || []; } catch (e) { /* HA's own key */ }
      const self = (this._panel && this._panel.url_path) || "membrane";
      const items = menuEntries(h.panels, order, hidden, self);
      const row = (e) => '<a role="menuitem" href="/' + esc(e.path) + '" data-path="' + esc(e.path) + '"><ha-icon icon="' +
        esc(e.icon) + '"></ha-icon><span>' + esc(e.title) + "</span></a>";
      const main = items.filter((e) => !TAIL.includes(e.path)), tail = items.filter((e) => TAIL.includes(e.path));
      this._menu.innerHTML = main.map(row).join("") + (tail.length ? "<hr>" + tail.map(row).join("") : "") +
        '<hr><a role="menuitem" href="#" data-logout="1"><ha-icon icon="mdi:logout"></ha-icon><span>Log out</span></a>';
    }

    /** Log out (HA's own 'hass-logout': it revokes the refresh token and goes
     * to the login page). Not a "left through the menu": the next login lands
     * on membrane again. */
    _logout() {
      lsSet(LS_LEFT, "0");
      this._toggleMenu(false);
      this._dock("restore");
      this.dispatchEvent(new CustomEvent("hass-logout", { bubbles: true, composed: true }));
    }

    /** Leave the field for an HA page: HA's sidebar comes back, and the next
     * fresh start stays in HA (until membrane is opened again). */
    _leaveTo(path) {
      // the profile page is where people log out: it is not "switching to HA"
      lsSet(LS_LEFT, path === "/profile" ? "0" : "1");
      this._toggleMenu(false);
      this._dock("restore");
      history.pushState(null, "", path);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
    }

    // ---------------------------------------------------------- dom
    _render() {
      const s = document.createElement("style");
      s.textContent = HSTYLE;
      const root = document.createElement("div");
      const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      const nm = esc(SELF_NAME);                    // #277: the name it goes by
      root.innerHTML = `
        <div class="field"></div>
        <div class="msg">finding you on the field…</div>
        <div class="top glass">
          <button class="ha" title="Home Assistant: its dashboards and pages" aria-haspopup="menu" aria-expanded="false" aria-label="Home Assistant menu"><img class="seat-logo" src="${SEAT_LOGO}" alt="" width="26" height="26"></button>
          <button class="zoobtn" title="The zoo: this house's seats, each with its own ${nm} -- tap one to talk to it" aria-label="the zoo: this house's seats" aria-haspopup="dialog" aria-expanded="false" aria-controls="zoopanel">🦠</button>
          <button class="me" title="What the camera follows: activity (whichever mind is working, else where the field changes) · seat · you · ${nm} (the 🌱 loop, framed to what it is doing) · 👁 ${nm}'s eyes (looking out from it)">◎ activity</button>
          <button class="chatbtn" title="Show / hide the chat">💬</button>
          <button class="pausebtn" title="the seat's pause -- reading its state…" aria-label="the seat's pause" aria-pressed="false">▶</button>
          <span class="qwarn" hidden><button class="qbtn" aria-haspopup="true">⚠</button><span class="qpop glass" role="tooltip"></span></span>
          <span class="selfstat" title="where ${nm} is now: its rung, the bar toward the next, what it waits on"></span>
          <button class="rungbell" aria-label="${nm}'s rungs and ball: the chime and notifications" aria-pressed="true">🔔</button>
          <button class="statbtn" title="Stats: the seat's tokens -- today, the last hour, the Claude rung" aria-label="stats" aria-haspopup="dialog" aria-expanded="false" aria-controls="statpanel">📊</button>
          <button class="optbtn" title="Options: the seat's long-term settings" aria-label="options" aria-haspopup="dialog" aria-expanded="false" aria-controls="optpanel">⚙</button>
          <button class="tilt" hidden title="Turn the view by tilting the device">📱</button>
          <span class="sbox"><input class="q" placeholder="search entities…" autocomplete="off"><div class="hits glass"></div></span>
          <span class="status"></span>
        </div>
        <nav class="hamenu glass" role="menu"></nav>
        <aside class="zoopanel glass" id="zoopanel" aria-label="the zoo: this house's seats" hidden>
          <div class="zhead"><span>🦠 the zoo</span><button class="zx" aria-label="close the zoo" title="Close">×</button></div>
          <div class="zlist"></div>
          <p class="zmsg" aria-live="polite"></p>
        </aside>
        <aside class="optpanel glass" id="optpanel" role="dialog" aria-label="options: the seat's long-term settings" hidden>
          <div class="ohead"><span>⚙ options</span><button class="ox" aria-label="close the options" title="Close">×</button></div>
          <div class="obody"></div>
          <p class="omsg" role="status" aria-live="polite"></p>
        </aside>
        <aside class="optpanel statpanel glass" id="statpanel" role="dialog" aria-label="stats: the seat's tokens" hidden>
          <div class="ohead"><span>📊 stats</span><button class="stx" aria-label="close the stats" title="Close">×</button></div>
          <div class="stbody"></div>
          <p class="stmsg" role="status" aria-live="polite"></p>
        </aside>
        <button class="babybtn" aria-label="${nm}: its character sheet and what it is doing" aria-expanded="false" aria-controls="selfpanel"
                title="${nm} (#161): its character sheet -- level, attributes, quest, growth, toys, the others it met -- and what it is doing now"><canvas class="bbody" width="72" height="72" aria-hidden="true"></canvas></button>
        <aside class="selfpanel glass" id="selfpanel" aria-label="${nm}'s character sheet" hidden>
          <div class="sphead"><span class="sptitle"><canvas class="bbody sm" width="52" height="52" aria-hidden="true"></canvas> ${nm}</span><button class="spsleep" type="button" aria-pressed="false" disabled title="${nm}: on or off -- tap to switch">⏻ off</button><button class="spx" aria-label="close ${nm}'s panel" title="Close">×</button></div>
          <div class="sptabs" role="tablist" aria-label="${nm}'s panel">
            <button type="button" class="sptab" role="tab" id="sptab-sheet" data-tab="sheet" aria-selected="true" aria-controls="sppane-sheet">Sheet</button>
            <button type="button" class="sptab" role="tab" id="sptab-tree" data-tab="tree" aria-selected="false" aria-controls="sppane-tree" tabindex="-1">Skill tree</button>
            <button type="button" class="sptab" role="tab" id="sptab-code" data-tab="code" aria-selected="false" aria-controls="sppane-code" tabindex="-1">Code</button>
          </div>
          <div class="sppane" role="tabpanel" id="sppane-sheet" aria-labelledby="sptab-sheet">
            <div class="spxp"></div>
            <div class="sheet spcard"></div>
            <details class="sec" data-sec="activity" data-was="0"><summary><span class="stt">Activity</span><span class="sth">live</span></summary>
              <div class="secb"><ol class="spact"><li class="sl none">…</li></ol></div></details>
          </div>
          <div class="sppane" role="tabpanel" id="sppane-tree" aria-labelledby="sptab-tree" hidden><div class="sktree"></div></div>
          <div class="sppane" role="tabpanel" id="sppane-code" aria-labelledby="sptab-code" hidden><div class="spcode"></div></div>
          <p class="spplay" aria-live="polite" hidden></p>
        </aside>
        <div class="toydock" hidden>
          <button type="button" class="dockbtn" aria-label="${nm}'s toys" aria-expanded="false" aria-controls="toydockl" title="${nm}'s toys">🧸</button>
          <ul class="dkl" id="toydockl" aria-label="${nm}'s toys: hover or tap one to see it and play"></ul>
        </div>
        <div class="dkpop glass" role="dialog" aria-label="a toy" hidden></div>
        <div class="chat glass"><ai-terminal-panel overlay></ai-terminal-panel></div>
        <div class="orb" aria-hidden="true"><canvas class="wave" width="560" height="140"></canvas></div>
        <div class="orbtext" aria-live="polite"></div>
        <div class="brainline glass" hidden><span class="bl-t"></span><span class="bl-c"></span></div>
        <div class="voice glass">
          <div class="vsay glass" role="status" aria-live="polite"></div>
          <button class="ptt" title="Hold to talk (or hold Space)" aria-label="Hold to talk">🎙</button>
          <button class="lock" title="Hands-free: tap to start, tap again to send" aria-label="Talk hands-free">🔒</button>
          <button class="ear" title="Hands-free by name: listen for “Hey ${SEAT}” (speech near this device is transcribed on your network)" aria-label="Listen for Hey ${SEAT}" aria-pressed="false">👂</button>
          <span class="vsep" aria-hidden="true"></span>
          <button class="eng" title="Where speech is recognised: on this device (private, fast) or on the brain phone (more accurate)" aria-label="Speech recognition engine">⚙</button>
          <button class="tts" title="Speak membrane's answers" aria-label="Speak answers" aria-pressed="false">🔈</button>
        </div>
        <div class="card glass"><div class="t"></div><div class="k"></div>
          <div class="row"><button class="ask">Ask about it</button><button class="go">Fly there</button><button class="x">×</button></div></div>
        <div class="tip glass"></div>
        <div class="celebrate" hidden aria-live="polite"><canvas class="cb-c"></canvas><div class="cb-t"></div>
          <div class="cb-acc" role="alertdialog" aria-modal="false" aria-labelledby="cbq" hidden><p class="cb-q" id="cbq"></p>
            <div class="cb-row"><button class="cb-yes" type="button">🏅 Accept</button><button class="cb-no" type="button">Later</button></div>
            <p class="cb-msg" role="status"></p></div></div>
        <div class="ballnote" role="alert" hidden><span class="bn-t"></span>
          <button type="button" class="bn-roll" data-poke-action="roll">Roll it back</button>
          <button type="button" class="bn-x" aria-label="dismiss">✕</button><p class="bn-msg" role="status"></p></div>`;
      this.shadowRoot.append(s, root);
      const $ = (q) => root.querySelector(q);
      this._host3d = $(".field"); this._msgEl = $(".msg"); this._statusEl = $(".status");
      this._chatBox = $(".chat"); this._chat = $("ai-terminal-panel");
      if (VIEW === "field") this._buildCockpit(root);   // k6: the field view, before the chat boots (hass below)
      this._card = $(".card"); this._tip = $(".tip");
      this._brainEl = $(".brainline"); this._brainText = $(".bl-t"); this._brainCite = $(".bl-c");
      this._q = $(".q"); this._hits = $(".hits");
      this._q.addEventListener("input", () => this._showHits());
      this._q.addEventListener("focus", () => this._showHits());
      this._q.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { this._q.value = ""; this._showHits(); this._q.blur(); }
        else if (e.key === "Enter") { const h = this._hits.querySelector(".hit"); if (h) h.click(); }
      });
      this._hits.addEventListener("click", (e) => {
        const h = e.target.closest(".hit"); if (!h) return;
        this._pickHit(h.dataset.id);
      });
      root.addEventListener("pointerdown", (e) => { if (!e.composedPath().includes(this._q.parentNode)) this._hits.classList.remove("open"); });
      this._wireVoice(root);
      this._wireTilt(root);
      if (this._hass) this._chat.hass = this._hass;
      if (this._narrow != null) this._chat.narrow = this._narrow;
      const chatOn = VIEW === "chat" || lsGet(LS_CHAT, "1") === "1";
      this._chatBox.classList.toggle("hidden", !chatOn);
      $(".chatbtn").classList.toggle("on", chatOn);
      // THE VIEW (2026-10-13, the operator's burger-menu ask): graph-only
      // hides the chat and its buttons; chat-only fills the screen with the
      // conversation -- the search box and the follow modes have nothing to
      // aim at there
      if (VIEW === "graph") {
        this._chatBox.classList.add("hidden");
        $(".chatbtn").style.display = "none";
      } else if (VIEW === "chat") {
        root.classList.add("view-chat");
      } else if (VIEW === "field") {
        root.classList.add("view-field");      // #470: the cockpit fills the screen
        this._mountDecisions();
      }
      this._menu = $(".hamenu"); this._haBtn = $(".ha");
      this._haBtn.title = window.MEMBRANE_STANDALONE
        ? "Membrane's interfaces: chat only, graph only, field" : this._haBtn.title;
      this._haBtn.addEventListener("click", () => this._toggleMenu());
      this._menu.addEventListener("click", (e) => {
        if (e.target.closest("a[data-logout]")) { e.preventDefault(); this._logout(); return; }
        const a = e.target.closest("a[data-path]"); if (!a) return;
        e.preventDefault();
        this._leaveTo("/" + a.dataset.path);
      });
      // any click outside the menu closes it
      root.addEventListener("pointerdown", (e) => {
        if (!this._menu.classList.contains("open")) return;
        const path = e.composedPath();
        if (!path.includes(this._menu) && !path.includes(this._haBtn)) this._toggleMenu(false);
      });
      root.addEventListener("keydown", (e) => { if (e.key === "Escape") this._toggleMenu(false); });
      this._mode = FOLLOW_MODES.includes(lsGet(LS_FOLLOW, "activity")) ? lsGet(LS_FOLLOW, "activity") : "activity";
      this._modeBtn = $(".me"); this._modeBtn.textContent = followLabel(this._mode);
      this._modeBtn.addEventListener("click", () => this._setMode(nextMode(this._mode)));
      $(".chatbtn").addEventListener("click", (e) => {
        const on = this._chatBox.classList.toggle("hidden") === false;
        e.currentTarget.classList.toggle("on", on);
        lsSet(LS_CHAT, on ? "1" : "0");
      });
      // THE SEAT'S PAUSE (#348): the ▶ / ⏸ beside 💬 pauses and resumes the
      // seat's acting lanes (PauseButton above); the chat's auto-run it
      // drove before is gone. Read now, polled while the page is visible.
      this._pause = new PauseButton({ btn: $(".pausebtn"), get: (p) => this._get(p),
        post: async (p, b) => {
          const r = await this._hass.fetchWithAuth(HSERVE + p, { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
          let d = null; try { d = await r.json(); } catch (e) { /* no body */ }
          if (!r.ok) throw new Error((d && d.error) || (r.status === 401 ? "the seat token is needed to pause the seat"
            : r.status === 403 ? "only an admin pauses the seat" : String(r.status)));
          return d;
        } });
      this._pause.btn.addEventListener("click", () => this._pause.click());
      this._pause.load();
      // OUT OF TOKENS (quota.py): the ⚠ beside the pause, its hover names what waits
      this._quotaBadge = new QuotaBadge({ wrap: $(".qwarn"), btn: $(".qbtn"), pop: $(".qpop"),
        get: (p) => this._get(p),
        post: async (p, b) => {
          const r = await this._hass.fetchWithAuth(HSERVE + p, { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
          let d = null; try { d = await r.json(); } catch (e) { /* no body */ }
          if (!r.ok) throw new Error((d && d.error) || (r.status === 401 ? "the seat token is needed" : String(r.status)));
          return d;
        } });
      this._quotaBadge.btn.addEventListener("click", () => this._quotaBadge.wrap.classList.toggle("open"));
      this._quotaBadge.pop.addEventListener("click", (ev) => {
        const b = ev.target.closest("[data-qresume]");
        if (b) { b.disabled = true; this._quotaBadge.resume(b.dataset.qresume); }
      });
      this._quotaBadge.load();
      this._pauseT = setInterval(() => {
        if (document.visibilityState === "hidden") return;
        this._pause.load();
        this._quotaBadge.load();
      }, PAUSE_POLL_MS);
      // #161 / #299: where self is (the exp bar); the 🌱 growth-limit chooser that
      // sat here gave way to acceptance -- sleep / wake is the 👶 panel's toggle
      this._selfStat = $(".selfstat");
      this._paintSelf();
      this._selfT = setInterval(() => { this._paintSelf(); this._rungTick(); this._bodyTick(); }, 1500);
      // #289: the 🔔 (the rung chime + notifications) and the celebration
      this._bell = $(".rungbell"); this._cb = $(".celebrate"); this._cbText = $(".cb-t"); this._cbCanvas = $(".cb-c");
      // #299: the celebration's Accept -- real buttons, the one clickable part of the overlay
      this._cbAcc = $(".cb-acc"); this._cbQ = $(".cb-q"); this._cbYes = $(".cb-yes"); this._cbNo = $(".cb-no"); this._cbMsg = $(".cb-msg");
      this._askNo = [];                                        // "later" for these, this page view
      this._cbYes.addEventListener("click", () => this._onAskYes());
      this._cbNo.addEventListener("click", () => this._askDone(true));
      this._cbAcc.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); this._askDone(true); } });
      this._bellOn = lsGet(LS_BELL, "1") !== "0";
      this._bell.addEventListener("click", () => this._onBell());
      this._paintBell();
      if (!this._gestureOn) {
        // the chime waits for the page's first gesture (autoplay rules); any click dismisses the celebration
        this._gestureOn = () => { this._gesture = true; };
        window.addEventListener("pointerdown", this._gestureOn, { capture: true, passive: true });
        window.addEventListener("keydown", this._gestureOn, { capture: true, passive: true });
      }
      root.addEventListener("pointerdown", () => this._celebrateEnd(), { capture: true, passive: true });
      // the 👶 panel (#230): read-only, remembered per browser
      this._babyBtn = $(".babybtn"); this._selfPanel = $(".selfpanel");
      this._spXp = $(".spxp"); this._spAct = $(".spact"); this._spCard = $(".spcard"); this._spPlay = $(".spplay");
      this._spCard.addEventListener("click", (e) => this._onToyPoke(e));     // #244: the toys' play buttons
      // #296: the panel's tabs (the sheet, the Skill tree): arrows move between them
      this._spTabs = Array.from(root.querySelectorAll(".sptab"));
      this._spPaneSheet = $("#sppane-sheet"); this._spPaneTree = $("#sppane-tree"); this._spTree = $(".sktree");
      this._spPaneCode = $("#sppane-code"); this._spCode = $(".spcode");
      // #299: the tree card's Accept; #394: the clicked skill, remembered per viewer
      this._spTreeSel = { id: lsGet(LS_TREESEL, null) || undefined, onAccept: (id) => this._acceptSelf(id),
                          onPick: (id) => lsSet(LS_TREESEL, id) };
      this._spSleep = $(".spsleep");
      this._spSleep.addEventListener("click", () => this._onSleep());
      for (const b of this._spTabs) {
        b.addEventListener("click", () => this._selfTab(b.dataset.tab));
        b.addEventListener("keydown", (e) => {
          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight" && e.key !== "Home" && e.key !== "End") return;
          e.preventDefault();
          const tabs = this._spTabs.map((t) => t.dataset.tab), i = tabs.indexOf(this._spTabNow);
          const j = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1
            : (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
          this._selfTab(tabs[j], true);
        });
      }
      this._selfTab(lsGet(LS_SELFTAB, "sheet"));
      this._babyBtn.addEventListener("click", () => this._toggleSelfPanel());
      this._wireBodies(root);                                 // #358: Blurp's body on the tab and the panel
      // the 🦠 zoo (#283): Home Assistant's seats; standalone is one seat
      this._zooBtn = $(".zoobtn"); this._zoo = $(".zoopanel"); this._zooList = $(".zlist"); this._zooMsg = $(".zmsg");
      if (window.MEMBRANE_STANDALONE) this._zooBtn.hidden = true;
      this._zooBtn.addEventListener("click", () => this._toggleZoo());
      $(".zx").addEventListener("click", () => { this._toggleZoo(false); this._zooBtn.focus(); });
      this._zoo.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.stopPropagation(); this._toggleZoo(false); this._zooBtn.focus(); }
      });
      // the ⚙ options window (#344): seat-wide settings, read on open, saved on change
      this._optBtn = $(".optbtn");
      this._opt = new OptionsWindow({ btn: this._optBtn, panel: $(".optpanel"), body: $(".obody"), msg: $(".omsg"),
        get: (p) => this._get(p),
        post: async (p, b) => {
          const r = await this._hass.fetchWithAuth(HSERVE + p, { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
          let d = null; try { d = await r.json(); } catch (e) { /* no body */ }
          if (!r.ok) throw new Error((d && d.error) || (r.status === 401 ? "the seat token is needed to change options" : String(r.status)));
          return d;
        } });
      this._optBtn.addEventListener("click", () => { if (this._stats) this._stats.toggle(false); this._opt.toggle(); });
      // the 📊 stats window: the seat's tokens, read on open and every 10 s while open
      this._statBtn = $(".statbtn");
      this._stats = new StatsWindow({ btn: this._statBtn, panel: $(".statpanel"), body: $(".stbody"), msg: $(".stmsg"),
        get: (p) => this._get(p) });
      this._statBtn.addEventListener("click", () => { if (!this._stats.open) this._opt.toggle(false); this._stats.toggle(); });
      $(".stx").addEventListener("click", () => { this._stats.toggle(false); this._statBtn.focus(); });
      this._stats.panel.addEventListener("keydown", (e) => this._stats.key(e));
      this._statBtn.addEventListener("keydown", (e) => this._stats.key(e));
      $(".ox").addEventListener("click", () => { this._opt.toggle(false); this._optBtn.focus(); });
      this._opt.panel.addEventListener("keydown", (e) => this._opt.key(e));
      this._optBtn.addEventListener("keydown", (e) => this._opt.key(e));   // Escape right after the click, focus still on ⚙
      this._opt.panel.addEventListener("input", (e) => {          // the % follows the thumb
        if (e.target.classList.contains("oshare")) { const o = this._opt.panel.querySelector(".oval"); if (o) o.textContent = e.target.value + "%"; }
      });
      this._opt.panel.addEventListener("change", (e) => {         // let go: save it
        if (e.target.classList.contains("oshare")) this._opt.setShare(e.target.value);
        else if (e.target.dataset && e.target.dataset.opt)
          this._opt.setOption(e.target.dataset.opt, e.target.type === "number" ? Number(e.target.value) : e.target.value);
      });
      this._zooList.addEventListener("click", (e) => {
        const c = e.target.closest(".zcard");
        if (c && !c.classList.contains("active")) this._zooSwitch(c.dataset.id);
      });
      $(".spx").addEventListener("click", () => { this._toggleSelfPanel(false); this._babyBtn.focus(); });
      this._selfPanel.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.stopPropagation(); this._toggleSelfPanel(false); this._babyBtn.focus(); }
      });
      // #249: the sheet's sections (open or closed, remembered per browser)
      try { this._sheetOpen = JSON.parse(lsGet(LS_SELFSHEET, "{}")) || {}; } catch (e) { this._sheetOpen = {}; }
      if (typeof this._sheetOpen !== "object" || Array.isArray(this._sheetOpen)) this._sheetOpen = {};
      this._spActSec = $('details[data-sec="activity"]');
      if (this._sheetOpen.activity) { this._spActSec.setAttribute("data-was", "1"); this._spActSec.open = true; }
      this._selfPanel.addEventListener("toggle", (e) => this._onSheetToggle(e), true);
      // #249: the toy dock under the 👶 tab
      this._toyDock = $(".toydock"); this._toyDockL = $(".dkl"); this._dockBtn = $(".dockbtn"); this._dockPop = $(".dkpop");
      this._dockBtn.addEventListener("click", () => this._toggleDock());
      if (lsGet(LS_DOCK, "0") === "1") this._toggleDock(true);
      this._toyDockL.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-dock-toy]"); if (!b) return;
        const name = b.getAttribute("data-dock-toy");
        if (this._dockCur === name && !this._dockPop.hidden && !this._dockHover) this._dockClose();
        else this._dockOpen(name, b);
        this._dockHover = false;
      });
      if (window.matchMedia && matchMedia("(hover: hover)").matches) {
        this._toyDockL.addEventListener("mouseover", (e) => {
          const b = e.target.closest("button[data-dock-toy]");
          if (b) { this._dockHover = true; this._dockOpen(b.getAttribute("data-dock-toy"), b); }
        });
        this._toyDockL.addEventListener("mouseleave", () => this._dockHideSoon());
        this._dockPop.addEventListener("mouseenter", () => clearTimeout(this._dockHideT));
        this._dockPop.addEventListener("mouseleave", () => this._dockHideSoon());
      }
      this._dockPop.addEventListener("click", (e) => this._onToyPoke(e));        // the play actions
      for (const el of [this._toyDock, this._dockPop]) {
        el.addEventListener("keydown", (e) => { if (e.key === "Escape" && this._dockCur) { e.stopPropagation(); this._dockClose(true); } });
      }
      root.addEventListener("pointerdown", (e) => {
        if (!this._dockCur) return;
        const path = e.composedPath();
        if (!path.includes(this._toyDock) && !path.includes(this._dockPop)) this._dockClose();
      });
      // #331: self rolled you the ball -- the toast (its button: the dock's own poke), and
      // self's cycles (the chat's event stream) check the toys at once, even with the tab hidden
      this._ballNote = $(".ballnote"); this._ballNoteT = $(".bn-t"); this._ballNoteMsg = $(".bn-msg");
      this._ballNote.addEventListener("click", (e) => this._onBallNote(e));
      this._ballNote.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); this._ballHide(); } });
      if (!this._cycOn) {
        this._cycOn = () => this._onSelfCycle();
        this._chat.addEventListener("ai-self-cycle", this._cycOn);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) this._titleStop(); });
      }
      this._dockStart();
      this._toggleSelfPanel(lsGet(LS_SELFPANEL, "0") === "1");
      $(".x").addEventListener("click", () => { this._card.classList.remove("open"); if (this._v3) this._v3.select(null); this._select(null); });
      $(".go").addEventListener("click", () => { if (this._v3 && this._sel) { this._lastTouch = performance.now(); this._v3.flyTo(this._sel.id); } });
      $(".ask").addEventListener("click", () => this._askAbout());
      // a manual orbit / zoom: ◎ self lets go for SELF_HOLD_MS after the last
      // touch, then follows again on its own; it leaves 👁 self's eyes (#237)
      const touched = () => {
        this._lastTouch = performance.now();
        const m = modeAfterTouch(this._mode);
        if (m !== this._mode) this._setMode(m, true);
      };
      this._host3d.addEventListener("pointerdown", touched);
      this._host3d.addEventListener("pointermove", (e) => { if (e.buttons) touched(); });   // a long drag keeps the hold
      this._host3d.addEventListener("wheel", touched, { passive: true });
    }

    /** The camera's mode. byTouch: a manual orbit left 👁 self's eyes -- ◎ self
     *  resumes a few seconds after the last touch. */
    _setMode(m, byTouch) {
      this._mode = m; lsSet(LS_FOLLOW, m); this._tracked = null;
      this._modeBtn.textContent = followLabel(m);
      if (this._v3) { this._v3.selfEyes = m === "eyes"; this._v3.selfFollow = m === "self" || m === "eyes"; }
      this._eyeDir = null;
      if (byTouch) { this._say("you took the camera: ◎ " + SELF_NAME + " picks it up again in a few seconds"); return; }
      this._lastTouch = -1e9; this._goal = null; this._aim(true);   // idle at once (a fresh page too)
    }

    /** The panels over the field (#237): the chat and the 👶 panel when open,
     *  the top bar, the voice row and the brain's caption -- rects in the
     *  field's own coordinates. */
    _occluders() {
      const host = this._host3d, root = this.shadowRoot;
      if (!host || !root) return [];
      const h = host.getBoundingClientRect(), out = [];
      for (const el of [this._chatBox, this._selfPanel, this._toyDock, this._dockPop, root.querySelector(".top"), root.querySelector(".voice"), this._brainEl]) {
        if (!el || el.hidden || el.classList.contains("hidden")) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) out.push({ x: r.left - h.left, y: r.top - h.top, w: r.width, h: r.height });
      }
      return out;
    }

    /** k6: the field view -- icons for goals, tasks and decisions over the field; the
     * normal chat stays as it is. It replaces Everything and the Decisions cockpit. */
    _buildCockpit(root) {
      const el = this._cockpit = document.createElement("membrane-fieldview");
      root.appendChild(el);
    }

    /** k6: the view's API once fieldview.js is loaded: it only GETs /membrane/work-graph. */
    async _mountDecisions() {
      try { await import("/ui/www/fieldview.js"); } catch (e) { this._msgEl.textContent = "the field view failed to load"; return; }
      this._cockpit.api = { get: (p) => this._get(p) };
      this._msgEl.style.display = "none";
    }

    async _get(path) {
      const r = await this._hass.fetchWithAuth(HSERVE + path);
      const t = await r.text();
      let d = null; try { d = t ? JSON.parse(t) : null; } catch (e) { d = t; }
      if (!r.ok) throw new Error(path + " → " + r.status);
      return d;
    }

    async _start() {
      this._started = true;
      const hass = this._hass;
      this._me = "person:" + personSlug(hass.user && hass.user.name);
      if (VIEW === "chat") {                           // THE VIEW: chat-only never boots the
        this._msgEl.style.display = "none"; return;   // field -- the camera
      }                                                 // loop is a no-op anyway
      try {
        if (!window.MembraneField3D) await import("/ui/www/field3d.js");
        const api = {
          get: (p) => this._get(p),
          stream: (p, signal) => hass.fetchWithAuth(HSERVE + p, { signal, headers: { Accept: "text/event-stream" } }),
          // the live layer for every signed-in user (the proxy relays it to them all)
          kindColor: hKindColor, isAdmin: true, maxFps: 30, labelIds: () => this._labelIds(),
          occluders: () => this._occluders(),          // #237: the labels round self keep out from under the panels
          snapshot: "membrane-home-" + ((hass.user && hass.user.id) || "anon"),   // IndexedDB: the last view at once
          onStatus: (t) => { this._v3Txt = t; this._paintStatus(); },
          onSelect: (m) => this._select(m),
          onAvatar: (agent) => this._selectMind(agent),
          onRelated: () => {},
          onHover: (m, x, y) => {
            if (!m) { this._tip.style.display = "none"; return; }
            this._tip.textContent = m.label + "  ·  " + m.kind;
            this._tip.style.left = Math.min(x + 14, (this._host3d.clientWidth || 0) - 240) + "px";
            this._tip.style.top = (y + 14) + "px"; this._tip.style.display = "block";
          },
        };
        const v = this._v3 = new window.MembraneField3D(this._host3d, api);
        if (window.matchMedia("(max-width: 760px)").matches) { v.maxBudget = 3000; v._applyBudget(3000); }
        v.selfEyes = this._mode === "eyes"; v.selfFollow = this._mode === "self" || this._mode === "eyes"; v.selfName = SELF_NAME;
        await v.start();
        if (!this._started) { v.stop(); return; }
        await this._aim(true);
        this._glide();
        if (VIEW !== "field") this._brainStart();    // the cockpit has no brain caption
      } catch (e) {
        this._msgEl.style.display = ""; this._msgEl.textContent = "the field is unavailable\n" + ((e && e.message) || e);
        return;
      }
      clearInterval(this._follow);
      this._follow = setInterval(() => this._aim(false), ACTIVITY_EVERY_MS);
    }

    // ---------------------------------------------------------- the camera
    async _locateWorld(ids) {
      const v = this._v3;
      for (const id of ids) {
        v.locateCache.delete(id);                            // entities move
        const hit = await v._locate(id);
        if (hit && hit.p) return [(hit.p[0] - 0.5) * 1000, (hit.p[1] - 0.5) * 1000, (hit.p[2] - 0.5) * 1000];
      }
      return null;
    }

    /** The labels: what is tracked and what it interacts with (its edges),
     * and in activity mode whatever is active. */
    _labelIds() {
      const v = this._v3, out = new Set();
      if (!v) return out;
      if (v.sel) {
        out.add(v.sel);
        if (v.selNb && v.selNb.id === v.sel) for (const x of v.selNb.nb) out.add(x.id);
        return out;
      }
      if (this._mode === "activity") {
        for (const id of v.activeIds()) out.add(id);
      } else if (this._tracked) {
        out.add(this._tracked.id);
        for (const id of this._tracked.nb) out.add(id);
      }
      return out;
    }

    async _trackNb(ids) {
      const v = this._v3;
      if (!v || !v.pid) return;
      for (const id of ids) {
        try {
          const hit = await v.api.get("/membrane/map/locate?pid=" + v.pid + "&eid=" + encodeURIComponent(id) + "&nb=1");
          if (hit && hit.id) { this._tracked = { id: hit.id, nb: (hit.neighbours || []).map((x) => x.id) }; return; }
        } catch (e) { /* the next candidate */ }
      }
    }

    async _posOf(id) {
      const v = this._v3, s = v.slots.get(id);
      if (s != null) return [v.posA[s * 3], v.posA[s * 3 + 1], v.posA[s * 3 + 2]];
      return this._locateWorld([id]);
    }

    async _aim(force) {
      const v = this._v3; if (!v) return;
      if (this._mindFollow) return;                         // a selected orb: the glide follows it
      if (this._selFollow) {                                // a selected node: the camera stays on it
        const id = this._selFollow, p = await this._posOf(id);
        if (p && this._selFollow === id) {
          this._goal = { p, r: this._goal && this._goal.sel ? this._goal.r : Math.min(v.orbit.r, 520), sel: true };
          this._say("following the selected " + id);
        }
        return;
      }
      const idle = followsNow(this._mode, performance.now() - this._lastTouch);
      if (!force && !idle) return;
      const now = performance.now();
      if (this._mode === "self" || this._mode === "eyes") {   // #161: the amoeba (the glide keeps up with it)
        const p = v.selfPos();
        if (p) {
          this._goal = { p, r: this._goal && this._goal.r ? this._goal.r : 420, frame: this._mode === "self" };
          this._msgEl.style.display = "none";
          this._say(this._mode === "eyes" ? "👁 through " + SELF_NAME + "'s eyes" : "following " + SELF_NAME);
          return;
        }
        this._msgEl.style.display = "";
        this._msgEl.textContent = SELF_NAME + " is not on the field yet — it shows up while it is on and awake (⏻ in its panel, the tab at the left edge)";
        setTimeout(() => { if (this._msgEl.textContent.startsWith(SELF_NAME + " is not")) this._msgEl.style.display = "none"; }, 6000);
        return;
      }
      if (this._mode === "activity") {
        const a = v.activity();
        if (a) {
          this._goal = { p: a.p, r: a.kind === "mind" ? 520 : 900 };
          this._say(a.kind === "mind" ? "following " + a.agent + " · " + a.eid : "following the field's changes");
          this._msgEl.style.display = "none";
          return;
        }
        if (this._goal && !force) return;                   // quiet: stay where it last happened
      }
      // seat / you, and activity's first aim before anything happened
      if (!force && this._lastLocate && now - this._lastLocate < FOLLOW_EVERY_MS) return;
      this._lastLocate = now;
      const ids = this._mode === "you" ? [this._me] : SEAT_IDS;
      const p = await this._locateWorld(ids);
      if (p) this._trackNb(ids);
      if (p) {
        this._goal = { p, r: 600 };
        this._msgEl.style.display = "none";
        this._say(this._mode === "you" ? "following you" : (this._mode === "seat" ? "following the seat" : "waiting for activity · at the seat"));
        return;
      }
      if (this._mode === "you") {
        this._msgEl.style.display = "";
        this._msgEl.textContent = "you are not on the field yet — say something to membrane";
        setTimeout(() => { if (this._msgEl.textContent.startsWith("you are not")) this._msgEl.style.display = "none"; }, 6000);
      } else {
        this._msgEl.style.display = "none";
      }
    }

    _say(t) { this._followTxt = t; this._paintStatus(); }
    _paintStatus() {
      if (this._statusEl) this._statusEl.textContent = [this._followTxt, this._v3Txt].filter(Boolean).join("  ·  ");
    }

    _glide() {
      let last = performance.now();
      const id = this._glideId = (this._glideId || 0) + 1;     // one loop, ever
      const step = (t) => {
        if (!this._started || !this._v3 || id !== this._glideId) return;
        const dt = Math.min(0.1, (t - last) / 1000); last = t;
        if (this._mindFollow) {                              // the selected orb moves: the camera goes with it
          const p = this._v3.avatarPos(this._mindFollow);
          if (p) this._goal = { p, r: this._goal && this._goal.sel ? this._goal.r : 420, sel: true };
        }
        const rm = this._v3.reducedMotion ? this._v3.reducedMotion() : false;
        if (this._mode === "eyes" && !this._mindFollow && !this._selFollow && !this._v3._flyAnim) {
          // 👁 SELF'S EYES (#237, the operator: "PUT ME INTO self's WORLD!"): the
          // camera sits at the amoeba and turns toward what it reaches for
          // (else its aim, else the way it heads) -- smoothly; at once under
          // reduced motion. A manual orbit leaves the mode (touched, above).
          const v = this._v3, sp = v.selfPos();
          const t = sp ? eyesTarget({ pos: sp, look: v.selfLookNow(), aim: v.selfAim(), trail: v.selfTrail() }) : null;
          if (t) {
            const want = [t.p[0] - sp[0], t.p[1] - sp[1], t.p[2] - sp[2]];
            this._eyeDir = this._eyeDir ? turnToward(this._eyeDir, want, rm ? 1 : 1 - Math.exp(-dt * 1.6)) : turnToward(want, want, 1);
            const o = eyesOrbit(sp, this._eyeDir, 6);
            v.orbit.target = o.target; v.orbit.r = o.r; v.orbit.theta = o.theta; v.orbit.phi = o.phi;
          }
          requestAnimationFrame(step);
          return;
        }
        if (this._mode === "self" && !this._mindFollow && !this._selFollow) {   // it moves every cycle: keep up
          // ◎ SELF'S FRAMING (#237, the operator: "much closer ... scale with what
          // self is doing", then "too far and not really following/looking"):
          // the distance fits the amoeba, what it looks at NOW, its aim and its
          // last couple of steps -- close; the camera sits behind self looking
          // along self -> its look (the chase from behind when it looks at nothing)
          const v = this._v3, sp = v.selfPos();
          if (sp) {
            const look = v.selfLookNow(), tr = v.selfTrail();
            const fr = selfFrame({ pos: sp, look, aim: v.selfAim(), steps: tr });
            this._goal = { p: sp, r: frameDistance(fr.radius), frame: true, ang: selfCamAngles({ pos: sp, look, trail: tr }) };
          }
        }
        const v = this._v3, g = this._goal, rate = followRates(this._mode);
        const idle = followsNow(this._mode, performance.now() - this._lastTouch);
        if (g && g.frame && idle && !v._flyAnim) {          // the frame: damped, a step under reduced motion
          v.orbit.r = dampTo(v.orbit.r, g.r, dt, rate.zoom, rm);
        }
        if (g && !v._flyAnim && (idle || g.sel)) {
          const k = 1 - Math.exp(-dt * (g.sel ? 2.2 : rate.target));
          for (let i = 0; i < 3; i++) v.orbit.target[i] += (g.p[i] - v.orbit.target[i]) * k;
          if (idle) {
            if (!g.frame) v.orbit.r += (g.r - v.orbit.r) * k * 0.6;
            const a = this._tiltAng ? null
              : ((g.frame && g.ang)                          // ◎ self: behind self, toward its look
                 || throughCenter(v.orbit.target));
            if (a) {
              const ka = 1 - Math.exp(-dt * rate.turn);
              v.orbit.theta = angleStep(v.orbit.theta, a.theta, ka);
              v.orbit.phi += (a.phi - v.orbit.phi) * ka;
            }
          }
        }
        if (this._tiltAng) {                               // the device's tilt turns the view
          const kt = 1 - Math.exp(-dt * 6);
          v.orbit.theta = angleStep(v.orbit.theta, this._tiltAng.theta, kt);
          v.orbit.phi += (this._tiltAng.phi - v.orbit.phi) * kt;
        }
        requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    // ---------------------------------------------------------- voice
    _wireVoice(root) {
      const $ = (q) => root.querySelector(q);
      this._ptt = $(".ptt"); this._lockBtn = $(".lock"); this._ttsBtn = $(".tts");
      this._ttsOn = lsGet(LS_TTS, "0") === "1";
      this._paintTts();
      const down = (e) => { e.preventDefault(); this._unlockAudio(); this._ptt.setPointerCapture && this._ptt.setPointerCapture(e.pointerId); this._recStart("ptt"); };
      const up = () => { if (this._rec && this._rec.mode === "ptt") this._recStop(true); };
      this._ptt.addEventListener("pointerdown", down);
      this._ptt.addEventListener("pointerup", up);
      this._ptt.addEventListener("pointercancel", () => { if (this._rec && this._rec.mode === "ptt") this._recStop(false); });
      this._ptt.addEventListener("contextmenu", (e) => e.preventDefault());
      this._lockBtn.addEventListener("click", () => {
        this._unlockAudio();
        if (this._rec && this._rec.mode === "lock") this._recStop(true); else this._recStart("lock");
      });
      // long-press / right-click: choose the voice (this device's own, on-device ones)
      let lp = 0;
      this._ttsBtn.addEventListener("pointerdown", () => { lp = setTimeout(() => { lp = -1; this._voiceMenu(); }, 550); });
      this._ttsBtn.addEventListener("pointerup", () => clearTimeout(lp));
      this._ttsBtn.addEventListener("contextmenu", (e) => { e.preventDefault(); this._voiceMenu(); });
      this._ttsBtn.addEventListener("click", (e) => {
        if (lp === -1) { lp = 0; e.preventDefault(); return; }   // the long-press opened the menu
        this._unlockAudio();
        this._ttsOn = !this._ttsOn; lsSet(LS_TTS, this._ttsOn ? "1" : "0"); this._paintTts();
        if (!this._ttsOn) this._hushTts();
      });
      // a laptop's PTT: hold Space anywhere but a text box
      const typing = (e) => e.composedPath().some((n) => n && (n.tagName === "TEXTAREA" || n.tagName === "INPUT" || n.isContentEditable));
      this._onKeyDown = (e) => {
        if (e.code === "Space" && !e.repeat && !typing(e) && this.isConnected) { e.preventDefault(); this._recStart("ptt"); }
      };
      this._onKeyUp = (e) => { if (e.code === "Space" && this._rec && this._rec.mode === "ptt" && !typing(e)) { e.preventDefault(); this._recStop(true); } };
      this._listenKeys(true);
      // DICTATION by the brain phone's whisper by default (the in-page Vosk model
      // missed accented English, 2026-10-10); the WAKE WORD always runs on the
      // device -- nothing is sent until the name was heard. ⚙: all on-device.
      // a host with no speech server (standalone, a Home Assistant without one set:
      // window.MEMBRANE_STT = "browser") listens in the browser; the boat's whisper otherwise
      const sttDefault = (typeof window !== "undefined" && window.MEMBRANE_STT === "browser") ? "device" : "lan";
      this._engine = lsGet(LS_STT, sttDefault) === "device" ? "device" : "lan";
      this._engBtn = root.querySelector(".eng");
      const paintEng = () => { this._engBtn.textContent = this._engine === "device" ? "⚙ᴰ" : "⚙";
        this._engBtn.title = this._engine === "device" ? "Speech recognised ON THIS DEVICE (whisper in the browser: private; ~80 MB once, slower on old phones). Tap: by the brain phone's whisper"
                                                       : "Speech recognised by the brain phone's whisper (over your network; the wake word stays on this device). Tap: on this device (whisper in the browser)"; };
      paintEng();
      this._engBtn.addEventListener("click", () => {
        this._engine = this._engine === "device" ? "lan" : "device"; lsSet(LS_STT, this._engine); paintEng();
        this._vsay(this._engine === "device" ? "speech recognised on this device" : "speech recognised on the brain phone (whisper)");
        if (this._hot) { this._hotStop(); this._hotStart(); }
      });
      if (window.speechSynthesis) {
        const load = () => { this._voice = pickVoice(window.speechSynthesis.getVoices(), "en", lsGet(LS_VOICE, "")); };
        load(); window.speechSynthesis.addEventListener && window.speechSynthesis.addEventListener("voiceschanged", load);
      }
      // WHAT STOPS HIM (the operator: "only a tap that sends a message or starts
      // the microphone, or 'Hey <name>'"): sending (below), 🎙/🔒 (_recStart),
      // his name (_localWake) -- nothing else
      this._chat.addEventListener("ai-sent", () => { if (this._speaking || (this._speechQ || []).length) { this._hushTts(); this._orb(null); this._orbText(""); } });
      this._earBtn = root.querySelector(".ear");
      this._earMode = lsGet(LS_EARMODE, "wake") === "addressed" ? "addressed" : "wake";
      let lpe = 0;
      this._earBtn.addEventListener("pointerdown", () => { lpe = setTimeout(() => { lpe = -1; this._earMenu(); }, 550); });
      this._earBtn.addEventListener("pointerup", () => clearTimeout(lpe));
      this._earBtn.addEventListener("contextmenu", (e) => { e.preventDefault(); this._earMenu(); });
      this._paintEar();
      this._earBtn.addEventListener("click", (e) => {
        if (lpe === -1) { lpe = 0; e.preventDefault(); return; }
        this._unlockAudio();
        if (this._hot) { this._hotStop(); lsSet(LS_HOTWORD, "0"); } else { lsSet(LS_HOTWORD, "1"); this._hotStart(); }
      });
      this._onVis = () => { if (document.hidden) this._hotPause(true); else this._hotPause(false); };
      document.addEventListener("visibilitychange", this._onVis);
      if (lsGet(LS_HOTWORD, "0") === "1") setTimeout(() => this._hotStart(), 800);
      // the chat went idle: the spoken turn is over, the orb rests once the queue is done
      // the chat read the out-of-tokens state (a pause, a hold, a resume): the ⚠ follows
      this._chat.addEventListener("ai-quota", (e) => {
        if (this._quotaBadge && e.detail) { this._quotaBadge.state = e.detail; this._quotaBadge.paint(); }
      });
      this._chat.addEventListener("ai-idle", () => {
        setTimeout(() => {
          if (this._busyChat()) return;
          this._voiceTurn = false;
          if (!this._speaking && !(this._speechQ || []).length) this._orb(null);
        }, 1500);
      });
      // SPEECH AS IT COMES: the seat's words the moment each part closes (🔊 on, or
      // a turn you started by voice), membrane's own lines in its own voice (🔊 on)
      // the reply finished (a busy chat may not go idle for a long while:
      // don't wait for it) -- the voice turn is over; the wave rests once speech is done
      this._chat.addEventListener("ai-reply", () => {
        this._voiceTurn = false;
        setTimeout(() => { if (!this._speaking && !(this._speechQ || []).length) this._speechDone(); }, 400);
      });
      // VOICE OUT (2026-10-11, the operator: "it should not voice the chat since we
      // have voice:out"): the seat speaks through his voice:out line (ai-say);
      // the chat's text is never read aloud, nor membrane's lines.
      // THE SEAT OPENS THE MIC (2026-10-12, the operator: "let the seat turn listening on and
      // off instead of that follow-up mode"): voice:in's `listen` -> listen that long once
      // he has finished speaking (never his own voice); 0 closes. Needs the ear (👂) on.
      this._chat.addEventListener("ai-listen", (e) => {
        const secs = Math.max(0, Math.min(30, Number((e.detail || {}).seconds) || 0));
        const h = this._hot;
        if (!h) { if (secs) this._vsay(SEAT + " is listening for you — turn the ear (👂) on to answer by voice", 4000); return; }
        if (!secs) { if (h.state === "dictate") { this._wakeRecognizer(h); this._orb(null); } return; }
        const t0 = Date.now();
        // his question may still be on its way to the speakers: a moment's grace, then
        // wait while anything is queued or playing (the mic never talks over him)
        const go = () => {
          if (this._hot !== h) return;
          if (Date.now() - t0 < 1200 || ((this._speaking || (this._speechQ || []).length) && Date.now() - t0 < 30000)) { setTimeout(go, 200); return; }
          if (h.state !== "wake") return;                   // already hearing something
          h.followWait = secs * 1000;
          this._vsay("listening… (" + SEAT + " asked)", secs * 1000);
          this._orb("listening");
          this._dictate(h, true);
        };
        setTimeout(go, 300);
      });
      this._chat.addEventListener("ai-say", (e) => {
        const d = e.detail || {};
        speechLog("seat", this._ttsOn ? "say (voice:out)" : "skip: 🔊 off", d.text);
        if (this._ttsOn && d.text) this._enqueueSpeech("seat", d.text);
      });
      // ONLY voice:out is spoken (2026-10-11, the operator: "why does it voice
      // membrane's babbles instead of whatever is sent thru voice:out?"): the
      // chat and membrane's lines are text; they land in the voice log only
      this._chat.addEventListener("ai-speak", (e) => {
        const d = e.detail || {};
        speechLog(d.kind, "not read aloud: only voice:out is spoken", d.text);
      });
      // CHUNKED STT (Phase 2, "more is coming"): the person's speech streams in
      // -- interim transcripts show as they arrive, before the turn starts
      this._chat.addEventListener("ai-stream", (e) => {
        const d = e.detail || {};
        const words = String(d.text || "").trim();
        if (!words) return;
        this._orbText(words + (d.final ? "" : " …"));
      });
    }

    _listenKeys(on) {
      if (!this._onKeyDown || on === this._keysOn) return;
      this._keysOn = on;
      const f = on ? "addEventListener" : "removeEventListener";
      window[f]("keydown", this._onKeyDown); window[f]("keyup", this._onKeyUp);
    }

    /** A status line that is only shown, never spoken. */
    _vhint(text, ms = 8000) {
      const el = this.shadowRoot.querySelector(".vsay"); if (!el) return;
      clearTimeout(this._vsayT);
      el.textContent = text || ""; el.classList.toggle("show", !!text);
      if (text && ms) this._vsayT = setTimeout(() => el.classList.remove("show"), ms);
    }

    /** The voice status, by the buttons (the top bar's status is hidden on phones). */
    _vsay(text, ms = 6000) {
      if (text) speechLog("page", "status", text);
      const el = this.shadowRoot.querySelector(".vsay"); if (!el) return;
      clearTimeout(this._vsayT);
      el.textContent = text || ""; el.classList.toggle("show", !!text);
      if (text && ms) this._vsayT = setTimeout(() => el.classList.remove("show"), ms);
      if (text) this._say(text);
    }

    /** Phones only play sound that a tap started: the first tap on a voice
     * button plays a blink of silence on THE player, which every reply reuses. */
    _unlockAudio() {
      if (this._player) return;
      const a = this._player = new Audio();
      try {
        const blob = new Blob([wavBytes(new Int16Array(800), 16000)], { type: "audio/wav" });
        a.src = URL.createObjectURL(blob);
        a.play().catch(() => {});
      } catch (e) { /* the fallback voice remains */ }
      try { if (window.speechSynthesis) window.speechSynthesis.speak(new SpeechSynthesisUtterance("")); } catch (e) { /* none */ }
    }

    _busyChat() {
      const st = this._chat && this._chat._chat && this._chat._chat.status;
      return !!(st && st.type && st.type !== "idle");
    }

    /** The orb: null | "listening" | "thinking" | "speaking". */
    _orb(mode) {
      const o = this.shadowRoot.querySelector(".orb"), t = this.shadowRoot.querySelector(".orbtext");
      if (!o) return;
      if (!mode && (this._ttsOn || this._hot)) mode = "idle";      // voice on, nobody speaking: the cube
      clearTimeout(this._orbT); clearTimeout(this._orbMax);
      if (mode === "thinking") this._orbMax = setTimeout(() => this._orb(null), 5 * 60000);   // never ripple forever
      this._waveMode = mode || null;
      o.classList.toggle("on", !!mode);
      if (mode) this._waveRun();
      if (!mode) this._orbT = setTimeout(() => t.classList.remove("on"), 2500);
      if (mode === "listening" || mode === "idle") { if (mode === "listening" || !this._speaking) { t.textContent = ""; t.classList.remove("on"); } }
    }

    /** The wave's frame loop: runs while the orb shows, stops after it fades. */
    _waveRun() {
      if (this._waveRaf) return;
      const cv = this.shadowRoot.querySelector(".orb canvas"); if (!cv) return;
      const g = cv.getContext("2d");
      const N = 15, amps = new Float32Array(N), seeds = Array.from({ length: N }, (_, i) => i * 1.7 + Math.random() * 3);
      let last = performance.now(), off = 0, burst = 0, next = 0;
      const step = (t) => {
        const dt = Math.min(0.1, (t - last) / 1000); last = t;
        const W = cv.width, H = cv.height, mid = H / 2, mode = this._waveMode;
        if (!mode) off += dt; else off = 0;
        if (off > 0.6) { g.clearRect(0, 0, W, H); this._waveRaf = 0; return; }
        // unfold: 0 = the cube (voice on, quiet), 1 = the line (listening / thinking / speaking)
        const want = mode === "idle" ? 0 : 1;
        this._unfold = (this._unfold == null ? want : this._unfold) + (want - (this._unfold == null ? want : this._unfold)) * Math.min(1, dt * 5);
        const u = this._unfold;
        // the energy that drives the nodes
        let energy;
        if (mode === "listening") energy = Math.min(1, Math.sqrt(this._lvl || 0) * 3.2);
        else if (mode === "speaking") {                    // syllable-like bursts (speech gives no level to read)
          if (t > next) { burst = 0.45 + Math.random() * 0.55; next = t + 90 + Math.random() * 170; }
          burst *= Math.exp(-dt * 6); energy = 0.4 + burst * 1.1;
        } else if (mode === "idle") energy = 0;
        else energy = 0.18;                                // thinking: a slow ripple
        for (let i = 0; i < N; i++) {
          const edge = Math.sin(Math.PI * (i + 0.5) / N);  // calm at the ends, alive in the middle
          const wob = mode === "thinking" ? Math.sin(t / 520 + seeds[i]) : (Math.sin(t / 95 + seeds[i] * 2.3) + Math.sin(t / 61 + seeds[i]) * 0.5) / 1.5;
          const target = wob * energy * edge;
          amps[i] += (target - amps[i]) * Math.min(1, dt * (mode === "thinking" ? 3 : 14));
        }
        g.clearRect(0, 0, W, H);
        const line = Array.from(amps, (a, i) => [W * (i + 0.5) / N, mid + Math.max(-0.92, Math.min(0.92, a)) * mid]);
        // the cube IS the field's box as the field camera sees it (its view matrix), else turns by itself
        const cam = this._v3 && this._v3.camera;
        const view = cam && cam.matrixWorldInverse && cam.matrixWorldInverse.elements;
        const cube = cubePoints(N, t / 4000, 0.45, view && view.length === 16 ? view : null), sz = H * 0.27, at = ([x, y]) => [W / 2 + x * sz, mid - y * sz];
        const pts = line.map((p, i) => { const c = at(cube.nodes[i]); return [c[0] + (p[0] - c[0]) * u, c[1] + (p[1] - c[1]) * u]; });
        // THE LISTENING RING (2026-10-12, the operator: "a squiggly red line that makes a
        // circle whenever mic is actively listening"): the line closes into a circle,
        // wiggling with the mic's level, and turns red; it opens again when the mic stops
        this._ringT = (this._ringT || 0) + ((mode === "listening" ? 1 : 0) - (this._ringT || 0)) * Math.min(1, dt * 6);
        const ring = this._ringT;
        if (ring > 0.002) {
          const R = Math.min(W, H) * 0.3;
          for (let i = 0; i < pts.length; i++) {
            const a = -Math.PI / 2 + (2 * Math.PI * i) / (pts.length - 1), rr = R * (1 + amps[i] * 0.45);
            const cx = W / 2 + Math.cos(a) * rr, cy = mid + Math.sin(a) * rr;
            pts[i] = [pts[i][0] + (cx - pts[i][0]) * ring, pts[i][1] + (cy - pts[i][1]) * ring];
          }
        }
        const mix = (a, b) => { const p = (h) => [1, 3, 5].map((k) => parseInt(h.slice(k, k + 2), 16));
          const x = p(a), y = p(b); return "rgb(" + x.map((v, k) => Math.round(v + (y[k] - v) * ring)).join(",") + ")"; };
        const grad = g.createLinearGradient(0, 0, W, 0);
        grad.addColorStop(0, mix("#6f8cff", "#ff3b3b")); grad.addColorStop(0.5, mix("#b07cff", "#ff2e63")); grad.addColorStop(1, mix("#4fd1ff", "#ff6b3d"));
        // the cube's own edges, fading as it unfolds
        if (u < 0.98 && ring < 0.98) {
          g.globalAlpha = (1 - u) * 0.55 * (1 - ring); g.strokeStyle = grad; g.lineWidth = 2; g.beginPath();
          for (const [a, b] of cube.edges) { const p = at(a), q = at(b); g.moveTo(p[0], p[1]); g.lineTo(q[0], q[1]); }
          g.stroke();
        }
        // the edges: straight segments (angular, a strip of graph) + a faint mirror
        for (const [mirror, alpha, width] of [[true, 0.25 * u * (1 - ring), 2], [false, 1, 3]]) {
          if (alpha <= 0.01) continue;
          g.globalAlpha = alpha; g.strokeStyle = grad; g.lineWidth = width; g.lineJoin = "miter";
          g.beginPath();
          pts.forEach(([x, y], i) => { const yy = mirror ? 2 * mid - y : y; if (i) g.lineTo(x, yy); else g.moveTo(x, yy); });
          g.stroke();
        }
        // the nodes: dots sized by how hard each one moves
        g.globalAlpha = 1;
        pts.forEach(([x, y], i) => {
          const r = 2.5 + Math.abs(amps[i]) * 7 * u;
          g.fillStyle = grad; g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill();
        });
        this._waveRaf = requestAnimationFrame(step);
      };
      this._waveRaf = requestAnimationFrame(step);
    }
    _orbText(text) {
      const t = this.shadowRoot.querySelector(".orbtext"); if (!t) return;
      t.textContent = text || ""; t.classList.toggle("on", !!text);
    }
    _orbLevel(rms) { this._lvl = rms; }

    /** "Voice" UI: the speech queue's end -- the wave rests unless a reply you asked for by voice is still coming. */
    _speechDone() {
      if (this._voiceTurn && this._busyChat()) this._orb("thinking");
      else { this._orb(null); this._orbText(""); }
    }

    _voiceMenu() {
      const vs = (window.speechSynthesis ? window.speechSynthesis.getVoices() : [])
        .filter((v) => v.localService && String(v.lang || "").toLowerCase().startsWith("en"));
      let m = this.shadowRoot.querySelector(".vmenu");
      if (!m) {
        m = document.createElement("div"); m.className = "vmenu glass hamenu open"; m.setAttribute("role", "menu");
        this.shadowRoot.querySelector(".voice").appendChild(m);
        m.addEventListener("click", (e) => {
          const a = e.target.closest("a[data-v]"); if (!a) return;
          e.preventDefault();
          lsSet(LS_VOICE, a.dataset.v);
          this._voice = pickVoice(window.speechSynthesis.getVoices(), "en", a.dataset.v);
          m.remove();
          this._hushTts(); this._say1("Hi, I'm " + SEAT + ". This is how I'll sound.");
        });
        setTimeout(() => {
          const close = (e) => { if (!e.composedPath().includes(m)) { m.remove(); window.removeEventListener("pointerdown", close, true); } };
          window.addEventListener("pointerdown", close, true);
        }, 0);
      }
      const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      const cur = this._voice ? this._voice.name : "";
      m.innerHTML = vs.length ? vs.map((v) => '<a role="menuitem" href="#" data-v="' + esc(v.name) + '">' + (v.name === cur ? "● " : "") +
        esc(v.name) + ' <span style="opacity:.55">' + esc(v.lang) + "</span></a>").join("") +
        '<hr><div style="padding:4px 12px;font-size:12px;opacity:.7;max-width:260px">Better voices: download Enhanced / Premium ones in this device\'s Spoken Content settings.</div>'
        : '<div style="padding:8px 12px">this device has no voices of its own</div>';
    }

    _paintEar() {
      if (!this._earBtn) return;
      this._earBtn.textContent = this._earMode === "addressed" ? "👂ᴬ" : "👂";
      this._earBtn.title = this._earMode === "addressed"
        ? "Listening for " + SEAT + " being ADDRESSED anywhere in a sentence (long-press: the mode)"
        : "Listening for “Hey " + SEAT + "” (long-press: the mode)";
    }

    _earMenu() {
      let m = this.shadowRoot.querySelector(".emenu");
      if (m) { m.remove(); return; }
      m = document.createElement("div"); m.className = "vmenu emenu glass hamenu open"; m.setAttribute("role", "menu");
      const row = (mode, label, sub) => '<a role="menuitem" href="#" data-m="' + mode + '">' + (this._earMode === mode ? "● " : "") +
        label + '<span style="opacity:.55;font-size:12px;margin-left:6px">' + sub + "</span></a>";
      m.innerHTML = row("wake", "“Hey " + SEAT + "”", "the name first") +
        row("addressed", "Whenever he's addressed", "his name anywhere in a sentence") +
        '<hr><div style="padding:4px 12px;font-size:12px;opacity:.7;max-width:280px">Every sentence is heard on this device; only one that names him is sent to be transcribed.</div>';
      this.shadowRoot.querySelector(".voice").appendChild(m);
      m.addEventListener("click", (e) => {
        const a = e.target.closest("a[data-m]"); if (!a) return;
        e.preventDefault();
        this._earMode = a.dataset.m; lsSet(LS_EARMODE, this._earMode); this._paintEar(); m.remove();
        if (this._hot) { this._hotStop(); this._hotStart(); }
        this._vsay(this._earMode === "addressed" ? "listening for " + SEAT + " being addressed" : "listening for “Hey " + SEAT + "”");
      });
      setTimeout(() => {
        const close = (e) => { if (!e.composedPath().includes(m)) { m.remove(); window.removeEventListener("pointerdown", close, true); } };
        window.addEventListener("pointerdown", close, true);
      }, 0);
    }

    /** The mic and 🔊 as the person's voice:in / voice:out (the seat stamps who). */
    _reportVoice() {
      const chat = this._chat; if (!chat || !chat._api) return;
      const inMode = this._hot ? (this._earMode === "addressed" ? "addressed" : "hotword") : (this._rec ? this._rec.mode : "off");
      const body = { in_on: inMode !== "off", in_mode: inMode, out_on: !!this._ttsOn,
                     device: (navigator.maxTouchPoints > 0 ? "phone/tablet" : "computer") + " · " + (navigator.platform || "") };
      clearTimeout(this._vrT);
      this._vrT = setTimeout(() => chat._api(HSERVE + "/membrane/voice/state",
        { method: "POST", body: JSON.stringify(body) }).catch(() => {}), 400);
    }

    _paintTts() {
      this._reportVoice();
      if (!this._waveMode || this._waveMode === "idle") this._orb(null);   // the cube, or nothing
      this._ttsBtn.classList.toggle("on", this._ttsOn);
      this._ttsBtn.textContent = this._ttsOn ? "🔊" : "🔈";
      this._ttsBtn.setAttribute("aria-pressed", String(this._ttsOn));
    }

    async _recStart(mode) {
      if (this._rec || this._recBusy) return;
      this._hotPause(true);
      this._hushTts();                                     // talking over it stops it
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        this._vsay("no microphone here (it needs https and a browser that allows it)"); return;
      }
      const rec = this._rec = { mode, chunks: [], t0: performance.now() };
      this._reportVoice();
      (mode === "ptt" ? this._ptt : this._lockBtn).classList.add("rec");
      try {
        rec.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        if (this._rec !== rec) { rec.stream.getTracks().forEach((t) => t.stop()); return; }   // released while asking
        const Ctx = window.AudioContext || window.webkitAudioContext;
        rec.ctx = new Ctx();
        rec.src = rec.ctx.createMediaStreamSource(rec.stream);
        rec.local = this._engine === "device";
        // while the on-device model loads (the first time only), audio is kept and fed after
        this._orb("listening");
        rec.spoke = false; rec.silent = 0;
        const _onFrame = (ev) => {
          const f = ev.inputBuffer.getChannelData(0);
          let ss = 0; for (let i = 0; i < f.length; i += 2) ss += f[i] * f[i];
          const rms = Math.sqrt(ss / (f.length / 2)); this._orbLevel(rms);
          if (rec.mode === "lock") {                        // Siri-like: a pause ends it
            if (rms > 0.02) { rec.spoke = true; rec.silent = 0; } else rec.silent += (f.length / rec.ctx.sampleRate) * 1000;
            if (rec.spoke && rec.silent > LOCK_END_MS && this._rec === rec) setTimeout(() => this._recStop(true), 0);
          }
          if (rec.vr) rec.vr.kr.acceptWaveform(ev.inputBuffer);
          else rec.chunks.push(new Float32Array(f));
        };
        rec.proc = await micNode(rec.ctx, _onFrame);
        if (this._rec !== rec) { this._recCleanup(rec); return; }
        rec.src.connect(rec.proc); if (rec.proc._sp) rec.proc.connect(rec.ctx.destination);
        if (rec.local) this._asrModel();                     // loading while you talk (the first time only)
        rec.timer = setTimeout(() => { if (this._rec === rec) this._recStop(true); }, REC_MAX_S * 1000);
        this._vsay(mode === "lock" ? "listening… tap 🔒 again to send" : "listening… release to send", 0);
      } catch (e) {
        this._recCleanup(rec);
        this._vsay("the microphone is blocked: " + ((e && e.name) || e));
      }
    }

    _recCleanup(rec) {
      if (!rec) return;
      clearTimeout(rec.timer);
      try { rec.proc && rec.proc.disconnect(); rec.src && rec.src.disconnect(); } catch (e) { /* gone */ }
      if (rec.stream) rec.stream.getTracks().forEach((t) => t.stop());
      if (rec.ctx) rec.ctx.close().catch(() => {});
      this._ptt.classList.remove("rec"); this._lockBtn.classList.remove("rec");
      if (this._rec === rec) { this._rec = null; this._reportVoice(); }
      setTimeout(() => { if (!this._rec) this._hotPause(false); }, 300);
    }

    async _recStop(send) {
      const rec = this._rec; if (!rec) return;
      const rate = rec.ctx ? rec.ctx.sampleRate : 48000;
      rec.stopping = true;
      this._recCleanup(rec);
      if (rec.local) return this._recStopLocal(rec, send, rate);
      const n = rec.chunks.reduce((a, c) => a + c.length, 0);
      if (!send || n / rate < REC_MIN_S) { this._vsay(send ? "too short to hear" : ""); return; }
      const all = new Float32Array(n); let o = 0;
      for (const c of rec.chunks) { all.set(c, o); o += c.length; }
      let ss = 0; for (let i = 0; i < n; i += 4) ss += all[i] * all[i];
      if (Math.sqrt(ss / Math.max(1, n / 4)) < 0.004) { this._vsay("didn't hear anything (is the mic muted?)"); return; }   // silence: whisper would invent "You"
      const wav = wavBytes(downsample(all, rate, STT_RATE), STT_RATE);
      this._recBusy = true; this._ptt.classList.add("busy"); this._lockBtn.classList.add("busy");
      this._vsay("transcribing…", 0); this._orb("thinking");
      try {
        const r = await this._hass.fetchWithAuth(STT_PATH, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || ("speech service " + r.status));
        const text = (d.text || "").trim();
        if (!text) { this._vsay("didn't catch that"); this._orb(null); return; }
        this._sendSaid(text);
      } catch (e) {
        this._vsay("couldn't transcribe: " + ((e && e.message) || e));
      } finally {
        this._recBusy = false; this._ptt.classList.remove("busy"); this._lockBtn.classList.remove("busy");
      }
    }

    async _recStopLocal(rec, send, rate) {
      if (!send) { this._orb(null); return; }
      const n = rec.chunks.reduce((a, c) => a + c.length, 0);
      if (n / rate < REC_MIN_S) { this._vsay("too short to hear"); this._orb(null); return; }
      this._recBusy = true; this._ptt.classList.add("busy"); this._lockBtn.classList.add("busy");
      try {
        this._orb("thinking"); this._vsay("transcribing on this device…", 0);
        const text = await this._whisperLocal(rec.chunks, rate);
        if (!text) { this._vsay("didn't catch that"); this._orb(null); return; }
        this._sendSaid(text);
      } finally {
        this._recBusy = false; this._ptt.classList.remove("busy"); this._lockBtn.classList.remove("busy");
      }
    }

    _sendSaid(text, cut) {
      this._orbText(text); this._orb("thinking");
      clearTimeout(this._orbTextT);
      this._orbTextT = setTimeout(() => { if (!this._speaking) this._orbText(""); }, 4000);   // the phrase fades
      const queued = this._busyChat();
      this._voiceTurn = true;
      if (this._chat && this._chat.sendVoice) this._chat.sendVoice(text);   // voice:in, not a chat bubble
      else this._sendToChat(text);
      this._vsay("“" + text + "” — " + (queued ? "cutting in on " + SEAT + "'s turn" : "sent; " + SEAT + " is thinking…"), 12000);
    }

    /** The on-device model, with its first-time download in the bubble. */
    _voiceModel() {
      return localVoice((got, total) => this._vsay("loading the on-device voice (once): " +
        Math.round(got / 1e6) + " / " + Math.round(total / 1e6) + " MB", 0));
    }

    _sendToChat(text) {
      const sr = this._chat && this._chat.shadowRoot; if (!sr) return;
      const ta = sr.querySelector("textarea"), send = sr.querySelector(".send");
      if (!ta || !send) return;
      ta.value = text;
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      if (this._chatBox.classList.contains("hidden")) this.shadowRoot.querySelector(".chatbtn").click();
      send.click();
    }

    // ---------------------------------------------------------- the hotword
    async _hotStart() {
      if (this._hot) return;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { this._vsay("no microphone here"); return; }
      const h = this._hot = { cur: null, below: 0, above: 0, floor: 0.004, sends: [], follow: 0, paused: false, muteUntil: 0 };
      this._reportVoice();
      if (!this._waveMode) this._orb(null);
      this._prefetchAcks();
      this._earBtn.classList.add("on"); this._earBtn.setAttribute("aria-pressed", "true");
      try {
        h.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
        if (this._hot !== h) { h.stream.getTracks().forEach((t) => t.stop()); return; }
        const Ctx = window.AudioContext || window.webkitAudioContext;
        h.ctx = new Ctx(); h.rate = h.ctx.sampleRate;
        h.src = h.ctx.createMediaStreamSource(h.stream);
        h.local = true;                                  // the wake word: always on this device
        const _onHot = (ev) => (h.local ? this._hotLocalFrame(h, ev.inputBuffer) : this._hotFrame(h, ev.inputBuffer.getChannelData(0)));
        if (h.local) {
          h.model = await this._voiceModel();
          if (this._hot !== h) return;
          this._wakeRecognizer(h);
        }
        h.proc = await micNode(h.ctx, _onHot);
        if (this._hot !== h) { try { h.stream.getTracks().forEach((t) => t.stop()); } catch (e) { /* gone */ } return; }
        h.src.connect(h.proc); if (h.proc._sp) h.proc.connect(h.ctx.destination);
        // started without a tap (remembered from last time), the browser
        // keeps audio SUSPENDED: the first tap anywhere wakes it
        if (h.ctx.state === "suspended") {
          await h.ctx.resume().catch(() => {});
          if (h.ctx.state === "suspended") {
            this._vsay("tap anywhere to start listening for “Hey " + SEAT + "”", 0);
            const wake = () => { h.ctx.resume().then(() => this._vsay("listening for “Hey " + SEAT + "”")).catch(() => {}); };
            window.addEventListener("pointerdown", wake, { once: true, capture: true });
            return;
          }
        }
        this._vsay("listening for “Hey " + SEAT + "”");
      } catch (e) {
        this._hotStop(); lsSet(LS_HOTWORD, "0");
        this._vsay("the microphone is blocked: " + ((e && e.name) || e));
      }
    }

    _hotStop() {
      const h = this._hot; this._hot = null;
      this._reportVoice();
      if (!this._waveMode || this._waveMode === "idle") this._orb(null);
      if (h && h.any) { h.any.drop(); h.any = null; }
      if (this._earBtn) { this._earBtn.classList.remove("on", "hearing"); this._earBtn.setAttribute("aria-pressed", "false"); }
      if (!h) return;
      try { h.proc && h.proc.disconnect(); h.src && h.src.disconnect(); } catch (e) { /* gone */ }
      if (h.stream) h.stream.getTracks().forEach((t) => t.stop());
      if (h.ctx) h.ctx.close().catch(() => {});
    }

    // ---- the wake word ON THE DEVICE: every frame to a grammar-bound
    // recognizer; its partial result says the name within a few hundred ms
    _wakeRecognizer(h) {
      if (h.wake) h.wake.drop();
      h.wake = recognizer(h.model, h.rate, WAKE_GRAMMAR, (t) => this._localWake(h, t, false));
      h.wake.onResult = (t) => this._localWake(h, t, true);
      if (this._earMode === "addressed") {
        if (h.any) h.any.drop();
        h.abuf = []; h.aHit = false;
        h.any = recognizer(h.model, h.rate, null, (t) => { if (NAME_ANY_RE.test(t)) h.aHit = true; });
        h.any.onResult = (t) => this._addressed(h, t);
      }
      h.followWait = 0;                                   // a seat-opened window is one-shot
      h.state = "wake";
    }

    /** A sentence heard on the device ended: did it address him? */
    async _addressed(h, text) {
      const chunks = h.abuf || [];
      h.abuf = [];
      const hit = h.aHit || NAME_ANY_RE.test(String(text || ""));
      h.aHit = false;
      if (!hit || h.state !== "wake" || this._hot !== h || this._speaking) return;
      h.state = "busy";
      this._hotHeard(h);                                   // the chime: he heard his name
      this._orb("thinking");
      const said = this._engine === "device" ? await this._whisperLocal(chunks, h.rate) : await this._whisper(chunks, h.rate);
      if (this._hot !== h) return;
      this._wakeRecognizer(h);
      if (said && NAME_ANY_RE.test(said.replace(/[,.!?]/g, " "))) this._sendSaid(said);   // whisper agrees he was named
      else if (said) { this._vsay("heard “" + said + "” — not for " + SEAT, 4000); this._orb(null); }
      else this._orb(null);
    }

    _localWake(h, text, final) {
      if (h.state !== "wake" || !WAKE_RE.test(String(text || ""))) return;
      if (this._speaking || (this._speechQ || []).length) {
        // while he speaks the mic hears HIM (the speakers): only a whole short
        // utterance that IS the wake phrase interrupts -- never a partial
        // match inside his own sentences (it cut him off, 2026-10-10)
        const words = String(text || "").trim().split(/\s+/).filter((w) => w && w !== "[unk]");
        if (!final || words.length > 3) return;
        this._vsay("👂 heard my name — stopped", 3000);
      }
      this._hushTts();                                     // called by name: he stops talking
      this._hotHeard(h);                                   // the chime, now
      this._dictate(h, false);
    }

    /** Transcribe what follows (on the device): the same breath after the
     * name, or -- after a bare "Hey <name>" -- the next sentence. */
    _dictate(h, follow) {
      h.state = "dictate"; h.follow = follow; h.spoke = !follow; h.silent = 0; h.dStart = performance.now();
      // the words after the name go to whisper (in the browser or on the brain
      // phone), with 0.6 s before the wake kept so the first word isn't lost
      h.free = null;
      h.dchunks = follow ? [] : (h.ring || []).slice(-Math.ceil(0.6 * h.rate / 2048));
      if (this._engine === "device") this._asrModel();
      this._vsay("listening…", 0);
      h.ring = [];
      this._orb("listening");
    }

    /** The in-browser whisper, its first download shown in the bubble. */
    _asrModel() {
      return localASR((got, total, file) => {
        if (total > 2e6) this._vsay("loading speech recognition on this device (once): " + Math.round(got / 1e6) + " / " + Math.round(total / 1e6) + " MB", 0);
      });
    }

    /** Chunks of audio -> text by whisper IN THE BROWSER ('' on failure: then the brain phone's). */
    async _whisperLocal(chunks, rate) {
      const n = chunks.reduce((a, c) => a + c.length, 0), all = new Float32Array(n); let o = 0;
      for (const c of chunks) { all.set(c, o); o += c.length; }
      try {
        const asr = await this._asrModel();
        const r = await asr(toWhisperRate(all, rate));
        return cleanTranscript(r && r.text);
      } catch (e) {
        speechLog("you", "on-device recognition failed", String((e && (e.stack || e.message)) || e));
        this._vsay("on-device recognition failed (" + ((e && e.message) || e) + "): using the brain phone", 4000);
        return this._whisper(chunks, rate);
      }
    }

    /** Chunks of audio -> text by the brain phone's whisper ('' on any failure). */
    async _whisper(chunks, rate) {
      const n = chunks.reduce((a, c) => a + c.length, 0), all = new Float32Array(n); let o = 0;
      for (const c of chunks) { all.set(c, o); o += c.length; }
      try {
        const wav = wavBytes(downsample(all, rate, STT_RATE), STT_RATE);
        const r = await this._hass.fetchWithAuth(STT_PATH, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) speechLog("you", "speech server " + r.status, d.error || "");
        return (r.ok && d.text || "").trim();
      } catch (e) { speechLog("you", "speech server unreachable", String(e)); return ""; }
    }

    _hotLocalFrame(h, buf) {
      const f32 = buf.getChannelData(0);
      // The seat speaking does NOT stop the ear: his name cuts him off (Siri-like barge-in)
      const busy = h.paused || h.muteUntil > performance.now() || this._rec || this._recBusy;
      let ss = 0; for (let i = 0; i < f32.length; i++) ss += f32[i] * f32[i];
      const rms = Math.sqrt(ss / f32.length), thr = Math.max(0.012, h.floor * 3.5);
      this._orbLevel(rms);
      if (h.state === "wake") {
        if (busy) return;
        if (rms < h.floor * 3) h.floor = h.floor * 0.995 + rms * 0.005;
        const copy = new Float32Array(f32);
        h.ring = (h.ring || []).concat([copy]);
        while (h.ring.length > Math.ceil(1.6 * h.rate / f32.length)) h.ring.shift();
        h.wake.kr.acceptWaveform(buf);
        if (h.any) {
          h.any.kr.acceptWaveform(buf);
          h.abuf.push(copy);
          while (h.abuf.length * f32.length > ADDR_MAX_S * h.rate) h.abuf.shift();
        }
        return;
      }
      if (h.state !== "dictate") return;                   // finishing: frames are not needed
      if (rms > (h.dpeak || 0)) h.dpeak = rms;              // the voice log: how loud the request came in
      h.dthr = thr;
      if (h.free) h.free.kr.acceptWaveform(buf); else if (h.dchunks) h.dchunks.push(new Float32Array(f32));
      const ms = (f32.length / h.rate) * 1000;
      if (rms >= thr) { h.spoke = true; h.silent = 0; } else h.silent += ms;
      const age = performance.now() - h.dStart;
      if ((h.spoke && h.silent >= DICTATE_END_MS) || (!h.spoke && age > (h.follow ? (h.followWait || HOT_FOLLOW_MS) : DICTATE_WAIT_MS)) || age > Math.max(15000, (h.followWait || 0) + 2000)) {
        this._dictateDone(h);
      }
    }

    async _dictateDone(h) {
      h.state = "busy";
      const follow = h.follow;
      let text = "";
      if (h.free) text = await h.free.finish();
      else if (h.dchunks && h.dchunks.length && h.spoke) {
        this._orb("thinking");
        text = this._engine === "device" ? await this._whisperLocal(h.dchunks, h.rate) : await this._whisper(h.dchunks, h.rate);
      }
      speechLog("you", "dictation", JSON.stringify({ engine: this._engine, spoke: !!h.spoke, free: !!h.free,
        secs: +((h.dchunks || []).reduce((a, c) => a + c.length, 0) / (h.rate || 1)).toFixed(2),
        peak: +(h.dpeak || 0).toFixed(4), thr: +(h.dthr || 0).toFixed(4), floor: +(h.floor || 0).toFixed(4), text }));
      h.dpeak = 0;
      if (this._hot !== h) return;
      this._wakeRecognizer(h);
      if (follow) {
        if (text) this._sendSaid(hotword(text).hit ? hotword(text).rest || text : text);
        else { this._vsay("didn't catch that"); this._orb(null); }
        return;
      }
      // whisper hears the name's tail in the kept 0.6 s ("gus, turn on..."): off with it
      const w = hotword(text), rest = w.hit ? w.rest : text
        .replace(STRIP_RE, "").trim();
      if (rest.split(/\s+/).filter(Boolean).length >= 2) { this._sendSaid(rest); return; }
      await this._hotAck(h);
      if (FOLLOW_ON && this._hot === h) this._dictate(h, true);
      else if (this._hot === h) { this._wakeRecognizer(h); this._orb(null); }
    }

    _hotPause(on) { if (this._hot) { this._hot.paused = on; if (on) { this._hot.cur = null; this._earBtn.classList.remove("hearing"); } } }

    _hotFrame(h, f32) {
      if (h.paused || h.muteUntil > performance.now() || this._rec || this._recBusy || (this._audio && !this._audio.paused) ||
          (window.speechSynthesis && window.speechSynthesis.speaking)) { if (h.cur) h.cur = null; return; }
      let ss = 0; for (let i = 0; i < f32.length; i++) ss += f32[i] * f32[i];
      const rms = Math.sqrt(ss / f32.length), thr = Math.max(0.012, h.floor * 3.5);
      const frameMs = (f32.length / h.rate) * 1000;
      if (!h.cur) {
        h.floor = rms < h.floor * 3 ? h.floor * 0.995 + rms * 0.005 : h.floor;     // the room's own noise
        h.pre = (h.pre || []).concat([new Float32Array(f32)]).slice(-3);
        h.above = rms >= thr ? h.above + 1 : 0;
        if (h.above >= 2) { h.cur = { chunks: h.pre.slice(), early: null, follow: Date.now() < h.follow }; h.below = 0; this._earBtn.classList.add("hearing"); }
        return;
      }
      const u = h.cur;
      u.chunks.push(new Float32Array(f32));
      h.below = rms < thr * 0.6 ? h.below + frameMs : 0;
      const len = u.chunks.reduce((a, c) => a + c.length, 0) / h.rate;
      // THE EARLY LOOK: the first stretch goes now, while they keep talking
      if (!u.follow && !u.early && len >= HOT_EARLY_S) u.early = this._hotStt(h, u.chunks.slice(), true);
      if (h.below >= HOT_END_MS || len >= HOT_MAX_S) {
        h.cur = null; this._earBtn.classList.remove("hearing");
        if (len >= HOT_MIN_S) this._hotDone(h, u);
      }
    }

    /** One whisper look (budgeted): the transcript, or null. */
    async _hotStt(h, chunks, early) {
      const now = Date.now();
      h.sends = h.sends.filter((t) => now - t < 60000);
      if (h.sends.length >= HOT_PER_MIN) return null;     // the brain phone's budget
      h.sends.push(now);
      const n = chunks.reduce((a, c) => a + c.length, 0), all = new Float32Array(n); let o = 0;
      for (const c of chunks) { all.set(c, o); o += c.length; }
      try {
        const wav = wavBytes(downsample(all, h.rate, STT_RATE), STT_RATE);
        const r = await this._hass.fetchWithAuth(STT_PATH, { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
        const d = await r.json().catch(() => ({}));
        const text = (r.ok && d.text || "").trim();
        if (early && text && hotword(text).hit && this._hot === h) this._hotHeard(h);   // the chime, mid-sentence
        return text;
      } catch (e) { return null; }
    }

    /** The utterance ended: decide what it was. */
    async _hotDone(h, u) {
      if (u.follow) {                                       // the sentence after a bare "Hey <name>"
        h.follow = 0;
        const t = await this._hotStt(h, u.chunks, false);
        if (t && this._hot === h) this._hotSend(t); else this._vsay("didn't catch that");
        return;
      }
      let early = null;
      if (u.early) {
        early = await u.early;
        if (early === null || !hotword(early).hit) return;  // not for us: the rest isn't sent at all
      }
      const t = await this._hotStt(h, u.chunks, false);
      if (!t || this._hot !== h) return;
      const w = hotword(t);
      if (!w.hit && !early) return;                        // a short burst that wasn't the name: dropped
      if (!u.early) this._hotHeard(h);
      const rest = w.hit ? w.rest : t;
      if (rest.split(/\s+/).filter(Boolean).length >= 2) { this._hotSend(rest); return; }
      if (FOLLOW_ON) h.follow = Date.now() + HOT_FOLLOW_MS;
      this._hotAck(h);
    }

    /** It heard its name: the chime, at once (made here, no network). */
    _hotHeard(h) {
      if (h.chimed && performance.now() - h.chimed < 1500) return;
      h.chimed = performance.now();
      h.muteUntil = performance.now() + 350;                // don't hear the chime itself
      try {
        const ctx = h.ctx, t0 = ctx.currentTime;
        for (const [f, at] of [[880, 0], [1318.5, 0.11]]) {
          const o = ctx.createOscillator(), g = ctx.createGain();
          o.type = "sine"; o.frequency.value = f;
          g.gain.setValueAtTime(0.0001, t0 + at); g.gain.exponentialRampToValueAtTime(0.18, t0 + at + 0.015);
          g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.16);
          o.connect(g); g.connect(ctx.destination); o.start(t0 + at); o.stop(t0 + at + 0.18);
        }
      } catch (e) { /* no sound: the bubble still says it */ }
      this._vsay("👂 yes?", 3000);
    }

    /** A bare "Hey <name>": a short spoken ack in the seat's voice (fetched once). */
    /** The wake word heard: a chime, not a spoken "uh-huh" (2026-10-11, the
     *  operator: the "uh-huhs" and "yesses" cut into him listening to me). */
    async _hotAck(h) {
      this._vsay(FOLLOW_ON ? "listening… (say it now)" : "heard my name — say it with the request, in one breath", 3000);
      this._orb("listening");
      h.muteUntil = performance.now() + 260;              // the mic never hears its own chime
      this._wakeChime();
    }

    /** Two soft notes, made on the device (WebAudio): nothing to download, nothing spoken.
     *  (#331: its own name -- as a second _chime it had replaced the bell's.) */
    _wakeChime() {
      try {
        const C = window.AudioContext || window.webkitAudioContext; if (!C) return;
        const ac = this._chimeAc = this._chimeAc || new C();
        if (ac.state === "suspended") ac.resume().catch(() => {});
        const t0 = ac.currentTime + 0.01;
        for (const [f, at] of [[880, 0], [1318.5, 0.09]]) {
          const o = ac.createOscillator(), g = ac.createGain();
          o.type = "sine"; o.frequency.value = f;
          g.gain.setValueAtTime(0, t0 + at);
          g.gain.linearRampToValueAtTime(0.07, t0 + at + 0.015);
          g.gain.exponentialRampToValueAtTime(0.0001, t0 + at + 0.16);
          o.connect(g).connect(ac.destination);
          o.start(t0 + at); o.stop(t0 + at + 0.18);
        }
      } catch (e) { /* no sound: the orb and the status line still say it */ }
    }

    async _prefetchAcks() {
      if (this._ackUrls || this._voice) return;
      const engine = this._ttsEngine(); if (!engine) return;
      this._ackUrls = {};
      for (const a of HOT_ACKS) {
        try { const r = await this._hass.callApi("POST", "tts_get_url", { engine_id: engine, message: a }); this._ackUrls[a] = r.path || r.url; }
        catch (e) { /* that one falls back to the browser's voice */ }
      }
    }

    _hotSend(text) {
      const queued = this._busyChat();
      this._voiceTurn = true;
      if (this._chat && this._chat.sendVoice) this._chat.sendVoice(text);   // voice:in, not a chat bubble
      else this._sendToChat(text);
      this._vsay("“" + text + "” — " + (queued ? "cutting in on " + SEAT + "'s turn" : "sent; " + SEAT + " is thinking…"), 12000);
    }

    // ---------------------------------------------------------- speech out
    _ttsEngine() {
      const ids = Object.keys((this._hass && this._hass.states) || {}).filter((k) => k.startsWith("tts."));
      return ids.find((k) => k.includes("google_translate")) || ids[0] || null;
    }

    _hushTts() {
      this._ttsGen = (this._ttsGen || 0) + 1;
      this._speaking = false;
      this._speechQ = [];
      if (this._audio) { try { this._audio.pause(); if (this._audio.onended) this._audio.onended(); } catch (e) { /* gone */ } this._audio = null; }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    }

    /** The speech queue (2026-10-10: membrane's long babble queued AHEAD of
     * the seat and the cap dropped his words): the seat first -- his segment cuts a
     * membrane line that's playing and jumps every queued one; membrane says
     * at most two sentences, only when the seat has nothing waiting, and a line
     * older than 30 s is dropped. The seat's own segments are never dropped. */
    _enqueueSpeech(kind, text) {
      const q = this._speechQ = this._speechQ || [];
      const item = { kind, text, ts: Date.now() };
      if (kind === "membrane") {
        q.push(item);
        while (q.filter((x) => x.kind === "membrane").length > 3) q.splice(q.findIndex((x) => x.kind === "membrane"), 1);
      } else {
        const at = q.findIndex((x) => x.kind === "membrane");
        if (at < 0) q.push(item); else q.splice(at, 0, item);  // ahead of every membrane line
        if (this._speakingWho === "membrane") this._cutCurrent();
      }
      if (!this._qRunning) this._drainSpeech();
    }

    /** Stop the utterance playing now, keep the queue. */
    _cutCurrent() {
      this._cutGen = (this._cutGen || 0) + 1;
      try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) { /* none */ }
      if (this._audio) { try { this._audio.pause(); if (this._audio.onended) this._audio.onended(); } catch (e) { /* gone */ } }
    }

    async _drainSpeech() {
      this._qRunning = true;
      const gen = this._ttsGen;
      try {
        while ((this._speechQ || []).length && gen === this._ttsGen) {
          this._speechQ = catchUp(this._speechQ, Date.now());     // never fall behind the chat
          this._rateBoost = this._speechQ.length > CATCHUP_FAST_AT ? CATCHUP_RATE : 1;
          if (!this._speechQ.length) break;
          const item = this._speechQ.shift();
          if (item.kind === "membrane") {
            if (Date.now() - item.ts > 30000) continue;    // stale babble
            if (this._speechQ.some((x) => x.kind !== "membrane")) { this._speechQ.push(item); continue; }
            this._speakingWho = "membrane";
            await this._speak(speechChunks(item.text).slice(0, 2).join(" "), "membrane", true);
          } else {
            this._speakingWho = "seat";
            await this._speak(item.text, "seat", true);
          }
          this._speakingWho = null;
        }
      } finally {
        this._qRunning = false;
        this._speaking = false;
        this._speakingWho = null;
        if (!(this._speechQ || []).length) this._speechDone();   // a busy chat may stay busy: don't wait for idle
      }
    }

    /** Membrane's own voice: another of this device's voices, a little higher. */
    _membraneVoice() {
      const vs = (window.speechSynthesis ? window.speechSynthesis.getVoices() : [])
        .filter((v) => v.localService && String(v.lang || "").toLowerCase().startsWith("en") && v !== this._voice);
      return vs.find((v) => !/google/i.test(v.name) && /female|samantha|karen|moira|tessa|zoe|ava|allison|susan/i.test(v.name))
        || vs.find((v) => !/google/i.test(v.name)) || this._voice;
    }

    /** One utterance in the device's own voice; resolves when it ends. */
    _say1(text, who = "seat") {
      return new Promise((res) => {
        try {
          // Chrome's engine stalls on long stretches: keep it awake, and clear a stuck queue first
          const ss = window.speechSynthesis;
          if (ss.speaking && !this._speaking) ss.cancel();
          // CHROME ONLY: its engine stalls on long stretches and a pause/resume
          // keeps it going -- in Firefox a pause() can leave speech stuck for good
          clearInterval(this._ssKeep);
          if (IS_CHROME) this._ssKeep = setInterval(() => { if (ss.speaking) { ss.pause(); ss.resume(); } else clearInterval(this._ssKeep); }, 10000);
          const u = new SpeechSynthesisUtterance(text);
          const v = who === "membrane" ? this._membraneVoice() : this._voice;
          try { if (v) { u.voice = v; u.lang = v.lang; } } catch (e) { /* a vanished voice: the default speaks */ }
          if (who === "membrane") { u.pitch = 1.25; u.rate = 1.05; }
          u.rate = (u.rate || 1) * (this._rateBoost || 1);         // a backlog: a little faster
          u.onend = () => res();
          u.onerror = (ev) => { speechLog(who, "engine error: " + ((ev && ev.error) || "?"), text); res(); };
          window.speechSynthesis.speak(u);
          setTimeout(res, 2000 + text.length * 120);        // a stuck engine never holds the mic
        } catch (e) { res(); }
      });
    }

    async _speak(text, who = "seat", queued = false) {
      if (!queued) this._hushTts();
      if (this._voice && window.speechSynthesis) {          // ON THE DEVICE: nothing sent anywhere
        const gen = this._ttsGen;
        this._speaking = true;
        this._orb("speaking");
        const cut = this._cutGen;
        for (const chunk of speakable(text)) {
          if (gen !== this._ttsGen || cut !== this._cutGen) break;   // hushed, or cut for the seat
          this._orbText(chunk);
          await this._say1(chunk, who);
        }
        this._speaking = false;
        if (gen === this._ttsGen && !queued) { this._orb(null); this._orbText(""); }
        return;
      }
      if (!this._noLocalVoiceSaid) {
        this._noLocalVoiceSaid = true;
        this._vsay("this device has no voice of its own: using Home Assistant's", 5000);
      }
      const gen = this._ttsGen, engine = this._ttsEngine();
      for (const chunk of speakable(text)) {
        if (gen !== this._ttsGen || !this._ttsOn) return;
        try {
          if (!engine) throw new Error("no tts entity");
          const r = await this._hass.callApi("POST", "tts_get_url", { engine_id: engine, message: chunk });
          if (gen !== this._ttsGen) return;
          await new Promise((res, rej) => {
            // THE unlocked player (phones refuse a fresh one); the PATH (same
            // origin): the url may be HA's http LAN address, which https can't play
            const a = this._audio = this._player || new Audio();
            a.onended = () => res();
            a.onerror = () => rej(new Error("audio failed"));
            a.src = r.path || r.url;
            a.play().catch(rej);                           // refused: the browser's voice instead
          });
        } catch (e) {
          if (gen !== this._ttsGen) return;
          if (!window.speechSynthesis) { this._vsay("can't play sound here: tap 🔊 once"); return; }
          await new Promise((res) => { const u = new SpeechSynthesisUtterance(chunk); u.onend = u.onerror = () => res(); window.speechSynthesis.speak(u); });
        }
      }
    }

    // ---------------------------------------------------------- tilt
    _wireTilt(root) {
      const btn = this._tiltBtn = root.querySelector(".tilt");
      const touch = (navigator.maxTouchPoints || 0) > 0 || window.matchMedia("(pointer: coarse)").matches;
      btn.hidden = !(typeof window.DeviceOrientationEvent !== "undefined" && touch);
      btn.addEventListener("click", () => (this._tiltOn ? this._tiltStop() : this._tiltStart()));
    }

    async _tiltStart() {
      const DOE = window.DeviceOrientationEvent;
      try { if (DOE && typeof DOE.requestPermission === "function" && (await DOE.requestPermission()) !== "granted") { this._say("motion access was refused"); return; } }
      catch (e) { this._say("motion access was refused"); return; }
      this._tiltOn = true; this._tiltBase = null; this._tiltBtn.classList.add("on");
      this._onTilt = (e) => {
        if (e.beta == null || e.gamma == null || !this._v3) return;
        const ang = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
        const [lr, fb] = tiltAxes(e.beta, e.gamma, ang);
        if (!this._tiltBase) this._tiltBase = { lr, fb, theta: this._v3.orbit.theta, phi: this._v3.orbit.phi };
        this._tiltAng = tiltOrbit(this._tiltBase, lr, fb);
      };
      window.addEventListener("deviceorientation", this._onTilt);
      setTimeout(() => { if (this._tiltOn && !this._tiltBase) { this._tiltStop(); this._say("no motion sensor answered"); } }, 2500);
      this._say("tilt to turn the view");
    }

    _tiltStop() {
      this._tiltOn = false; this._tiltAng = null; this._tiltBase = null;
      if (this._tiltBtn) this._tiltBtn.classList.remove("on");
      if (this._onTilt) window.removeEventListener("deviceorientation", this._onTilt);
    }

    /** A MIND'S ORB selected (2026-10-11, the operator: "make the chat switch
     * to the thinker's loop when I select the thinker orb, loop-x for a loop
     * orb, ditto the dreamer; back to chat when I deselect; the cam follows
     * the selected orb"). */
    async _selectMind(agent) {
      const chat = this._chat;
      if (!agent) {
        this._mindFollow = null; this._goal = null;
        if (chat && chat.endMind) chat.endMind();
        this._aim(true);
        return;
      }
      this._mindFollow = agent; this._selFollow = null; this._sel = null;
      if (this._card) this._card.classList.remove("open");
      this._say("following " + agent);
      const kind = /^loop/.test(agent) ? "loop" : agent;
      let sid = ((this._v3 && this._v3.agentSids) || {})[agent] || null;
      if (kind === "loop" && !sid) {
        try { const d = await this._get("/membrane/minds"); sid = ((d.loops || {})[agent] || {}).sid || null; } catch (e) { /* not an admin, or the seat is old */ }
      }
      if (this._mindFollow !== agent) return;
      if (chat && chat.viewMind) chat.viewMind({ name: agent, kind, sid });
    }

    _select(m) {
      if (m && this._mindFollow) { this._mindFollow = null; if (this._chat && this._chat.endMind) this._chat.endMind(); }
      this._sel = m || null;
      this._selFollow = m ? m.id : null;
      if (!m) { this._card.classList.remove("open"); this._goal = null; this._aim(true); return; }
      this._aim(true);
      this._lastTouch = performance.now();
      this._card.querySelector(".t").textContent = m.label || m.id;
      this._card.querySelector(".k").textContent = (m.kind || "") + (m.mass != null ? "  ·  mass " + m.mass : "");
      this._card.classList.add("open");
    }

    _showHits() {
      const v = this._v3, q = this._q.value;
      // the rendered nodes answer FIRST (instant); then the seat's whole
      // catalog (membrane/map/search) fills the list -- a hit needn't be
      // on the field (2026-10-13, the operator's ask); the newest
      // keystroke wins (stale answers from racing fetches never paint)
      const local = v && q.trim() ? v.search(q) : [];
      this._paintHits(local, q);
      if (!q.trim() || !v || typeof v.searchAll !== "function") return;
      const seq = (this._hitSeq = (this._hitSeq || 0) + 1);
      Promise.resolve(v.searchAll(q)).then((hits) => {
        if (seq !== this._hitSeq || !this._v3) return;
        this._paintHits(hits || [], q);
      }).catch(() => {});
    }

    _paintHits(hits, q) {
      const v = this._v3;
      const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      this._hitMeta = new Map(hits.map((n) => [n.id, n]));
      this._hits.innerHTML = hits.map((n) => '<div class="hit" data-id="' + esc(n.id) + '"><span class="sw" style="background:' +
        hKindColor(n.kind) + '"></span>' + esc(n.label) + '<span class="k">' + esc(n.kind) + "</span></div>").join("") ||
        (q.trim() && v ? '<div class="hit" data-id="' + esc(q.trim()) + '">go to ' + esc(q.trim()) + '<span class="k">(by id)</span></div>' : "");
      this._hits.classList.toggle("open", !!this._hits.innerHTML);
    }

    async _pickHit(id) {
      const v = this._v3; if (!v) return;
      this._hits.classList.remove("open");
      const m = (this._hitMeta && this._hitMeta.get(id)) || { id, label: id, kind: "" };
      this._lastTouch = performance.now();
      const ok = await v.flyTo(id);
      if (!ok) { this._say("not on the field: " + id); return; }
      this._select(m);
    }

    _askAbout() {
      if (!this._sel || !this._chat || !this._chat.shadowRoot) return;
      const ta = this._chat.shadowRoot.querySelector("textarea");
      if (!ta) return;
      ta.value = (ta.value ? ta.value + " " : "") + "[about " + this._sel.id + "] ";
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      if (this._chatBox.classList.contains("hidden")) this.shadowRoot.querySelector(".chatbtn").click();
      ta.focus();
    }
  }
  customElements.define("membrane-home-panel", MembraneHomePanel);
  // the chat element lives in panel.js (the AI Dashboard's): load it once
  if (!customElements.get("ai-terminal-panel")) import("/ui/www/panel.js");
}
