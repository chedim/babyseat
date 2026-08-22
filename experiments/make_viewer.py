#!/usr/bin/env python3
"""make_viewer -- bake a self-contained 3D Three.js viewer of the energy closed loop.

Runs the SAME closed loop as multi_energy_loop.py (two cells over the same growth
stream, OFF no-nurser vs ON nurser), snapshots each cell's field every step, and
PCA-projects the D=8 veil space to 3D. Writes a single viewer.html with the data
inlined -- open it in a browser, no server, no claude.ai hosting needed.

What you see: each cell is a cloud of stars (its exemplars). A star is GREEN when its
exemplar is safe (the environment did not vote it down) and RED when demoted (unsafe).
Stars APPEAR as the cell absorbs; they CHANGE COLOR as the nurser culls (green->red) or
the model contests (red->green). A bud fires a glowing GOLD child that drifts out of
the cloud. Two energy gauges (OFF / ON) show free_energy vs the budding threshold, so
the drain / re-cross / fade is visible alongside the cloud. The contrast IS the thesis:
OFF stays green and keeps budding (immortal); ON goes red, drains below the threshold,
and stops reproducing (faded) -- then is rescued (contest -> green -> re-bud) and
out-voted (re-ask -> red -> faded for good).

The exemplar VEC is stable for the life of the slot (repair/demotion mutate action/
safe, not the vec), so 3D positions are fixed per exemplar and only color/count/buds
animate -- a demotion reads as a star turning red where it stands.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from experiments.multi_energy_loop import (  # noqa: E402
    _cull_all, _rescue_all, _outvote_all, _maybe_bud, _state,
)
from experiments.multi_telemetry import _pull, PHASE_A, PHASE_B  # noqa: E402
from experiments.multi_cell import _build_cell  # noqa: E402

_OUT = os.environ.get(
    "VIEWER_OUT",
    "/Users/dmitrii.chechetkin/.claude/jobs/8b615977/tmp/membrane-viewer.html",
)
_SCALE = 3.0
_OFF_X, _ON_X = -4.5, 4.5


def _pca3(vecs):
    V = np.array(vecs, dtype=float)
    mu = V.mean(0)
    _, _, Vt = np.linalg.svd(V - mu, full_matrices=False)
    return Vt[:3], mu


def _proj(basis, mu, v):
    return [float(x) for x in ((basis @ (np.asarray(v, dtype=float) - mu)) * _SCALE)]


def _safe_list(cell):
    return [1 if e.safe else 0 for e in cell.membrane.store.exemplars]


def _vecs(cell):
    return [list(e.vec) for e in cell.membrane.store.exemplars]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="viewer-")
    cfg = {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}
    off = _build_cell("off", cfg, tmp)
    on = _build_cell("on", cfg, tmp)
    off_ctr, on_ctr = [0], [0]

    raw = []  # per-frame: safe lists + gauges + bud flags + phase/event

    def record(phase, event, ob, nb):
        raw.append({
            "phase": phase, "event": event, "off_bud": bool(ob), "on_bud": bool(nb),
            "off_safe": _safe_list(off), "on_safe": _safe_list(on),
            "off": _state(off), "on": _state(on),
        })

    # ---- replicate multi_energy_loop.main()'s timeline exactly ----------------
    for t in PHASE_A:
        _pull(off, t); _pull(on, t)
        record("A", "grow", _maybe_bud(off, tmp, "off", off_ctr),
               _maybe_bud(on, tmp, "on", on_ctr))
    _cull_all(on)
    record("A", "cull", False, _maybe_bud(on, tmp, "on", on_ctr))
    for t in PHASE_B:
        _pull(off, t); _pull(on, t); _cull_all(on)
        record("B", "grow+cull", _maybe_bud(off, tmp, "off", off_ctr),
               _maybe_bud(on, tmp, "on", on_ctr))
    _rescue_all(on)
    record("|", "rescue", False, _maybe_bud(on, tmp, "on", on_ctr))
    for t in PHASE_B[:3]:
        _pull(on, t)
        record("|", "rescue-grow", False, _maybe_bud(on, tmp, "on", on_ctr))
    _outvote_all(on)
    record("|", "out-vote", False, _maybe_bud(on, tmp, "on", on_ctr))
    for t in PHASE_B[-3:]:
        _pull(off, t); _pull(on, t); _cull_all(on)
        record("D", "static+cull", _maybe_bud(off, tmp, "off", off_ctr),
               _maybe_bud(on, tmp, "on", on_ctr))

    # ---- PCA each cell's FINAL field (stable basis; vec is stable per exemplar) -
    ob, om = _pca3(_vecs(off))
    nb, nm = _pca3(_vecs(on))
    off_pos = [[_OFF_X + _proj(ob, om, v)[0]] + _proj(ob, om, v)[1:] for v in _vecs(off)]
    on_pos = [[_ON_X + _proj(nb, nm, v)[0]] + _proj(nb, nm, v)[1:] for v in _vecs(on)]

    fe_all = [r["off"]["fe"] for r in raw] + [r["on"]["fe"] for r in raw]
    th_all = [r["off"]["thr"] for r in raw] + [r["on"]["thr"] for r in raw]
    fe_min = min(min(fe_all), min(th_all), 0.0)
    fe_max = max(max(fe_all), max(th_all)) * 1.05

    meta = {
        "off_pos": off_pos, "on_pos": on_pos,
        "off_cx": _OFF_X, "on_cx": _ON_X,
        "fe_min": fe_min, "fe_max": fe_max, "n": len(raw),
    }

    html = _HTML.replace("__FRAMES__", json.dumps(raw)) \
                .replace("__META__", json.dumps(meta))
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w") as f:
        f.write(html)
    print(f"viewer written -> {_OUT}")
    print(f"  {len(raw)} frames, OFF {len(off_pos)} pts, ON {len(on_pos)} pts")
    print(f"  free_energy range {_f(fe_min)} -> {_f(fe_max)}")
    print("open it in a browser (no server needed).")


def _f(x: float) -> str:
    return f"{x:.3f}"


# ---------------------------------------------------------------- the page
_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Membrane Worlds — the energy closed loop, in 3D</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<style>
  :root{--ink:#dfe6e0;--muted:#8a958e;--bg:#05070a;--panel:rgba(12,18,22,.78);
       --green:#5fd47a;--red:#e05a4a;--gold:#e6c34a;--line:rgba(120,140,130,.25)}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;overflow:hidden;background:var(--bg);color:var(--ink);
    font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace}
  #c{position:fixed;inset:0}
  .hud{position:fixed;backdrop-filter:blur(6px);background:var(--panel);
       border:1px solid var(--line);border-radius:10px;padding:12px 14px}
  #top{top:14px;left:14px;min-width:220px}
  #top .t{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
  #top .ph{font-size:24px;font-weight:600;margin:2px 0 1px}
  #top .ev{font-size:12.5px;color:var(--muted);min-height:1.4em}
  #step{font-size:11px;color:var(--muted)}
  .gauge{position:fixed;bottom:96px;width:150px}
  #goff{left:14px} #gon{right:14px}
  .gauge .lab{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:5px}
  .gauge .lab b{color:var(--ink)}
  .bar{position:relative;height:190px;width:100%;background:rgba(255,255,255,.04);
       border:1px solid var(--line);border-radius:6px;overflow:hidden}
  .fill{position:absolute;left:0;right:0;bottom:0;background:linear-gradient(var(--green),rgba(95,212,122,.25));
        transition:height .12s linear}
  .fill.neg{background:linear-gradient(var(--red),rgba(224,90,74,.25))}
  .thr{position:absolute;left:0;right:0;height:2px;background:var(--gold);opacity:.85}
  .thr::after{content:"threshold";position:absolute;right:4px;top:-13px;font-size:9px;color:var(--gold)}
  .val{font-size:11px;color:var(--muted);margin-top:5px;font-variant-numeric:tabular-nums}
  .val b{color:var(--ink)}
  .ready{font-size:11px;margin-top:2px}
  .ready.y{color:var(--green)} .ready.n{color:var(--red)}
  #ctl{bottom:14px;left:50%;transform:translateX(-50%);display:flex;align-items:center;
       gap:12px;width:min(620px,92vw)}
  #ctl button{background:rgba(255,255,255,.06);color:var(--ink);border:1px solid var(--line);
       border-radius:6px;padding:7px 12px;font:inherit;font-size:13px;cursor:pointer}
  #ctl button:hover{background:rgba(255,255,255,.12)}
  #scrub{flex:1;-webkit-appearance:none;appearance:none;height:4px;border-radius:3px;
         background:rgba(255,255,255,.12);outline:none}
  #scrub::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;
         background:var(--gold);cursor:pointer}
  #spd{font-size:11px;color:var(--muted);min-width:54px;text-align:right}
  #legend{top:14px;right:14px;font-size:11px;line-height:1.7}
  #legend .d{display:inline-flex;align-items:center;gap:6px}
  .dot{width:9px;height:9px;border-radius:50%;display:inline-block}
  .dg{background:var(--green)} .dr{background:var(--red)} .db{background:var(--gold)}
  .hint{position:fixed;bottom:14px;left:14px;font-size:10px;color:var(--muted)}
</style>
</head>
<body>
<canvas id="c"></canvas>

<div class="hud" id="top">
  <div class="t">the membrane · a pocket universe</div>
  <div class="ph" id="ph">—</div>
  <div class="ev" id="ev"></div>
  <div id="step"></div>
</div>

<div class="hud" id="legend">
  <div class="d"><span class="dot dg"></span> safe exemplar</div>
  <div class="d"><span class="dot dr"></span> demoted (unsafe)</div>
  <div class="d"><span class="dot db"></span> a bud (child)</div>
</div>

<div class="hud gauge" id="goff">
  <div class="lab"><b>OFF</b> · no nurser</div>
  <div class="bar"><div class="fill" id="offfill"></div><div class="thr" id="offthr"></div></div>
  <div class="val">free_energy <b id="offfe">0</b></div>
  <div class="ready y" id="offrd">● ready</div>
</div>
<div class="hud gauge" id="gon">
  <div class="lab"><b>ON</b> · the nurser culls</div>
  <div class="bar"><div class="fill" id="onfill"></div><div class="thr" id="onthr"></div></div>
  <div class="val">free_energy <b id="onfe">0</b></div>
  <div class="ready y" id="onrd">● ready</div>
</div>

<div class="hud" id="ctl">
  <button id="play">❚❚ pause</button>
  <input id="scrub" type="range" min="0" max="0" value="0">
  <span id="spd">8 fps</span>
</div>
<div class="hint">drag to orbit · scroll to zoom · the nurser is the only difference between the two worlds</div>

<script>
const FRAMES = __FRAMES__;
const META = __META__;
const C = [0.36,0.83,0.48], R = [0.94,0.35,0.29], GOLD = [0.9,0.76,0.29];

// ---- three scene -----------------------------------------------------------
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x05070a, 0.018);
const renderer = new THREE.WebGLRenderer({canvas:document.getElementById("c"),antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
const camera = new THREE.PerspectiveCamera(55,1,0.1,200);
let theta=0.6, phi=1.0, rad=18, tgt=new THREE.Vector3(0,0,0);
function cam(){const p=new THREE.Vector3(rad*Math.sin(phi)*Math.sin(theta),
  rad*Math.cos(phi), rad*Math.sin(phi)*Math.cos(theta));camera.position.copy(p).add(tgt);
  camera.lookAt(tgt);}
function resize(){const w=innerWidth,h=innerHeight;renderer.setSize(w,h);camera.aspect=w/h;
  camera.updateProjectionMatrix();}
addEventListener("resize",resize); resize(); cam();

// star sprite
function sprite(){const c=document.createElement("canvas");c.width=c.height=64;const g=c.getContext("2d");
  const grd=g.createRadialGradient(32,32,0,32,32,32);grd.addColorStop(0,"rgba(255,255,255,1)");
  grd.addColorStop(.4,"rgba(255,255,255,.6)");grd.addColorStop(1,"rgba(255,255,255,0)");
  g.fillStyle=grd;g.fillRect(0,0,64,64);return new THREE.CanvasTexture(c);}
const tex=sprite();

function makeCloud(pos){
  const g=new THREE.BufferGeometry();
  g.setAttribute("position",new THREE.BufferAttribute(new Float32Array(pos.flat()),3));
  const col=new Float32Array(pos.length*3);
  g.setAttribute("color",new THREE.BufferAttribute(col,3));
  const m=new THREE.PointsMaterial({size:0.42,sizeAttenuation:true,map:tex,
    vertexColors:true,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending});
  return new THREE.Points(g,m);
}
const offCloud=makeCloud(META.off_pos), onCloud=makeCloud(META.on_pos);
scene.add(offCloud); scene.add(onCloud);

// a faint anchor disc under each cloud so the two worlds are separable
function disc(x){const g=new THREE.RingGeometry(2.6,2.7,48);
  const m=new THREE.MeshBasicMaterial({color:0x2a3a32,side:THREE.DoubleSide,transparent:true,opacity:.35});
  const r=new THREE.Mesh(g,m);r.rotation.x=Math.PI/2;r.position.set(x,-3.2,0);return r;}
scene.add(disc(META.off_cx)); scene.add(disc(META.on_cx));

// bud spheres pool
const buds=[]; const bgeo=new THREE.SphereGeometry(0.13,16,16);
for(let i=0;i<28;i++){const m=new THREE.MeshBasicMaterial({color:GOLD,transparent:true,opacity:0});
  const s=new THREE.Mesh(bgeo,m);s.visible=false;scene.add(s);buds.push({s,life:0,v:new THREE.Vector3()});}
function spawnBud(cx,pts,count){
  // centroid of the live points
  let cxn=0,cy=0,cz=0;for(let i=0;i<count;i++){cxn+=pts[i*3];cy+=pts[i*3+1];cz+=pts[i*3+2];}
  if(count){cxn/=count;cy/=count;cz/=count;} else {cxn=cx;}
  const b=buds.find(b=>b.life<=0);if(!b)return;
  b.s.visible=true;b.s.position.set(cxn,cy,cz);b.life=1.0;b.s.material.opacity=1;
  b.v.set((Math.random()-.5),(Math.random()-.3+.6),(Math.random()-.5)).normalize().multiplyScalar(1.4);
}
function stepBuds(dt){
  for(const b of buds){if(b.life<=0)continue;b.s.position.addScaledVector(b.v,dt);
    b.life-=dt*0.7;b.s.material.opacity=Math.max(0,b.life);
    b.s.scale.setScalar(0.6+0.8*(1-b.life));if(b.life<=0)b.s.visible=false;}
}

// orbit controls (manual)
let drag=false,lx=0,ly=0;
renderer.domElement.addEventListener("mousedown",e=>{drag=true;lx=e.clientX;ly=e.clientY});
addEventListener("mouseup",()=>drag=false);
addEventListener("mousemove",e=>{if(!drag)return;theta-=(e.clientX-lx)*0.01;
  phi=Math.max(0.15,Math.min(Math.PI-0.15,phi+(e.clientY-ly)*0.01));lx=e.clientX;ly=e.clientY;cam();});
renderer.domElement.addEventListener("wheel",e=>{rad=Math.max(8,Math.min(40,rad+e.deltaY*0.02));cam();});

// ---- playback --------------------------------------------------------------
let fi=0, playing=true, last=performance.now(), fps=8;
const scrub=document.getElementById("scrub"), play=document.getElementById("play");
scrub.max=FRAMES.length-1;
play.onclick=()=>{playing=!playing;play.textContent=playing?"❚❚ pause":"▶ play";};
scrub.oninput=()=>{fi=+scrub.value;render();};

function setColors(cloud,safe){
  const col=cloud.geometry.attributes.color, pos=cloud.geometry.attributes.position;
  const n=Math.min(safe.length,pos.count);
  for(let i=0;i<n;i++){const c=safe[i]?C:R;col.array[i*3]=c[0];col.array[i*3+1]=c[1];col.array[i*3+2]=c[2];}
  for(let i=n;i<col.count;i++){col.array[i*3]=0;col.array[i*3+1]=0;col.array[i*3+2]=0;}
  col.needsUpdate=true;cloud.geometry.setDrawRange(0,n);
}
function gauge(fillId,thrId,feId,rdId,fe,thr,ready){
  const span=META.fe_max-META.fe_min, h=v=>Math.max(0,Math.min(1,(v-META.fe_min)/span))*100;
  const f=document.getElementById(fillId);f.style.height=h(fe)+"%";
  f.className="fill"+(fe<0?" neg":"");
  document.getElementById(thrId).style.bottom=h(thr)+"%";
  document.getElementById(feId).textContent=fe.toFixed(2);
  const r=document.getElementById(rdId);r.className="ready "+(ready?"y":"n");
  r.textContent=(ready?"●":"○")+" "+(ready?"ready":"faded");
}
let playedTo=-1;
function render(){
  const fr=FRAMES[Math.max(0,Math.min(FRAMES.length-1,Math.floor(fi)))];
  setColors(offCloud,fr.off_safe); setColors(onCloud,fr.on_safe);
  gauge("offfill","offthr","offfe","offrd",fr.off.fe,fr.off.thr,fr.off.ready);
  gauge("onfill","onthr","onfe","onrd",fr.on.fe,fr.on.thr,fr.on.ready);
  document.getElementById("ph").textContent=
    {A:"phase A · growth",B:"phase B · culled","|":"the boundary",D:"static tail"}[fr.phase]||fr.phase;
  document.getElementById("ev").textContent=fr.event;
  document.getElementById("step").textContent=
    "step "+Math.floor(fi)+" / "+(FRAMES.length-1)+"  ·  OFF buds "+fr.off_bud+"  ON buds "+fr.on_bud;
  if(Math.floor(fi)>playedTo){
    for(let k=playedTo+1;k<=Math.floor(fi);k++){
      const g=FRAMES[k];if(!g)continue;
      if(g.off_bud)spawnBud(META.off_cx,META.off_pos,META.off_pos.length/3);
      if(g.on_bud)spawnBud(META.on_cx,META.on_pos,META.on_pos.length/3);
    }
    playedTo=Math.floor(fi);
  }
  scrub.value=Math.floor(fi);
}
render();

function loop(now){const dt=Math.min(0.1,(now-last)/1000);last=now;
  if(playing){fi+=fps*dt;if(fi>=FRAMES.length){fi=0;playedTo=-1;}render();}
  stepBuds(dt);renderer.render(scene,camera);requestAnimationFrame(loop);}
requestAnimationFrame(loop);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()