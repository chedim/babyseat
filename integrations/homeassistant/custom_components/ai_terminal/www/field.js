/**
 * <membrane-field-panel>: the brain's field as a 2D map (2026-10-08).
 *
 * Data (through the ai_terminal proxy, GET only): serve/membrane/map/field
 * (the field's live entities + their relations + every node's value on
 * every axis: PC1..8, kind directions, mass/recency/age/activity/
 * coherence/degree), serve/membrane/state + serve/agent (the HUD).
 *
 * THE ANTI-CLUMP LAYOUT: a raw PCA projection piles most entities into one
 * blob with a few outliers setting the scale. layout() places each axis by
 * RANK (order kept, the tails no longer squash the middle; ties broken by
 * a per-id hash so identical states scatter instead of stacking), then a
 * collision pass pushes overlapping nodes apart while a spring holds each
 * near its spot. Labels appear only where they fit (placeLabels), heavier
 * entities first.
 *
 * Controls: drag = pan, wheel/pinch = zoom at the cursor, click = the
 * drawer (neighbors, the entity read, "ask the brain"), search = fly to.
 */
const FSERVE = "/api/ai_terminal/serve";

// ------------------------------------------------------------ pure logic

function hash32(s) {
  let h = 2166136261;
  s = String(s);
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

function minSep(n, world) { return 0.8 * world / Math.sqrt(Math.max(1, n)); }

// rank in [-1, 1], ties broken by a per-id hash (identical values scatter)
function rankNorm(vals, ids, salt) {
  const n = vals.length;
  if (n < 2) return vals.map(() => 0);
  const ix = vals.map((v, i) => i);
  ix.sort((a, b) => (vals[a] - vals[b]) || (hash32(ids[a] + salt) - hash32(ids[b] + salt)));
  const out = new Array(n);
  ix.forEach((i, r) => { out[i] = (r / (n - 1)) * 2 - 1; });
  return out;
}

// robust z (median/MAD), clipped to [-1, 1]: keeps a little of the real gaps
function robust(vals) {
  const s = vals.slice().sort((a, b) => a - b);
  const med = s[Math.floor(s.length / 2)] || 0;
  const dev = vals.map((v) => Math.abs(v - med)).sort((a, b) => a - b);
  const mad = (dev[Math.floor(dev.length / 2)] || 0) * 1.4826 || 1;
  return vals.map((v) => Math.max(-1, Math.min(1, (v - med) / mad / 3)));
}

/** World positions for nodes on axes (xi, yi): rank-spread + de-overlap. */
function layout(nodes, xi, yi, opts) {
  return layoutXY(nodes, nodes.map((d) => (d.v || [])[xi]), nodes.map((d) => (d.v || [])[yi]), opts);
}

/** The same layout over explicit per-node values (an entity axis's distances). */
function layoutXY(nodes, xsIn, ysIn, opts) {
  const world = (opts && opts.world) || 1000;
  const n = nodes.length;
  if (!n) return [];
  const ids = nodes.map((d) => d.id);
  const xs = xsIn.map((v) => Number(v) || 0);
  const ys = ysIn.map((v) => Number(v) || 0);
  const rx = rankNorm(xs, ids, "|x"), ry = rankNorm(ys, ids, "|y");
  const zx = robust(xs), zy = robust(ys);
  const half = world / 2 * 0.92;
  const ax = rx.map((r, i) => (0.8 * r + 0.2 * zx[i]) * half);
  const ay = ry.map((r, i) => (0.8 * r + 0.2 * zy[i]) * half);
  const px = ax.slice(), py = ay.slice();
  const sep = minSep(n, world);
  const lim = world / 2 * 1.02;
  for (let it = 0; it < 90; it++) {
    const grid = new Map();
    for (let i = 0; i < n; i++) {
      const k = Math.floor(px[i] / sep) + "," + Math.floor(py[i] / sep);
      if (!grid.has(k)) grid.set(k, []);
      grid.get(k).push(i);
    }
    const dx = new Float64Array(n), dy = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const cx = Math.floor(px[i] / sep), cy = Math.floor(py[i] / sep);
      for (let gx = cx - 1; gx <= cx + 1; gx++) for (let gy = cy - 1; gy <= cy + 1; gy++) {
        const cell = grid.get(gx + "," + gy);
        if (!cell) continue;
        for (const j of cell) {
          if (j <= i) continue;
          let ex = px[i] - px[j], ey = py[i] - py[j];
          let d = Math.hypot(ex, ey);
          if (d >= sep) continue;
          if (d < 1e-6) {                       // stacked: a deterministic direction
            const a = (hash32(ids[i] + "#" + ids[j]) / 4294967296) * Math.PI * 2;
            ex = Math.cos(a); ey = Math.sin(a); d = 1e-6;
          } else { ex /= d; ey /= d; }
          const push = (sep - d) * 0.5;
          dx[i] += ex * push; dy[i] += ey * push;
          dx[j] -= ex * push; dy[j] -= ey * push;
        }
      }
    }
    const pull = it < 60 ? 0.02 : 0.005;
    for (let i = 0; i < n; i++) {
      px[i] = Math.max(-lim, Math.min(lim, px[i] + dx[i] + (ax[i] - px[i]) * pull));
      py[i] = Math.max(-lim, Math.min(lim, py[i] + dy[i] + (ay[i] - py[i]) * pull));
    }
  }
  return nodes.map((d, i) => ({ id: d.id, x: Math.round(px[i] * 100) / 100, y: Math.round(py[i] * 100) / 100 }));
}

/** Greedy non-overlapping labels, heaviest first. items: {id,x,y,w,h,weight}. */
function placeLabels(items) {
  const sorted = items.slice().sort((a, b) => (b.weight - a.weight) || (a.id < b.id ? -1 : 1));
  const kept = [];
  const cell = 64, grid = new Map();
  for (const it of sorted) {
    const box = [it.x, it.y - it.h / 2, it.x + it.w, it.y + it.h / 2];
    const c0 = Math.floor(box[0] / cell), c1 = Math.floor(box[2] / cell);
    const r0 = Math.floor(box[1] / cell), r1 = Math.floor(box[3] / cell);
    let hit = false;
    for (let cx = c0; cx <= c1 && !hit; cx++) for (let cy = r0; cy <= r1 && !hit; cy++) {
      for (const b of grid.get(cx + "," + cy) || []) {
        if (box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]) { hit = true; break; }
      }
    }
    if (hit) continue;
    kept.push(it);
    for (let cx = c0; cx <= c1; cx++) for (let cy = r0; cy <= r1; cy++) {
      const k = cx + "," + cy;
      if (!grid.has(k)) grid.set(k, []);
      grid.get(k).push(box);
    }
  }
  return kept;
}

