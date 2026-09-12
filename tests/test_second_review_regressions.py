"""Second-batch defensive regressions. Network traffic uses in-memory substitutes only."""
import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from jose import jwt
from sqlalchemy import select

from app import cpu_pool
from app.config import settings
from app.models import Article, User
from app.routers import auth, health
from app.security import decode_token
from app.storage import verify_download
from app.tools import crawler, support
from app.tools.llm import LLMClient, LLMError
from app.tools.sql_ddl import parse_ddl
from app.tools.word import build_data_dictionary
from app.wechat_pay import WeChatPayError, assert_notify_fresh
from tests.conftest import TestSession, engine, sso_authorize
from tests.test_support_messages import identity, message

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_two_password_changes_at_identical_instant_revoke_both_older_tokens(client, monkeypatch):
    _, first = await identity(client, 'rev_user')
    class Frozen:
        @staticmethod
        def now(tz):
            return datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(auth, 'datetime', Frozen)
    r = await client.post('/auth/password', headers=first, json={'old_password': 'secret123', 'new_password': 'second123'})
    second = {'Authorization': 'Bearer ' + r.json()['access_token']}
    r = await client.post('/auth/password', headers=second, json={'old_password': 'second123', 'new_password': 'third123'})
    third = {'Authorization': 'Bearer ' + r.json()['access_token']}
    assert (await client.get('/auth/me', headers=first)).status_code == 401
    assert (await client.get('/auth/me', headers=second)).status_code == 401
    assert (await client.get('/auth/me', headers=third)).status_code == 200


def test_legacy_token_without_revision_is_rejected():
    token = jwt.encode({'sub': 'alice', 'exp': 2000000000}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    assert decode_token(token) is None


@pytest.mark.asyncio
async def test_old_oauth_code_cannot_refresh_new_credential_revision(client):
    _, headers = await identity(client, 'oauth_revision')
    response = await sso_authorize(client, headers, client_id='tools', redirect_uri='https://tools.codemax.top/callback')
    from urllib.parse import parse_qs, urlsplit
    code = parse_qs(urlsplit(response.headers['location']).query)['code'][0]
    assert (await client.post('/auth/password', headers=headers, json={'old_password': 'secret123', 'new_password': 'newsecret123'})).status_code == 200
    result = await client.post('/oauth/token', data={'grant_type': 'authorization_code', 'code': code,
                              'redirect_uri': 'https://tools.codemax.top/callback', 'client_id': 'tools',
                              'client_secret': 'codemax-tools-secret'})
    assert result.status_code == 400 and result.json()['detail']['error'] == 'invalid_grant'


@pytest.mark.asyncio
async def test_updated_article_invalidates_cached_index_without_process_reset(client):
    support.reset_article_index()
    async with TestSession() as db:
        row = Article(url='https://example.invalid/article', title='数据库', content='数据库 索引 old')
        db.add(row)
        await db.commit()
        await support._retrieve_articles(db, '数据库')
        fingerprint = support._ARTICLE_CACHE[0]
    async with TestSession() as db:
        row = await db.scalar(select(Article))
        row.content = '数据库 新内容 new'
        await db.commit()
    async with TestSession() as db:
        results = await support._retrieve_articles(db, '数据库')
    assert support._ARTICLE_CACHE[0] != fingerprint
    assert any('new' in body for _, body in results)
    support.reset_article_index()


@pytest.mark.parametrize('value', ['--1', '²', '9' * 5000, ''])
def test_malformed_payment_timestamp_is_a_controlled_error(value):
    with pytest.raises(WeChatPayError):
        assert_notify_fresh(value)


def test_nonascii_download_signature_is_rejected_not_500():
    assert not verify_download('secret', 'a.zip', 2000000000, '无效')


@pytest.mark.asyncio
async def test_nul_password_and_terminal_newline_username_are_rejected(client):
    assert (await client.post('/auth/register', json={'username': 'safe_user\n', 'password': 'secret123'})).status_code == 422
    assert (await client.post('/auth/register', json={'username': 'safe_user', 'password': 'secret\x00123'})).status_code == 422
    assert (await client.post('/auth/login', data={'username': 'nobody', 'password': '\x00'})).status_code == 401


def test_literal_keywords_do_not_become_column_constraints():
    graph = parse_ddl("CREATE TABLE t (a TEXT DEFAULT 'PRIMARY KEY NOT NULL REFERENCES x(id)', b TEXT COMMENT 'DEFAULT x');")
    a, b = graph['tables'][0]['columns']
    assert not a['primary_key'] and a['nullable'] and not graph['edges']
    assert b['default'] is None


def test_schema_comments_and_references_do_not_cross_same_named_tables():
    graph = parse_ddl("CREATE TABLE a.t (id INT PRIMARY KEY); CREATE TABLE b.t (id INT, ref INT REFERENCES a.t(id)); COMMENT ON COLUMN b.t.id IS 'only b';")
    assert [t['name'] for t in graph['tables']] == ['a.t', 'b.t']
    assert graph['tables'][0]['columns'][0]['comment'] is None
    assert graph['tables'][1]['columns'][0]['comment'] == 'only b'
    assert graph['edges'][0]['to_table'] == 'a.t'
    quoted = parse_ddl('CREATE TABLE "a.t" (id INT); CREATE TABLE a.t (id INT); CREATE TABLE b.t (id INT);')
    assert len({t['name'] for t in quoted['tables']}) == 3



def test_word_export_removes_xml_incompatible_controls():
    graph = parse_ddl("CREATE TABLE t (a TEXT COMMENT 'hello\\0world');")
    with ZipFile(BytesIO(build_data_dictionary(graph))) as archive:
        xml = archive.read('word/document.xml')
    assert b'helloworld' in xml and b'\x00' not in xml


@pytest.mark.asyncio
async def test_cpu_task_error_does_not_poison_executor(monkeypatch):
    monkeypatch.setattr(cpu_pool, '_broken', False)
    def bad_input():
        raise ValueError('invalid document')
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(cpu_pool, '_get_executor', lambda: executor)
        with pytest.raises(ValueError, match='invalid document'):
            await cpu_pool.run_cpu_bound(bad_input)
    assert not cpu_pool._broken


@pytest.mark.asyncio
async def test_cpu_cancelled_request_keeps_admission_slot_until_work_finishes(monkeypatch):
    release = asyncio.Event()
    async def work(*args):
        await release.wait()
        return 'done'
    monkeypatch.setattr(cpu_pool, '_execute_cpu', work)
    a = asyncio.create_task(cpu_pool.run_cpu_bound(None))
    b = asyncio.create_task(cpu_pool.run_cpu_bound(None))
    await asyncio.sleep(0)
    try:
        with pytest.raises(cpu_pool.CPUQueueFull):
            await cpu_pool.run_cpu_bound(None)
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        with pytest.raises(cpu_pool.CPUQueueFull):
            await cpu_pool.run_cpu_bound(None)
    finally:
        release.set()
        await b
    await asyncio.sleep(0)
    assert cpu_pool._inflight == 0


@pytest.mark.asyncio
async def test_readiness_times_out_without_blocking_forever():
    class SlowDB:
        async def execute(self, *args):
            await asyncio.sleep(30)
    async with asyncio.timeout(4):
        result = await health.readyz(SlowDB())
    assert result.status_code == 503


@pytest.mark.asyncio
async def test_transport_pins_checked_ip_and_preserves_host_tls_name(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
    seen = []
    async def no_network(self, request):
        seen.append(request)
        return httpx.Response(200, content=b'ok')
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_network)
    async with crawler.PublicTransport() as transport:
        await transport.handle_async_request(httpx.Request('GET', 'https://example.invalid/test'))
    assert isinstance(seen[0].stream, httpx.AsyncByteStream)
    assert seen[0].url.host == '93.184.216.34'
    assert seen[0].headers['host'] == 'example.invalid'
    assert seen[0].extensions['sni_hostname'] == 'example.invalid'


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['https://example.invalid:bad/x', 'https://example.invalid:99999/x'])
async def test_invalid_url_port_is_a_crawl_error(url):
    with pytest.raises(crawler.CrawlError, match='端口'):
        await crawler.assert_public_url(url)


