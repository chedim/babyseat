// THE MINI-CUBE (2026-10-11, the operator: it should read as a mini-graph with the main view's orientation): its edges point where three.js projects the field box's edges.  node tests/browser/cube.test.cjs
const fs = require("fs"), vm = require("vm"), path = require("path");
const www = path.join(__dirname, "..", "..", "custom_components", "ai_terminal", "www");
const ctx = { window: {}, self: {}, console };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(www, "three.min.js"), "utf8"), ctx);
const THREE = ctx.THREE || ctx.window.THREE || ctx.self.THREE;
const src = fs.readFileSync(path.join(www, "home.js"), "utf8");
const m = src.match(/const CUBE_PATH[\s\S]*?\nfunction cubePoints[\s\S]*?\n}\n/);
const cubePoints = new Function(m[0] + "; return cubePoints;")();
let worst = 0;
for (const [th, ph] of [[0.7, 1.05], [2.5, 0.3], [-1.2, 2.6], [3.0, 1.57]]) {
  const cam = new THREE.PerspectiveCamera(55, 1, 1, 20000);
  const r = 1750;
  cam.position.set(r * Math.sin(ph) * Math.sin(th), r * Math.cos(ph), r * Math.sin(ph) * Math.cos(th));
  cam.lookAt(0, 0, 0); cam.updateMatrixWorld();
  const cube = cubePoints(16, 0, 0, cam.matrixWorldInverse.elements);
  // compare each edge's 2D DIRECTION with three's projection of the same box edge (far away, ~orthographic)
  const corners = []; for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) corners.push([x, y, z]);
  let k = 0;
  for (let i = 0; i < 8; i++) for (let j = i + 1; j < 8; j++) {
    const d = corners[i].reduce((n, v, q) => n + (v !== corners[j][q] ? 1 : 0), 0);
    if (d !== 1) continue;
    const pa = new THREE.Vector3(...corners[i]).multiplyScalar(10).project(cam), pb = new THREE.Vector3(...corners[j]).multiplyScalar(10).project(cam);
    const [a, b] = cube.edges[k++];
    const v1 = [b[0] - a[0], b[1] - a[1]], v2 = [pb.x - pa.x, pb.y - pa.y];
    const n1 = Math.hypot(...v1), n2 = Math.hypot(...v2);
    if (n1 < 1e-6 || n2 < 1e-9) continue;
    worst = Math.max(worst, 1 - (v1[0] * v2[0] + v1[1] * v2[1]) / n1 / n2);
  }
}
console.log("worst 1-cos between mini-cube edges and the camera's projection:", worst.toExponential(2));
process.exit(worst < 1e-3 ? 0 : 1);
