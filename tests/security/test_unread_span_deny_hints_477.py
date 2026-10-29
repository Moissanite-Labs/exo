import time

import pytest

from exo import gate


def nest(depth, command):
    return 'echo $(' * depth + command + ')' * depth


@pytest.mark.parametrize('depth', [17, 20, 100, 5000])
@pytest.mark.parametrize('command,category', [('rm -rf /srv/data', 'delete'),
                                              ('sudo rm -rf /srv/data', 'delete'),
                                              ('git push --force origin main', 'force_push')])
def test_unread_span_retains_direct_destructive_category_deny(depth, command, category):
    action = {'tool': 'bash', 'args': {'command': nest(depth, command)}}
    start = time.monotonic()
    result = gate.decide(gate.classify(action), gate.normalize_policies({'gates': {'deny': [category]}}))
    assert result.outcome == 'deny'
    assert time.monotonic() - start < 4


@pytest.mark.parametrize('prefix,suffix', [('PROMPT_COMMAND="', '"'), ("GIT_PAGER='", "' git log")])
def test_unread_hint_survives_environment_command_context(prefix, suffix):
    command = prefix + nest(100, 'rm -rf /srv/data') + suffix
    policy = gate.normalize_policies({'gates': {'deny': ['delete']}})
    assert gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy).outcome == 'deny'


def test_unrecognized_unread_text_keeps_existing_ask():
    command = nest(100, 'printf x')
    policy = gate.normalize_policies({'gates': {'deny': ['delete']}})
    assert gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy).outcome == 'ask'


def test_raw_category_word_cannot_deduplicate_the_internal_hint():
    command = 'echo $(delete); ' + nest(100, 'rm -rf /srv/data')
    policy = gate.normalize_policies({'gates': {'deny': ['delete']}})
    assert gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy).outcome == 'deny'
    assert gate._UnreadDenyHint('delete') != 'delete'


def test_original_whole_line_opaque_reserved_context_survives_candidate():
    command = nest(100, 'rm -rf /srv/data')
    c = gate.classify({'tool': 'bash', 'args': {'command': command}})
    policy = gate.normalize_policies({'gates': {'reserved': ['command:rm']}})
    assert ('opaque_exec', '') in gate.decide(c, policy).reserved
    policy = gate.normalize_policies({'gates': {'deny': [{'category': 'opaque_exec', 'command': 'rm'}]}})
    assert gate.decide(c, policy).outcome == 'deny'
