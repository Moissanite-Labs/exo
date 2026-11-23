"""A decision number the fix sweep lists as free must not already be a used decision (issue #600).

On PR #235 the sweep's list still read D-082 as free while the PR itself added the D-082 entry, and the
docs tests passed on that head; an earlier collision with D-081 had to be caught by hand. These checks
read the list the way the file words it and the headings DECISIONS.md keeps, so the stale list and the
collision both fail review.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX_SWEEP = ROOT / "docs" / "FIX-SWEEP.md"
DECISIONS = ROOT / "DECISIONS.md"

# The list's own sentence shape: "D-078 is free." (D-082 landed with #235; when the
# shape or the numbers move again, this fixture fails first, before the disjoint check.)
FREE_RE = re.compile(r"(D-\d{3}(?:(?:, | and )D-\d{3})*) (?:is|are) free")


def _free_numbers() -> set:
    text = FIX_SWEEP.read_text(encoding="utf-8")
    return {n for m in FREE_RE.finditer(text) for n in re.findall(r"D-\d{3}", m.group(1))}


def _used_numbers() -> set:
    return set(re.findall(r"^## (D-\d{3})\b", DECISIONS.read_text(encoding="utf-8"), re.M))


def test_the_free_list_parses():
    free = _free_numbers()
    assert free == {"D-078"}, (
        "docs/FIX-SWEEP.md's free list is not the known sentence any more; reword FREE_RE and this "
        f"fixture to the new list (parsed {sorted(free)})"
    )


def test_free_decision_numbers_are_not_used():
    used = _used_numbers()
    assert used, "no decision headings found in DECISIONS.md"
    clash = sorted(_free_numbers() & used)
    assert not clash, (
        f"docs/FIX-SWEEP.md lists free decision numbers that DECISIONS.md already uses: {clash}; "
        "renumber the new decision or fix the stale list"
    )
