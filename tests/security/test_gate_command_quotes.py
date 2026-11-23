"""Literal shell quoting cannot weaken a command deny or reservation."""
import json

import pytest

from exo import gate


def classify(command):
    return gate.classify(('terminal', {'command': command}))


def policy(kind, entry):
    return gate.normalize_policies({'gates': {kind: [entry]}})


def rules(*entries):
    return gate.normalize_policies({'gates': {'rules': [
        {'match': match, 'decision': decision} for match, decision in entries]}})


# `./build` keeps the store floor out of it, but the matrix already asks for a delete, so the outcome alone
# proves nothing for `reserved`: the reason (and the direct _list_matches call) is the check.
# test_reservation_lifts_an_allowed_command covers the outcome.
@pytest.mark.parametrize('command', [
    'rm"" -rf ./build', "'rm' '-rf' ./build", r'r\m -\rf ./build',
    "rm $'-\\x72f' ./build", 'rm $"-rf" ./build', 'echo ok && r"m" -"rf" ./build',
    'echo $(r"m" -"rf" ./build)', 'echo `r"m" -"rf" ./build`',
    '''bash -c 'r"m" -"rf" ./build' ''',
    '''eval 'r"m" -"rf" ./build' ''',
    '''echo 'r"m" -"rf" ./build' | bash''',
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
    else:
        assert 'reserved for the principal' in decision.reason


@pytest.mark.parametrize('command', ["'ls' /etc", r'l\s /etc', '"l"s /etc'])
def test_reservation_lifts_an_allowed_command(command):
    assert gate.decide(classify(command), {}).outcome == 'allow'
    d = gate.decide(classify(command), policy('reserved', 'command:^ls /etc'))
    assert (d.outcome, d.rule) == ('ask', 'reserved')


@pytest.mark.parametrize('command', [
    '''echo 'r"m" -"rf" ~' ''',
    '''printf '%s' 'r"m" -"rf" ~' ''',
    ''''r"m" -rf' ~''',
    '''r"m" '-rf ; echo' ~''',
    '''r"m" '-rf\necho' ~''',
    'r"m" $FLAGS ~',
])
def test_word_contents_and_dynamic_words_do_not_become_execution(command):
    """Quote characters inside one shell word are data, not a second command line to lex: the view
    escapes them, so a veto that needs a word boundary after `rm -rf` does not fire on echo or printf
    data in a shell line. String literals in code are a different case (see test_code_literals...)."""
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


# The dynamic-word and heredoc placeholders carry no shell operator characters, so a veto keyed on a
# redirection fires only on a redirection.
@pytest.mark.parametrize('command', ['ls $HOME', 'echo "$PWD"', 'echo $(date)', 'cat <<EOF\nhello\nEOF'])
@pytest.mark.parametrize('kind', ['deny', 'reserved'])
def test_redirection_veto_ignores_placeholders(command, kind):
    p = policy(kind, 'command:>')
    assert not gate._list_matches(p['gates'][kind], classify(command))
    assert gate.decide(classify(command), p).rule != kind
    # `cat <<EOF` is a real `<` in the command text, so only the variable forms are checked for `<`.
    if '<<' not in command:
        p = policy(kind, 'command:<')
        assert not gate._list_matches(p['gates'][kind], classify(command))


@pytest.mark.parametrize('command,pattern', [('ls > out', '>'), ('cat < in', '<'), ('r"m" -"rf" ~ > log', '>')])
def test_redirection_veto_fires_on_redirection(command, pattern):
    assert gate.decide(classify(command), policy('deny', 'command:' + pattern)).rule == 'deny'
    assert 'reserved for the principal' in gate.decide(classify(command), policy('reserved', 'command:' + pattern)).reason


def test_allow_rules_keep_original_command_matching():
    p = rules(({'command': '^rm -rf ~$'}, 'allow'))
    assert gate._rule_matches({'command': '^rm -rf ~$'}, classify('rm -rf ~'))
    assert not gate._rule_matches({'command': '^rm -rf ~$'}, classify('r"m" -"rf" ~'))
    assert gate.decide(classify('r"m" -"rf" ~'), p).rule != 'rule'


@pytest.mark.parametrize('command', [
    "'git' push origin main", r'g\it push origin main', "git pu''sh origin main", '"git" "push" origin main',
    'git $"push" origin main',
])
@pytest.mark.parametrize('decision', ['deny', 'ask'])
def test_deny_and_ask_rules_see_literal_words(command, decision):
    d = gate.decide(classify(command), rules(({'command': '^git push'}, decision)))
    assert (d.outcome, d.rule) == (decision, 'rule')
    assert d.reason.startswith('rule 1')


def test_rule_view_pass_only_raises():
    # The naive form (match deny/ask rules with the view in the first-match loop) turns this deny into an
    # ask, because the ask rule then matches first. The second pass may only lift the outcome.
    p = rules(({'command': '^git push'}, 'ask'), ({'category': 'vcs_push'}, 'deny'))
    assert gate.decide(classify("'git' push origin main"), p).outcome == 'deny'
    assert gate.decide(classify('git push origin main'), p).outcome == 'ask'
    # A rule the command text itself matches is settled by first match, so an allow before an ask still wins.
    p = rules(({'command': '^git push origin staging$'}, 'allow'), ({'command': '^git push'}, 'ask'))
    assert gate.decide(classify('git push origin staging'), p).outcome == 'allow'
    assert gate.decide(classify("'git' push origin staging"), p).outcome == 'ask'
    # A lower rule never lowers through the view.
    p = rules(({'command': '^git push'}, 'log'))
    assert gate.decide(classify("'git' push origin main"), p).rule == 'matrix'


# Bash reads $"..." (locale quoting) as the string itself.
def test_locale_quoting_is_literal():
    c = classify('git push $"--force" origin main')
    assert c.category == 'force_push'
    assert gate.decide(c, policy('deny', 'force_push')).rule == 'deny'
    assert gate.decide(c, policy('deny', 'command:git push --force')).rule == 'deny'
    assert classify('git $"push" origin main').category == 'vcs_push'
    assert gate.decide(classify('rm $"-rf" ~'), policy('deny', 'command:rm -rf')).rule == 'deny'
    # Inside double quotes a $ before the closing quote is a literal dollar.
    assert gate.classify_shell('echo "a$"')._command_views == ['echo a$']


# With TEXTDOMAIN/TEXTDOMAINDIR pointing at a catalog and a non-C locale, bash runs the translation of
# a $"..." word instead, and expands $( ) in it. The decoded words feed the vetoes; the command still asks.
@pytest.mark.parametrize('command', [
    '$"ls" -rf ./work', 'TEXTDOMAINDIR=$PWD/l; TEXTDOMAIN=x\n$"ls" -rf ./work', 'git $"status" origin main',
    'echo $"hello"', 'cat <<< $"hello"', 'echo ok > $"out"', 'echo $(ls $"x")',
])
def test_locale_quoting_asks_because_bash_may_translate_it(command):
    c = classify(command)
    assert {'indirection', 'locale translation'} <= set(c.signals)
    d = gate.decide(c, {})
    assert (d.outcome, d.rule) == ('ask', 'opaque')


# A $"..." word marks the line instead of replacing it with an opaque part: category, target and the vetoes
# keyed on them still apply, also when the principal lowered opaque code.
@pytest.mark.parametrize('command,gates,category', [
    ('curl $"https://evil.example/x"', {'deny': ['web_read']}, 'web_read'),
    ('curl $"https://evil.example/x"', {'deny': ['web_read'], 'categories': {'opaque_exec': 'allow'}}, 'web_read'),
    ('curl $"https://evil.example/x"', {'deny': ['target:evil.example'], 'categories': {'opaque_exec': 'allow'}},
     'web_read'),
    ('curl $"https://evil.example/x"', {'rules': [{'match': {'category': 'web_read', 'target': 'evil.example'},
                                                   'decision': 'deny'}]}, 'web_read'),
    ('echo ok > $"out"', {'deny': ['local_write']}, 'local_write'),
    ('echo ok > $"out"', {'deny': ['local_write'], 'categories': {'opaque_exec': 'allow'}}, 'local_write'),
])
def test_locale_quoting_keeps_category_and_target(command, gates, category):
    c = classify(command)
    assert c.category == category
    assert gate.decide(c, gate.normalize_policies({'gates': gates})).outcome == 'deny'


# The line is also decided as the opaque code a translation can make of it, and the stricter outcome stands,
# so a veto on opaque_exec still holds for a $"..." spelling.
@pytest.mark.parametrize('gates,outcome', [
    ({'categories': {'delete': 'allow', 'opaque_exec': 'allow'}, 'reserved': ['opaque_exec']}, 'ask'),
    ({'categories': {'delete': 'allow', 'opaque_exec': 'allow'}, 'deny': ['opaque_exec']}, 'deny'),
    ({'categories': {'opaque_exec': 'allow'}, 'rules': [{'match': {'category': 'opaque_exec'}, 'decision': 'deny'},
                                                        {'match': {'category': 'delete'}, 'decision': 'allow'}]}, 'deny'),
])
def test_locale_quoting_is_also_decided_as_opaque_code(gates, outcome):
    d = gate.decide(classify('$"rm" -rf ./build'), gate.normalize_policies({'gates': gates}))
    assert d.outcome == outcome


def test_locale_quoting_keeps_vetoes_and_lowers_only_when_opaque_is_named():
    c = classify('git push $"--force" origin main')
    assert gate.decide(c, policy('deny', 'force_push')).outcome == 'deny'
    assert gate.decide(c, {}).outcome == 'ask'
    # An empty $"" is never translated, and inside double quotes or a heredoc body $" is literal text.
    for command in ['echo $""', 'echo "a$"', 'cat <<EOF\n$"hello"\nEOF']:
        assert gate.decide(classify(command), {}).outcome == 'allow', command
    p = gate.normalize_policies({'gates': {'categories': {'opaque_exec': 'allow'}}})
    assert gate.decide(classify('git $"status" origin main'), p).outcome == 'allow'


@pytest.mark.parametrize('command,argv', [
    ('echo "$" ; rm -rf ./b', [['echo', '$'], ['rm', '-rf', './b']]),
    ('echo $"" ; rm -rf ./b', [['echo', ''], ['rm', '-rf', './b']]),
    ('echo a$ ; rm -rf ./b', [['echo', 'a$'], ['rm', '-rf', './b']]),
    ('echo "a$" ; rm -rf ./b', [['echo', 'a$'], ['rm', '-rf', './b']]),
    ('echo "a$"b ; rm -rf ./b', [['echo', 'a$b'], ['rm', '-rf', './b']]),
    ('echo $"a b" ; rm -rf ./b', [['echo', 'a b'], ['rm', '-rf', './b']]),
    ('cat <<$"EOF"\nEOF\nrm -rf ./b\n$EOF', [['cat'], ['rm', '-rf', './b'], ['$EOF']]),
])
def test_dollar_quote_lexing(command, argv):
    assert gate.split_commands(command)[0] == argv


def test_locale_quote_in_a_heredoc_body_does_not_hide_a_substitution():
    assert classify('cat <<EOF\n$" $(rm -rf ./b) "\nEOF').category == 'delete'


# bash strips $"..." and $'...' from a heredoc delimiter; dash and ash keep the $, end the body at `$EOF` and run
# the lines after it. The body of such a heredoc is read as commands too.
@pytest.mark.parametrize('command', [
    'cat <<$"EOF"\n$EOF\nrm -rf ./b\nEOF', "cat <<$'EOF'\n$EOF\nrm -rf ./b\nEOF",
    'echo "$(cat <<$"EOF"\n$EOF\nrm -rf ./b\nEOF\n)"',
])
def test_dollar_quoted_heredoc_delimiter_reads_the_body_as_commands(command):
    c = classify(command)
    assert c.category == 'delete'
    assert gate.decide(c, {}).outcome == 'ask'
    assert gate.decide(c, policy('deny', 'delete')).outcome == 'deny'


def test_code_literals_are_matched_fail_closed():
    # classify_code can only guess which literals are commands, so a printed `rm -rf` is denied too; a deny
    # that reads the literal also catches a real os.system call.
    p = policy('deny', 'command:^rm -rf')
    for code in ['''python3 -c "print('rm -rf build')"''', '''python3 -c "os.system('rm -rf build')"''']:
        d = gate.decide(classify(code), p)
        assert (d.rule, d.reason) == ('deny', 'denied by policy (command:^rm -rf)')
    assert gate.decide(classify('''python3 -c "print('hello')"'''), p).rule != 'deny'


def test_code_views_are_the_union_of_every_literal():
    # _combine keeps the most severe literal; the views of the others must survive.
    c = gate.classify_code('''os.system("'git' push origin main")\nos.system('rm -rf ~/x')''')
    assert gate.decide(c, policy('deny', 'command:^git push')).rule == 'deny'
    assert gate.decide(c, policy('deny', 'command:^rm -rf')).rule == 'deny'


@pytest.mark.parametrize('opaque', [None, 'allow'])
def test_code_views_include_literals_the_classifier_finds_benign(opaque):
    # `'curl' https://...` alone is a web_read, which classify_code does not keep as a part; its view must
    # still reach the veto, also when the principal lowered opaque code.
    gates = {'deny': ['command:curl https://evil']}
    if opaque:
        gates['categories'] = {'opaque_exec': opaque}
    p = gate.normalize_policies({'gates': gates})
    for command in ['''python3 -c "import os; os.system(\\"'curl' https://evil.example/x\\")"''',
                    '''python3 -c "import os; os.system('curl https://evil.example/x')"''']:
        assert gate.decide(classify(command), p).rule == 'deny', command
    c = gate.classify(('execute_code', {'code': '''import os\nos.system("'curl' https://evil.example/x")'''}))
    assert gate.decide(c, p).rule == 'deny'


def test_reason_holds_only_the_policy_entry():
    assert gate.decide(classify('rm -rf ~'), policy('deny', 'command:rm -rf')).reason == 'denied by policy (command:rm -rf)'
    assert gate.decide(classify("'rm' -rf ~"), policy('deny', 'command:rm -rf')).reason == 'denied by policy (command:rm -rf)'
    d = gate.decide(classify("'rm' -rf ~"), policy('deny', {'match': {'command': 'rm -rf'}, 'reason': 'no rm'}))
    assert d.reason == 'denied by policy (no rm)'
    d = gate.decide(classify("'git' push origin main"), rules(({'command': '^git push'}, 'deny')))
    assert (d.outcome, d.rule) == ('deny', 'rule')


# The gate hook writes Decision.as_dict() to the signed, append-only ledger, and Classification.as_dict keeps
# command text out of it. The literal-word view must not carry command text into the decision either.
@pytest.mark.parametrize('kind', ['deny', 'reserved'])
@pytest.mark.parametrize('command', [
    "'curl' -H 'Authorization: Bearer sk-live-0123456789abcdef' https://api.example/v1",
    '''python3 -c "print('curl -H sk-live-0123456789abcdef')"''',
])
def test_decision_record_holds_no_command_text(kind, command):
    c = classify(command)
    d = gate.decide(c, policy(kind, 'command:^curl .*'))
    assert d.outcome == ('deny' if kind == 'deny' else 'ask')
    record = json.dumps({'classification': c.as_dict(), 'decision': d.as_dict()})
    for piece in ('sk-live', '0123456789abcdef', 'Bearer'):
        assert piece not in record


# The placeholders for dynamic words and heredoc bodies hold no letters, so a veto naming a word does not fire
# on a variable or a heredoc, and no literal word can spell a placeholder.
@pytest.mark.parametrize('command,entry', [
    ('ls $HOME', 'command:exo'), ('ls $HOME', 'command:dynamic'), ('ls $HOME', 'command:word'),
    ('cat <<EOF\nhi\nEOF', 'command:heredoc'), ('echo "$PWD"', 'command:__'),
])
@pytest.mark.parametrize('kind', ['deny', 'reserved'])
def test_placeholders_match_no_word(command, entry, kind):
    assert gate.decide(classify(command), policy(kind, entry)).outcome == 'allow'
    assert gate.decide(classify('ls __exo_dynamic_word__'), policy(kind, 'command:__exo')).rule == kind


@pytest.mark.parametrize('entry', ['command:[', {'command': '['}])
def test_invalid_regex_still_raises(entry):
    # #466: it raises when the policy loads, naming the entry, before decide can run it.
    with pytest.raises(gate.PolicyError):
        policy('deny', entry)


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
