/**
 * The Membrane Field in 3D (2026-10-08, viewer v3): the WHOLE field, live.
 *
 * Data (ai_terminal proxy, GET only):
 *   serve/membrane/map/field3d?axes=...   the projection: pid, n_total, kinds
 *   serve/membrane/map/tile?pid&l&x&y&z   one octree cell's entities + edges
 *   serve/membrane/map/locate?pid&eid     where an entity sits
 *   serve/membrane/map/search?q&limit     the WHOLE catalog (search needs
 *                                         every entity, not the budget's)
 *   serve/event                           field.* / mind.* frames (HA admins)
 *
 * THE RECYCLED FIELD (Android's lists, in space): the seat projects every
 * live entity into a cube and files each into exactly ONE octree tile
 * (heaviest coarsest, K per cell). Each frame the view walks the octree
 * from the root, refines a tile when it covers enough of the screen, fetches
 * what it lacks and unloads tiles that left the view (an LRU under a node
 * budget). Zoom in: more of the field appears; pan away: it's let go.
 *
 * THE LIVE LAYER: moved entities glide, applied ones pulse, watch fires
 * ring; each mind (the chat, the phone, loop-1..3, the thinker, the
 * dreamer) is an avatar that flies to the entity it is processing or
 * touching with a tool, beams onto it while attached, and trails between.
 *
 * Controls: drag = orbit, right-drag / shift-drag / two fingers = pan,
 * wheel / pinch = zoom, click = the drawer, double-click = fly there.
 */
const F3_WORLD = 1000;
const LOCATE_CACHE_MAX = 500;      // every entity a mind touches is located: cap it
const BRAIN_TRAIL = 8;             // #237: self's last looks drawn (dots + paths), capped
const BRAIN_RINGS = 4;             // #237: shift / level-up rings alive at once, capped
function trailAlpha3(i, n) { return 0.15 + 0.75 * (i + 1) / Math.max(1, n); }   // = home.js trailAlpha
// #244: a look at a thing with no field point is drawn as a GHOST -- a hollow
// dashed ring at a stable spot a short fixed way from the amoeba (its
// direction a hash of the eid), reached by a dashed beam, labelled
// "(unplaced)"; never a filled dot, never on the trail, never where a real
// field position could be mistaken for it
const GHOST_R = 70;                // world units from the amoeba (the cube is F3_WORLD wide)
const BRAIN_GHOST = 0x9aa7b8;      // the ghost's colour token: muted, never a look's answer colour

/** FNV-1a, 32 bits: a stable number per string. */
function fnv32(s) {
  let h = 0x811c9dc5;
  s = String(s == null ? "" : s);
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}

/** A stable unit direction per eid (uniform on the sphere). */
function ghostDir(eid) {
  const u = fnv32(eid) / 4294967296, v = fnv32(String(eid) + "#ghost") / 4294967296;
  const z = 1 - 2 * u, r = Math.sqrt(Math.max(0, 1 - z * z)), phi = 2 * Math.PI * v;
  return [r * Math.cos(phi), r * Math.sin(phi), z];
}

/** Where a look's beam goes: the thing's field point, else a ghost GHOST_R
 *  from the amoeba (apos) in the eid's direction; with no amoeba, nowhere. */
function lookTarget(p, eid, apos) {
  if (p) return { p: p.slice(), ghost: false };
  if (!apos) return { p: null, ghost: false };
  const d = ghostDir(eid);
  return { p: [apos[0] + d[0] * GHOST_R, apos[1] + d[1] * GHOST_R, apos[2] + d[2] * GHOST_R], ghost: true };
}

function lookLabel(label, ghost) { return ghost ? label + " (unplaced)" : label; }

/** The look's chip text: 👁 name (unplaced for a ghost) + its answer. */
function lookChip(label, answer, ghost) {
  const mark = answer === "changed" ? " ✓ changed" : answer === "unchanged" ? " · unchanged" : " · no answer";
  return "👁 " + lookLabel(label, ghost) + mark;
}

// ---- #237 (review of #241): the labels round self, readable on both themes ----
// a chip on the theme's card colour with the theme's text colour; the label's
// own colour is its left edge (set per label)
const SELF_LABEL_CSS = "position:absolute;left:0;top:0;padding:1px 6px 1px 5px;border-radius:6px;white-space:nowrap;pointer-events:none;"
  + "font:600 11px/16px system-ui,sans-serif;color:var(--primary-text-color,#e1e1e1);"
  + "background:color-mix(in srgb, var(--card-background-color,#1c1c1c) 84%, transparent);"
  + "box-shadow:0 0 0 1px color-mix(in srgb, var(--divider-color,#888) 45%, transparent);border-left:3px solid #888;";

/** The visible field: the canvas (w x h) minus each panel over it (rects in
 *  the canvas's own coordinates), each cut from the side that keeps the most. */
function fieldArea(w, h, rects) {
  let A = { x0: 0, y0: 0, x1: w, y1: h };
  for (const r of rects || []) {
    if (!r || !(r.w > 0) || !(r.h > 0)) continue;
    const rx0 = r.x, ry0 = r.y, rx1 = r.x + r.w, ry1 = r.y + r.h;
    if (rx1 <= A.x0 || rx0 >= A.x1 || ry1 <= A.y0 || ry0 >= A.y1) continue;     // not over what is left
    const cuts = [Object.assign({}, A, { x0: Math.max(A.x0, rx1) }), Object.assign({}, A, { x1: Math.min(A.x1, rx0) }),
                  Object.assign({}, A, { y0: Math.max(A.y0, ry1) }), Object.assign({}, A, { y1: Math.min(A.y1, ry0) })];
    const size = (c) => Math.max(0, c.x1 - c.x0) * Math.max(0, c.y1 - c.y0);
    A = cuts.reduce((best, c) => (size(c) > size(best) ? c : best), cuts[0]);
  }
  return A;
}

function inArea(x, y, A) { return !A || (x >= A.x0 && x <= A.x1 && y >= A.y0 && y <= A.y1); }

/** An arrow from one screen point toward another. */
function arrowFor(x0, y0, x1, y1) {
  const ang = Math.atan2(y1 - y0, x1 - x0);
  return ["→", "↘", "↓", "↙", "←", "↖", "↑", "↗"][((Math.round(ang / (Math.PI / 4)) % 8) + 8) % 8];
}

/** Lay labels out round their anchors: the most important first keep their
 *  place (anchor + dx/dy), each next one steps down / up a row until it
 *  overlaps none; every box stays inside the area; an anchor outside it (a
 *  thing behind a panel) puts its label at the edge with an arrow toward it;
 *  an optional label with no room hides instead of piling up.
 *  items: [{key, ax, ay, w, h, dx?, dy?, prio?, optional?}] -> {key: {x, y, out, arrow, hidden}} */