function search(nodes, q) {
  q = String(q || "").trim().toLowerCase();
  if (!q) return [];
  const out = [];
  for (const d of nodes) {
    const l = String(d.label || "").toLowerCase(), id = String(d.id || "").toLowerCase();
    const s = l.startsWith(q) ? 0 : id.startsWith(q) ? 1 : l.includes(q) ? 2 : id.includes(q) ? 3 : -1;
    if (s >= 0) out.push([s, String(d.label || d.id).length, d]);
  }
  out.sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  return out.slice(0, 30).map((x) => x[2]);
}

function kindColor(kind) {
  const h = hash32("kind:" + kind) % 360;
  return "hsl(" + h + ", 68%, 62%)";
}

function neighbors(edges, id) {
  const out = [];
  for (const e of edges || []) {
    if (e.s === id) out.push({ id: e.t, rel: e.rel, dir: "out", n: e.n });
    else if (e.t === id) out.push({ id: e.s, rel: e.rel, dir: "in", n: e.n });
  }
  return out.sort((a, b) => b.n - a.n);
}

// THE ENTITY AXES (2026-10-08): an axis is {kind:"axis", index} or
// {kind:"entity", id, label, prev} -- the distance from one entity (its
// 0-point); prev = the regular axis the chip's x restores
function setEntityAxis(spec, ent) {
  const prev = spec && spec.kind === "entity" ? spec.prev : (spec ? spec.index : 0);
  return { kind: "entity", id: ent.id, label: ent.label || ent.id, prev: prev || 0, mode: "distance" };
}
// distance (1 - cos, the entity = 0) <-> toward (signed: 0 = the typical memory)
function toggleAxisMode(spec) {
  if (!spec || spec.kind !== "entity") return spec;
  return Object.assign({}, spec, { mode: spec.mode === "toward" ? "distance" : "toward" });
}
function distKey(spec) { return spec.id + "|" + (spec.mode || "distance"); }
function clearEntityAxis(spec) {
  return spec && spec.kind === "entity" ? { kind: "axis", index: spec.prev || 0 } : spec;
}
const FAR = 2;                          // cosine distance's ceiling: unknown = the far end
function axisValues(spec, nodes, dist) {
  if (spec && spec.kind === "entity") {
    const d = dist || {};
    const miss = spec.mode === "toward" ? 0 : FAR;
    return nodes.map((n) => (d[n.id] != null ? d[n.id] : miss));
  }
  const i = spec ? spec.index : 0;
  return nodes.map((n) => (n.v || [])[i]);
}

const FieldLogic = { layout, layoutXY, minSep, placeLabels, search, kindColor, neighbors, rankNorm,
                     setEntityAxis, clearEntityAxis, axisValues, toggleAxisMode, distKey };
if (typeof module !== "undefined" && module.exports) module.exports = FieldLogic;

// ------------------------------------------------------------ the element

function fesc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const FSTYLE = `
  :host { display:block; height:100vh; height:100dvh; overflow:hidden; position:relative;
          background:var(--primary-background-color,#0e1116); color:var(--primary-text-color,#ddd);
          font:13px/1.45 system-ui,-apple-system,sans-serif; }
  * { box-sizing:border-box; }
  button, select, input { font:inherit; color:inherit; }
  canvas { position:absolute; inset:0; width:100%; height:100%; touch-action:none; cursor:grab; display:block; }
  canvas.drag { cursor:grabbing; }
  .top { position:absolute; left:10px; right:10px; top:10px; display:flex; flex-wrap:wrap; gap:6px; align-items:center;
         pointer-events:none; z-index:3; }
  .top > * { pointer-events:auto; }
  .card { background:color-mix(in srgb, var(--card-background-color,#1b1f27) 88%, transparent);
          border:1px solid rgba(128,128,128,.25); border-radius:10px; box-shadow:0 2px 10px rgba(0,0,0,.25); }
  .ctl { display:flex; gap:6px; align-items:center; padding:5px 8px; }
  .ctl select, .ctl input { border-radius:7px; border:1px solid rgba(128,128,128,.35);
                            background:var(--secondary-background-color,#222); padding:3px 6px; }
  .ctl label { opacity:.7; font-size:12px; }
  .btn { border:0; border-radius:7px; padding:4px 9px; background:var(--secondary-background-color,#2a2a2a); cursor:pointer; }
  .btn.on { background:var(--primary-color,#3477d6); color:var(--text-primary-color,#fff); }
  .title { font-weight:600; padding:5px 10px; }
  .tagchip { display:inline-flex; align-items:center; gap:4px; padding:2px 4px 2px 8px; border-radius:999px;
             background:var(--primary-color,#3477d6); color:var(--text-primary-color,#fff); font-size:12px;
             max-width:220px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .tagchip .unx { border:0; background:rgba(255,255,255,.22); color:inherit; border-radius:50%; width:18px; height:18px;
                  line-height:18px; padding:0; cursor:pointer; font-size:11px; flex:none; }
  .searchbox { position:relative; }
  .searchbox input { width:200px; }
  .hits { position:absolute; top:34px; left:0; width:300px; max-height:50vh; overflow:auto; display:none; z-index:5; }
  .hits.open { display:block; }
  .hit { padding:5px 10px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .hit:hover { background:rgba(128,128,128,.18); }
  .hud { position:absolute; left:10px; bottom:10px; display:flex; flex-wrap:wrap; gap:6px; z-index:3; max-width:70%; }
  .chip { padding:3px 9px; font-size:12px; }
  .chip b { font-weight:600; }
  .legend { position:absolute; right:10px; bottom:10px; z-index:3; padding:6px 8px; max-height:40vh; overflow:auto; }
  .lk { display:flex; align-items:center; gap:6px; cursor:pointer; padding:1px 2px; font-size:12px; white-space:nowrap; }
  .lk.off { opacity:.35; }
  .sw { width:10px; height:10px; border-radius:50%; flex:none; }
  .drawer { position:absolute; top:60px; right:10px; width:340px; max-width:calc(100% - 20px); max-height:calc(100% - 120px);
            overflow:auto; z-index:4; padding:12px; display:none; }
  .drawer.open { display:block; }
  .drawer h3 { margin:0 0 4px; font-size:15px; overflow-wrap:anywhere; }
  .drawer .id { font:11px ui-monospace,Menlo,monospace; opacity:.6; overflow-wrap:anywhere; }
  .kv { display:grid; grid-template-columns:auto 1fr; gap:2px 10px; margin:8px 0; font-size:12px; }
  .kv span:nth-child(odd) { opacity:.6; }
  .nb { padding:3px 6px; border-radius:6px; cursor:pointer; display:flex; gap:6px; font-size:12px; }
  .nb:hover { background:rgba(128,128,128,.15); }
  .nb .rel { opacity:.6; font-family:ui-monospace,Menlo,monospace; }
  .drawer pre { font:11px ui-monospace,Menlo,monospace; white-space:pre-wrap; max-height:260px; overflow:auto;
                background:rgba(0,0,0,.25); border-radius:6px; padding:6px; }
  .x { float:right; border:0; background:none; cursor:pointer; font-size:16px; opacity:.7; }
  .msg { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; z-index:2;
         pointer-events:none; opacity:.7; white-space:pre-wrap; text-align:center; padding:20px; }
  .tip { position:absolute; z-index:4; pointer-events:none; padding:3px 8px; font-size:12px; display:none; max-width:320px; }
  .host3d { position:absolute; inset:0; display:none; }
  :host([mode="3d"]) .host3d { display:block; }
  :host([mode="3d"]) canvas.c2d, :host([mode="3d"]) .only2d { display:none !important; }
  :host(:not([mode="3d"])) .only3d { display:none !important; }
  .agents { position:absolute; right:10px; top:60px; z-index:3; padding:6px 8px; font-size:12px; display:none; }
  :host([mode="3d"]) .agents.has { display:block; }
`;

