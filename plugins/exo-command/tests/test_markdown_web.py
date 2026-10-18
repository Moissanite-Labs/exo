"""Markdown in completed chat messages (safe pipe tables, lists, line breaks, quotes), verified in Node and Chrome."""
import json
import shutil
import subprocess
import time

import pytest

from browser import Browser, Served, find_chrome
from exo_command.server import STATIC
from test_layout_web import GEOMETRY, ThreadSrc, open_chat, parts

NODE = shutil.which("node")
TABLE = "| Name | Note | Count |\n| :--- | :---: | ---: |\n| **safe** | escaped \\| pipe | 7 |\n| <img src=x onerror=alert(1)> | [bad](javascript:alert(1)) | <script>alert(2)</script> |"
# 4.2 KB of pipes: a 600-column header and 600 one-cell rows, which GFM padding turns into 360,000 cells.
WIDE = "a|" * 600 + "\n" + "-|" * 600 + "\n" + "b|\n" * 600
STEPS = "1. Install:\n```\npip install x\n```\n2. Run:\n```\nx --go\n```\n3. Check."
SPAN = "```git status``` shows what changed.\n\nThe rest."
CELLS = r"| `C:\\Users\\me` | a\|b |" + "\n|---|---|\n" + r"| `x\\y` | \| |"
QUOTE = "> note\n>\n> | a | b |\n> |---|---|\n> | 1 | 2 |"
# 726 bytes that GFM padding turns into 9,984 cells, just under the cap: repeating it must not repeat the cells.
CAPPED = "a|" * 64 + "\n" + "-|" * 64 + "\n" + "b|\n" * 155
FULL = "a|" * 64 + "\n" + "-|" * 64 + "\n" + ("b|" * 64 + "\n") * 155   # 64 x 156 cells, every row full
PRELUDE = r'''
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
const elements=(n)=>(n.tagName?1:0)+(n.children||[]).reduce((s,x)=>s+elements(x),0);
const shape=(el)=>el.children.map(x=>x.tagName||x.textContent);
const numbers=(root)=>all(root,"OL").flatMap(ol=>ol.children.filter(x=>x.tagName==="LI").map((_,k)=>(+ol.attrs.start||1)+k));
const nesting=(n,tag)=>(n.tagName===tag?1:0)+(n.children||[]).reduce((m,x)=>Math.max(m,nesting(x,tag)),0);
const timed=(src)=>{const t0=performance.now(); const md=renderMarkdown(src); return [md,performance.now()-t0];};
'''


