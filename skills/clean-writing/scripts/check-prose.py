#!/usr/bin/env python3
"""Blocking prose gate for anything posted where a human reads it.

Run this BEFORE posting any external text (PR/issue body or comment, review, docs,
client deliverable). Non-zero exit means DO NOT POST. It is mechanical on purpose:
these tells are invisible while you write them, and "I will be careful" has already
failed at least once.

    check-prose.py <file> [<file> ...]
    check-prose.py --text "the string I am about to post"
    check-prose.py --policy <prose.json> --names <internal-names.txt> <file>

Exit codes: 0 clean, 1 tells found, 3 usage/IO error.

Rules are the generic clean-writing rules. A principal adds their own style profile and the
internal names that must never be published in the principal store:
  <store>/policies/prose.json          {"banned_phrases": [...], "internal_names": [...],
                                        "identifier_patterns": [...], "max_paragraph_chars": 420,
                                        "flag_mentions": true, "flag_semicolons": true}
  <store>/policies/internal-names.txt  one name per line, '#' comments allowed
<store> is $EXO_PRINCIPAL_STORE, else $EXO_HOME/principals/$EXO_PRINCIPAL (EXO_HOME defaults
to ~/.exo). $EXO_PROSE_POLICY and $EXO_INTERNAL_NAMES override the two paths.
Provenance: generalized from the Protean team's check-prose.py (stdlib only).
A hit is a candidate to fix, not a verdict: a quote from someone else, a code block,
and an em dash inside a quoted command are all legitimate. Read each hit.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

EM_DASH = "\u2014"
EM_DASH_ALLOWED_IN = ("```",)  # fenced code keeps the source text verbatim

BANNED_PHRASES = [
    # frame openers that announce the message instead of being it
    r"two things worth stating",
    r"findings first",
    r"the honest gap",
    r"i want to be upfront",
    r"one caveat worth flagging",
    r"two notes for",
    r"note on mechanism",
    r"a few thoughts",
    r"worth settling",
    r"worth pinning down",
    r"something to consider",
    # cardinality prefaces: the list itself shows the count ("Two integration notes.")
    r"\b(two|three|four|five) ([a-z]+ ){0,3}(notes|points|things|thoughts|observations|findings)[.:]",
    r"^one ([a-z]+ ){1,4}notes?\s*[:.]",
    r"^a quick note\b",
    # self-referential meta-framing
    r"i'?d frame this differently",
    r"i'?d argue",
    r"i would say",
    r"my earlier note",
    r"this deserves",
    r"stand on their own",
    r"deserves precision",
    # narration of the search
    r"after tracing",
    r"it turns out that",
    r"i checked .{0,20} and found",
    # verdicts the reader can form
    r"the right shape",
    r"right design",
    r"exactly what is needed",
    r"the natural single base",
    r"the obvious choice",
    r"a step in the right direction",
    # praise that grades a contribution (condescending; state the fact instead)
    r"the strongest evidence",
    r"the strongest proof",
    r"the strongest signal",
    r"the best datapoint",
    r"the best data point",
    r"the best report",
    r"the best analysis",
    r"the cleanest proof",
    # signposts and closers
    r"^notably",
    r"^importantly",
    r"^it is worth noting",
    r"^at its core",
    r"that is okay",
    r"no shame",
    r"none subsumes another",
    r"the rest follows",
    r"nothing else to add",
]

# Generic identifiers that should never appear in published text. Principal-specific names
# (agents, seats, hosts, accounts) are added from the principal store at run time.
IDENTIFIER_PATTERNS = [r"/Users/[A-Za-z]", r"/home/[A-Za-z]"]

SETTINGS = {"max_paragraph_chars": 420, "flag_mentions": True, "flag_semicolons": True}


def _store_dir():
    explicit = os.environ.get("EXO_PRINCIPAL_STORE")
    if explicit:
        return Path(explicit).expanduser()
    principal = os.environ.get("EXO_PRINCIPAL")
    if principal:
        return Path(os.environ.get("EXO_HOME", "~/.exo")).expanduser() / "principals" / principal
    return None


def _read_names(path):
    return [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def load_policy(policy_path=None, names_path=None):
    """Merge the principal's prose policy and internal names into the module rules.

    Returns a short description of what was loaded, for the report line.
    """
    store = _store_dir()
    loaded = []
    policy_path = policy_path or os.environ.get("EXO_PROSE_POLICY") or (
        store / "policies" / "prose.json" if store else None)
    names_path = names_path or os.environ.get("EXO_INTERNAL_NAMES") or (
        store / "policies" / "internal-names.txt" if store else None)
    names = []
    if policy_path and Path(policy_path).is_file():
        doc = json.loads(Path(policy_path).read_text(encoding="utf-8"))
        BANNED_PHRASES.extend(doc.get("banned_phrases", []))
        IDENTIFIER_PATTERNS.extend(doc.get("identifier_patterns", []))
        names.extend(doc.get("internal_names", []))
        for key in SETTINGS:
            if key in doc:
                SETTINGS[key] = doc[key]
        loaded.append(str(policy_path))
    if names_path and Path(names_path).is_file():
        names.extend(_read_names(names_path))
        loaded.append(str(names_path))
    if names:
        IDENTIFIER_PATTERNS.append(names_pattern(names))
    return loaded


def names_pattern(names):
    """Whole names: a word edge must not touch another word character, a non-word edge
    ('.agents', '@bot', 'seat.') needs no boundary. Longest first, so the full name is reported."""
    def one(n):
        return ((r"(?<!\w)" if re.match(r"\w", n) else "") + re.escape(n)
                + (r"(?!\w)" if re.search(r"\w\Z", n) else ""))
    return "|".join(one(n) for n in sorted(names, key=len, reverse=True))


# A GitHub handle mention on a thread owned by that same account is a self-ping
# (see clean-writing: "Never @-mention the thread owner"). Flag every mention as
# a candidate to remove; the writer keeps it only on genuinely external threads.
# Negative lookbehind excludes emails (word@domain) and chained handles.
MENTION_RE = re.compile(r"(?<![\w.@])@\w+")


def strip_code(text):
    """Drop fenced blocks and inline code so quoted material is not flagged.

    Fence lines are replaced with an empty line rather than removed, so reported line
    numbers still map onto the file the reader will open.
    """
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def check(name, raw):
    prose = strip_code(raw)
    findings = []

    for n, line in enumerate(prose.splitlines(), 1):
        if EM_DASH in line:
            findings.append(f"{name}:{n}: em dash (T2) -> use a period or comma: {line.strip()[:110]}")
        if SETTINGS["flag_semicolons"] and ";" in line and len(line) > 60:
            findings.append(f"{name}:{n}: semicolon stitching clauses (T2) -> prefer a period: {line.strip()[:110]}")

    for pat in BANNED_PHRASES:
        for m in re.finditer(pat, prose, re.IGNORECASE | re.MULTILINE):
            line_no = prose[: m.start()].count("\n") + 1
            findings.append(f"{name}:{line_no}: banned phrase '{m.group(0)}' (AI tell)")

    for pat in IDENTIFIER_PATTERNS:
        for m in re.finditer(pat, raw, re.IGNORECASE):
            line_no = raw[: m.start()].count("\n") + 1
            findings.append(f"{name}:{line_no}: internal identifier '{m.group(0)}' -> use a generic term or a relative path")

    for m in (MENTION_RE.finditer(prose) if SETTINGS["flag_mentions"] else ()):
        line_no = prose[: m.start()].count("\n") + 1
        findings.append(
            f"{name}:{line_no}: @-mention '{m.group(0)}' -> on a thread owned by the "
            f"authenticated account this is a self-ping; remove it (credit lives in "
            f"git authorship, not the comment)"
        )

    # line length: a posted paragraph should scan, not wall
    limit = int(SETTINGS["max_paragraph_chars"])
    longs = [n for n, l in enumerate(prose.splitlines(), 1) if len(l) > limit and not l.strip().startswith("|")]
    if longs:
        findings.append(f"{name}: paragraph over {limit} chars on line(s) {longs} -> split it")

    return findings


def check_dupes(name, raw):
    """Duplicate-reference gate: each external reference appears exactly once.

    Every detected reference is normalized to a canonical key before counting:
      - GitHub PR/issue URL (.../pull/<N> or .../issues/<N>) -> canonical gh#<N>
      - Inline forms (#<N>, PR #<N>, issue #<N>) -> canonical gh#<N>
      - Any other http(s) URL -> scheme+netloc+path, lowercased, trailing
        punctuation stripped.
    """
    import urllib.parse
    counts = {}
    first_line = {}
    forms = {}
    url_spans = []

    def _record(key, form, pos):
        counts[key] = counts.get(key, 0) + 1
        first_line.setdefault(key, raw[:pos].count("\n") + 1)
        forms.setdefault(key, [])
        if form not in forms[key]:
            forms[key].append(form)

    gh_path_re = re.compile(r"github\.com/[^/\s]+/[^/\s]+/(?:pull|issues)/(\d+)", re.IGNORECASE)
    for m in re.finditer(r"https?://\S+", raw, re.IGNORECASE):
        url = m.group(0).rstrip(".,;:!?\'\"`)]}>")
        url_spans.append((m.start(), m.end()))
        gh = gh_path_re.search(url)
        if gh:
            _record("gh#" + gh.group(1), url, m.start())
            continue
        try:
            parts = urllib.parse.urlparse(url)
            if parts.scheme and parts.netloc:
                key = (parts.scheme.lower() + "://" + parts.netloc.lower() + parts.path).lower()
            else:
                key = url.lower()
        except Exception:
            key = url.lower()
        _record(key, url, m.start())
    for m in re.finditer(r"(?:PR|issue)?\s*#(\d+)", raw, re.IGNORECASE):
        if any(s <= m.start() < e for s, e in url_spans):
            continue
        key = "gh#" + m.group(1)
        _record(key, m.group(0).strip(), m.start())
    out = []
    for k in sorted(counts):
        n = counts[k]
        if n > 1:
            seen = "; ".join(forms.get(k, [k]))
            out.append(
                name + ":" + str(first_line[k]) + ": duplicate reference \'" + k
                + "\' appears " + str(n) + "x -> mention once (seen: " + seen + ")"
            )
    return out


def main(argv):
    parser = argparse.ArgumentParser(description="Blocking prose gate.")
    parser.add_argument("--dupes", action="store_true",
                        help="duplicate-reference gate: each URL / PR # / issue # appears once")
    parser.add_argument("--text", nargs="+", default=None,
                        help="the string about to be posted")
    parser.add_argument("--policy", default=None, help="prose policy JSON (default: principal store)")
    parser.add_argument("--names", default=None, help="internal names file (default: principal store)")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    try:
        load_policy(args.policy, args.names)
    except (OSError, ValueError) as exc:
        print(f"check-prose: cannot load policy: {exc}", file=sys.stderr)
        return 3

    if args.text is not None:
        sources = [("<text>", " ".join(args.text))]
    elif not args.files:
        print(__doc__)
        return 3
    else:
        sources = []
        for p in args.files:
            try:
                with open(p, encoding="utf-8") as fh:
                    sources.append((p, fh.read()))
            except OSError as exc:
                print(f"check-prose: cannot read {p}: {exc}", file=sys.stderr)
                return 3

    if args.dupes:
        findings = []
        for name, text in sources:
            findings += check_dupes(name, text)
        if findings:
            print(f"check-prose --dupes: {len(findings)} duplicate reference(s). DO NOT POST:\n")
            for f in findings:
                print(f"  {f}")
            return 1
        print(f"check-prose --dupes: clean ({len(sources)} file(s))")
        return 0

    findings = []
    for name, text in sources:
        findings += check(name, text)

    if findings:
        print(f"check-prose: {len(findings)} finding(s). DO NOT POST until each is fixed or justified:\n")
        for f in findings:
            print(f"  {f}")
        return 1

    print(f"check-prose: clean ({len(sources)} file(s), {sum(len(t.splitlines()) for _, t in sources)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
