"""Offline tests of the opt-in live probe; no real provider or user key is used."""
import asyncio
import json

import httpx
import pytest

from app.tools.llm import LLMClient
from scripts import probe_llm as probe


@pytest.fixture
def configured(monkeypatch):
    for name, value in {
        'LLM_API_KEY': 'test-only-key', 'LLM_BASE_URL': 'https://provider.invalid/v1',
        'LLM_MODEL': 'test-chat', 'LLM_EMBED_MODEL': 'test-embedding',
    }.items():
        monkeypatch.setenv(name, value)
    settings_class = probe.Settings
    monkeypatch.setattr(probe, 'Settings', lambda **kw: settings_class(_env_file=None))


@pytest.mark.parametrize('url', ['http://example.com/v1', 'https://u:p@example.com/v1',
                                 'https://example.com/v1?key=hidden', 'https://example.com/v1#fragment',
                                 'https://example.com:bad/v1', ''])
def test_base_url_rejected_before_request(configured, monkeypatch, url):
    monkeypatch.setenv('LLM_BASE_URL', url)
    with pytest.raises(ValueError):
        probe.configured_client('models')


@pytest.mark.parametrize('mode,field', [('models', 'LLM_API_KEY'), ('chat', 'LLM_MODEL'),
                                      ('mermaid', 'LLM_MODEL'), ('embeddings', 'LLM_EMBED_MODEL')])
def test_missing_configuration_is_nonzero_without_secret_output(configured, monkeypatch, capsys, mode, field):
    monkeypatch.setenv(field, '')
    assert probe.main([mode]) == 1
    output = capsys.readouterr()
    assert 'PROBE FAILED' in output.err
    assert 'test-only-key' not in output.out + output.err


@pytest.mark.parametrize('mode', ['models', 'chat', 'mermaid', 'embeddings'])
def test_modes_call_expected_endpoint_once(configured, mode, capsys):
    requests = []

    def handle(request):
        requests.append(request)
        if mode == 'models':
            data = {'data': [{'id': 'test-chat'}]}
        elif mode == 'embeddings':
            data = {'data': [{'index': 1, 'embedding': [0.0, 1.0]}, {'index': 0, 'embedding': [1.0, 0.0]}]}
        else:
            data = {'choices': [{'message': {'content': 'classDiagram\nclass Customer\nclass Order'}}]}
        return httpx.Response(200, json=data)

    client = probe.configured_client(mode)
    client.transport = httpx.MockTransport(handle)
    asyncio.run(probe.probe(mode, client))
    assert len(requests) == 1
    request = requests[0]
    suffix = 'models' if mode == 'models' else 'embeddings' if mode == 'embeddings' else 'chat/completions'
    assert str(request.url) == 'https://provider.invalid/v1/' + suffix
    assert request.headers['authorization'] == 'Bearer test-only-key'
    assert request.method == ('GET' if mode == 'models' else 'POST')
    if mode != 'models':
        body = json.loads(request.content)
        assert body['model'] == ('test-embedding' if mode == 'embeddings' else 'test-chat')
        if mode == 'embeddings':
            assert body['input'] == ['customer order', 'customer purchase']
        else:
            expected = 'Reply with OK.' if mode == 'chat' else 'A customer has many orders. Draw two classes and their relationship.'
            assert body['messages'][-1]['content'] == expected
    output = capsys.readouterr().out
    assert 'test-only-key' not in output
    assert ('MODEL IDS' if mode == 'models' else 'PASS') in output


@pytest.mark.parametrize('payload', [[], {}, {'data': []}, {'data': [None]}, {'data': [{'id': ''}]}])
def test_malformed_models_are_not_passed_off_as_success(configured, payload):
    client = probe.configured_client('models')
    client.transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ValueError):
        asyncio.run(probe.probe('models', client))


def test_models_do_not_forward_credentials_to_redirect(configured):
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={'Location': 'https://other.invalid/models'})

    client = probe.configured_client('models')
    client.transport = httpx.MockTransport(redirect)
    with pytest.raises(ValueError, match='HTTP 302'):
        asyncio.run(probe.probe('models', client))
    assert len(requests) == 1


def test_failure_exit_does_not_print_server_echo_of_key(configured, monkeypatch, capsys):
    client = probe.configured_client('chat')
    client.transport = httpx.MockTransport(lambda request: httpx.Response(401, text='test-only-key'))
    monkeypatch.setattr(probe, 'configured_client', lambda mode: client)
    assert probe.main(['chat']) == 1
    captured = capsys.readouterr()
    assert 'test-only-key' not in captured.err + captured.out
    assert 'PROBE FAILED' in captured.err


def test_untrusted_model_ids_are_redacted_and_terminal_safe(configured, capsys):
    client = probe.configured_client('models')
    client.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'data': [{'id': 'test-only-key\x1b[31m\nspoof'}],
    }))
    asyncio.run(probe.probe('models', client))
    text = capsys.readouterr().out
    assert 'test-only-key' not in text and '\x1b' not in text
    assert '[REDACTED]' in text and '\\x1b' in text


def test_help_is_offline_and_no_default_network_action():
    with pytest.raises(SystemExit) as result:
        probe.main(['--help'])
    assert result.value.code == 0


def test_non_mermaid_content_and_invalid_vectors_fail():
    for mode, payload in [
        ('mermaid', {'choices': [{'message': {'content': 'plain prose'}}]}),
        ('embeddings', {'data': [{'index': 0, 'embedding': []}, {'index': 1, 'embedding': []}]}),
    ]:
        client = LLMClient(api_key='test-only-key', transport=httpx.MockTransport(
            lambda request, data=payload: httpx.Response(200, json=data)))
        with pytest.raises(probe.LLMError):
            asyncio.run(probe.probe(mode, client))


@pytest.mark.parametrize('mode,field', [('models', 'LLM_BASE_URL'), ('chat', 'LLM_MODEL'),
                                      ('embeddings', 'LLM_EMBED_MODEL')])
def test_implicit_defaults_do_not_send_key_to_unselected_provider(configured, monkeypatch, mode, field):
    monkeypatch.delenv(field)
    with pytest.raises(ValueError):
        probe.configured_client(mode)


def test_private_root_env_and_process_precedence(tmp_path, monkeypatch):
    for name in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'LLM_EMBED_MODEL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    (tmp_path / '.env').write_text('LLM_API_KEY=test-only-key\nLLM_BASE_URL=https://provider.invalid/v1\nLLM_MODEL=from-file\n')
    assert probe.configured_client('chat').model == 'from-file'
    monkeypatch.setenv('LLM_MODEL', 'from-process')
    assert probe.configured_client('chat').model == 'from-process'
