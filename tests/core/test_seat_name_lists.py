"""A seat's allow and deny lists must be lists (exo-desktop #52).

``toolsets = "fs"`` read as no list at all, and no list means every toolset: a seat meant to have one toolset got
all of them. ``skills.load`` (no list: every skill) and ``env_deny`` (no list: nothing more hidden) widened the
same way. The loader now refuses a value of the wrong type for these three keys, so the seat does not start with
more than its file asked for.
"""
import pytest

from exo.core import seats

WRONG = ["fs", "fs, search", True, 3, {"fs": True}]


@pytest.mark.parametrize("value", WRONG)
@pytest.mark.parametrize("where", ["tools", "top"])
def test_a_toolsets_value_that_is_not_a_list_is_refused(value, where):
    data = {"name": "scout", **({"tools": {"toolsets": value}} if where == "tools" else {"toolsets": value})}
    with pytest.raises(seats.SeatError, match="toolsets"):
        seats.parse(data)


@pytest.mark.parametrize("value", WRONG)
def test_skills_load_and_env_deny_that_are_not_lists_are_refused(value):
    with pytest.raises(seats.SeatError, match="load"):
        seats.parse({"name": "scout", "skills": {"load": value}})
    with pytest.raises(seats.SeatError, match="env_deny"):
        seats.parse({"name": "scout", "tools": {"env_deny": value}})


def test_a_seat_file_with_a_string_toolsets_does_not_load(tmp_path):
    (tmp_path / "seats").mkdir()
    (tmp_path / "seats" / "scout.toml").write_text('name = "scout"\n[tools]\ntoolsets = "fs"\n')
    with pytest.raises(seats.SeatError, match="toolsets"):
        seats.load(tmp_path, "scout")
    assert "toolsets" in seats.list_seats(tmp_path)[0]["error"]


def test_lists_and_absent_keys_read_as_before():
    st = seats.parse({"name": "scout", "tools": {"toolsets": ["fs"], "env_deny": ["COMPANY_*"]},
                      "skills": {"load": ["choreography"]}})
    assert (st.toolsets, st.env_deny, st.skill_load) == (["fs"], ["COMPANY_*"], ["choreography"])
    st = seats.parse({"name": "scout", "toolsets": []})
    assert st.toolsets == []  # an empty list keeps only the always-on toolsets
    st = seats.parse({"name": "scout"})
    assert (st.toolsets, st.env_deny, st.skill_load) == (None, [], None)
