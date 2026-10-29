# Saved Swarm groups

`exo.core.groups.GroupStore` saves group definitions in the existing sessions database.
This is the G01 storage foundation from the [Swarm plan](https://github.com/Moissanite-Labs/exo/discussions/934).
It does not start agents, route messages, or expose client commands.

## Definition

A group has a stable ID, a metadata owner, a mode, member profile references,
a session per member, and a creation time. Conductor mode requires one conductor
from the membership. Socratic mode rejects a conductor. Members must be distinct
valid seat IDs present in the catalog supplied by the trusted caller.

Saving a group creates fresh private sessions for its members in the same write
transaction. Failure leaves neither the group nor partial sessions. Existing
conversations are never attached or copied. Reopening the database restores the
same group and session IDs.

Mode, membership and conductor selection are immutable in this slice. To change
these choices, create a separate group with fresh sessions. No history transfer
is implied. There is no edit or deletion operation yet.

## Boundaries

Reads require the metadata owner's ID. A missing group and a foreign group both
return no record. The owner argument must come from trusted runtime identity,
not an agent-supplied field. Metadata ownership does not authorize execution as
a member profile, reading member history, or approving that profile's actions.

The adapter is internal and has no protocol or tool entry point. It stores no
credentials or model settings. Future runtime integration must validate profile
availability and execution authority again before starting members. It must also
enforce Socratic and conductor message audiences. Session privacy alone is not
an implementation of group message routing.

## Remaining G01 work

- Shared create/list/get APIs with caller authorization and profile resolution.
- Lifecycle and safe configuration changes, including retention policy.
- Client controls and recovery integrated with the concurrent runtime.

`tests/test_saved_groups.py` covers validation, restart, owner-scoped metadata,
fresh private sessions, and rollback when the group insert fails.
