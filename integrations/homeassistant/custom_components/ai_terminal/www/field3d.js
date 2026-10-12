/**
 * The Membrane Field in 3D (2026-10-08, viewer v3): the WHOLE field, live.
 *
 * Data (ai_terminal proxy, GET only):
 *   serve/membrane/map/field3d?axes=...   the projection: pid, n_total, kinds
 *   serve/membrane/map/tile?pid&l&x&y&z   one octree cell's entities + edges
 *   serve/membrane/map/locate?pid&eid     where an entity sits
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

const F3Logic = { snapshotOf, IDBCache, locateCandidates, tileKey, tileCenter, tileRadius, childrenOf, axesSpec, wantedTiles, TileCache, Avatar, parseSSE, cubeToWorld, F3_WORLD, edgePulsePairs, PULSE_COLORS };
if (typeof module !== "undefined" && module.exports) module.exports = F3Logic;

// ------------------------------------------------------------ three.js

function loadThree() {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.THREE) return Promise.resolve(window.THREE);
  if (window.__f3ThreeLoading) return window.__f3ThreeLoading;
  window.__f3ThreeLoading = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "/api/ai_terminal/www/three.min.js";
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
    if (this.showLabels && now - this._lastLabels > 180) { this._lastLabels = now; this._paintLabels(); }
    this.renderer.render(this.scene, this.camera);
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
      cands.push({ id, txt: m.label, x: sc[0], y: sc[1], w: (m.mass + 5) / (d * d) + (id === this.sel ? 1e9 : 0) });
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

  /** Where a mind's orb is now (world coordinates), or null. */
  avatarPos(agent) {
    const a = this.avatars.get(agent);
    return a ? a.pos.slice() : null;
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
