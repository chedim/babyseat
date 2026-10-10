// The 3D field viewer's pure logic (www/field3d.js exports it under node),
// 2026-10-08: the recycled octree (what the camera wants, what it lets go),
// the minds' avatars, the SSE parsing, the axes the seat reads.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = path.join(__dirname, "..", "..", "custom_components", "ai_terminal", "www", "field3d.js");
const ctx = { module: { exports: {} }, console, Date, Math };
vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx, { filename: SRC });
const F = ctx.module.exports;
const same = (a, b) => assert.deepEqual(JSON.parse(JSON.stringify(a)), b);

function cam(dist, fwd) {
  return { pos: [0, 0, dist], fwd: fwd || [0, 0, -1], fovY: 55 * Math.PI / 180, aspect: 1.5, heightPx: 800 };
}

function knownAll(levels) {
  const k = new Map();
  for (let l = 0; l <= levels; l++) {
    const side = 1 << l;
    for (let x = 0; x < side; x++) for (let y = 0; y < side; y++) for (let z = 0; z < side; z++)
      k.set(F.tileKey(l, x, y, z), { more: l < levels, n: 10 });
  }
  return k;
}

test("from far away only the root is wanted", () => {
  const w = F.wantedTiles(cam(20000), { maxLevel: 4, known: knownAll(4) });
  assert.equal(w.length, 1);
  assert.equal(w[0].key, "0/0/0/0");
});

test("closer in, the octree refines: more tiles, deeper levels", () => {
  const known = knownAll(3);
  const far = F.wantedTiles(cam(2500), { maxLevel: 3, known });
  const near = F.wantedTiles(cam(700), { maxLevel: 3, known });
  assert.ok(near.length > far.length, near.length + " > " + far.length);
  assert.ok(Math.max(...near.map((t) => t.l)) > Math.max(...far.map((t) => t.l)));
  assert.equal(near[0].l, 0);                                  // coarse first
});

test("only loaded tiles that hold finer entities refine", () => {
  const known = new Map([["0/0/0/0", { more: true, n: 150 }]]);
  const w = F.wantedTiles(cam(700), { maxLevel: 3, known });
  assert.ok(w.every((t) => t.l <= 1));                         // level 1 not loaded yet: no level 2
  const none = F.wantedTiles(cam(700), { maxLevel: 3, known: new Map([["0/0/0/0", { more: false }]]) });
  assert.equal(none.length, 1);
});

test("tiles behind the camera are not wanted", () => {
  const known = knownAll(2);
  const fwd = F.wantedTiles(cam(300), { maxLevel: 2, known });
  const back = F.wantedTiles(cam(300, [0, 0, 1]), { maxLevel: 2, known });
  const behind = (ts) => ts.filter((t) => t.l === 2 && F.tileCenter(t.l, t.x, t.y, t.z)[2] < -300).length;
  assert.ok(behind(back) < behind(fwd));
});

test("the cap holds", () => {
  const w = F.wantedTiles(cam(10), { maxLevel: 4, known: knownAll(4), maxTiles: 50 });
  assert.equal(w.length, 50);
});

test("the cache lets go of what left the view, after a grace or over budget", () => {
  const c = new F.TileCache(100, 1000);
  c.put("a", { nodes: new Array(60) }, 0);
  c.put("b", { nodes: new Array(30) }, 0);
  assert.equal(c.nodes, 90);
  same(c.evict(new Set(["a", "b"]), 5000), []);    // both wanted: kept
  same(c.evict(new Set(["a"]), 500), []);          // b in grace
  same(c.evict(new Set(["a"]), 1500), ["b"]);      // grace over
  assert.equal(c.nodes, 60);
  c.put("c", { nodes: new Array(60) }, 1500);
  same(c.evict(new Set(["c"]), 1600), ["a"]);      // over budget: at once
  assert.equal(c.nodes, 60);
});

test("an avatar glides to its entity, holds, and drifts home", () => {
  const a = new F.Avatar("loop-1", [0, 500, 0]);
  a.attach("f:x", [100, 0, 0], 1000, 0);
  for (let i = 0; i < 60; i++) a.step(0.05, 500);
  assert.ok(Math.abs(a.pos[0] - 100) < 5 && Math.abs(a.pos[1]) < 5);
  assert.ok(a.attached(500));
  a.step(0.05, 2000);                                         // the hold ran out
  assert.equal(a.eid, null);
  for (let i = 0; i < 60; i++) a.step(0.05, 2100);
  assert.ok(Math.abs(a.pos[1] - 500) < 5);
  assert.ok(a.trail.length > 1);
});

test("a focus holds until released", () => {
  const a = new F.Avatar("loop-2", [0, 0, 0]);
  a.attach("goal:x", [1, 1, 1], null, 0);
  assert.ok(a.attached(1e9));
  a.release();
  assert.ok(!a.attached(1));
});

test("SSE frames parse, torn tails wait", () => {
  const buf = 'event: field.moved\ndata: {"type":"field.moved","properties":{"pid":"p"}}\n\n' +
              'data: {"type":"mind.touch","properties":{"eid":"c:x"}}\n\ndata: {"type":"mind.fo';
  const out = F.parseSSE(buf);
  assert.equal(out.frames.length, 2);
  assert.equal(out.frames[1].properties.eid, "c:x");
  assert.equal(out.rest, 'data: {"type":"mind.fo');
});

test("the axes spell the way the seat reads them", () => {
  assert.equal(F.axesSpec([{ kind: "pc", index: 1 }, { kind: "entity", id: "f:a.py", mode: "toward" },
                           { kind: "kind", name: "s" }]), "pc1,e:f:a.py:toward,kind:s");
  assert.equal(F.axesSpec([{ kind: "entity", id: "c:x" }]), "e:c:x");
});

test("tiles tile the cube", () => {
  assert.deepEqual(Array.from(F.tileCenter(0, 0, 0, 0)), [0, 0, 0]);
  assert.equal(F.childrenOf(1, 1, 0, 1).length, 8);
  assert.ok(F.tileRadius(1) < F.tileRadius(0));
});

test("store specs map to the field's keys", () => {
  const c = (e) => JSON.parse(JSON.stringify(F.locateCandidates(e)));
  assert.ok(c("files:membrane/efield.py").includes("f:membrane/efield.py"));
  assert.ok(c("membrane::goal:abc").includes("m:goal:abc"));
  assert.ok(c("component:argus").includes("m:component:argus"));
  assert.equal(c("f:a.py")[0], "f:a.py");
});

test("edge pulses: visible edges only, from the event's first entity outwards, capped", () => {
  const edges = new Set(["a|b", "b|a", "a|c", "c|a"]);
  const has = (x, y) => edges.has(x + "|" + y);
  const pairs = F.edgePulsePairs([{ s: "discourse", e: ["a", "b", "c", "d"] }, { s: "observation", e: ["c", "a"] }], has);
  assert.deepEqual(JSON.parse(JSON.stringify(pairs)), [["a", "b", "discourse"], ["a", "c", "discourse"], ["c", "a", "observation"]]);
  const many = [{ s: "", e: Array.from({ length: 12 }, (_, i) => "n" + i) }];
  assert.equal(F.edgePulsePairs(many, () => true, 5).length, 5);
  assert.equal(F.edgePulsePairs(null, has).length, 0);
});
