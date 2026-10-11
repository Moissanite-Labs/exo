# Decision record — template (v1.0.0)

    council: <short-lane-name>
    bounded question: "<verbatim>"
    date: <UTC ISO-8601>
    status: OPEN | RESOLVED | SUPERSEDED (by <ref>)

## 1. Rulings

| # | ruling | evidence | dissent (if any) | gate |
|---|---|---|---|---|
| R1 | <decision> | <register row ids> | <who / what / why, or none> | <owner gate, or none> |

## 2. Recommendations (bounded action list)

| # | action | owner lane | acceptance signal | stop condition |
|---|---|---|---|---|
| A1 | <action> | <member> | <observable check> | <condition that halts the lane> |

## 3. Owner-autonomous split

- Team executes autonomously: <A-ids>
- Waits on owner decision: <A-ids> — gate: <exact gate name>

## 4. Dissents and open items

- D1: <seat> dissents from <claim> — evidence: <...> — resolution: <resolved how / open item id>

## 5. Model receipt

<seat → profile/config model table, G-2 discipline>
