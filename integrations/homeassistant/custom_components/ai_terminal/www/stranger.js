// The login page's background (2026-10-10): the visitor is a stranger on
// membrane's field, and this draws the small, label-free piece of the field
// around it -- shape, masses, edges, pulses. The login form waits behind a
// "login" button in the top-right corner.
//
// Injected by nginx into HA's /auth/authorize page. Talks only to
// /api/ai_terminal/stranger/* (numbers out, shape in). If anything fails the
// page stays a plain dark backdrop and the form still opens.
(() => {
  "use strict";
  if (window.__stranger) return;
  window.__stranger = true;

  const API = "/api/ai_terminal/stranger/";
  const VIEW_EVERY_MS = 3000;
  const FLUSH_EVERY_MS = 2000;
  // mirrors membrane/strangers.py: cube units per pixel / per event, over the window radius
  const STEP_W = 0.00015 / 0.1;
  const MAX_STEP_W = 0.004 / 0.1;

  // ---- the form behind a button -------------------------------------------
  const css = document.createElement("style");
  css.textContent = `
    :root { --sg-bg: #05070d; --sg-node: 150, 200, 255; --sg-edge: 120, 160, 220; --sg-me: 255, 210, 120;
            --sg-btn-bg: rgba(20, 26, 40, .72); --sg-btn-fg: #dfe7f5; --sg-btn-bd: rgba(160, 190, 240, .35); }
    @media (prefers-color-scheme: light) {
      :root { --sg-bg: #eef2f8; --sg-node: 40, 70, 130; --sg-edge: 70, 100, 160; --sg-me: 200, 120, 20;
              --sg-btn-bg: rgba(255, 255, 255, .8); --sg-btn-fg: #1b2433; --sg-btn-bd: rgba(40, 70, 130, .3); }
    }
    html, body { background: var(--sg-bg) !important; }
    #sg-canvas { position: fixed; inset: 0; width: 100vw; height: 100vh; z-index: 0; display: block; }
    html:not(.sg-open) ha-authorize { display: none !important; }
    html.sg-open ha-authorize { position: relative; z-index: 2; }
    #sg-login { position: fixed; top: 16px; right: 16px; z-index: 3; font: 500 14px/1 system-ui, sans-serif;
                letter-spacing: .04em; padding: 10px 16px; border-radius: 999px; cursor: pointer;
                background: var(--sg-btn-bg); color: var(--sg-btn-fg); border: 1px solid var(--sg-btn-bd);
                backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }
    #sg-login:focus-visible { outline: 2px solid rgb(var(--sg-me)); outline-offset: 2px; }
  `;
  document.head.appendChild(css);

  const btn = document.createElement("button");
  btn.id = "sg-login";
  btn.type = "button";
  btn.textContent = "login";
  btn.setAttribute("aria-expanded", "false");
  const setOpen = (open) => {
    document.documentElement.classList.toggle("sg-open", open);
    btn.textContent = open ? "close" : "login";
    btn.setAttribute("aria-expanded", String(open));
    if (open) {
      setTimeout(() => {
        const host = document.querySelector("ha-authorize");
        const f = host && (host.shadowRoot || host).querySelector("input, ha-textfield, button");
        if (f && f.focus) f.focus();
      }, 50);
    }
  };
  btn.addEventListener("click", () => setOpen(!document.documentElement.classList.contains("sg-open")));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setOpen(false);
    else if (!document.documentElement.classList.contains("sg-open") && e.key.length === 1 && !e.ctrlKey && !e.metaKey) setOpen(true);
  });
  // a failed login comes back with an error shown in the form: keep it visible
  if (/[?&]error=/.test(location.search)) setOpen(true);

  const canvas = document.createElement("canvas");
  canvas.id = "sg-canvas";
  canvas.setAttribute("aria-hidden", "true");
  const mount = () => { document.body.prepend(canvas); document.body.appendChild(btn); };
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  // ---- the stranger --------------------------------------------------------
  let token = null, alive = true;
  let nodes = [];          // {p:[x,y,z], m, a (alpha), glow, born}
  let edges = [];          // [i, j]
  let fading = [];         // the previous window, fading out
  let meGlow = 0;
  let offset = [0, 0, 0];  // local drift since the last view (window units)
  const acc = { move: 0, dx: 0, dy: 0, click: 0 };

  const get = (op, params) => {
    const q = new URLSearchParams(params || {});
    return fetch(API + op + (q.toString() ? "?" + q : ""), { credentials: "omit", keepalive: op === "leave" })
      .then((r) => r.json()).catch(() => ({ ok: false }));
  };

  const adopt = (v) => {
    if (!v || !v.ok) return;
    fading = nodes.map((n) => ({ ...n }));
    const now = performance.now();
    nodes = (v.p || []).map((p, i) => ({ p, m: (v.m || [])[i] || 0, a: 0, glow: 0, born: now }));
    edges = (v.e || []).filter(([a, b]) => a < nodes.length && b < nodes.length).map(([a, b]) => [a, b, 1]);
    for (const [a, b] of v.n || []) if (a < nodes.length && b < nodes.length) edges.push([a, b, 0.45]);
    for (const i of v.pulse || []) if (nodes[i]) nodes[i].glow = 1;
    if (v.me_pulse) meGlow = 1;
    offset = [0, 0, 0];
  };

  const pulsesOnly = (v) => {
    if (!v || !v.ok) return;
    for (const i of v.pulse || []) if (nodes[i]) nodes[i].glow = 1;
    if (v.me_pulse) meGlow = 1;
  };

  let lastShape = "";
  const poll = async () => {
    if (!token || !alive || document.hidden) return;
    const v = await get("view", { t: token });
    if (!v.ok) { if (v.error === "gone") token = null; return; }
    const shape = (v.p || []).length + ":" + JSON.stringify((v.p || [])[0] || []);
    if (shape !== lastShape) { lastShape = shape; adopt(v); } else pulsesOnly(v);
  };

  const flush = () => {
    if (!token || document.hidden) return;
    if (acc.move) {
      // mirror the seat's step so the window glides before the next view lands
      let s = [acc.dx * STEP_W, -acc.dy * STEP_W, (acc.dx - acc.dy) * STEP_W * 0.3];
      const norm = Math.max(...s.map(Math.abs));
      if (norm > MAX_STEP_W) s = s.map((x) => (x * MAX_STEP_W) / norm);
      offset = offset.map((o, k) => o - s[k]);
      get("event", { t: token, kind: "move", n: acc.move, dx: Math.round(acc.dx), dy: Math.round(acc.dy) });
    }
    if (acc.click) get("event", { t: token, kind: "click", n: acc.click });
    acc.move = acc.dx = acc.dy = acc.click = 0;
  };

  // the view turns only while the pointer moves: motion feeds a spin that
  // eases out when the pointer rests
  let lastXY = null, pointer = [0, 0], spin = [0, 0];
  const onMove = (x, y) => {
    pointer = [(x / innerWidth) * 2 - 1, (y / innerHeight) * 2 - 1];
    if (lastXY) {
      const dx = x - lastXY[0], dy = y - lastXY[1];
      acc.dx += dx; acc.dy += dy; acc.move += 1;
      spin = [spin[0] + dx * 0.00035, spin[1] + dy * 0.00025];
    }
    lastXY = [x, y];
  };
  addEventListener("pointermove", (e) => onMove(e.clientX, e.clientY), { passive: true });
  addEventListener("pointerdown", () => { acc.click += 1; askTilt(); }, { passive: true });

  // ---- tilt (2026-10-10): on a phone the device's tilt turns the view too
  // (tilt right turns right, toward you looks from above -- membrane home's
  // convention); iOS asks once, on the first tap
  let tiltBase = null, tiltTarget = null, tiltAsked = false;
  const tiltAxes = (beta, gamma, angle) => {
    const a = (((angle || 0) % 360) + 360) % 360;
    if (a === 90) return [beta, -gamma];
    if (a === 270) return [-beta, gamma];
    if (a === 180) return [-gamma, -beta];
    return [gamma, beta];
  };
  const onTilt = (e) => {
    if (e.beta == null || e.gamma == null) return;
    const [lr, fb] = tiltAxes(e.beta, e.gamma, (screen.orientation && screen.orientation.angle) || window.orientation || 0);
    if (!tiltBase) tiltBase = { lr, fb, yaw, pitch };
    const d = Math.PI / 180;
    tiltTarget = { yaw: tiltBase.yaw + (lr - tiltBase.lr) * d * 1.2,
                   pitch: Math.max(-0.6, Math.min(0.6, tiltBase.pitch + (fb - tiltBase.fb) * d * 1.2)) };
  };
  const touchDevice = (navigator.maxTouchPoints || 0) > 0;
  if (touchDevice && typeof window.DeviceOrientationEvent !== "undefined" &&
      typeof window.DeviceOrientationEvent.requestPermission !== "function") {
    addEventListener("deviceorientation", onTilt);            // Android: no prompt needed
  }
  function askTilt() {
    const DOE = window.DeviceOrientationEvent;
    if (tiltAsked || !touchDevice || !DOE || typeof DOE.requestPermission !== "function") return;
    tiltAsked = true;                                          // iOS: once, from a tap
    DOE.requestPermission().then((r) => { if (r === "granted") addEventListener("deviceorientation", onTilt); }).catch(() => {});
  }
  document.addEventListener("visibilitychange", () => {
    if (token) get("event", { t: token, kind: document.hidden ? "blur" : "focus", n: 1 });
  });
  // a reload is the same stranger: the page only says it went quiet (the
  // seat lets a quiet stranger go after 2 min, a hidden one after 15)
  addEventListener("pagehide", () => { alive = false; if (token) get("event", { t: token, kind: "blur", n: 1 }); });

  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } };
  let vid = store("sg.v");
  if (!vid || !/^[0-9a-f]{32}$/.test(vid)) {
    const b = new Uint8Array(16); crypto.getRandomValues(b);
    vid = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    store("sg.v", vid);
  }
  const start = (r) => {
    if (!r.ok) return;
    token = r.t;
    poll();
    setInterval(poll, VIEW_EVERY_MS);
    setInterval(flush, FLUSH_EVERY_MS);
  };
  get("visit", { v: vid }).then(start);

  // ---- drawing (canvas 2D: ~150 nodes, no library) ---------------------------
  const ctx = canvas.getContext("2d");
  let W = 0, H = 0, dpr = 1;
  const resize = () => {
    dpr = Math.min(2, devicePixelRatio || 1);
    W = canvas.width = Math.floor(innerWidth * dpr);
    H = canvas.height = Math.floor(innerHeight * dpr);
  };
  addEventListener("resize", resize);
  resize();

  const cssRGB = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "150, 200, 255";
  let colors = null;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  let yaw = 0, pitch = 0, tilt = [0, 0];
  const project = (p) => {
    const x0 = p[0] + offset[0], y0 = p[1] + offset[1], z0 = p[2] + offset[2];
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    let x = x0 * cy - z0 * sy, z = x0 * sy + z0 * cy, y = y0;
    const cx = Math.cos(tilt[1]), sx = Math.sin(tilt[1]);
    const y2 = y * cx - z * sx; z = y * sx + z * cx; y = y2;
    const cam = 2.2, f = Math.min(W, H) * 0.9;
    const d = cam - z;
    if (d < 0.15) return null;
    return [W / 2 + (x * f) / d, H / 2 + (y * f) / d, 1 / d];
  };

  let last = performance.now();
  const frame = (now) => {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (!colors) colors = { node: cssRGB("--sg-node"), edge: cssRGB("--sg-edge"), me: cssRGB("--sg-me") };
    const k = reduce ? 0.3 : 1;
    yaw += spin[0] * k;
    pitch = Math.max(-0.6, Math.min(0.6, pitch + spin[1] * k));
    if (tiltTarget) {                                          // the phone's tilt eases the view toward it
      const kt = 1 - Math.exp(-dt * 6);
      yaw += (tiltTarget.yaw - yaw) * kt;
      pitch += (tiltTarget.pitch - pitch) * kt;
    }
    const ease = Math.exp(-dt * 3);
    spin = [spin[0] * ease, spin[1] * ease];
    tilt = [0, pitch];
    ctx.clearRect(0, 0, W, H);

    const draw = (set, alphaOf) => {
      const pts = set.map((n) => project(n.p));
      ctx.lineWidth = dpr;
      for (const [a, b, w] of set === nodes ? edges : []) {
        const pa = pts[a], pb = pts[b];
        if (!pa || !pb) continue;
        const al = Math.min(alphaOf(set[a]), alphaOf(set[b])) * 0.22 * w * Math.min(pa[2], pb[2]) * 2;
        ctx.strokeStyle = `rgba(${colors.edge}, ${al.toFixed(3)})`;
        ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke();
        if (set[a].glow > 0.05 || set[b].glow > 0.05) {
          ctx.strokeStyle = `rgba(${colors.me}, ${(Math.max(set[a].glow, set[b].glow) * 0.5).toFixed(3)})`;
          ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke();
        }
      }
      set.forEach((n, i) => {
        const p = pts[i];
        if (!p) return;
        const r = (1.2 + Math.log2(2 + n.m) * 0.55) * dpr * p[2] * 2;
        const al = alphaOf(n) * Math.min(1, p[2] * 1.6);
        ctx.fillStyle = `rgba(${colors.node}, ${al.toFixed(3)})`;
        ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, Math.PI * 2); ctx.fill();
        if (n.glow > 0.02) {
          ctx.fillStyle = `rgba(${colors.me}, ${(n.glow * 0.8).toFixed(3)})`;
          ctx.beginPath(); ctx.arc(p[0], p[1], r * (1.5 + (1 - n.glow) * 3), 0, Math.PI * 2); ctx.fill();
          n.glow = Math.max(0, n.glow - dt * 0.8);
        }
      });
    };
    for (const n of fading) n.a = Math.max(0, n.a - dt * 1.5);
    fading = fading.filter((n) => n.a > 0.01);
    draw(fading, (n) => n.a);
    for (const n of nodes) n.a = Math.min(1, n.a + dt * 1.2);
    draw(nodes, (n) => n.a);

    // the stranger itself: at the centre, where the camera follows it
    const me = project([-offset[0], -offset[1], -offset[2]]);
    if (me) {
      const r = 3.2 * dpr * (1 + meGlow * 1.5);
      ctx.fillStyle = `rgba(${colors.me}, ${(0.85).toFixed(2)})`;
      ctx.beginPath(); ctx.arc(me[0], me[1], r, 0, Math.PI * 2); ctx.fill();
      meGlow = Math.max(0, meGlow - dt * 0.7);
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
})();
