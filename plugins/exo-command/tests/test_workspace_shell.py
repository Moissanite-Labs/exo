"""The workspace shell navigates honestly, preserves focus, and stores only an appearance enum."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc

STATIC = Path(__file__).resolve().parents[1] / "exo_command" / "static"


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed")
def test_appearance_only_stores_valid_choices_and_handles_unavailable_storage(tmp_path):
    (tmp_path / "appearance.mjs").write_text((STATIC / "appearance.js").read_text())
    (tmp_path / "test.mjs").write_text('''
import assert from "node:assert/strict";
import {getAppearance,applyAppearance,setAppearance} from "./appearance.mjs";
const written=[], root={setAttribute:(key,value)=>written.push([key,value])};
let value=null;const saved=[];const store={getItem:key=>{assert.equal(key,"xc-appearance");return value;},setItem:(key,v)=>saved.push([key,v])};
assert.equal(getAppearance(store),"dark");assert.deepEqual(saved,[]);
for(value of ["dark","light","system"]) assert.equal(getAppearance(store),value);
for(value of ["bad", "a prompt or secret", {}, null]) assert.equal(getAppearance(store),"dark");
for(const choice of ["light","system","dark"]) assert.equal(setAppearance(choice,root,store),choice);
setAppearance("a prompt or secret",root,store);
assert.deepEqual(saved,[["xc-appearance","light"],["xc-appearance","system"],["xc-appearance","dark"],["xc-appearance","dark"]]);
const blocked={getItem(){throw Error("storage blocked");},setItem(){throw Error("storage blocked");}};
assert.equal(getAppearance(blocked),"dark");assert.equal(setAppearance("light",root,blocked),"light");
assert.deepEqual(written.at(-1),["data-theme","light"]);
assert.equal(applyAppearance("system",root),"system");
''')
    result = subprocess.run([shutil.which("node"), str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


needs_browser = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"), reason="Chrome or websockets missing")


def activate(b):
    b.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
    b.call("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)


@needs_browser
def test_shell_semantic_navigation_history_skip_focus_and_no_boot_overlay():
    srv = Served(ThreadSrc(pairs=0))
    try:
        with Browser(width=1200, height=850) as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-nav a[aria-current=page]')")
            assert b.eval("[...document.querySelectorAll('.xc-nav-link')].map(a=>a.textContent)") == ["Chat", "Tasks", "Schedule", "Command", "Providers"]
            assert b.eval("document.querySelector('nav').getAttribute('role')") is None
            assert b.eval("document.querySelector('main').id") == "xc-main"
            assert b.eval("document.querySelector('.xc-conn').getAttribute('role')") == "status"
            assert b.eval("document.querySelector('.xc-boot') === null")
            b.wait("document.querySelector('.xc-conn').textContent === 'Connected'")
            b.eval("window.originalTasksLink=document.querySelector('a[href=\\\"#tasks\\\"]');window.originalTasksLink.focus()")
            activate(b)
            b.wait("document.querySelector('a[aria-current=page]')?.textContent === 'Tasks'")
            assert b.eval("document.activeElement === window.originalTasksLink")
            assert b.eval("document.querySelector('a[href=\\\"#tasks\\\"]') === window.originalTasksLink")
            b.eval("document.querySelector('a[href=\\\"#schedule\\\"]').click()")
            b.wait("document.querySelector('a[aria-current=page]')?.textContent === 'Schedule'")
            b.eval("history.back()")
            b.wait("document.querySelector('a[aria-current=page]')?.textContent === 'Tasks'")
            b.eval("history.forward()")
            b.wait("document.querySelector('a[aria-current=page]')?.textContent === 'Schedule'")
            assert b.eval("document.title") == "Schedule | Exo"
            b.eval("document.querySelector('.xc-skip').focus()")
            activate(b)
            assert b.eval("document.activeElement === document.querySelector('main')")
            assert b.eval("location.hash") == "#schedule"
            assert b.eval("!!document.getElementById('xc-live') && !!document.getElementById('xc-where')")
    finally:
        srv.close()


@needs_browser
def test_appearance_controls_persist_choice_and_do_not_change_route_or_draft():
    srv = Served(ThreadSrc(pairs=0))
    try:
        with Browser(width=390, height=850) as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-input')")
            assert b.eval("document.documentElement.dataset.theme") == "dark"
            b.eval("(()=>{const input=document.querySelector('.xc-input');input.value='keep this draft';input.setSelectionRange(2,6);input.dispatchEvent(new Event('input'));})()")
            for choice in ("light", "system", "dark"):
                b.eval(f"(()=>{{const select=document.querySelector('.xc-appearance');select.value='{choice}';select.dispatchEvent(new Event('change'));}})()")
                assert b.eval("document.documentElement.dataset.theme") == choice
                assert b.eval("localStorage.getItem('xc-appearance')") == choice
                assert b.eval("location.hash") == "#chat"
                assert b.eval("[document.querySelector('.xc-input').value,document.querySelector('.xc-input').selectionStart,document.querySelector('.xc-input').selectionEnd]") == ["keep this draft", 2, 6]
            b.eval("document.querySelector('.xc-appearance').value='light';document.querySelector('.xc-appearance').dispatchEvent(new Event('change'))")
            b.eval("location.reload()")
            b.wait("document.documentElement.dataset.theme === 'light' && !!document.querySelector('.xc-appearance')")
            assert b.eval("document.querySelector('.xc-appearance').value") == "light"
            assert b.eval("sessionStorage.getItem('xc-token')") == srv.token
            assert b.eval("Object.keys(localStorage).sort()") == ["xc-appearance"]
    finally:
        srv.close()


@pytest.mark.skipif(not shutil.which("node"), reason="Node is needed")
def test_slow_bootstrap_keeps_route_changes_and_initial_panels_before_mount(tmp_path):
    (tmp_path / "app.mjs").write_text((STATIC / "app.js").read_text().replace('.js"', '.mjs"'))
    (tmp_path / "pet.mjs").write_text('export const createPet=()=>({glyph:{},control:{},start(){},suspend(){}});')
    (tmp_path / "desktop_prefs.mjs").write_text((STATIC / "desktop_prefs.js").read_text())
    (tmp_path / "appearance.mjs").write_text((STATIC / "appearance.js").read_text())
    (tmp_path / "dom.mjs").write_text('''
export const token=()=>"test-token",redeem=async()=>({});
export const h=(tag,attrs,...children)=>({tag,attrs:attrs||{},children,classList:{remove(){}},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},replaceChildren(...c){this.children=c;}});
''')
    (tmp_path / "bus.mjs").write_text('''
export const onState=f=>{globalThis.connectionState=f;f({connected:false});};
export const start=()=>new Promise(resolve=>globalThis.finishStartup=()=>{globalThis.panelsReady=true;resolve();});
export const restart=async()=>{};
''')
    for name in ("chat", "tasks", "schedule", "command", "providers"):
        (tmp_path / f"{name}.mjs").write_text(f'export const mount=()=>{{globalThis.mounts.push(["{name}",globalThis.panelsReady]);return ()=>{{}};}};')
    (tmp_path / "permissions.mjs").write_text('export const banner=()=>({hidden:true}),start=()=>{};')
    (tmp_path / "test.mjs").write_text('''
import assert from "node:assert/strict";
globalThis.sessionStorage={getItem:()=>"test-token"};
globalThis.location={hash:"#chat",pathname:"/",search:""};
globalThis.history={replaceState:(a,b,url)=>{location.hash=url.slice(url.indexOf("#"));}};
const app={children:[],replaceChildren(...children){this.children=children;}};
const handlers={};globalThis.document={getElementById:()=>app};
globalThis.window={addEventListener:(name,fn)=>handlers[name]=fn};
globalThis.mounts=[];globalThis.panelsReady=false;
const loading=import("./app.mjs");
while(!globalThis.finishStartup)await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(typeof handlers.hashchange,"function");assert.deepEqual(mounts,[]);
assert.match(app.children.at(-1).children[0].children.join(""),/Connecting to the workspace/);
location.hash="#tasks";await handlers.hashchange();
assert.equal(app.children[0].children.find(x=>x.attrs?.href==="#tasks").attrs["aria-current"],"page");
assert.deepEqual(mounts,[]);
finishStartup();await loading;
assert.deepEqual(mounts,[["tasks",true]]);
const nav=app.children[0], status=nav.children.at(-1).children[1];
connectionState({connected:true});assert.equal(status.textContent,"Connected");
connectionState({connected:false});assert.equal(status.textContent,"Reconnecting");
connectionState({connected:false,denied:true});assert.match(status.textContent,/Page link expired.*latest complete link/);
assert.equal(nav.children.filter(x=>x.attrs?.["aria-current"]==="page").length,1);
assert.equal(nav.children.find(x=>x.attrs?.href==="#chat").attrs["aria-current"],undefined);
''')
    result = subprocess.run([shutil.which("node"), str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
