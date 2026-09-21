"""Cross-review regressions: bounded work/input, callback shape, and real JS lifecycle.

All provider responses here are synthetic. Node tests do not certify a browser.
"""
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.ratelimit import Limiter
from app.routers import shop
from app.routers.diagrams import _parse_if_match
from tests.conftest import engine
from tests.test_download import auth_headers
from tests.test_second_frontend_regressions import HARNESS
from tests.test_shop_polling import _EXPIRY_HARNESS
from tests.test_wechat_pay import CFG

ROOT = Path(__file__).resolve().parents[1]


def test_limiter_admission_never_scans_all_keys_or_evicts_active_bucket():
    class NoScan(dict):
        def items(self):
            raise AssertionError('request path performed a full scan')

    now = [1000.0]
    lim = Limiter(clock=lambda: now[0], max_keys=2000, _hits=NoScan())
    for i in range(2000):
        assert lim.allow(str(i), limit=1, window=60)[0]
    assert not lim.allow('overflow', limit=1, window=60)[0]
    assert not lim.allow('0', limit=1, window=60)[0]
    assert len(lim._hits) == len(lim._expires) == 2000 and set(lim._hits) == set(lim._expires)
    now[0] += 61
    assert lim.allow('overflow', limit=1, window=60)[0]
    assert len(lim._hits) <= 2000
    lim.reset()
    assert not lim._hits and not lim._expires


def test_limiter_expiration_uses_each_buckets_window():
    now = [0.0]
    lim = Limiter(clock=lambda: now[0])
    assert lim.allow('long', limit=1, window=100)[0]
    now[0] = 20
    assert lim.allow('short', limit=1, window=1)[0]
    assert not lim.allow('long', limit=1, window=100)[0]


@pytest.mark.parametrize('raw', ['9' * 5000, '""1""', '"1', '1"', '¹', '0', '-1', '2147483648', 'W/"1"', '"1","2"'])
def test_if_match_rejects_unbounded_or_malformed_tokens(raw):
    with pytest.raises(HTTPException) as exc:
        _parse_if_match(raw)
    assert exc.value.status_code == 400
    assert len(exc.value.detail) < 100


@pytest.mark.parametrize('raw', ['1', '"1"', ' "2147483647" '])
def test_if_match_keeps_supported_strong_version_forms(raw):
    assert _parse_if_match(raw) == int(raw.strip().strip('"'))


async def test_login_nul_is_normal_auth_failure(client):
    response = await client.post('/auth/login', data={'username': 'bad\x00name', 'password': 'test-pass'})
    assert response.status_code == 401
    assert response.json()['detail'] == '用户名或密码错误'


@pytest.mark.parametrize('field', ['name', 'content'])
async def test_diagram_nul_is_rejected_before_database(client, field):
    headers = await auth_headers(client)
    body = {'name': 'valid', 'content': '<mxfile/>'}
    body[field] += '\x00'
    response = await client.post('/diagrams', headers=headers, json=body)
    assert response.status_code == 422
    assert (await client.get('/diagrams', headers=headers)).json() == []


async def test_diagram_list_does_not_select_xml(client):
    headers = await auth_headers(client)
    assert (await client.post('/diagrams', headers=headers, json={'name': 'summary', 'content': '<mxfile/>'})).status_code == 201
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(engine.sync_engine, 'before_cursor_execute', capture)
    try:
        response = await client.get('/diagrams', headers=headers)
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', capture)
    assert response.status_code == 200 and response.json()[0]['name'] == 'summary'
    selected = [s.split('from')[0] for s in statements if s.lstrip().startswith('select') and 'from sys_diagram' in s]
    assert selected and all('content' not in s for s in selected)


async def test_log_fields_are_bounded_and_control_characters_escaped(client, caplog):
    with caplog.at_level(logging.INFO, logger='codemax.access'):
        response = await client.get('/missing%0dINJECTED', headers={'X-Request-ID': 'x' * 5000})
    assert re.fullmatch('[a-f0-9]{32}', response.headers['x-request-id'])
    messages = [r.getMessage() for r in caplog.records if r.name == 'codemax.access']
    assert messages and all('\r' not in m and '\n' not in m and len(m) < 1500 for m in messages)
    assert '\\r' in messages[-1]


@pytest.fixture
def callback_stub(monkeypatch):
    monkeypatch.setattr(shop, 'pay_config', lambda: SimpleNamespace(notify_ready=True, platform_cert='test', api_v3_key='test', appid='test', mchid='test'))
    monkeypatch.setattr(shop, 'assert_notify_fresh', lambda value: None)
    monkeypatch.setattr(shop, 'assert_notify_identity', lambda *args: None)
    monkeypatch.setattr(shop, 'verify_notify_signature', lambda *args, **kw: None)
    monkeypatch.setattr(shop, 'decrypt_resource', lambda *args, **kw: {})


