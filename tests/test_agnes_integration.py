"""Agnes defaults, explicit embedding opt-in, and safe network diagnostics (offline)."""
import asyncio
import json

import httpx
import pytest
from dotenv import dotenv_values

from app.config import Settings
from app.tools import faq
from app.tools.llm import LLMClient, LLMError
from scripts import probe_llm


@pytest.fixture
def clean_config(monkeypatch):
    for key in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY', 'LLM_EMBED_MODEL', 'LLM_EMBED_ENABLED'):
        monkeypatch.delenv(key, raising=False)
    original = probe_llm.Settings
    monkeypatch.setattr(probe_llm, 'Settings', lambda **kw: original(_env_file=None))


def test_agnes_defaults_match_template_and_client(clean_config):
    config = Settings(_env_file=None)
    template = dotenv_values('.env.example')
    client = LLMClient()
    assert config.LLM_BASE_URL == client.base_url == template['LLM_BASE_URL'] == 'https://apihub.agnes-ai.com/v1'
    assert config.LLM_MODEL == client.model == template['LLM_MODEL'] == 'agnes-2.5-flash'
    assert not config.LLM_EMBED_ENABLED and template['LLM_EMBED_ENABLED'] == 'false'
    assert config.LLM_EMBED_MODEL == client.embed_model == template['LLM_EMBED_MODEL'] == ''


async def test_agnes_chat_protocol_non_streaming_single_request():
    seen = []

    def handle(request):
        seen.append(request)
        assert str(request.url) == 'https://apihub.agnes-ai.com/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-only-key'
        body = json.loads(request.content)
        assert body['model'] == 'agnes-2.5-flash'
        assert body['stream'] is False and body['temperature'] == 0.2
        assert body['messages'] == [{'role': 'system', 'content': 'test system'}, {'role': 'user', 'content': 'OK?'}]
        return httpx.Response(200, json={'choices': [{'message': {'content': 'OK'}}]})

    client = LLMClient(api_key='test-only-key', transport=httpx.MockTransport(handle))
    assert await client.chat('test system', 'OK?') == 'OK'
    assert len(seen) == 1  # No model-list prerequisite, retry, or paid-model fallback.


@pytest.mark.parametrize('status', [400, 401, 402, 403, 404, 429, 500])
@pytest.mark.parametrize('mode', ['chat', 'embeddings'])
async def test_http_error_preserves_status_without_echoing_secret(status, mode):
    client = LLMClient(api_key='test-only-key', embed_model='test-embedding', transport=httpx.MockTransport(
        lambda request: httpx.Response(status, text='upstream echoed test-only-key')))
    with pytest.raises(LLMError) as result:
        if mode == 'chat':
            await client.chat('system', 'OK')
        else:
            await client.embeddings(['test'])
    assert result.value.category == 'http' and result.value.status_code == status
    assert str(status) in str(result.value) and 'test-only-key' not in str(result.value)


async def test_network_failure_is_not_reported_as_http_or_invalid_key():
    def disconnect(request):
        raise httpx.ConnectError('TLS EOF; unexpected echo test-only-key')

    client = LLMClient(api_key='test-only-key', transport=httpx.MockTransport(disconnect))
    with pytest.raises(LLMError) as result:
        await client.chat('system', 'OK')
    assert result.value.category == 'network' and result.value.status_code is None
    assert isinstance(result.value.__cause__, httpx.ConnectError)
    assert 'test-only-key' not in str(result.value)


async def test_disabled_embedding_makes_no_warmup_or_query_request(monkeypatch):
    monkeypatch.setattr(faq.settings, 'LLM_EMBED_ENABLED', False)

    class Forbidden:
        async def embeddings(self, texts):
            raise AssertionError('Disabled feature must not access external API')

    assert not await faq.warm_semantic_index(Forbidden())
    assert not faq.semantic_ready()
    assert await faq.semantic_search('test', client=Forbidden()) is None


