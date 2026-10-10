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
import { chromium } from "playwright-core";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WWW = path.join(HERE, "..", "..", "custom_components", "ai_terminal", "www");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const TYPES = { ".js": "application/javascript", ".gz": "application/gzip", ".html": "text/html" };

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

function serve() {
  const srv = http.createServer((req, res) => {
    const u = new URL(req.url, "http://x");
    let file = null;
    if (u.pathname === "/") file = path.join(HERE, "harness.html");
    else if (u.pathname.startsWith("/api/ai_terminal/www/")) file = path.join(WWW, path.basename(u.pathname));
    if (!file || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((ok) => srv.listen(0, "127.0.0.1", () => ok(srv)));
}

async function withPage(wav, fn) {
  const srv = await serve();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: [
    "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    "--use-file-for-fake-audio-capture=" + wav, "--autoplay-policy=no-user-gesture-required"] });
  try {
    const ctx = await browser.newContext({ permissions: ["microphone"] });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => console.log("pageerror:", e.message));
    await page.goto("http://127.0.0.1:" + srv.address().port + "/");
    await page.waitForFunction(() => window.__ready, null, { timeout: 30000 });
    await fn(page);
  } finally { await browser.close(); srv.close(); }
}

const said = (page, ms) => page.waitForFunction(() => window.__said.length > 0, null, { timeout: ms })
  .then(() => page.evaluate(() => window.__said.slice()));

test("👂 on the device: 'Hey Argus, …' wakes it and the command reaches the chat", { timeout: 240000 }, async () => {
  await withPage(spoken("Hey Argus, turn on the deck lights."), async (page) => {
    await page.evaluate(() => { localStorage.setItem("membrane.home.stt", "device"); });
    await page.evaluate(() => window.__home.shadowRoot.querySelector(".ear").click());
    const out = await said(page, 200000);
    console.log("said:", out);
    assert.ok(out.some((t) => /deck|lights?|turn on/i.test(t)), JSON.stringify(out));
    assert.ok(!out.some((t) => /\b(argus|are gus|our gus|angus|august)\b/i.test(t.split(" ").slice(0, 2).join(" "))), "the name is stripped");
  });
});

test("🔒 on the device: hands-free dictation ends itself on a pause", { timeout: 240000 }, async () => {
  await withPage(spoken("What is the battery level?"), async (page) => {
    await page.evaluate(() => { localStorage.setItem("membrane.home.stt", "device"); });
    await page.evaluate(() => window.__home.shadowRoot.querySelector(".lock").click());
    const out = await said(page, 200000);
    console.log("said:", out);
    assert.ok(out.some((t) => /battery|level/i.test(t)), JSON.stringify(out));
  });
});

test("👂 stays quiet for speech that doesn't call it", { timeout: 240000 }, async () => {
  await withPage(spoken("Turn on the deck lights, and what is the battery level?"), async (page) => {
    await page.evaluate(() => { localStorage.setItem("membrane.home.stt", "device"); });
    await page.evaluate(() => window.__home.shadowRoot.querySelector(".ear").click());
    await page.waitForFunction(() => window.__membraneVoice, null, { timeout: 60000 });
    await page.waitForTimeout(25000);                     // the loop plays it several times
    assert.deepEqual(await page.evaluate(() => window.__said.slice()), []);
  });
});