function layoutLabels(items, A) {
  const out = {}, placed = [], GAP = 2;
  const order = (items || []).map((it, i) => [it, i]).sort((p, q) => ((q[0].prio || 0) - (p[0].prio || 0)) || (p[1] - q[1]));
  for (const [it] of order) {
    const w = it.w || 0, h = it.h || 16;
    const isOut = !inArea(it.ax, it.ay, A);
    if (isOut && it.optional) { out[it.key] = { x: 0, y: 0, out: true, arrow: "", hidden: true }; continue; }   // a mark: only where it is seen
    const ax = Math.min(Math.max(it.ax, A.x0), A.x1), ay = Math.min(Math.max(it.ay, A.y0), A.y1);
    let x = ax + (it.dx == null ? 8 : it.dx), y = ay + (it.dy == null ? -8 : it.dy);
    x = Math.max(A.x0, Math.min(x, A.x1 - w)); y = Math.max(A.y0, Math.min(y, A.y1 - h));
    const hits = (yy) => placed.some((p) => x < p.x + p.w && p.x < x + w && yy < p.y + p.h && p.y < yy + h);
    let at = null;
    for (let k = 0; k <= 12 && at == null; k++) {
      const s = (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (h + GAP);
      const yy = y + (k === 0 ? 0 : s);
      if (yy < A.y0 || yy + h > A.y1) continue;
      if (!hits(yy)) at = yy;
    }
    if (at == null && it.optional) { out[it.key] = { x, y, out: isOut, arrow: "", hidden: true }; continue; }
    if (at == null) at = y;
    placed.push({ x, y: at, w, h });
    out[it.key] = { x, y: at, out: isOut, arrow: isOut ? arrowFor(ax, ay, it.ax, it.ay) : "", hidden: false };
  }
  return out;
}

/** self's tag: short while the camera follows self (the exp bar carries the rest).
 *  name: what self is called (#277: the operator named it Blurp; home.js passes
 *  the seat's name in as the view's selfName). */
function selfTag(L, short, name) {
  L = L || {};
  name = name || "Blurp";
  const pct = Math.round(100 * (L.bar || 0));
  if (short) return name + " · Lv " + (L.rung || 0) + " · " + pct + "%";
  const skill = L.skill == null ? "" : " · skill " + Number(L.skill).toFixed(2);
  const otherEid = (L.meets || {}).other;
  const split = otherEid && L.split ? L.split[otherEid] : null;
  const meet = split ? " · meets " + (split === "other" ? "an other" : "its body") : "";
  return name + " · rung " + (L.rung || 0) + " · " + pct + "%" + skill
    + (L.steer != null ? " · lands " + Math.round(100 * L.steer) + "% closer to its aim" : "")
    + (L.slipping ? " · slipping" : "") + (L.mode === "watch" ? " · watched" : "") + meet;
}
const F3_AGENTS = {
  chat: "#ffd166", phone: "#06d6a0", "loop-1": "#ef476f", "loop-2": "#f78c6b",
  "loop-3": "#c77dff", loop: "#ff8fab", thinker: "#4cc9f0", dreamer: "#b5e48c",
};

// ------------------------------------------------------------ pure logic

function tileKey(l, x, y, z) { return l + "/" + x + "/" + y + "/" + z; }

function cubeToWorld(p) { return [(p[0] - 0.5) * F3_WORLD, (p[1] - 0.5) * F3_WORLD, (p[2] - 0.5) * F3_WORLD]; }

function tileCenter(l, x, y, z) {
  const s = 1 / (1 << l);
  return cubeToWorld([(x + 0.5) * s, (y + 0.5) * s, (z + 0.5) * s]);
}

function tileRadius(l) { return (F3_WORLD / (1 << l)) * 0.8661; }

function childrenOf(l, x, y, z) {
  const out = [];
  for (let i = 0; i < 8; i++) out.push([l + 1, 2 * x + (i & 1), 2 * y + ((i >> 1) & 1), 2 * z + ((i >> 2) & 1)]);
  return out;
}

/** The axes as the seat reads them: pcN, kind:<k>, e:<eid>[:toward]. */
function axesSpec(ax) {
  return ax.map((a) => (a.kind === "entity" ? "e:" + a.id + (a.mode === "toward" ? ":toward" : "")
    : a.kind === "kind" ? "kind:" + a.name : "pc" + (a.index || 1))).join(",");
}

const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len3 = (a) => Math.hypot(a[0], a[1], a[2]);

/**
 * The tiles the camera wants: from the root down, a tile in the view cone is
 * wanted; it refines into its 8 children while it spans more than
 * thresholdPx on screen (and the seat said it has finer levels). Coarse and
 * near first; capped at maxTiles.
 */
function wantedTiles(cam, opts) {
  const o = Object.assign({ maxLevel: 8, thresholdPx: 260, maxTiles: 500, known: new Map() }, opts || {});
  const tanH = Math.tan(cam.fovY / 2);
  const halfDiag = Math.atan(tanH * Math.sqrt(1 + cam.aspect * cam.aspect));
  const out = [];
  const queue = [[0, 0, 0, 0]];
  while (queue.length && out.length < o.maxTiles * 4) {
    const [l, x, y, z] = queue.shift();
    const c = tileCenter(l, x, y, z), r = tileRadius(l);
    const v = sub3(c, cam.pos), dist = len3(v);
    if (dist > r) {
      const cosA = dot3(v, cam.fwd) / dist;
      const ang = Math.acos(Math.max(-1, Math.min(1, cosA)));
      if (ang > halfDiag + Math.asin(Math.min(1, r / dist))) continue;
    }
    const screen = (2 * r / Math.max(dist - r, 1e-3)) / (2 * tanH) * cam.heightPx;
    const key = tileKey(l, x, y, z);
    out.push({ key, l, x, y, z, pri: l * 1e7 + dist });
    const k = o.known.get(key);
    // refine only a LOADED tile that said it holds finer entities: the
    // walk deepens level by level, never into empty octants
    if (screen > o.thresholdPx && l < o.maxLevel && k && k.more) {
      for (const ch of childrenOf(l, x, y, z)) queue.push(ch);
    }
  }
  out.sort((a, b) => a.pri - b.pri);
  return out.slice(0, o.maxTiles);
}

/** The recycled tiles: loaded ones by key; what isn't wanted goes, oldest
 * first -- after a grace period, or at once when over the node budget. */
class TileCache {
  constructor(nodeBudget, graceMs) { this.budget = nodeBudget || 8000; this.grace = graceMs == null ? 2500 : graceMs;
    this.tiles = new Map(); this.nodes = 0; }
  has(k) { return this.tiles.has(k); }
  get(k) { return this.tiles.get(k); }
  put(k, tile, now) { if (this.tiles.has(k)) this.drop(k); tile.ts = now == null ? Date.now() : now; this.tiles.set(k, tile);
    this.nodes += (tile.nodes || []).length; }
  touch(keys, now) { for (const k of keys) { const t = this.tiles.get(k); if (t) t.ts = now == null ? Date.now() : now; } }
  drop(k) { const t = this.tiles.get(k); if (!t) return null; this.tiles.delete(k); this.nodes -= (t.nodes || []).length; return t; }
  evict(wanted, now) {
    now = now == null ? Date.now() : now;
    const out = [];
    const idle = [...this.tiles.entries()].filter(([k]) => !wanted.has(k)).sort((a, b) => a[1].ts - b[1].ts);
    for (const [k, t] of idle) {
      if (now - t.ts > this.grace || this.nodes > this.budget) { this.drop(k); out.push(k); }
    }
    return out;
  }
  clear() { this.tiles.clear(); this.nodes = 0; }
}

/** One mind's avatar: glides toward its target, attached while it holds
 * an entity, back to its idle orbit when it lets go. */
const THINK_MAX_MS = 180000;             // a lost 'end' never leaves an orb thinking forever
/** A thinking orb's pose now: a slow circle (radius 22) and a breath (±18%). */
function thinkPose(since, now) {
  if (!since || now - since > THINK_MAX_MS) return { dx: 0, dy: 0, dz: 0, scale: 1 };
  const t = (now - since) / 1000;
  return { dx: Math.cos(t * 1.6) * 22, dy: Math.sin(t * 2.3) * 6, dz: Math.sin(t * 1.6) * 22, scale: 1 + 0.18 * Math.sin(t * 3.1) };
}

const AVATAR_HIT_PX = 26;               // a tap this close to an orb selects it

class Avatar {
  constructor(agent, idle) { this.agent = agent; this.idle = idle.slice(); this.pos = idle.slice(); this.target = idle.slice();
    this.eid = null; this.until = 0; this.trail = []; this._lastTrail = 0; this.hold = false; }
  attach(eid, p, holdMs, now) { this.eid = eid; this.target = p.slice(); this.hold = holdMs == null;
    this.at = now == null ? Date.now() : now;
    this.until = (now == null ? Date.now() : now) + (holdMs == null ? 0 : holdMs); }
  release() { this.hold = false; this.until = 0; }
  attached(now) { return !!this.eid && (this.hold || (now == null ? Date.now() : now) < this.until); }
  step(dt, now) {
    now = now == null ? Date.now() : now;
    if (this.eid && !this.attached(now)) { this.eid = null; this.target = this.idle.slice(); }
    const k = 1 - Math.exp(-dt * 3.2);
    for (let i = 0; i < 3; i++) this.pos[i] += (this.target[i] - this.pos[i]) * k;
    if (now - this._lastTrail > 90) { this.trail.push(this.pos.slice()); if (this.trail.length > 32) this.trail.shift(); this._lastTrail = now; }
  }
}

/** The field keys a store spec may stand for: the store's long kinds
 * (files:, symbols:, shell:, urls:, models:) are the field's f:/s:/c:/u:/m:;
 * the membrane:: namespace is the field's m: mirror. */
function locateCandidates(eid) {
  const out = [eid];
  const bare = String(eid || "").replace(/^membrane::/, "");
  const m = /^(files|symbols|shell|urls|models):(.*)$/.exec(bare);
  if (m) out.push({ files: "f", symbols: "s", shell: "c", urls: "u", models: "m" }[m[1]] + ":" + m[2]);
  if (!/^[a-z]{1,4}:/.test(bare) || /^membrane::/.test(eid)) out.push("m:" + bare);
  else if (!m) out.push("m:" + bare);
  if (!m && !/^[a-z]{1,4}:/.test(bare)) out.push("m:" + bare.toLowerCase());   // field_id lowercases the mirror (#244)
  return [...new Set(out)];
}

/** SSE bytes-as-text -> complete JSON frames + the unfinished rest. */
function parseSSE(buf) {
  const frames = [];
  const parts = buf.split(/\r?\n\r?\n/);
  const rest = parts.pop();
  for (const block of parts) {
    const data = block.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
    if (!data) continue;
    try { frames.push(JSON.parse(data)); } catch (e) { /* a torn frame: skip */ }
  }
  return { frames, rest };
}

/** EDGE PULSES (2026-10-10, the operator: "show all the events on the visible
 *  edges of the graph as pulses"): for each event's entity group, the pairs
 *  joined by a VISIBLE edge, from the event's first entity outwards, capped. */
const PULSE_COLORS = { discourse: [1.0, 0.82, 0.35], observation: [0.35, 0.85, 1.0], code: [0.75, 0.55, 1.0] };
function edgePulsePairs(groups, hasEdge, cap = 48) {
  const out = [];
  for (const g of groups || []) {
    const e = g.e || [];
    for (let i = 0; i < e.length && out.length < cap; i++) {
      for (let j = i + 1; j < e.length && out.length < cap; j++) {
        if (hasEdge(e[i], e[j])) out.push([e[i], e[j], g.s || ""]);
      }
    }
  }
  return out;
}

/** A tiny IndexedDB key-value store (the viewer's snapshot: it opens on the
 * last view at once while the seat projects afresh). Every call swallows
 * errors and answers null: private windows, blocked storage and old browsers
 * just load from the network as before. */
class IDBCache {
  constructor(name) { this.name = name; this._db = null; }
  _open() {
    if (this._db) return this._db;
    this._db = new Promise((res) => {
      try {
        if (typeof indexedDB === "undefined") return res(null);
        const rq = indexedDB.open(this.name, 1);
        rq.onupgradeneeded = () => rq.result.createObjectStore("kv");
        rq.onsuccess = () => res(rq.result);
        rq.onerror = () => res(null);
        rq.onblocked = () => res(null);
      } catch (e) { res(null); }
    });
    return this._db;
  }
  async get(key) {
    const db = await this._open(); if (!db) return null;
    return new Promise((res) => {
      try { const rq = db.transaction("kv").objectStore("kv").get(key); rq.onsuccess = () => res(rq.result || null); rq.onerror = () => res(null); }
      catch (e) { res(null); }
    });
  }
  async put(key, val) {
    const db = await this._open(); if (!db) return false;
    return new Promise((res) => {
      try { const tx = db.transaction("kv", "readwrite"); tx.objectStore("kv").put(val, key); tx.oncomplete = () => res(true); tx.onerror = () => res(false); }
      catch (e) { res(false); }
    });
  }
}

/** What a snapshot keeps: the projection's meta and the loaded tiles (the
 * newest `max`), plain data only. */
function snapshotOf(info, tiles, max = 400) {
  const meta = { ...info }; delete meta.root;
  const out = [];
  for (const [key, t] of tiles) { out.push([key, t]); }
  return { v: 1, ts: Date.now(), meta, tiles: out.slice(-max) };
}

// ---- #257: THE AMOEBA ACTS IT OUT -- pure pose logic (node-tested) ------------
// The body acts out self's record, nothing scripted: each record line (and the
// cycle frame) becomes zero or more ACTS ({kind, t0, dir, ...}); an act's pose
// is a pure function of its age, eased in and out, and back to the calm
// baseline when its life is over; the blender sums the live acts onto that
// baseline (clamped) into one reused object -- no per-frame allocation.
// A pose (all numbers, the calm baseline all 0):
//   sx sy sz   a stretch (world direction x amount; 0.5 = half again as long)
//   ox oy oz   an offset of the body (world direction x body radii)
//   px py pz   the pseudopod (its direction x reach), reach 0..1 its length
//   puff       a uniform swell (- smaller), squash (+ wide and flat: curled)
//   glow       the level-up halo, still 0..1 (damps the idle wobble), dim 0..1
// Reduced motion: an act holds one static shape for its life, no motion.
const POSE_KEYS = ["sx", "sy", "sz", "ox", "oy", "oz", "px", "py", "pz", "reach", "puff", "squash", "glow", "still", "dim"];
const ACT_CAP = 12;                  // live acts at once (the oldest goes first, listening last)
const LISTEN_MS = 600000;            // a push's listening holds until the reply check, at most 10 min
const SURPRISE_MIN = 0.5;            // |outcome - p| over this: the answer went against its lean
const POD_LEN = 55;                  // world units: a pseudopod at full reach (the body's radius is ~20)
const FACE_MIN = 0.75;               // a motion keeps at least this much of its direction on the screen (faceOn)
// [attack, hold, decay] ms per act; the life is their sum
const ACT_ENV = { move: [200, 600, 1000], look: [600, 1000, 250], surprise: [0, 0, 650], startle: [0, 0, 1800],
  wait: [600, 6000, 2400], push: [0, 0, 1400], bounce: [0, 0, 1500], droop: [800, 2200, 1500], toy: [150, 700, 650],
  glow: [300, 2500, 2200] };
const LOOK_LANDS = ACT_ENV.look[0] + ACT_ENV.look[1];   // the answer lands: the pseudopod starts back (the jolt, the wiggle)

/** The unit vector p -> q, or null when there is no way to tell (same spot, a missing end). */
function unit3(p, q) {
  if (!p || !q) return null;
  const x = q[0] - p[0], y = q[1] - p[1], z = q[2] - p[2], n = Math.hypot(x, y, z);
  return n > 1e-6 ? [x / n, y / n, z / n] : null;
}

function calmPose(out) { out = out || {}; for (const k of POSE_KEYS) out[k] = 0; return out; }

/** One record line -> the acts it plays (no time, no direction yet). */
function amoebaActs(r) {
  if (!r || typeof r !== "object") return [];
  const k = r.kind;
  if (k === "cycle") return r.act === "move" ? [{ kind: "move", stride: Math.max(0, Number(r.step) || 0), steer: r.aim_n != null || r.steer === true }] : [];
  if (k === "reach") {
    const ans = r.answer === "changed" ? 1 : r.answer === "unchanged" ? 0 : null;
    if (r.reply) {
      // the check's own outcome when it has one (#258: `replied`); #264: a look at
      // the ball reads `changed` after self's own roll and `unchanged` after a roll back
      const got = typeof r.replied === "boolean" ? (r.replied ? 1 : 0) : ans;
      return got == null ? [] : [{ kind: got ? "bounce" : "droop" }];
    }
    const out = [{ kind: "look", answer: r.answer }];
    const p = r.p == null ? NaN : Number(r.p);
    if (ans != null && isFinite(p)) {
      const err = Math.abs(ans - p);
      if (err > SURPRISE_MIN) out.push({ kind: "surprise", err, delay: LOOK_LANDS });
    }
    if (Array.isArray(r.by) && r.by.includes("self") && /(^|:)(toy|cradle):/.test(String(r.eid || ""))) out.push({ kind: "toy", delay: LOOK_LANDS });
    return out;
  }
  if (k === "push") return [{ kind: "push" }, { kind: "listen" }];
  if (k === "shift") return [{ kind: "startle" }];
  if (k === "wait") return [{ kind: "wait" }];
  if (k === "rung") return [{ kind: "glow" }];
  return [];
}

function actLife(act) {
  if (!act) return 0;
  if (act.kind === "listen") return LISTEN_MS;
  const e = ACT_ENV[act.kind];
  return e ? e[0] + e[1] + e[2] : 0;
}

function actAlive(act, now) { const a = now - ((act && act.t0) || 0); return a >= 0 && a < actLife(act); }

const sstep = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));

/** 0 -> 1 over the attack, 1 through the hold, 1 -> 0 over the decay (smooth). */
function actEnv(act, a) {
  const e = ACT_ENV[act.kind];
  if (!e) return 0;
  if (a < e[0]) return sstep(a / e[0]);
  if (a < e[0] + e[1]) return 1;
  return 1 - sstep((a - e[0] - e[1]) / Math.max(1, e[2]));
}

/** Add one act's pose at `now` onto `out` (in place). */
function addPose(out, act, now, rm) {
  if (!act || !actAlive(act, now)) return out;
  const a = now - (act.t0 || 0), life = actLife(act), u = a / life;
  const d = act.dir || null, dx = d ? d[0] : 0, dy = d ? d[1] : 0, dz = d ? d[2] : 0;
  const TAU = 2 * Math.PI;
  const e = rm ? 1 : actEnv(act, a);
  switch (act.kind) {
    case "move": {                      // stretch along the step, scaled by the stride; lean to its aim
      const s = Math.min(0.8, 0.2 + 2 * (act.stride || 0)) * e;
      out.sx += dx * s; out.sy += dy * s; out.sz += dz * s;
      const m = act.aimDir;
      if (m) { out.ox += m[0] * 0.3 * e; out.oy += m[1] * 0.3 * e; out.oz += m[2] * 0.3 * e; }
      break;
    }
    case "look": {                      // a pseudopod to the thing, back when the answer lands
      const r = Math.max(0, Math.min(1, act.reach == null ? 1 : act.reach)) * e;
      if (d && r > out.reach) { out.reach = r; out.px = dx * r; out.py = dy * r; out.pz = dz * r; }
      if (d) { out.sx += dx * 0.15 * r; out.sy += dy * 0.15 * r; out.sz += dz * 0.15 * r; }
      break;
    }
    case "surprise": {                  // a jolt away from the thing, sized by the error
      const amp = Math.max(0, Math.min(1, ((act.err || 0) - SURPRISE_MIN) / (1 - SURPRISE_MIN)));
      const k = d ? 1 : 0, jx = d ? -dx : 0, jy = d ? -dy : 1, jz = d ? -dz : 0;
      const j = rm ? 0.3 * amp : 0.5 * amp * Math.sin(TAU * a / 170) * (1 - u) * (1 - u);
      out.ox += jx * j; out.oy += jy * j; out.oz += jz * j;
      out.puff += (rm ? 0.12 : 0.18 * (1 - u)) * amp * (k || 1);
      break;
    }
    case "startle": {                   // a shift noticed: puff up, then settle
      if (rm) { out.puff += 0.3; break; }
      out.puff += a < 120 ? 0.45 * sstep(a / 120) : 0.45 * Math.exp(-(a - 120) / 380) * Math.cos((a - 120) / 170);
      break;
    }
    case "wait": {                      // slow breathing, a little curled
      const b = rm ? 0 : 0.07 * Math.sin(TAU * a / 4200);
      out.puff += (-0.1 + b) * e; out.squash += 0.12 * e; out.still += 0.6 * e;
      break;
    }
    case "push": {                      // a ripple toward the other
      const w = rm ? 0.25 : 0.45 * (0.5 - 0.5 * Math.cos(TAU * a / 450)) * Math.pow(1 - u, 1.5);
      out.ox += dx * w; out.oy += dy * w; out.oz += dz * w;
      const s = rm ? 0.2 : 0.2 * (1 - u);
      out.sx += dx * s; out.sy += dy * s; out.sz += dz * s;
      break;
    }
    case "listen": {                    // then still, leaning in, until the reply check
      const l = rm ? 1 : sstep((a - 1000) / 800);
      out.still += l; out.ox += dx * 0.08 * l; out.oy += dy * 0.08 * l; out.oz += dz * 0.08 * l;
      break;
    }
    case "bounce": {                    // they answered: hops toward them
      const h = rm ? 0.3 : 0.5 * Math.abs(Math.sin(Math.PI * a / 500)) * (1 - u);
      out.ox += dx * h; out.oy += dy * h; out.oz += dz * h;
      out.puff += rm ? 0.1 : 0.1 * (1 - u);
      break;
    }
    case "droop": {                     // silence: it sags, flattens, dims
      out.oy -= 0.3 * e; out.squash += 0.2 * e; out.dim += 0.3 * e; out.still += 0.5 * e;
      break;
    }
    case "toy": {                       // a toy it changed: a wiggle toward it
      const w = rm ? 0.1 : 0.15 * Math.sin(TAU * a / 260) * e;
      out.ox += dx * w; out.oy += dy * w; out.oz += dz * w;
      out.sx += dx * 0.22 * e; out.sy += dy * 0.22 * e; out.sz += dz * 0.22 * e;
      break;
    }
    case "glow": {                      // a level-up: the whole body glows
      out.glow += e; out.puff += (rm ? 0.1 : 0.1 * (0.6 + 0.4 * Math.sin(a / 240))) * e;
      break;
    }
    default: break;
  }
  return out;
}

