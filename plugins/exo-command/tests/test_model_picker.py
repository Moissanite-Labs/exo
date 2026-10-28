"""Search and keyboard selection through the actual browser DOM. No real core or credentials."""
import pytest

from browser import Browser, Served, find_chrome
from test_chat import FakeSource

pytestmark = pytest.mark.skipif(not find_chrome(), reason='Chrome is unavailable')


@pytest.fixture
def page():
    pytest.importorskip('websockets.sync.client')
    web = Served(FakeSource())
    browser = Browser()
    browser.goto(web.url('providers'))
    browser.wait('document.readyState === "complete"')
    try:
        yield browser
    finally:
        browser.close()
        web.close()


def prepare(page, extra=''):
    page.eval('''(async()=>{
      const {createModelPicker} = await import('/static/model_picker.js');
      window.applied=[]; window.cancelled=0;
      window.picker=createModelPicker({label:'Main model', selected:'alpha:sonnet',
        rows:[{route:'alpha:sonnet',name:'Sonnet',provider:'Alpha'},
              {route:'beta:sonnet',name:'Sonnet',provider:'Beta'},
              {route:'openai:gpt-fast',name:'GPT Fast',provider:'OpenAI'},
              {route:'beta:sonnet',name:'duplicate'}],
        onCancel:()=>{window.cancelled++;document.querySelector('#return-focus').focus()},
        onApply:async route=>{window.applied.push(route);''' + extra + '''}});
      document.body.replaceChildren(picker.element);
      const button=document.createElement('button');button.id='return-focus';document.body.append(button);
      picker.focus();
    })()''')


def search(page, text):
    page.eval('(()=>{const field=document.querySelector("[role=combobox]");field.focus();field.value=' + repr(text) + ';field.dispatchEvent(new Event("input",{bubbles:true}));})()')


def key(page, value):
    page.eval('document.querySelector("[role=combobox]").dispatchEvent(new KeyboardEvent("keydown",{key:' + repr(value) + ',bubbles:true,cancelable:true}))')


def test_filter_keyboard_chooses_exact_route_without_applying_while_typing(page):
    prepare(page)
    assert page.eval('document.querySelectorAll("[role=option]").length') == 3
    search(page, 'sonnet')
    assert page.eval('applied.length') == 0
    assert page.eval('document.querySelectorAll("[role=option]").length') == 2
    key(page, 'ArrowDown')
    assert page.eval('document.querySelector("[aria-selected=true]").textContent') == 'Sonnetbeta:sonnet'
    key(page, 'Enter')
    page.wait('applied.length === 1')
    assert page.eval('applied') == ['beta:sonnet']
    search(page, 'openai fast')
    key(page, 'Enter')
    page.wait('applied.length === 2')
    assert page.eval('applied') == ['beta:sonnet', 'openai:gpt-fast']
    key(page, 'Escape')
    assert page.eval('cancelled === 1 && document.activeElement.id === "return-focus"')


def test_empty_loading_error_and_pending_duplicate_guards(page):
    prepare(page, 'await new Promise(resolve=>window.release=resolve);')
    search(page, 'no such model')
    key(page, 'Enter')
    assert page.eval('applied.length === 0 && document.querySelector("[role=status]").textContent.includes("No matching")')
    search(page, 'beta')
    key(page, 'Enter')
    key(page, 'Enter')
    page.wait('applied.length === 1')
    assert page.eval('document.querySelector("[role=combobox]").disabled')
    page.eval('release()')
    page.wait('!document.querySelector("[role=combobox]").disabled')
    page.eval('picker.setError("Models failed to load")')
    assert page.eval('document.querySelector("[role=status]").textContent === "Models failed to load"')
    assert page.eval('document.querySelector(".xc-primary").disabled')
    page.eval('picker.setRows([])')
    assert page.eval('document.querySelector("[role=status]").textContent.includes("No models listed")')