async def test_disabling_embedding_invalidates_cached_results(monkeypatch):
    monkeypatch.setattr(faq.settings, 'LLM_EMBED_ENABLED', True)
    seen = []

    class Provider:
        async def embeddings(self, texts):
            seen.append(texts)
            return [[1.0, 0.0] for _ in texts]

    try:
        client = Provider()
        assert await faq.warm_semantic_index(client)
        assert faq.semantic_ready()
        monkeypatch.setattr(faq.settings, 'LLM_EMBED_ENABLED', False)
        assert not faq.semantic_ready()
        assert await faq.semantic_search('test', client=client) is None
        assert not await faq.warm_semantic_index(client)
        assert faq._SEMANTIC is None and len(seen) == 1
    finally:
        faq.reset_semantic_index()


async def test_missing_embedding_model_is_rejected_before_network():
    def forbidden(request):
        raise AssertionError('Missing model must not send an empty ID')

    client = LLMClient(api_key='test-only-key', transport=httpx.MockTransport(forbidden))
    with pytest.raises(LLMError, match='LLM_EMBED_MODEL'):
        await client.embeddings(['test'])


@pytest.mark.parametrize('status', [200, 401, 403, 404, 503])
def test_connectivity_does_not_send_key_or_claim_model_success(clean_config, monkeypatch, capsys, status):
    monkeypatch.setenv('LLM_API_KEY', 'test-only-key')
    client = probe_llm.configured_client('connectivity')
    assert not client.api_key

    def handle(request):
        assert 'authorization' not in request.headers
        return httpx.Response(status, text='do not print this response body')

    client.transport = httpx.MockTransport(handle)
    asyncio.run(probe_llm.probe('connectivity', client))
    out = capsys.readouterr().out
    assert f'HTTP REACHED: {status}' in out and 'not an authentication/model test' in out
    assert 'test-only-key' not in out and 'do not print this response body' not in out


def test_embedding_probe_requires_explicit_opt_in(clean_config, monkeypatch):
    for key, value in {'LLM_API_KEY': 'test-only-key', 'LLM_BASE_URL': 'https://provider.invalid/v1',
                       'LLM_EMBED_MODEL': 'test-embedding'}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match='LLM_EMBED_ENABLED'):
        probe_llm.configured_client('embeddings')


def test_cli_reports_http_status_without_error_body(monkeypatch, capsys):
    client = LLMClient(api_key='test-only-key', transport=httpx.MockTransport(
        lambda request: httpx.Response(429, text='test-only-key')))
    monkeypatch.setattr(probe_llm, 'configured_client', lambda mode: client)
    assert probe_llm.main(['chat']) == 1
    text = capsys.readouterr().err
    assert 'HTTP 429' in text and 'test-only-key' not in text


def test_live_workflow_requires_manual_opt_in_and_scopes_secret():
    from pathlib import Path

    import yaml
    config = yaml.load(Path('.github/workflows/agnes-connectivity.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    assert config['on']['push']['paths'] == ['.github/workflows/agnes-connectivity.yml']
    assert config['on']['push']['branches'] == ['arena/01a0bf7a-codemax-platform']  # 本会话分支（TD-270）
    assert 'pull_request' not in config['on']
    live = config['jobs']['live-chat']
    assert live['if'] == "(github.event_name == 'workflow_dispatch' && inputs.live_chat) || (github.event_name == 'push' && contains(github.event.head_commit.message, '[agnes-live-test]'))"
    assert config['on']['workflow_dispatch']['inputs']['live_chat']['default'] == 'false'
    assert all('AGNES_API_KEY' not in str(step) for step in live['steps'][:-1])
    assert live['steps'][-1]['env']['LLM_API_KEY'] == '${{ secrets.AGNES_API_KEY }}'
    assert live['steps'][-1]['env']['LLM_EMBED_ENABLED'] == 'false'
    assert 'AGNES_API_KEY' not in str(config['jobs']['connectivity'])
    run = live['steps'][-1]['run']
    for mode in ('models', 'chat', 'mermaid'):
        assert run.count(f'run_probe {mode} python scripts/probe_llm.py {mode}') == 1
    assert 'run_probe embedding env LLM_EMBED_ENABLED=true LLM_EMBED_MODEL=agnes-2.5-flash python scripts/probe_llm.py embeddings' in run
    assert 'sys.stdin.read()[:4000]' in run
    assert "replace('%', '%25')" in run and "'%0D'" in run and "'%0A'" in run
    assert 'return "$result"' in run
    assert 'test "$chat" -eq 0 && test "$mermaid" -eq 0' in run