def test_skeleton_bounds_deep_trees_and_long_selector_tokens():
    html = '<div>' * 3000 + 'text' + '</div>' * 3000
    assert len(crawler.to_skeleton(html).splitlines()) <= 33
    result = crawler.to_skeleton('<div id="' + 'a' * 100000 + '" class="' + 'b' * 100000 + '">text</div>')
    assert len(result) < 32000


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [None, {}, [], 3, ''])
async def test_llm_chat_contract_is_validated(content):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'choices': [{'message': {'content': content}}]}))
    with pytest.raises(LLMError):
        await LLMClient(api_key='test', transport=transport).chat('system', 'hello')


@pytest.mark.asyncio
@pytest.mark.parametrize('rows', [
    [{'index': 0, 'embedding': [1]}, {'index': 0, 'embedding': [2]}],
    [{'index': 0, 'embedding': [1]}, {'index': 1, 'embedding': [2, 3]}],
    [{'index': 0, 'embedding': ['x']}, {'index': 1, 'embedding': [2]}],
])
async def test_embedding_indices_dimensions_and_values_are_validated(rows):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'data': rows}))
    with pytest.raises(LLMError):
        await LLMClient(api_key='test', transport=transport).embeddings(['a', 'b'])


@pytest.mark.asyncio
async def test_trash_counts_towards_total_quota_and_explicit_purge_frees_it(client, monkeypatch):
    _, headers = await identity(client, 'quota_user')
    monkeypatch.setattr(settings, 'DIAGRAM_TOTAL_QUOTA', 1)
    data = {'name': 'one', 'content': '<mxfile/>'}
    first = await client.post('/diagrams', headers=headers, json=data)
    did = first.json()['id']
    await client.delete(f'/diagrams/{did}', headers=headers)
    assert (await client.post('/diagrams', headers=headers, json=data)).status_code == 409
    assert (await client.delete(f'/diagrams/{did}/purge', headers=headers)).status_code == 428
    assert (await client.delete(f'/diagrams/{did}/purge', headers={**headers, 'If-Match': first.headers['etag']})).status_code == 204
    assert (await client.post('/diagrams', headers=headers, json=data)).status_code == 201


