"""Malformed presence records do not break unrelated peer discovery (#638)."""
import json
import os
from dataclasses import asdict

import pytest

from exo.peers import Directory, Presence


@pytest.mark.parametrize("value", ["7", 7.0, True, None])
@pytest.mark.parametrize("prune", [False, True])
def test_bad_pid_is_ignored_without_hiding_a_live_peer(tmp_path, value, prune):
    directory = Directory(tmp_path)
    valid = Presence("good", "good-1", os.getpid(), "exo", "/tmp/not-opened.sock")
    directory.write(valid)
    bad = asdict(valid) | {"daemon": "bad-1", "pid": value}
    path = tmp_path / "bad-1.json"
    path.write_text(json.dumps(bad))
    assert directory.entry("bad-1") is None
    assert directory.get("bad-1") is None
    assert directory.scan(prune=prune) == [valid]
    assert path.exists() is (not prune)
    assert directory.entry("good-1")[1] == asdict(valid)


@pytest.mark.parametrize("value", [[1, 2], "text", 17, True, None])
@pytest.mark.parametrize("prune", [False, True])
def test_non_object_presence_is_ignored_and_pruning_is_optional(tmp_path, value, prune):
    directory = Directory(tmp_path)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(value))
    assert directory.entry("bad") is None
    assert directory.get("bad") is None
    assert directory.scan(prune=prune) == []
    assert path.exists() is (not prune)


def test_newer_signed_fields_are_kept_in_the_original_record(tmp_path):
    directory = Directory(tmp_path)
    raw = asdict(Presence("good", "good-1", os.getpid(), "exo", "/tmp/not-opened.sock"))
    raw["future_signed_field"] = {"version": 2}
    (tmp_path / "good-1.json").write_text(json.dumps(raw))
    presence, document = directory.entry("good-1")
    assert presence.pid == os.getpid()
    assert document == raw
