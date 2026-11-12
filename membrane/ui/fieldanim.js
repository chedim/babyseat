/* THE FIELD ANIMATION (#1115, #1134): the field's shape growing on a canvas -- ONE piece, a direction
 * (forward = the loading run, points appearing in their load order; reverse = the same run played
 * backwards, points leaving in reverse load order) and a colour state (coloured, or grey = the
 * field cannot answer). The boot door embeds this file inline in its page; the UI loads it with a
 * <script> tag; node requires it in the tests. Numbers and shapes only: it never holds, fetches or
 * shows a name or a word of the field (it renders when the seat is unreachable and without login).
 *
 *   const a = fieldAnim(canvas, {colors: {dot, line, pulse, bg}})   // "r,g,b" strings; bg null = transparent
 *   a.shape(d)       // /__boot/shape's numbers {n,p,m,o,e}, or none: a generated field
 *   a.set({dir: 1|-1, colour: 'coloured'|'grey', progress: 0..1, recall: bool, ready: bool, instant: bool})
 *   a.start() / a.stop()   // the requestAnimationFrame loop;  a.tick(nowMs) one step;  a.view() what is drawn
 *
 *   new SeatWatch({fails}).note({kind: 'down'|'loading'|'up', d})  -> {mode: alive|offline|booting, progress, reload}
 *   SeatWatch.probe(fetchFn) -> the note for one answer from /global/health.
 * (All speeds, sizes and levels below are cosmetic.) */
