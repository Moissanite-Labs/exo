"""Offline estimates use route metadata; discovery alone does not supply a price."""
import pytest

from exo.core.models.router import Route, Router
from exo.core.types import Usage


@pytest.mark.parametrize('model,expected', [('claude-fable-5-2', 58.2), ('claude-sonnet-5-5', 14.6)])
def test_current_anthropic_rates_include_uncached_output_and_cache_tokens(model, expected):
    router = Router(env={}, key_sources=False)
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000,
                  cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    priced = router.price(Route('anthropic', model), usage)
    assert priced.cost_usd == pytest.approx(expected)
    assert usage.cost_usd is None  # Estimation does not rewrite saved historical usage.
    assert (priced.input_tokens, priced.output_tokens, priced.cache_read_tokens, priced.cache_write_tokens) == (
        1_000_000, 1_000_000, 1_000_000, 1_000_000)


@pytest.mark.parametrize('field,expected', [('input_tokens', 8), ('output_tokens', 40),
                                         ('cache_read_tokens', .2), ('cache_write_tokens', 10)])
def test_fable_52_prices_each_usage_component_at_its_own_rate(field, expected):
    priced = Router(env={}, key_sources=False).price(Route('anthropic', 'claude-fable-5-2'),
                                                   Usage(**{field: 1_000_000}))
    assert priced.cost_usd == pytest.approx(expected)


def test_config_metadata_merges_over_fable_52_prices_without_changing_selected_model():
    ref = 'anthropic:claude-fable-5-2'
    config = {'roles': {'main': [ref]}, 'models': {ref: {'context': 900_000, 'cache_read': .3}}}
    router = Router.from_config(config, env={}, key_sources=False)
    assert router.roles['main'] == [ref]
    assert router.models[ref]['context'] == 900_000
    priced = router.price(Route.parse(ref), Usage(input_tokens=1_000_000, output_tokens=1_000_000,
                                                cache_read_tokens=1_000_000, cache_write_tokens=1_000_000))
    assert priced.cost_usd == pytest.approx(58.3)
    assert config['models'][ref] == {'context': 900_000, 'cache_read': .3}


@pytest.mark.parametrize('ref', ['anthropic:unknown-model', 'custom:claude-fable-5-2'])
def test_models_without_route_pricing_stay_unknown(ref):
    usage = Usage(input_tokens=100, cache_read_tokens=1_000_000)
    assert Router(env={}, key_sources=False).price(Route.parse(ref), usage) is usage
    assert usage.cost_usd is None


def test_provider_reported_cost_is_not_replaced_by_catalog_estimate():
    usage = Usage(cache_read_tokens=1_000_000, cost_usd=.123)
    assert Router(env={}, key_sources=False).price(Route('anthropic', 'claude-fable-5-2'), usage) is usage
