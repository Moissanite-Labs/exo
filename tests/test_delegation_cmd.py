"""Old cores refuse before any grant mutation, never a model message."""
import asyncio

import pytest

from exo.delegation_cmd import manage


@pytest.mark.parametrize('supported', [False, True])
def test_cli_probes_capability_before_mutating_selected_parent(supported):
    class Fake:
        def __init__(self): self.calls = []
        async def call(self, body):
            self.calls.append(body)
            return {'output': {'supports_delegated_approval': supported, 'enabled': body.args == 'grant'}}
    client = Fake()
    if supported:
        assert asyncio.run(manage(client, 'parent', 'grant'))['enabled']
    else:
        with pytest.raises(ValueError, match='does not support'):
            asyncio.run(manage(client, 'parent', 'grant'))
    assert [body.args for body in client.calls] == (['status', 'grant'] if supported else ['status'])
    assert all(body.session == 'parent' and body.name == 'approval-authority' for body in client.calls)
