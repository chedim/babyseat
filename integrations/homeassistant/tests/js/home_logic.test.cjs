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
