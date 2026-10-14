"""Literal shell quoting cannot weaken a command deny or reservation."""
import re

import pytest

from exo import gate


def classify(command):
    return gate.classify(('terminal', {'command': command}))


def policy(kind, entry):
    return gate.normalize_policies({'gates': {kind: [entry]}})


@pytest.mark.parametrize('command', [
    'rm"" -rf ~', "'rm' '-rf' ~", r'r\m -\rf ~',
    "rm $'-\\x72f' ~", 'echo ok && r"m" -"rf" ~',
    'echo $(r"m" -"rf" ~)', 'echo `r"m" -"rf" ~`',
    '''bash -c 'r"m" -"rf" ~' ''',
    '''eval 'r"m" -"rf" ~' ''',
    '''echo 'r"m" -"rf" ~' | bash''',
])
@pytest.mark.parametrize('kind,outcome', [('deny', 'deny'), ('reserved', 'ask')])
@pytest.mark.parametrize('entry', ['command:rm -rf', {'match': {'command': 'rm -rf'}}])
def test_literal_quotes_cannot_bypass_veto(command, kind, outcome, entry):
    c = classify(command)
    p = policy(kind, entry)
    assert gate._list_matches(p['gates'][kind], c)
    decision = gate.decide(c, p)
    assert decision.outcome == outcome
    if kind == 'deny':
        assert decision.rule == kind


@pytest.mark.parametrize('command', [
    '''echo 'r"m" -"rf" ~' ''',
    '''printf '%s' 'r"m" -"rf" ~' ''',
    ''''r"m" -rf' ~''',
    '''r"m" '-rf ; echo' ~''',
    '''r"m" '-rf\necho' ~''',
    'r"m" $FLAGS ~',
])
def test_word_contents_and_dynamic_words_do_not_become_execution(command):
    decision = gate.decide(classify(command), policy('deny', r'command:rm -rf(?: |$|;)'))
    assert decision.rule != 'deny'


@pytest.mark.parametrize('command,pattern,hit', [
    ('r"m" -"rf" ~ && echo ok', r'rm -rf ~ && echo', True),
    ('r"m" -"rf" ~ || echo ok', r'rm -rf ~ && echo', False),
    ('r"m" -"rf" ~ > out', r'rm -rf ~ > out', True),
    ('r"m" -"rf" ~ | echo ok', r'rm -rf ~ > out', False),
    ('r"m" -"rf" ~', r'^rm -rf ~$' , True),
    ('r"m" -"rf" ~', r'^rm -rf /$', False),
])
def test_operator_and_target_sensitive_regex(command, pattern, hit):
    assert (gate.decide(classify(command), policy('deny', 'command:' + pattern)).rule == 'deny') is hit


def test_allow_rules_keep_original_command_matching():
    p = gate.normalize_policies({'gates': {'rules': [
        {'match': {'command': '^rm -rf ~$'}, 'decision': 'allow'},
    ]}})
    assert gate._rule_matches({'command': '^rm -rf ~$'}, classify('rm -rf ~'))
    assert not gate._rule_matches({'command': '^rm -rf ~$'}, classify('r"m" -"rf" ~'))
    assert gate.decide(classify('r"m" -"rf" ~'), p).rule != 'rule'


@pytest.mark.parametrize('entry', ['command:[', {'command': '['}])
def test_invalid_regex_still_raises(entry):
    with pytest.raises(re.error):
        gate.decide(classify('r"m" -"rf" ~'), policy('deny', entry))


def test_command_views_stay_out_of_ledger():
    c = classify('r"m" -"rf" private-secret')
    ledger = c.as_dict()
    assert '_command_views' not in ledger
    assert c.command not in repr(ledger)
    assert c._command_views[0] not in repr(ledger)


def test_recursive_veto_view_keeps_existing_depth_limit():
    # The classifier already bounds inspection; no separate recursive shell parser.
    c = gate.classify_shell('r"m" -"rf" ~', depth=gate._DEPTH)
    assert gate.decide(c, policy('deny', 'command:rm -rf')).rule == 'deny'
    c = gate.classify_shell('''bash -c 'r"m" -"rf" ~' ''', depth=gate._DEPTH)
    assert gate.decide(c, policy('deny', 'command:rm -rf')).rule != 'deny'
