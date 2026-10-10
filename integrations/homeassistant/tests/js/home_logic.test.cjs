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

test("the recording: 48 kHz float -> 16 kHz int16 -> a WAV whisper reads", () => {
  const f = new Float32Array(4800).fill(0.5);
  const i16 = H.downsample(f, 48000, 16000);
  assert.equal(i16.length, 1600);
  assert.ok(Math.abs(i16[10] - 0.5 * 0x7fff) < 2);
  const w = H.wavBytes(i16, 16000);
  assert.equal(String.fromCharCode(...w.slice(0, 4)), "RIFF");
  assert.equal(String.fromCharCode(...w.slice(8, 12)), "WAVE");
  assert.equal(w.length, 44 + 3200);
  const dv = new DataView(w.buffer);
  assert.equal(dv.getUint32(24, true), 16000);
  assert.equal(dv.getUint16(22, true), 1);
});

test("speech comes in sentences, long ones split at a space", () => {
  const c = H.speechChunks("The lights are on. Battery is at 80%! " + "word ".repeat(80));
  assert.equal(c[0], "The lights are on.");
  assert.equal(c[1], "Battery is at 80%!");
  assert.ok(c.slice(2).every((x) => x.length <= 220));
});

test("tilt: the axes follow the screen's rotation; the orbit turns relative to the start", () => {
  assert.equal(JSON.stringify(H.tiltAxes(10, 20, 0)), "[20,10]");
  assert.equal(JSON.stringify(H.tiltAxes(10, 20, 90)), "[10,-20]");
  assert.equal(JSON.stringify(H.tiltAxes(10, 20, -90)), "[-10,20]");
  const base = { lr: 0, fb: 40, theta: 1, phi: 1 };
  const o = H.tiltOrbit(base, 10, 40, 1);
  assert.ok(Math.abs(o.theta - (1 + 10 * Math.PI / 180)) < 1e-9);
  assert.equal(o.phi, 1);
  assert.ok(H.tiltOrbit(base, 0, 500).phi <= Math.PI - 0.05);
  assert.ok(H.tiltOrbit(base, 0, -500).phi >= 0.05);
});

test("the hotword: 'Hey Argus' and whisper's usual misspellings, the rest is the message", () => {
  const same = (t, hit, rest) => { const w = H.hotword(t); assert.equal(w.hit, hit, t); if (hit) assert.equal(w.rest, rest, t); };
  same("Hey Argus, turn on the deck lights.", true, "turn on the deck lights.");
  same("hey, argus!", true, "");
  same("Argos what's the battery?", true, "what's the battery?");
  same("OK Angus. How are you", true, "How are you");
  same("...Hey Argus", true, "");
  same("I told Argus about it", false);
  same("hey august", true, "");
  same("Argusville is a town", false);
  same("", false);
});

test("the device's own voice: English, on-device, not Google's when there is a choice", () => {
  const v = (name, lang, local) => ({ name, lang, localService: local });
  assert.equal(H.pickVoice([v("Google US English", "en-US", false), v("Samantha", "en-US", true)]).name, "Samantha");
  assert.equal(H.pickVoice([v("Google UK", "en-GB", true), v("Daniel", "en-GB", true)]).name, "Daniel");
  assert.equal(H.pickVoice([v("Google US English", "en-US", true)]).name, "Google US English");   // on-device anyway
  assert.equal(H.pickVoice([v("Google US English", "en-US", false)]), null);                       // network-only: no
  assert.equal(H.pickVoice([]), null);
});

test("the local engine's spellings of the name wake it and are stripped from the message", () => {
  assert.equal(H.hotword("hey are gus turn on the lights").rest, "turn on the lights");
  assert.equal(H.hotword("hey our gus").hit, true);
});
