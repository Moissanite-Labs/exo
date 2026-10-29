"""Render the workspace over synthetic records, without core or credential access.

    PYTHONPATH=src:plugins/exo-command python scripts/shots_workspace.py OUTDIR

Uses the existing disposable headless-Chrome driver and screenshot fixtures.
"""
import json
import datetime as dt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'plugins/exo-command'), str(ROOT / 'plugins/exo-command/tests')]
from browser import Browser, Served  # noqa: E402
from shots_web_tabs import Demo, SNAP, stub_providers  # noqa: E402
from exo_command import providers  # noqa: E402

def text(value):
    return [{'type': 'text', 'text': value}]

class Workspace(Demo):
    def __init__(self, empty=False):
        super().__init__(True)
        self.empty = empty

    async def call(self, body):
        kind = type(body).TYPE
        if kind == 'session.list':
            return {'sessions': [
                {'id':'release','title':'Prepare the next release','agent':'atlas','model':'demo:reasoning-model','status':'idle','updated':dt.datetime.now(dt.timezone.utc).isoformat()},
                {'id':'research','title':'Research notes and open questions','agent':'atlas','model':'demo:reasoning-model','status':'idle','updated':(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=2)).isoformat()},
                {'id':'site','title':'A clearer introduction to Exo','agent':'atlas','model':'demo:reasoning-model','status':'idle','updated':(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=1)).isoformat()},
            ]}
        if kind == 'session.get':
            sid = body.session
            return {'session':{'id':sid,'model':'demo:reasoning-model','agent':'atlas','cwd':'/workspace/exo'}, 'seq':0,
                    'messages': [] if self.empty else [
                {'id':'u1','role':'user','content':text('Prepare a small, well-tested release. Keep the recovery work together and tell me what needs a decision.')},
                {'id':'a1','role':'assistant','content':text('I’ll check the changes and their tests, then prepare the release notes. I’ll keep any open decision beside the evidence.')},
                {'id':'tool1','role':'assistant','content':[], 'tool_calls':[{'id':'read1','name':'read_file','arguments':{'path':'CHANGELOG.md'}}]},
                {'id':'result1','role':'tool','content':[], 'tool_results':[{'call_id':'read1','content':text('Three changes ready for review. No live installation changed.')} ]},
                {'id':'a2','role':'assistant','content':text('## Ready for review\n\nThis synthetic sample shows a review summary with a table, an open decision and a tool result.\n\n| Change | Evidence |\n| --- | --- |\n| Conversation layout | Sample text and controls |\n| Review table | Sample evidence cells |\n| Tool result | Sample file-read record |\n\nThe remaining choice is **when to test the native build**. The release notes include the current limits and a rollback step.\n\nNo live installation has changed.')},
            ]}
        return await super().call(body)

def render(out):
    out.mkdir(parents=True, exist_ok=True)
    providers.handle = stub_providers(True)
    rows=[]
    for empty in [False, True]:
        srv=Served(Workspace(empty), token='sample-data')
        srv.centre.snapshot=lambda *args, **kwargs:SNAP
        try:
            for width in [1440, 1024, 768, 390, 320]:
                if empty and width not in [1440,390]:
                    continue
                with Browser(width=width,height=900) as b:
                    b.goto(srv.url())
                    b.wait("document.querySelector('.xc-chat') && document.querySelectorAll('.xc-sess').length === 3")
                    if not empty:
                        b.wait("document.querySelector('.xc-log').textContent.includes('Ready for review')")
                    for theme in ['dark','light']:
                        b.eval(f"(()=>{{const choice=document.querySelector('.xc-appearance');choice.value='{theme}';choice.dispatchEvent(new Event('change'));}})()")
                        time.sleep(.7)
                        name=f"{'empty' if empty else 'chat'}-{theme}-{width}"
                        b.shot(out/(name+'.png'))
                        rows.append({'screen':name,**b.eval("(()=>{const i=document.querySelector('.xc-composer textarea'),r=i.getBoundingClientRect();return {pageWidth:innerWidth,scrollWidth:document.documentElement.scrollWidth,inputWidth:r.width,inputTop:r.top,inputHeight:r.height,bodyFont:getComputedStyle(document.querySelector('.xc-log')).fontFamily}})()")})
                    if width in [1440,390] and not empty:
                        for view in ['tasks','schedule','command','providers']:
                            b.goto(srv.url(view))
                            b.wait("document.querySelector('.xc-main') && !document.querySelector('.xc-loading')")
                            time.sleep(.5)
                            b.shot(out/f'{view}-light-{width}.png')
                            rows.append({'screen':f'{view}-light-{width}',**b.eval("({pageWidth:innerWidth,scrollWidth:document.documentElement.scrollWidth})")})
        finally:
            srv.close()
    (out/'geometry.json').write_text(json.dumps(rows,indent=2))
    print(json.dumps(rows,indent=2))

def main(out):
    previous = providers.handle
    try:
        render(out)
    finally:
        providers.handle = previous


if __name__=='__main__':
    main(Path(sys.argv[1]))
