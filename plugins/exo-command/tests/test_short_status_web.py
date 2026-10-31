"""Short-window status, transcript and composer layout regression."""
import json

import pytest

import test_layout_web as layout_web

needs_chrome = layout_web.needs_chrome
open_chat = layout_web.open_chat
page = layout_web.page


@needs_chrome
@pytest.mark.parametrize("layout", ["classic", "inverse"])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_expanded_status_keeps_composer_and_transcript_reachable(page, layout, theme):
    srv, b = page
    open_chat(srv, b, layout)
    b.eval(f'document.documentElement.setAttribute("data-theme", "{theme}")')
    for seq, kind, body in [
        (1, "turn.started", {"turn_id": "synthetic", "model": "requested"}),
        (2, "model.call", {"requested": "requested", "route": "provider/" + "long-model-name-" * 8,
                           "usage": {"input_tokens": 15000, "output_tokens": 1200, "cache_read_tokens": 10000}}),
        (3, "notice", {"text": "Warning: " + "unbroken" * 35, "level": "warn"}),
    ]:
        srv.call(srv.centre.record, {"seq": seq, "type": kind, "body": body, "session": "s1",
                                     "ts": "2026-10-28T06:00:00Z"})
    b.wait("document.querySelector('.xc-flash')?.textContent.includes('unbroken')")
    for width, height, pane in [(1460, 500, True), (360, 740, False), (1460, 900, True)]:
        # Wait for the page to adapt: below 760 px it closes the conversation list on the media query's change
        # event, one frame later, and that moves the transcript up by the list's height.
        b.resize(width, height)
        b.eval("document.querySelector('.xc-chat').style.gridTemplateColumns = " +
               json.dumps("224px 320px 340px" if pane else ""))
        got = b.eval('''(() => {
          const strip = document.querySelector('.xc-strip'), notice = strip.querySelector('.xc-flash');
          const stage = document.querySelector('.xc-stage'), composer = document.querySelector('.xc-composer');
          const box = el => { const r = el.getBoundingClientRect(); return {top:r.top, bottom:r.bottom, height:r.height}; };
          const s = box(strip), n = box(notice), t = box(stage), c = box(composer);
          const disjoint = (a,b) => a.bottom <= b.top + 1 || b.bottom <= a.top + 1;
          return {s,n,t,c, disjoint:disjoint(n,t) && disjoint(n,c),
            scroll:strip.scrollWidth, width:strip.clientWidth, page:document.documentElement.scrollWidth,
            logHeight:document.querySelector('.xc-log').clientHeight, mainScroll:document.querySelector('.xc-chat-main').scrollTop,
            notice:notice.textContent};
        })()''')
        assert got['n']['bottom'] <= got['s']['bottom'] + 1 and got['n']['top'] >= got['s']['top'] - 1, got
        assert got['disjoint'] and got['logHeight'] > 40, got
        assert got['notice'] == 'Warning: ' + 'unbroken' * 35, got
        if height == 900:
            assert got['mainScroll'] == 0, got
        if height == 500:
            assert got['t']['height'] >= 120, got
            main = b.eval('''(() => { const m=document.querySelector('.xc-chat-main'); m.scrollTop=0;
              const start=m.scrollTop, max=m.scrollHeight-m.clientHeight; m.scrollTop=m.scrollHeight;
              const end=m.scrollTop; m.scrollTop=0; return {start, end, max}; })()''')
            assert main['start'] == 0 and main['max'] > 0 and abs(main['end'] - main['max']) < 2, main
        if width == 360:
            assert got['logHeight'] >= height * 0.6, got
        assert got['scroll'] <= got['width'] + 1 and got['page'] <= width + 1, got
        for selector in ['.xc-flash', '.xc-input', '.xc-log']:
            b.eval(f"document.querySelector('{selector}').scrollIntoView({{block:'center'}})")
            visible = b.eval(f'''(() => {{ const r=document.querySelector('{selector}').getBoundingClientRect();
              return r.top >= -1 && r.bottom <= innerHeight + 1; }})()''')
            assert visible, (selector, got)
        log = b.eval('''(() => { const l=document.querySelector('.xc-log'); l.scrollTop=0; const start=l.scrollTop;
          l.scrollTop=l.scrollHeight; return {start, end:l.scrollTop, max:l.scrollHeight-l.clientHeight}; })()''')
        assert log['start'] == 0 and log['max'] > 0 and abs(log['end'] - log['max']) < 2, log


@needs_chrome
@pytest.mark.parametrize("layout", ["classic", "inverse"])
def test_mobile_transcript_keeps_height_with_normal_status(page, layout):
    srv, b = page
    open_chat(srv, b, layout)
    for width, height in [(360, 740), (390, 844), (700, 900)]:
        b.resize(width, height)
        log_height = b.eval("document.querySelector('.xc-log').clientHeight")
        assert log_height >= height * 0.6, (width, height, log_height)


@needs_chrome
@pytest.mark.parametrize("layout", ["classic", "inverse"])
def test_short_window_keeps_slash_menu_visible(page, layout):
    srv, b = page
    open_chat(srv, b, layout)
    b.resize(1460, 500)
    b.eval("document.querySelector('.xc-chat').style.gridTemplateColumns = '224px 320px 340px'")
    for seq, kind, body in [
        (1, "turn.started", {"turn_id": "synthetic", "model": "requested"}),
        (2, "model.call", {"requested": "requested", "route": "provider/" + "long-model-name-" * 8}),
        (3, "notice", {"text": "Warning: " + "unbroken" * 35, "level": "warn"}),
    ]:
        srv.call(srv.centre.record, {"seq": seq, "type": kind, "body": body, "session": "s1",
                                     "ts": "2026-10-28T06:00:00Z"})
    b.wait("document.querySelector('.xc-flash')?.textContent.includes('unbroken')")
    b.eval("(()=>{const i=document.querySelector('.xc-input');i.focus();i.value='/';"
           "i.dispatchEvent(new Event('input',{bubbles:true}));})()")
    b.wait("document.querySelectorAll('.xc-menu-row').length > 2")
    got = b.eval('''(() => { const m=document.querySelector('.xc-menu'), row=m.querySelector('.xc-menu-row');
      return {height:m.clientHeight, row:row.getBoundingClientRect().height,
              stage:document.querySelector('.xc-stage').getBoundingClientRect().height}; })()''')
    assert got['stage'] >= 120 and got['height'] >= got['row'] * 3, got