(function (root) {
  "use strict";
  var TAU_FWD = 320, TAU_REV = 900, TAU_COL = 700;   // ms: the growth, its reverse run, the colour's drain/return
  var FLOOR = 0.35;                                  // the offline field: this much of the load order stays, dim
  var GREY = [128, 128, 128];

  function rgb(s, d) { var a = String(s || d).split(",").map(Number); return a.length === 3 && a.every(isFinite) ? a : d.split(",").map(Number); }
  function mix(c, sat) { return Math.round(GREY[0] + (c[0] - GREY[0]) * sat) + "," + Math.round(GREY[1] + (c[1] - GREY[1]) * sat) + "," + Math.round(GREY[2] + (c[2] - GREY[2]) * sat); }

  /** What the boot status says as one progress number, 0..1 (1.01: ready). */
  function progressOf(st, ready) {
    st = st || {};
    if (ready) return 1.01;
    if (st.states_total) return st.states / st.states_total;
    if (st.rows_total) return st.rows / st.rows_total;
    return Math.min(1, (st.steps || 0) / 12);
  }

  function fieldAnim(cv, opts) {
    opts = opts || {};
    var cx = cv.getContext("2d"), dpr = root.devicePixelRatio || 1;
    var col = opts.colors || {};
    var DOT = rgb(col.dot, "90,130,200"), LINE = rgb(col.line, "90,130,200"), PULSE = rgb(col.pulse, "240,160,60");
    var BG = col.bg || null;
    var P = [], M = [], O = [], E = [];
    var a = { dir: 1, colour: "coloured", progress: 0, recall: false, ready: false };
    var reveal = 0, sat = 1, rot = 0, last = 0, pulses = [], raf = 0, readyAt = 0;

    function gen(n) {
      P = []; M = []; O = []; E = [];
      for (var i = 0; i < n; i++) {
        var y = 1 - 2 * (i + 0.5) / n, r = Math.sqrt(1 - y * y), g = i * 2.399963, k = Math.cbrt(((i * 37) % 101 + 1) / 101);
        P.push(0.5 + 0.5 * Math.cos(g) * r * k, 0.5 + 0.5 * y * k, 0.5 + 0.5 * Math.sin(g) * r * k);
        M.push(255 * Math.pow(1 - i / n, 3)); O.push(((i * 61) % n) / n);
      }
      for (var j = 1; j < n; j++) E.push(j, (j * 7919) % j);
    }
    gen(1200);

    function target() {
      if (a.dir < 0) return FLOOR;
      return a.ready ? 1.01 : a.progress;
    }
    function fit() { cv.width = (root.innerWidth || cv.width) * dpr; cv.height = (root.innerHeight || cv.height) * dpr; }
    if (opts.fit !== false && root.addEventListener) { root.addEventListener("resize", fit); fit(); }

    var self = {
      get dir() { return a.dir; }, get colour() { return a.colour; },
      shape: function (d) {
        if (d && d.n) { P = d.p.map(function (x) { return x / 1000; }); M = d.m; O = d.o.map(function (x) { return x / 1000; }); E = d.e; } else gen(1200);
      },
      set: function (o) {
        o = o || {};
        if (o.dir !== undefined) a.dir = o.dir < 0 ? -1 : 1;
        if (o.colour !== undefined) a.colour = o.colour === "grey" ? "grey" : "coloured";
        if (o.progress !== undefined) a.progress = o.progress;
        if (o.recall !== undefined) a.recall = !!o.recall;
        if (o.ready !== undefined) { a.ready = !!o.ready; if (a.ready) readyAt = last; }
        if (o.instant) { reveal = target(); sat = a.colour === "grey" ? 0 : 1; }
      },
      /** What is drawn now: how many points show, which (by load order), the colour's saturation. */
      view: function () {
        var on = O.map(function (o) { return o <= reveal ? 1 : 0; });
        return { shown: on.reduce(function (s, x) { return s + x; }, 0), on: on, sat: sat, reveal: reveal };
      },
      tick: function (now) {
        var dt = last ? Math.min(100, now - last) : 16;
        last = now;
        var t = target(), tau = a.dir < 0 ? TAU_REV : TAU_FWD;
        reveal += (t - reveal) * (1 - Math.exp(-dt / tau));
        sat += ((a.colour === "grey" ? 0 : 1) - sat) * (1 - Math.exp(-dt / TAU_COL));
        rot += 0.0012 * dt / 16;
        draw(now);
      },
      start: function () {
        if (raf) return;
        (function loop(now) { self.tick(now); raf = root.requestAnimationFrame(loop); })(0);
      },
      stop: function () { if (raf && root.cancelAnimationFrame) root.cancelAnimationFrame(raf); raf = 0; },
    };

    function draw(now) {
      var w = cv.width, h = cv.height;
      if (BG) { cx.fillStyle = BG; cx.fillRect(0, 0, w, h); } else cx.clearRect(0, 0, w, h);
      var c = Math.cos(rot), s = Math.sin(rot), S = Math.min(w, h) * 0.8;
      var glow = a.ready ? Math.min(2, 1 + (now - readyAt) / 600) : 0.35 + 0.65 * Math.min(1, a.progress || 0);
      if (a.dir < 0 || a.colour === "grey") glow = Math.min(glow, 0.55);
      var dot = mix(DOT, sat), line = mix(LINE, sat);
      var n = M.length, X = new Float32Array(n), Y = new Float32Array(n), Z = new Float32Array(n), on = new Uint8Array(n);
      for (var i = 0; i < n; i++) {
        var x = P[3 * i] - 0.5, y = P[3 * i + 1] - 0.5, z = P[3 * i + 2] - 0.5, rx = x * c + z * s, rz = -x * s + z * c, q = 1 / (1.4 + rz);
        X[i] = w / 2 + rx * S * q; Y[i] = h / 2 + y * S * q; Z[i] = q; on[i] = O[i] <= reveal ? 1 : 0;
      }
      cx.strokeStyle = "rgba(" + line + "," + (0.07 * glow).toFixed(3) + ")"; cx.lineWidth = dpr * 0.6; cx.beginPath();
      var live = [];
      for (var k = 0; k + 1 < E.length; k += 2) { var p = E[k], b = E[k + 1]; if (on[p] && on[b]) { cx.moveTo(X[p], Y[p]); cx.lineTo(X[b], Y[b]); live.push(k); } }
      cx.stroke();
      for (i = 0; i < n; i++) {
        if (!on[i]) continue;
        var m = M[i] / 255, rad = dpr * (0.8 + 2.6 * Math.sqrt(m)) * Z[i] * 1.2;
        cx.fillStyle = "rgba(" + dot + "," + Math.min(1, (0.18 + 0.7 * m) * glow).toFixed(3) + ")";
        cx.beginPath(); cx.arc(X[i], Y[i], rad, 0, 6.283); cx.fill();
      }
      if ((a.recall || a.ready) && a.dir > 0 && sat > 0.5 && live.length) {     // the pulses: only a coloured, forward field
        while (pulses.length < Math.min(60, live.length)) pulses.push({ k: live[(Math.random() * live.length) | 0], t: 0 });
        cx.fillStyle = "rgba(" + mix(PULSE, sat) + ",0.9)";
        for (var u = pulses.length - 1; u >= 0; u--) {
          var pu = pulses[u]; pu.t += 0.02;
          if (pu.t >= 1) { pulses.splice(u, 1); continue; }
          var A = E[pu.k], B = E[pu.k + 1];
          if (!on[A] || !on[B]) continue;
          cx.beginPath(); cx.arc(X[A] + (X[B] - X[A]) * pu.t, Y[A] + (Y[B] - Y[A]) * pu.t, dpr * 1.6, 0, 6.283); cx.fill();
        }
      }
    }
    return self;
  }

  /** The seat as the page sees it: alive -> (down x fails) -> offline -> booting (the door's 503 loading)
   *  -> alive again, which asks for a reload into the UI (never when it never went away). */
  function SeatWatch(o) { this.fails = (o && o.fails) || 2; this.n = 0; this.mode = "alive"; this.progress = 0; this.was = false; }
  SeatWatch.prototype.note = function (e) {
    if (e.kind === "down") {
      this.n++;
      if (this.n >= this.fails) { this.mode = "offline"; this.was = true; }
    } else if (e.kind === "loading") {
      this.n = 0; this.mode = "booting"; this.was = true; this.progress = progressOf(e.d, false); this.step = (e.d && e.d.step) || "starting"; this.secs = Math.round((e.d && e.d.elapsed_s) || 0);
    } else {
      this.n = 0;
      var reload = this.was; this.mode = "alive"; this.was = false;
      return { mode: "alive", progress: 1, reload: reload };
    }
    return { mode: this.mode, progress: this.progress, reload: false };
  };
  /** One poll of /global/health as a note: the door's 503 loading JSON, a network failure or a 5xx, anything else (up). */
  SeatWatch.probe = function (fetchFn) {
    return fetchFn("/global/health", { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (r.status === 503 && d && d.loading) return { kind: "loading", d: d };
          return { kind: r.status >= 500 ? "down" : "up" };
        });
      }, function () { return { kind: "down" }; });
  };

  /** The UI's offline window (#1134): polls the seat every `every` ms; after repeated failures a full-window
   *  GREY field (played in reverse from the coloured one) with a one-line status; once the seat answers
   *  the boot door's loading JSON the coloured loading run follows the real progress; then `onUp` (a reload). */
  function offlineWindow(o) {
    var doc = root.document, watch = new SeatWatch({ fails: o.fails }), box = null, anim = null, line = null, shown = false;
    function mount() {
      box = doc.createElement("div");
      box.setAttribute("role", "status");
      box.style.cssText = "position:fixed;inset:0;z-index:2147483000;background:" + (o.bg || "#0b0d12") + ";color:#c9d1dc;font:14px system-ui,sans-serif";
      box.innerHTML = '<canvas style="position:absolute;inset:0;width:100%;height:100%"></canvas><div style="position:absolute;left:0;right:0;bottom:24px;text-align:center;opacity:.7;padding:0 16px"></div>';
      doc.body.appendChild(box);
      line = box.lastChild;
      anim = fieldAnim(box.firstChild, { colors: { dot: "150,190,255", line: "120,160,230", pulse: "255,190,90", bg: o.bg || "#0b0d12" } });
      anim.set({ progress: 1, instant: true });      // the field the person was just using, whole, then drained
      fetch("/__boot/shape", { headers: { Accept: "application/json" }, cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) { anim.shape(d); }).catch(function () {});
      anim.start();
    }
    function step() {
      SeatWatch.probe(root.fetch.bind(root)).then(function (e) {
        var r = watch.note(e);
        if (r.mode === "alive" && r.reload) { (o.onUp || function () { root.location.reload(); })(); return; }
        if (r.mode !== "alive") {
          if (!shown) { shown = true; mount(); }
          if (r.mode === "offline") { anim.set({ dir: -1, colour: "grey" }); line.textContent = "the seat is not reachable · waiting for it"; }
          else { anim.set({ dir: 1, colour: "coloured", progress: r.progress, recall: !!(e.d && e.d.recall) }); line.textContent = watch.step + " · " + watch.secs + "s"; }
        }
      }).catch(function () {}).then(function () { root.setTimeout(step, o.every); });
    }
    step();
    return { watch: watch };
  }

  var api = { fieldAnim: fieldAnim, SeatWatch: SeatWatch, progressOf: progressOf, offlineWindow: offlineWindow };
  root.fieldAnim = fieldAnim; root.SeatWatch = SeatWatch; root.progressOf = progressOf; root.offlineWindow = offlineWindow;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
