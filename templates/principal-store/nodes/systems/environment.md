---
id: systems/environment
title: Machines, accounts and tools (example node)
type: entity
domain: systems
summary: Example node. The machines, accounts and tools agents may use for the principal, and the limits on each. Replace with the real setup.
load_when: Choosing where to run something, which account to act from, or which tool to call.
confidence: low
stability: durable
valid_from: 2026-01-01
sources:
  - {kind: inferred, ref: "template:example", note: "placeholder; replace with real evidence"}
links:
  - {to: person/identity, rel: applies-to}
tags: [example, systems]
---

This is an example entity node. It names systems without storing any secret.

**Claim.** List each machine (name, role, how agents reach it), each account agents may act
from (service and handle only), and each tool with its limits.

**Never here.** Passwords, tokens, keys, recovery codes or private addresses. Keep those in
a secret manager and point to it by name.

**How to apply.** Act only from accounts listed here. Anything that changes security
settings, spends money or cannot be undone goes through `policies/gates.yaml`.
