/* THE TAB'S BABYSEAT (#161, 2026-10-19, the operator: "put [the babyseat icon]
 * into our favicon instead of rotating cube"). Formerly THE TAB'S CUBE
 * (2026-10-12, "can you make that purple cube into UI's favicon? (bonus points
 * for animating it, too :D)"). The seat of favicon.svg -- self, the glowing
 * teal amoeba, strapped into an infant car seat -- rasterised once into a
 * small canvas and set as the tab's icon, rocking a little on its base while
 * the amoeba's glow breathes. Still (the plain SVG) for prefers-reduced-motion;
 * asleep while the tab is hidden; lighter on a dark tab strip. */
const SIZE = 64, FPS = 10;
const SRC = "/ui/www/favicon.svg";
// favicon.svg's frame: viewBox "58 55 430 430" around a translate(0,-46) group
const K = SIZE / 430, at = (x, y) => [(x - 58) * K, (y - 46 - 55) * K];
const PIVOT = at(280, 470);                  // where the rocker meets the floor
const AMOEBA = at(268, 318), AMOEBA_R = 120 * K;
const ROCK = 0.06, ROCK_HZ = 0.2, BREATH_HZ = 0.25;   // ~3.4 degrees; slow, a sleeping baby

export function startFavicon() {
  const still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
  if (still && still.matches) return;        // the static favicon.svg stays
  const cv = document.createElement("canvas");
  cv.width = cv.height = SIZE;
  const g = cv.getContext("2d");
  if (!g) return;
  let link = document.querySelector('link[rel="icon"]');
  if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
  // the SVG has only a viewBox: give it a size, so every browser rasterises it at SIZE.
  // Its dark-tab palette (#269: the slate seat vanished on #202124) is a media rule
  // the page's scheme decides here, since an SVG drawn as an image can't be trusted to
  // see it: on, it becomes @media all; off, @media not all. Re-rasterised on a change.
  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)");
  const DARK_RULE = "@media (prefers-color-scheme: dark)";
  let svg = "", seat = null, url = "", started = false;
  const raster = () => {
    const img = new Image();
    img.onload = () => {
      if (url) URL.revokeObjectURL(url);
      url = img.src; seat = img;
      if (!started) { started = true; run(); }
    };
    img.src = URL.createObjectURL(new Blob([svg
      .replace("<svg ", `<svg width="${SIZE}" height="${SIZE}" `)
      .replace(DARK_RULE, dark && dark.matches ? "@media all" : "@media not all")], { type: "image/svg+xml" }));
  };
  fetch(SRC).then((r) => r.text()).then((text) => {
    svg = text; raster();
    if (dark && dark.addEventListener) dark.addEventListener("change", raster);
  }).catch(() => {});

  function run() {
    const t0 = performance.now();
    let timer = null;
    const draw = () => {
      const t = (performance.now() - t0) / 1000;
      const tilt = ROCK * Math.sin(2 * Math.PI * ROCK_HZ * t);
      const breath = 0.5 - 0.5 * Math.cos(2 * Math.PI * BREATH_HZ * t);   // 0..1
      g.clearRect(0, 0, SIZE, SIZE);
      g.save();
      g.translate(PIVOT[0], PIVOT[1]); g.rotate(tilt); g.translate(-PIVOT[0], -PIVOT[1]);
      g.drawImage(seat, 0, 0, SIZE, SIZE);
      const halo = g.createRadialGradient(AMOEBA[0], AMOEBA[1], 0, AMOEBA[0], AMOEBA[1], AMOEBA_R);
      halo.addColorStop(0, "rgba(234,255,248," + (0.12 + 0.26 * breath) + ")");
      halo.addColorStop(0.5, "rgba(95,240,200," + (0.06 + 0.16 * breath) + ")");
      halo.addColorStop(1, "rgba(95,240,200,0)");
      g.globalCompositeOperation = "lighter";            // the glow adds light, never a plate
      g.fillStyle = halo;
      g.beginPath(); g.arc(AMOEBA[0], AMOEBA[1], AMOEBA_R, 0, 2 * Math.PI); g.fill();
      g.restore();
      try { link.type = "image/png"; link.href = cv.toDataURL("image/png"); }
      catch (e) { /* a blob from our own SVG doesn't taint the canvas */ }
    };
    const wake = () => { if (!timer && !document.hidden && !(still && still.matches)) { draw(); timer = setInterval(draw, 1000 / FPS); } };
    const sleep = () => { if (timer) { clearInterval(timer); timer = null; } };
    document.addEventListener("visibilitychange", () => (document.hidden ? sleep() : wake()));
    if (still && still.addEventListener) still.addEventListener("change", (e) => {
      if (!e.matches) return wake();
      sleep(); link.type = "image/svg+xml"; link.href = SRC;   // back to the still seat
    });
    wake();
  }
}
