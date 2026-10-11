# Follow-up action — template (v1.0.0)

    action id: <council-lane>-F<n>
    derives from: decision record <path>, ruling/rec <id>
    status: OPEN | DONE | SUPERSEDED (by <ref>)

| field | value |
|---|---|
| action | <one sentence> |
| owner lane | <member / profile> |
| authorized by | receipt <path> terminal marker <...> |
| acceptance signal | <observable check> |
| stop condition | <what halts the lane> |
| read-back | <artifact + UTC, verified by> |

Rule: a follow-up either cites the receipt that authorized it, or it is a new
decision (open a new council/brief). No silent canonical edits while rulings
are unresolved.
