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
const context={document:{getElementById:get,createElement:make},AbortController,URLSearchParams,Number,
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
if(scenario==='refund-notice' || scenario==='refund-partial' || scenario==='refund-notice-race'){
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
                                     'review-retry', 'review-stale', 'review-switch', 'review-filter'])
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
    template = (ROOT / 'app/templates/payments-admin.html').read_text()
    script = (ROOT / 'app/frontend/payments-admin.js').read_text()
    assert '.finance [hidden] { display:none !important; }' in template
    assert 'innerHTML' not in script and 'localStorage' not in script


@pytest.mark.skipif(shutil.which('node') is None, reason='HTML pattern v-flag check requires system Node')
def test_html_patterns_use_browser_unicode_sets():
    """Modern HTML pattern uses v, not a plain JS/Python regex; literal slash/hyphen need escapes."""
    patterns = re.findall(r'pattern="([^"]+)"', (ROOT / 'app/templates/payments-admin.html').read_text())
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
