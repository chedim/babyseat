/* THE TAB'S CUBE (standalone mode, 2026-10-12, the operator: "can you make that
 * purple cube into UI's favicon? (bonus points for animating it, too :D)"):
 * membrane home's idle cube -- the wave's gradient on a wireframe box -- drawn
 * into a small canvas and set as the tab's icon, turning slowly. */
const SIZE = 64, FPS = 12;
const CORNERS = [];
for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) CORNERS.push([x, y, z]);
const EDGES = [];
for (let i = 0; i < 8; i++) for (let j = i + 1; j < 8; j++)
  if (CORNERS[i].reduce((n, v, q) => n + (v !== CORNERS[j][q] ? 1 : 0), 0) === 1) EDGES.push([i, j]);

export function startFavicon() {
  const cv = document.createElement("canvas");
  cv.width = cv.height = SIZE;
  const g = cv.getContext("2d");
  if (!g) return;
  let link = document.querySelector('link[rel="icon"]');
  if (!link) { link = document.createElement("link"); link.rel = "icon"; document.head.appendChild(link); }
  link.type = "image/png";
  const grad = g.createLinearGradient(0, 0, SIZE, SIZE);
  grad.addColorStop(0, "#6f8cff"); grad.addColorStop(0.5, "#b07cff"); grad.addColorStop(1, "#4fd1ff");
  const t0 = performance.now();
  const draw = () => {
    const t = (performance.now() - t0) / 1000;
    const yaw = t * 0.9, pitch = 0.5 + Math.sin(t * 0.4) * 0.15;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const P = CORNERS.map(([x, y, z]) => {
      const x1 = x * cy + z * sy, z1 = -x * sy + z * cy, y1 = y * cp - z1 * sp, z2 = y * sp + z1 * cp;
      return [SIZE / 2 + x1 * SIZE * 0.36, SIZE / 2 - y1 * SIZE * 0.36, z2];
    });
    g.clearRect(0, 0, SIZE, SIZE);
    g.lineCap = "round"; g.strokeStyle = grad;
    for (const [i, j] of EDGES) {                         // the far edges fainter: depth at 16 px
      const depth = (P[i][2] + P[j][2]) / 2;
      g.globalAlpha = 0.55 + 0.45 * (depth + 1.7) / 3.4;
      g.lineWidth = depth > 0 ? 6 : 4.5;
      g.beginPath(); g.moveTo(P[i][0], P[i][1]); g.lineTo(P[j][0], P[j][1]); g.stroke();
    }
    g.globalAlpha = 1;
    try { link.href = cv.toDataURL("image/png"); } catch (e) { /* a tainted canvas can't happen here */ }
  };
  draw();
  setInterval(draw, 1000 / FPS);
}
