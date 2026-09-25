"""Execute admin source and committed bundle in Node VM; no browser/merchant certification."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const fs=require('fs'),vm=require('vm');
const nodes=new Map(),requests=[],confirmations=[];
const make=()=>({value:'',textContent:'',hidden:false,disabled:false,children:[],
  append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x;this.textContent=''}});
const get=(id)=>{if(!nodes.has(id))nodes.set(id,make());return nodes.get(id)};
const el=(id)=>get('finance-'+id);
let paid=false, accept=true;
const auth={user:{username:'adminA',role:1},onChange(fn){this.listener=fn},open(){},errorText(d,s){return String(d?.detail||s)}};
const rows=(prefix='ORDER')=>({orders:[1,2].map(n=>({order_no:prefix+n,username:'<img src=x onerror=BAD>',user_id:n,
  product_name:'<script>PRODUCT</script>',amount:19900,status:'pending',payment_mode:'manual'})),next_cursor:null});
const ledger=(no)=>({order_no:no,order:{user_id:1,product_name:'<img src=x onerror=BAD>',amount:19900,currency:'CNY',
  status:paid?'paid':'pending',payment_mode:'manual',delivery_key:'ORIGINAL',delivery_digest:'HASH',delivery_size:3},
  actions:{manual:true},receipt:paid?{source:'manual',reference:'BANK-1',amount:19900,currency:'CNY',actor:'adminA',evidence:'<script>PROOF</script>'}:null,
  review:{state:'open',version:7,snapshot:'a'.repeat(64),issues:1,orphans:0,note:'<script>NOTE</script>',actor:'A'},
  events:[{id:1,kind:'PRIVATE_'+no,actor:'A',evidence:'<script>EVENT</script>'}],next_cursor:null});
let impl=async(url,opt)=>url.includes('/ledger')?ledger(url.split('/')[4]):rows();
const context={document:{getElementById:get,createElement:make},AbortController,URLSearchParams,Number,TextEncoder,
  window:{CodeMaxAuth:auth,confirm(text){confirmations.push(text);return accept}},
  fetch:async(url,options={})=>{requests.push({url,options});const data=await impl(url,options);
    if(data?.httpStatus)return {ok:false,status:data.httpStatus,json:async()=>({detail:'rejected'})};
    return {ok:true,status:200,json:async()=>data}}};
const tick=async()=>{for(let i=0;i<5;i++)await new Promise(setImmediate)};
const start=()=>vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),context);
const clickOrder=async(n=0)=>{el('list').children[n].children[0].onclick();await tick()};
const input=(no='ORDER1')=>{el('confirm-no').value=no;el('evidence').value='已在银行核对';el('reference').value='BANK-1';el('amount').value='19900'};
const submit=()=>el('manual').onsubmit({preventDefault(){}});
const posts=()=>requests.filter(r=>r.options.method==='POST');
(async()=>{
const scenario=process.argv[2];
if(scenario.startsWith('close-')){
 let release;
 const d=ledger('ORDER1');d.order.status='closed';d.order.payment_mode='wechat';
 d.channel_close={enabled:true,allowed:scenario!=='close-denied',query_attempt_id:'a'.repeat(32),latest:{actor:'FIRST<script>',state:'unknown'}};
 impl=async(url,opt)=>{
  if(opt.method==='POST'){
   d.channel_close.query_attempt_id='b'.repeat(32);d.channel_close.allowed=false;
   if(['close-unknown','close-change'].includes(scenario))throw Error('unknown');
   return {attempt:{state:'acknowledged'}};
  }
  if(url.includes('/ledger')){if(scenario==='close-race')return new Promise(r=>release=()=>r(d));return d;}return rows();
 };
 start();await tick();await clickOrder();
 if(scenario==='close-race'){auth.listener(null);release();await tick()}
 input();el('close-amount').value='19900';if(scenario==='close-cancel')accept=false;
 const text=el('channel-close-view').textContent;
 await el('channel-close').onsubmit({preventDefault(){}});
 if(scenario==='close-change')el('evidence').value='changed after unknown';
 if(['close-unknown','close-change'].includes(scenario))await el('channel-close').onsubmit({preventDefault(){}});
 const ps=posts().map(x=>({url:x.url,body:JSON.parse(x.options.body)}));auth.listener(null);
 console.log(JSON.stringify({text,posts:ps,confirmations,cleared:el('channel-close-view').textContent===''&&el('close-amount').value===''}));
}else if(scenario.startsWith('reapprove-')){
 let release;
 const d=ledger('ORDER1');d.refund_request={request_id:'c'.repeat(32),state:'prepared',out_refund_no:'CMR'+'d'.repeat(32),amount:19900};
 d.refund_submission={authorization_id:'a'.repeat(32),digest:'b'.repeat(64),body:{reason:'old'},stop:{request_id:'e'.repeat(32),actor:'old-admin',evidence:'old-stop'},reauthorize_allowed:scenario!=='reapprove-denied',history:[{authorization_id:'a'.repeat(32),body:{reason:'old'},actor:'first<script>',stop:{actor:'old-admin',evidence:'old-stop'}}]};
 impl=async(url,opt)=>{
  if(opt.method==='POST'){
   if(['reapprove-unknown','reapprove-changed'].includes(scenario))throw Error('unknown');
   return {changed:true};
  }
  if(url.includes('/ledger')){
   if(scenario==='reapprove-race')return new Promise(r=>release=()=>r(d));return d;
  }return rows();
 };
 start();await tick();await clickOrder();
 if(scenario==='reapprove-race'){auth.listener(null);release();await tick()}
 const hidden=el('refund-reauthorize').hidden,history=el('authorization-history').textContent;
 input();el('send-number').value=d.refund_request.out_refund_no;el('send-amount').value='19900';el('customer-reason').value='corrected customer reason';
 await el('refund-reauthorize').onsubmit({preventDefault(){}});
 if(scenario==='reapprove-changed')el('customer-reason').value='changed after unknown';
 if(['reapprove-unknown','reapprove-changed'].includes(scenario))await el('refund-reauthorize').onsubmit({preventDefault(){}});
 const ps=posts().map(x=>({url:x.url,body:JSON.parse(x.options.body)}));auth.listener(null);
 console.log(JSON.stringify({hidden,history,posts:ps,confirmations,cleared:el('authorization-history').textContent===''&&el('customer-reason').value===''}));
}else if(scenario.startsWith('system-')){
 let release;
 impl=async(url,opt)=>{
  if(!url.includes('/ledger'))return rows();
  const d=ledger('ORDER1');d.refund_verify_enabled=true;d.refund_auto_record_enabled=scenario!=='system-off';
  d.refund_verification={jobs:[{id:9,state:'verified',attempts:1,refund_no:'ORIGINAL',outcome:'success_recorded'}]};
  d.refund={source:'wechat',recorded_by:'system',actor:'system<script>',verification_event_id:123,out_refund_no:'ORIGINAL',amount:19900};
  if(scenario==='system-race')return new Promise(r=>release=()=>r(d));return d;
 };
 start();await tick();await clickOrder();
 if(scenario==='system-race'){auth.listener(null);release();await tick()}
 const text=el('verification-view').textContent,receipt=el('refund-receipt').textContent;
 auth.listener(null);console.log(JSON.stringify({text,receipt,posts:posts().length,cleared:el('verification-view').textContent===''&&el('refund-receipt').textContent===''}));
}else if(scenario.startsWith('control-')){
 let tries=0,latest=null,release,snapshot='a'.repeat(64);
 impl=async(url,opt)=>{
  if(opt.method==='POST'){
   const b=JSON.parse(opt.body);tries++;
   if(scenario==='control-race')return new Promise(r=>release=r);
   if(tries===1 && ['control-retry','control-change','control-stale'].includes(scenario)){
    snapshot='b'.repeat(64);
    if(scenario==='control-stale')return {httpStatus:409};
    throw Error('unknown');
   }
   latest={...b,actor:'FIRST<script>',created_at:'TIME'};
   if(scenario==='control-lost-ack')throw Error('lost ACK');
   return {control:latest};
  }
  if(!url.includes('/ledger'))return rows();
  const d=ledger('ORDER1');d.refund_verification={jobs:[{id:9,state:'attention',attempts:2,refund_no:'ORIGINAL',snapshot}],latest_control:latest};return d;
 };
 start();await tick();await clickOrder();input();el('verify-job').value='9';
 const act=()=>el('verification-control').onsubmit({preventDefault(){}});
 if(scenario==='control-cancel')accept=false;
 const first=act();await tick();
 if(scenario==='control-race'){auth.listener(null);release({control:{actor:'OLD'}})}
 await first;await tick();
 if(scenario==='control-change')el('evidence').value='更改未知依据';
 if(['control-retry','control-change','control-stale'].includes(scenario))await act();
 const sent=posts().map(x=>({url:x.url,body:JSON.parse(x.options.body)}));
 const text=el('verification-control-view').textContent;
 auth.listener(null);
 console.log(JSON.stringify({sent,text,confirmations,cleared:el('verification-control-view').textContent===''&&el('verify-job').value===''&&el('message').textContent===''}));
}else if(scenario.startsWith('verify-')){
 let release;
 impl=async(url,opt)=>{
  if(!url.includes('/ledger'))return rows();
  const d=ledger('ORDER1');d.refund_verification={jobs:[{id:9,notice_event_id:7,state:'verified',attempts:1,refund_no:'ORIGINAL',outcome:'<script>SUCCESS</script>',updated_at:'NOW'}],has_more:true};
  if(scenario==='verify-completed')d.refund={out_refund_no:'ORIGINAL'};
  if(scenario==='verify-race')return new Promise(r=>release=()=>r(d));
  return d;
 };
 start();await tick();await clickOrder();
 if(scenario==='verify-race'){auth.listener(null);release();await tick()}
 const text=el('verification-view').textContent;
 auth.listener(null);
 console.log(JSON.stringify({text,cleared:el('verification-view').textContent==='',posts:posts().length}));
}else if(scenario.startsWith('stop-')){
 paid=true;let stopped=null,tries=0,release;
 const ready={authorization_id:'a'.repeat(32),digest:'c'.repeat(64),body:{reason:'client reason'},actor:'A'};
 impl=async(url,opt)=>{
  if(opt.method==='POST'){
   ++tries;const body=JSON.parse(opt.body);
   if(scenario==='stop-race')return new Promise(r=>release=r);
   if(tries===1 && ['stop-retry','stop-change'].includes(scenario))throw Error('unknown');
   stopped={request_id:body.request_id,actor:'FIRST',evidence:body.evidence};
   if(scenario==='stop-lost-ack')throw Error('lost ACK');
   return {stop:stopped};
  }
  if(!url.includes('/ledger'))return rows();
  const d=ledger('ORDER1');d.refund_request={request_id:'d'.repeat(32),out_refund_no:'CMR'+'b'.repeat(32),amount:19900,state:'prepared'};
  d.refund_submission={...ready,stop:stopped};d.refund_send_enabled=true;return d;
 };
 start();await tick();await clickOrder();input();el('send-number').value='CMR'+'b'.repeat(32);el('send-amount').value='19900';
 const submitStop=()=>el('refund-stop').onsubmit({preventDefault(){}});
 if(scenario==='stop-cancel')accept=false;
 const pending=submitStop();await tick();
 if(scenario==='stop-race'){auth.listener(null);release({stop:{actor:'OLD'}})}
 await pending;
 if(scenario==='stop-change')el('evidence').value='不能改变未知请求';
 if(['stop-retry','stop-change','stop-lost-ack'].includes(scenario))await submitStop();
 const sent=posts().map(r=>({url:r.url,body:JSON.parse(r.options.body)}));
 const hidden=el('refund-send').hidden,stopHidden=el('refund-stop').hidden,text=el('stop-view').textContent;
 auth.listener(null);
 console.log(JSON.stringify({sent,hidden,stopHidden,text,cleared:el('stop-view').textContent===''&&el('send-number').value===''}));
}else if(scenario.startsWith('submit-')){
 paid=true;let saved=null,tries=0,release;
 const ready={authorization_id:'a'.repeat(32),digest:'c'.repeat(64),body:{reason:'client reason'},actor:'A'};
 if(!scenario.startsWith('submit-authorize'))saved=ready;
 impl=async(url,opt)=>{
  if(opt.method==='POST'){
   ++tries;if(scenario==='submit-race')return new Promise(r=>release=r);
   if(tries===1 && ['submit-retry','submit-authorize-retry'].includes(scenario))throw Error('lost ACK');
   if(url.endsWith('/authorize'))saved=ready;
   return {attempt:{state:'unknown'},submission:saved};
  }
  if(!url.includes('/ledger'))return rows();
  const d=ledger('ORDER1');d.receipt.source='wechat';d.order.payment_mode='wechat';
  d.refund_request={request_id:'d'.repeat(32),out_refund_no:'CMR'+'b'.repeat(32),amount:19900,state:'prepared'};
  d.refund_submission=saved;d.refund_send_enabled=scenario!=='submit-disabled';return d;
 };
 start();await tick();await clickOrder();input();el('send-number').value='CMR'+'b'.repeat(32);el('send-amount').value='19900';
 el('customer-reason').value=scenario==='submit-authorize-bytes'?'中'.repeat(27):'客户取消';
 const action=scenario.startsWith('submit-authorize')?'refund-authorize':'refund-send';
 if(scenario==='submit-cancel')accept=false;
 const pending=el(action).onsubmit({preventDefault(){}});await tick();
 if(scenario==='submit-race'){auth.listener(null);release({attempt:{state:'accepted'}})}
 await pending;
 if(['submit-retry','submit-authorize-retry'].includes(scenario))await el(action).onsubmit({preventDefault(){}});
 const sent=posts().map(r=>({url:r.url,body:JSON.parse(r.options.body)}));
 auth.listener(null);
 console.log(JSON.stringify({sent,confirmations,cleared:el('submission-view').textContent===''&&el('send-number').value==='',hidden:el('workspace').hidden}));
}else if(scenario.startsWith('prepare-')){
 paid=true; let saved=null, release, tries=0;
 const prepared=(body)=>({request_id:body.request_id,out_refund_no:'CMR'+'b'.repeat(32),amount:19900,currency:'CNY',state:'prepared',actor:'adminA',evidence:body.evidence,merchant_id:'M',app_id:'A'});
 if(scenario==='prepare-prefill')saved=prepared({request_id:'a'.repeat(32),evidence:'<script>proof</script>'});
 impl=async(url,opt)=>{
  if(opt.method==='POST'){
   ++tries;const body=JSON.parse(opt.body);
   if(scenario==='prepare-race')return new Promise(r=>release=r);
   if(tries===1 && ['prepare-retry','prepare-change'].includes(scenario))throw Error('unknown');
   saved=prepared(body);if(scenario==='prepare-lost-ack')throw Error('lost ACK');
   return {refund_request:saved};
  }
  if(!url.includes('/ledger'))return rows();
  const d=ledger('ORDER1');d.receipt.source='wechat';d.order.payment_mode='wechat';d.refund_prepare_allowed=!saved;d.refund_request=saved;return d;
 };
 start();await tick();await clickOrder();
 if(scenario==='prepare-prefill'){
  el('request-prefill').onclick();const filled=el('refund-no').value,text=el('request-view').textContent;
  saved.state='completed_elsewhere';await el('refresh').onclick();el('refund-no').value='';el('request-prefill').onclick();
  console.log(JSON.stringify({posts:posts().length,filled,text,blocked:el('request-prefill').disabled&&el('refund-no').value==='',confirm:el('confirm-no').value}));
 }else{
  input();el('request-amount').value='19900';if(scenario==='prepare-cancel')accept=false;
  const submitPrepare=()=>el('refund-request').onsubmit({preventDefault(){}});
  const pending=submitPrepare();await tick();
  if(scenario==='prepare-race'){auth.listener(null);auth.listener({username:'adminB',role:1});release({refund_request:prepared({request_id:'a'.repeat(32)})})}
  await pending;await tick();
  if(scenario==='prepare-change')el('evidence').value='different note';
  if(['prepare-retry','prepare-change','prepare-lost-ack'].includes(scenario))await submitPrepare();
  const bodies=posts().map(r=>JSON.parse(r.options.body));
  console.log(JSON.stringify({bodies,posts:posts().length,hidden:el('refund-request').hidden,text:el('request-view').textContent,message:el('message').textContent,proof:el('evidence').value,confirmation:confirmations.at(-1)}));
 }
}else if(scenario==='refund-notice' || scenario==='refund-partial' || scenario==='refund-notice-race'){
 paid=true;impl=async(url)=>{if(!url.includes('/ledger'))return rows();const d=ledger('ORDER1');d.receipt.source='wechat';d.order.payment_mode='wechat';d.refund_notice={notification_id:'NOTICE',refund_no:'REFUND1',refund_id:'500123',state:'SUCCESS',refund:19900,partial:scenario==='refund-partial'};return d};
 start();await tick();await clickOrder();
 const text=el('refund-notice').textContent,disabled=el('refund-prefill').disabled;
 el('refund-prefill').onclick();
 const filled=el('refund-no').value,confirmed=el('confirm-no').value,evidence=el('evidence').value;
 let release;if(scenario==='refund-notice-race'){
  const old=impl;impl=async(url)=>url.includes('/ledger')?new Promise(r=>release=r):rows('BOB');
  const pending=el('refresh').onclick();await tick();auth.listener(null);auth.listener({username:'adminB',role:1});
  release(await old('/ledger'));await pending;await tick();
 }else{auth.listener(null);await tick()}
 el('refund-prefill').onclick();
 console.log(JSON.stringify({text,disabled,filled,confirmed,evidence,posts:posts().length,cleared:el('refund-notice').textContent===''&&el('refund-no').value==='',title:el('title').textContent}));
}else if(scenario==='refund-manual' || scenario==='refund-query'){
 paid=true; const mode=scenario==='refund-query'?'wechat':'manual';
 impl=async(url)=>{if(!url.includes('/ledger'))return rows();const d=ledger('ORDER1');d.receipt.source=mode;d.order.payment_mode=mode;return d};
 start();await tick();await clickOrder();input();
 el('refund-reference').value='BANK-REFUND';el('refund-amount').value='19900';el('refund-time').value='2026-09-17T13:00:00+02:00';el('refund-no').value='REFUND1';
 accept=false;await el(scenario).onsubmit({preventDefault(){}});const cancelled=posts().length;accept=true;
 await el(scenario).onsubmit({preventDefault(){}});
 const first=posts()[0];auth.listener(null);await tick();
 console.log(JSON.stringify({cancelled,url:first.url,body:JSON.parse(first.options.body),cleared:el('refund-reference').value===''&&el('refund-time').value===''&&el('refund-receipt').textContent==='',hidden:el('workspace').hidden}));
}else if(scenario==='refund-display'){
 paid=true;impl=async(url)=>{if(!url.includes('/ledger'))return rows();const d=ledger('ORDER1');d.refund={source:'manual',refund_id:'REFUND1',out_refund_no:'BANK1',amount:19900,currency:'CNY',completed_at:'TIME',received_at:'LOCAL',actor:'FIRST',evidence:'<script>REFUND</script>'};return d};
 start();await tick();await clickOrder();
 console.log(JSON.stringify({posts:posts().length,hidden:el('refund-manual').hidden,receipt:el('refund-receipt').textContent}));
}else if(scenario==='permissions'){
 auth.user={username:'ordinary',role:0};start();await tick();
 console.log(JSON.stringify({requests:requests.length,hidden:el('workspace').hidden,empty:el('list').children.length===0}));
}else if(scenario==='list-account-race'){
 let release;impl=()=>new Promise(r=>release=r);start();await tick();const oldRelease=release;
 await auth.listener(null);impl=async()=>rows('BOB');await auth.listener({username:'adminB',role:1});await tick();
 oldRelease({httpStatus:403});await tick();
 console.log(JSON.stringify({visible:!el('workspace').hidden,text:el('list').children[0].children[0].textContent,access:el('access').textContent}));
}else if(scenario==='detail-race'){
 start();await tick();let release;impl=async(url)=>url.includes('ORDER1/ledger')?new Promise(r=>release=r):ledger('ORDER2');
 await clickOrder();await clickOrder(1);release(ledger('ORDER1'));await tick();
 console.log(JSON.stringify({title:el('title').textContent,event:el('events').children[0].textContent}));
}else if(scenario==='manual-confirmation'){
 start();await tick();await clickOrder();input('WRONG');await submit();input();el('amount').value='199.00';await submit();
 input();el('reference').value='<script>';await submit();input();accept=false;await submit();
 const before=posts().length;accept=true;impl=async(url,opt)=>{if(opt.method==='POST'){paid=true;return {paid:true}}return ledger('ORDER1')};
 await submit();await tick();
 console.log(JSON.stringify({before,payload:JSON.parse(posts()[0].options.body),url:posts()[0].url,receipt:el('receipt').textContent,
   hidden:el('manual').hidden,confirmation:confirmations.at(-1)}));
}else if(scenario==='mutation-account-race'){
 start();await tick();await clickOrder();input();let release;
 impl=async(url,opt)=>opt.method==='POST'?new Promise(r=>release=r):rows('BOB');
 const pending=submit();await tick();await auth.listener(null);await auth.listener({username:'adminB',role:1});await tick();
 release({paid:true});await pending;await tick();
 console.log(JSON.stringify({posts:posts().length,title:el('title').textContent,receipt:el('receipt').textContent,
  proof:el('evidence').value,message:el('message').textContent,visible:!el('workspace').hidden}));
}else if(scenario==='query'||scenario==='binding'){
 const saved=impl;impl=async(url,opt)=>{if(url.includes('/ledger')){const d=ledger('ORDER1');d.order.payment_mode=scenario==='query'?'wechat':'legacy';return d}return saved(url,opt)};
 start();await tick();await clickOrder();input();el('mode').value='wechat';el('source').value='original/paid.zip';
 impl=async(url,opt)=>opt.method==='POST'?{observed_state:'REFUND',status:'pending',warning:'需人工处理'}:ledger('ORDER1');
 await el(scenario).onsubmit({preventDefault(){}});await tick();
 console.log(JSON.stringify({url:posts()[0].url,body:JSON.parse(posts()[0].options.body),confirmation:confirmations.at(-1)}));
}else if(scenario==='review-filter'){
 impl=async()=>({...rows(),next_cursor:100});start();await tick();el('bucket').value='reviewed';
 await el('next').onclick();await tick();await el('next').onclick();await tick();
 console.log(JSON.stringify({urls:requests.map(r=>r.url)}));
}else if(scenario==='review-retry'||scenario==='review-stale'||scenario==='review-switch'){
 start();await tick();await clickOrder();input();el('review-action').value='close';let tries=0,release;
 impl=async(url,opt)=>{
  if(opt.method==='POST'){
   ++tries;
   if(scenario==='review-switch')return new Promise(r=>release=r);
   if(tries===1){if(scenario==='review-stale')return {httpStatus:409};throw Error('lost response')}
   return {saved:true};
  }
  const d=ledger('ORDER1');if(scenario==='review-stale'){d.review.version=8;d.review.snapshot='b'.repeat(64)}return d;
 };
 const first=el('review').onsubmit({preventDefault(){}});await tick();
 if(scenario==='review-switch'){
  await auth.listener(null);release({saved:true});await first;await tick();
  console.log(JSON.stringify({hidden:el('workspace').hidden,note:el('review-status').textContent,evidence:el('evidence').value}));
 }else{
  await first;await el('review').onsubmit({preventDefault(){}});await tick();
  console.log(JSON.stringify({bodies:posts().map(x=>JSON.parse(x.options.body)),url:posts()[0].url,text:el('review-status').textContent}));
 }
}else if(scenario==='empty-list'){
 impl=async()=>({orders:[],next_cursor:null});
 start();await tick();
 const e=el('list-empty');
 console.log(JSON.stringify({items:el('list').children.length,listText:el('list').textContent,emptyHidden:e.hidden,emptyText:e.textContent}));
}else if(scenario==='detail-visibility'){
 start();await tick();
 const before={detail:el('detail').hidden,hint:el('detail-hint').hidden};
 await clickOrder();
 const selected={detail:el('detail').hidden,hint:el('detail-hint').hidden};
 await auth.listener(null);await tick();
 console.log(JSON.stringify({before,selected,after:{detail:el('detail').hidden,hint:el('detail-hint').hidden}}));
}else if(scenario==='lost-response'){
 start();await tick();await clickOrder();input();
 impl=async(url,opt)=>{if(opt.method==='POST'){paid=true;throw Error('response lost')}return ledger('ORDER1')};
 await submit();await tick();await submit();
 console.log(JSON.stringify({posts:posts().length,receipt:el('receipt').textContent,hidden:el('manual').hidden,reference:el('reference').value}));
}
})().catch(e=>{console.error(e);process.exitCode=1});
'''


@pytest.mark.skipif(shutil.which('node') is None, reason='Node VM execution requires system Node')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
@pytest.mark.parametrize('scenario', ['permissions', 'list-account-race', 'detail-race', 'manual-confirmation',
                                     'mutation-account-race', 'lost-response', 'query', 'binding',
                                     'review-retry', 'review-stale', 'review-switch', 'review-filter',
                                     'empty-list', 'detail-visibility'])
def test_workbench_browser_logic(folder, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / folder / 'payments-admin.js'), scenario],
                            text=True, capture_output=True, check=True, timeout=20)
    data = json.loads(result.stdout)
    if scenario == 'permissions':
        assert data == {'requests': 0, 'hidden': True, 'empty': True}
    elif scenario == 'list-account-race':
        assert data['visible'] and 'BOB1' in data['text'] and 'adminB' in data['access']
    elif scenario == 'detail-race':
        assert data['title'] == '订单 ORDER2' and 'PRIVATE_ORDER2' in data['event'] and 'PRIVATE_ORDER1' not in data['event']
    elif scenario == 'manual-confirmation':
        assert data['before'] == 0 and data['hidden']
        assert data['payload'] == {'evidence': '已在银行核对', 'amount': 19900, 'reference': 'BANK-1'}
        assert data['url'] == '/shop/orders/ORDER1/confirm'
        assert 'ORDER1' in data['confirmation'] and '<script>PROOF</script>' in data['receipt']
    elif scenario == 'mutation-account-race':
        assert data == {'posts': 1, 'title': '请选择订单', 'receipt': '', 'proof': '', 'message': '', 'visible': True}
    elif scenario in ('query', 'binding'):
        assert 'ORDER1' in data['confirmation']
        if scenario == 'query':
            assert data['url'] == '/shop/admin/orders/ORDER1/reconcile'
            assert data['body'] == {'evidence': '已在银行核对', 'confirm_order_no': 'ORDER1'}
        else:
            assert data['url'] == '/shop/orders/ORDER1/legacy-binding'
            assert data['body'] == {'evidence': '已在银行核对', 'payment_mode': 'wechat', 'source_key': 'original/paid.zip'}
    elif scenario == 'empty-list':
        # TD-278：空结果提示写在列表**外**的 role=status 段落里。以前直接写进 <ul> 的 textContent，
        # 读屏把它当成「没有列表项的列表」（axe-core `list`，serious 级，管理页实测命中）。
        assert data == {'items': 0, 'listText': '', 'emptyHidden': False, 'emptyText': '没有匹配订单'}
    elif scenario == 'detail-visibility':
        # 选单前只显示提示、不显示十几个空凭证框；选单后反过来；换账号/退出回到提示。
        assert data['before'] == {'detail': True, 'hint': False}
        assert data['selected'] == {'detail': False, 'hint': True}
        assert data['after'] == {'detail': True, 'hint': False}
    elif scenario == 'review-filter':
        assert 'bucket=reviewed' in data['urls'][1] and 'before=' not in data['urls'][1]
        assert 'before=100' in data['urls'][2]
    elif scenario.startswith('review-'):
        if scenario == 'review-switch':
            assert data == {'hidden': True, 'note': '', 'evidence': ''}
        else:
            first, second = data['bodies']
            assert data['url'] == '/shop/admin/orders/ORDER1/review' and first['action'] == 'close'
            assert '<script>NOTE</script>' in data['text'] and len(first['request_id']) == 32
            if scenario == 'review-retry':
                assert first == second
            else:
                assert first['request_id'] != second['request_id'] and second['expected_version'] == 8
                assert second['snapshot'] == 'b'*64
    else:
        assert data['posts'] == 1 and data['hidden'] and data['reference'] == 'BANK-1' and 'BANK-1' in data['receipt']


def test_hidden_css_and_no_html_interpolation():
    template = (ROOT / 'app/templates/payments-admin.html').read_text(encoding='utf-8')
    script = (ROOT / 'app/frontend/payments-admin.js').read_text(encoding='utf-8')
    assert '.finance [hidden] { display:none !important; }' in template
    assert 'innerHTML' not in script and 'localStorage' not in script


@pytest.mark.skipif(shutil.which('node') is None, reason='HTML pattern v-flag check requires system Node')
def test_html_patterns_use_browser_unicode_sets():
    """Modern HTML pattern uses v, not a plain JS/Python regex; literal slash/hyphen need escapes."""
    patterns = re.findall(r'pattern="([^"]+)"', (ROOT / 'app/templates/payments-admin.html').read_text(encoding='utf-8'))
    assert len(patterns) == 2
    script = "const p=JSON.parse(process.argv[1]).map(x=>new RegExp('^(?:'+x+')$','v'));" \
             "console.log(JSON.stringify([p[0].test('ORDER_1-A'),p[0].test('../x')," \
             "p[1].test('BANK/2026-1'),p[1].test('x space')]))"
    result = subprocess.run(['node', '-e', script, json.dumps(patterns)], text=True, capture_output=True,
                            check=True, timeout=20)
    assert json.loads(result.stdout) == [True, False, True, False]


@pytest.mark.parametrize('file', ['app/frontend/payments-admin.js', 'app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['refund-manual', 'refund-query', 'refund-display'])
def test_refund_controls_in_actual_source_and_bundle(file, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / file), scenario], text=True, capture_output=True,
                            check=True, timeout=20)
    data = json.loads(result.stdout)
    if scenario == 'refund-display':
        assert data['posts'] == 0 and data['hidden']
        assert '<script>REFUND</script>' in data['receipt'] and 'FIRST' in data['receipt']
    else:
        assert data['cancelled'] == 0 and data['cleared'] and data['hidden']
        assert data['url'].endswith('/refunds/' + ('query' if scenario == 'refund-query' else 'manual'))
        assert data['body']['confirm_order_no'] == 'ORDER1'
        if scenario == 'refund-query':
            assert data['body']['out_refund_no'] == 'REFUND1' and 'amount' not in data['body']
        else:
            assert data['body']['reference'] == 'BANK-REFUND' and data['body']['amount'] == 19900
            assert data['body']['completed_at'] == '2026-09-17T13:00:00+02:00'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node VM execution requires system Node')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
@pytest.mark.parametrize('scenario', ['refund-notice', 'refund-partial', 'refund-notice-race'])
def test_refund_notice_only_refills_and_never_submits_or_leaks(folder, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / folder / 'payments-admin.js'), scenario],
                            text=True, capture_output=True, check=True, timeout=20)
    data = json.loads(result.stdout)
    assert '不是本地退款完成凭证' in data['text'] and 'SUCCESS' in data['text']
    assert data['posts'] == 0 and data['confirmed'] == data['evidence'] == '' and data['cleared']
    assert data['filled'] == ('' if scenario == 'refund-partial' else 'REFUND1')
    assert data['disabled'] is (scenario == 'refund-partial') and data['title'] == '请选择订单'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node VM execution requires system Node')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
@pytest.mark.parametrize('scenario', ['prepare-retry', 'prepare-change', 'prepare-lost-ack', 'prepare-race', 'prepare-cancel', 'prepare-prefill'])
def test_preparation_ui_preserves_request_and_never_sends_money(folder, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / folder / 'payments-admin.js'), scenario],
                            text=True, capture_output=True, check=True, timeout=20)
    data = json.loads(result.stdout)
    if scenario == 'prepare-prefill':
        assert data['posts'] == 0 and data['confirm'] == '' and data['filled'] == 'CMR' + 'b' * 32 and data['blocked']
        assert '<script>proof</script>' in data['text']
    elif scenario == 'prepare-cancel':
        assert data['posts'] == 0
    elif scenario == 'prepare-race':
        assert data['posts'] == 1 and data['text'] == data['message'] == data['proof'] == ''
    elif scenario == 'prepare-change':
        assert data['posts'] == 1 and '结果未知' in data['message']
    else:
        assert data['hidden'] and '仅有本地准备' in data['text']
        assert '不是发送或自动退款授权' in data['confirmation']
        assert data['posts'] == (2 if scenario == 'prepare-retry' else 1)
        assert all(body == data['bodies'][0] for body in data['bodies'])
        assert re.fullmatch(r'[0-9a-f]{32}', data['bodies'][0]['request_id'])
        assert data['bodies'][0]['amount'] == 19900


@pytest.mark.parametrize('path', ['app/frontend/payments-admin.js','app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['submit-retry','submit-authorize-retry','submit-disabled','submit-cancel','submit-race','submit-authorize-bytes'])
def test_explicit_submission_ui_is_not_automatic(path, scenario):
    process = subprocess.run(['node', '-e', HARNESS, str(ROOT / path), scenario], text=True, capture_output=True, timeout=15, check=True)
    result = json.loads(process.stdout)
    assert result['cleared'] and result['hidden']
    if scenario in ('submit-disabled','submit-cancel','submit-authorize-bytes'):
        assert result['sent'] == []
    elif scenario.endswith('retry'):
        assert len(result['sent']) == 2
        assert result['sent'][0] == result['sent'][1]
        assert len(result['sent'][0]['body']['request_id']) == 32
        assert all('即将真实' in text or '冻结并授权' in text for text in result['confirmations'])
    else:
        assert len(result['sent']) == 1


@pytest.mark.parametrize('path', ['app/frontend/payments-admin.js','app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['stop-retry','stop-change','stop-cancel','stop-race','stop-lost-ack'])
def test_stop_ui_keeps_original_request_and_never_sends_refund(path, scenario):
    process = subprocess.run(['node', '-e', HARNESS, str(ROOT / path), scenario], text=True, capture_output=True, timeout=15, check=True)
    data = json.loads(process.stdout)
    assert data['cleared']
    assert all(item['url'].endswith('/refunds/stop') for item in data['sent'])
    if scenario == 'stop-cancel':
        assert data['sent'] == []
    elif scenario == 'stop-retry':
        assert len(data['sent']) == 2 and data['sent'][0] == data['sent'][1]
    else:
        assert len(data['sent']) == 1
    if scenario in ('stop-retry','stop-lost-ack'):
        assert data['hidden'] and data['stopHidden'] and '不是渠道取消' in data['text']


@pytest.mark.parametrize('path', ['app/frontend/payments-admin.js', 'app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['verify-state', 'verify-completed', 'verify-race'])
def test_read_only_verification_queue_and_account_isolation(path, scenario):
    process = subprocess.run(['node', '-e', HARNESS, str(ROOT / path), scenario], text=True, capture_output=True, timeout=15, check=True)
    result = json.loads(process.stdout)
    assert result['posts'] == 0 and result['cleared']
    if scenario == 'verify-race':
        assert result['text'] == ''
    else:
        assert 'ORIGINAL' in result['text'] and '<script>SUCCESS</script>' in result['text']
        assert '仅显示最近50项' in result['text'] and '不证明进程在线' not in result['text']
        assert ('该原号已有成功退款凭证' if scenario == 'verify-completed' else '待管理员按原号确认') in result['text']


@pytest.mark.parametrize('path', ['app/frontend/payments-admin.js', 'app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['control-retry', 'control-change', 'control-cancel', 'control-race', 'control-lost-ack', 'control-stale'])
def test_verification_control_immutable_unknown_request_and_account_isolation(path, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / path), scenario],
                            text=True, capture_output=True, timeout=15, check=True)
    data = json.loads(result.stdout)
    assert data['cleared']
    assert all(x['url'].endswith('/refunds/verification/control') for x in data['sent'])
    assert all('不发退款' in c and '原退款号 ORIGINAL' in c for c in data['confirmations'])
    if scenario == 'control-cancel':
        assert not data['sent']
    elif scenario == 'control-retry':
        assert len(data['sent']) == 2 and data['sent'][0] == data['sent'][1]
    elif scenario == 'control-stale':
        assert len(data['sent']) == 2 and data['sent'][0]['body']['snapshot'] != data['sent'][1]['body']['snapshot']
    else:
        assert len(data['sent']) == 1
    if scenario in ('control-retry', 'control-lost-ack', 'control-stale'):
        assert 'FIRST<script>' in data['text'] and '不是当前状态保证' in data['text']


@pytest.mark.parametrize('path', ['app/frontend/payments-admin.js', 'app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['system-on', 'system-off', 'system-race'])
def test_system_receipt_identity_and_authority_display(path, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / path), scenario],
                            text=True, capture_output=True, timeout=15, check=True)
    data = json.loads(result.stdout)
    assert data['posts'] == 0 and data['cleared']
    if scenario == 'system-race':
        assert data['text'] == data['receipt'] == ''
    else:
        assert '系统核验（非管理员代办）' in data['receipt'] and 'system<script>' in data['receipt']
        assert '123' in data['receipt']
        assert ('已允许系统登记' if scenario == 'system-on' else 'SUCCESS观察不自动撤销下载') in data['text']


@pytest.mark.parametrize('path', ['app/frontend/payments-admin.js', 'app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['reapprove-save', 'reapprove-unknown', 'reapprove-changed', 'reapprove-denied', 'reapprove-race'])
def test_reauthorization_ui_frozen_unknown_and_no_automatic_send(path, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / path), scenario],
                            text=True, capture_output=True, timeout=15, check=True)
    data = json.loads(result.stdout)
    assert data['cleared']
    if scenario in ('reapprove-denied', 'reapprove-race'):
        assert data['posts'] == []
        if scenario == 'reapprove-race':
            assert data['history'] == ''
        return
    assert not data['hidden'] and 'first<script>' in data['history'] and 'old-stop' in data['history']
    assert len(data['posts']) == (2 if scenario == 'reapprove-unknown' else 1)
    if scenario == 'reapprove-unknown':
        assert data['posts'][0] == data['posts'][1]
    assert data['posts'][0]['url'].endswith('/refunds/reauthorize')
    assert data['posts'][0]['body']['authorization_id'] == 'a'*32
    assert data['posts'][0]['body']['reason'] == 'corrected customer reason'
    assert '只授权不发送' in data['confirmations'][0]


@pytest.mark.parametrize('path', ['app/frontend/payments-admin.js', 'app/static/js/payments-admin.js'])
@pytest.mark.parametrize('scenario', ['close-save', 'close-unknown', 'close-change', 'close-denied', 'close-cancel', 'close-race'])
def test_channel_close_explicit_unknown_frozen_and_account_isolation(path, scenario):
    result = subprocess.run(['node', '-e', HARNESS, str(ROOT / path), scenario],
                            text=True, capture_output=True, timeout=15, check=True)
    data = json.loads(result.stdout)
    assert data['cleared']
    if scenario in ('close-denied', 'close-cancel', 'close-race'):
        assert data['posts'] == []
        if scenario == 'close-race':
            assert data['text'] == ''
        return
    assert 'FIRST<script>' in data['text'] and 'closed不等于微信已关闭' in data['text']
    assert len(data['posts']) == (2 if scenario == 'close-unknown' else 1)
    assert data['posts'][0]['url'].endswith('/close-channel')
    assert data['posts'][0]['body']['query_attempt_id'] == 'a'*32
    if scenario == 'close-unknown':
        assert data['posts'][0] == data['posts'][1]
    assert '不是退款' in data['confirmations'][0]
