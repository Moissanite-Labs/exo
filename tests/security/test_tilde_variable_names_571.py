import shutil
import subprocess

import pytest

from exo import gate


@pytest.mark.parametrize('command', ['unset ~-', 'read ~- <<< /tmp', 'printf -v ~- /tmp',
                                     'declare -n r=~-; r=/tmp'])
def test_tilde_variable_name_cannot_hide_path_assignment(command):
    c = gate.classify({'tool': 'bash', 'args': {'command': 'OLDPWD=PATH; ' + command + '; ls'}})
    assert c.category == 'opaque_exec'
    assert gate.decide(c, gate.normalize_policies({})).outcome == 'ask'


@pytest.mark.parametrize('command', ["unset '~-'; ls", "read '~-'; ls", "printf -v '~-'; ls",
                                     "declare -n r='~-'; r=/tmp; ls"])
def test_quoted_tilde_is_not_expanded_to_path(command):
    c = gate.classify({'tool': 'bash', 'args': {'command': 'OLDPWD=PATH; ' + command}})
    assert gate.decide(c, gate.normalize_policies({})).outcome == 'allow'


@pytest.mark.skipif(not shutil.which('bash'), reason='inert Bash control')
def test_bash_unset_tilde_can_really_name_path():
    result = subprocess.run([shutil.which('bash'), '--norc', '-c',
                             'OLDPWD=PATH; unset ~-; printf "%s" "${PATH+still_set}"'],
                            env={'PATH': '/bin'}, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == ''