function clampLen3(o, kx, ky, kz, max) {
  const n = Math.hypot(o[kx], o[ky], o[kz]);
  if (n > max) { const f = max / n; o[kx] *= f; o[ky] *= f; o[kz] *= f; }
}
const clampTo = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

/** THE BLENDER: the live acts' poses summed onto the calm baseline, clamped. */
function posesAt(acts, now, rm, out) {
  out = calmPose(out);
  if (acts) for (let i = 0; i < acts.length; i++) addPose(out, acts[i], now, rm);
  clampLen3(out, "sx", "sy", "sz", 0.8);
  clampLen3(out, "ox", "oy", "oz", 0.6);
  out.reach = clampTo(out.reach, 0, 1); out.puff = clampTo(out.puff, -0.4, 0.8);
  out.squash = clampTo(out.squash, -0.4, 0.4); out.glow = clampTo(out.glow, 0, 1);
  out.still = clampTo(out.still, 0, 1); out.dim = clampTo(out.dim, 0, 0.6);
  return out;
}

/** One act's pose alone (a fresh object). */
function poseFor(act, now, rm) { return posesAt(act ? [act] : [], now, rm); }

/** A new act into the live list: a push restarts listening, a reply check ends it; capped. */
function addAct(acts, act) {
  const k = act && act.kind;
  if (k === "bounce" || k === "droop" || k === "push" || k === "listen") {
    for (let i = acts.length - 1; i >= 0; i--) if (acts[i].kind === "listen") acts.splice(i, 1);
  }
  acts.push(act);
  while (acts.length > ACT_CAP) {
    let i = acts.findIndex((x) => x.kind !== "listen");
    acts.splice(i < 0 ? 0 : i, 1);
  }
  return acts;
}

/** Drop the finished acts, in place. */
function pruneActs(acts, now) {
  let w = 0;
  for (let i = 0; i < acts.length; i++) if (now - (acts[i].t0 || 0) < actLife(acts[i])) acts[w++] = acts[i];   // a delayed act waits
  acts.length = w;
  return acts;
}

/** The body on the screen: a stretch `s` along the screen direction (ux, uy)
 *  (area kept), then a squash `q` (+ wider and flatter), as an ellipse --
 *  {rot, a, b}: the long axis's angle, the semi-axes (1 = calm). A 2x2 SVD. */
function bodyShape(ux, uy, s, q, out) {
  out = out || {};
  const k = 1 / Math.sqrt(1 + s);
  // M = diag(1+q, 1-q) (I + s u u^T) k
  const m00 = (1 + q) * (1 + s * ux * ux) * k, m01 = (1 + q) * s * ux * uy * k;
  const m10 = (1 - q) * s * ux * uy * k, m11 = (1 - q) * (1 + s * uy * uy) * k;
  const E = (m00 + m11) / 2, F = (m00 - m11) / 2, G = (m10 + m01) / 2, H = (m10 - m01) / 2;
  const Q = Math.hypot(E, H), R = Math.hypot(F, G);
  out.a = Q + R; out.b = Math.abs(Q - R);
  out.rot = (Math.atan2(H, E) + Math.atan2(G, F)) / 2;
  return out;
}

/** A motion's direction seen from the camera (camera space, unit; z toward the
 *  viewer): ◎ self chases from behind, so a step or a look points INTO the
 *  screen and a stretch along it would vanish end-on. Keep at least `min` of
 *  it in the screen plane, turned the way it leans on screen (straight away:
 *  up the screen, toward the horizon), the depth's sign kept. Into `out`. */
function faceOn(x, y, z, min, out) {
  const r = Math.hypot(x, y);
  if (r >= min) { out[0] = x; out[1] = y; out[2] = z; return out; }
  const ux = r > 1e-6 ? x / r : 0, uy = r > 1e-6 ? y / r : 1;
  out[0] = ux * min; out[1] = uy * min; out[2] = (z < 0 ? -1 : 1) * Math.sqrt(1 - min * min);
  return out;
}

const F3Logic = { faceOn, calmPose, amoebaActs, actLife, actAlive, LOOK_LANDS, poseFor, posesAt, addAct, pruneActs, bodyShape, POD_LEN,
  ghostDir, lookTarget, lookLabel, lookChip, GHOST_R, fnv32, snapshotOf, IDBCache, locateCandidates, tileKey, tileCenter, tileRadius, childrenOf, axesSpec, wantedTiles, TileCache, Avatar, parseSSE, cubeToWorld, F3_WORLD, edgePulsePairs, PULSE_COLORS,
  fieldArea, inArea, arrowFor, layoutLabels, selfTag };
if (typeof module !== "undefined" && module.exports) module.exports = F3Logic;

// ------------------------------------------------------------ three.js

function loadThree() {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.THREE) return Promise.resolve(window.THREE);
  if (window.__f3ThreeLoading) return window.__f3ThreeLoading;
  window.__f3ThreeLoading = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "/ui/www/three.min.js";
    s.onload = () => (window.THREE ? res(window.THREE) : rej(new Error("three.js did not load")));
    s.onerror = () => rej(new Error("three.js failed to load"));
    document.head.appendChild(s);
  });
  return window.__f3ThreeLoading;
}

const F3_VERT = `
  attribute float asize; attribute vec3 acolor; varying vec3 vColor; uniform float uScale;
  void main() { vColor = acolor; vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = clamp(asize * uScale / max(1.0, -mv.z), asize > 0.0 ? 2.0 : 0.0, 22.0);
    gl_Position = projectionMatrix * mv; }`;
const F3_FRAG = `
  varying vec3 vColor;
  void main() { vec2 d = gl_PointCoord - 0.5; float r = dot(d, d); if (r > 0.25) discard;
    float a = smoothstep(0.25, 0.16, r); gl_FragColor = vec4(vColor * (0.8 + 1.2 * (0.25 - r)), a); }`;

class Field3DView {
  /**
   * api: { get(path) -> json, stream(path) -> Response (fetchWithAuth),
   *        kindColor(name) -> css, onSelect(node), onHover(node|null, x, y),
   *        onStatus(text), isAdmin: bool }
   */
  constructor(host, api) {
    this.host = host; this.api = api;
    this.budget = 8000; this.cap = 10000; this.edgeCap = 30000;
    // THE VIEWER'S OWN PACE (2026-10-13, the operator: "limit # of nodes
    // rendered based on its performance"): the gauge (_gaugeFrame) walks
    // this.budget between the floor and maxBudget -- which home.js may
    // have set lower (a small screen starts at 3000) -- from measured
    // frame costs. maxBudget is the CONFIGURED ceiling, never exceeded
    // by the adaptation.
    this.maxBudget = 8000; this.budgetFloor = 1000;
    this._ft = []; this._ftAt = 0;
    this.cache = new TileCache(this.budget);
    this.pending = new Set(); this.known = new Map();
    this.slots = new Map(); this.free = []; this.meta = []; this.hidden = new Set();
    this.tweens = new Map(); this.pulses = new Map(); this.rings = [];
    this.avatars = new Map(); this.locateCache = new Map();
    this.ax = [{ kind: "pc", index: 1 }, { kind: "pc", index: 2 }, { kind: "pc", index: 3 }];
    this.sel = null; this._alive = false; this._edgesDirty = true; this._lastWant = 0; this._lastLabels = 0;
    this.live = false;
  }