def mount_view(page, view, applied='true'):
    page.eval(r'''(async()=>{
      window.posts=[];window.defaultProvider="alpha";
      const models={models:[{route:'alpha:sonnet',name:'Sonnet',provider:'Alpha'}],
                    available:[{route:'beta:sonnet',name:'Sonnet',provider:'Beta'}]};
      window.fetch=async (path,opt={})=>{
        if(opt.method==='POST') posts.push({path,body:JSON.parse(opt.body||'{}')});
        let data={};
        if(path.endsWith('/models'))data=models;
        else if(path==='/api/providers')data={providers:['alpha','beta'].map(provider=>({provider,ready:true,default:provider===defaultProvider,needs:'cli'})),ready:true};
        else if(path==='/api/providers/default'){defaultProvider=JSON.parse(opt.body).ref.split(':')[0];data={applied:''' + applied + r'''}}
        else if(path==='/api/complete/commands')data={commands:[{name:'model'},{name:'default'}],core:[],client:[],guidance:false};
        else if(path==='/api/chat/sessions')data={sessions:[{id:'s1',model:'alpha:sonnet'},{id:'s2',model:'alpha:sonnet'}]};
        else if(/^\/api\/chat\/sessions\/s[12]$/.test(path))data={session:{id:path.split('/').pop(),model:'alpha:sonnet'},messages:[],seq:0};
        return {ok:true,status:200,json:async()=>data};
      };
      sessionStorage.setItem('xc-token','offline-test');
      const root=document.createElement('main');document.body.replaceChildren(root);
      const view=await import('/static/''' + view + '''.js');
      window.unmount=view.mount(root);
    })()''')


def test_providers_search_saves_exact_default_only_after_enter(page):
    mount_view(page, 'providers')
    page.wait('!!document.querySelector("[role=combobox]")')
    search(page, 'beta sonnet')
    assert page.eval('posts.filter(p=>p.path.startsWith("/api/providers/")).length') == 0
    key(page, 'Enter')
    page.wait('posts.filter(p=>p.path.startsWith("/api/providers/")).length === 1')
    assert page.eval('posts.filter(p=>p.path.startsWith("/api/providers/"))') == [{'path': '/api/providers/default', 'body': {'ref': 'beta:sonnet', 'role': 'main'}}]
    page.wait('document.querySelector(".xc-model-status").textContent.includes("Default set")')


def command(page, text):
    page.eval('(()=>{const input=document.querySelector(".xc-input");input.value=' + repr(text) + ';input.dispatchEvent(new Event("input"));document.querySelector(".xc-composer .xc-btnrow button").click()})()')


@pytest.mark.parametrize(('text', 'path', 'body'), [
    ('/model', '/api/chat/sessions/s1/model', {'model': 'beta:sonnet'}),
    ('/default', '/api/providers/default', {'ref': 'beta:sonnet', 'role': 'main'}),
])
def test_chat_search_preserves_thread_and_default_routes_and_returns_focus(page, text, path, body):
    mount_view(page, 'chat')
    page.wait('document.querySelectorAll(".xc-sess").length === 2')
    command(page, text)
    page.wait('!!document.querySelector("[role=combobox]")')
    assert page.eval('document.querySelector(".xc-model-note").textContent.includes("shared core")')
    assert page.eval('document.querySelector(".xc-model-note").textContent.includes("default for new threads")') == (text == '/default')
    search(page, 'beta sonnet')
    assert page.eval('posts.filter(p=>p.path.endsWith("/model")||p.path.endsWith("/default")).length') == 0
    key(page, 'Enter')
    page.wait('posts.some(p=>p.path===' + repr(path) + ')')
    assert page.eval('posts.filter(p=>p.path.endsWith("/model")||p.path.endsWith("/default"))') == [{'path': path, 'body': body}]
    page.wait('document.activeElement.classList.contains("xc-input")')


