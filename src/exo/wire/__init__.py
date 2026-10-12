"""exo.wire: the agent-to-agent wire (docs/AGENT-WIRE.md).

SYM-2P/2 dense lines with Exo's vocabulary for delegation briefs and receipts, plus a JSON
fallback and a deterministic English renderer. Code reads and writes the line form; people
and models read the English form. The vendored upstream library stays in
``exo.protocols.sym2p`` and checks pure upstream packets unchanged.
"""

from exo.wire.grammar import PROFILE, WireError, check_line, repair
from exo.wire.packets import (TASK_STATUSES, WIRE_FORMATS, Brief, Packet, Receipt, TaskStatus, decode, decode_any,
                              encode, from_json, negotiate, render, sign, to_json, verify)

__all__ = ["PROFILE", "TASK_STATUSES", "WIRE_FORMATS", "Brief", "Packet", "Receipt", "TaskStatus", "WireError",
           "check_line", "decode", "decode_any", "encode", "from_json", "negotiate", "render", "repair", "sign",
           "to_json", "verify"]