def node_md(tmp_path, script, **consts):
    """Run `script` against the real markdown.js and dom.js on a stub DOM; the stub throws on any innerHTML."""
    for name in ("dom", "markdown"):
        (tmp_path / f"{name}.mjs").write_text((STATIC / f"{name}.js").read_text().replace('.js"', '.mjs"'))
    head = "".join(f"const {k}={json.dumps(v)};\n" for k, v in consts.items())
    (tmp_path / "test.mjs").write_text(PRELUDE + head + script + '\nconsole.log("ok");\n')
    result = subprocess.run([NODE, str(tmp_path / "test.mjs")], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


needs_node = pytest.mark.skipif(not NODE, reason="Node missing")


@needs_node
def test_table_parser_alignment_escaped_pipes_fences_and_hostile_text(tmp_path):
    node_md(tmp_path, r'''
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
''', TABLE=TABLE)


@needs_node
def test_a_table_past_the_column_or_cell_cap_is_shown_as_source_text(tmp_path):
    node_md(tmp_path, r'''
const wide=renderMarkdown(WIDE);
assert.equal(all(wide,"TABLE").length,0);
assert.equal(all(wide,"PRE").length,1);
assert.ok(all(wide,"PRE")[0].textContent.startsWith("a|a|") && all(wide,"PRE")[0].textContent.endsWith("b|"));
assert.ok(elements(wide)<10, "elements: "+elements(wide));
const make=(c,r)=>[Array.from({length:c},(_,k)=>"c"+k).join(" | "),Array(c).fill("---").join(" | "),...Array(r).fill(Array(c).fill("x").join(" | "))].join("\n");
const fits=renderMarkdown(make(64,100));                       // 64 x 101 cells is under the cap
assert.equal(all(fits,"TABLE").length,1); assert.equal(all(fits,"TD").length,6400);
const tall=renderMarkdown(make(64,200)+"\n\nAfter.");           // 64 x 201 cells is over it
assert.equal(all(tall,"TABLE").length,0); assert.equal(all(tall,"PRE").length,1); assert.equal(all(tall,"P")[0].textContent,"After.");
assert.equal(all(renderMarkdown(make(65,1)),"TABLE").length,0); // one column too many
const cells=(md)=>all(md,"TD").length+all(md,"TH").length;
const many=renderMarkdown((FULL+"\n").repeat(3));                // the cap is a budget for the whole reply
assert.equal(all(many,"TABLE").length,1); assert.equal(all(many,"PRE").length,2); assert.ok(cells(many)<=10000, cells(many));
const lines=FULL.trimEnd().split("\n");
const quoted=renderMarkdown((lines.map(x=>"> "+x).join("\n")+"\n\n").repeat(3));
assert.equal(all(quoted,"BLOCKQUOTE").length,3); assert.ok(cells(quoted)<=10000, cells(quoted));
const items=renderMarkdown(("- x\n"+lines.map(x=>"  "+x).join("\n")+"\n").repeat(3));
assert.equal(all(items,"LI").length,3); assert.ok(cells(items)<=10000, cells(items));
// short rows padded out to the header: more padded cells than source characters is source text, so many such
// replies in one thread cost what their bytes cost; a few padded cells still render
const padded=renderMarkdown(CAPPED);
assert.equal(all(padded,"TABLE").length,0); assert.equal(all(padded,"PRE").length,1); assert.ok(elements(padded)<10);
const short=renderMarkdown("a|b|c\n-|-|-\nx|y\n1|2|3");
assert.equal(all(short,"TABLE").length,1); assert.equal(all(short,"TD").length,6);
''', WIDE=WIDE, CAPPED=CAPPED, FULL=FULL)


@needs_node
def test_ordered_lists_keep_their_numbers_across_fences_blank_lines_and_sub_items(tmp_path):
    node_md(tmp_path, r'''
const steps=renderMarkdown(STEPS);
assert.deepEqual(numbers(steps),[1,2,3]);
assert.deepEqual(all(steps,"PRE").map(x=>x.textContent),["pip install x","x --go"]);
assert.deepEqual(numbers(renderMarkdown("3. third\n4. fourth")),[3,4]);
const loose=renderMarkdown("1. a\n\n2. b\n\n3. c");
assert.equal(all(loose,"OL").length,1); assert.deepEqual(numbers(loose),[1,2,3]);
const nested=renderMarkdown("1. Step one\n   - a\n   - b\n2. Step two\n3. Step three");
assert.equal(all(nested,"OL").length,1); assert.deepEqual(numbers(nested),[1,2,3]);
assert.equal(all(nested,"UL").length,1); assert.deepEqual(all(nested,"UL")[0].children.map(x=>x.textContent),["a","b"]);
assert.ok(all(nested,"OL")[0].children[0].children.includes(all(nested,"UL")[0]));   // the bullets sit inside the first item
const fenced=renderMarkdown("1. a\n   ```\n   code\n   ```\n   more\n2. b");
assert.equal(all(fenced,"OL").length,1); assert.deepEqual(numbers(fenced),[1,2]);
assert.equal(all(fenced,"PRE")[0].textContent,"code"); assert.ok(all(fenced,"LI")[0].children.includes(all(fenced,"PRE")[0]));
assert.ok(all(fenced,"LI")[0].textContent.endsWith("more"));
const after=renderMarkdown("- a\n\nText.\n- b"); assert.equal(all(after,"UL").length,2); assert.equal(all(after,"P").length,1);
const mixed=renderMarkdown("- a\n1. b"); assert.equal(all(mixed,"UL").length,1); assert.equal(all(mixed,"OL").length,1);
''', STEPS=STEPS)


@needs_node
def test_tables_quotes_headings_and_sub_lists_indented_under_an_item_render_inside_it(tmp_path):
    node_md(tmp_path, r'''
for(const gap of ["","\n"]){
  const md=renderMarkdown("1. Compare:\n"+gap+"   | a | b |\n   |---|---|\n   | 1 | 2 |\n"+gap+"2. Next");
  assert.equal(all(md,"OL").length,1); assert.deepEqual(numbers(md),[1,2]);
  const first=all(md,"LI")[0];
  assert.equal(all(first,"TABLE").length,1); assert.deepEqual(all(first,"TD").map(x=>x.textContent),["1","2"]);
}
const bullet=renderMarkdown("- Results\n  | k | v |\n  |---|---|\n  | x | 1 |");
assert.equal(all(all(bullet,"LI")[0],"TABLE").length,1);
const quote=renderMarkdown("1. Note:\n   > careful\n2. Next");
assert.equal(all(all(quote,"LI")[0],"BLOCKQUOTE")[0].textContent,"careful"); assert.deepEqual(numbers(quote),[1,2]);
const heading=renderMarkdown("1. a\n   ### Sub\n2. b");
assert.equal(all(all(heading,"LI")[0],"H5")[0].textContent,"Sub"); assert.deepEqual(numbers(heading),[1,2]);
const deep=renderMarkdown("- a\n  - b\n    - c\n- d");
assert.equal(nesting(deep,"UL"),3); assert.deepEqual(all(deep,"LI").map(x=>x.children[0].textContent),["a","b","c","d"]);
assert.equal(all(renderMarkdown("  - a\n  - b"),"UL")[0].children.length,2);   // items indented alike are siblings
''')


@needs_node
def test_paragraph_line_breaks_hard_breaks_indentation_and_quotes_are_kept(tmp_path):
    node_md(tmp_path, r'''
const status=renderMarkdown("Status:\npassed: 12\nfailed: 0");
assert.equal(all(status,"P").length,1);
assert.deepEqual(shape(all(status,"P")[0]),["Status:","BR","passed: 12","BR","failed: 0"]);
assert.deepEqual(shape(all(renderMarkdown("a  \nb\\\nc"),"P")[0]),["a","BR","b","BR","c"]);
assert.deepEqual(shape(all(renderMarkdown("Run:\n  cd /tmp\n  make clean"),"P")[0]),["Run:","BR","  cd /tmp","BR","  make clean"]);
const quote=renderMarkdown(QUOTE+"\n\nAfter.");
assert.equal(all(quote,"BLOCKQUOTE").length,1);
const q=all(quote,"BLOCKQUOTE")[0];
assert.equal(all(q,"TABLE").length,1); assert.deepEqual(all(q,"TD").map(x=>x.textContent),["1","2"]);
assert.equal(all(q,"P")[0].textContent,"note"); assert.equal(all(quote,"P").length,2);
assert.equal(all(renderMarkdown("> a\n> b"),"BR").length,1);
assert.equal(all(renderMarkdown("Path C:\\Exo\\"),"P")[0].textContent,"Path C:\\Exo\\");   // only a break before another line
const wrapped=renderMarkdown("**bold\ntext**, [link\ntext](https://e.com) and `code\nspan`");   // inline markup may wrap
assert.equal(all(wrapped,"STRONG").length,1); assert.equal(all(wrapped,"A").length,1); assert.equal(all(wrapped,"CODE").length,1);
const dock=renderMarkdown("Run `docker run \\\n  -p 80:80 img` to start");   // a code span keeps its line-end backslash
assert.equal(all(dock,"CODE")[0].textContent,"docker run \\\n  -p 80:80 img"); assert.equal(all(dock,"BR").length,0);
''', QUOTE=QUOTE)


@needs_node
def test_quotes_and_list_items_nest_at_most_eight_deep_in_linear_time(tmp_path):
    node_md(tmp_path, r'''
const ladder=Array.from({length:400},(_,k)=>" ".repeat(2*k)+"- x").join("\n");   // 157 KB of ever deeper items
for(const src of [">".repeat(100000)+" x","> ".repeat(50000)+"x",(">".repeat(200)+" x\n").repeat(500),ladder]){
  const [md,ms]=timed(src);
  assert.ok(ms<1500, src.slice(0,8)+" took "+Math.round(ms)+" ms");
  assert.ok(nesting(md,"BLOCKQUOTE")<=8 && nesting(md,"LI")<=9, nesting(md,"BLOCKQUOTE")+" "+nesting(md,"LI"));
}
assert.ok(all(timed(">".repeat(5000)+" x")[0],"P")[0].textContent.endsWith("> x"));   // the deeper markers stay text
assert.equal(nesting(renderMarkdown("> > > a"),"BLOCKQUOTE"),3);
''')


@needs_node
def test_a_backtick_fence_opener_may_not_contain_a_backtick(tmp_path):
    node_md(tmp_path, r'''
const span=renderMarkdown(SPAN);
assert.equal(all(span,"PRE").length,0); assert.equal(all(span,"P").length,2);
assert.equal(all(span,"CODE")[0].textContent,"git status");
assert.equal(all(span,"P")[0].textContent,"``git status`` shows what changed.");   // code spans use single backticks
assert.equal(all(span,"P")[1].textContent,"The rest.");
const tilde=renderMarkdown("~~~ `x`\ncode\n~~~");                 // a tilde fence may keep backticks in its info string
assert.equal(all(tilde,"PRE").length,1); assert.equal(all(tilde,"PRE")[0].textContent,"code");
assert.equal(all(renderMarkdown("```python\nx = 1\n```"),"PRE")[0].textContent,"x = 1");
''', SPAN=SPAN)


@needs_node
def test_unclosed_brackets_render_in_linear_time(tmp_path):
    node_md(tmp_path, r'''
const t0=performance.now(); const brackets=renderMarkdown("[".repeat(80000)); const ms=performance.now()-t0;
assert.equal(all(brackets,"P")[0].textContent.length,80000);
assert.ok(ms<1500, "80k brackets took "+Math.round(ms)+" ms");
const [, url]=timed("[a](http://x".repeat(12000));                 // an unclosed URL stops at the next link start
assert.ok(url<1500, "144 KB of unclosed links took "+Math.round(url)+" ms");
const link=renderMarkdown("see [the docs](https://example.com/x) and [bad](javascript:alert(1)) and [a [b]](https://e.com)");
assert.equal(all(link,"A").length,1); assert.equal(all(link,"A")[0].attrs.href,"https://example.com/x"); assert.equal(all(link,"A")[0].textContent,"the docs");
''')


@needs_node
def test_table_cells_unescape_only_pipes(tmp_path):
    node_md(tmp_path, r'''
const md=renderMarkdown(CELLS);
assert.deepEqual(all(md,"CODE").map(x=>x.textContent),[WINDOWS,"x\\\\y"]);
assert.equal(all(md,"TD")[1].textContent,"|"); assert.equal(all(md,"TH")[1].textContent,"a|b");
''', CELLS=CELLS, WINDOWS=r"C:\\Users\\me")


@needs_node
def test_table_body_stops_at_a_heading_list_or_quote_line(tmp_path):
    node_md(tmp_path, r'''
const md=renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |\n- not a row | x\n# Head | er\n> q | r");
assert.deepEqual(all(md,"TD").map(x=>x.textContent),["1","2"]);
assert.equal(all(md,"LI")[0].textContent,"not a row | x");
assert.equal(all(md,"H3")[0].textContent,"Head | er"); assert.equal(all(md,"BLOCKQUOTE")[0].textContent,"q | r");
''')


class TableSrc(ThreadSrc):
    def __init__(self, answer=None):
        super().__init__(pairs=0)
        self.answer = answer if answer is not None else TABLE + "\n\n```\n" + TABLE + "\n```\n\n| Wide | Value |\n| --- | --- |\n| " + "longcolumn" * 70 + " | end |"

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


@needs_chrome
def test_a_wide_table_reply_renders_as_source_text_with_few_nodes():
    srv = Served(TableSrc(WIDE))
    try:
        with Browser(width=1440) as browser:
            t0 = time.monotonic()
            browser.goto(srv.url())
            browser.wait("document.querySelectorAll('.xc-asst .xc-md').length === 1")
            elapsed = time.monotonic() - t0
            assert browser.eval("document.querySelectorAll('.xc-asst *').length") < 50
            assert browser.eval("document.querySelectorAll('.xc-asst table').length") == 0
            assert browser.eval("document.querySelector('.xc-asst pre').textContent").startswith("a|a|")
            assert elapsed < 8, elapsed   # main renders this text in well under a second; padded cells took 8 s and more
    finally:
        srv.close()


@needs_chrome
def test_deep_quotes_and_many_capped_tables_in_a_reply_still_show_the_thread():
    srv = Served(TableSrc(">" * 5000 + " deep\n\n" + (CAPPED + "\n") * 40))   # 34 KB
    try:
        with Browser(width=1440) as browser:
            t0 = time.monotonic()
            browser.goto(srv.url())
            browser.wait("document.querySelectorAll('.xc-asst .xc-md').length === 1")
            elapsed = time.monotonic() - t0
            assert browser.eval("[document.querySelectorAll('.xc-user').length, document.querySelectorAll('.xc-asst blockquote').length]") == [1, 8]
            assert browser.eval("document.querySelectorAll('.xc-asst td, .xc-asst th').length") <= 10000
            assert elapsed < 8, elapsed
    finally:
        srv.close()


@needs_chrome
def test_completed_replies_keep_list_numbers_line_breaks_and_quotes():
    answer = STEPS + "\n\nAlso:\n\n3. third\n4. fourth\n\nLoose:\n\n1. a\n\n2. b\n\n3. c\n\nStatus:\npassed: 12\nfailed: 0\n\n" + QUOTE
    srv = Served(TableSrc(answer))
    try:
        with Browser(width=1440) as browser:
            browser.goto(srv.url())
            browser.wait("document.querySelectorAll('.xc-asst ol').length === 5")
            markers = browser.eval("[...document.querySelectorAll('.xc-asst ol > li')].map(li=>li.value||li.parentElement.start+[...li.parentElement.children].indexOf(li))")
            assert markers == [1, 2, 3, 3, 4, 1, 2, 3], markers
            assert browser.eval("[...document.querySelectorAll('.xc-asst pre')].map(x=>x.textContent)") == ["pip install x", "x --go"]
            lines = browser.eval("[...document.querySelectorAll('.xc-asst p')].map(p=>p.innerText)")
            assert "Status:\npassed: 12\nfailed: 0" in lines, lines
            assert browser.eval("document.querySelectorAll('.xc-asst blockquote table td').length") == 2
    finally:
        srv.close()


@needs_chrome
def test_classic_layout_keeps_following_when_a_table_reply_completes():
    table = "| Step | Result |\n| --- | --- |\n" + "".join(f"| step {i} | passed |\n" for i in range(20))
    srv = Served(ThreadSrc())
    try:
        with Browser() as b:
            open_chat(srv, b, "classic")
            for n, line in enumerate(table.splitlines(keepends=True)):   # streamed a line at a time, as a model writes it
                srv.call(srv.centre.record, {"seq": n + 1, "session": "s1", "type": "message.delta", "body": {"message_id": "live", "text": line}})
            b.wait("(document.querySelector('.xc-streaming') || {textContent: ''}).textContent.includes('step 19')")
            g = b.eval(GEOMETRY)
            assert abs(g["top"] - (g["h"] - g["c"])) < 2, g       # the streamed pipe text keeps the view at the bottom
            srv.call(srv.centre.record, {"seq": 99, "session": "s1", "type": "message.completed", "body": {"message": {"id": "live", "role": "assistant", "content": parts(table)}}})
            b.wait("document.querySelectorAll('.xc-asst table').length === 1 && !document.querySelector('.xc-streaming')")
            b.eval("new Promise((r) => setTimeout(r, 700))")
            g = b.eval(GEOMETRY)
            assert abs(g["top"] - (g["h"] - g["c"])) < 2, g       # the rendered table is taller, and the view followed it
    finally:
        srv.close()