  async start() {
    const THREE = this.THREE = await loadThree();
    this._alive = true;
    const r = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: !!this.api.preserve });
    r.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    this.renderer = r;
    r.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%;touch-action:none;display:block";
    this.host.appendChild(r.domElement);
    this.labelLayer = document.createElement("div");
    this.labelLayer.style.cssText = "position:absolute;inset:0;pointer-events:none;overflow:hidden;z-index:1";
    this.host.appendChild(this.labelLayer);
    this.labels = [];
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(55, 1, 1, 20000);
    this.orbit = { target: [0, 0, 0], r: 1750, theta: 0.7, phi: 1.05 };
    // the cube's edges, faint: the space the field lives in
    const box = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(F3_WORLD, F3_WORLD, F3_WORLD)),
      new THREE.LineBasicMaterial({ color: 0x334055, transparent: true, opacity: 0.35 }));
    this.scene.add(box);
    // nodes: one preallocated point buffer, slots recycled
    const g = new THREE.BufferGeometry();
    this.posA = new Float32Array(this.cap * 3); this.colA = new Float32Array(this.cap * 3);
    this.sizeA = new Float32Array(this.cap); this.baseCol = new Float32Array(this.cap * 3); this.baseSize = new Float32Array(this.cap);
    g.setAttribute("position", new THREE.BufferAttribute(this.posA, 3));
    g.setAttribute("acolor", new THREE.BufferAttribute(this.colA, 3));
    g.setAttribute("asize", new THREE.BufferAttribute(this.sizeA, 1));
    g.setDrawRange(0, this.cap);
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), F3_WORLD * 2);
    this.pointMat = new THREE.ShaderMaterial({ vertexShader: F3_VERT, fragmentShader: F3_FRAG, transparent: true,
      depthWrite: false, uniforms: { uScale: { value: 600 } } });
    this.points = new THREE.Points(g, this.pointMat);
    this.scene.add(this.points);
    for (let i = this.cap - 1; i >= 0; i--) this.free.push(i);
    // edges: one preallocated segment buffer, rebuilt when tiles change
    const eg = new THREE.BufferGeometry();
    this.edgeA = new Float32Array(this.edgeCap * 6);
    eg.setAttribute("position", new THREE.BufferAttribute(this.edgeA, 3));
    eg.setDrawRange(0, 0);
    eg.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), F3_WORLD * 2);
    this.edges = new THREE.LineSegments(eg, new THREE.LineBasicMaterial({ color: 0x8aa0c8, transparent: true, opacity: 0.12, depthWrite: false }));
    this.scene.add(this.edges);
    // edge pulses: small bright points running along visible edges
    this.edgeSet = new Set();
    this.edgePulses = [];
    this.pulseCap = 256;
    const pg = new THREE.BufferGeometry();
    this.pulseP = new Float32Array(this.pulseCap * 3);
    this.pulseC = new Float32Array(this.pulseCap * 3);
    pg.setAttribute("position", new THREE.BufferAttribute(this.pulseP, 3));
    pg.setAttribute("color", new THREE.BufferAttribute(this.pulseC, 3));
    pg.setDrawRange(0, 0);
    pg.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), F3_WORLD * 2);
    this.pulsePts = new THREE.Points(pg, new THREE.PointsMaterial({ size: 7, sizeAttenuation: false, vertexColors: true,
      transparent: true, opacity: 0.95, depthWrite: false }));
    this.scene.add(this.pulsePts);
    // the selected entity's edges, bright (the 2D map's gold highlight)
    const hg = new THREE.BufferGeometry();
    this.hiCap = 2000;
    this.hiA = new Float32Array(this.hiCap * 6);
    hg.setAttribute("position", new THREE.BufferAttribute(this.hiA, 3));
    hg.setDrawRange(0, 0);
    hg.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), F3_WORLD * 2);
    this.hiEdges = new THREE.LineSegments(hg, new THREE.LineBasicMaterial({ color: 0xffd25a, transparent: true, opacity: 0.9, depthWrite: false }));
    this.hiEdges.renderOrder = 2;
    this.scene.add(this.hiEdges);
    // the selection's neighbours that aren't loaded yet: small gold ghosts
    const gg = new THREE.BufferGeometry();
    this.ghostA = new Float32Array(this.hiCap * 3);
    gg.setAttribute("position", new THREE.BufferAttribute(this.ghostA, 3));
    gg.setDrawRange(0, 0);
    gg.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 0, 0), F3_WORLD * 2);
    this.ghosts = new THREE.Points(gg, new THREE.PointsMaterial({ color: 0xffd25a, size: 5, sizeAttenuation: false,
      transparent: true, opacity: 0.85, depthWrite: false }));
    this.scene.add(this.ghosts);
    this.selNb = null;
    this.showEdges = true; this.showLabels = true;
    this._wire();
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(this.host);
    this._resize();
    this._t = performance.now();
    // api.maxFps caps the frame rate (membrane home: 30 -- a full-screen
    // canvas at 60 fps under page chrome taxes the whole machine)
    const minGap = this.api.maxFps ? 1000 / this.api.maxFps - 1 : 0;
    let lastT = 0;
    const loop = (t) => {
      if (!this._alive) return;
      if (!minGap || t - lastT >= minGap) { lastT = t; this._frame(); }
      this._raf = requestAnimationFrame(loop);
    };
    this._raf = requestAnimationFrame(loop);
    // THE SNAPSHOT (2026-10-10, the operator: "it takes forever to load"):
    // the last view draws at once from IndexedDB; the fresh projection loads
    // behind it and replaces it when it lands
    this.snap = this.api.snapshot ? new IDBCache(this.api.snapshot) : null;
    const old = this.snap ? await this.snap.get("snapshot") : null;
    if (old && old.v === 1 && old.meta && old.meta.pid && Array.isArray(old.tiles) && this._alive) {
      this._fromSnapshot(old);
      this.load().catch((e) => this.api.onStatus("the seat is slow to answer: showing the saved view (" + ((e && e.message) || e) + ")"));
    } else {
      await this.load();
    }
    if (this.snap) this._snapTimer = setInterval(() => this._saveSnapshot(), 30000);
    if (this.api.isAdmin) this._live();
  }

  _fromSnapshot(snap) {
    this.stale = true;
    this.info = snap.meta; this.pid = snap.meta.pid; this.kinds = snap.meta.kinds || [];
    this.kindRGB = this.kinds.map((k) => new this.THREE.Color(this.api.kindColor(k)));
    for (const [key, tile] of snap.tiles) if (tile && Array.isArray(tile.nodes)) this._ingest(key, tile);
    this._lastWant = 0;
    this._status();
    this.api.onStatus("saved view (" + Math.round((Date.now() - snap.ts) / 60000) + " min old) · projecting afresh…");
  }

  _saveSnapshot() {
    if (!this.snap || this.stale || !this.info || !this.cache.tiles.size) return;
    this.snap.put("snapshot", snapshotOf(this.info, this.cache.tiles)).catch(() => {});
  }

  stop() {
    this._saveSnapshot();
    clearInterval(this._snapTimer);
    this._alive = false; cancelAnimationFrame(this._raf);
    if (this._ro) this._ro.disconnect();
    if (this._streamAbort) this._streamAbort.abort();
    if (this.renderer) { this.renderer.dispose(); this.renderer.domElement.remove(); }
    if (this.labelLayer) this.labelLayer.remove();
  }

  // ---------------------------------------------------------- data
  async load() {
    this.api.onStatus("projecting the whole field…");
    const d = await this.api.get("/membrane/map/field3d?axes=" + encodeURIComponent(axesSpec(this.ax)));
    this.info = d; this.pid = d.pid; this.kinds = d.kinds || []; this.stale = false;
    this.kindRGB = this.kinds.map((k) => new this.THREE.Color(this.api.kindColor(k)));
    for (const k of [...this.cache.tiles.keys()]) this._unload(k);
    this.cache.clear(); this.known.clear(); this.pending.clear(); this.locateCache.clear();
    if (d.root) this._ingest(tileKey(0, 0, 0, 0), d.root);
    this._lastWant = 0;
    this._status();
    return d;
  }

  async setAxis(i, spec) { this.ax[i] = spec; await this.load(); }

  setHidden(set) { this.hidden = new Set(set); for (const [id, s] of this.slots) this._paintSlot(s); this._edgesDirty = true; }

  _ingest(key, tile) {
    this.known.set(key, { more: !!tile.more, n: (tile.nodes || []).length });
    this.cache.put(key, tile, Date.now());
    for (const n of tile.nodes || []) {
      if (this.slots.has(n.id)) continue;
      const s = this.free.pop();
      if (s == null) break;
      this.slots.set(n.id, s);
      this.meta[s] = { id: n.id, label: n.l, kind: this.kinds[n.k] || "?", k: n.k, mass: n.m, c: n.c, p: n.p, lv: n.lv };
      const w = cubeToWorld(n.p);
      this.posA.set(w, s * 3);
      const col = this.kindRGB[n.k] || new this.THREE.Color("#888");
      this.baseCol[s * 3] = col.r; this.baseCol[s * 3 + 1] = col.g; this.baseCol[s * 3 + 2] = col.b;
      this.baseSize[s] = 3 + Math.min(9, Math.sqrt(Math.max(0, n.m)) * 0.45);
      this._paintSlot(s);
    }
    this._edgesDirty = true;
    this._dirtyPoints();
  }

  _unload(key) {
    const t = this.cache.get(key);
    if (!t) return;
    for (const n of t.nodes || []) {
      const s = this.slots.get(n.id);
      if (s == null) continue;
      this.slots.delete(n.id); this.meta[s] = null; this.sizeA[s] = 0; this.tweens.delete(s); this.pulses.delete(s);
      this.free.push(s);
    }
    this._edgesDirty = true;
    this._dirtyPoints();
  }

  _paintSlot(s) {
    const m = this.meta[s];
    const off = !m || this.hidden.has(m.kind);
    this.sizeA[s] = off ? 0 : this.baseSize[s] * (this.sel && m && this.sel === m.id ? 1.8 : 1);
    for (let i = 0; i < 3; i++) this.colA[s * 3 + i] = this.baseCol[s * 3 + i];
  }

  _dirtyPoints() {
    if (!this.points) return;
    const a = this.points.geometry.attributes;
    a.position.needsUpdate = true; a.acolor.needsUpdate = true; a.asize.needsUpdate = true;
  }

  async _fetchTile(t) {
    if (this.stale) return;               // the saved view's pid: wait for the fresh one
    this.pending.add(t.key);
    try {
      const d = await this.api.get("/membrane/map/tile?pid=" + this.pid + "&l=" + t.l + "&x=" + t.x + "&y=" + t.y + "&z=" + t.z);
      if (d && d.pid === this.pid && this._alive) this._ingest(t.key, d);
    } catch (e) {
      if (/ 410 /.test(String(e.message || e))) { this.load().catch(() => {}); }
    } finally { this.pending.delete(t.key); }
  }

  _stepEdgePulses(now) {
    let k = 0;
    const keep = [];
    for (const q of this.edgePulses) {
      const u = (now - q.t0) / q.dur;
      if (u >= 1) continue;
      keep.push(q);
      if (u < 0) continue;
      const a = this.slots.get(q.a), b = this.slots.get(q.b);
      if (a == null || b == null || k >= this.pulseCap) continue;
      for (let i = 0; i < 3; i++) {
        this.pulseP[k * 3 + i] = this.posA[a * 3 + i] + (this.posA[b * 3 + i] - this.posA[a * 3 + i]) * u;
        this.pulseC[k * 3 + i] = q.col[i] * (0.4 + 0.6 * Math.sin(Math.PI * u));
      }
      k++;
    }
    this.edgePulses = keep;
    this.pulsePts.geometry.setDrawRange(0, k);
    this.pulsePts.geometry.attributes.position.needsUpdate = true;
    this.pulsePts.geometry.attributes.color.needsUpdate = true;
  }

  _rebuildEdges() {
    this._edgesDirty = false;
    this.edgeSet = new Set();
    let n = 0, h = 0;
    const sel = this.sel;
    const posOf = (id, cube) => {
      const sl = this.slots.get(id);
      return sl != null ? [this.posA[sl * 3], this.posA[sl * 3 + 1], this.posA[sl * 3 + 2]] : (cube ? cubeToWorld(cube) : null);
    };
    let g = 0;
    if (sel && this.selNb && this.selNb.id === sel) {
      const from = posOf(sel, this.selNb.p);
      for (const nb of this.selNb.nb) {
        if (!from || h >= this.hiCap) break;
        const to = posOf(nb.id, nb.p);
        this.hiA.set(from, h * 6); this.hiA.set(to, h * 6 + 3); h++;
        if (!this.slots.has(nb.id)) { this.ghostA.set(to, g * 3); g++; }
      }
    } else if (sel) {
      for (const t of this.cache.tiles.values()) {
        for (const e of t.edges || []) {
          if (e.s !== sel && e.t !== sel) continue;
          const a = this.slots.get(e.s), b = this.slots.get(e.t);
          if (a == null || b == null || h >= this.hiCap) continue;
          this.hiA.set(this.posA.subarray(a * 3, a * 3 + 3), h * 6);
          this.hiA.set(this.posA.subarray(b * 3, b * 3 + 3), h * 6 + 3);
          h++;
        }
      }
    }
    this.hiEdges.geometry.setDrawRange(0, h * 2);
    this.hiEdges.geometry.attributes.position.needsUpdate = true;
    this.ghosts.geometry.setDrawRange(0, g);
    this.ghosts.geometry.attributes.position.needsUpdate = true;
    if (this.showEdges) {
      outer: for (const t of this.cache.tiles.values()) {
        for (const e of t.edges || []) {
          const a = this.slots.get(e.s), b = this.slots.get(e.t);
          if (a == null || b == null || !this.sizeA[a] || !this.sizeA[b]) continue;
          if (n >= this.edgeCap) break outer;
          this.edgeSet.add(e.s + "|" + e.t); this.edgeSet.add(e.t + "|" + e.s);
          this.edgeA.set(this.posA.subarray(a * 3, a * 3 + 3), n * 6);
          this.edgeA.set(this.posA.subarray(b * 3, b * 3 + 3), n * 6 + 3);
          n++;
        }
      }
    }
    this.edges.geometry.setDrawRange(0, n * 2);
    this.edges.geometry.attributes.position.needsUpdate = true;
  }

  _status() {
    const total = (this.info || {}).n_total || 0;
    this.api.onStatus("showing " + this.slots.size.toLocaleString() + " of " + total.toLocaleString() +
      " · tiles " + this.cache.tiles.size + (this.pending.size ? " (+" + this.pending.size + ")" : "") +
      (this.api.isAdmin ? (this.live ? " · live ●" : " · live ○") : ""));
  }

  // ---------------------------------------------------------- the frame
  /** THE GAUGE (2026-10-13, the operator: the node limit follows the
   * machine): every frame's cost lands in a small window; every few
   * seconds the median speaks. Struggling (a frame slower than the
   * maxFps target allows) steps the budget down -- decisively, it
   * compounds; comfortable (real headroom) grows it back slowly and
   * never past the configured maxBudget. One bad frame (a GC pause, a
   * tab switch) moves nothing: the median, not the mean. */
  _gaugeFrame(cost) {
    this._ft.push(cost);
    if (this._ft.length > 45) this._ft.shift();
    const now = performance.now();
    if (this._ft.length < 30 || now < this._ftAt) return;
    this._ftAt = now + 2500;
    const s = [...this._ft].sort((a, b) => a - b);
    const med = s[s.length >> 1];
    const target = this.api.maxFps ? 1000 / this.api.maxFps - 2 : 15;
    if (med > target) {
      this._applyBudget(Math.round(this.budget * 0.7));
    } else if (med < target * 0.55) {
      this._applyBudget(Math.round(this.budget * 1.12) + 1);
    }
  }

  _applyBudget(n) {
    const b = Math.max(this.budgetFloor, Math.min(this.maxBudget, Math.round(n)));
    if (b === this.budget) return;
    this.budget = b; this.cache.budget = b;
  }

  _camVecs() {
    const o = this.orbit;
    const p = [o.target[0] + o.r * Math.sin(o.phi) * Math.sin(o.theta), o.target[1] + o.r * Math.cos(o.phi),
               o.target[2] + o.r * Math.sin(o.phi) * Math.cos(o.theta)];
    const f = sub3(o.target, p), fl = len3(f) || 1;
    return { pos: p, fwd: [f[0] / fl, f[1] / fl, f[2] / fl] };
  }

  _frame() {
    const now = performance.now(), dt = Math.min(0.1, (now - this._t) / 1000);
    this._t = now;
    const cv = this._camVecs();
    this.camera.position.set(cv.pos[0], cv.pos[1], cv.pos[2]);
    this.camera.lookAt(this.orbit.target[0], this.orbit.target[1], this.orbit.target[2]);
    // the recycler: what the camera wants, fetch / unload, ~4x a second
    if (this.pid && !this.stale && now - this._lastWant > 250) {
      this._lastWant = now;
      const want = wantedTiles({ pos: cv.pos, fwd: cv.fwd, fovY: this.camera.fov * Math.PI / 180, aspect: this.camera.aspect,
        heightPx: this._h || 600 }, { maxLevel: (this.info || {}).max_level || 0, known: this.known });
      const keys = new Set(want.map((t) => t.key));
      this.cache.touch(keys);
      const gone = this.cache.evict(keys);
      if (gone.length) this._reconcile(gone);
      let inflight = this.pending.size;
      for (const t of want) {
        if (inflight >= 6) break;
        if (this.cache.has(t.key) || this.pending.has(t.key)) continue;
        if (this.cache.nodes > this.budget) break;
        this._fetchTile(t); inflight++;
      }
      this._status();
    }
    // tweens (moved entities glide), pulses (applied entities glow)
    let moved = false;
    for (const [s, tw] of this.tweens) {
      const u = Math.min(1, (now - tw.t0) / tw.dur), e = u * u * (3 - 2 * u);
      for (let i = 0; i < 3; i++) this.posA[s * 3 + i] = tw.from[i] + (tw.to[i] - tw.from[i]) * e;
      moved = true;
      if (u >= 1) this.tweens.delete(s);
    }
    for (const [s, t0] of this.pulses) {
      const u = (now - t0) / 1400;
      if (u >= 1 || !this.meta[s]) { this.pulses.delete(s); this._paintSlot(s); continue; }
      const glow = Math.sin(Math.PI * u);
      this.sizeA[s] = this.hidden.has(this.meta[s].kind) ? 0 : this.baseSize[s] * (1 + 1.6 * glow);
      for (let i = 0; i < 3; i++) this.colA[s * 3 + i] = this.baseCol[s * 3 + i] + (1 - this.baseCol[s * 3 + i]) * 0.7 * glow;
      moved = true;
    }
    if (moved) { this._dirtyPoints(); this._edgesDirty = true; }
    if (this._edgesDirty) this._rebuildEdges();
    this._stepRings(now);
    this._stepEdgePulses(now);
    this._stepAvatars(dt);
    this._stepSelf(now);
    this._stepBrain(now);
    this._layoutSelfLabels(now);
    if (this.showLabels && now - this._lastLabels > 180) { this._lastLabels = now; this._paintLabels(); }
    this.renderer.render(this.scene, this.camera);
    this._gaugeFrame(performance.now() - now);
  }

  /** Evicted tiles are already out of the cache: free every slot no
   * remaining tile holds (one pass per eviction batch). */
  _reconcile(goneKeys) {
    const keep = new Set();
    for (const t of this.cache.tiles.values()) for (const n of t.nodes || []) keep.add(n.id);
    for (const [id, s] of [...this.slots]) {
      if (keep.has(id)) continue;
      this.slots.delete(id); this.meta[s] = null; this.sizeA[s] = 0; this.tweens.delete(s); this.pulses.delete(s); this.free.push(s);
    }
    for (const k of goneKeys) this.known.delete(k);
    this._edgesDirty = true; this._dirtyPoints();
  }

  _project(p) {
    const v = new this.THREE.Vector3(p[0], p[1], p[2]).project(this.camera);
    if (v.z > 1 || v.z < -1) return null;
    return [(v.x + 1) / 2 * this._w, (1 - v.y) / 2 * this._h];
  }

  _paintLabels() {
    if (this.api.labels === false) {                  // a label-free view
      for (const el of this.labels) el.style.display = "none";
      return;
    }
    // api.labelIds() -> Set: label only these (membrane home: what is
    // tracked and what it touches, what is active)
    const only = this.api.labelIds ? this.api.labelIds() : null;
    const cands = [];
    for (const [id, s] of this.slots) {
      if (!this.sizeA[s]) continue;
      if (only && !only.has(id)) continue;
      const m = this.meta[s];
      const p = [this.posA[s * 3], this.posA[s * 3 + 1], this.posA[s * 3 + 2]];
      const sc = this._project(p);
      if (!sc || sc[0] < 0 || sc[1] < 0 || sc[0] > this._w || sc[1] > this._h) continue;
      const d = len3(sub3(p, this._camVecs().pos));
      // THE TINY LABEL (2026-10-15): a fresh change stashes `act` -- show
      // what/why changed as a one-line suffix, still 11px + ellipsis.
      let txt = m.label;
      if (m.act) {
        if (performance.now() - m.act.t0 < 6000) {
          txt = m.label + " · " + m.act.kind + (m.act.brief ? " " + m.act.brief : "");
        } else {
          m.act = null;
        }
      }
      cands.push({ id, txt, x: sc[0], y: sc[1], w: (m.mass + 5) / (d * d) + (id === this.sel ? 1e9 : 0) });
    }
    cands.sort((a, b) => b.w - a.w);
    const shown = [], boxes = [];
    for (const c of cands) {
      if (shown.length >= 90) break;
      const bw = Math.min(200, c.txt.length * 6.5 + 6), bx = c.x + 8, by = c.y - 8;
      if (boxes.some((b) => bx < b[0] + b[2] && bx + bw > b[0] && by < b[1] + 16 && by + 16 > b[1])) continue;
      boxes.push([bx, by, bw]); shown.push([c, bx, by]);
    }
    while (this.labels.length < shown.length) {
      const el = document.createElement("div");
      el.style.cssText = "position:absolute;font:11px system-ui,sans-serif;color:rgba(235,240,250,.92);text-shadow:0 1px 2px #000;white-space:nowrap;max-width:200px;overflow:hidden;text-overflow:ellipsis";
      this.labelLayer.appendChild(el); this.labels.push(el);
    }
    this.labels.forEach((el, i) => {
      const it = shown[i];
      if (!it) { el.style.display = "none"; return; }
      el.style.display = ""; el.textContent = it[0].txt;
      el.style.transform = "translate(" + it[1].toFixed(0) + "px," + it[2].toFixed(0) + "px)";
      el.style.color = it[0].id === this.sel ? "#fff" : "";
      el.style.fontWeight = it[0].id === this.sel ? "600" : "";
    });
  }

  // ---------------------------------------------------------- picking
  _pick(x, y) {
    let best = null, bd = 16 * 16;
    for (const [id, s] of this.slots) {
      if (!this.sizeA[s]) continue;
      const sc = this._project([this.posA[s * 3], this.posA[s * 3 + 1], this.posA[s * 3 + 2]]);
      if (!sc) continue;
      const d = (sc[0] - x) ** 2 + (sc[1] - y) ** 2;
      if (d < bd) { bd = d; best = s; }
    }
    return best == null ? null : this.meta[best];
  }

  select(id) {
    const old = this.sel; this.sel = id;
    for (const k of [old, id]) { const s = this.slots.get(k); if (s != null) this._paintSlot(s); }
    this._dirtyPoints();
    this._edgesDirty = true;
    if (!id) { this.selNb = null; return; }
    if (this.selNb && this.selNb.id === id) return;
    // the whole neighbourhood (loaded or not): the seat knows every edge
    const pid = this.pid;
    this.api.get("/membrane/map/locate?pid=" + pid + "&eid=" + encodeURIComponent(id) + "&nb=1").then((hit) => {
      if (this.sel !== id || this.pid !== pid || !hit) return;
      this.selNb = { id, p: hit.p, nb: hit.neighbours || [] };
      this._edgesDirty = true;
      if (this.api.onRelated) this.api.onRelated(id);
    }).catch(() => {});
  }

  /** The selected entity's relations among the loaded tiles. */
  related(id) {
    if (this.selNb && this.selNb.id === id) {
      return this.selNb.nb.map((x) => ({ id: x.id, label: x.l, rel: x.rel, n: x.n, dir: x.dir,
        loaded: this.slots.has(x.id) }));
    }
    const out = [], seen = new Set();
    for (const t of this.cache.tiles.values()) {
      for (const e of t.edges || []) {
        if (e.s !== id && e.t !== id) continue;
        const other = e.s === id ? e.t : e.s, key = other + "|" + e.rel + "|" + (e.s === id ? "out" : "in");
        if (seen.has(key)) continue;
        seen.add(key);
        const s = this.slots.get(other), m = s == null ? null : this.meta[s];
        out.push({ id: other, label: m ? m.label : other, rel: e.rel, n: e.n, dir: e.s === id ? "out" : "in" });
      }
    }
    return out.sort((a, b) => b.n - a.n);
  }

  async flyTo(id) {
    let p = null;
    const s = this.slots.get(id);
    if (s != null) p = [this.posA[s * 3], this.posA[s * 3 + 1], this.posA[s * 3 + 2]];
    else { const hit = await this._locate(id); if (hit) p = cubeToWorld(hit.p); }
    if (!p) return false;
    this._flyAnim = { from: this.orbit.target.slice(), to: p, r0: this.orbit.r, r1: Math.min(this.orbit.r, 260), t0: performance.now() };
    const step = () => {
      const a = this._flyAnim; if (!a) return;
      const u = Math.min(1, (performance.now() - a.t0) / 700), e = u * u * (3 - 2 * u);
      for (let i = 0; i < 3; i++) this.orbit.target[i] = a.from[i] + (a.to[i] - a.from[i]) * e;
      this.orbit.r = a.r0 + (a.r1 - a.r0) * e;
      if (u < 1) requestAnimationFrame(step); else this._flyAnim = null;
    };
    requestAnimationFrame(step);
    this.select(id);
    return true;
  }

  search(q) {
    q = String(q || "").trim().toLowerCase();
    if (!q) return [];
    const out = [];
    for (const [id, s] of this.slots) {
      const m = this.meta[s];
      if ((m.label || "").toLowerCase().includes(q) || id.toLowerCase().includes(q)) out.push(m);
      if (out.length >= 40) break;
    }
    return out.sort((a, b) => b.mass - a.mass);
  }

  /** THE WHOLE-CATALOG SEARCH (2026-10-13, the operator: "still search
   * thru all entities when using the search box"): the rendered answer
   * comes first (search(), instant, mass-ranked), then the seat's full
   * entity store fills the rest (membrane/map/search). A hit needn't be
   * on the field -- flyTo locates and flies to it. */
  async searchAll(q) {
    const local = this.search(q);
    let remote = [];
    try {
      const d = await this.api.get("/membrane/map/search?limit=40&q=" +
        encodeURIComponent(String(q || "").trim()));
      remote = (d && d.hits) || [];
    } catch (e) { remote = []; }         // offline/degraded: rendered is the answer
    const seen = new Set(local.map((m) => m.id));
    for (const h of remote) {
      if (!h || !h.id || seen.has(h.id)) continue;
      seen.add(h.id); local.push(h);
    }
    return local.slice(0, 40);
  }

  async _locate(eid) {
    if (this.locateCache.has(eid)) return this.locateCache.get(eid);
    let hit = null;
    for (const cand of locateCandidates(eid)) {
      try { hit = await this.api.get("/membrane/map/locate?pid=" + this.pid + "&eid=" + encodeURIComponent(cand)); break; }
      catch (e) { hit = null; }
    }
    this.locateCache.set(eid, hit);
    if (this.locateCache.size > LOCATE_CACHE_MAX) this.locateCache.delete(this.locateCache.keys().next().value);
    return hit;
  }

  // ---------------------------------------------------------- controls
  _wire() {
    const el = this.renderer.domElement;
    const pts = new Map();
    let last = null, moved = false, pinch = null, mode = "rot";
    el.addEventListener("contextmenu", (e) => e.preventDefault());
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.offsetX, e.offsetY]);
      last = [e.offsetX, e.offsetY]; moved = false;
      mode = (e.button === 2 || e.shiftKey) ? "pan" : "rot";
      if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), r: this.orbit.r, c: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] }; }
    });
    el.addEventListener("pointermove", (e) => {
      if (!pts.has(e.pointerId)) { this._hover(e.offsetX, e.offsetY); return; }
      pts.set(e.pointerId, [e.offsetX, e.offsetY]);
      if (pts.size === 2 && pinch) {
        const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        this.orbit.r = Math.max(30, Math.min(6000, pinch.r * pinch.d / Math.max(1, d)));
        const c = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        this._pan(c[0] - pinch.c[0], c[1] - pinch.c[1]); pinch.c = c; moved = true; return;
      }
      const dx = e.offsetX - last[0], dy = e.offsetY - last[1];
      if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
      if (mode === "pan") this._pan(dx, dy);
      else { this.orbit.theta -= dx * 0.006; this.orbit.phi = Math.max(0.05, Math.min(Math.PI - 0.05, this.orbit.phi - dy * 0.006)); }
      last = [e.offsetX, e.offsetY];
    });
    const up = (e) => {
      pts.delete(e.pointerId); if (pts.size < 2) pinch = null;
      if (!moved && e.type === "pointerup" && e.button !== 2) {
        // A MIND'S ORB first (2026-10-11): selecting one hands it to the page
        // (membrane home: the chat shows that mind, the camera follows it);
        // tapping it again, or empty space, lets go
        const av = this.pickAvatar(e.offsetX, e.offsetY);
        if (av) {
          this.selAgent = this.selAgent === av.agent ? null : av.agent;
          if (this.api.onAvatar) this.api.onAvatar(this.selAgent);
          return;
        }
        const m = this._pick(e.offsetX, e.offsetY);
        if (this.selAgent) { this.selAgent = null; if (this.api.onAvatar) this.api.onAvatar(null); }
        if (m) { this.select(m.id); this.api.onSelect(m); } else { this.select(null); this.api.onSelect(null); }
      }
    };
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
    el.addEventListener("wheel", (e) => { e.preventDefault(); this.orbit.r = Math.max(30, Math.min(6000, this.orbit.r * Math.exp(e.deltaY * 0.0012))); }, { passive: false });
    el.addEventListener("dblclick", (e) => { const m = this._pick(e.offsetX, e.offsetY); if (m) { this.flyTo(m.id); this.api.onSelect(m); } });
    el.addEventListener("pointerleave", () => this.api.onHover(null));
  }

  _pan(dx, dy) {
    const cv = this._camVecs(), f = cv.fwd;
    const right = [f[2], 0, -f[0]], rl = len3(right) || 1;
    const r = [right[0] / rl, 0, right[2] / rl];
    const upv = [f[1] * r[2] - f[2] * r[1], f[2] * r[0] - f[0] * r[2], f[0] * r[1] - f[1] * r[0]];
    const k = this.orbit.r * 0.0016;
    for (let i = 0; i < 3; i++) this.orbit.target[i] += (-dx * r[i] + dy * upv[i]) * k;
  }

  _hover(x, y) {
    const now = performance.now();
    if (now - (this._lastHover || 0) < 60) return;
    this._lastHover = now;
    this.api.onHover(this._pick(x, y), x, y);
  }

  _resize() {
    const r = this.host.getBoundingClientRect();
    this._w = Math.max(1, r.width); this._h = Math.max(1, r.height);
    this.renderer.setSize(this._w, this._h, false);
    this.camera.aspect = this._w / this._h; this.camera.updateProjectionMatrix();
    this.pointMat.uniforms.uScale.value = this._h / (2 * Math.tan(this.camera.fov * Math.PI / 360));
  }

  // ---------------------------------------------------------- the live layer
  async _live() {
    let backoff = 1000;
    while (this._alive) {
      try {
        this._streamAbort = new AbortController();
        const res = await this.api.stream("/event", this._streamAbort.signal);
        if (!res.ok || !res.body) throw new Error("stream " + res.status);
        this.live = true; backoff = 1000;
        const reader = res.body.getReader(), dec = new TextDecoder();
        let buf = "";
        while (this._alive) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const out = parseSSE(buf); buf = out.rest;
          for (const f of out.frames) this._onFrame(f);
        }
      } catch (e) { /* reconnect */ }
      this.live = false;
      if (!this._alive) return;
      await new Promise((r) => setTimeout(r, backoff));
      backoff = Math.min(30000, backoff * 2);
    }
  }

  /** THE FIELD GREW (2026-10-12, the operator: "Bob says there's 200 live entities,
   *  but my viewer is showing 0 out of 0"): the projection loads once; a page opened
   *  on a young field never saw what was born after. Project afresh -- debounced;
   *  at most every 20 s on a small field, every 5 min on a big one (a projection
   *  costs the seat seconds there). */
  _regrow(why) {
    if (this._regrowT || !this._alive) return;
    const gap = ((this.info || {}).n_total || 0) < 500 ? 20000 : 300000;
    const wait = Math.max(2000, gap - (Date.now() - (this._regrownAt || 0)));
    this._regrowT = setTimeout(() => {
      this._regrowT = 0; this._regrownAt = Date.now(); this._unknownBorn = 0;
      if (this._alive) this.load().catch(() => {});
    }, wait);
  }

  _onFrame(f) {
    const t = f && f.type, p = (f && f.properties) || {};
    if (!t) return;
    const now = performance.now();
    if (t === "field.moved" && p.pid === this.pid) {
      for (const mv of p.moves || []) {
        const s = this.slots.get(mv[0]);
        if (s == null) continue;
        const to = cubeToWorld([mv[1], mv[2], mv[3]]);
        this.tweens.set(s, { from: [this.posA[s * 3], this.posA[s * 3 + 1], this.posA[s * 3 + 2]], to, t0: now, dur: 1500 });
        if (this.meta[s]) this.meta[s].p = [mv[1], mv[2], mv[3]];
      }
    } else if (t === "field.applied") {
      const hits = this.recentHits || (this.recentHits = []);
      for (const id of p.eids || []) {
        const s = this.slots.get(id);
        if (s != null) { this.pulses.set(s, now); hits.push([s, now]); }
      }
      if (hits.length > 600) hits.splice(0, hits.length - 600);
      const pairs = edgePulsePairs(p.groups, (a, b) => this.edgeSet.has(a + "|" + b));
      for (const [a, b, st] of pairs) {
        if (this.edgePulses.length >= this.pulseCap) break;
        this.edgePulses.push({ a, b, t0: now + Math.random() * 300, dur: 900, col: PULSE_COLORS[st] || [1, 1, 1] });
      }
    } else if (t === "field.changed") {
      for (const c of p.changes || []) {
        const s = this.slots.get(c.eid);
        if (s == null) continue;
        this.pulses.set(s, now);
        const m = this.meta[s];
        if (m) m.act = { kind: c.kind || "", brief: c.brief || "", t0: now };
      }
    } else if (t === "self.cycle" && p.pid === this.pid) {
      this._selfCycle(p, now);
    } else if (t === "field.born") {
      for (const id of p.eids || []) {
        const s = this.slots.get(id);
        if (s != null) this.pulses.set(s, now);
        else this._unknownBorn = (this._unknownBorn || 0) + 1;
      }
      // enough new ones to matter (an empty field: the first; a grown one: 5%)
      const total = (this.info || {}).n_total || 0;
      if ((this._unknownBorn || 0) >= Math.max(1, Math.ceil(total * 0.05))) this._regrow("born");
    } else if (t === "field.watch") {
      for (const [id] of p.fires || []) { const s = this.slots.get(id); if (s != null) this._ring(s); }
    } else if (t === "mind.focus") {
      if (p.phase === "end") { const a = this.avatars.get(p.agent); if (a) a.release(); }
      else { this._attach(p.agent, p.eid, null); if (p.sid) { this.agentSids = this.agentSids || {}; this.agentSids[p.agent] = p.sid; if (this.selAgent === p.agent && this.api.onAvatar) this.api.onAvatar(p.agent); } }
    } else if (t === "mind.think") {
      // THE WAIT, SHOWN (2026-10-11, the operator: "sometimes it's just...
      // paused"): a model round of this mind is generating -- its orb
      // breathes and circles where it is until the answer arrives
      const a = this._avatar(p.agent || "chat");
      a.thinking = p.phase === "start" ? Date.now() : 0;
    } else if (t === "mind.touch") {
      this._attach(p.agent || "chat", p.eid, 4000);
    } else if (t === "mind.thinker") {
      if (p.eid) this._attach("thinker", p.eid, 3000);
    } else if (t === "mind.dreamer") {
      if (p.goal) this._attach("dreamer", p.goal, 6000);
    }
  }

  /** The orb under a screen point (within AVATAR_HIT_PX), nearest first. */
  pickAvatar(x, y) {
    let best = null, bd = AVATAR_HIT_PX;
    for (const a of this.avatars.values()) {
      const sc = this._project(a.pos);
      if (!sc) continue;
      const d = Math.hypot(sc[0] - x, sc[1] - y);
      if (d < bd) { bd = d; best = a; }
    }
    return best;
  }

  /** Where self (#161) is now (world coordinates), or null before its first cycle. */
  selfPos() {
    const a = this.amoeba;
    return a && a.pts ? a.pos.slice() : null;
  }

  /** Where self has been (world coordinates, oldest first) -- the chase cam's way back. */
  selfTrail() {
    const a = this.amoeba;
    return a && a.trail ? a.trail.map((q) => q.slice()) : [];
  }

  /** Where a mind's orb is now (world coordinates), or null. */
  avatarPos(agent) {
    const a = this.avatars.get(agent);
    return a ? a.pos.slice() : null;
  }

  /** THE SELF LOOP (#161) -- an amoeba in the field's space. Each cycle the seat
   *  sends five placed points: where it started (from), where it MEANT TO GO
   *  (its own step), where it EXPECTED to end up (its step + the push it has
   *  learned), where it ENDED UP (the world's current pushed it), and where it
   *  has learned THE CURRENT PULLS. Green: its own push; orange: the world's;
   *  red: the gap (what it did not see coming); violet: its model of the current. */
  _selfCycle(p, now) {
    const THREE = this.THREE, pts = p.points || {};
    if (!pts.at || !pts.from) return;
    let a = this.amoeba;
    if (!a) {
      const c = document.createElement("canvas"); c.width = c.height = 64;
      const g = c.getContext("2d"), grd = g.createRadialGradient(32, 32, 2, 32, 32, 30);
      grd.addColorStop(0, "#fff"); grd.addColorStop(0.3, "#7cf0c8"); grd.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = grd; g.fillRect(0, 0, 64, 64);
      const oc = document.createElement("canvas"); oc.width = oc.height = 64;
      const og = oc.getContext("2d"), ogrd = og.createRadialGradient(32, 32, 2, 32, 32, 30);
      ogrd.addColorStop(0, "#fff"); ogrd.addColorStop(0.3, "#ffd27a"); ogrd.addColorStop(1, "rgba(0,0,0,0)");
      og.fillStyle = ogrd; og.fillRect(0, 0, 64, 64);
      const line = (color, opacity) => new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
      a = this.amoeba = {
        pos: cubeToWorld(pts.from), tw: null, trail: [], last: p,
        sprite: new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false })),
        own: line(0x5ad17a, 0.9), world: line(0xffa94d, 0.9), gap: line(0xff5d5d, 0.9), pull: line(0xb48cff, 0.35),
        aimLine: line(0xffffff, 0.25),
        beam: line(0xffd27a, 0.9),
        other: new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(oc), transparent: true, depthWrite: false })),
        trailLine: new THREE.Line(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(32 * 3), 3)),
          new THREE.LineBasicMaterial({ color: 0x7cf0c8, transparent: true, opacity: 0.3 })),
        tag: document.createElement("div"),
      };
      a.sprite.scale.set(40, 40, 1);
      a.other.scale.set(30, 30, 1);
      a.other.position.set(F3_WORLD * 0.46, F3_WORLD * 0.30, F3_WORLD * 0.46);   // the other sits at the field's edge
      a.other.visible = false;
      a.tag.style.cssText = SELF_LABEL_CSS + "border-left-color:#2ec4a0;display:none";
      a.tag.title = (this.selfName || "Blurp") + " (#161): white ring = where it wants to be (its aim, once it steers) · green = where it pushes itself to · "
        + "orange = where the current took it · red = the gap from where it expected to end up · violet = where it has learned the current pulls";
      this.labelLayer.appendChild(a.tag);
      // THE MARKERS (the operator, twice: "what are the squary things?"): round
      // and labelled, each hidden until it means something (no pull before a
      // model of the current, no aim before it steers)
      const marker = (color, ring) => {
        const mc = document.createElement("canvas"); mc.width = mc.height = 32;
        const mg = mc.getContext("2d");
        mg.beginPath(); mg.arc(16, 16, ring ? 12 : 9, 0, 2 * Math.PI);
        if (ring) { mg.lineWidth = 4; mg.strokeStyle = color; mg.stroke(); } else { mg.fillStyle = color; mg.fill(); }
        const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(mc), transparent: true,
                                                                depthWrite: false, sizeAttenuation: false }));
        sp.scale.set(ring ? 0.05 : 0.03, ring ? 0.05 : 0.03, 1);
        return sp;
      };
      a.marks = [
        { key: "aim", text: "wants", color: "#ffffff", ring: true },
        { key: "intent", text: "pushes to", color: "#5ad17a" },
        { key: "expected", text: "expects", color: "#ff5d5d" },
        { key: "pull", text: "pull", color: "#b48cff" },
      ].map((m) => {
        const lab = document.createElement("div");
        lab.style.cssText = SELF_LABEL_CSS + "font-weight:500;font-size:10px;opacity:.9;display:none;border-left-color:"
          + (m.color === "#ffffff" ? "#9aa4b2" : m.color);
        lab.textContent = m.text;
        this.labelLayer.appendChild(lab);
        const sp = marker(m.color, m.ring);
        this.scene.add(sp);
        return Object.assign({}, m, { sp, lab });
      });
      this.scene.add(a.sprite, a.own, a.world, a.gap, a.pull, a.aimLine, a.trailLine, a.beam, a.other);
      // #257: THE BODY ACTS IT OUT -- the live acts, the blended pose (reused),
      // a pseudopod of a few lobes (the body's own texture) and a gold halo
      a.acts = []; a.pose = calmPose(); a.shape = { a: 1, b: 1, rot: 0 };
      a.lobes = [0.8, 0.68, 0.57, 0.47, 0.38].map((sz, i) => {
        const l = new THREE.Sprite(a.sprite.material.clone());
        l.userData.sz = sz; l.userData.t = [0.2, 0.4, 0.6, 0.8, 1][i]; l.visible = false;
        this.scene.add(l);
        return l;
      });
      const hc = document.createElement("canvas"); hc.width = hc.height = 64;
      const hg = hc.getContext("2d"), hgrd = hg.createRadialGradient(32, 32, 4, 32, 32, 31);
      hgrd.addColorStop(0, "rgba(255,226,140,0.95)"); hgrd.addColorStop(0.45, "rgba(240,190,60,0.55)"); hgrd.addColorStop(1, "rgba(212,160,23,0)");
      hg.fillStyle = hgrd; hg.fillRect(0, 0, 64, 64);
      a.halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(hc), transparent: true, depthWrite: false }));
      a.halo.renderOrder = -1; a.halo.visible = false;
      this.scene.add(a.halo);
      this._pv = new THREE.Vector3(); this._iq = new THREE.Quaternion(); this._fo = [0, 0, 0];
    }
    const W = (q) => (q ? cubeToWorld(q) : null);
    a.pts = { from: W(pts.from), intent: W(pts.intent), expected: W(pts.expected), at: W(pts.at), pull: W(pts.pull), aim: W(pts.aim) };
    a.tw = { from: a.pos.slice(), to: a.pts.at, t0: now, dur: 1500 };
    a.trail.push(a.pts.at); if (a.trail.length > 32) a.trail.shift();
    a.last = p; a.t0 = now;
    const meets0 = p.meets || {};                  // ages -> this viewer's clock: when the push/answer landed
    a.pushAt = meets0.push_age != null ? now - meets0.push_age * 1000 : null;
    a.heardAt = meets0.heard_age != null ? now - meets0.heard_age * 1000 : null;
    // #257: the move, acted out -- a stretch along its own step (from -> intent),
    // scaled by the stride (the frame's push), a lean toward its aim when it steers
    const P = a.pts, sd = unit3(P.from, P.intent);
    const stride = p.push != null ? Number(p.push) : (P.intent ? Math.hypot(P.intent[0] - P.from[0], P.intent[1] - P.from[1], P.intent[2] - P.from[2]) / F3_WORLD : 0);
    for (const act of amoebaActs({ kind: "cycle", act: "move", step: stride, steer: !!P.aim })) {
      if (!sd) break;
      act.t0 = now; act.dir = sd; act.aimDir = P.aim ? unit3(P.at, P.aim) : null;
      addAct(a.acts, act);
    }
  }

  /** #257: a record line, acted out by the body (home.js feeds the fresh ones).
   *  `at`: where the thing it is about sits (a look's, a shift's), or null --
   *  a look with no place reaches toward its ghost (#244); a push and a reply
   *  check go toward the other. A move comes from the cycle frame, not here. */
  selfActs(r, at) {
    const a = this.amoeba;
    if (!a || !a.pts || !a.acts) return;
    const now = performance.now();
    for (const act of amoebaActs(r)) {
      const k = act.kind;
      if (k === "move") continue;
      let to = null;
      if (k === "look" || k === "surprise" || k === "toy") to = lookTarget(at, r.eid, a.pos).p;
      else if (k === "push" || k === "listen" || k === "bounce" || k === "droop") {
        to = this.selfOther && this.selfOther.p ? this.selfOther.p : a.other ? a.other.position.toArray() : null;
      }
      act.t0 = now + (act.delay || 0);             // a jolt / a wiggle: when the answer lands
      act.dir = to ? unit3(a.pos, to) : null;
      if (k === "look") {
        if (!act.dir) continue;
        act.reach = Math.min(1, Math.hypot(to[0] - a.pos[0], to[1] - a.pos[1], to[2] - a.pos[2]) / POD_LEN);
      }
      addAct(a.acts, act);
    }
  }

  /** #257: a world vector -> this._fo (camera space, unit, faceOn'd); returns its length.
   *  Expects this._iq = the camera's inverse rotation (set once per frame). */
  _faceCam(x, y, z) {
    const n = Math.hypot(x, y, z), fo = this._fo;
    if (n < 1e-6) { fo[0] = fo[1] = fo[2] = 0; return 0; }
    this._pv.set(x / n, y / n, z / n).applyQuaternion(this._iq);
    faceOn(this._pv.x, this._pv.y, this._pv.z, FACE_MIN, fo);
    return n;
  }

  /** #257: this._fo x n back to world space, into this._pv. */
  _faceWorld(n) { const fo = this._fo; return this._pv.set(fo[0] * n, fo[1] * n, fo[2] * n).applyQuaternion(this.camera.quaternion); }

  _stepSelf(now) {
    const a = this.amoeba;
    if (!a || !a.pts) return;
    if (a.tw) {
      const u = Math.min(1, (now - a.tw.t0) / a.tw.dur), e = u * u * (3 - 2 * u);
      a.pos = a.tw.from.map((v, i) => v + (a.tw.to[i] - v) * e);
      if (u >= 1) a.tw = null;
    }
    const rm = this.reducedMotion();
    // #257: the body acts out what self is doing -- the live acts blended into
    // one pose (calm when nothing is going on); a.pos itself never moves for
    // it, so the marks, the trail and the camera's framing stay where self is
    pruneActs(a.acts, now);
    const pose = posesAt(a.acts, now, rm, a.pose);
    const show = !this.selfEyes;                   // 👁 self's eyes (#237): the camera IS the amoeba
    const wob = rm ? 1 : 1 + (0.08 * Math.sin(now / 260) + 0.05 * Math.sin(now / 97)) * (1 - pose.still);   // it is alive
    const BR = 20;                                 // the body's radius in world units (the sprite is 40)
    // every motion's direction is seen from the camera and kept partly in the
    // screen plane (faceOn): ◎ self chases from behind, so a step or a look
    // points into the screen and would otherwise vanish end-on
    this._iq.copy(this.camera.quaternion).invert();
    const v = this._pv, fo = this._fo;
    this._faceWorld(this._faceCam(pose.ox, pose.oy, pose.oz));
    const bx = a.pos[0] + v.x * BR, by = a.pos[1] + v.y * BR, bz = a.pos[2] + v.z * BR;
    a.sprite.position.set(bx, by, bz);
    // the stretch on the screen: its direction and length there
    const sn = this._faceCam(pose.sx, pose.sy, pose.sz), sl = Math.hypot(fo[0], fo[1]);
    const sh = bodyShape(sl > 1e-6 ? fo[0] / sl : 0, sl > 1e-6 ? fo[1] / sl : 0, sn * sl, pose.squash, a.shape);
    const size = 40 * (1 + pose.puff);
    a.sprite.scale.set(size * sh.a * wob, size * sh.b / wob, 1);
    a.sprite.material.rotation = sh.rot;
    a.sprite.visible = show;
    a.sprite.material.opacity = ((this.brain && this.brain.dimUntil > now) ? 0.3 : 1) * (1 - pose.dim);   // a wait: it dims
    // the pseudopod: lobes out along the look, shrinking toward the tip
    const podOn = show && pose.reach > 0.03;
    if (podOn) this._faceWorld(this._faceCam(pose.px, pose.py, pose.pz));   // v: the pod (direction x reach), faced
    for (const l of a.lobes) {
      l.visible = podOn;
      if (!podOn) continue;
      const d = POD_LEN * l.userData.t;
      l.position.set(bx + v.x * d, by + v.y * d, bz + v.z * d);
      const ls = 40 * l.userData.sz * (0.55 + 0.45 * pose.reach);
      l.scale.set(ls, ls, 1);
      l.material.opacity = a.sprite.material.opacity * Math.min(1, 0.4 + pose.reach);
    }
    // a level-up: the whole body glows (the gold ring is _stepBrain's)
    a.halo.visible = show && pose.glow > 0.01;
    if (a.halo.visible) {
      a.halo.position.set(bx, by, bz);
      const hs = size * (1.7 + 0.6 * pose.glow);
      a.halo.scale.set(hs, hs, 1);
      a.halo.material.opacity = 0.85 * pose.glow;
    }
    const seg = (ln, p, q) => {
      ln.visible = !!(p && q);
      if (!ln.visible) return;
      const g = ln.geometry.attributes.position;
      g.setXYZ(0, p[0], p[1], p[2]); g.setXYZ(1, q[0], q[1], q[2]); g.needsUpdate = true;
    };
    const P = a.pts;
    seg(a.own, P.from, P.intent);          // where it meant to go
    seg(a.world, P.intent, P.at);          // where it ended up: the world's push
    seg(a.gap, P.expected, P.at);          // what it did not see coming
    seg(a.pull, P.at, P.pull);             // where it has learned the current pulls
    const L = a.last || {};
    a.gap.material.opacity = 0.35 + 0.65 * Math.min(1, (L.gap || 0) / Math.max(1e-9, L.naive || 1e-9));
    seg(a.aimLine, a.pos, P.aim);          // how far it is from what it wants
    // the exchange (rung 6): on a push self beams out to the other (the amber
    // orb at the field's edge); when the other answers, the orb flashes back
    const pushed = a.pushAt != null && now - a.pushAt < 1400;
    const answered = a.heardAt != null && now - a.heardAt < 1400;
    if (a.other) {
      // THE PERSON SELF MEETS (#237): a labelled presence wherever their
      // chat:in sits on the field (the field's edge until it is placed);
      // a push beams out to them, their reply flashes back to self
      const who = this.selfOther;
      if (who && who.p) a.other.position.set(who.p[0], who.p[1], who.p[2]);
      a.other.visible = pushed || answered || !!who;
      if (a.other.visible) {
        const s = rm ? (pushed || answered ? 1.3 : 1)
          : 1 + (answered ? 0.8 * Math.sin(now / 110) : pushed ? 0.2 * Math.sin(now / 260) : 0);
        a.other.scale.set(30 * s, 30 * s, 1);
        a.other.material.opacity = answered ? (rm ? 1 : 0.5 + 0.5 * Math.abs(Math.sin(now / 110))) : pushed ? 0.9 : 0.55;
      }
      seg(a.beam, a.pos, a.other.position.toArray());
      a.beam.visible = pushed || answered;           // out on a push, back on the reply
      a.beam.material.opacity = answered ? (rm ? 0.8 : 0.35 + 0.6 * Math.abs(Math.sin(now / 90))) : 0.9;
      a.beam.material.color.setHex(answered ? 0x7cf0c8 : 0xffd27a);
    }
    for (const m of a.marks) {
      const q = P[m.key];
      m.sp.visible = !!q;
      if (q) m.sp.position.set(q[0], q[1], q[2]);
    }
    const tp = a.trailLine.geometry.attributes.position;
    for (let i = 0; i < 32; i++) { const q = a.trail[Math.max(0, a.trail.length - 32 + i)] || a.pos; tp.setXYZ(i, q[0], q[1], q[2]); }
    tp.needsUpdate = true;
    // the tag's and the marks' labels are placed by _layoutSelfLabels (no overlaps,
    // inside the visible field); here only what the tag says
    a.tagText = selfTag(L, !!this.selfFollow, this.selfName);
  }

  // ---------------------------------------------------------- #237: THE BRAIN ON THE SCREEN
  // What self looks at now (a beam + a label with its answer), the trail of
  // its last BRAIN_TRAIL looks (fading with age), a pulse ring where rung 5
  // noticed a shift, a dim on a wait, a gold ring on a level-up. home.js
  // feeds it from self's record (read-only); positions are where the field
  // already places a thing (its drawn point, else the seat's locate) -- a
  // thing with neither gets its label beside the amoeba, never a made-up spot.
  // Capped: BRAIN_TRAIL dots + paths, BRAIN_RINGS rings, three labels.

  /** prefers-reduced-motion: no pulses, no glides -- static marks instead. */
  reducedMotion() {
    if (this._rmq === undefined) {
      this._rmq = (typeof window !== "undefined" && window.matchMedia) ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    }
    return !!(this._rmq && this._rmq.matches);
  }

  /** Where a thing sits on the field now (world coordinates), or null. */
  async placeOf(eid) {
    if (!eid) return null;
    const s = this.slots.get(eid);
    if (s != null && this.sizeA[s]) return [this.posA[s * 3], this.posA[s * 3 + 1], this.posA[s * 3 + 2]];
    if (!this.pid) return null;
    const hit = await this._locate(eid);
    return hit && hit.p ? cubeToWorld(hit.p) : null;
  }

  _brainObjs() {
    if (this.brain && this.brain.beam) return this.brain;
    const b = this.brain = this.brain || { looks: [], rings: [] };
    const THREE = this.THREE;
    if (!THREE || !this.scene) return b;
    const line = (color) => new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9, depthWrite: false }));
    const dc = document.createElement("canvas"); dc.width = dc.height = 32;
    const dg = dc.getContext("2d"); dg.beginPath(); dg.arc(16, 16, 10, 0, 2 * Math.PI); dg.lineWidth = 4; dg.strokeStyle = "#fff"; dg.stroke();
    const dotTex = new THREE.CanvasTexture(dc);
    b.beam = line(0xffe08a); b.beam.renderOrder = 3; b.beam.visible = false;
    // #244: the ghost -- a dashed beam and a hollow dashed ring (lookTarget)
    b.ghostBeam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color: BRAIN_GHOST, dashSize: 6, gapSize: 5, transparent: true, opacity: 0.85, depthWrite: false }));
    b.ghostBeam.renderOrder = 3; b.ghostBeam.visible = false;
    const gc = document.createElement("canvas"); gc.width = gc.height = 64;
    const gg = gc.getContext("2d"); gg.setLineDash([7, 5]); gg.lineWidth = 4; gg.strokeStyle = "#fff";
    gg.beginPath(); gg.arc(32, 32, 24, 0, 2 * Math.PI); gg.stroke();
    b.ghost = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(gc), color: BRAIN_GHOST, transparent: true,
      depthWrite: false, sizeAttenuation: false }));
    b.ghost.scale.set(0.05, 0.05, 1); b.ghost.visible = false; b.ghost.renderOrder = 3;
    this.scene.add(b.ghostBeam, b.ghost);
    b.dots = []; b.paths = [];
    for (let i = 0; i < BRAIN_TRAIL; i++) {
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: dotTex, transparent: true, depthWrite: false, sizeAttenuation: false }));
      sp.scale.set(0.035, 0.035, 1); sp.visible = false;
      b.dots.push(sp);
      const pl = line(0xf0a830); pl.visible = false; b.paths.push(pl);
      this.scene.add(sp, pl);
    }
    this.scene.add(b.beam);
    const lab = (accent, css) => {
      const el = document.createElement("div");
      el.style.cssText = SELF_LABEL_CSS + "display:none;border-left-color:" + accent + ";" + (css || "");
      this.labelLayer.appendChild(el);
      return el;
    };
    b.lookLab = lab("#e0b030", "font-size:12px");
    b.ringLab = lab("#ff9a3c");
    b.otherLab = lab("#ffb347", "font-size:12px");
    b.trailDirty = true;
    return b;
  }

  /** self looked at a thing (p: where it sits, or null when the field has no place for it). */
  selfLook(eid, p, label, answer) {
    const b = this._brainObjs();
    b.looks.push({ eid, p: p ? p.slice() : null, label: label || eid, answer, t0: performance.now() });
    if (b.looks.length > BRAIN_TRAIL) b.looks.shift();
    b.trailDirty = true;
  }

  /** The look self has on now (for 👁 self's eyes): {eid, p, age} or null. */
  selfLookNow() {
    const b = this.brain, l = b && b.looks[b.looks.length - 1];
    return l ? { eid: l.eid, p: l.p, age: performance.now() - l.t0 } : null;
  }

  /** The points of the trail of looks that have a place (◎ self frames them). */
  selfLookPoints() { return ((this.brain && this.brain.looks) || []).map((l) => l.p).filter(Boolean); }

  /** self's aim (world coordinates), or null before it steers. */
  selfAim() { const a = this.amoeba; return a && a.pts && a.pts.aim ? a.pts.aim.slice() : null; }

  _brainRing(p, color, text, gold) {
    const b = this._brainObjs(), THREE = this.THREE;
    if (!THREE || !this.scene) return;
    while (b.rings.length >= BRAIN_RINGS) { const o = b.rings.shift(); this.scene.remove(o.m); o.m.geometry.dispose(); o.m.material.dispose(); }
    const m = new THREE.Mesh(new THREE.RingGeometry(0.82, 1, 48),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false }));
    m.renderOrder = 3;
    this.scene.add(m);
    b.rings.push({ m, p: p ? p.slice() : null, t0: performance.now(), text, gold });
  }

  /** Rung 5 noticed a shift: a ring pulses on the thing (round self when it has no place). */
  selfShift(eid, p, label) { this._brainRing(p, 0xffa94d, "noticed: " + (label || eid) + " changed its behaviour", false); }
  /** A wait: the amoeba dims briefly. */
  selfWait() { this._brainObjs().dimUntil = performance.now() + 2200; }
  /** A level-up: the exp bar's gold moment, echoed round the amoeba
   *  (extra: what it brought -- #267's "new toy: 🪞 mirror"). */
  selfLevelUp(rung, extra) { this._brainRing(null, 0xd4a017, "⬆ level up: rung " + rung + (extra ? " · " + extra : ""), true); }
  /** The person self meets: their label and their place (null: the field's edge). */
  setSelfOther(label, p) { this.selfOther = label ? { label, p: p ? p.slice() : null } : null; }

  _stepBrain(now) {
    const a = this.amoeba, b = this.brain;
    if (!b || !b.beam) return;
    const on = !!(a && a.pts), rm = this.reducedMotion();
    const cur = b.looks[b.looks.length - 1];
    const age = cur ? now - cur.t0 : Infinity;
    const LIVE = 6000;
    // the beam to what it looks at now: its field point, or its ghost (#244)
    const tgt = on && cur ? lookTarget(cur.p, cur.eid, a.pos) : { p: null, ghost: false };
    const beamOn = !!(on && cur && cur.p && age < LIVE);
    b.beam.visible = beamOn;
    const ghostOn = !!(tgt.ghost && age < LIVE);
    b.ghostBeam.visible = b.ghost.visible = ghostOn;
    if (ghostOn) {
      const g = b.ghostBeam.geometry.attributes.position;
      g.setXYZ(0, a.pos[0], a.pos[1], a.pos[2]); g.setXYZ(1, tgt.p[0], tgt.p[1], tgt.p[2]); g.needsUpdate = true;
      b.ghostBeam.computeLineDistances();
      b.ghost.position.set(tgt.p[0], tgt.p[1], tgt.p[2]);
      const gp = this._project(tgt.p);                        // like the rings: not drawn behind a panel
      b.ghost.visible = !!(gp && inArea(gp[0], gp[1], this._area));
      const fade = rm ? 0.85 : 0.85 * Math.min(1, (LIVE - age) / 1500);
      b.ghostBeam.material.opacity = fade; b.ghost.material.opacity = fade;
    }
    if (beamOn) {
      const g = b.beam.geometry.attributes.position;
      g.setXYZ(0, a.pos[0], a.pos[1], a.pos[2]); g.setXYZ(1, cur.p[0], cur.p[1], cur.p[2]); g.needsUpdate = true;
      b.beam.material.color.setHex(cur.answer === "changed" ? 0x7cf0c8 : cur.answer === "unchanged" ? 0xc8d0dc : 0xff8a5d);
      b.beam.material.opacity = rm ? 0.9 : 0.9 * Math.min(1, (LIVE - age) / 1500) * (age < 400 ? age / 400 : 1);
    }
    // the trail: a dot on each looked-at thing, the path between, oldest faint
    if (b.trailDirty) {
      b.trailDirty = false;
      const n = b.looks.length;
      for (let i = 0; i < BRAIN_TRAIL; i++) {
        const l = b.looks[i], prev = b.looks[i - 1];
        const dot = b.dots[i], pl = b.paths[i];
        dot.visible = !!(l && l.p);
        if (dot.visible) {
          dot.position.set(l.p[0], l.p[1], l.p[2]);
          dot.material.opacity = trailAlpha3(i, n);
          dot.material.color.setHex(l.answer === "changed" ? 0x7cf0c8 : l.answer === "unchanged" ? 0xc8d0dc : 0xff8a5d);
        }
        pl.visible = !!(l && l.p && prev && prev.p);
        if (pl.visible) {
          const g = pl.geometry.attributes.position;
          g.setXYZ(0, prev.p[0], prev.p[1], prev.p[2]); g.setXYZ(1, l.p[0], l.p[1], l.p[2]); g.needsUpdate = true;
          pl.material.opacity = 0.55 * trailAlpha3(i, n);
        }
      }
    }
    // the rings: a shift's pulse on the thing, a level-up's gold round self
    const HOLD = rm ? 7000 : 4500;
    b.rings = b.rings.filter((o) => {
      const u = (now - o.t0) / HOLD;
      if (u >= 1) { this.scene.remove(o.m); o.m.geometry.dispose(); o.m.material.dispose(); return false; }
      const p = o.p || (on ? a.pos : null);
      // clamped to the visible field: a ring behind the chat / 👶 panel is not
      // drawn there -- its label waits at the panel's edge with an arrow
      const sp = p ? this._project(p) : null;
      o.m.visible = !!(p && sp && inArea(sp[0], sp[1], this._area));
      if (!o.m.visible) return true;
      o.m.position.set(p[0], p[1], p[2]);
      o.m.quaternion.copy(this.camera.quaternion);
      if (rm) { o.m.scale.setScalar(o.gold ? 46 : 34); o.m.material.opacity = 0.85; }
      else {
        const w = (u * 3) % 1;                                   // three pulses
        o.m.scale.setScalar((o.gold ? 26 : 12) + 70 * w); o.m.material.opacity = 0.9 * (1 - w) * (1 - 0.4 * u);
      }
      return true;
    });
  }

  /** The visible field: the canvas minus the panels over it (api.occluders():
   *  the chat, the 👶 panel, the top bar -- re-read twice a second). */
  _fieldArea(now) {
    if (!this._area || now - (this._areaAt || 0) > 500) {
      this._areaAt = now;
      let rects = [];
      try { rects = this.api.occluders ? this.api.occluders() || [] : []; } catch (e) { rects = []; }
      this._area = fieldArea(this._w || 0, this._h || 0, rects);
    }
    return this._area;
  }

  /** THE LABELS ROUND SELF (review of #241: "labels pile up at the amoeba"):
   *  the tag, its marks, the look, the ring and the other -- laid out each
   *  frame so they never overlap (the important ones keep their place, a mark
   *  with no room hides), kept inside the visible field (a thing behind a
   *  panel gets its label at the edge with an arrow). DOM writes only on change. */
  _layoutSelfLabels(now) {
    const a = this.amoeba, b = this.brain;
    if (!a || !a.tag) return;
    const area = this._fieldArea(now);
    const on = !!a.pts, items = [], els = {};
    const proj = (p) => (p ? this._project(p) : null);
    const sa = on ? proj(a.pos) : null;
    const add = (key, el, text, anchor, o) => {
      els[key] = el;
      if (!anchor || !text) return;
      if (el._t !== text) { el.textContent = text; el._t = text; el.style.display = ""; el._w = el.offsetWidth || text.length * 6.5 + 14; }
      items.push(Object.assign({ key, ax: anchor[0], ay: anchor[1], w: el._w, h: 18 }, o));
    };
    add("tag", a.tag, a.tagText, !this.selfEyes ? sa : null, { prio: 9, dx: 14, dy: -9 });
    for (const m of a.marks || []) add("mark:" + m.key, m.lab, m.text, on ? proj(a.pts[m.key]) : null, { prio: 1, dx: 8, dy: -9, optional: true });
    if (b && b.lookLab) {
      const cur = b.looks[b.looks.length - 1];
      if (cur && on && now - cur.t0 < 6000) {
        // its field point, or its ghost (#244): either way the chip goes
        // through the layout like every other label (clamped, no overlaps)
        const tgt = lookTarget(cur.p, cur.eid, a.pos);
        const sp = proj(tgt.p), text = lookChip(cur.label, cur.answer, tgt.ghost);
        if (sp) add("look", b.lookLab, text, sp, { prio: 8, dx: 10, dy: -24 });
        else if (sa) add("look", b.lookLab, (tgt.p ? this._arrowTo(sa, tgt.p) + " " : "") + text, sa, { prio: 8, dx: 14, dy: 14 });
        else els.look = b.lookLab;
      } else els.look = b.lookLab;
      const ring = b.rings.filter((o) => o.text).slice(-1)[0];
      if (ring && on) {
        const rp = ring.p ? proj(ring.p) : sa;
        b.ringLab.style.borderLeftColor = ring.gold ? "#d4a017" : "#ff9a3c";
        if (rp) add("ring", b.ringLab, ring.text, rp, { prio: 7, dx: 12, dy: 8 });
        else if (sa) add("ring", b.ringLab, this._arrowTo(sa, ring.p) + " " + ring.text, sa, { prio: 7, dx: 14, dy: 34 });
        else els.ring = b.ringLab;
      } else els.ring = b.ringLab;
      const who = this.selfOther;
      add("other", b.otherLab, who ? who.label : "", who && a.other && a.other.visible ? proj(a.other.position.toArray()) : null,
          { prio: 6, dx: 14, dy: -9 });
    }
    const out = layoutLabels(items, area);
    for (const key of Object.keys(els)) {
      const el = els[key], r = out[key];
      if (!r || r.hidden) { if (el.style.display !== "none") el.style.display = "none"; continue; }
      const base = el._t || "";
      const txt = r.arrow ? r.arrow + " " + base.replace(/^[→↘↓↙←↖↑↗] /, "") : base;
      if (el.textContent !== txt) el.textContent = txt;
      if (el.style.display === "none") el.style.display = "";
      const tf = "translate(" + Math.round(r.x) + "px," + Math.round(r.y) + "px)";
      if (el._tf !== tf) { el.style.transform = tf; el._tf = tf; }
    }
  }

  /** An arrow from a screen point toward a world point (it may be off-screen or behind). */
  _arrowTo(from, p) {
    const v = new this.THREE.Vector3(p[0], p[1], p[2]).project(this.camera);
    let x = (v.x + 1) / 2 * this._w, y = (1 - v.y) / 2 * this._h;
    if (v.z > 1) { x = this._w - x; y = this._h - y; }        // behind the camera: the other way
    const ang = Math.atan2(y - from[1], x - from[0]);
    return ["→", "↘", "↓", "↙", "←", "↖", "↑", "↗"][((Math.round(ang / (Math.PI / 4)) % 8) + 8) % 8];
  }

  _avatar(agent) {
    let a = this.avatars.get(agent);
    if (a) return a;
    const n = this.avatars.size, ang = n * 0.9;
    const idle = [Math.cos(ang) * F3_WORLD * 0.75, F3_WORLD * 0.62, Math.sin(ang) * F3_WORLD * 0.75];
    a = new Avatar(agent, idle);
    const THREE = this.THREE, color = new THREE.Color(F3_AGENTS[agent] || "#ffffff");
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const g = c.getContext("2d"), grd = g.createRadialGradient(32, 32, 2, 32, 32, 30);
    grd.addColorStop(0, "#fff"); grd.addColorStop(0.25, "#" + color.getHexString()); grd.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grd; g.fillRect(0, 0, 64, 64);
    a.sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
    a.sprite.scale.set(46, 46, 1);
    a.beam = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.8 }));
    a.trailLine = new THREE.Line(new THREE.BufferGeometry().setAttribute("position", new THREE.BufferAttribute(new Float32Array(32 * 3), 3)),
      new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.35 }));
    a.tag = document.createElement("div");
    a.tag.style.cssText = "position:absolute;font:600 11px system-ui,sans-serif;color:" + (F3_AGENTS[agent] || "#fff") + ";text-shadow:0 1px 2px #000;white-space:nowrap";
    a.tag.textContent = agent;
    this.labelLayer.appendChild(a.tag);
    this.scene.add(a.sprite, a.beam, a.trailLine);
    this.avatars.set(agent, a);
    return a;
  }

  async _attach(agent, eid, holdMs) {
    if (!agent || !eid) return;
    const a = this._avatar(agent);
    let p = null;
    const s = this.slots.get(eid);
    if (s != null) p = [this.posA[s * 3], this.posA[s * 3 + 1], this.posA[s * 3 + 2]];
    else { const hit = await this._locate(eid); if (hit) p = cubeToWorld(hit.p); }
    a.label = eid;
    if (p) a.attach(eid, p, holdMs, Date.now());
    a.tag.textContent = agent + " · " + eid;
  }

  /** The ids active now: the minds' current entities + those pulsed in the
   * last `ms` (membrane home labels them in activity mode). */
  activeIds(ms = 6000) {
    const out = new Set(), now = Date.now(), pn = performance.now();
    for (const a of this.avatars.values()) if (a.eid && a.attached(now)) out.add(a.eid);
    for (const [sl, t] of this.recentHits || []) {
      if (pn - t > ms) continue;
      const m = this.meta[sl];
      if (m && m.id) out.add(m.id);
    }
    return out;
  }

  /** Where things happen now (membrane home's camera): the mind that most
   * recently attached to an entity, else the mass-weighted centre of the
   * last few seconds' pulses, else null. World coordinates. */
  activity(mindMs = 8000, pulseMs = 10000) {
    const now = Date.now();
    let best = null;
    for (const a of this.avatars.values()) {
      if (a.eid && a.at && now - a.at < mindMs && (!best || a.at > best.at)) best = a;
    }
    if (best) return { kind: "mind", agent: best.agent, eid: best.eid, p: best.target.slice() };
    const pn = performance.now();
    let w = 0; const c = [0, 0, 0];
    for (const [sl, t] of this.recentHits || []) {
      if (pn - t > pulseMs || !this.meta[sl]) continue;
      const k = 1 + Math.log2(2 + ((this.meta[sl] || {}).mass || 0));
      for (let i = 0; i < 3; i++) c[i] += this.posA[sl * 3 + i] * k;
      w += k;
    }
    if (w > 0) return { kind: "pulses", p: c.map((v) => v / w) };
    return null;
  }

  _stepAvatars(dt) {
    const now = Date.now();
    for (const a of this.avatars.values()) {
      a.step(dt, now);
      const th = thinkPose(a.thinking, now);
      a.sprite.position.set(a.pos[0] + th.dx, a.pos[1] + th.dy, a.pos[2] + th.dz);
      const big = (this.selAgent === a.agent ? 84 : 46) * th.scale;
      if (Math.abs(a.sprite.scale.x - big) > 0.5) a.sprite.scale.set(big, big, 1);
      const on = a.attached(now);
      a.beam.visible = on;
      if (on) {
        const bp = a.beam.geometry.attributes.position;
        bp.setXYZ(0, a.pos[0], a.pos[1], a.pos[2]); bp.setXYZ(1, a.target[0], a.target[1], a.target[2]); bp.needsUpdate = true;
      }
      const tp = a.trailLine.geometry.attributes.position;
      for (let i = 0; i < 32; i++) { const q = a.trail[Math.max(0, a.trail.length - 32 + i)] || a.pos; tp.setXYZ(i, q[0], q[1], q[2]); }
      tp.needsUpdate = true;
      const sc = this._project(a.pos);
      a.tag.style.display = sc ? "" : "none";
      if (sc) a.tag.style.transform = "translate(" + (sc[0] + 14).toFixed(0) + "px," + (sc[1] - 6).toFixed(0) + "px)";
      if (!on) a.tag.textContent = a.agent;
      if (a.thinking && !/thinking/.test(a.tag.textContent)) a.tag.textContent += " · thinking…";
      else if (!a.thinking && / · thinking…$/.test(a.tag.textContent)) a.tag.textContent = a.tag.textContent.replace(/ · thinking…$/, "");
    }
  }

  _ring(s) {
    const THREE = this.THREE;
    const m = new THREE.Mesh(new THREE.RingGeometry(0.8, 1, 40),
      new THREE.MeshBasicMaterial({ color: 0xffd166, transparent: true, opacity: 0.9, side: THREE.DoubleSide, depthWrite: false }));
    m.position.set(this.posA[s * 3], this.posA[s * 3 + 1], this.posA[s * 3 + 2]);
    m.userData.t0 = performance.now();
    this.scene.add(m); this.rings.push(m);
  }

  _stepRings(now) {
    this.rings = this.rings.filter((m) => {
      const u = (now - m.userData.t0) / 1600;
      if (u >= 1) { this.scene.remove(m); m.geometry.dispose(); m.material.dispose(); return false; }
      m.scale.setScalar(10 + 90 * u); m.material.opacity = 0.9 * (1 - u); m.quaternion.copy(this.camera.quaternion);
      return true;
    });
  }
}

if (typeof window !== "undefined") { window.MembraneField3D = Field3DView; window.MembraneField3DAgents = F3_AGENTS; }