@pytest.mark.parametrize('raw,status', [(b'\xff', 400), (b'x' * 65537, 413), (b'[]', 400), (b'null', 400), (b'{"resource":[]}', 400)])
async def test_callback_bad_bytes_or_structure_never_become_500(client, callback_stub, raw, status):
    response = await client.post('/shop/pay/notify', content=raw)
    assert response.status_code == status
    assert response.json()['code'] == 'FAIL'


@pytest.mark.parametrize('data', [None, [], {'trade_state': 'SUCCESS'}, {'trade_state': 'SUCCESS', 'out_trade_no': 'order', 'transaction_id': 'tx', 'amount': []}])
async def test_decrypted_callback_shape_is_checked(client, callback_stub, monkeypatch, data):
    monkeypatch.setattr(shop, 'decrypt_resource', lambda *args, **kw: data)
    response = await client.post('/shop/pay/notify', json={'event_type': 'TRANSACTION.SUCCESS', 'resource': {'ciphertext': 'test', 'nonce': 'test'}})
    assert response.status_code == 400 and response.json()['code'] == 'FAIL'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required to execute actual frontend code')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
def test_support_without_randomuuid_retries_same_valid_nonce(folder):
    harness = HARNESS.replace("const start=()=>vm.runInNewContext", "delete context.crypto; const start=()=>vm.runInNewContext")
    result = subprocess.run(['node', '-e', harness, str(ROOT / folder / 'support-page.js'), 'support-retry'], capture_output=True, text=True, check=True, timeout=20)
    out = json.loads(result.stdout)
    assert out['payloads'][0] == out['payloads'][1]
    assert re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}', out['payloads'][0]['client_nonce'])


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required to execute actual frontend code')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
def test_admin_without_selected_conversation_cannot_send(folder):
    harness = HARNESS.replace("username:'alice',role:0", "username:'alice',role:1")
    result = subprocess.run(['node', '-e', harness, str(ROOT / folder / 'support-page.js'), 'support-retry'], capture_output=True, text=True, check=True, timeout=20)
    assert json.loads(result.stdout)['payloads'] == []


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required to execute actual frontend code')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
def test_cancel_invalidates_pending_shop_response(folder):
    source = (ROOT / folder / 'shop-page.js').read_text()
    scenario = r'''
(async()=>{
  await click('btn-buy');
  let release;
  global.fetch = () => new Promise(r => release = r);
  const underway = __pendingTimer.fn();
  click('btn-cancel');
  release({ok:true,status:200,json:async()=>({order_no:'CM1',status:'pending',expired:false})});
  await underway;
  console.log(JSON.stringify({landing:!els['st-landing'].hidden,pending:!els['st-pending'].hidden,timer:__state().timerActive}));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run(['node', '-e', _EXPIRY_HARNESS + '\n' + source + '\n' + scenario], capture_output=True, text=True, check=True, timeout=20)
    assert json.loads(result.stdout) == {'landing': True, 'pending': False, 'timer': False}


@pytest.mark.parametrize('prefix', ['TEMP', 'TEMPORARY', 'UNLOGGED'])
def test_sql_temporary_table_not_silently_lost(prefix):
    from app.tools.sql_ddl import parse_ddl
    graph = parse_ddl(f'CREATE {prefix} TABLE t (id INT);')
    assert [t['name'] for t in graph['tables']] == ['t']


def test_sql_dollar_and_nested_comment_cannot_invent_tables():
    from app.tools.sql_ddl import parse_ddl
    sql = '''DO $body$ BEGIN RAISE NOTICE 'CREATE TABLE phantom(id INT);'; END $body$;
    /* outer /* inner */ CREATE TABLE phantom2(id INT); */
    CREATE TABLE actual ("a,b" INT, b INT, PRIMARY KEY ("a,b",b));'''
    graph = parse_ddl(sql)
    assert [t['name'] for t in graph['tables']] == ['actual']
    assert all(c['primary_key'] for c in graph['tables'][0]['columns'])


def test_sql_common_types_and_parenthesized_default_keep_information():
    from app.tools.sql_ddl import parse_ddl
    graph = parse_ddl('CREATE TABLE t(a TIMESTAMP(6) WITH TIME ZONE,b DOUBLE PRECISION,c INTEGER[],d public.mytype,e INT DEFAULT (1 + (2)));')
    assert [c['type'] for c in graph['tables'][0]['columns']] == ['TIMESTAMP(6) WITH TIME ZONE', 'DOUBLE PRECISION', 'INTEGER[]', 'PUBLIC.MYTYPE', 'INT']
    assert graph['tables'][0]['columns'][-1]['default'] == '(1 + (2))'


async def test_prepay_only_after_durable_local_order(client, monkeypatch):
    from sqlalchemy import select

    from app.models import Order
    from tests.conftest import TestSession

    headers = await auth_headers(client)
    seen = []
    monkeypatch.setattr(shop, 'pay_config', lambda: CFG)

    async def provider(cfg, **kwargs):
        async with TestSession() as db:
            row = await db.scalar(select(Order).where(Order.order_no == kwargs['out_trade_no']))
            assert row is not None
            assert kwargs['total'] == row.amount and kwargs['description'] == row.product_name
        seen.append(kwargs['out_trade_no'])
        return 'weixin://test-only'

    monkeypatch.setattr(shop, 'native_prepay', provider)
    response = await client.post('/shop/orders', headers=headers)
    assert response.status_code == 200 and seen == [response.json()['order_no']]


async def test_commit_failure_never_reaches_payment_provider(client, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    headers = await auth_headers(client)
    monkeypatch.setattr(shop, 'pay_config', lambda: CFG)
    calls = []

    async def provider(*args, **kwargs):
        calls.append(kwargs)
        return 'weixin://must-not-be-created'

    async def failed_commit(self):
        raise RuntimeError('injected pre-commit failure')

    monkeypatch.setattr(shop, 'native_prepay', provider)
    monkeypatch.setattr(AsyncSession, 'commit', failed_commit)
    with pytest.raises(RuntimeError, match='injected pre-commit failure'):
        await client.post('/shop/orders', headers=headers)
    assert not calls


@pytest.mark.parametrize('origin', ['https://', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com?x=1', 'https://example.com/#x', 'https://example.com:bad', 'https://exa<mple.com', 'https://@example.com', 'https://example.com?', 'https://example.com#'])
def test_production_origin_is_not_just_a_prefix(origin, monkeypatch):
    from app.config import settings
    from app.startup_checks import check_production_settings

    monkeypatch.setattr(settings, 'ENV', 'production')
    monkeypatch.setattr(settings, 'SITE_BASE_URL', origin)
    assert any('SITE_BASE_URL' in problem for problem in check_production_settings())


def test_production_links_ignore_request_host(monkeypatch):
    from starlette.requests import Request

    from app.config import settings
    from app.middleware import public_base_url

    monkeypatch.setattr(settings, 'ENV', 'production')
    monkeypatch.setattr(settings, 'SITE_BASE_URL', 'https://shop.example.com')
    req = Request({'type': 'http', 'scheme': 'http', 'path': '/', 'root_path': '', 'query_string': b'', 'headers': [(b'host', b'evil.invalid')], 'server': ('evil.invalid', 80)})
    assert public_base_url(req) == 'https://shop.example.com'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node required to execute actual frontend code')
@pytest.mark.parametrize('folder', ['app/frontend', 'app/static/js'])
@pytest.mark.parametrize('page', ['support', 'drawio'])
def test_management_lists_ignore_out_of_order_responses(folder, page):
    extra = r'''
