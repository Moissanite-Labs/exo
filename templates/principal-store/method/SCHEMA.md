# Node schema

A node is one Markdown file holding one cluster of claims about the principal that are loaded
together. Target 150 to 450 words. If a node grows past that, split it and link the parts.

## Frontmatter (all keys required unless marked optional)

The parser reads a small subset of YAML: `key: value`, `key: [a, b]`, and lists of strings or
flow maps (`- {k: v, k: "v, w"}`). Block maps inside lists also work.

```yaml
---
id: mind/communication-style        # = path under nodes/ without .md; stable forever
title: How the principal communicates
type: trait | value | fact | preference | rule | episode | entity | procedure | question
domain: person | mind | relationship | work | systems | <any folder you add>
summary: One line, under 160 chars. This is what search results and INDEX show.
load_when: One line. The situation in which an agent should pull this node.
confidence: high | medium | low      # how sure we are the claims hold now
stability: core | durable | volatile # core = identity-level; volatile = changes weekly
valid_from: 2026-01-01               # when this became true (or first observed)
valid_until:                         # optional; set when superseded, keep the node
sources:                             # every claim traces here
  - kind: principal-said | principal-wrote | observed | research | inferred
    ref: dialogue:<session>@<timestamp>
    note: optional short context
links:                               # typed edges, become graph/graph.json
  - {to: relationship/reporting, rel: explains}
tags: [optional, free]
---
```

## Source `ref` prefixes

| Prefix | Resolves to |
|---|---|
| `event:<id>` | an event in `ledger.sqlite` |
| `dialogue:<session>@<ts>` | a message in a transcript under `corpus/private/` (session id prefix, UTC timestamp) |
| `private:<path>` | file under `corpus/private/` |
| `sources:<name>#<section>` | `corpus/sources/<name>.md`, section heading |
| `memory:<file>` | a file in an agent's working memory directory |
| `timeline:<date>` | `timeline/<date>.md` |
| `file:<path>` | any other local path |
| `https://...` | public page |

## Edge types (`rel`)

| rel | meaning |
|---|---|
| `part-of` | this node is a component of the target |
| `explains` | this node gives the reason behind the target |
| `evidence-for` | this node's episodes support the target's claim |
| `contradicts` | tension between the two; both may be true in context |
| `supersedes` | this node replaces the target (target gets `valid_until`) |
| `applies-to` | a rule or preference that governs the target entity |
| `instance-of` | an episode illustrating a trait or rule |
| `see-also` | weak association |

## Body conventions

- Lead with the claim in one or two sentences. Then evidence, then "How to apply".
- Quote the principal verbatim in quotation marks, short, with the date. Mark paraphrase as such.
- Separate what the principal **said**, what they **did**, and what we **infer**. Label inference.
- Never put secrets, credentials, phone numbers or third parties' private details in a node.
  Point to `corpus/private/` instead.

## Node types

- **trait**: stable pattern in how the principal thinks or acts.
- **value**: what the principal holds as good; stated or revealed.
- **fact**: biographical or organisational fact.
- **preference**: how the principal wants things done.
- **rule**: standing order or ruling with authority behind it.
- **episode**: a dated event worth remembering for its lesson.
- **entity**: a person, company, agent, machine, repository.
- **procedure**: how to do a recurring thing in the principal's world.
- **question**: an open uncertainty to ask the principal about (active learning).

## Checks

`exo lint` checks required keys, types, id against path, edge types, dangling links,
orphans, length, and leak patterns (phone numbers, keys, tokens, passwords).
