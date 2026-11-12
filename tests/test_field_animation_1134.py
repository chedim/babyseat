"""THE FIELD ANIMATION FOR EVERY CAN'T-ANSWER STATE (#1134): one shared piece
(membrane/ui/fieldanim.js: a direction forward/reverse x a colour grey/coloured), embedded by the
boot door's page and loaded by the UI; the offline window, and the out-of-tokens window's model
switch (the person's pick for the paused chats; never automatic)."""
import json
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from membrane import bootprof as BP  # noqa: E402
from membrane import quota as Q  # noqa: E402
from tests.test_quota_pause import LIMIT, TestTheRoutes, _Fresh, _runner, _texts, _wait  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


class TheSwitch(_Fresh):
    def test_a_pick_sends_the_held_messages_on_the_picked_model(self):
        r, lad, _ = _runner()
        sid = r.sessions.create("chat")["id"]
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 3600)
        r.run(sid, "hello there")
        self.assertEqual(lad.bodies, [])
        self.assertEqual(r.switch_held("cloudy:cloud", "local:8b"), 1)
        self.assertTrue(_wait(lambda: _texts(r, sid) == ["answered"]))
        self.assertEqual(lad.bodies[0]["model"], "local:8b")
        self.assertEqual(r.sessions.sessions[sid]["model"]["id"], "local:8b")   # the chat's own pick sticks
        self.assertEqual(r.held("cloudy:cloud"), [])
        self.assertIsNotNone(Q.paused("cloudy:cloud"))          # the pause itself is untouched

    def test_a_pick_onto_another_paused_model_holds_again(self):
        r, lad, _ = _runner()
        sid = r.sessions.create("chat")["id"]
        Q.note("cloudy:cloud", LIMIT, until=time.time() + 3600)
        Q.note("other:cloud", LIMIT, until=time.time() + 3600)
        r.run(sid, "hi")
        r.switch_held("cloudy:cloud", "other:cloud")
        self.assertTrue(_wait(lambda: [h["model"] for h in r.held()] == ["other:cloud"]))


class TheRoute(TestTheRoutes):
    def test_use_needs_a_valid_name_and_never_clears_the_pause(self):
        Q.note("claude-agent:sonnet", LIMIT, until=time.time() + 600)
        code, out = self._go("POST", "/membrane/quota/resume", {"model": "claude-agent:sonnet", "use": "bad name!"})
        self.assertEqual(code, 400)
        code, out = self._go("POST", "/membrane/quota/resume", {"model": "claude-agent:sonnet", "use": "local:8b"})
        self.assertEqual(code, 200, out)
        self.assertIn("switched", out)
        self.assertIsNotNone(Q.paused("claude-agent:sonnet"))   # a switch never clears the pause


class TheSharedPiece(unittest.TestCase):
    def test_one_source_for_the_door_and_the_file(self):
        src = (UI / "fieldanim.js").read_text()
        self.assertEqual(BP.ANIM_JS, src)
        page = BP.page_html(BP.Laps("boot"))
        self.assertIn(src, page)                                    # embedded, not a hand copy
        self.assertEqual(page.count("function gen("), 1)            # the one animation, once
        loaders = (UI / "index.html").read_text() + (UI / "panel.js").read_text() + (UI / "home.js").read_text()
        self.assertIn("fieldanim.js", loaders)

    def test_it_holds_no_field_words(self):
        src = (UI / "fieldanim.js").read_text()
        self.assertNotIn("/membrane/", src)


STUB = r"""
global.window=global;global.devicePixelRatio=1;global.innerWidth=100;global.innerHeight=100;
let T=0;
const ctx=new Proxy({}, {get:(t,k)=>()=>{},set:()=>true});
const cv={width:100,height:100,getContext:()=>ctx};
global.getComputedStyle=()=>({getPropertyValue:()=>'1,2,3'});
global.addEventListener=()=>{};
global.requestAnimationFrame=()=>0;
const A=require(%r);
const SH={n:3,p:[500,500,500,200,200,200,800,800,800],m:[255,200,100],o:[0,500,900],e:[0,1,1,2]};
"""


def _node(script):
    return json.loads(subprocess.check_output(["node", "-e", script], text=True))


@NODE
class TheAnimationRuns(unittest.TestCase):
    def run_js(self, body):
        return _node(STUB % str(UI / "fieldanim.js") + body)

    def test_a_direction_and_a_colour(self):
        r = self.run_js("""
const a=A.fieldAnim(cv,{});const o={start:[a.dir,a.colour]};
a.set({colour:'grey',dir:-1});o.grey=[a.dir,a.colour];
a.set({colour:'coloured',dir:1});o.col=[a.dir,a.colour];
console.log(JSON.stringify(o));""")
        self.assertEqual(r["start"], [1, "coloured"])
        self.assertEqual(r["grey"], [-1, "grey"])
        self.assertEqual(r["col"], [1, "coloured"])

    def test_the_reverse_run_drains_the_colour_and_leaves_the_dim_field(self):
        r = self.run_js("""
const a=A.fieldAnim(cv,{});a.shape(SH);a.set({progress:1,instant:true});
const full=a.view().shown;a.set({dir:-1,colour:'grey'});
for(let i=0;i<900;i++){T+=16;a.tick(T);}
const v=a.view();console.log(JSON.stringify({full,shown:v.shown,sat:v.sat}));""")
        self.assertEqual(r["full"], 3)
        self.assertGreater(r["shown"], 0)          # the dim, still field: not gone
        self.assertLess(r["sat"], 0.05)

    def test_the_points_leave_in_reverse_load_order(self):
        r = self.run_js("""
const a=A.fieldAnim(cv,{});a.shape(SH);a.set({progress:1,instant:true});a.set({dir:-1});
const seen=[];
for(let i=0;i<900;i++){T+=16;a.tick(T);const on=a.view().on.join('');if(seen[seen.length-1]!==on)seen.push(on);}
console.log(JSON.stringify(seen));""")
        self.assertEqual(r[:3], ["111", "110", "100"])

    def test_forward_again_follows_the_progress(self):
        r = self.run_js("""
const a=A.fieldAnim(cv,{});a.shape(SH);a.set({progress:1,instant:true});a.set({dir:-1});
for(let i=0;i<900;i++){T+=16;a.tick(T);}
a.set({dir:1,colour:'coloured',progress:0.6});
for(let i=0;i<900;i++){T+=16;a.tick(T);}
const v=a.view();console.log(JSON.stringify({on:v.on.join(''),sat:v.sat}));""")
        self.assertEqual(r["on"], "110")
        self.assertGreater(r["sat"], 0.9)


