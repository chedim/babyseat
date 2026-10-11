// The panel's pure logic (www/panel.js exports it under node), red first
// (2026-10-08): markdown safety, the SSE parser, the live reducer, tool
// cards (lane refusals), the stick-to-bottom rule.
// Run: node --test integrations/homeassistant/tests/js/
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = path.join(__dirname, "..", "..", "custom_components",
                      "ai_terminal", "www", "panel.js");
function load() {
  const ctx = { module: { exports: {} }, console };
  vm.runInNewContext(fs.readFileSync(SRC, "utf8"), ctx, { filename: SRC });
  return ctx.module.exports;
}
const L = load();
// the vm sandbox is another realm: compare plain JSON, not prototypes
const eq = (a, b) => assert.deepEqual(JSON.parse(JSON.stringify(a)), b);

test("markdown escapes html and refuses script urls", () => {
  const h = L.md("<script>alert(1)</script> & [x](javascript:alert(1))");
  assert.ok(!h.includes("<script"));
  assert.ok(h.includes("&lt;script&gt;"));
  assert.ok(h.includes("&amp;"));
  assert.ok(!/href="javascript/i.test(h));
});

test("markdown: fences, inline code, emphasis, links, lists, headings", () => {
  const h = L.md("# Title\n\nsome **bold** and *it* and `a**b**c`\n\n" +
                 "```py\nprint('<x>')\n```\n\n- one\n- two\n\n" +
                 "see [docs](https://ex.com/a?b=1&c=2)");
  assert.match(h, /<h\d[^>]*>Title<\/h\d>/);
  assert.match(h, /<strong>bold<\/strong>/);
  assert.match(h, /<em>it<\/em>/);
  // inline code is literal: no emphasis inside
  assert.match(h, /<code>a\*\*b\*\*c<\/code>/);
  assert.match(h, /<pre[^>]*>[\s\S]*print\('&lt;x&gt;'\)[\s\S]*<\/pre>/);
  assert.match(h, /data-lang="py"/);
  assert.match(h, /<ul>\s*<li>one<\/li>\s*<li>two<\/li>\s*<\/ul>/);
  assert.match(h, /<a href="https:\/\/ex\.com\/a\?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">docs<\/a>/);
});

test("sse parser holds partial frames, skips pings, joins data lines", () => {
  const st = L.sseState();
  eq(L.sseFeed(st, 'data: {"type":"a","prop'), []);
  const evs = L.sseFeed(st, 'erties":{}}\n\n: ping\n\ndata: {"type":\ndata: "b"}\n\ndata: bad\n\n');
  eq(evs.map((e) => e.type), ["a", "b"]);
  eq(L.sseFeed(st, "data: {\"type\":\"c\"}\r\n\r\n").map((e) => e.type), ["c"]);
});

test("speaker line is hidden", () => {
  assert.equal(L.stripSpeaker("[HA user: Dmitrii]\nhello"), "hello");
  assert.equal(L.stripSpeaker("[HA user: Dmitrii]"), "");
  assert.equal(L.stripSpeaker("plain"), "plain");
});

function ev(type, properties) { return { id: "evt", type, properties }; }

test("reducer: rows, parts, deltas, status - own session only", () => {
  const st = L.chatState("s1");
  assert.equal(L.applyEvent(st, ev("message.updated",
    { sessionID: "s2", info: { id: "m0", sessionID: "s2", role: "user" } })), false);
  L.applyEvent(st, ev("message.updated", { sessionID: "s1",
    info: { id: "m1", sessionID: "s1", role: "user", time: { created: 1 } } }));
  L.applyEvent(st, ev("message.part.updated", { sessionID: "s1",
    part: { id: "p1", sessionID: "s1", messageID: "m1", type: "text", text: "hi" } }));
  L.applyEvent(st, ev("message.updated", { sessionID: "s1",
    info: { id: "m2", sessionID: "s1", role: "assistant", time: { created: 2 } } }));
  L.applyEvent(st, ev("message.part.updated", { sessionID: "s1",
    part: { id: "p2", sessionID: "s1", messageID: "m2", type: "text", text: "" } }));
  L.applyEvent(st, ev("message.part.delta", { sessionID: "s1",
    messageID: "m2", partID: "p2", field: "text", delta: "Hel" }));
  L.applyEvent(st, ev("message.part.delta", { sessionID: "s1",
    messageID: "m2", partID: "p2", field: "text", delta: "lo" }));
  eq(st.rows.map((r) => r.info.id), ["m1", "m2"]);
  assert.equal(st.rows[1].parts[0].text, "Hello");
  // a later full part.updated replaces the streamed text
  L.applyEvent(st, ev("message.part.updated", { sessionID: "s1",
    part: { id: "p2", sessionID: "s1", messageID: "m2", type: "text", text: "Hello!" } }));
  assert.equal(st.rows[1].parts.length, 1);
  assert.equal(st.rows[1].parts[0].text, "Hello!");
  L.applyEvent(st, ev("session.status", { sessionID: "s1",
    status: { type: "busy", queued: true, depth: 2, elapsed_ms: 4000 } }));
  assert.equal(st.status.type, "busy");
  assert.equal(st.status.depth, 2);
  L.applyEvent(st, ev("session.idle", { sessionID: "s1" }));
  assert.equal(st.status.type, "idle");
});

test("reducer: a delta for an unseen part/message still lands", () => {
  const st = L.chatState("s1");
  L.applyEvent(st, ev("message.part.delta", { sessionID: "s1",
    messageID: "mX", partID: "pX", field: "text", delta: "early" }));
  assert.equal(st.rows.length, 1);
  assert.equal(st.rows[0].info.role, "assistant");
  assert.equal(st.rows[0].parts[0].text, "early");
});

test("reducer: history load replaces rows and keeps order", () => {
  const st = L.chatState("s1");
  L.loadRows(st, [
    { info: { id: "b", role: "assistant", time: { created: 5 } }, parts: [] },
    { info: { id: "a", role: "user", time: { created: 3 } }, parts: [] },
  ]);
  eq(st.rows.map((r) => r.info.id), ["a", "b"]);
});

test("tool cards: status, pretty input, lane refusals", () => {
  const done = L.toolView({ type: "tool", tool: "entity_link",
    state: { status: "completed", input: { eid: "x", commands: ["uname -a"] },
             output: "linked 1", title: "link x" },
    time: { start: 1000, end: 3500 } });
  assert.equal(done.name, "entity_link");
  assert.equal(done.status, "completed");
  assert.match(done.input, /"commands": \[/);
  assert.equal(done.output, "linked 1");
  assert.equal(done.ms, 2500);
  const refused = L.toolView({ type: "tool", tool: "entity_link",
    state: { status: "completed", input: {},
             output: "Refused: the operator lane cannot link commands" } });
  assert.equal(refused.status, "refused");
  const err = L.toolView({ type: "tool", tool: "x", state: { status: "error", error: "boom" } });
  assert.equal(err.status, "error");
  assert.equal(err.output, "boom");
});

test("stick-to-bottom rule", () => {
  assert.equal(L.shouldStick({ scrollTop: 900, scrollHeight: 1000, clientHeight: 60 }), true);
  assert.equal(L.shouldStick({ scrollTop: 100, scrollHeight: 1000, clientHeight: 60 }), false);
});

test("elapsed + usage formatting", () => {
  assert.equal(L.fmtElapsed(4200), "4s");
  assert.equal(L.fmtElapsed(65000), "1m 05s");
  assert.equal(L.fmtUsage({ tokens: { input: 1234, output: 56, reasoning: 0 },
                            time: { created: 1000, completed: 4500 } }),
               "in 1.2k · out 56 · 3.5s");
  assert.equal(L.fmtUsage({}), "");
});

test("the model picker lists what membrane sees on Ollama, nothing else", () => {
  const rows = [{ id: "qwen3:8b", owned_by: "membrane->ollama" }, { id: "glm-5.3-flash:cloud", owned_by: "membrane->ollama" },
                { id: "autoselect", owned_by: "membrane-router" }, { id: "openai/gpt-5", owned_by: "membrane->openrouter" },
                { id: "@explore", owned_by: "membrane->ollama" }];
  assert.deepEqual(JSON.parse(JSON.stringify(L.ollamaModels(rows))), ["qwen3:8b", "glm-5.3-flash:cloud"]);
  assert.deepEqual(JSON.parse(JSON.stringify(L.ollamaModels(null))), []);
});

test("a pick rides the message; back to the seat default undoes a session pick", () => {
  const j = (x) => JSON.parse(JSON.stringify(x));
  assert.deepEqual(j(L.modelField("qwen3:8b", "", "glm-5.3-flash:cloud")), { providerID: "membrane", modelID: "qwen3:8b" });
  assert.equal(L.modelField("", "", "glm-5.3-flash:cloud"), null);
  assert.equal(L.modelField("", "glm-5.3-flash:cloud", "glm-5.3-flash:cloud"), null);
  assert.deepEqual(j(L.modelField("", "qwen3:8b", "glm-5.3-flash:cloud")), { providerID: "membrane", modelID: "glm-5.3-flash:cloud" });
});

// MEMBRANE'S OWN VOICE (2026-10-09): the third participant's line rides
// beside the user message it answered; the teaching controls render.
test("voice: the reducer keys the line by the user message it answered", () => {
  const st = L.chatState("s1");
  const ev = (type, properties) => ({ type, properties });
  assert.equal(L.applyEvent(st, ev("membrane.voice", { sessionID: "s1",
    line: { id: "vox_1", mid: "msg_u1", text: "Battery is fine." } })), true);
  assert.equal(st.voice.msg_u1.text, "Battery is fine.");
  assert.ok(st.dirty.has("msg_u1"));
  L.applyEvent(st, ev("membrane.voice", { sessionID: "s1", line: { id: "vox_1", mid: "msg_u1", closeness: 0.71 } }));
  assert.equal(st.voice.msg_u1.text, "Battery is fine.");      // the grade merges in
  assert.equal(st.voice.msg_u1.closeness, 0.71);
  assert.equal(L.applyEvent(st, ev("membrane.voice", { sessionID: "s2", line: { id: "x", mid: "m" } })), false);
});

test("voice: history load and the bubble", () => {
  const st = L.chatState("s1");
  L.loadVoice(st, [{ id: "vox_1", mid: "msg_u1", text: "<b>hi</b>", closeness: 0.5, vote: "up" }]);
  const h = L.voiceHtml(st.voice.msg_u1, false);
  assert.ok(h.includes("&lt;b&gt;hi"));                       // escaped
  assert.ok(h.includes("50%"));
  assert.ok(/vbtn on" data-vvote="up"/.test(h));
  assert.ok(!h.includes("vedit\""));
  assert.ok(L.voiceHtml(st.voice.msg_u1, true).includes("vteach"));
  assert.equal(L.voiceHtml(null), "");
});

test("auto-chat: the reducer tracks the mode; a voice turn shows its line", () => {
  const st = L.chatState("s1");
  assert.equal(L.applyEvent(st, { type: "membrane.auto", properties: { sessionID: "s1", on: true, rounds: 2 } }), true);
  eq(st.auto, { on: true, rounds: 2, mode: "explore" });
  assert.equal(L.voiceTurnText("[membrane voice] membrane's own voice said: «Battery is fine.»\nAnswer it"), "Battery is fine.");
});

test("voice: the bubble names the mode that spoke (the recall/walk A/B)", () => {
  assert.ok(L.voiceHtml({ id: "v1", text: "Hi.", mode: "recall" }).includes("· recall"));
  assert.ok(!L.voiceHtml({ id: "v2", text: "Hi." }).includes("vmode"));
});

test("a fresh tab on the default dashboard lands on membrane home; deep links and HA-chosen tabs don't", () => {
  const land = (p, ha) => L.membraneLanding(p, ha);
  for (const p of ["/", "/lovelace", "/lovelace/", "/lovelace/0", "/lovelace/default_view", "/home"]) assert.ok(land(p, false), p);
  for (const p of ["/config/integrations", "/lovelace-boat/0", "/lovelace/0/edit", "/history", "/membrane", "/ai-terminal"]) assert.ok(!land(p, false), p);
  assert.ok(!land("/", true));
});

test("the reply to speak: the newest finished assistant text, frames and markdown out", () => {
  const row = (id, role, parts, done = true) => ({ info: { id, role, time: done ? { completed: 1 } : {} }, parts });
  const rows = [row("u1", "user", [{ type: "text", text: "hi" }]),
    row("a1", "assistant", [{ type: "reasoning", text: "thinking" }, { type: "text", text: "**Sure.** The lights are `on`.\nmembrane: learn that x" },
      { type: "tool", text: "ignored" }])];
  eq(L.replyToSpeak(rows), { id: "a1", text: "Sure. The lights are on." });
  assert.equal(L.replyToSpeak([...rows, row("a2", "assistant", [{ type: "text", text: "half" }], false)]), null);
  assert.equal(L.replyToSpeak([...rows, row("u2", "user", [{ type: "text", text: "[HA user: D]\nand?" }])]), null);
  // membrane's own voice line lands as a user row DURING the turn: it doesn't hide the reply
  const voiced = [row("u3", "user", [{ type: "text", text: "[HA user: D]\nhi" }]),
    row("v1", "user", [{ type: "text", text: "[membrane voice] hello there" }]),
    row("a3", "assistant", [{ type: "text", text: "Hi Dmitrii." }])];
  eq(L.replyToSpeak(voiced), { id: "a3", text: "Hi Dmitrii." });
  eq(L.replyToSpeak([voiced[0], voiced[2], voiced[1]]), { id: "a3", text: "Hi Dmitrii." });
});

test("speakText: what gets said aloud, frames and markdown out, membrane's own tag off", () => {
  assert.equal(L.speakText("**Done.** The `light` is on.\nmembrane: learn that x"), "Done. The light is on.");
  assert.equal(L.speakText("[membrane voice] the room feels quiet"), "the room feels quiet");
  assert.equal(L.speakText("see [the docs](http://x)"), "see the docs");
});

test("membrane's framed auto-chat line: only its own words are spoken", () => {
  const framed = "[membrane voice] membrane's own voice (the field learning to speak -- not the operator) said: «the lights hum before the dark»";
  assert.equal(L.speakText(L.voiceTurnText(framed)), "the lights hum before the dark");
});

test("membrane's guess of Argus's actions rides its voice line, scored", () => {
  const line = { id: "v1", text: "x", guess: { tools: [["entity.read", 0.7], ["find.memory", 0.2]], ents: [["ha:light.cabin", 1, "named"]] },
                 guess_score: { tool1: true, tool3: true, ent3: false }, did: { tools: ["entity.read"], ents: ["ha:light.aft"] } };
  const h = L.guessHtml(line);
  assert.ok(h.includes("🔮 entity.read, find.memory ✓ · ha:light.cabin ✗"), h);
  assert.ok(h.includes("he did: entity.read on ha:light.aft"), h);
  assert.equal(L.guessHtml({ text: "no guess" }), "");
  assert.ok(L.guessHtml({ guess: { tools: [["(none)", 1]], ents: [] }, guess_score: { tool1: false, tool3: false } }).includes("(none) ✗"));
});
