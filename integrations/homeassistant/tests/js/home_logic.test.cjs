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

test("the HA menu: the user's sidebar order, hidden panels out, this page out, settings last", () => {
  const panels = {
    lovelace: { url_path: "lovelace", component_name: "lovelace", title: null, icon: null },
    "boat-dash": { url_path: "boat-dash", component_name: "lovelace", title: "Boat", icon: "mdi:sail-boat" },
    energy: { url_path: "energy", component_name: "energy", title: null, icon: null },
    config: { url_path: "config", component_name: "config", title: null, icon: null },
    profile: { url_path: "profile", component_name: "profile", title: null, icon: null },
    membrane: { url_path: "membrane", component_name: "custom", title: "Membrane", icon: "mdi:cube-scan" },
    hidden: { url_path: "hidden", component_name: "custom", title: "Secret", icon: "mdi:x" },
    notitle: { url_path: "notitle", component_name: "weird", title: null, icon: null },
  };
  const e = H.menuEntries(panels, ["boat-dash", "energy"], ["hidden"], "membrane");
  assert.equal(JSON.stringify(e.map((x) => x.path)), JSON.stringify(["boat-dash", "energy", "lovelace", "config", "profile"]));
  assert.equal(e.find((x) => x.path === "lovelace").title, "Overview");
  assert.equal(e.find((x) => x.path === "boat-dash").icon, "mdi:sail-boat");
});
