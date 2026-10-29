import shutil
import subprocess

import pytest

from exo import gate


@pytest.mark.parametrize('operator,body,expected', [
    ('<<EF', 'E\\\nF', 'deny'),
    ('<<-EF', '\tE\\\nF', 'deny'),
    # Bash strips leading tabs after joining: the second physical tab remains internal.
    ('<<-EF', '\tE\\\n\tF', 'allow'),
    ('<<EF', 'E\\\\\nF', 'allow'),
    ("<<'EF'", 'E\\\nF', 'allow'),
    ('<<"EF"', 'E\\\nF', 'allow'),
    ('<<\\EF', 'E\\\nF', 'allow'),
])
def test_heredoc_delimiter_matches_bash_logical_line(operator, body, expected):
    command = 'cat ' + operator + '\n' + body + '\ngit push --force origin main\nEF'
    policy = gate.normalize_policies({'gates': {'deny': ['force_push']}})
    assert gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy).outcome == expected


@pytest.mark.skipif(not shutil.which("bash"), reason="bash required for inert parse control")
def test_inert_bash_confirms_join_and_tab_order():
    for body,executes in [('E\\\nF', True), ('\tE\\\nF', True), ('\tE\\\n\tF', False)]:
        # Only cat and printf run. The token printed by printf distinguishes body from execution.
        script = 'cat <<-EF\n' + body + '\nprintf "\\101FTER"\nEF\n'
        result = subprocess.run([shutil.which('bash'), '--norc', '-c', script], capture_output=True, text=True)
        assert ('AFTER' in result.stdout) is executes
