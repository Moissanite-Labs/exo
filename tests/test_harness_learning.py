"""Disabled learning stays inert through the lazy feedback and panel handlers."""
from types import SimpleNamespace

import pytest

from exo.config import Config
from exo.harness import resolve
from exo.learn import handle_feedback, handle_ui_respond
from exo.learn.learner import get_learner
from exo.learn.runtime import load_settings
from exo.protocol import schema as s


def engine_for(tmp_path, template_store, harness, *, startup_plan=False):
    cfg = Config({'harness': harness, 'learn': {'enabled': True, 'store': str(template_store.root),
                                              'fast_loop': {'every_turns': 7}}}, tmp_path / 'home')
    cfg.save()
    engine = SimpleNamespace(home=cfg.home)
    if startup_plan:
        engine.harness_plan = resolve(cfg.data, env={})
    return engine, cfg


def assert_inert(engine, template_store):
    before = template_store.ledger_path.read_bytes() if template_store.ledger_path.exists() else None
    feedback = handle_feedback(engine, s.FeedbackSend(session='missing', verdict='accept'))
    panel = handle_ui_respond(engine, s.UiRespond(panel_id='missing', action='opened'))
    assert feedback['recorded'] is False and 'learning is off' in feedback['reason']
    assert panel['recorded'] is False and 'learning is off' in panel['reason']
    learner = get_learner(engine)
    assert learner.runtime is None
    after = template_store.ledger_path.read_bytes() if template_store.ledger_path.exists() else None
    assert after == before
    assert not (engine.home / 'keys' / 'exo-learn.key').exists()


@pytest.mark.parametrize('harness', [
    {'profile': 'lean'}, {'profile': 'coding'}, {'profile': 'full', 'disable': ['learning']},
])
def test_profile_blocks_lazy_learning_even_when_raw_setting_enables_it(tmp_path, template_store, monkeypatch, harness):
    monkeypatch.delenv('EXO_PROFILE', raising=False)
    engine, cfg = engine_for(tmp_path, template_store, harness)
    before = cfg.path.read_bytes()
    settings = load_settings(engine)
    assert settings['enabled'] is False
    assert settings['fast_loop']['every_turns'] == 7
    assert_inert(engine, template_store)
    assert cfg.path.read_bytes() == before


def test_environment_profile_restriction_applies_to_lazy_learning(tmp_path, template_store, monkeypatch):
    engine, _ = engine_for(tmp_path, template_store, {'profile': 'full'})
    monkeypatch.setenv('EXO_PROFILE', 'lean')
    assert_inert(engine, template_store)


@pytest.mark.parametrize('change', ['profile', 'remove_profile', 'environment'])
def test_running_profile_cannot_gain_learning_after_config_change(tmp_path, template_store, monkeypatch, change):
    monkeypatch.delenv('EXO_PROFILE', raising=False)
    engine, cfg = engine_for(tmp_path, template_store, {'profile': 'lean'}, startup_plan=True)
    if change == 'profile':
        cfg.set('harness.profile', 'full')
    elif change == 'remove_profile':
        cfg.unset('harness')
    else:
        monkeypatch.setenv('EXO_PROFILE', 'full')
    cfg.save()
    # The first learner does not exist until after this update. Its upper bound
    # comes from the composed engine rather than the newly edited config file.
    assert not hasattr(engine, '_learn')
    assert_inert(engine, template_store)


def test_legacy_and_full_still_honor_learning_settings(tmp_path, template_store, monkeypatch):
    monkeypatch.delenv('EXO_PROFILE', raising=False)
    engine, cfg = engine_for(tmp_path, template_store, {'profile': 'full'}, startup_plan=True)
    assert load_settings(engine)['enabled'] is True
    cfg.unset('harness')
    cfg.set('learn.enabled', False)
    cfg.save()
    assert load_settings(engine)['enabled'] is False
    assert_inert(engine, template_store)
