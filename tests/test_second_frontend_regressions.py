"""Execute source and bundled browser code in Node; no external editor/network involved."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
const fs = require('fs'), vm = require('vm');
const nodes = new Map(), events = {}, sent = [], requests = [];
const make = () => ({value:'', textContent:'', hidden:false, children:[],
  append(...items){this.children.push(...items)}, appendChild(item){this.children.push(item)},
  prepend(...items){this.children.unshift(...items)}, replaceChildren(...items){this.children=items},
  classList:{add(){},remove(){}}, click(){}, focus(){} });
function newFrame(){const frame=make();frame.contentWindow={postMessage(text){sent.push(JSON.parse(text))}};
  frame.parentNode={replaceChild(next){nodes.set('drawio-frame',next)}};frame.cloneNode=newFrame;return frame;}
function get(id){if(!nodes.has(id))nodes.set(id,id==='drawio-frame'?newFrame():make());return nodes.get(id)}
const auth={user:{username:'alice',role:0},onChange(fn){this.listener=fn},open(){},errorText(d,s){return String(d?.detail||s)}};
let nonce=0, fetchImpl=async()=>[];
const context={document:{getElementById:get,createElement:make}, CodeMaxAuth:auth,
 window:{CodeMaxAuth:auth,addEventListener(name,fn){events[name]=fn}},
 AbortController, JSON, Blob, URL, crypto:{randomUUID(){return 'nonce-'+(++nonce)}},
 setTimeout(){return 1},clearTimeout(){},
 DOMParser:class{parseFromString(){return {documentElement:{nodeName:'mxfile'},querySelector(){return null}}}},
 fetch:async(url,options={})=>{requests.push({url,options});const data=await fetchImpl(url,options);
   return {ok:true,status:200,json:async()=>data,headers:{get(){return '"1"'}}}}};
const tick=async()=>{for(let i=0;i<4;i++)await new Promise(setImmediate)};
const event=(msg,source=get('drawio-frame').contentWindow)=>events.message({origin:'https://embed.diagrams.net',source,data:JSON.stringify(msg)});
const start=()=>vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{
const scenario=process.argv[2];
if(scenario==='support-privacy'){
  let release;fetchImpl=()=>new Promise(r=>release=r);start();await tick();
  await auth.listener(null);release([{id:1,body:'ALICE_PRIVATE',sender_role:0,create_time:'2026-09-12T00:00:00Z'}]);await tick();
  const cleared=get('support-messages').children.length===0 && get('support-workspace').hidden;
  fetchImpl=async()=>[{id:2,body:'<script>BOB_LITERAL</script>',sender_role:0,create_time:'2026-09-12T00:00:00Z'}];
  await auth.listener({username:'bob',role:0});await tick();
  console.log(JSON.stringify({cleared,text:get('support-messages').children[0].children[1].textContent}));
}else if(scenario==='support-retry'){
  let tries=0;fetchImpl=async(url,opt)=>{
    if(opt.method==='POST'){if(++tries===1)throw Error('lost response');return {id:100}}
    return tries>=2?[{id:99,body:'admin reply',sender_role:1,create_time:'2026-09-12T00:00:00Z'},
      {id:100,body:'customer text',sender_role:0,create_time:'2026-09-12T00:00:00Z'}]:[];
  };start();await tick();get('support-body').value='customer text';
  await get('support-form').onsubmit({preventDefault(){}});await tick();
  await get('support-form').onsubmit({preventDefault(){}});await tick();
  console.log(JSON.stringify({payloads:requests.filter(r=>r.options.method==='POST').map(r=>JSON.parse(r.options.body)),
    messages:get('support-messages').children.map(n=>n.children[1].textContent)}));
}else if(scenario==='drawio-export'){
  fetchImpl=async(url,opt)=>opt.method==='POST'?{id:10}:[];start();await tick();
  event({event:'init'});event({event:'load'});event({event:'autosave',xml:'<mxfile>STALE</mxfile>'});
  const saving=get('btn-save').onclick();await tick();
  const command=sent.find(s=>s.action==='export');const before=requests.filter(r=>r.options.method==='POST').length;
  event({event:'export',xml:'<mxfile>WRONG</mxfile>',message:{requestId:command.requestId+1}});
  event({event:'export',xml:'<mxfile>FRESH</mxfile>',message:command});await saving;await tick();
  const body=JSON.parse(requests.find(r=>r.options.method==='POST').options.body);
  const oldWindow=get('drawio-frame').contentWindow;
  await auth.listener(null);await auth.listener({username:'bob'});await tick();
  event({event:'autosave',xml:'<mxfile>ALICE_PRIVATE</mxfile>'},oldWindow);event({event:'init'});
  console.log(JSON.stringify({before,body,lastLoad:sent.filter(s=>s.action==='load').at(-1)}));
}
})().catch(e=>{console.error(e);process.exitCode=1});
'''


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for frontend tests')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
@pytest.mark.parametrize('scenario', ['support-privacy', 'support-retry', 'drawio-export'])
def test_browser_lifecycle(folder, scenario):
    filename = 'drawio-page.js' if scenario.startswith('drawio') else 'support-page.js'
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / folder / filename), scenario],
                            capture_output=True, text=True, check=True, timeout=20)
    output = json.loads(result.stdout)
    if scenario == 'support-privacy':
        assert output == {'cleared': True, 'text': '<script>BOB_LITERAL</script>'}
    elif scenario == 'support-retry':
        assert output['payloads'][0] == output['payloads'][1]
        assert output['messages'] == ['admin reply', 'customer text']
    else:
        assert output['before'] == 0
        assert output['body']['content'] == '<mxfile>FRESH</mxfile>'
        assert output['lastLoad']['autosave'] == 1
        assert 'ALICE_PRIVATE' not in output['lastLoad']['xml'] and 'FRESH' not in output['lastLoad']['xml']
