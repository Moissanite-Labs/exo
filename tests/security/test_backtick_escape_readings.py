import pytest

from exo import gate


@pytest.mark.parametrize('command,category', [
    ('echo `r\\\nm -rf /srv/data`', 'delete'),
    ('echo `git pu\\\nsh --force origin main`', 'force_push'),
    ('echo `echo \\#; git push --force origin main`', 'force_push'),
    ("echo `echo \\'; git push --force origin main; echo \\'`", 'force_push'),
    ('echo `echo \\"; git push --force origin main; echo \\"`', 'force_push'),
    ('echo "`echo \\\'; git push --force origin main; echo \\\'`"', 'force_push'),
    ('echo `echo \\#; cd ~/.exo/policies; cp /tmp/evil default.yaml`', 'store_write'),
])
def test_backtick_hidden_action_keeps_its_category_deny(command, category, tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('EXO_HOME', str(tmp_path / '.exo'))
    classification = gate.classify({'tool': 'bash', 'args': {'command': command}})
    policy = gate.normalize_policies({'gates': {'deny': [category], 'categories': {'opaque_exec': 'allow'}}})
    assert gate.decide(classification, policy).outcome == 'deny'


@pytest.mark.parametrize('double', [False, True])
def test_decode_preserves_escaped_non_specials_and_removes_continuations(double):
    decoded = gate._backtick_text('echo \\# \\' + "'" + ' c\\ p r\\\nm \\$', double)
    assert decoded == 'echo \\# \\' + "'" + ' c\\ p rm $'
    assert gate._backtick_text('echo \\"', double) == ('echo "' if double else 'echo \\"')


def test_plain_backtick_and_dollar_substitution_controls():
    policy = gate.normalize_policies({'gates': {'deny': ['delete']}})
    for command in ['echo `rm -rf /srv/data`', 'echo $(r\\\nm -rf /srv/data)']:
        assert gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy).outcome == 'deny'