@NODE
class TheWindows(unittest.TestCase):
    def test_the_watch_goes_grey_then_follows_the_boot_then_reloads(self):
        r = _node("""
const L=require(%r);const o=new L.SeatWatch({fails:2});const seq=[];
seq.push(o.note({kind:'down'}).mode);seq.push(o.note({kind:'down'}).mode);
const b=o.note({kind:'loading',d:{step:'entities',elapsed_s:3,rows:5,rows_total:10,states:5,states_total:10}});
seq.push(b.mode,b.progress);
const n=o.note({kind:'up'});seq.push(n.mode,n.reload);
console.log(JSON.stringify(seq));""" % str(UI / "fieldanim.js"))
        self.assertEqual(r, ["alive", "offline", "booting", 0.5, "alive", True])

    def test_an_up_seat_that_never_went_down_does_not_reload(self):
        r = _node("""
const L=require(%r);const o=new L.SeatWatch({fails:2});
console.log(JSON.stringify(o.note({kind:'up'})));""" % str(UI / "fieldanim.js"))
        self.assertFalse(r["reload"])

    def test_the_quota_window_lists_available_and_paused(self):
        st = {"now": 1800000000, "paused": [{"model": "a:cloud", "resumes_at": 1800003600, "chats": [{"sessionID": "s"}],
                                              "minds": ["Chat"], "goals": []},
                                             {"model": "c:cloud", "resumes_at": 1800003600, "chats": [], "minds": [], "goals": []}]}
        r = _node("""
const L=require(%r);
console.log(JSON.stringify(L.quotaWindowHtml(%s,['a:cloud','b:8b','c:cloud'],'a:cloud',1800000000000)));""" %
                  (str(UI / "panel.js"), json.dumps(st)))
        self.assertIn('data-quse="b:8b"', r)
        self.assertNotIn('data-quse="c:cloud"', r)       # listed as paused, not pickable
        self.assertIn("c:cloud · paused until", r)
        self.assertNotIn('data-quse="a:cloud"', r)
        self.assertIn("<canvas", r)
        self.assertIn("Resume now", r)


if __name__ == "__main__":
    unittest.main()


@NODE
class TheBootPageRuns(unittest.TestCase):
    def test_the_page_goes_grey_follows_the_boot_then_reloads(self):
        import re
        page = BP.page_html(BP.Laps("boot"))
        js = "\n".join(re.findall(r"<script>(.*?)</script>", page, re.S))
        harness = r"""
let T=0;const calls=[];global.window=global;global.devicePixelRatio=1;global.innerWidth=100;global.innerHeight=100;
const ctx=new Proxy({}, {get:()=>()=>{},set:()=>true});
const txt={textContent:''},cv={width:1,height:1,getContext:()=>ctx};
global.document={getElementById:(i)=>i==='f'?cv:txt,documentElement:{}};
global.getComputedStyle=()=>({getPropertyValue:()=>'1,2,3'});
global.addEventListener=()=>{};global.requestAnimationFrame=()=>0;
let reloaded=false;global.location={reload:()=>{reloaded=true;}};
const script=[{down:1},{down:1},{loading:{loading:true,step:'entities',elapsed_s:4,steps:3}},{up:1}];
const texts=[];
global.fetch=(u)=>{ if(u==='/__boot/shape')return Promise.resolve({json:()=>Promise.resolve({shape:null})});
  const s=script.shift(); if(!s)return new Promise(()=>{});
  if(s.down)return Promise.reject(new Error('x'));
  if(s.loading)return Promise.resolve({status:503,json:()=>Promise.resolve(s.loading)});
  return Promise.resolve({status:200,json:()=>Promise.resolve({})});};
global.setTimeout=(f,ms)=>{texts.push(txt.textContent);setImmediate(f);return 0;};
""" + js + r"""
setTimeout=global.setTimeout;
process.on('exit',()=>console.log(JSON.stringify({texts,reloaded,last:txt.textContent})));
"""
        out = subprocess.check_output(["node", "-e", harness], text=True)
        r = json.loads(out.strip().splitlines()[-1])
        self.assertTrue(any("not reachable" in t for t in r["texts"]))
        self.assertTrue(any("entities" in t for t in r["texts"]))
        self.assertEqual(r["last"], "ready")
