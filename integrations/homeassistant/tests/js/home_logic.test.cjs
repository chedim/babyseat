// Membrane home (2026-10-10): whose entity the camera follows.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = path.join(__dirname, "..", "..", "custom_components", "ai_terminal", "www", "home.js");
const ctx = { module: { exports: {} }, console, Math };
vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx, { filename: SRC });
const H = ctx.module.exports;

test("the followed entity is the speaker's own (membrane/speakers.py slug)", () => {
  assert.equal(H.personSlug("Dmitrii Chechetkin"), "dmitrii-chechetkin");
  assert.equal(H.personSlug("  Claude (engineer) "), "claude-engineer");
  assert.equal(H.personSlug(""), "unknown");
});

test("the follow button cycles activity -> seat -> you -> activity", () => {
  assert.equal(H.nextMode("activity"), "seat");
  assert.equal(H.nextMode("seat"), "you");
  assert.equal(H.nextMode("you"), "activity");
  assert.equal(H.nextMode("bogus"), "activity");
});

test("the camera sits on the ray from the centre through the followed point", () => {
  const camDir = (a) => [Math.sin(a.phi) * Math.sin(a.theta), Math.cos(a.phi), Math.sin(a.phi) * Math.cos(a.theta)];
  for (const p of [[300, 0, 0], [0, 0, -250], [120, 200, 80], [-90, -300, 10]]) {
    const a = H.throughCenter(p), d = camDir(a), n = Math.hypot(...p);
    // the camera's offset from p points AWAY from the centre: it looks at the centre through p
    for (let i = 0; i < 3; i++) assert.ok(Math.abs(d[i] - p[i] / n) < 0.06, JSON.stringify([p, d]));
  }
  assert.equal(H.throughCenter([5, 5, 5]), null);
});

test("angles turn the short way round", () => {
  assert.ok(Math.abs(H.angleStep(3.0, -3.0, 1) - (3.0 + (2 * Math.PI - 6.0))) < 1e-9);
  assert.equal(H.angleStep(0, 1, 0.5), 0.5);
});
