"""Principal-selected, process-local limits; admitted work holds an immutable snapshot."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

MAX_LIMIT = 2_147_483_647


@dataclass(frozen=True)
class LimitSnapshot:
    rounds: int | None = None
    worker_timeout_s: int | None = None

    def rounds_for(self, default: int, explicit: int | None = None) -> int:
        return self.rounds if self.rounds is not None else default if explicit is None else explicit

    def timeout_for(self, default: int) -> int:
        return default if self.worker_timeout_s is None else self.worker_timeout_s


class RunLimits:
    def __init__(self):
        self._snapshot = LimitSnapshot()

    def snapshot(self) -> LimitSnapshot:
        return self._snapshot

    def update(self, args: str) -> None:
        parts = args.split()
        if parts == ['default']:
            self._snapshot = LimitSnapshot()
            return
        if len(parts) == 3 and parts[-1] == 'all':
            parts.pop()
        if len(parts) != 2 or parts[0] not in ('rounds', 'worker-timeout'):
            raise ValueError('usage: /limits rounds|worker-timeout default|off|nonnegative-integer [all], or /limits default')
        word = parts[1]
        if word == 'default':
            value = None
        elif word == 'off':
            value = 0
        else:
            match = re.fullmatch(r'([0-9]{1,10})([smh]?)', word, flags=re.ASCII)
            if not match or (parts[0] == 'rounds' and match[2]):
                raise ValueError('limit must be default, off, or a bounded nonnegative integer')
            value = int(match[1]) * {'': 1, 's': 1, 'm': 60, 'h': 3600}[match[2]]
            if value > MAX_LIMIT:
                raise ValueError('limit exceeds 2147483647 seconds or rounds')
        key = 'rounds' if parts[0] == 'rounds' else 'worker_timeout_s'
        self._snapshot = replace(self._snapshot, **{key: value})

    def status(self, main_default: int, worker_round_default: int, worker_timeout_default: int) -> dict:
        snap = self.snapshot()
        show = lambda value: 'unlimited' if value == 0 else str(value)
        rounds = snap.rounds_for(main_default)
        main_label = show(rounds) if snap.rounds is not None else show(rounds) + ' configured default (window/per-turn choices apply)'
        worker_rounds = snap.rounds_for(worker_round_default)
        deadline = snap.timeout_for(worker_timeout_default)
        return {'supported': True, 'scope': 'core-all-conversations-and-delegated-workers',
                'ephemeral': True, 'applies_to': 'new turns and newly started delegated workers',
                'running_work_unchanged': True, 'rounds_override': snap.rounds,
                'worker_timeout_override_s': snap.worker_timeout_s,
                'main_rounds': rounds, 'worker_rounds': worker_rounds, 'worker_timeout_s': deadline,
                'defaults': {'main_rounds': main_default, 'worker_rounds': worker_round_default,
                             'worker_timeout_s': worker_timeout_default},
                'text': ('Core scope: all conversations and delegated workers. Resets on core restart. '
                         f'New main turns: {main_label} tool rounds; new workers: {show(worker_rounds)} tool rounds, '
                         f'worker wall deadline {"off" if deadline == 0 else str(deadline) + " seconds" + (" (" + str(deadline // 3600) + "h)" if deadline % 3600 == 0 else " (" + str(deadline // 60) + "m)" if deadline % 60 == 0 else "")}. '
                         'Running turns and workers keep their admitted limits. A core rounds override takes precedence '
                         'over window/per-turn round choices; default restores those choices. '
                         'Token, depth, concurrency, permissions and spending limits remain independent.')}
