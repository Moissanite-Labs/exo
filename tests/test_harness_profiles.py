"""Composition plans do no I/O and cannot disable a required dependency."""
import copy

import pytest

from exo.config import ConfigError
from exo.harness import CAPABILITIES, PROFILES, resolve


def test_legacy_configuration_is_preserved_without_opting_in():
    data = {'mcp': {'files': {'command': 'server'}}, 'learn': {'enabled': True},
            'channels': {'custom': {'seat': 'builder'}}, 'custom': {'unchanged': [1]}}
    plan = resolve(data, env={})
    assert plan.profile == 'legacy' and not plan.explicit
    assert plan.plugins is None and plan.settings == data
    assert not plan.enabled('instructions')
    plan.settings['custom']['unchanged'].append(2)
    assert data['custom']['unchanged'] == [1]
    assert resolve({'core_plugins': ['test-allow']}, env={}).plugins == ['test-allow']


@pytest.mark.parametrize('value,loaded', [(['exo-gate', ''], ['exo-gate']), ('exo-gate', []),
                                          ([None, 'exo-gate', 'exo-gate'], ['exo-gate'])])
def test_without_a_profile_core_plugins_is_read_as_main_reads_it(value, loaded):
    """Main's loader matches core_plugins as a set of names and refuses nothing. Without a profile the plan does
    the same, so a config that starts on main still starts. A profile, saved or from EXO_PROFILE, checks it."""
    plan = resolve({'core_plugins': value}, env={})
    assert not plan.explicit and plan.plugins == loaded and plan.settings['core_plugins'] == value
    for data, env in (({'core_plugins': value, 'harness': {'profile': 'lean'}}, {}),
                      ({'core_plugins': value}, {'EXO_PROFILE': 'lean'})):
        with pytest.raises(ConfigError, match='core_plugins must be a list of nonempty names'):
            resolve(data, env=env)


@pytest.mark.parametrize('profile', PROFILES)
def test_profiles_have_stable_capability_and_plugin_order(profile):
    data = {'harness': {'profile': profile}}
    first = resolve(data, env={})
    assert first.capabilities == PROFILES[profile]
    assert first.plugins[0] == 'exo-gate'
    assert first.explicit
    assert resolve(first.settings, env={}) == first
    assert data == {'harness': {'profile': profile}}


def test_lean_restricts_services_without_erasing_user_configuration():
    data = {'harness': {'profile': 'lean'}, 'models': {'roles': {'main': ['fake:echo']}},
            'peers': {'wake': True, 'policy': {'alice': 'hold'}},
            'jobs': {'max_concurrent': 2}, 'mcp': {'srv': {'command': 'server'}},
            'channels': {'bus': {}}, 'learn': {'threshold': 0.9}}
    before = copy.deepcopy(data)
    plan = resolve(data, env={})
    assert plan.capabilities == ('gate',)
    assert plan.plugins == ['exo-gate']
    for key in ('jobs', 'delegation', 'peers', 'learn'):
        assert plan.settings[key]['enabled'] is False
    assert plan.settings['jobs']['max_concurrent'] == 2
    assert plan.settings['peers']['policy'] == {'alice': 'hold'}
    assert plan.settings['schedule'] is False
    assert plan.settings['mcp'] == plan.settings['channels'] == {}
    assert plan.settings['models'] == data['models'] and data == before


def test_profile_keeps_custom_plugins_and_controls_builtin_selection():
    plan = resolve({'harness': {'profile': 'lean'},
                    'core_plugins': ['my-tools', 'exo-memory', 'my-tools', 'exo-gate']}, env={})
    assert plan.plugins == ['exo-gate', 'my-tools']
    assert resolve({'harness': {'profile': 'lean'}, 'core_plugins': []}, env={}).plugins == ['exo-gate']


def test_explicit_enable_adds_transitive_dependencies_and_disable_wins_over_preset():
    plan = resolve({'harness': {'profile': 'lean', 'enable': ['orchestra', 'learning']}}, env={})
    assert plan.capabilities == ('gate', 'memory', 'skills', 'delegation', 'learning', 'orchestra')
    assert plan.plugins == ['exo-gate', 'exo-memory', 'exo-orchestra']
    plan = resolve({'harness': {'profile': 'coding', 'disable': ['mcp', 'jobs']}}, env={})
    assert not plan.enabled('mcp') and not plan.enabled('jobs')


@pytest.mark.parametrize('harness,match', [
    ({'profile': 'coding', 'disable': ['memory']}, 'context -> memory'),
    ({'profile': 'lean', 'enable': ['orchestra'], 'disable': ['skills']}, 'orchestra -> skills'),
    ({'profile': 'full', 'disable': ['gate']}, 'gate is required'),
    ({'enable': ['mcp'], 'disable': ['mcp']}, 'both enabled and disabled'),
    ({'profile': 'typo'}, 'must be lean, coding or full'),
    ({'profile': []}, 'must be lean, coding or full'),
    ({'enable': 'jobs'}, 'must be a list'),
    ({'disable': [1]}, 'must be a list'),
    ({'enable': ['typo']}, 'unknown capabilities'),
    ({'enabled': ['jobs']}, 'unknown harness settings'),
    (None, 'harness must be an object'),
])
def test_invalid_plans_fail_before_composition(harness, match):
    with pytest.raises(ConfigError, match=match):
        resolve({'harness': harness}, env={})


def test_environment_overrides_profile_but_not_capability_overrides():
    data = {'harness': {'profile': 'full', 'enable': ['skills'], 'disable': ['mcp']}}
    plan = resolve(data, env={'EXO_PROFILE': 'lean'})
    assert plan.profile == 'lean' and plan.capabilities == ('gate', 'skills')
    assert data['harness']['profile'] == 'full'
    assert resolve({}, env={'EXO_PROFILE': 'coding'}).profile == 'coding'
    with pytest.raises(ConfigError, match='^EXO_PROFILE must be lean'):  # the error names where the value came from
        resolve({'harness': {'profile': 'full'}}, env={'EXO_PROFILE': 'oops'})
    with pytest.raises(ConfigError, match='^harness.profile must be lean'):
        resolve({'harness': {'profile': 'oops'}}, env={})


def test_summary_never_contains_subsystem_or_provider_settings():
    plan = resolve({'harness': {'profile': 'full'},
                    'models': {'api_key': 'sensitive'}, 'plugin_config': {'x': {'password': 'private'}}}, env={})
    summary = plan.summary()
    assert set(summary) == {'profile', 'explicit', 'capabilities', 'disabled', 'plugins', 'details', 'note'}
    assert summary['capabilities'] == list(CAPABILITIES)
    assert 'sensitive' not in str(summary) and 'private' not in str(summary)
    with pytest.raises(ConfigError, match='unknown harness capability'):
        plan.enabled('typo')


def test_summary_explains_selection_and_subsystem_disables():
    plan = resolve({'harness': {'profile': 'lean', 'enable': ['orchestra'], 'disable': ['mcp']},
                    'delegation': {'enabled': False}}, env={})
    detail = plan.summary()['details']
    assert detail['gate']['reason'] == 'profile:lean'
    assert detail['orchestra']['reason'] == 'explicit enable'
    assert detail['skills']['reason'] == 'dependency:orchestra'
    assert detail['mcp']['reason'] == 'disabled:explicit'
    assert detail['memory']['reason'] == 'default:off'
    assert detail['delegation']['selected'] and detail['delegation']['setting_disabled']
