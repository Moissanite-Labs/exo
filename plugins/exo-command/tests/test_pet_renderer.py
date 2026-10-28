"""Geometry is bounded and rendering remains passive across activity and visibility changes."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc

STATIC = Path(__file__).resolve().parents[1] / "exo_command" / "static"


@pytest.mark.skipif(not shutil.which("node"), reason="Node is unavailable")
def test_all_forms_are_deterministic_real_bounded_graphs(tmp_path):
    shutil.copy(STATIC / "pet-renderer.js", tmp_path / "renderer.mjs")
    (tmp_path / "check.mjs").write_text('''
import assert from 'node:assert/strict';
import {PET_FORMS,geometryForForm} from './renderer.mjs';
const counts=[276,9,61,14,75,24,42,76,142,272];
const fingerprints=new Set();
PET_FORMS.forEach((name,index)=>{
  const g=geometryForForm(name);
  assert.equal(g.points.length,counts[index]);
  assert(g.points.length<=512 && g.edges.length<=4096);
  assert(g.points.every(p=>p.position.every(Number.isFinite)));
  const edges=new Set();
  for(const e of g.edges){
    assert(e.a>=0 && e.b<g.points.length && e.a!==e.b);
    const key=[Math.min(e.a,e.b),Math.max(e.a,e.b)].join(':');
    assert(!edges.has(key));edges.add(key);
  }
  assert.equal(JSON.stringify(g),JSON.stringify(geometryForForm(name)));
  assert.throws(()=>{g.points[0].position[0]=999;},TypeError);
  fingerprints.add(JSON.stringify(g.points));
});
assert.equal(fingerprints.size,10);
assert.equal(geometryForForm('<svg onload=alert(1)>').form,'egg');
assert.equal(geometryForForm('cell').points[0].position.length,2);
assert.equal(geometryForForm('d8').points[0].position.length,8);
// The inner cell's four incentres use the actual square construction.
const centers=geometryForForm('cell').points.filter(p=>p.kind==='lattice');
assert.equal(centers.length,4);
assert(centers.every(p=>Math.abs(Math.hypot(...p.position)-(Math.SQRT2-1))<1e-10));
console.log(JSON.stringify(counts));
''')
    result = subprocess.run([shutil.which("node"), str(tmp_path / "check.mjs")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


browser_only = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"), reason="Chrome or websockets unavailable")


@pytest.fixture
def page(tmp_path):
    for name in ("pet-renderer.js", "pet.css", "style.css"):
        shutil.copy(STATIC / name, tmp_path / name)
    (tmp_path / "index.html").write_text('''<!doctype html><html lang="en" data-theme="dark"><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/pet.css">
</head><body><main class="xc-root"><div id="host" class="xc-pet-figure"></div></main></body></html>''')
    server = Served(ThreadSrc(0), static=tmp_path)
    try:
        with Browser(width=390, height=850) as browser:
            browser.goto(server.url())
            browser.wait("!!document.getElementById('host')")
            browser.eval("(async()=>{window.rendererModule=await import('/static/pet-renderer.js');window.renderer=rendererModule.createPetRenderer(document.getElementById('host'));})()")
            yield browser
    finally:
        server.close()


@browser_only
def test_renderer_counts_themes_and_destroy_without_background_work(page):
    b = page
    # A static renderer must not introduce an animation loop, timer, or visibility listener.
    b.eval('''window.timerCalls=0;window.rafCalls=0;
const timer=window.setTimeout,raf=window.requestAnimationFrame;
window.setTimeout=(...a)=>{timerCalls++;return timer(...a)};
window.requestAnimationFrame=(...a)=>{rafCalls++;return raf(...a)};''')
    for form in ("egg", "cell", "tile", "cube", "frame", "d4", "d5", "d6", "d7", "d8"):
        b.eval(f"renderer.render({{form:{json.dumps(form)},state:'idle',learned:3,reducedMotion:true}})")
        assert b.eval("document.querySelectorAll('.xc-pet-node').length") <= 512
        assert b.eval("document.querySelectorAll('.xc-pet-learned').length") == 3
        assert b.eval("document.querySelector('.xc-pet-svg').getAttribute('aria-hidden')") == "true"
    colors = []
    for theme in ("dark", "light"):
        colors.append(b.eval(f"document.documentElement.dataset.theme='{theme}';getComputedStyle(document.querySelector('.xc-pet-node')).fill"))
    assert colors[0] != colors[1]
    assert b.eval("[timerCalls,rafCalls]") == [0, 0]
    b.eval("renderer.destroy();renderer.destroy();renderer.render({state:'thinking'})")
    assert b.eval("document.querySelectorAll('.xc-pet-svg').length") == 0


@browser_only
def test_same_visual_state_has_no_dom_churn_and_motion_cancels_immediately(page):
    b = page
    b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
    b.eval("renderer.render({state:'idle'});renderer.render({state:'thinking'});window.bodyNode=document.querySelector('.xc-pet-body');window.runningAnimation=bodyNode.getAnimations()[0]")
    assert b.eval("!!window.runningAnimation")
    b.eval("for(let i=0;i<100;i++)renderer.render({state:'thinking'})")
    assert b.eval("bodyNode===document.querySelector('.xc-pet-body')")
    assert b.eval("document.querySelector('.xc-pet-body').getAnimations()[0]===runningAnimation")
    b.eval("renderer.render({state:'thinking',reducedMotion:true})")
    assert b.eval("runningAnimation.playState") == "idle"
    b.eval("renderer.render({state:'tool'});renderer.pause()")
    assert b.eval("document.getAnimations().length") == 0
    b.eval("document.getElementById('host').hidden=true;renderer.render({state:'answering'})")
    assert b.eval("document.getAnimations().length") == 0
    b.eval("document.getElementById('host').hidden=false;renderer.render({state:'answering'})")
    assert b.eval("document.getAnimations().length") == 0  # no delayed restart when revealed
    b.eval("renderer.render({state:'waiting',active:false})")
    assert b.eval("document.getAnimations().length") == 0


@browser_only
def test_destroy_cancels_active_transition_and_rejects_invalid_values(page):
    b = page
    b.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
    b.eval("renderer.render({form:'<svg onload=alert(1)>',state:'<img>',learned:Infinity})")
    assert b.eval("document.querySelector('.xc-pet-svg').dataset.form") == "egg"
    assert b.eval("document.querySelector('.xc-pet-svg').dataset.state") == "idle"
    assert b.eval("document.querySelectorAll('.xc-pet-learned').length") == 0
    b.eval("renderer.render({state:'tool'});window.lastAnimation=document.querySelector('.xc-pet-body').getAnimations()[0];renderer.destroy()")
    assert b.eval("lastAnimation.playState") == "idle"
    assert b.eval("document.getAnimations().length") == 0
    assert b.eval("document.querySelectorAll('svg').length") == 0
