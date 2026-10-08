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
