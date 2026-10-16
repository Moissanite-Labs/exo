"""Cron reads stay safe; mutations require a one-operation approval."""
from exo import gate


def test_crontab_list_is_read_but_install_is_opaque_and_exactly_bound():
    read = gate.classify_shell("crontab -l")
    install = gate.classify_shell("crontab /tmp/new-crontab")
    assert read.category == "read"
    assert gate.decide(read).outcome == "allow"
    assert install.category == "opaque_exec"
    assert gate.decide(install).outcome == "ask"
    assert gate.binds_exact(install)
    assert install.command == "crontab /tmp/new-crontab"


def test_cronjob_manage_read_and_mutation_have_expected_gate():
    read = gate.classify({"tool": "cronjob_manage", "args": {"action": "list"}})
    mutation = gate.classify({"tool": "cronjob_manage", "args": {"action": "create", "command": "echo A"}})
    assert read.category == "read"
    assert gate.decide(read).outcome == "allow"
    assert mutation.category == "opaque_exec"
    assert gate.decide(mutation).outcome == "ask"
    assert gate.binds_exact(mutation)
    assert gate.approval_binding(mutation, {"action": "create", "command": "echo A"}) != \
           gate.approval_binding(mutation, {"action": "create", "command": "echo B"})
