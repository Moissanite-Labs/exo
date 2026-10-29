# Authority claim candidates (#833)

This is a proposed detector building block, not an ingress authority control.
`principal_claim_candidate(text, author=...)` recognizes a bounded multi-line
unquoted instruction-override block with replacement directions. It preserves the
recorded author's identity and returns no user text. Quotes and code fences are
excluded. Over-limit input is refused rather than partially scanned.

The detector is not wired into ingress, findings or the UI. It cannot prove intent
or identify all paraphrases, and a match must never change authority, permission,
model policy or submission behavior. The existing author stamp remains the source
of identity. Review this limited heuristic before choosing runtime placement.

Remaining #833 acceptance: record a harness finding with the actual session/turn
and transport author, show a principal-visible notice, choose signal/deduplication
policy, test real ingress and steers/imports, and expand quoted-discussion and
multilingual controls. Normal messages must retain exactly their current path.
The caller must not treat a non-match as proof that text is trustworthy.