def test_chat_escape_and_old_picker_cannot_change_new_thread(page):
    mount_view(page, 'chat')
    page.wait('document.querySelectorAll(".xc-sess").length === 2')
    command(page, '/model')
    page.wait('!!document.querySelector("[role=combobox]")')
    search(page, 'beta')
    key(page, 'Escape')
    assert page.eval('document.querySelector(".xc-model-picker").hidden && document.activeElement.classList.contains("xc-input")')
    command(page, '/model')
    page.wait('!document.querySelector(".xc-model-picker").hidden')
    page.eval('window.oldSearch=document.querySelector("[role=combobox]");document.querySelectorAll(".xc-sess")[1].click()')
    page.eval('oldSearch.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true}))')
    assert page.eval('posts.filter(p=>p.path.endsWith("/model")||p.path.endsWith("/default")).length') == 0


def test_loading_and_apply_error_keep_search_available_for_retry(page):
    page.eval('''(async()=>{
      const {createModelPicker}=await import('/static/model_picker.js');
      window.attempts=0;
      window.picker=createModelPicker({label:'Model',onApply:async()=>{if(++attempts===1)throw Error('Core refused this model');}});
      document.body.replaceChildren(picker.element);picker.focus();
    })()''')
    assert page.eval('document.querySelector("[role=status]").textContent.includes("Loading models") && document.querySelector(".xc-primary").disabled')
    search(page, 'beta')
    key(page, 'Enter')
    assert page.eval('attempts') == 0
    page.eval('picker.setRows([{route:"beta:sonnet",name:"Sonnet"}])')
    key(page, 'Enter')
    page.wait('document.querySelector("[role=status]").textContent === "Core refused this model"')
    assert page.eval('document.activeElement.value === "beta" && !document.activeElement.disabled')
    key(page, 'Enter')
    page.wait('attempts === 2')


def test_model_search_ignores_name_punctuation_and_prioritizes_exact_route(page):
    prepare(page)
    page.eval('picker.setRows([{route:"alpha:gpt-6-pro",name:"GPT 6 Pro"},{route:"beta:sonnet-4",name:"Claude Sonnet 4"},{route:"beta:gpt6",name:"GPT 6"}])')
    search(page, 'gpt6')
    assert page.eval('document.querySelectorAll("[role=option]").length') == 2
    search(page, 'sonnet4')
    key(page, 'Enter')
    page.wait('applied.length === 1')
    assert page.eval('applied') == ['beta:sonnet-4']
    search(page, 'beta:gpt6')
    key(page, 'Enter')
    page.wait('applied.length === 2')
    assert page.eval('applied')[-1] == 'beta:gpt6'


def test_providers_next_start_reply_is_not_shown_as_applied_now(page):
    mount_view(page, 'providers', applied='"next start"')
    page.wait('!!document.querySelector("[role=combobox]")')
    search(page, 'beta')
    key(page, 'Enter')
    page.wait('document.querySelector(".xc-model-status").textContent.includes("core will use it on next start")')
    assert page.eval('posts.filter(p=>p.path.startsWith("/api/providers/"))') == [{'path': '/api/providers/default', 'body': {'ref': 'beta:sonnet', 'role': 'main'}}]



@pytest.mark.parametrize('apply_with', ['enter', 'button'])
def test_providers_default_refresh_updates_readout_and_row_without_losing_search(page, apply_with):
    mount_view(page, 'providers')
    page.wait('!!document.querySelector("[role=combobox]")')
    search(page, 'beta sonnet')
    if apply_with == 'enter':
        key(page, 'Enter')
    else:
        page.eval('document.querySelector(".xc-prov-def .xc-primary").click()')
    page.wait('document.querySelector(".xc-prow.def .xc-prow-name")?.textContent === "beta"')
    assert page.eval('document.querySelector(".xc-phead-read").textContent.includes("Defaultbeta")')
    assert page.eval('document.activeElement.classList.contains("xc-model-search") && document.activeElement.value === "beta sonnet"')
    assert page.eval('document.querySelectorAll("[role=option]").length') == 1
    assert page.eval('document.querySelector(".xc-model-current").closest("[role=option]").textContent.includes("beta:sonnet")')
    assert page.eval('posts.filter(p=>p.path.startsWith("/api/providers/"))') == [
        {'path': '/api/providers/default', 'body': {'ref': 'beta:sonnet', 'role': 'main'}}]
