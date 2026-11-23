"""A finding id or id prefix is matched as text, never as a LIKE pattern (exo-desktop #52).

``Findings.get`` put what it was given straight into ``id LIKE ? || '%'``. So ``%`` and ``_`` matched any text:
``exo findings fix % --in <sha>`` marked the only finding fixed, and ``f_ab1_`` named a finding whose id does not
start with those characters. An empty id matched every finding too.
"""
import pytest

from exo.core.findings import Findings
from exo.core.sessions import SessionStore


@pytest.fixture
def findings():
    store = SessionStore()
    try:
        yield Findings(store, version="fixture")
    finally:
        store.close()


def add(findings, signature, fid):
    row = findings.file(signature, signature, severity="bug")
    findings.store._q("UPDATE findings SET id=? WHERE id=?", (fid, row["id"]))
    return fid


@pytest.mark.parametrize("pattern", ["%", "_", "f%", "f_%", "%34", "f__b12cd34", "f_ab12cd3_", "f_a_", ""])
def test_a_pattern_character_names_no_finding(findings, pattern):
    add(findings, "only", "f_ab12cd34")
    assert findings.get(pattern) is None
    with pytest.raises(KeyError):
        findings.fix(pattern, "abc1234")
    assert findings.get("f_ab12cd34")["status"] == "open"


def test_a_prefix_with_a_pattern_character_does_not_reach_a_sibling(findings):
    add(findings, "one", "f_ab12cd34")
    add(findings, "two", "f_ab99ef00")
    assert findings.get("f_ab1_") is None  # LIKE read _ as any character and found f_ab12cd34
    assert findings.get("f_ab_9") is None


def test_an_exact_id_and_a_unique_prefix_still_name_their_finding(findings):
    add(findings, "one", "f_ab12cd34")
    add(findings, "two", "f_ab99ef00")
    assert findings.get("f_ab12cd34")["signature"] == "one"
    assert findings.get("f_ab9")["signature"] == "two"
    assert findings.get("f_ab") is None  # two findings start this way
    assert findings.fix("f_ab1", "abc1234")["id"] == "f_ab12cd34"
