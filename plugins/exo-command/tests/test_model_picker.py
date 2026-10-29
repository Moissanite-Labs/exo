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
    browser.goto(web.url())
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
      window.posts=[];
      const models={models:[{route:'alpha:sonnet',name:'Sonnet',provider:'Alpha'}],
                    available:[{route:'beta:sonnet',name:'Sonnet',provider:'Beta'}]};
      window.fetch=async (path,opt={})=>{
        if(opt.method==='POST') posts.push({path,body:JSON.parse(opt.body||'{}')});
        let data={};
        if(path.endsWith('/models'))data=models;
        else if(path==='/api/providers')data={providers:[],ready:true};
        else if(path==='/api/providers/default')data={applied:''' + applied + r'''};
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
    assert page.eval('posts.length') == 0
    key(page, 'Enter')
    page.wait('posts.length === 1')
    assert page.eval('posts') == [{'path': '/api/providers/default', 'body': {'ref': 'beta:sonnet', 'role': 'main'}}]
    page.wait('document.querySelector(".xc-model-status").textContent.includes("Default set")')


def test_providers_keeps_the_search_and_focus_across_the_refresh_after_a_save(page):
    mount_view(page, 'providers')
    page.wait('!!document.querySelector(".xc-prov-def [role=combobox]")')
    page.eval('window.el0 = document.querySelector(".xc-prov-def [role=combobox]")')
    page.eval('window.read0 = document.querySelector(".xc-phead-read > *")')
    page.wait('window.read0 !== null')
    search(page, 'beta sonnet')
    key(page, 'Enter')
    page.wait('document.querySelector(".xc-prov-def .xc-prov-res") !== null')
    # The page itself must still redraw after a save (the picker staying put must not freeze the readout).
    page.wait('document.querySelector(".xc-phead-read > *") !== window.read0')
    # The refresh that follows a save must not rebuild the picker: same field, same query, same focus.
    assert page.eval('document.querySelector(".xc-prov-def [role=combobox]") === window.el0')
    assert page.eval('document.querySelector(".xc-prov-def [role=combobox]").value') == 'beta sonnet'
    assert page.eval('document.activeElement === window.el0')
    assert page.eval('document.querySelector(".xc-prov-def .xc-prov-res").textContent') == 'Default set to beta:sonnet.'
    assert page.eval('document.querySelectorAll(".xc-prov-def [role=combobox]").length') == 1


def test_providers_marks_a_route_no_role_uses_yet_as_available(page):
    mount_view(page, 'providers')
    page.wait('document.querySelectorAll(".xc-model-option").length === 2')
    tags = page.eval('[...document.querySelectorAll(".xc-model-option")].map(o => [o.querySelector(".xc-model-route").textContent, o.querySelector(".xc-model-tag") ? o.querySelector(".xc-model-tag").textContent : ""])')
    # alpha:sonnet is a configured role; beta:sonnet is only listed as available to the provider.
    assert sorted(tags) == [['alpha:sonnet', ''], ['beta:sonnet', 'available']]
    search(page, 'beta')
    assert page.eval('document.querySelectorAll(".xc-model-option").length') == 1
    assert page.eval('document.querySelector(".xc-model-tag").textContent') == 'available'


def mount_providers(page):
    """The Providers page against a fake core with two providers, where the model list can be held back, failed
    or answered out of order. Nothing here reads or writes a key."""
    page.eval(r'''(async()=>{
      window.posts=[];
      window.state={def:'alpha'};
      window.modelsAnswer=async()=>({models:[{route:'alpha:sonnet',name:'Sonnet',provider:'Alpha'}],
                                     available:[{route:'beta:sonnet',name:'Sonnet',provider:'Beta'}]});
      const providers=()=>({providers:[
        {provider:'alpha',name:'Alpha',default:window.state.def==='alpha',needs:'key',key:'found',source:'keychain',models:1},
        {provider:'beta',name:'Beta',default:window.state.def==='beta',needs:'key',key:'found',source:'keychain',models:1}],
        ready:true,store:'keychain'});
      window.fetch=async (path,opt={})=>{
        if(opt.method==='POST'){
          const body=JSON.parse(opt.body||'{}');
          posts.push({path,body});
          if(path==='/api/providers/default')window.state.def=String(body.ref||'').split(':')[0];
          const out=path==='/api/providers/default'?{applied:true}:{ok:true,latency_ms:7};
          return {ok:true,status:200,json:async()=>out};
        }
        let data={};
        if(path.endsWith('/models'))data=await window.modelsAnswer();
        else if(path==='/api/providers')data=providers();
        return {ok:true,status:200,json:async()=>data};
      };
      sessionStorage.setItem('xc-token','offline-test');
      const root=document.createElement('main');document.body.replaceChildren(root);
      const view=await import('/static/providers.js');
      window.unmount=view.mount(root);
    })()''')


def press_test(page, index=0):
    page.eval('[...document.querySelectorAll(".xc-prow")][' + str(index) + '].querySelectorAll(".xc-prow-act button")'
              '.forEach(b => b.textContent === "Test" && b.click())')


def test_providers_default_readout_and_mark_follow_the_save(page):
    mount_providers(page)
    page.wait('document.querySelectorAll(".xc-model-option").length === 2')
    # alpha holds the default, so the picker marks alpha:sonnet as the current route before anything is saved.
    assert page.eval('document.querySelector(".xc-model-current").closest(".xc-model-option").querySelector(".xc-model-route").textContent') == 'alpha:sonnet'
    assert page.eval('document.querySelector(".xc-phead-read").textContent').count('alpha') == 1
    assert page.eval('document.querySelector(".xc-prow .xc-defmark") !== null')
    search(page, 'beta sonnet')
    key(page, 'Enter')
    page.wait('posts.length === 1')
    page.wait('document.querySelector(".xc-phead-read").textContent.includes("beta")')
    assert page.eval('document.querySelector(".xc-prow .xc-defmark").closest(".xc-prow").querySelector(".xc-prow-name").textContent') == 'beta'
    page.wait('document.querySelector(".xc-model-current") !== null')
    assert page.eval('document.querySelector(".xc-model-current").closest(".xc-model-option").querySelector(".xc-model-route").textContent') == 'beta:sonnet'


def test_a_slow_model_list_cannot_overwrite_a_newer_one(page):
    mount_providers(page)
    page.wait('document.querySelectorAll(".xc-model-option").length === 2')
    # The next read is held open until the test lets it go.
    page.eval(r"""(()=>{window.modelsAnswer=()=>new Promise(r=>{window.releaseSlow=()=>{window.released=true;
        r({models:[{route:'stale:one',name:'Stale',provider:'Alpha'}],available:[]})}})})()""")
    press_test(page, 1)
    page.wait('typeof window.releaseSlow === "function"')
    # A read that starts later answers at once, so it is the newer answer.
    page.eval('window.modelsAnswer=async()=>({models:[{route:"fresh:two",name:"Fresh",provider:"Alpha"}],available:[]})')
    press_test(page, 0)
    page.wait('document.querySelectorAll(".xc-model-option").length === 1')
    assert page.eval('document.querySelector(".xc-model-route").textContent') == 'fresh:two'
    page.eval('window.releaseSlow()')
    page.eval('new Promise(r=>setTimeout(r,200)).then(()=>true)')
    # The late answer is not drawn: the page still shows the list that arrived after it.
    assert page.eval('document.querySelectorAll(".xc-model-option").length') == 1
    assert page.eval('document.querySelector(".xc-model-route").textContent') == 'fresh:two'


def test_a_failed_model_list_keeps_the_last_good_one_and_offers_a_retry(page):
    mount_providers(page)
    page.wait('document.querySelectorAll(".xc-model-option").length === 2')
    page.eval('window.modelsAnswer=async()=>({error:"core unreachable (ConnectError)",error_class:"transport",retryable:true})')
    press_test(page)
    page.wait('!!document.querySelector(".xc-prov-def .xc-warn")')
    # A transport failure is retryable, so it gets a button, and the picker keeps the list it already had.
    assert page.eval('document.querySelector(".xc-prov-def .xc-warn").textContent.includes("core unreachable")')
    assert page.eval('[...document.querySelectorAll(".xc-prov-def-act button")].map(b => b.textContent).join()') == 'Try again'
    assert page.eval('document.querySelectorAll(".xc-model-option").length') == 2
    page.eval('window.modelsAnswer=async()=>({models:[{route:"fresh:two",name:"Fresh",provider:"Alpha"}],available:[]})')
    page.eval('document.querySelector(".xc-prov-def-act button").click()')
    page.wait('document.querySelectorAll(".xc-model-option").length === 1')
    assert page.eval('document.querySelector(".xc-model-route").textContent') == 'fresh:two'
    # A refusal is not retryable: no button, and the last good list stays where it is.
    page.eval('window.modelsAnswer=async()=>({error:"the core refused the page token",error_class:"unauthorized",retryable:false})')
    press_test(page)
    page.wait('document.querySelectorAll(".xc-prov-def-act button").length === 0')
    note = page.eval('document.querySelector(".xc-prov-def .xc-warn").textContent')
    assert "This list is from the last good read." in note and "Reopen this tab after the core restarts." in note
    assert page.eval('document.querySelectorAll(".xc-model-option").length') == 1


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
    assert page.eval('posts') == [{'path': '/api/providers/default', 'body': {'ref': 'beta:sonnet', 'role': 'main'}}]
