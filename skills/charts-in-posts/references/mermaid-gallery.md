# Mermaid gallery: one working example per allowlisted type

Every block below passes `scripts/check-mermaid.py`. Copy a block into a
post draft and replace the labels. Keep diagrams small: past six nodes,
split the diagram or use an image instead.

## graph

```mermaid
graph TD;
    A-->B;
    A-->C;
    B-->D;
    C-->D;
```

## flowchart

```mermaid
flowchart LR
    Start --> Check --> Done
```

## sequenceDiagram

```mermaid
sequenceDiagram
    Alice->>Bob: hello
    Bob-->>Alice: ack
```

## gantt

```mermaid
gantt
    title Schedule
    dateFormat YYYY-MM-DD
    section Work
    Build :a1, 2026-10-01, 3d
    Review :after a1, 2d
```

## pie

```mermaid
pie
    title Votes
    "Yes" : 70
    "No" : 30
```

## erDiagram

```mermaid
erDiagram
    AUTHOR ||--o{ BOOK : writes
    BOOK ||--|{ CHAPTER : contains
```

## stateDiagram-v2

```mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Posted
    Posted --> [*]
```
