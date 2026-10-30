"""Explicit human management of a running core's selected parent capability. Never autostarts a core."""
from __future__ import annotations

import argparse
import asyncio
import json

from exo.protocol import schema as s
from exo.protocol.client import Client


async def manage(client, session: str, action: str) -> dict:
    status = (await client.call(s.CommandRun(name='approval-authority', args='status', session=session))).get('output')
    if not isinstance(status, dict) or status.get('supports_delegated_approval') is not True:
        raise ValueError('Selected core does not support delegated approval authority')
    if action == 'status':
        return status
    return (await client.call(s.CommandRun(name='approval-authority', args=action, session=session))).get('output') or {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='exo approval-authority', description=__doc__)
    parser.add_argument('action', choices=('status', 'grant', 'revoke'))
    parser.add_argument('--socket', required=True, help='the running core Unix socket')
    parser.add_argument('--session', required=True, help='the selected parent session ID')
    args = parser.parse_args(argv)
    async def run():
        async with Client(socket=args.socket, principal='approval-authority') as client:
            return await manage(client, args.session, args.action)
    try:
        print(json.dumps(asyncio.run(run())))
        return 0
    except Exception:
        print('Delegated approval management failed. Check core support, principal access and signed audit availability.')
        return 1
