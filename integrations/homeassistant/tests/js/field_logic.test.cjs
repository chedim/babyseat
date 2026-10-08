// The field map's pure logic (www/field.js exports it under node), red
// first (2026-10-08): the anti-clump layout ("make sure the entities don't
// cluster together in one single clump"), labels that never overlap,
// search, kind colors, neighbor lookup.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = path.join(__dirname, "..", "..", "custom_components",
                      "ai_terminal", "www", "field.js");
const ctx = { module: { exports: {} }, console };
vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx, { filename: SRC });
const F = ctx.module.exports;
const eq = (a, b) => assert.deepEqual(JSON.parse(JSON.stringify(a)), b);

// a raw-PCA-shaped input: 280 nodes in a tight blob + 20 far outliers
function clumped() {
  const nodes = [];
  let s = 7;
  const rnd = () => { s = (s * 16807) % 2147483647; return s / 2147483647; };
  for (let i = 0; i < 280; i++)
    nodes.push({ id: "c" + i, kind: "term", mass: 1 + (i % 9), v: [rnd() * 0.02, rnd() * 0.02] });
  for (let i = 0; i < 20; i++)
    nodes.push({ id: "o" + i, kind: "file", mass: 5, v: [(i - 10) * 40, (i % 5 - 2) * 40] });
  return nodes;
}

function nnDists(pts) {
  return pts.map((p, i) => {
    let best = Infinity;
    pts.forEach((q, j) => { if (i !== j) best = Math.min(best, Math.hypot(p.x - q.x, p.y - q.y)); });
    return best;
  });
}

function spearman(a, b) {
  const rank = (xs) => { const ix = xs.map((x, i) => [x, i]).sort((p, q) => p[0] - q[0]);
    const r = new Array(xs.length); ix.forEach(([, i], k) => { r[i] = k; }); return r; };
  const ra = rank(a), rb = rank(b), n = a.length;
  const d2 = ra.reduce((s, r, i) => s + (r - rb[i]) ** 2, 0);
  return 1 - (6 * d2) / (n * (n * n - 1));
}

test("layout spreads a clump: real spacing, no central crowd", () => {
  const nodes = clumped();
  const pts = F.layout(nodes, 0, 1, { world: 1000 });
  assert.equal(pts.length, nodes.length);
  const nn = nnDists(pts).sort((a, b) => a - b);
  const sep = F.minSep(nodes.length, 1000);
  assert.ok(nn[Math.floor(nn.length * 0.5)] >= 0.6 * sep, "median NN " + nn[150] + " < 0.6*" + sep);
  assert.ok(nn[Math.floor(nn.length * 0.05)] >= 0.25 * sep, "5th pct NN too small");
  // the middle tenth of the world (1% of its area) must not hold the crowd
  const central = pts.filter((p) => Math.abs(p.x) < 50 && Math.abs(p.y) < 50).length;
  assert.ok(central / pts.length < 0.08, "central share " + central / pts.length);
  for (const p of pts) assert.ok(Math.abs(p.x) <= 520 && Math.abs(p.y) <= 520);
});

test("layout keeps the axis order (the map still means something)", () => {
  const nodes = clumped();
  const pts = F.layout(nodes, 0, 1, { world: 1000 });
  assert.ok(spearman(nodes.map((n) => n.v[0]), pts.map((p) => p.x)) > 0.85);
  assert.ok(spearman(nodes.map((n) => n.v[1]), pts.map((p) => p.y)) > 0.85);
});

test("layout is deterministic and survives identical points", () => {
  const same = Array.from({ length: 50 }, (_, i) => ({ id: "s" + i, kind: "k", mass: 1, v: [3, 3] }));
  const a = F.layout(same, 0, 1, { world: 1000 });
  const b = F.layout(same, 0, 1, { world: 1000 });
  assert.equal(JSON.stringify(a), JSON.stringify(b));
  const nn = nnDists(a);
  assert.ok(Math.min(...nn) > 0.2 * F.minSep(50, 1000));
});

test("labels never overlap and prefer heavy nodes", () => {
  const items = [
    { id: "a", x: 0, y: 0, w: 60, h: 12, weight: 9 },
    { id: "b", x: 10, y: 4, w: 60, h: 12, weight: 1 },
    { id: "c", x: 200, y: 0, w: 60, h: 12, weight: 2 },
  ];
  const shown = F.placeLabels(items).map((l) => l.id).sort();
  assert.deepEqual(JSON.parse(JSON.stringify(shown)), ["a", "c"]);
});

test("search ranks prefix over contains, case-insensitive", () => {
  const nodes = [{ id: "t:boat", label: "my boat" }, { id: "f:x", label: "Boat motor" },
                 { id: "t:y", label: "unrelated" }];
  const hits = F.search(nodes, "boat").map((n) => n.id);
  assert.deepEqual(JSON.parse(JSON.stringify(hits)), ["f:x", "t:boat"]);
  assert.equal(F.search(nodes, "").length, 0);
});

