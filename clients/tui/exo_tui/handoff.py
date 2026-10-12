"""/handoff (D-032): the client asks the core for the facts, then sends one user turn that has the agent write the
handoff with the exo-handoff skill. The agent writes the file through its normal tools and the gate; nothing here
touches the disk. Kept apart from app.py so the command table is the only thing app.py has to know."""

from __future__ import annotations

import json
from typing import Any

FACTS_LIMIT = 14_000  # characters of JSON sent to the model; the agent can run `exo trace` itself for more


def handoff_prompt(facts: dict[str, Any]) -> str:
    body = json.dumps(facts, indent=1, default=str)
    if len(body) > FACTS_LIMIT:
        body = json.dumps({**facts, "decisions": [d[:600] for d in facts.get("decisions", [])],
                           "trace": str(facts.get("trace", ""))[:2000]}, indent=1, default=str)[:FACTS_LIMIT] + "\n[cut]"
    return (
        "/handoff\n\n"
        "The user ran /handoff. Load the exo-handoff skill with the skill tool and write a handoff for the next "
        f"session to {facts.get('path')} with your normal write tool (create the folder first if it is missing). "
        "Follow the skill's template and rules. The core gathered these facts; add what only you know (the goal, "
        "what was tried, why choices were made):\n\n```json\n" + body + "\n```\n\n"
        "When the file is written, reply with its path and two lines of summary.")
