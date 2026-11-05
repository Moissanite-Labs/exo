"""Sidebar "5m ago" labels keep aging while the app sits idle: a fake clock advances with no conversation events, and
the labels follow it without a request, a reorder, a list redraw or a moved selection/scroll. Disposable browser and
fake API; no real chat reads or writes."""
import pytest

from browser import Browser, Served, find_chrome
from test_chat import FakeSource

pytestmark = pytest.mark.skipif(not find_chrome(), reason='Chrome is unavailable')

TICK = 30000


@pytest.fixture
def page():
    pytest.importorskip('websockets.sync.client')
    web = Served(FakeSource())
    browser = Browser()
    browser.goto(web.url('command'))
    browser.wait('document.readyState === "complete"')
    browser.eval(r'''(async()=>{
      // Fake clock: Date.now is skewed on demand; ticking intervals are recorded and fired by hand.
      const realNow=Date.now.bind(Date);window.skew=0;Date.now=()=>realNow()+skew;
      window.timers=new Map();let nextTimer=1e6;
      const realSet=window.setInterval.bind(window),realClear=window.clearInterval.bind(window);
      window.setInterval=(f,ms,...a)=>{if(ms!==%d)return realSet(f,ms,...a);const id=++nextTimer;timers.set(id,f);return id;};
      window.clearInterval=id=>{if(timers.has(id))timers.delete(id);else realClear(id);};
      window.advance=ms=>{skew+=ms;for(const f of [...timers.values()])f();};
      window.fakeHidden=false;
      Object.defineProperty(document,'hidden',{configurable:true,get:()=>fakeHidden});
      window.setHidden=v=>{fakeHidden=v;document.dispatchEvent(new Event('visibilitychange'));};
      window.calls=[];
      const iso=ago=>new Date(realNow()-ago).toISOString();
      window.rows=[{id:'s1',title:'Active thread',status:'idle',updated:iso(60e3)}];
      for(let i=2;i<=30;i++)rows.push({id:'s'+i,title:'Thread '+i,status:'idle',updated:iso(i*3600e3)});
      window.fetch=async(path,opt={})=>{
        calls.push(path);
        let data={};
        if(path==='/api/chat/sessions')data={sessions:rows};
        else if(/^\/api\/chat\/sessions\/s\d+$/.test(path))data={session:rows.find(s=>s.id===path.split('/').pop()),messages:[],seq:0};
        else if(path==='/api/complete/commands')data={commands:[],core:[],client:[],guidance:false};
        else if(path==='/api/section/approvals')data={pending:[]};
        else if(path==='/api/providers')data={ready:true};
        return {ok:true,status:200,json:async()=>data};
      };
      sessionStorage.setItem('xc-token','fake');
      const root=document.createElement('main');document.body.replaceChildren(root);
      const chat=await import('/static/chat.js');window.unmount=chat.mount(root);
    })()''' % TICK)
    browser.wait('document.querySelectorAll(".xc-sess").length===30 && !!document.querySelector(".xc-sess.on")')
    browser.eval('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    browser.eval('''(()=>{const l=document.querySelector(".xc-sessions");l.style.maxHeight="120px";l.style.overflowY="auto";
      l.scrollTop=60;window.before={rows:[...document.querySelectorAll(".xc-sess")],scroll:l.scrollTop,
      stamps:[...document.querySelectorAll(".xc-sessions time")].map(t=>t.getAttribute("datetime")),
      updated:rows.map(s=>s.updated)};calls=[];})()''')
    try:
        yield browser
    finally:
        browser.close()
        web.close()


def first_label(page):
    return page.eval('document.querySelector(".xc-sess").textContent')


def test_idle_labels_age_with_the_clock_without_requests_redraws_or_moves(page):
    assert '1m ago' in first_label(page)
    page.eval(f'advance({5 * 60000})')
    assert '6m ago' in first_label(page)
    page.eval(f'advance({60 * 60000})')
    assert '1h ago' in first_label(page)
    assert page.eval('calls') == []
    # same nodes in the same order: nothing redrawn or reordered, selection and scroll where they were
    assert page.eval('[...document.querySelectorAll(".xc-sess")].every((b,i)=>b===before.rows[i])')
    assert page.eval('before.rows.length===document.querySelectorAll(".xc-sess").length')
    assert page.eval('document.querySelector(".xc-sess.on")===before.rows[0]')
    assert page.eval('document.querySelector(".xc-sessions").scrollTop===before.scroll && before.scroll>0')
    # absolute stamps and stored activity times are untouched
    assert page.eval('JSON.stringify([...document.querySelectorAll(".xc-sessions time")].map(t=>t.getAttribute("datetime")))===JSON.stringify(before.stamps)')
    assert page.eval('JSON.stringify(rows.map(s=>s.updated))===JSON.stringify(before.updated)')
    assert page.eval('before.stamps.every(s=>!isNaN(Date.parse(s)))')


def test_hidden_page_keeps_no_timer_and_catches_up_when_shown(page):
    assert page.eval('timers.size') == 1
    page.eval('setHidden(true)')
    assert page.eval('timers.size') == 0
    page.eval(f'skew+={10 * 60000}')
    assert '1m ago' in first_label(page)
    page.eval('setHidden(false)')
    assert '11m ago' in first_label(page)
    assert page.eval('timers.size') == 1
    assert page.eval('calls') == []


def test_unmount_stops_the_clock_and_its_listener(page):
    page.eval('unmount()')
    assert page.eval('timers.size') == 0
    page.eval('setHidden(true);setHidden(false)')
    assert page.eval('timers.size') == 0
    assert '1m ago' in first_label(page)
