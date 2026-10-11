#!/usr/bin/env python3
"""Mermaid fence checker for GitHub post drafts.

Checks fenced ```mermaid blocks in a markdown draft against the
charts-in-posts v1 contract. Standalone advisory gate: run it on the
draft BEFORE invoking the draft-review-html pipeline. It never rewrites
the draft, never fetches URLs, never shells out.

    check-mermaid.py <draft.md>

Exit codes: 0 all fences valid (or no mermaid fences present),
1 any fence fails, 2 usage error (missing/unreadable file).

A fence is valid iff ALL hold:
  (a) it opens with exactly ```mermaid and closes with ```,
  (b) its first non-blank line declares an allowlisted diagram type,
  (c) round/square/curly brackets balance inside the fence,
  (d) no <script or <iframe inside the fence,
  (e) no http(s) URLs inside the fence except GitHub attachment hosts.
"""

import sys

ALLOWLIST = {
    "graph",
    "flowchart",
    "sequenceDiagram",
    "gantt",
    "pie",
    "erDiagram",
    "stateDiagram-v2",
}

# Hosts GitHub uses for issue/PR attachment images (served via camo).
URL_ALLOW_PREFIXES = (
    "https://github.com",
    "https://user-images.githubusercontent.com",
    "https://private-user-images.githubusercontent.com",
    "https://objects.githubusercontent.com",
    "https://camo.githubusercontent.com",
)

OPEN = "```mermaid"
CLOSE = "```"


def check_fence(lines):
    """Return (diagram_type, error_reason_or_None) for one fence body."""
    first = None
    for line in lines:
        stripped = line.strip()
        if stripped:
            first = stripped
            break
    if first is None:
        return ("?", "empty fence")
    dtype = first.split()[0].rstrip(";:,")
    if dtype not in ALLOWLIST:
        return (dtype, "type '%s' not in allowlist" % dtype)
    for opener, closer in (("(", ")"), ("[", "]"), ("{", "}")):
        if dtype == "erDiagram" and (opener, closer) == ("{", "}"):
            # erDiagram relationship notation uses lone braces
            # (e.g. ||--o{), so brace balance does not apply there.
            continue
        if sum(l.count(opener) for l in lines) != sum(
            l.count(closer) for l in lines
        ):
            return (dtype, "unbalanced %s%s" % (opener, closer))
    lowered = "\n".join(lines).lower()
    if "<script" in lowered or "<iframe" in lowered:
        return (dtype, "embedded <script or <iframe")
    for lineno, line in enumerate(lines, start=1):
        for token in line.split():
            low = token.lower()
            if low.startswith("http://") or low.startswith("https://"):
                url = token.strip("()<>,;\"'")
                if not url.startswith(URL_ALLOW_PREFIXES):
                    return (dtype, "non-attachment URL on line %d" % lineno)
    return (dtype, None)


def main(argv):
    if len(argv) != 2:
        print("usage: check-mermaid.py <draft.md>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            text_lines = handle.read().splitlines()
    except OSError as exc:
        print("cannot read %s: %s" % (argv[1], exc), file=sys.stderr)
        return 2

    fences = []
    current = None
    for line in text_lines:
        stripped = line.strip()
        if current is None:
            if stripped == OPEN:
                current = []
        else:
            if stripped == CLOSE:
                fences.append(current)
                current = None
            else:
                current.append(line)
    failed = False
    if current is not None:
        print("fence %d: FAIL unclosed fence (missing ```)" % (len(fences) + 1))
        failed = True
    for number, body in enumerate(fences, start=1):
        dtype, reason = check_fence(body)
        if reason is None:
            print("fence %d: %s OK" % (number, dtype))
        else:
            print("fence %d: FAIL %s" % (number, reason))
            failed = True
    if not fences and not failed:
        print("no mermaid fences found")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
