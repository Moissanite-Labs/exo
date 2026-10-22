"""Issue #322 item 5: parsing a run of unclosed `<exo-pinned>` open tags must be linear.

The old lazy regex rescanned to the end of the text from every open tag when no close tag
followed, so a run of unclosed opens cost quadratic time (about 5 s at 100 KB). The scan now
uses `str.find` and stops at the first open tag with no close after it. These tests pin both
the timing and that the block boundaries read exactly as the old regex read them.
"""

import re
import time

import pytest

from exo import handoff as hf

OPEN, CLOSE = hf.PIN_OPEN, hf.PIN_CLOSE
BLOCK = f"{OPEN}\n### Rulings\n> never push to main\n### Corrections\n> use the branch\n{CLOSE}"


def _ref_strip(text: str) -> str:
    return re.sub(re.escape(OPEN) + r".*?" + re.escape(CLOSE), "", text, flags=re.S)


def _ref_bodies(text: str) -> list[str]:
    return re.findall(re.escape(OPEN) + r"(.*?)" + re.escape(CLOSE), text, re.S)


def _ref_parse(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {k: [] for _, k, _ in hf._PIN_SECTIONS}
    for body in _ref_bodies(text):
        for title, kind, _ in hf._PIN_SECTIONS:
            m = re.search(r"^### " + re.escape(title) + r"\s*$(.*?)(?=^### |\Z)", body, re.S | re.M)
            if m:
                out[kind].extend(hf._unquote_items(m.group(1)))
    return out


CASES = [
    "",
    "no tags at all",
    BLOCK,
    f"before\n{BLOCK}\nafter",
    f"{BLOCK}\nbetween\n{BLOCK}",
    f"{OPEN}{CLOSE}",
    f"{OPEN}unclosed",
    f"{CLOSE} close alone",
    f"{CLOSE}{BLOCK}",
    f"{OPEN} outer {OPEN} inner {CLOSE} trailing {CLOSE}",
    f"{BLOCK}{OPEN}{OPEN}{OPEN}",
    f"{OPEN}{OPEN}{CLOSE}",
    f"prefix {OPEN[:-1]}> not a tag {CLOSE}",
    f"{OPEN}\n### Rulings\n> one\n> two lines\n\n> second\n{CLOSE} tail {OPEN}",
    f"{OPEN} body with {CLOSE} early close {CLOSE} and {BLOCK}",
]


@pytest.mark.parametrize("text", CASES)
def test_strip_matches_reference(text: str) -> None:
    assert hf.strip_pinned(text) == _ref_strip(text)


@pytest.mark.parametrize("text", CASES)
def test_parse_matches_reference(text: str) -> None:
    assert hf.parse_pinned_blocks(text) == _ref_parse(text)


def test_parse_reads_sections() -> None:
    out = hf.parse_pinned_blocks(BLOCK)
    assert out["ruling"] == ["never push to main"]
    assert out["correction"] == ["use the branch"]
    assert out["open_decision"] == []


def test_unclosed_run_is_linear() -> None:
    text = OPEN * 8000
    start = time.perf_counter()
    assert hf.parse_pinned_blocks(text) == {"ruling": [], "correction": [], "open_decision": []}
    parse_s = time.perf_counter() - start
    start = time.perf_counter()
    assert hf.strip_pinned(text) == text
    strip_s = time.perf_counter() - start
    assert parse_s < 1.0, f"parse of 96 KB of unclosed opens took {parse_s:.2f}s"
    assert strip_s < 1.0, f"strip of 96 KB of unclosed opens took {strip_s:.2f}s"


def test_unclosed_run_after_block_is_linear_and_keeps_block() -> None:
    text = BLOCK + OPEN * 8000
    start = time.perf_counter()
    out = hf.parse_pinned_blocks(text)
    stripped = hf.strip_pinned(text)
    elapsed = time.perf_counter() - start
    assert out["ruling"] == ["never push to main"]
    assert stripped == OPEN * 8000
    assert elapsed < 1.0, f"block plus unclosed run took {elapsed:.2f}s"
