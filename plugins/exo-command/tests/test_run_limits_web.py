"""Core capability probe precedes global mutations; the composer never sends these as turns."""
from exo.protocol import schema as s
from exo.core.run_limits import RunLimits
from exo_command.source import SourceError
from test_chat import FakeSource, call, centre


class Source(FakeSource):
    def __init__(self,old=False,denied=False):
        super().__init__();self.policy=RunLimits();self.old=old;self.denied=denied
    async def call(self,b):
        self.calls.append(b)
        if isinstance(b,s.CommandRun) and b.name=='limits':
            if self.old:return {'output':'not supported'}
            if b.args!='json':
                if self.denied:raise SourceError('forbidden','principal only')
                self.policy.update(b.args)
            return {'output':self.policy.status(24,24,900)}
        return {}


def test_web_probes_then_mutates_and_preserves_principal_refusal():
    src=Source();c=centre(src)
    code,result=call(c,'POST','/api/chat/sessions/s1/command',{'name':'limits','args':'worker-timeout 15m'})
    assert code==200 and result['output']['worker_timeout_s']==900
    assert [b.args for b in src.calls]==['json','worker-timeout 15m']
    src=Source(denied=True)
    assert call(centre(src),'POST','/api/chat/sessions/s1/command',{'name':'limits','args':'rounds off'})[0]==403
    assert src.policy.snapshot().rounds is None


def test_old_core_never_receives_mutation_or_turn_and_invalid_body_is_rejected():
    src=Source(old=True);c=centre(src)
    assert call(c,'POST','/api/chat/sessions/s1/command',{'name':'limits','args':'rounds off'})[0]==400
    assert len(src.calls)==1 and src.calls[0].args=='json'
    assert not any(isinstance(b,s.TurnSend) for b in src.calls)
    src=Source()
    assert call(centre(src),'POST','/api/chat/sessions/s1/command',{'name':'limits','args':True})[0]==400
    assert src.calls==[]


def test_mounted_composer_routes_limits_and_refuses_old_core_without_a_turn():
    import importlib.util
    import pytest
    from browser import Browser, Served, find_chrome
    from test_model_command_web import ModelSrc, enter
    if not find_chrome() or not importlib.util.find_spec('websockets'):
        pytest.skip('headless Chrome/websockets unavailable')
    class BrowserSource(ModelSrc):
        def __init__(self,old):
            super().__init__();self.old=old;self.policy=RunLimits()
        async def call(self,b):
            if isinstance(b,s.CommandRun) and b.name=='limits':
                self.calls.append(b)
                if self.old:return {'output':'unknown old command'}
                if b.args!='json':self.policy.update(b.args)
                return {'output':self.policy.status(24,24,900)}
            return await super().call(b)
    for old in (False,True):
        src=BrowserSource(old);web=Served(src)
        try:
            with Browser()as browser:
                browser.goto(web.url());browser.wait('!!document.querySelector(".xc-msg")')
                browser.eval('new Promise(r=>setTimeout(r,300))')
                enter(browser,'/limits rounds off')
                if old:
                    browser.wait('document.body.textContent.includes("does not support /limits")')
                    assert [b.args for b in src.of('command.run')if b.name=='limits']==['json']
                else:
                    browser.wait('document.body.textContent.includes("Core scope: all conversations and delegated workers")')
                    assert src.policy.snapshot().rounds==0
                assert not src.sent()
        finally:web.close()