function defineFieldPanel() {
  class MembraneFieldPanel extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._data = null;
      this._pos = [];
      // each axis: {kind:"axis", index} or an entity axis {kind:"entity", id, label, prev}
      this._ax = { x: { kind: "axis", index: 0 }, y: { kind: "axis", index: 1 } };
      this._dists = {};                 // entity id -> {node id: distance}
      this._m = 1200;
      this._showEdges = true;
      this._showLabels = true;
      this._hidden = new Set();
      this._view = { s: 1, tx: 0, ty: 0 };
      this._sel = null;
      this._booted = false;
    }

    set hass(h) { this._hass = h; if (!this._booted && this.isConnected && this._cv) { this._booted = true; this._load(); } }
    get hass() { return this._hass; }
    set narrow(n) { this._narrow = !!n; if (this._menuBtn) this._menuBtn.style.display = n ? "" : "none"; }

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
      if (!this._cv) this._build();
      if (!this.hasAttribute("mode")) this.setAttribute("mode", "3d");
      if (!this._booted && this._hass) { this._booted = true; this._load(); }
    }
    disconnectedCallback() { this._booted = false; clearInterval(this._hudTimer); if (this._ro) this._ro.disconnect();
      if (this._v3) { this._v3.stop(); this._v3 = null; } }

    _build() {
      const st = document.createElement("style");
      st.textContent = FSTYLE;
      const root = document.createElement("div");
      root.innerHTML = `
        <canvas class="c2d"></canvas>
        <div class="host3d"></div>
        <div class="msg">loading the field…</div>
        <div class="top">
          <button class="btn card menu" style="display:none" title="Home Assistant menu">☰</button>
          <span class="card title">Membrane Field</span>
          <span class="card ctl"><button class="btn mode3d on" title="3D: the whole field, live">3D</button><button class="btn mode2d" title="2D: today's map of the heaviest entities">2D</button></span>
          <span class="card ctl only3d"><label>X</label><select class="a3" data-i="0"></select><span class="axchip3" data-i="0"></span>
            <label>Y</label><select class="a3" data-i="1"></select><span class="axchip3" data-i="1"></span>
            <label>Z</label><select class="a3" data-i="2"></select><span class="axchip3" data-i="2"></span></span>
          <span class="card ctl only3d"><button class="btn on edges3" title="Relations">edges</button><button class="btn on labels3">labels</button>
            <span class="status3" style="opacity:.75;font-size:12px"></span></span>
          <span class="card ctl only2d"><label>X</label><select class="ax"></select><span class="axchip" data-axis="x"></span>
            <label>Y</label><select class="ay"></select><span class="axchip" data-axis="y"></span>
            <button class="btn swap" title="Swap axes">⇄</button></span>
          <span class="card ctl only2d"><label>nodes</label><select class="mm">
            <option value="400">400</option><option value="1200" selected>1200</option><option value="3000">3000</option></select>
            <button class="btn on edges" title="Relations">edges</button><button class="btn on labels">labels</button>
            <button class="btn fit" title="Fit">⤢</button><button class="btn reload" title="Reload the field">↻</button></span>
          <span class="card ctl searchbox"><input class="q" placeholder="search entities…"><div class="card hits"></div></span>
        </div>
        <div class="hud"></div>
        <div class="card agents"></div>
        <div class="card legend"></div>
        <div class="card drawer"></div>
        <div class="card tip"></div>`;
      this.shadowRoot.append(st, root);
      const $ = (q) => root.querySelector(q);
      this._cv = $("canvas.c2d"); this._ctx = this._cv.getContext("2d");
      this._host3d = $(".host3d"); this._status3 = $(".status3"); this._agents = $(".agents");
      $(".mode3d").addEventListener("click", () => this._setMode("3d"));
      $(".mode2d").addEventListener("click", () => this._setMode("2d"));
      root.querySelectorAll(".a3").forEach((sel) => sel.addEventListener("change", () => {
        const i = +sel.dataset.i, v = sel.value;
        const spec = v.startsWith("kind:") ? { kind: "kind", name: v.slice(5) } : { kind: "pc", index: +v.slice(2) };
        if (this._v3) this._v3.setAxis(i, spec).then(() => this._paint3dAxes()).catch((e) => this._err3(e));
      }));
      root.querySelectorAll(".axchip3").forEach((c) => c.addEventListener("click", (e) => {
        if (!e.target.closest(".unx") || !this._v3) return;
        const i = +c.dataset.i, a = this._v3.ax[i];
        const next = e.target.closest(".mode") ? Object.assign({}, a, { mode: a.mode === "toward" ? "distance" : "toward" })
          : (a.prev || { kind: "pc", index: i + 1 });
        this._v3.setAxis(i, next).then(() => this._paint3dAxes()).catch((er) => this._err3(er));
      }));
      const tog3 = (sel, key) => $(sel).addEventListener("click", (e) => {
        if (!this._v3) return; this._v3[key] = !this._v3[key]; e.target.classList.toggle("on", this._v3[key]);
        this._v3._edgesDirty = true; if (key === "showLabels" && !this._v3.showLabels) this._v3.labels.forEach((l) => { l.style.display = "none"; }); });
      tog3(".edges3", "showEdges"); tog3(".labels3", "showLabels");
      this._msg = $(".msg"); this._hud = $(".hud"); this._legend = $(".legend");
      this._drawer = $(".drawer"); this._tip = $(".tip"); this._hits = $(".hits");
      this._axSel = $(".ax"); this._aySel = $(".ay"); this._q = $(".q");
      this._menuBtn = $(".menu");
      if (this._narrow) this._menuBtn.style.display = "";
      this._menuBtn.addEventListener("click", () => this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })));
      this._axSel.addEventListener("change", () => { this._ax.x = { kind: "axis", index: +this._axSel.value }; this._relayout(); });
      this._aySel.addEventListener("change", () => { this._ax.y = { kind: "axis", index: +this._aySel.value }; this._relayout(); });
      $(".swap").addEventListener("click", () => { [this._ax.x, this._ax.y] = [this._ax.y, this._ax.x];
        this._paintAxisChips(); this._relayout(); });
      root.querySelectorAll(".axchip").forEach((c) => c.addEventListener("click", async (e) => {
        if (!e.target.closest(".unx")) return;
        const which = c.dataset.axis;
        if (e.target.closest(".mode")) {
          const next = toggleAxisMode(this._ax[which]);
          if (await this._ensureDists(next)) { this._ax[which] = next; this._paintAxisChips(); this._relayout(true); }
          if (this._sel) this._select(this._sel);
          return;
        }
        this._ax[which] = clearEntityAxis(this._ax[which]);
        this._paintAxisChips(); this._relayout(true);
        if (this._sel) this._select(this._sel);
      }));
      $(".mm").addEventListener("change", (e) => { this._m = +e.target.value; this._load(); });
      const tog = (sel, key) => $(sel).addEventListener("click", (e) => {
        this[key] = !this[key]; e.target.classList.toggle("on", this[key]); this._draw(); });
      tog(".edges", "_showEdges"); tog(".labels", "_showLabels");
      $(".fit").addEventListener("click", () => { this._fit(); this._draw(); });
      $(".reload").addEventListener("click", () => this._load());
      this._q.addEventListener("keydown", (e) => e.stopPropagation());
      this._q.addEventListener("input", () => this._showHits());
      this._hits.addEventListener("click", (e) => {
        const h = e.target.closest(".hit"); if (!h) return;
        this._hits.classList.remove("open");
        if (this._is3d()) { this._v3.flyTo(h.dataset.id).then((ok) => { if (ok) this._select3d(h.dataset.id); }); }
        else this._flyTo(h.dataset.id);
      });
      this._legend.addEventListener("click", (e) => {
        const k = e.target.closest(".lk"); if (!k) return;
        const kd = k.dataset.kind;
        if (this._hidden.has(kd)) this._hidden.delete(kd); else this._hidden.add(kd);
        if (this._is3d()) { this._v3.setHidden(this._hidden); this._paintLegend3d(); } else { this._paintLegend(); this._draw(); }
      });
      this._drawer.addEventListener("click", (e) => (this._is3d() ? this._onDrawer3d(e) : this._onDrawer(e)));
      this._wireCanvas();
      this._ro = new ResizeObserver(() => this._resize());
      this._ro.observe(this);
    }

    async _get(path) {
      const r = await this._hass.fetchWithAuth(FSERVE + path);
      const t = await r.text();
      let d = null; try { d = t ? JSON.parse(t) : null; } catch (e) { d = t; }
      if (!r.ok) throw new Error(path + " → " + r.status + " " + (typeof d === "string" ? d : JSON.stringify(d)).slice(0, 160));
      return d;
    }

    async _load() {
      if (this.getAttribute("mode") === "3d") return this._load3d();
      this._msg.style.display = ""; this._msg.textContent = "loading the field…";
      try {
        const d = await this._get("/membrane/map/field?m=" + this._m);
        this._data = d;
        this._dists = {};
        for (const k of ["x", "y"]) {
          const a = this._ax[k];
          if (a.kind === "entity" && !(await this._ensureDists(a))) this._ax[k] = clearEntityAxis(a);
        }
        this._byId = new Map(d.nodes.map((n, i) => [n.id, i]));
        this._paintAxes();
        this._paintLegend();
        this._relayout(true);
        this._msg.style.display = d.nodes.length ? "none" : "";
        if (!d.nodes.length) this._msg.textContent = "the field is empty";
      } catch (e) {
        this._msg.textContent = "field unavailable\n" + (e.message || e);
      }
      this._hudOnce();
      clearInterval(this._hudTimer);
      this._hudTimer = setInterval(() => this._hudOnce(), 60000);
    }

    // ---------------------------------------------------------- 3D
    _is3d() { return this.getAttribute("mode") === "3d" && !!this._v3; }

    _setMode(m) {
      if (this.getAttribute("mode") === m) return;
      this.setAttribute("mode", m);
      this.shadowRoot.querySelector(".mode3d").classList.toggle("on", m === "3d");
      this.shadowRoot.querySelector(".mode2d").classList.toggle("on", m === "2d");
      this._sel = null; this._drawer.classList.remove("open"); this._tip.style.display = "none";
      if (m === "3d") { this._load3d(); }
      else { if (this._v3) { this._v3.stop(); this._v3 = null; } this._agents.classList.remove("has"); this._data = null; this._load(); }
    }

    _err3(e) { this._msg.style.display = ""; this._msg.textContent = "3D field unavailable\n" + ((e && e.message) || e); }

    async _load3d() {
      this._msg.style.display = ""; this._msg.textContent = "projecting the whole field…";
      try {
        if (!window.MembraneField3D) await import("/api/ai_terminal/www/field3d.js");
        if (this._v3) this._v3.stop();
        const hass = this._hass;
        const api = {
          get: (path) => this._get(path),
          stream: (path, signal) => hass.fetchWithAuth(FSERVE + path, { signal, headers: { Accept: "text/event-stream" } }),
          kindColor, isAdmin: !!(hass.user && hass.user.is_admin),
          onStatus: (t) => { this._status3.textContent = t; this._paintAgents(); },
          onSelect: (m) => { if (m) this._select3d(m.id); else { this._sel = null; this._drawer.classList.remove("open"); } },
          onRelated: (id) => { if (this._sel === id && this._drawer.classList.contains("open")) this._select3d(id); },
          onHover: (m, x, y) => {
            if (!m) { this._tip.style.display = "none"; return; }
            this._tip.textContent = m.label + "  ·  " + m.kind + "  ·  mass " + m.mass;
            this._tip.style.left = Math.min(x + 14, (this._host3d.clientWidth || 0) - 220) + "px";
            this._tip.style.top = (y + 14) + "px"; this._tip.style.display = "block";
          },
        };
        this._v3 = new window.MembraneField3D(this._host3d, api);
        this._v3.setHidden(this._hidden);
        await this._v3.start();
        this._paint3dAxes(); this._paintLegend3d();
        this._msg.style.display = (this._v3.info || {}).n_total ? "none" : "";
        if (!(this._v3.info || {}).n_total) this._msg.textContent = "the field is empty";
      } catch (e) { this._err3(e); }
      this._hudOnce();
      clearInterval(this._hudTimer);
      this._hudTimer = setInterval(() => this._hudOnce(), 60000);
    }

    _paint3dAxes() {
      const v = this._v3; if (!v || !v.info) return;
      const ex = v.info.explained || [];
      const pcs = ex.map((e, i) => '<option value="pc' + (i + 1) + '">PC' + (i + 1) + " (" + (e * 100).toFixed(1) + "%)</option>").join("");
      const kinds = (v.info.kinds || []).slice(0, 10).map((k) => '<option value="kind:' + fesc(k) + '">toward ' + fesc(k) + "</option>").join("");
      const html = '<optgroup label="components">' + pcs + '</optgroup><optgroup label="kinds">' + kinds + "</optgroup>";
      this.shadowRoot.querySelectorAll(".a3").forEach((sel) => {
        const i = +sel.dataset.i, a = v.ax[i];
        const chip = this.shadowRoot.querySelector('.axchip3[data-i="' + i + '"]');
        sel.innerHTML = html;
        if (a.kind === "entity") {
          sel.style.display = "none";
          const tw = a.mode === "toward";
          chip.innerHTML = '<span class="tagchip" title="' + (tw ? "toward " : "distance from ") + fesc(a.id) + '">' +
            '<button class="unx mode" title="Switch to ' + (tw ? "distance" : "toward (signed)") + '">' + (tw ? "±" : "⟂") + "</button> " +
            fesc(a.label || a.id) + ' <button class="unx" title="Back to the previous axis">✕</button></span>';
        } else {
          sel.style.display = ""; chip.innerHTML = "";
          sel.value = a.kind === "kind" ? "kind:" + a.name : "pc" + (a.index || i + 1);
        }
      });
    }

    _paintLegend3d() {
      const v = this._v3; if (!v || !v.info) return;
      const counts = v.info.kind_counts || {};
      this._legend.innerHTML = (v.info.kinds || []).slice(0, 24).map((k) =>
        '<div class="lk' + (this._hidden.has(k) ? " off" : "") + '" data-kind="' + fesc(k) + '"><span class="sw" style="background:' +
        kindColor(k) + '"></span>' + fesc(k) + ' <span style="opacity:.5">' + (counts[k] || 0).toLocaleString() + "</span></div>").join("");
    }

    _paintAgents() {
      const v = this._v3; if (!v) return;
      const now = Date.now();
      const rows = [...v.avatars.values()].map((a) => '<div class="lk" title="' + fesc(a.eid || "") + '"><span class="sw" style="background:' +
        (window.MembraneField3DAgents || {})[a.agent] + '"></span><b>' + fesc(a.agent) + "</b>&nbsp;" +
        fesc(a.attached(now) ? (a.eid || "").slice(0, 36) : "idle") + "</div>");
      this._agents.innerHTML = rows.join("");
      this._agents.classList.toggle("has", rows.length > 0);
    }

    _select3d(id) {
      const v = this._v3; if (!v) return;
      const s = v.slots.get(id), m = s == null ? null : v.meta[s];
      if (!m) return;
      this._sel = id;
      v.select(id);
      const ax = ["X", "Y", "Z"];
      this._drawer.innerHTML =
        '<button class="x" title="Close">✕</button><h3>' + fesc(m.label) + '</h3><div class="id">' + fesc(m.id) + "</div>" +
        '<div class="kv"><span>kind</span><span><span class="sw" style="display:inline-block;background:' + kindColor(m.kind) + '"></span> ' + fesc(m.kind) +
        "</span><span>mass</span><span>" + fesc(m.mass) + "</span><span>detail level</span><span>" + fesc(m.lv) + "</span>" +
        (m.c ? "<span>conflicts</span><span style=\"color:#ff7a7a\">" + fesc(m.c) + "</span>" : "") +
        ax.map((k, i) => "<span>" + k + "</span><span>" + fesc((+m.p[i]).toFixed(3)) + "</span>").join("") + "</div>" +
        '<button class="btn on ask">Ask the brain about this</button> <button class="btn read">Entity details</button>' +
        '<div style="margin-top:6px">' + ax.map((k, i) => '<button class="btn mk3" data-i="' + i + '" title="Distance from this entity becomes the ' + k +
          ' axis">Make ' + k + "-axis</button>").join(" ") + "</div>" +
        '<div class="detail"></div>' + (() => {
          const nb = v.related(id).slice(0, 40);
          return nb.length ? "<h4 style=\"margin:10px 0 4px\">Related (" + nb.length + ")</h4>" + nb.map((x) =>
            '<div class="nb" data-id="' + fesc(x.id) + '"><span class="rel">' + (x.dir === "out" ? "→ " : "← ") + fesc(x.rel) +
            "</span><span>" + fesc(x.label) + (x.loaded === false ? ' <span style="opacity:.45">(zoom in to load)</span>' : "") +
            '</span><span style="opacity:.5">×' + fesc(x.n) + "</span></div>").join("")
            : '<div style="opacity:.6;margin-top:10px">no relations</div>';
        })();
      this._drawer.classList.add("open");
    }

    async _onDrawer3d(e) {
      const v = this._v3; if (!v) return;
      if (e.target.closest(".x")) { this._sel = null; v.select(null); this._drawer.classList.remove("open"); return; }
      const nb = e.target.closest(".nb");
      if (nb) { if (await v.flyTo(nb.dataset.id)) this._select3d(nb.dataset.id); return; }
      const s = v.slots.get(this._sel), m = s == null ? null : v.meta[s];
      if (!m) return;
      const mk = e.target.closest(".mk3");
      if (mk) {
        const i = +mk.dataset.i, prev = v.ax[i].kind === "entity" ? v.ax[i].prev : v.ax[i];
        try { await v.setAxis(i, { kind: "entity", id: m.id, label: m.label, mode: "distance", prev }); this._paint3dAxes(); }
        catch (er) { this._err3(er); }
        return;
      }
      if (e.target.closest(".ask")) {
        const q = "Tell me about " + m.label + " (" + m.id + ") from your field: what it is, how it relates, why it matters.";
        history.pushState(null, "", "/ai-terminal?ask=" + encodeURIComponent(q));
        window.dispatchEvent(new CustomEvent("location-changed"));
        return;
      }
      if (e.target.closest(".read")) {
        const box = this._drawer.querySelector(".detail");
        box.innerHTML = "<pre>loading…</pre>";
        this._byId = this._byId || new Map();
        try { box.innerHTML = this._cardHtml(await this._get("/membrane/field/entity?eid=" + encodeURIComponent(m.id))); }
        catch (err) { box.innerHTML = "<pre>" + fesc(err.message || err) + "</pre>"; }
      }
    }

    _paintAxes() {
      const axes = this._data.axes || [];
      const groups = {};
      axes.forEach((a, i) => { (groups[a.group] = groups[a.group] || []).push([a, i]); });
      const html = Object.entries(groups).map(([g, list]) => '<optgroup label="' + fesc(g) + '">' +
        list.map(([a, i]) => '<option value="' + i + '">' + fesc(a.label) + "</option>").join("") + "</optgroup>").join("");
      this._axSel.innerHTML = html; this._aySel.innerHTML = html;
      for (const [k, dflt] of [["x", 0], ["y", Math.min(1, axes.length - 1)]]) {
        const a = this._ax[k];
        if (a.kind === "axis" && a.index >= axes.length) this._ax[k] = { kind: "axis", index: dflt };
        if (a.kind === "entity" && (a.prev || 0) >= axes.length) a.prev = dflt;
      }
      this._paintAxisChips();
    }

    _paintLegend() {
      const d = this._data; if (!d) return;
      const counts = d.kind_counts || {};
      this._legend.innerHTML = (d.kinds || []).slice(0, 24).map((k) =>
        '<div class="lk' + (this._hidden.has(k) ? " off" : "") + '" data-kind="' + fesc(k) + '"><span class="sw" style="background:' +
        kindColor(k) + '"></span>' + fesc(k) + ' <span style="opacity:.5">' + (counts[k] || 0) + "</span></div>").join("");
    }

    async _hudOnce() {
      try {
        const [st, ag] = await Promise.all([this._get("/membrane/state"), this._get("/agent").catch(() => [])]);
        const ef = st.efield || {}, ents = ef.entities || {}, geo = ef.geometry || {}, ev = ef.events || {};
        const waiting = Object.entries(ev).filter(([k]) => /\/(waiting|pending)$/.test(k)).reduce((s, [, v]) => s + v, 0);
        const loops = (ag || []).filter((a) => /^loop-\d+$/.test(a.name || ""));
        const busy = loops.filter((a) => /\(processing/.test(a.description || "")).length;
        const th = ((st.think || {}).stats || {});
        const chips = [
          ["entities", ents.live], ["nursery", ents.nursery], ["eff. rank", geo.eff_rank != null ? geo.eff_rank.toFixed(1) : null],
          ["waiting", waiting], ["thinker beats", th.processed], ["loops busy", loops.length ? busy + "/" + loops.length : null],
          ["relations", ((this._data || {}).field || {}).relations], [st.paused ? "PAUSED" : "running", ""],
        ].filter(([, v]) => v !== undefined && v !== null);
        // the watch frame: what the brain watches, as axes -- the ones the
        // talk leans along most (+ = more than usual)
        for (const r of (st.frame || []).filter((r) => r.n >= 3).slice(0, 3)) {
          chips.push(["axis " + String(r.axis).replace(/^membrane::/, "").slice(0, 28), (r.lean > 0 ? "+" : "") + (+r.lean).toFixed(2)]);
        }
        this._hud.innerHTML = chips.map(([k, v]) => '<span class="card chip">' + fesc(k) + (v !== "" ? " <b>" + fesc(v) + "</b>" : "") + "</span>").join("");
      } catch (e) { this._hud.innerHTML = '<span class="card chip">brain unreachable</span>'; }
    }

    _relayout(fit) {
      const d = this._data; if (!d) return;
      const ax = this._ax;
      const dv = (a) => (a.kind === "entity" ? this._dists[distKey(a)] : null);
      this._pos = layoutXY(d.nodes, axisValues(ax.x, d.nodes, dv(ax.x)),
                           axisValues(ax.y, d.nodes, dv(ax.y)), { world: 1000 });
      if (fit || !this._fitted) { this._fit(); this._fitted = true; }
      this._draw();
    }

    _resize() {
      const r = this.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      this._w = r.width; this._h = r.height;
      this._cv.width = Math.round(r.width * dpr); this._cv.height = Math.round(r.height * dpr);
      this._ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!this._fitted && this._pos.length) this._fit();
      this._draw();
    }

    _fit() {
      const w = this._w || 800, h = this._h || 600;
      this._view.s = Math.min(w, h) / 1100;
      this._view.tx = w / 2; this._view.ty = h / 2;
    }

    _toScreen(p) { return [p.x * this._view.s + this._view.tx, -p.y * this._view.s + this._view.ty]; }

    _visible(i) { return !this._hidden.has(this._data.nodes[i].kind); }

    _radius(n) { return Math.max(2.5, Math.min(11, 2.2 + Math.sqrt(Math.max(0, n.mass)) * 0.35)); }

    _draw() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = 0; this._drawNow(); });
    }

    _drawNow() {
      const c = this._ctx, d = this._data;
      if (!c) return;
      c.clearRect(0, 0, this._w || 0, this._h || 0);
      if (!d || !this._pos.length) return;
      const nodes = d.nodes, pos = this._pos;
      const scr = pos.map((p) => this._toScreen(p));
      const selId = this._sel;
      if (this._showEdges && d.edges && d.edges.length) {
        const maxN = Math.max(1, ...d.edges.slice(0, 50).map((e) => e.n));
        c.lineWidth = 1;
        for (const e of d.edges) {
          const a = this._byId.get(e.s), b = this._byId.get(e.t);
          if (a == null || b == null || !this._visible(a) || !this._visible(b)) continue;
          const hot = selId && (e.s === selId || e.t === selId);
          const al = hot ? 0.85 : 0.06 + 0.3 * Math.log1p(e.n) / Math.log1p(maxN);
          c.strokeStyle = hot ? "rgba(255,210,90," + al + ")" : "rgba(150,170,200," + al + ")";
          c.beginPath(); c.moveTo(scr[a][0], scr[a][1]); c.lineTo(scr[b][0], scr[b][1]); c.stroke();
        }
      }
      for (let i = 0; i < nodes.length; i++) {
        if (!this._visible(i)) continue;
        const [x, y] = scr[i];
        if (x < -20 || y < -20 || x > this._w + 20 || y > this._h + 20) continue;
        const n = nodes[i], r = this._radius(n);
        c.fillStyle = kindColor(n.kind);
        c.globalAlpha = selId && selId !== n.id ? 0.75 : 1;
        c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill();
        if (n.contest) { c.strokeStyle = "#ff5a5a"; c.lineWidth = 1.5; c.stroke(); }
        if (selId === n.id) { c.strokeStyle = "#fff"; c.lineWidth = 2.5; c.beginPath(); c.arc(x, y, r + 3, 0, Math.PI * 2); c.stroke(); }
      }
      c.globalAlpha = 1;
      if (this._showLabels) {
        c.font = "12px system-ui,sans-serif";
        const cands = [];
        for (let i = 0; i < nodes.length; i++) {
          if (!this._visible(i)) continue;
          const [x, y] = scr[i];
          if (x < 0 || y < 0 || x > this._w || y > this._h) continue;
          const n = nodes[i];
          cands.push({ i, id: n.id, weight: n.mass * (1 + n.deg) + (n.id === selId ? 1e12 : 0), x: x + this._radius(n) + 3, y, h: 14, txt: n.label });
        }
        cands.sort((a, b) => b.weight - a.weight);
        const top = cands.slice(0, 700);
        for (const it of top) it.w = c.measureText(it.txt).width + 4;
        const shown = placeLabels(top);
        c.textBaseline = "middle";
        for (const it of shown) {
          c.fillStyle = "rgba(0,0,0,.55)"; c.fillText(it.txt, it.x + 1, it.y + 1);
          c.fillStyle = it.id === selId ? "#fff" : "rgba(235,240,250,.92)"; c.fillText(it.txt, it.x, it.y);
        }
      }
    }

    _wireCanvas() {
      const cv = this._cv;
      const pts = new Map();
      let moved = false, last = null, pinch = null;
      cv.addEventListener("pointerdown", (e) => {
        cv.setPointerCapture(e.pointerId);
        pts.set(e.pointerId, [e.offsetX, e.offsetY]);
        moved = false; last = [e.offsetX, e.offsetY];
        if (pts.size === 2) {
          const [a, b] = [...pts.values()];
          pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), s: this._view.s };
        }
        cv.classList.add("drag");
      });
      cv.addEventListener("pointermove", (e) => {
        if (!pts.has(e.pointerId)) { this._hover(e.offsetX, e.offsetY); return; }
        pts.set(e.pointerId, [e.offsetX, e.offsetY]);
        if (pts.size === 2 && pinch) {
          const [a, b] = [...pts.values()];
          const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
          this._zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (pinch.s * d / pinch.d) / this._view.s);
          moved = true;
          return;
        }
        const dx = e.offsetX - last[0], dy = e.offsetY - last[1];
        if (Math.abs(dx) + Math.abs(dy) > 2) moved = true;
        this._view.tx += dx; this._view.ty += dy; last = [e.offsetX, e.offsetY];
        this._draw();
      });
      const up = (e) => {
        pts.delete(e.pointerId);
        if (pts.size < 2) pinch = null;
        if (!pts.size) cv.classList.remove("drag");
        if (!moved && e.type === "pointerup") this._click(e.offsetX, e.offsetY);
      };
      cv.addEventListener("pointerup", up);
      cv.addEventListener("pointercancel", up);
      cv.addEventListener("wheel", (e) => {
        e.preventDefault();
        this._zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0015));
      }, { passive: false });
      cv.addEventListener("dblclick", (e) => this._zoomAt(e.offsetX, e.offsetY, 2));
      cv.addEventListener("pointerleave", () => { this._tip.style.display = "none"; });
    }

    _zoomAt(x, y, f) {
      const s0 = this._view.s, s1 = Math.max(0.05, Math.min(60, s0 * f));
      const k = s1 / s0;
      this._view.tx = x - (x - this._view.tx) * k;
      this._view.ty = y - (y - this._view.ty) * k;
      this._view.s = s1;
      this._draw();
    }

    _pick(x, y) {
      if (!this._data) return -1;
      let best = -1, bd = 14 * 14;
      for (let i = 0; i < this._pos.length; i++) {
        if (!this._visible(i)) continue;
        const [sx, sy] = this._toScreen(this._pos[i]);
        const d = (sx - x) ** 2 + (sy - y) ** 2;
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    }

    _hover(x, y) {
      const i = this._pick(x, y);
      if (i < 0) { this._tip.style.display = "none"; return; }
      const n = this._data.nodes[i];
      const extra = ["x", "y"].filter((k) => this._ax[k].kind === "entity")
        .map((k) => k.toUpperCase() + " " + this._axisValue(k, n)).join("  ");
      this._tip.textContent = n.label + "  ·  " + n.kind + (extra ? "  ·  " + extra : "");
      this._tip.style.left = Math.min(x + 14, (this._w || 0) - 200) + "px";
      this._tip.style.top = (y + 14) + "px";
      this._tip.style.display = "block";
    }

    _click(x, y) {
      const i = this._pick(x, y);
      if (i < 0) { this._sel = null; this._drawer.classList.remove("open"); this._draw(); return; }
      this._select(this._data.nodes[i].id);
    }

    _flyTo(id) {
      const i = this._byId.get(id);
      if (i == null) return;
      this._hidden.delete(this._data.nodes[i].kind);
      this._paintLegend();
      const p = this._pos[i];
      this._view.s = Math.max(this._view.s, Math.min(this._w, this._h) / 300);
      this._view.tx = this._w / 2 - p.x * this._view.s;
      this._view.ty = this._h / 2 + p.y * this._view.s;
      this._select(id);
    }

    _select(id) {
      this._sel = id;
      const d = this._data, i = this._byId.get(id), n = d.nodes[i];
      const ax = d.axes || [];
      const nb = neighbors(d.edges, id).slice(0, 40);
      const lab = (nid) => { const j = this._byId.get(nid); return j == null ? nid : d.nodes[j].label; };
      this._drawer.innerHTML =
        '<button class="x" title="Close">✕</button><h3>' + fesc(n.label) + '</h3><div class="id">' + fesc(n.id) + "</div>" +
        '<div class="kv"><span>kind</span><span><span class="sw" style="display:inline-block;background:' + kindColor(n.kind) + '"></span> ' + fesc(n.kind) +
        "</span><span>mass</span><span>" + fesc(n.mass) + "</span><span>relations</span><span>" + fesc(n.deg) + "</span>" +
        (n.contest ? "<span>conflicts</span><span style=\"color:#ff7a7a\">" + fesc(n.contest) + "</span>" : "") +
        ["x", "y"].map((k) => "<span>" + k.toUpperCase() + ": " + fesc(this._axisLabel(k)) + "</span><span>" +
          fesc(this._axisValue(k, n)) + "</span>").join("") + "</div>" +
        '<button class="btn on ask">Ask the brain about this</button> <button class="btn read">Entity details</button>' +
        '<div style="margin-top:6px"><button class="btn mkx" title="Distance from this entity becomes the X axis">Make X-axis</button> ' +
        '<button class="btn mky" title="Distance from this entity becomes the Y axis">Make Y-axis</button></div>' +
        '<div class="detail"></div>' +
        (nb.length ? "<h4 style=\"margin:10px 0 4px\">Related (" + nb.length + ")</h4>" + nb.map((x) =>
          '<div class="nb" data-id="' + fesc(x.id) + '"><span class="rel">' + (x.dir === "out" ? "→ " : "← ") + fesc(x.rel) +
          "</span><span>" + fesc(lab(x.id)) + '</span><span style="opacity:.5">×' + fesc(x.n) + "</span></div>").join("")
          : '<div style="opacity:.6;margin-top:10px">no relations among the shown entities</div>');
      this._drawer.classList.add("open");
      this._draw();
    }

    _axisLabel(k) {
      const a = this._ax[k];
      if (a.kind === "entity") return (a.mode === "toward" ? "toward " : "distance to ") + a.label;
      return ((this._data && this._data.axes) || [])[a.index] ? this._data.axes[a.index].label : k;
    }

    _axisValue(k, n) {
      const a = this._ax[k];
      if (a.kind === "entity") {
        const d = (this._dists[distKey(a)] || {})[n.id];
        return d == null ? "n/a" : (a.mode === "toward" && d > 0 ? "+" : "") + (+d).toFixed(3);
      }
      return n.v[a.index];
    }

    _paintAxisChips() {
      for (const [k, sel] of [["x", this._axSel], ["y", this._aySel]]) {
        const a = this._ax[k];
        const chip = this.shadowRoot.querySelector('.axchip[data-axis="' + k + '"]');
        if (a.kind === "entity") {
          sel.style.display = "none";
          const tw = a.mode === "toward";
          chip.innerHTML = '<span class="tagchip" title="' + (tw ? "axis = toward " + fesc(a.id) + ": 0 = typical, + = shares what makes it distinctive"
              : "axis = distance from " + fesc(a.id) + " (it is the 0-point)") + '">' +
            '<button class="unx mode" title="Switch to ' + (tw ? "distance" : "toward (signed)") + '">' + (tw ? "±" : "⟂") + "</button> " + fesc(a.label) +
            ' <button class="unx" title="Back to ' + fesc(((this._data && this._data.axes) || [])[a.prev || 0]
              ? this._data.axes[a.prev || 0].label : "the axis") + '">✕</button></span>';
        } else {
          sel.style.display = "";
          sel.value = a.index;
          chip.innerHTML = "";
        }
      }
    }

    async _ensureDists(spec) {
      const key = distKey(spec);
      if (this._dists[key]) return true;
      try {
        const r = await this._get("/membrane/map/distance?eid=" + encodeURIComponent(spec.id) +
                                  (spec.mode === "toward" ? "&mode=toward" : ""));
        this._dists[key] = r.dist || {};
        return true;
      } catch (err) {
        const box = this._drawer.querySelector(".detail");
        if (box) box.innerHTML = "<pre>" + fesc(err.message || err) + "</pre>";
        return false;
      }
    }

    async _makeAxis(k, n) {
      try {
        const next = setEntityAxis(this._ax[k], { id: n.id, label: n.label });
        if (!(await this._ensureDists(next))) return;
        this._ax[k] = next;
        this._paintAxisChips();
        this._relayout(true);
        this._select(n.id);
      } catch (err) {
        const box = this._drawer.querySelector(".detail");
        if (box) box.innerHTML = "<pre>" + fesc(err.message || err) + "</pre>";
      }
    }

    _cardHtml(c) {
      const when = (t) => {
        if (!t) return "";
        const d = new Date(t > 1e12 ? t : t * 1000);
        return isNaN(d) ? String(t) : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      };
      const streams = Object.entries(c.n_by_stream || {}).map(([k, v]) => k + " " + v).join(", ");
      let h = '<div class="kv">' +
        [["seen", c.n + (streams ? " (" + streams + ")" : "")], ["coherence", c.coherence != null ? (+c.coherence).toFixed(2) : ""],
         ["activity", c.activity != null ? (+c.activity).toFixed(2) : ""], ["first seen", when(c.first_ts)],
         ["last seen", when(c.last_ts)], ["tier", c.tier]]
          .filter(([, v]) => v !== "" && v != null).map(([k, v]) => "<span>" + fesc(k) + "</span><span>" + fesc(v) + "</span>").join("") + "</div>";
      if ((c.evidence || []).length) {
        h += '<h4 style="margin:10px 0 4px">Evidence</h4>' + c.evidence.map((e) =>
          '<div style="margin:4px 0;padding:4px 8px;border-left:2px solid rgba(128,128,128,.4);font-size:12px">' + fesc(e.text) +
          '<div style="opacity:.5;font-size:11px">' + fesc([e.role, e.stream, when(e.ts)].filter(Boolean).join(" · ")) + "</div></div>").join("");
      }
      if ((c.near || []).length) {
        h += '<h4 style="margin:10px 0 4px">Nearest in the field</h4>' + c.near.map((x) =>
          '<div class="nb" data-id="' + fesc(x.eid) + '"><span class="rel">' + fesc(x.sim) + "</span><span>" + fesc(x.surface || x.eid) +
          (this._byId.has(x.eid) ? "" : ' <span style="opacity:.45">(not on the map)</span>') + "</span></div>").join("");
      }
      if ((c.relations || []).length) {
        h += '<h4 style="margin:10px 0 4px">Relations</h4>' + c.relations.map((r) =>
          '<div class="nb" data-id="' + fesc(r.subj === c.eid ? r.obj : r.subj) + '"><span class="rel">' + fesc(r.rel) +
          "</span><span>" + fesc(r.subj === c.eid ? "→ " + r.obj : "← " + r.subj) + ' <span style="opacity:.5">×' + fesc(r.n) + "</span></span></div>").join("");
      }
      return h;
    }

    async _onDrawer(e) {
      if (e.target.closest(".x")) { this._sel = null; this._drawer.classList.remove("open"); this._draw(); return; }
      const nb = e.target.closest(".nb");
      if (nb) { this._flyTo(nb.dataset.id); return; }
      const n = this._data.nodes[this._byId.get(this._sel)];
      if (!n) return;
      const mk = e.target.closest(".mkx, .mky");
      if (mk) { this._makeAxis(mk.classList.contains("mkx") ? "x" : "y", n); return; }
      if (e.target.closest(".ask")) {
        const q = "Tell me about " + n.label + " (" + n.id + ") from your field: what it is, how it relates, why it matters.";
        history.pushState(null, "", "/ai-terminal?ask=" + encodeURIComponent(q));
        window.dispatchEvent(new CustomEvent("location-changed"));
        return;
      }
      if (e.target.closest(".read")) {
        const box = this._drawer.querySelector(".detail");
        box.innerHTML = "<pre>loading…</pre>";
        try {
          // the FIELD entity's own card (membrane/field/entity); the old
          // membrane/entity/read is the named-entity store: field eids 404
          box.innerHTML = this._cardHtml(await this._get("/membrane/field/entity?eid=" + encodeURIComponent(n.id)));
        } catch (err) { box.innerHTML = "<pre>" + fesc(err.message || err) + "</pre>"; }
      }
    }

    _showHits() {
      const hits = this._is3d() ? this._v3.search(this._q.value) : (this._data ? search(this._data.nodes, this._q.value) : []);
      this._hits.innerHTML = hits.map((n) => '<div class="hit" data-id="' + fesc(n.id) + '"><span class="sw" style="display:inline-block;background:' +
        kindColor(n.kind) + '"></span> ' + fesc(n.label) + ' <span style="opacity:.5">' + fesc(n.kind) + "</span></div>").join("");
      this._hits.classList.toggle("open", hits.length > 0);
    }
  }
  customElements.define("membrane-field-panel", MembraneFieldPanel);
}

if (typeof HTMLElement !== "undefined" && typeof customElements !== "undefined" &&
    !customElements.get("membrane-field-panel")) {
  defineFieldPanel();
}
