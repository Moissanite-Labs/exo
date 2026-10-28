"""Visible title editing through a disposable browser and fake API; no real chat writes."""
import pytest

from browser import Browser, Served, find_chrome
from test_chat import FakeSource

pytestmark = pytest.mark.skipif(not find_chrome(), reason='Chrome is unavailable')


@pytest.fixture
def page():
    pytest.importorskip('websockets.sync.client')
    web = Served(FakeSource())
    browser = Browser()
    browser.goto(web.url('command'))
    browser.wait('document.readyState === "complete"')
    browser.eval(r'''(async()=>{
      window.posts=[];window.refuse=false;window.hold=null;
      window.rows=[{id:'s1',title:'Active thread',status:'live'}, {id:'s2',title:'Other thread',status:'idle'}];
      window.fetch=async(path,opt={})=>{
        const body=JSON.parse(opt.body||'{}');
        if(opt.method==='POST')posts.push({path,body});
        let data={};
        if(path.endsWith('/rename')){
          if(hold)await hold;
          if(refuse)return {ok:false,status:403,json:async()=>({error:'Rename refused'})};
          rows=rows.map(s=>s.id===path.split('/')[4]?{...s,title:body.title}:s);
        }else if(path==='/api/chat/sessions')data={sessions:rows};
        else if(/^\/api\/chat\/sessions\/s[12]$/.test(path))data={session:rows.find(s=>s.id===path.split('/').pop()),messages:[],seq:0};
        else if(path==='/api/complete/commands')data={commands:[],core:[],client:[],guidance:false};
        else if(path==='/api/section/approvals')data={pending:[]};
        else if(path==='/api/providers')data={ready:true};
        return {ok:true,status:200,json:async()=>data};
      };
      sessionStorage.setItem('xc-token','fake');
      const root=document.createElement('main');document.body.replaceChildren(root);
      const chat=await import('/static/chat.js');window.unmount=chat.mount(root);
    })()''')
    browser.wait('document.querySelectorAll(".xc-rename").length===2 && !!document.querySelector(".xc-sess.on")')
    browser.eval('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    browser.eval('posts=[];document.querySelector("textarea").value="Keep my current draft"')
    try:
        yield browser
    finally:
        browser.close()
        web.close()


def enter(page):
    # Enter's character event activates native buttons and implicit form submit.
    page.call('Input.dispatchKeyEvent', type='keyDown', key='Enter', code='Enter',
              windowsVirtualKeyCode=13, text='\r')
    page.call('Input.dispatchKeyEvent', type='keyUp', key='Enter', code='Enter', windowsVirtualKeyCode=13)


def test_visible_rename_targets_other_thread_without_switching_or_sending(page):
    page.eval('document.querySelectorAll(".xc-rename")[1].click()')
    assert page.eval('document.activeElement.getAttribute("aria-label")') == 'Thread title'
    page.type('[aria-label="Thread title"]', 'Renamed other')
    page.eval('document.querySelector(".xc-rename-editor [type=submit]").click()')
    page.wait('document.querySelector(".xc-rename-editor").hidden')
    assert page.eval('posts') == [{'path': '/api/chat/sessions/s2/rename', 'body': {'title': 'Renamed other'}}]
    assert page.eval('document.querySelector(".xc-sess.on").textContent.includes("Active thread")')
    assert page.eval('document.querySelectorAll(".xc-sess")[1].textContent.includes("Renamed other")')
    assert page.eval('document.querySelector("textarea").value') == 'Keep my current draft'
    assert page.eval('document.querySelector(".xc-strip").textContent.includes("working")')
    assert page.eval('document.activeElement.getAttribute("aria-label")') == 'Rename Renamed other'


def test_keyboard_enter_save_and_escape_cancel_do_not_submit_chat(page):
    page.eval('document.querySelector(".xc-rename").focus()')
    enter(page)
    page.wait('!document.querySelector(".xc-rename-editor").hidden')
    page.type('[aria-label="Thread title"]', 'Cancelled draft')
    page.press('Escape', vk=27)
    assert page.eval('document.querySelector(".xc-rename-editor").hidden && posts.length===0')
    assert page.eval('document.activeElement.classList.contains("xc-rename")')
    enter(page)
    page.type('[aria-label="Thread title"]', 'Keyboard title')
    enter(page)
    page.wait('posts.length===1 && document.querySelector(".xc-rename-editor").hidden')
    assert page.eval('posts[0].body.title') == 'Keyboard title'
    assert page.eval('document.querySelector("textarea").value') == 'Keep my current draft'


def test_cancel_blank_error_and_duplicate_save_preserve_input(page):
    page.eval('document.querySelector(".xc-rename").click()')
    page.eval('document.querySelector(".xc-rename-editor input").value="   "')
    enter(page)
    assert page.eval('posts.length===0 && document.querySelector("[role=alert]").textContent.includes("Enter a thread title")')
    page.eval('document.querySelector(".xc-rename-editor input").value="Retry title";refuse=true;hold=new Promise(r=>window.release=r);void 0')
    enter(page)
    enter(page)
    assert page.eval('posts.length===1 && document.querySelector(".xc-rename-editor [type=submit]").disabled')
    page.eval('release();hold=null')
    page.wait('!document.querySelector(".xc-rename-editor [type=submit]").disabled')
    assert page.eval('document.querySelector("[role=alert]").textContent.includes("Rename refused")')
    assert page.eval('document.querySelector(".xc-rename-editor input").value') == 'Retry title'
    page.eval('document.querySelector(".xc-rename-editor [type=button]").click()')
    assert page.eval('document.querySelector(".xc-rename-editor").hidden')
    assert page.eval('document.querySelector("textarea").value') == 'Keep my current draft'



def test_pending_save_after_unmount_does_not_redraw_or_restore_focus(page):
    page.eval('document.querySelector(".xc-rename").click()')
    page.type('[aria-label="Thread title"]', 'Late title')
    page.eval('hold=new Promise(r=>window.release=r);void 0')
    enter(page)
    page.wait('posts.length===1')
    page.eval('''(()=>{
      window.oldList=document.querySelector('.xc-sessions');
      window.oldInput=document.querySelector('.xc-rename-editor input');
      unmount();window.lateFocus=0;
      for(const el of document.querySelectorAll('input,button'))el.focus=()=>lateFocus++;
      const next=document.createElement('button');next.textContent='Next view';
      document.body.replaceChildren(next);next.focus();
      release();hold=null;
    })()''')
    page.eval('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    assert page.eval('lateFocus===0 && !oldList.textContent.includes("Late title")')
    assert page.eval('oldInput.value') == ''
    assert page.eval('document.activeElement.textContent') == 'Next view'
