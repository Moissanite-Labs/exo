"""sudo chroot changes the executable and file namespace; these strings are never run."""
import pytest
from exo import gate


@pytest.mark.parametrize('option', ['-R /srv/root', '-R/srv/root', '-nR/srv/root', '-nR /srv/root',
                                    '--chroot=/srv/root', '--chroot /srv/root', '--chr=/srv/root', '--chr /srv/root'])
@pytest.mark.parametrize('prefix', ['sudo ', 'command sudo ', 'env sudo '])
def test_sudo_chroot_keeps_an_opaque_namespace_part(option, prefix):
    c = gate.classify({'tool': 'bash', 'args': {'command': prefix + option + ' printf hello'}})
    assert gate.decide(c, {'gates': {'deny': ['opaque_exec']}}).outcome == 'deny'


@pytest.mark.parametrize('option', ['-R /srv/root', '-R/srv/root', '--chroot=/srv/root'])
def test_sudo_chroot_preserves_the_nested_command_deny(option):
    c = gate.classify({'tool': 'bash', 'args': {'command': 'sudo ' + option + ' rm -rf /x'}})
    assert gate.decide(c, {'gates': {'deny': ['delete']}}).outcome == 'deny'


def test_sudo_user_option_value_is_not_a_chroot_flag():
    c = gate.classify({'tool': 'bash', 'args': {'command': 'sudo -uROOT printf hello'}})
    assert not any(p.summary.startswith('chroot') for p in gate._each_part(c))
