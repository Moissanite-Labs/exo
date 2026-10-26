"""Cache the taste prompt until its inputs change, without replaying the ledger on every call."""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timezone, tzinfo

from .layers import taste_block, taste_policy


_NOTES = frozenset({
    "session_start", "belief_rejected", "belief_reinforced", "taste_signal", "taste_mode", "taste_pin", "taste_unpin",
    "taste_off", "taste_on", "taste_answer", "taste_undo", "taste_restore", "taste_reset",
    "taste_replaced", "taste_scoped", "value_stepped_back", "value_adopted", "taste_restored",
})


class TastePrompt:
    """One profile's bounded cache, keyed by exact work scope. Ordinary transcript writes leave it alone.

    A head lookup and a scan of only new relevant events detect another process's writes too. Policy
    changes and the next local calendar day also invalidate the cache. The principal pack is separate.
    """

    def __init__(self, store, ledger, budget_tokens: int):
        self.store, self.ledger, self.budget_tokens = store, ledger, budget_tokens
        self._seq = 0
        self._stamp: tuple[date, tuple[int, int] | None] | None = None
        self._policy_sig: tuple[int, int] | None = None
        self._tz: tzinfo = timezone.utc
        self._blocks: OrderedDict[str | None, str] = OrderedDict()

    def _day(self) -> date:
        return datetime.now(self._tz).date()

    def block(self, scope: str | None = None) -> str:
        if self.budget_tokens <= 0:
            return ""
        path = self.store.root / "policies" / "consolidate.yaml"
        try:
            stat = path.stat()
            policy_sig = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            policy_sig = None
        if self._stamp is None or policy_sig != self._policy_sig:
            from .adapt import zone
            self._tz = zone(taste_policy(self.store))
        stamp = (self._day(), policy_sig)
        head = self.ledger.head().seq
        changed = stamp != self._stamp or head < self._seq
        if not changed and head != self._seq:
            for ev in self.ledger.iter(kind=("ruling", "belief_proposed", "belief_accepted", "note"),
                                       since=self._seq):
                if ev["seq"] > head:
                    break
                if ev["kind"] != "note" or (ev["body"] or {}).get("type") in _NOTES:
                    changed = True
                    break
        if changed or scope not in self._blocks:
            block = taste_block(self.store, budget_tokens=self.budget_tokens, scope=scope)
            if changed:
                self._blocks.clear()
            self._blocks[scope] = block
            while len(self._blocks) > 32:
                self._blocks.popitem(last=False)
        self._seq, self._stamp, self._policy_sig = head, stamp, policy_sig
        self._blocks.move_to_end(scope)
        return self._blocks[scope]
