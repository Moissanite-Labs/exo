---
id: person/identity
title: Who the principal is (example node)
type: fact
domain: person
summary: Example node. Name, role, organisations, base and time zone of the principal. Replace with real facts and sources.
load_when: An agent needs to know who it serves, or must introduce or sign for the principal.
confidence: low
stability: core
valid_from: 2026-01-01
sources:
  - {kind: inferred, ref: "template:example", note: "placeholder; replace with real evidence"}
links:
  - {to: mind/communication-style, rel: see-also}
  - {to: work/projects, rel: see-also}
tags: [example, identity]
---

This is an example node shipped with the empty store. It shows the shape of a fact node.
Replace the text below with what the principal said or wrote, and cite each source.

**Claim.** The principal is a person with a name, a role and a base. Record the name they
want agents to use, the organisations they run or work for, and their time zone.

**Evidence.** List what the principal said (quoted, with a date), what they did, and what
was inferred. Keep the three apart. Point to `corpus/private/` for raw material.

**How to apply.** Use the preferred name. Schedule and timestamp in their time zone. Never
put contact details, account secrets or third parties' private details in this node.
