"""The persisted layout contains only bounded layout metadata."""
import pathlib
import subprocess


def test_layout_schema(tmp_path):
    source = pathlib.Path(__file__).resolve().parents[1] / 'exo_command/static/pane_layout.js'
    module = tmp_path / 'pane_layout.mjs'
    module.write_text(source.read_text())
    script = tmp_path / 'test.mjs'
    script.write_text('''import assert from 'node:assert/strict';
import {validateLayout,defaultLayout,loadPaneLayout,savePaneLayout} from './pane_layout.mjs';
const good=defaultLayout(); assert.ok(good);
for(const bad of [{...good,token:'private'}, {...good,threads:159}, {...good,details:521}, {...good,threads:NaN}, {...good,collapsed:['conversation']}, {...good,order:['threads','threads','details']}, {...good,version:true}]) assert.equal(validateLayout(bad),null);
let stored;globalThis.localStorage={getItem:()=>stored,setItem:(k,v)=>stored=v};
await savePaneLayout({...good,threads:300,collapsed:['details']});assert.equal((await loadPaneLayout()).threads,300);
let request;globalThis.webkit={messageHandlers:{exoPaneLayout:{postMessage:async r=>{request=r;return good}}}};
await savePaneLayout(good);assert.deepEqual(Object.keys(request).sort(),['action','layout']);assert.deepEqual(await loadPaneLayout(),good);
console.log('layout schema and storage pass');''')
    subprocess.run(['node', str(script)], check=True, capture_output=True, text=True)


def test_controls_move_existing_nodes_and_keep_draft(tmp_path):
    source = pathlib.Path(__file__).resolve().parents[1] / 'exo_command/static/pane_layout.js'
    (tmp_path / 'pane_layout.mjs').write_text(source.read_text())
    script = tmp_path / 'controls.mjs'
    script.write_text('''import assert from 'node:assert/strict';
import {attachPaneLayout} from './pane_layout.mjs';
class El {
 constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.events={};this.classList={add(){},remove(){}};this.hidden=false;}
 appendChild(x){if(x.parent)x.parent.children=x.parent.children.filter(y=>y!==x);this.children.push(x);x.parent=this;return x;}
 addEventListener(k,f){this.events[k]=f;}setAttribute(k,v){this[k]=v;}
 querySelectorAll(q){const key=q.includes('toggle')?'paneToggle':'paneWidth';return this.children.flatMap(x=>[...(x.dataset[key]?[x]:[]),...x.querySelectorAll(q)]);}
 replaceChildren(){this.children=[];}remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);}
}
globalThis.document={createElement:t=>new El(t)};let saved;
globalThis.localStorage={getItem:()=>null,setItem:(k,v)=>saved=JSON.parse(v)};
const container=new El('div'),toolbar=new El('nav'),panes={threads:new El('nav'),conversation:new El('main'),details:new El('aside')};
const draft=new El('textarea');draft.value='preserved';panes.conversation.appendChild(draft);
const stop=attachPaneLayout(container,panes,toolbar);const all=()=>toolbar.children.flatMap(x=>[x,...x.children]);
all().find(x=>x.dataset.paneToggle==='details').events.click();assert.equal(panes.details.hidden,true);
all().find(x=>x['aria-label']==='Move conversation left').events.click();assert.equal(container.children[0],panes.conversation);
const range=all().find(x=>x.dataset.paneWidth==='threads');range.value='400';range.events.input();assert.equal(panes.threads.style.width,'400px');
assert.equal(draft.value,'preserved');assert.equal(panes.conversation.children[0],draft);assert.equal(saved.threads,400);
all().find(x=>x.textContent==='Reset panes').events.click();assert.equal(panes.details.hidden,false);assert.equal(range.value,'224');
await Promise.resolve();assert.equal(draft.value,'preserved');stop();assert.equal(container.children[1],panes.conversation);assert.equal(toolbar.children.length,0);
console.log('controls preserve nodes and draft');''')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
