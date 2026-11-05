"""Anthropic discovery feeds the same cached catalog used by model selectors."""
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from exo.core.models.anthropic import API_VERSION
from exo.core.models.router import Router
from exo.models_cmd import CACHE_TTL, available_models, remote_models

KEY = 'synthetic-anthropic-discovery-key'


def router(**overrides):
    spec = {'transport': 'anthropic', 'base_url': 'https://api.anthropic.com',
            'key_env': 'ANTHROPIC_API_KEY', **overrides}
    return Router(catalog={'providers': {'anthropic': spec}},
                  env={'ANTHROPIC_API_KEY': KEY}, key_sources=False)


def response(url, data, status=200):
    return httpx.Response(status, json=data, request=httpx.Request('GET', url))


def test_remote_and_shared_catalog_paginate_and_reuse_cache(tmp_path):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs['headers'] == {'x-api-key': KEY, 'anthropic-version': API_VERSION}
        if len(calls) == 1:
            return response(url, {'data': [{'id': 'claude-second'}], 'has_more': True,
                                  'last_id': 'cursor/with?chars', 'next': 'https://foreign.test/steal'})
        assert urlsplit(url).netloc == 'api.anthropic.com'
        assert parse_qs(urlsplit(url).query) == {'after_id': ['cursor/with?chars']}
        return response(url, {'data': [{'id': 'claude-first'}], 'has_more': False})
    r = router()
    expected = [{'route': 'anthropic:claude-first', 'provider': 'anthropic'},
                {'route': 'anthropic:claude-second', 'provider': 'anthropic'}]
    assert available_models(r, tmp_path, get=get, now=100) == expected
    assert calls[0] == 'https://api.anthropic.com/v1/models'
    assert available_models(r, tmp_path, get=get, now=101) == expected
    assert len(calls) == 2
    saved = (tmp_path / 'cache/models/anthropic.json').read_text()
    assert KEY not in saved
    def offline(*args, **kwargs):
        raise ConnectionError(KEY)
    assert available_models(r, tmp_path, get=offline, now=101 + CACHE_TTL) == expected


def test_remote_models_uses_native_endpoint():
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(url, {'data': [{'id': 'from-api'}]})
    assert remote_models(router(), 'anthropic', get=get) == ['from-api']
    assert calls == ['https://api.anthropic.com/v1/models']


@pytest.mark.parametrize('status', [401, 403])
def test_invalid_credentials_do_not_expose_secret_or_invent_models(tmp_path, status):
    def get(url, **kwargs):
        return response(url, {'error': {'message': KEY}}, status)
    with pytest.raises(ValueError) as exc:
        remote_models(router(), 'anthropic', get=get)
    assert KEY not in str(exc.value)
    assert available_models(router(), tmp_path, get=get) == []
    assert not (tmp_path / 'cache/models/anthropic.json').exists()


@pytest.mark.parametrize('last_id', [None, '', 'repeated'])
def test_invalid_or_repeating_cursor_fails_without_partial_cache(tmp_path, last_id):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return response(url, {'data': [{'id': 'partial'}], 'has_more': True, 'last_id': last_id})
    assert available_models(router(), tmp_path, get=get) == []
    assert len(calls) <= 2
    assert not (tmp_path / 'cache/models/anthropic.json').exists()


def test_hidden_models_and_role_restrictions_remain_in_effect(tmp_path):
    def get(url, **kwargs):
        return response(url, {'data': [{'id': 'visible'}, {'id': 'hidden-model'}]})
    assert available_models(router(hide_models=['hidden']), tmp_path, get=get) == [
        {'route': 'anthropic:visible', 'provider': 'anthropic'}]
    def forbidden(*args, **kwargs):
        pytest.fail('restricted provider must not be queried')
    assert available_models(router(roles=['aux']), tmp_path, get=forbidden) == []