test("kind colors are stable and distinct", () => {
  assert.equal(F.kindColor("file"), F.kindColor("file"));
  assert.notEqual(F.kindColor("file"), F.kindColor("term"));
  assert.match(F.kindColor("x"), /^hsl\(/);
});

test("neighbors from edges, both directions, with relations", () => {
  const edges = [{ s: "a", t: "b", rel: "calls", n: 2 }, { s: "c", t: "a", rel: "uses", n: 1 },
                 { s: "b", t: "c", rel: "x", n: 1 }];
  const nb = JSON.parse(JSON.stringify(F.neighbors(edges, "a")));
  assert.deepEqual(nb, [{ id: "b", rel: "calls", dir: "out", n: 2 }, { id: "c", rel: "uses", dir: "in", n: 1 }]);
});

// THE ENTITY AXES (2026-10-08, the operator's ask): "Make X-axis" / "Make
// Y-axis" turn an axis into the distance from one entity (its 0-point);
// the chip's x restores the axis chosen before; each axis independently.
test("entity axis remembers the axis it replaced, across re-picks", () => {
  let x = { kind: "axis", index: 3 };
  x = F.setEntityAxis(x, { id: "w:agatha", label: "agatha" });
  assert.equal(x.kind, "entity");
  assert.equal(x.id, "w:agatha");
  assert.equal(x.prev, 3);
  x = F.setEntityAxis(x, { id: "w:boat", label: "boat" });     // re-pick
  assert.equal(x.id, "w:boat");
  assert.equal(x.prev, 3);                                      // still the original
  x = F.clearEntityAxis(x);
  assert.equal(JSON.stringify(x), JSON.stringify({ kind: "axis", index: 3 }));
  assert.equal(F.clearEntityAxis({ kind: "axis", index: 1 }).index, 1);
});

test("axis values: regular index or the entity's distances", () => {
  const nodes = [{ id: "a", v: [1, 2] }, { id: "b", v: [3, 4] }, { id: "c", v: [5, 6] }];
  eq(F.axisValues({ kind: "axis", index: 1 }, nodes, {}), [2, 4, 6]);
  const d = { a: 0, b: 0.4 };                                   // c unknown -> the far end
  const vals = F.axisValues({ kind: "entity", id: "a" }, nodes, d);
  assert.equal(vals[0], 0);
  assert.equal(vals[1], 0.4);
  assert.ok(vals[2] > 0.4);
});

test("an entity axis puts the reference at the 0-point and keeps distance order", () => {
  const nodes = Array.from({ length: 40 }, (_, i) => ({ id: "n" + i, kind: "k", mass: 1, v: [i, i % 7] }));
  const dist = {}; nodes.forEach((n, i) => { dist[n.id] = (i * 37 % 40) / 40; });
  dist.n0 = 0;
  const xs = F.axisValues({ kind: "entity", id: "n0" }, nodes, dist);
  const ys = F.axisValues({ kind: "axis", index: 1 }, nodes, dist);
  const pts = F.layoutXY(nodes, xs, ys, { world: 1000 });
  const ref = pts.find((p) => p.id === "n0");
  // the de-overlap may swap neighbors within ONE spacing (that is the
  // anti-clump at work); order must hold beyond it
  const sep = F.minSep(nodes.length, 1000);
  assert.ok(Math.min(...pts.map((p) => p.x)) >= ref.x - sep, "the reference sits at the axis start");
  const byD = nodes.map((n, i) => [dist[n.id], pts[i].x]).sort((a, b) => a[0] - b[0]);
  let inv = 0;
  for (let i = 1; i < byD.length; i++) if (byD[i][1] < byD[i - 1][1] - sep) inv++;
  assert.ok(inv === 0, "distance order kept beyond one spacing (" + inv + " inversions)");
});

test("layout() is layoutXY over the node's own axis values", () => {
  const nodes = clumped().slice(0, 60);
  const a = F.layout(nodes, 0, 1, { world: 1000 });
  const b = F.layoutXY(nodes, nodes.map((n) => n.v[0]), nodes.map((n) => n.v[1]), { world: 1000 });
  assert.equal(JSON.stringify(a), JSON.stringify(b));
});

// THE SIGNED ENTITY AXIS: the chip toggles distance <-> toward (0 = the
// field's typical memory, + = shares what makes the entity distinctive)
test("the chip's mode toggle keeps the entity and the axis to restore", () => {
  let x = F.setEntityAxis({ kind: "axis", index: 2 }, { id: "w:agatha", label: "agatha" });
  assert.equal(x.mode, "distance");
  const t = F.toggleAxisMode(x);
  assert.equal(t.mode, "toward");
  assert.equal(t.id, "w:agatha");
  assert.equal(t.prev, 2);
  assert.equal(F.toggleAxisMode(t).mode, "distance");
  assert.notEqual(F.distKey(x), F.distKey(t));
  assert.equal(F.toggleAxisMode({ kind: "axis", index: 1 }).kind, "axis");   // no-op on a regular axis
});

test("an unknown node sits at the far end on distance, at the typical 0 on toward", () => {
  const nodes = [{ id: "a", v: [0] }, { id: "b", v: [0] }];
  eq(F.axisValues({ kind: "entity", id: "a", mode: "distance" }, nodes, { a: 0 }), [0, 2]);
  eq(F.axisValues({ kind: "entity", id: "a", mode: "toward" }, nodes, { a: 3.1 }), [3.1, 0]);
});