if(scenario==='list-race'){
  auth.user.role=1;start();await tick();
  const releases=[];
  fetchImpl=()=>new Promise(r=>releases.push(r));
  const trigger=()=>get(PAGE==='support'?'support-inbox-refresh':'btn-manage').onclick();
  trigger();await tick();trigger();await tick();
  const newer={id:2,customer_id:2,username:'NEW',name:'NEW',version:1};
  const older={id:1,customer_id:1,username:'OLD',name:'OLD',version:1};
  if(PAGE==='support'){
    releases[1]([newer]);await tick();releases[0]([older]);
  }else{
    releases[2]([newer]);releases[3]([]);await tick();releases[0]([older]);releases[1]([]);
  }
  await tick();
  console.log(JSON.stringify(get(PAGE==='support'?'support-inbox':'diagram-manage').children.map(x=>x.textContent)));
}else '''.replace('PAGE', json.dumps(page))
    harness = HARNESS.replace("if(scenario==='support-privacy'){", extra + "if(scenario==='support-privacy'){")
    result = subprocess.run(['node', '-e', harness, str(ROOT / folder / f'{page}-page.js'), 'list-race'], capture_output=True, text=True, check=True, timeout=20)
    rows = json.loads(result.stdout)
    assert len(rows) == 1 and 'NEW' in rows[0] and 'OLD' not in rows[0]


def test_sql_scanner_does_not_copy_remaining_text_for_each_dollar():
    from app.tools.sql_ddl import _scan

    class NoTailSlice(str):
        def __getitem__(self, key):
            if isinstance(key, slice) and key.stop is None:
                raise AssertionError('scanner copied a remaining-string suffix')
            return super().__getitem__(key)

    assert len(list(_scan(NoTailSlice('$' * 1000)))) == 1000
