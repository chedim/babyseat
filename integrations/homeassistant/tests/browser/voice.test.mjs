// membrane home's in-page voice, in a real Chrome with a fake microphone
// (2026-10-10). The microphone plays a WAV made by macOS `say`; the test
// passes when the transcribed command reaches the chat -- Vosk loading, the
// wake word and the dictation, all inside the page.
//   cd tests/browser && npm install && node --test voice.test.mjs   (macOS + Chrome)
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, firefox } from "playwright-core";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WWW = path.join(HERE, "..", "..", "custom_components", "ai_terminal", "www");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
// BROWSER=firefox runs them in Playwright's Firefox (the operator's browser)
const BROWSER = process.env.BROWSER || "chrome";
const TYPES = { ".js": "application/javascript", ".mjs": "application/javascript", ".wasm": "application/wasm", ".gz": "application/gzip", ".html": "text/html" };

/** A WAV of `text` spoken by macOS, padded with silence (Chrome loops it). */
function spoken(text, padS = 2) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "mh-voice-"));
  const aiff = path.join(dir, "s.aiff"), wav = path.join(dir, "s.wav");
  execFileSync("say", ["-v", "Samantha", "-o", aiff, text]);
  execFileSync("afconvert", ["-f", "WAVE", "-d", "LEI16@48000", "-c", "1", aiff, wav]);
  const b = fs.readFileSync(wav);
  const dataAt = b.indexOf("data") + 8, pcm = b.subarray(dataAt, dataAt + b.readUInt32LE(dataAt - 4));
  const pad = Buffer.alloc(48000 * 2 * padS), body = Buffer.concat([pad, pcm, pad, pad]);
  const h = Buffer.alloc(44);
  h.write("RIFF", 0); h.writeUInt32LE(36 + body.length, 4); h.write("WAVEfmt ", 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(48000, 24); h.writeUInt32LE(96000, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write("data", 36); h.writeUInt32LE(body.length, 40);
  fs.writeFileSync(wav, Buffer.concat([h, body]));
  return wav;
}

function serve(wav) {
  const srv = http.createServer((req, res) => {
    const u = new URL(req.url, "http://x");
    let file = null;
    if (u.pathname === "/") file = path.join(HERE, "harness.html");
    else if (u.pathname === "/fixture.wav") file = wav;
    else if (u.pathname.startsWith("/api/ai_terminal/www/")) file = path.join(WWW, path.basename(u.pathname));
    if (!file || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || (file.endsWith(".wav") ? "audio/wav" : "application/octet-stream") });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((ok) => srv.listen(0, "127.0.0.1", () => ok(srv)));
}

async function withPage(wav, fn, query = "") {
  const srv = await serve(wav);
  const browser = BROWSER === "firefox"
    ? await firefox.launch({ headless: true, firefoxUserPrefs: { "media.autoplay.default": 0, "media.navigator.permission.disabled": true } })
    : await chromium.launch({ executablePath: CHROME, headless: true, args: ["--autoplay-policy=no-user-gesture-required"] });
  try {
    const ctx = await browser.newContext(BROWSER === "firefox" ? {} : { permissions: ["microphone"] });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => console.log("pageerror:", e.message));
    await page.goto("http://127.0.0.1:" + srv.address().port + "/" + query);
    await page.waitForFunction(() => window.__ready, null, { timeout: 30000 });
    await fn(page);
  } finally { await browser.close(); srv.close(); }
}

const said = (page, ms) => page.waitForFunction(() => window.__said.length > 0, null, { timeout: ms })
  .then(() => page.evaluate(() => window.__said.slice()));

test("👂 on the device: 'Hey Argus, …' wakes it and the command reaches the chat", { timeout: 240000 }, async () => {
  await withPage(spoken("Hey Argus, turn on the deck lights."), async (page) => {
    await page.evaluate(() => { localStorage.setItem("membrane.home.stt", "device"); window.__home._engine = "device"; });
    await page.evaluate(() => window.__home.shadowRoot.querySelector(".ear").click());
    const out = await said(page, 200000);
    console.log("said:", out);
    assert.ok(out.some((t) => /deck|lights?|turn on/i.test(t)), JSON.stringify(out));
    assert.ok(!out.some((t) => /\b(argus|are gus|our gus|angus|august)\b/i.test(t.split(" ").slice(0, 2).join(" "))), "the name is stripped");
  });
});

test("👂 a seat that named itself: 'Hey Juniper, …' wakes it (standalone mode)", { timeout: 240000 }, async () => {
  const NAME = process.env.WAKE_NAME || "Juniper";
  await withPage(spoken("Hey " + NAME + ", turn on the deck lights."), async (page) => {
    await page.evaluate(() => { localStorage.setItem("membrane.home.stt", "device"); window.__home._engine = "device"; });
    await page.evaluate(() => window.__home.shadowRoot.querySelector(".ear").click());
    const out = await said(page, 200000);
    console.log("said:", out);
    assert.ok(out.some((t) => /deck|lights?|turn on/i.test(t)), JSON.stringify(out));
    assert.ok(!out.some((t) => new RegExp("^\\s*(hey\\s+)?" + NAME + "\\b", "i").test(t)), "the name is stripped");
  }, "?name=" + encodeURIComponent(NAME));
});

test("🔒 on the device: hands-free dictation ends itself on a pause", { timeout: 240000 }, async () => {
  await withPage(spoken("What is the battery level?"), async (page) => {
    await page.evaluate(() => { localStorage.setItem("membrane.home.stt", "device"); window.__home._engine = "device"; });
    await page.evaluate(() => window.__home.shadowRoot.querySelector(".lock").click());
    const out = await said(page, 200000);
    console.log("said:", out);
    assert.ok(out.some((t) => /battery|level/i.test(t)), JSON.stringify(out));
  });
});

test("👂 stays quiet for speech that doesn't call it", { timeout: 240000 }, async () => {
  await withPage(spoken("Turn on the deck lights, and what is the battery level?"), async (page) => {
    await page.evaluate(() => { localStorage.setItem("membrane.home.stt", "device"); window.__home._engine = "device"; });
    await page.evaluate(() => window.__home.shadowRoot.querySelector(".ear").click());
    await page.waitForFunction(() => window.__membraneVoice, null, { timeout: 60000 });
    await page.waitForTimeout(25000);                     // the loop plays it several times
    assert.deepEqual(await page.evaluate(() => window.__said.slice()), []);
  });
});

test("👂 while Argus speaks: his name cuts him off (barge-in)", { timeout: 240000 }, async () => {
  await withPage(spoken("Hey Argus, stop."), async (page) => {
    await page.evaluate(() => {
      localStorage.setItem("membrane.home.stt", "device");
      const el = window.__home;
      el._engine = "device";
      window.__hushed = 0;
      const hush = el._hushTts.bind(el);
      el._hushTts = () => { if (el._speaking) window.__hushed += 1; hush(); };
      el._speaking = true;                                 // as if a reply were being read aloud
      el.shadowRoot.querySelector(".ear").click();
    });
    await page.waitForFunction(() => window.__hushed > 0, null, { timeout: 200000 });
    assert.ok(await page.evaluate(() => window.__hushed) > 0);
  });
});

test("streamed replies are said sentence by sentence, the rest when the part closes", { timeout: 120000 }, async () => {
  await withPage(spoken("."), async (page) => {
    const said = await page.evaluate(() => {
      const chat = window.__home.shadowRoot.querySelector("ai-terminal-panel");
      const out = [];
      chat.addEventListener("ai-speak", (e) => out.push(e.detail.kind + ": " + e.detail.text));
      chat._chat = { sid: "s1", byId: {} };                 // the message itself not known yet (streaming)
      const ev = (type, props) => chat._announceSpeech({ type, properties: props }, props);
      ev("message.part.updated", { part: { id: "p1", messageID: "m1", type: "text", text: "", time: { start: 1 } } });
      for (const d of ["The deck lights ", "are on now. The batt", "ery sits at 92 percent", " and charging. Anything", " else?"])
        ev("message.part.delta", { partID: "p1", messageID: "m1", field: "text", delta: d });
      const mid = out.slice();
      ev("message.part.updated", { part: { id: "p1", messageID: "m1", type: "text", text: "The deck lights are on now. The battery sits at 92 percent and charging. Anything else?", time: { start: 1, end: 2 } } });
      ev("message.part.updated", { part: { id: "r1", messageID: "m1", type: "reasoning", text: "", time: { start: 1 } } });
      ev("message.part.delta", { partID: "r1", messageID: "m1", field: "text", delta: "I think. I should check. " });
      ev("message.part.updated", { part: { id: "u1", messageID: "m2", type: "text", text: "[HA user: D]\nhi there. how are you. fine.", time: { start: 1, end: 2 } } });
      return { mid, out };
    });
    assert.deepEqual(said.mid, ["argus: The deck lights are on now.", "argus: The battery sits at 92 percent and charging."]);
    assert.deepEqual(said.out, [...said.mid, "argus: Anything else?"]);                 // reasoning and the person's text never said
  });
});

test("with the stream LIVE, Argus's parts are said too -- and membrane's lines (2026-10-11)", { timeout: 120000 }, async () => {
  // the speech buffer shared the name `_live` with the stream's flag: once the
  // stream was up, true[partId] = ... threw and only membrane's lines spoke
  await withPage(spoken("."), async (page) => {
    const said = await page.evaluate(() => {
      const chat = window.__home.shadowRoot.querySelector("ai-terminal-panel");
      const out = [];
      chat.addEventListener("ai-speak", (e) => out.push(e.detail.kind + ": " + e.detail.text));
      chat._chat = { sid: "s1", byId: { m1: { info: { id: "m1", role: "assistant" } } } };
      chat._live = true;                                     // the stream is up
      const ev = (type, props) => chat._announceSpeech({ type, properties: props }, props);
      ev("message.part.updated", { part: { id: "p1", messageID: "m1", type: "text", text: "", time: { start: 1 } } });
      ev("message.part.delta", { partID: "p1", messageID: "m1", field: "text", delta: "The relay is off now. Checking the camera" });
      ev("message.part.updated", { part: { id: "p1", messageID: "m1", type: "text", text: "The relay is off now. Checking the camera next.", time: { start: 1, end: 2 } } });
      ev("membrane.voice", { line: { id: "v1", text: "The camera sleeps." } });
      return { out, live: chat._live };
    });
    assert.deepEqual(said.out, ["argus: The relay is off now.", "argus: Checking the camera next.", "membrane: The camera sleeps."]);
    assert.equal(said.live, true);                          // and the flag stays a flag
  });
});

test("👂 hybrid (the default): the wake word on the device, the words after it by whisper", { timeout: 240000 }, async () => {
  await withPage(spoken("Hey Argus, turn on the deck lights."), async (page) => {
    await page.evaluate(() => {
      localStorage.removeItem("membrane.home.stt");          // the default engine
      window.__sttCalls = 0;
      window.fakeHass.fetchWithAuth = async (path, opt) => {
        if (path === "/api/ai_terminal/stt") {
          window.__sttCalls += 1;
          return new Response(JSON.stringify({ text: "gus, turn on the deck lights." }), { status: 200 });
        }
        return new Response("{}", { status: 404 });
      };
      window.__home.hass = window.fakeHass;
      window.__home.shadowRoot.querySelector(".ear").click();
    });
    const out = await said(page, 200000);
    assert.deepEqual(out.slice(0, 1), ["turn on the deck lights."]);
    assert.ok(await page.evaluate(() => window.__sttCalls) >= 1);
  });
});

test("Argus first: his words cut membrane's babble and jump its queue", { timeout: 120000 }, async () => {
  await withPage(spoken("."), async (page) => {
    const order = await page.evaluate(async () => {
      const el = window.__home, log = [];
      el._voice = { name: "Test", lang: "en-US" };
      window.speechSynthesis.speak = (u) => { log.push("start " + u.text); const t = setTimeout(() => { log.push("end " + u.text); u.onend && u.onend(); }, 400); u._t = t; window.__cur = u; };
      window.speechSynthesis.cancel = () => { const u = window.__cur; if (u) { clearTimeout(u._t); log.push("cut " + u.text); u.onend && u.onend(); window.__cur = null; } };
      el._enqueueSpeech("membrane", "The room hums.");
      el._enqueueSpeech("membrane", "Lights drift.");
      await new Promise((r) => setTimeout(r, 120));
      el._enqueueSpeech("argus", "The battery is at 92 percent.");
      await new Promise((r) => setTimeout(r, 2500));
      return log;
    });
    const i = (s) => order.findIndex((x) => x === s);
    assert.ok(i("cut The room hums.") >= 0, JSON.stringify(order));                       // the playing babble was cut
    assert.ok(i("start The battery is at 92 percent.") < i("start Lights drift."), JSON.stringify(order));
  });
});

test("only voice:out is spoken: the chat and membrane's lines are not (2026-10-11)", { timeout: 120000 }, async () => {
  await withPage(spoken("."), async (page) => {
    const q = await page.evaluate(() => {
      const el = window.__home, chat = el.shadowRoot.querySelector("ai-terminal-panel"), got = [];
      el._ttsOn = true;
      el._enqueueSpeech = (kind, text) => got.push(kind + ": " + text);
      const fire = (type, detail) => chat.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
      fire("ai-speak", { kind: "membrane", text: "The room hums." });
      fire("ai-speak", { kind: "argus", text: "The chat's own text." });
      fire("ai-say", { text: "The battery is at 40 percent." });
      return got;
    });
    assert.deepEqual(q, ["argus: The battery is at 40 percent."]);
  });
});

test("an orb selected: the chat shows that mind, read-only; let go: back to the chat (2026-10-11)", { timeout: 120000 }, async () => {
  await withPage(spoken("."), async (page) => {
    const out = await page.evaluate(async () => {
      const home = window.__home, chat = home.shadowRoot.querySelector("ai-terminal-panel");
      const opened = [];
      chat._sid = "s-own";
      chat._open = async (sid) => { opened.push(sid); chat._sid = sid; };
      chat._api = async (url) => url.endsWith("/membrane/minds")
        ? { ok: true, data: { loops: { "loop-1": { sid: "ses_loop1" } }, thinker: [{ ts: 5, kind: "chat", about: "battery" }], dreamer: [] } }
        : url.endsWith("/session/ses_loop1/message")
          ? { ok: true, data: [{ info: { id: "m1", role: "assistant", time: { created: 1 } }, parts: [{ id: "p1", type: "text", text: "DONE read the battery" }] }] }
          : { ok: true, data: [] };
      home._get = async () => ({ loops: { "loop-1": { sid: "ses_loop1" } } });
      await home._selectMind("thinker");
      await new Promise((r) => setTimeout(r, 150));
      const thinker = { mind: chat.hasAttribute("mind"), text: chat._chat.rows.map((r) => r.parts[0].text).join("|"),
                        composer: getComputedStyle(chat.shadowRoot.querySelector(".composer")).display,
                        follow: home._mindFollow };
      await home._selectMind("loop-1");
      await new Promise((r) => setTimeout(r, 150));
      const loop = chat._chat.rows.map((r) => r.parts[0].text).join("|");
      await home._selectMind(null);
      return { thinker, loop, after: { mind: chat.hasAttribute("mind"), opened, follow: home._mindFollow } };
    });
    assert.deepEqual(out.thinker, { mind: true, text: "[chat] about: battery", composer: "none", follow: "thinker" });
    assert.equal(out.loop, "DONE read the battery");
    assert.deepEqual(out.after, { mind: false, opened: ["s-own"], follow: null });
  });
});

async function addressedPage(page, transcript) {
  await page.evaluate((tx) => {
    window.__sttCalls = 0;
    window.fakeHass.fetchWithAuth = async (path) => path === "/api/ai_terminal/stt"
      ? (window.__sttCalls += 1, new Response(JSON.stringify({ text: tx }), { status: 200 }))
      : new Response("{}", { status: 404 });
    const el = window.__home;
    el.hass = window.fakeHass; el._engine = "lan"; el._earMode = "addressed";
    el.shadowRoot.querySelector(".ear").click();
  }, transcript);
}

test("👂ᴬ addressed: his name mid-sentence makes the sentence a message", { timeout: 240000 }, async () => {
  await withPage(spoken("Could you check the battery level for me, Argus?"), async (page) => {
    await addressedPage(page, "Could you check the battery level for me, Argus?");
    const out = await said(page, 200000);
    assert.equal(out[0], "Could you check the battery level for me, Argus?");
  });
});

test("👂ᴬ addressed: a sentence that doesn't name him is never sent to be transcribed", { timeout: 240000 }, async () => {
  await withPage(spoken("Could you check the battery level for me, please?"), async (page) => {
    await addressedPage(page, "Could you check the battery level for me, please?");
    await page.waitForFunction(() => window.__membraneVoice, null, { timeout: 60000 });
    await page.waitForTimeout(25000);
    assert.deepEqual(await page.evaluate(() => [window.__said.length, window.__sttCalls]), [0, 0]);
  });
});


test("the seat's JS runs in the opted-in window: page, values and console back (2026-10-11)", { timeout: 120000 }, async () => {
  await withPage(spoken("."), async (page) => {
    const out = await page.evaluate(async () => {
      const chat = window.__home.shadowRoot.querySelector("ai-terminal-panel");
      const posted = [];
      chat._api = async (path, opt) => { posted.push({ path, body: JSON.parse(opt.body) }); return { ok: true, data: {} }; };
      chat._jsOn = true; chat._jsTab = "t1"; chat._sid = "s1";
      document.title = "the boat";
      await chat._jsRun({ id: "j1", tab: "t1", timeout: 5, code: "console.log('hi', {a: 1}); return 2 + 3" });
      await chat._jsRun({ id: "j2", tab: "t1", timeout: 5, code: "return document.title + ' / ' + (panel === arguments[2]) + ' / ' + !!hass" });
      await chat._jsRun({ id: "j3", tab: "t1", timeout: 1, code: "await new Promise(() => {})" });
      await chat._jsRun({ id: "j4", tab: "t1", timeout: 5, code: "throw new Error('nope')" });
      await chat._jsRun({ id: "j5", tab: "other", timeout: 5, code: "return 1" });     // not this tab's job
      chat._jsOn = false;
      await chat._jsRun({ id: "j6", tab: "t1", timeout: 5, code: "return 1" });       // opted out: nothing runs
      return posted.filter((p) => p.path.endsWith("/membrane/jsexec/result")).map((p) => p.body);
    });
    const by = Object.fromEntries(out.map((r) => [r.id, r]));
    assert.deepEqual(Object.keys(by).sort(), ["j1", "j2", "j3", "j4"]);
    assert.equal(by.j1.ok, true); assert.equal(by.j1.value, "5"); assert.deepEqual(by.j1.logs, ['hi {"a":1}']);
    assert.equal(by.j2.value, "the boat / true / true");                  // the window, the panel, the HA connection
    assert.equal(by.j3.ok, false); assert.match(by.j3.error, /timeout/);
    assert.equal(by.j4.ok, false); assert.match(by.j4.error, /nope/);
  });
});


test("on a phone the chat's menu opens, and HA's burger isn't doubled inside membrane home (2026-10-11)", { timeout: 120000 }, async () => {
  await withPage(spoken("."), async (page) => {
    await page.setViewportSize({ width: 390, height: 800 });
    const r = await page.evaluate(async () => {
      const chat = window.__home.shadowRoot.querySelector("ai-terminal-panel");
      chat.narrow = true;
      const sr = chat.shadowRoot, side = sr.querySelector(".side"), menu = sr.querySelector(".menu");
      const shown = () => getComputedStyle(side).display !== "none" && getComputedStyle(side).transform === "none";
      const before = shown();
      sr.querySelector(".sidetoggle").click();
      await new Promise((res) => setTimeout(res, 300));
      const after = shown();
      sr.querySelector(".scrim").click();
      await new Promise((res) => setTimeout(res, 300));
      return { before, after, closed: !shown(), burger: getComputedStyle(menu).display };
    });
    assert.deepEqual(r, { before: false, after: true, closed: true, burger: "none" });
    const scrolls = await page.evaluate(() => getComputedStyle(window.__home.shadowRoot.querySelector("ai-terminal-panel").shadowRoot.querySelector(".side")).overflowY);
    assert.equal(scrolls, "auto");                                         // a tall menu on a 4" screen scrolls
  });
});
