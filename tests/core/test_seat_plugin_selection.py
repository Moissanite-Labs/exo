"""An invalid explicit plugin selection must not become the default."""
import json

import pytest

from exo.core import seats


@pytest.mark.parametrize("value", ["exo-memory", 1, False, None])
@pytest.mark.parametrize("table", [False, True])
def test_invalid_plugin_selection_raises(value, table):
    data = {"plugins": {"enabled": value} if table else value}
    with pytest.raises(seats.SeatError, match="plugins.*list"):
        seats.parse(data, name="test")


@pytest.mark.parametrize(("data", "expected"), [
    ({}, None),
    ({"plugins": {}}, None),
    ({"plugins": []}, []),
    ({"plugins": ["exo-memory"]}, ["exo-memory"]),
    ({"plugins": {"enabled": []}}, []),
    ({"plugins": {"enabled": ["exo-memory"]}}, ["exo-memory"]),
])
def test_missing_empty_and_named_selection(data, expected):
    assert seats.parse(data, name="test").plugins == expected


def test_null_plugin_selection_differs_from_the_three_other_name_lists():
    st = seats.parse({"tools": {"toolsets": None, "env_deny": None}, "skills": {"load": None}}, name="test")
    assert (st.toolsets, st.env_deny, st.skill_load) == (None, [], None)
    for plugins in (None, {"enabled": None}):
        with pytest.raises(seats.SeatError, match="plugins.*list"):
            seats.parse({"plugins": plugins}, name="test")


@pytest.mark.parametrize(("suffix", "content"), [
    ("toml", 'plugins = "exo-memory"\n'),
    ("toml", '[plugins]\nenabled = "exo-memory"\n'),
    ("json", json.dumps({"plugins": None})),
    ("json", json.dumps({"plugins": {"enabled": False}})),
])
def test_file_loader_rejects_invalid_selection(tmp_path, suffix, content):
    folder = tmp_path / "seats"
    folder.mkdir()
    (folder / f"test.{suffix}").write_text(content)
    with pytest.raises(seats.SeatError, match="plugins.*list"):
        seats.load(tmp_path, "test")
