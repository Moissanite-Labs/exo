"""Independent browser checks for a readable work surface under real content and theme changes."""
import importlib.util

import pytest

from browser import Browser, Served, find_chrome
from test_layout_web import ThreadSrc, parts

pytestmark = pytest.mark.skipif(not find_chrome() or not importlib.util.find_spec("websockets"), reason="Chrome or websockets missing")


class DesignSource(ThreadSrc):
    def __init__(self):
        super().__init__(pairs=25)
        self.sent = []

    async def call(self, body):
        kind = type(body).TYPE
        if kind == "session.list":
            return {"sessions": [{"id": sid, "title": title, "agent": "atlas", "model": "m", "status": "idle"}
                                 for sid, title in (("s1", "A readable selected conversation"), ("s2", "Another conversation"))]}
        result = await super().call(body)
        if kind == "session.get":
            result["messages"].append({"id": "readability", "role": "assistant", "content": parts(
                "Read the result, inspect the code, and choose the next action.\n\n"
                "```python\nresult = '" + "long_token_" * 60 + "'\n```\n\n"
                "| Evidence | Result |\n| --- | --- |\n| A recorded source | Review still required |")})
        if kind == "turn.send":
            self.sent.append(body)
        return result


READOUT = r"""(()=>{
  const input=document.querySelector('.xc-composer .xc-input'),ghost=document.querySelector('.xc-ghost');
  const prose=document.querySelector('.xc-asst .xc-text'),code=document.querySelector('.xc-code');
  const selected=document.querySelector('.xc-sess.on'),other=document.querySelector('.xc-sess:not(.on)');
  const css=e=>getComputedStyle(e),rgb=value=>(value.match(/[\d.]+/g)||[]).map(Number);
  const background=e=>{for(let n=e;n;n=n.parentElement){const c=css(n).backgroundColor,a=rgb(c);if(a.length===3 || a[3]===1)return c;}return css(document.body).backgroundColor;};
  const luminance=color=>rgb(color).slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
  const ratio=(a,b)=>{const x=luminance(a),y=luminance(b);return(Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
  const i=css(input),g=css(ghost),p=css(prose),c=css(code),s=css(selected);
  return {viewport:innerWidth,page:document.documentElement.scrollWidth,inputWidth:input.getBoundingClientRect().width,
    proseSize:parseFloat(p.fontSize),proseFont:p.fontFamily,codeFont:c.fontFamily,codeOverflow:c.overflowX,
    codeWidth:code.clientWidth,codeContent:code.scrollWidth,
    selectedCurrent:selected.getAttribute('aria-current'),selectedBackground:s.backgroundColor,otherBackground:css(other).backgroundColor,
    proseContrast:ratio(p.color,background(prose)),selectedContrast:ratio(s.color,background(selected)),
    selectedMarkerContrast:ratio(s.borderLeftColor,background(selected)),focusContrast:ratio(i.borderBottomColor,background(input)),
    ghostAligned:['fontFamily','fontSize','lineHeight','letterSpacing','paddingTop','paddingLeft','paddingRight','paddingBottom'].every(k=>i[k]===g[k]),
    ghostVisible:!ghost.hidden,focused:document.activeElement===input};
})()"""


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_responsive_readability_selection_and_writing_with_long_content(theme):
    src = DesignSource(); srv = Served(src)
    try:
        with Browser(width=1440, height=900) as b:
            b.goto(srv.url()); b.wait("!!document.querySelector('.xc-code') && !!document.querySelector('.xc-sess:not(.on)')")
            b.call('Emulation.setFocusEmulationEnabled', enabled=True)
            b.eval(f"(()=>{{const select=document.querySelector('.xc-appearance');select.value='{theme}';select.dispatchEvent(new Event('change'));}})()")
            for width in (1440, 1024, 768, 390, 320):
                b.call('Emulation.setDeviceMetricsOverride', width=width, height=900, deviceScaleFactor=1, mobile=False)
                b.wait(f"innerWidth === {width}")
                b.eval("(()=>{const input=document.querySelector('.xc-composer .xc-input');input.focus();input.value='/con';input.setSelectionRange(4,4);input.dispatchEvent(new Event('input'));})()")
                b.wait("!document.querySelector('.xc-ghost').hidden")
                result = b.eval(READOUT)
                assert result['page'] <= width, (theme, width, result)
                assert result['inputWidth'] >= (width * .70 if width <= 390 else 200), (theme, width, result)
                assert result['proseSize'] >= 15, (theme, width, result)
                assert 'monospace' not in result['proseFont'].lower(), result
                assert 'monospace' in result['codeFont'].lower(), result
                assert result['codeContent'] > result['codeWidth'] and result['codeOverflow'] in ('auto', 'scroll'), result
                assert result['selectedCurrent'] == 'true', result
                assert result['selectedBackground'] != result['otherBackground'], result
                assert result['proseContrast'] >= 4.5 and result['selectedContrast'] >= 4.5, (theme, width, result)
                assert result['selectedMarkerContrast'] >= 3 and result['focusContrast'] >= 3, (theme, width, result)
                assert result['ghostAligned'] and result['ghostVisible'] and result['focused'], (theme, width, result)
                assert b.eval("document.querySelector('.xc-composer .xc-input').value") == '/con'
            assert not src.sent
    finally:
        srv.close()


def test_real_expired_connection_status_wraps_without_hiding_navigation():
    src = DesignSource(); srv = Served(src)
    try:
        with Browser(width=1024, height=900) as b:
            b.goto(srv.url()); b.wait("document.querySelector('.xc-conn')?.textContent === 'Connected'")
            srv.call(srv.web.rotate)  # Real server token retirement closes the stream and refuses its retry.
            b.wait("document.querySelector('.xc-conn').textContent.includes('Page link expired: open the latest complete link printed by exo web')")
            for width in (1024, 390, 320):
                b.call('Emulation.setDeviceMetricsOverride', width=width, height=900, deviceScaleFactor=1, mobile=False)
                b.wait(f"innerWidth === {width}")
                result = b.eval("""(()=>{const c=document.querySelector('.xc-conn'),r=c.getBoundingClientRect();return {
                  page:document.documentElement.scrollWidth,width:innerWidth,left:r.left,right:r.right,
                  content:c.scrollHeight,box:c.clientHeight,overflow:getComputedStyle(c).overflow,
                  links:[...document.querySelectorAll('.xc-nav-link')].map(e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,height:r.height};})};})()""")
                assert result['page'] <= width, (width, result)
                assert result['left'] >= 0 and result['right'] <= width, (width, result)
                assert result['content'] <= result['box'] + 1, (width, result)
                assert result['overflow'] not in ('hidden', 'clip'), (width, result)
                assert len(result['links']) == 5 and all(link['left'] >= 0 and link['right'] <= width and link['height'] >= 40 for link in result['links']), (width, result)
            assert not src.sent
    finally:
        srv.close()
