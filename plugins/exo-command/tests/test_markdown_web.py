"""Safe pipe tables in completed chat messages, verified in Node and Chrome."""
import json
import shutil
import subprocess

import pytest

from browser import Browser, Served, find_chrome
from exo_command.server import STATIC
from test_layout_web import ThreadSrc, parts

NODE = shutil.which("node")
TABLE = "| Name | Note | Count |\n| :--- | :---: | ---: |\n| **safe** | escaped \\| pipe | 7 |\n| <img src=x onerror=alert(1)> | [bad](javascript:alert(1)) | <script>alert(2)</script> |"


@pytest.mark.skipif(not NODE, reason="Node missing")
def test_table_parser_alignment_escaped_pipes_fences_and_hostile_text(tmp_path):
    for name in ("dom", "markdown"):
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    script = r'''
import assert from "node:assert/strict";
class El {
  constructor(tag) { this.tagName=tag.toUpperCase(); this.children=[]; this.attrs={}; }
  setAttribute(k,v) { this.attrs[k]=v; }
  addEventListener() {}
  appendChild(x) { this.children.push(x); }
  get textContent() { return this.children.map(x=>x.textContent).join(""); }
  set innerHTML(_) { throw Error("unsafe HTML insertion"); }
}
globalThis.document={createElement:t=>new El(t),createTextNode:text=>({textContent:text})};
const {renderMarkdown}=await import("./markdown.mjs");
const all=(root,tag)=>[...(root.tagName===tag?[root]:[]),...(root.children||[]).flatMap(x=>all(x,tag))];
const source=TABLE;
const md=renderMarkdown(source);
assert.equal(all(md,"TABLE").length,1);
assert.deepEqual(all(md,"TH").map(x=>x.attrs["data-align"]),["left","center","right"]);
assert.equal(all(md,"TH")[0].attrs.scope,"col");
assert.equal(all(md,"TD")[1].textContent,"escaped | pipe");
assert.equal(all(md,"STRONG").length,1);
assert.equal(all(md,"IMG").length+all(md,"SCRIPT").length+all(md,"A").length,0);
assert.ok(md.textContent.includes("<script>alert(2)</script>"));
for(const fence of ["```", "~~~~", "  ````"]){
  const fenced=renderMarkdown(fence+"\n"+source+"\n"+fence);
  assert.equal(all(fenced,"TABLE").length,0);
  assert.equal(all(fenced,"PRE").length,1);
  assert.equal(all(fenced,"PRE")[0].textContent,source);
}
assert.equal(all(renderMarkdown("a | b\nnot | a rule\nx | y"),"TABLE").length,0);
assert.equal(all(renderMarkdown("a | b\n---\nx | y"),"TABLE").length,0);
const optional=renderMarkdown("a | b\n--- | ---\nx | y | ignored\nz |");
assert.equal(all(optional,"TABLE").length,1);
assert.deepEqual(all(optional,"TD").map(x=>x.textContent),["x","y","z",""]);
const one=renderMarkdown("| One |\n| --- |\n| a\\|b |\n\nAfter.");
assert.equal(all(one,"TABLE").length,1);assert.equal(all(one,"TD")[0].textContent,"a|b");
console.log("ok");
'''.replace("TABLE;", json.dumps(TABLE) + ";")
    (tmp_path / "test.mjs").write_text(script)
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


class TableSrc(ThreadSrc):
    def __init__(self):
        super().__init__(pairs=0)
        self.answer = TABLE + "\n\n```\n" + TABLE + "\n```\n\n| Wide | Value |\n| --- | --- |\n| " + "longcolumn" * 70 + " | end |"

    async def call(self, body):
        if type(body).TYPE == "session.get":
            return {"session": {"id": "s1", "model": "m"}, "messages": [
                {"id": "user", "role": "user", "content": parts(TABLE)},
                {"id": "answer", "role": "assistant", "content": parts(self.answer)}], "seq": 0}
        return await super().call(body)


needs_chrome = pytest.mark.skipif(not find_chrome() or not __import__("importlib").util.find_spec("websockets"), reason="Chrome or websockets missing")


@needs_chrome
def test_completed_chat_tables_stream_plain_and_scroll_inside_message():
    srv = Served(TableSrc())
    try:
        with Browser(width=1440) as browser:
            browser.goto(srv.url())
            browser.wait("document.querySelectorAll('.xc-asst table').length === 2")
            assert browser.eval("document.querySelectorAll('.xc-user table').length") == 0
            assert browser.eval("document.querySelectorAll('.xc-asst img,.xc-asst script').length") == 0
            assert browser.eval("document.querySelector('.xc-asst pre').textContent") == TABLE
            for width in (1440, 390, 320):
                browser.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1, mobile=False)
                browser.wait("innerWidth === " + str(width))
                geometry = browser.eval("""(()=>{const boxes=[...document.querySelectorAll('.xc-table-scroll')];
                  return {page:document.documentElement.scrollWidth,viewport:innerWidth,
                    wide:boxes.some(x=>x.scrollWidth>x.clientWidth),
                    overflow:boxes.every(x=>getComputedStyle(x).overflowX==='auto'),
                    focus:boxes.every(x=>x.tabIndex===0),
                    cellWidth:boxes[1].querySelector("tbody .xc-table-cell").getBoundingClientRect().width,
                    cellHeight:boxes[1].querySelector("tbody .xc-table-cell").getBoundingClientRect().height,
                    alignment:[...document.querySelectorAll('.xc-asst th')].slice(0,3).map(x=>getComputedStyle(x).textAlign)};})()""")
                assert geometry["page"] <= geometry["viewport"], geometry
                assert geometry["overflow"] and geometry["focus"], geometry
                if width <= 390:
                    assert geometry["wide"], geometry
                assert geometry["alignment"] == ["left", "center", "right"]
                assert geometry["cellWidth"] <= 448 and geometry["cellHeight"] > 30, geometry
            srv.call(srv.centre.record, {"seq": 1, "session": "s1", "type": "message.delta", "body": {"message_id": "live", "text": TABLE}})
            browser.wait("!!document.querySelector('.xc-streaming')")
            assert browser.eval("document.querySelector('.xc-streaming').querySelectorAll('table').length") == 0
            assert TABLE in browser.eval("document.querySelector('.xc-streaming').textContent")
            srv.call(srv.centre.record, {"seq": 2, "session": "s1", "type": "message.completed", "body": {"message": {"id": "live", "role": "assistant", "content": parts(TABLE)}}})
            browser.wait("document.querySelectorAll('.xc-asst table').length === 3 && !document.querySelector('.xc-streaming')")
    finally:
        srv.close()