@pytest.mark.asyncio
async def test_history_and_support_responses_are_private_and_not_cached(client):
    _, headers = await identity(client, 'private_user')
    for path in ['/shop/orders', '/support/messages']:
        result = await client.get(path, headers=headers)
        assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    assert (await client.get('/shop/orders', headers=headers)).json() == {'orders': [], 'next_cursor': None}


@pytest.mark.asyncio
async def test_pg_serializes_quota_and_message_retries(client, monkeypatch):
    if engine.dialect.name != 'postgresql':
        pytest.skip('Requires real PostgreSQL transactions; covered by CI PostgreSQL job')
    uid, headers = await identity(client, 'concurrent_user')
    monkeypatch.setattr(settings, 'DIAGRAM_QUOTA', 1)
    results = await asyncio.gather(*[client.post('/diagrams', headers=headers, json={'name': 'x', 'content': '<mxfile/>'}) for _ in range(4)])
    assert sorted(r.status_code for r in results) == [201, 409, 409, 409]
    data = message()
    results = await asyncio.gather(*[client.post('/support/messages', headers=headers, json=data) for _ in range(4)])
    assert all(r.status_code == 200 for r in results)
    assert len({r.json()['id'] for r in results}) == 1
    assert results[0].json()['customer_id'] == uid


@pytest.mark.asyncio
async def test_pg_legacy_schema_upgrade_preserves_customer_data(client):
    if engine.dialect.name != 'postgresql':
        pytest.skip('Migration SQL must run on PostgreSQL, not a SQLite imitation')
    uid, _ = await identity(client, 'migration_user')
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        await raw.execute('DROP TABLE support_message; ALTER TABLE oauth_code DROP COLUMN credential_version; ALTER TABLE sys_user DROP COLUMN credential_version;')
        for _ in range(2):
            for name in ['migrate_0007_support_messages.sql', 'migrate_0008_credential_revision.sql']:
                await raw.execute((ROOT / 'database init' / name).read_text())
    async with TestSession() as db:
        user = await db.get(User, uid)
        assert user.username == 'migration_user' and user.credential_version == 0


def test_callback_preserves_query_and_consent_is_bound_to_user():
    from app.routers.oauth import _callback, _sign
    assert _callback('https://example.invalid/cb?lang=zh', {'code': 'abc'}) == 'https://example.invalid/cb?lang=zh&code=abc'
    a, b = User(id=1, credential_version=0), User(id=2, credential_version=0)
    assert _sign('tools', 'https://example.invalid/cb', 'state', a) != _sign('tools', 'https://example.invalid/cb', 'state', b)


@pytest.mark.asyncio
async def test_semantic_cache_credentials_and_concurrent_replacement():
    from app.tools import faq
    started, release = asyncio.Event(), asyncio.Event()
    class Provider:
        api_key = 'test-account-a'
        async def embeddings(self, texts):
            if len(texts) == 1:
                started.set()
                await release.wait()
            return [[1.0, 0.0] for _ in texts]
    provider = Provider()
    faq.reset_semantic_index()
    try:
        assert await faq.warm_semantic_index(provider)
        key = faq._semantic_key(provider)
        task = asyncio.create_task(faq.semantic_search('test query', client=provider))
        await started.wait()
        provider.api_key = 'test-account-b'
        assert faq._semantic_key(provider) != key
        assert await faq.warm_semantic_index(provider)
        release.set()
        assert await task is None
    finally:
        release.set()
        faq.reset_semantic_index()


def test_literal_dot_identifier_is_not_a_schema_separator():
    graph = parse_ddl('CREATE TABLE "a.t" (id INT); CREATE TABLE a.t (id INT REFERENCES "a.t"(id)); CREATE TABLE b.t (id INT);')
    assert len({t['name'] for t in graph['tables']}) == 3
    assert graph['edges'][0]['from_table'] != graph['edges'][0]['to_table']


@pytest.mark.asyncio
async def test_article_storage_rejects_field_overflow(client):
    from app.tools.extract import ExtractError, ParsedArticle, save_article
    article = ParsedArticle(url='https://example.invalid/width', source_site='example.invalid', title='x'*301, content='body', author=None, published_at=None)
    async with TestSession() as db:
        with pytest.raises(ExtractError, match='title'):
            await save_article(db, article)


@pytest.mark.asyncio
async def test_postgres_concurrent_article_upsert(client):
    if engine.dialect.name != 'postgresql':
        pytest.skip('requires real PostgreSQL concurrent transactions')
    from app.tools.extract import ParsedArticle, save_article
    article = ParsedArticle(url='https://example.invalid/race', source_site='example.invalid', title='title', content='body', author=None, published_at=None)
    async def save():
        async with TestSession() as db:
            return (await save_article(db, article)).id
    ids = await asyncio.gather(save(), save())
    assert ids[0] == ids[1]
