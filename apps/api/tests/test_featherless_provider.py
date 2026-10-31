import httpx
import pytest
from signalloop_api import ai_provider


def test_featherless_routes_both_models(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        content = '{"allowed": true, "tag": null}' if len(calls) == 1 else 'Check the boundary condition.'
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]}, request=httpx.Request('POST', url))
    monkeypatch.setattr(ai_provider.httpx, 'post', post)
    provider = ai_provider.FeatherlessProvider('test-only', 'generator', 'classifier')
    result = provider.evaluate('What is a boundary condition?', None, [])
    assert result.allowed
    assert result.message == 'Check the boundary condition.'
    assert [c[1]['json']['model'] for c in calls] == ['classifier', 'generator']
    assert all(c[0] == 'https://api.featherless.ai/v1/chat/completions' for c in calls)
    assert all('response_format' not in c[1]['json'] for c in calls)


def test_provider_prioritizes_featherless(monkeypatch):
    monkeypatch.setattr(ai_provider.settings, 'featherless_api_key', 'test-only')
    monkeypatch.setattr(ai_provider.settings, 'openai_api_key', 'other-test-only')
    assert isinstance(ai_provider.get_ai_provider(), ai_provider.FeatherlessProvider)


def test_error_does_not_echo_response_body(monkeypatch):
    def post(url, **kwargs):
        return httpx.Response(401, text='sensitive upstream body', request=httpx.Request('POST', url))
    monkeypatch.setattr(ai_provider.httpx, 'post', post)
    provider = ai_provider.FeatherlessProvider('test-only', 'generator', 'classifier')
    with pytest.raises(RuntimeError, match='Featherless API returned HTTP 401') as exc:
        provider._call_openai('system', 'user', 'generator', 20)
    assert 'sensitive' not in str(exc.value)


@pytest.fixture(autouse=True)
def isolated_usage_budget(monkeypatch,session_factory):
    monkeypatch.setattr('signalloop_api.portfolio_limits.SessionLocal',session_factory)
