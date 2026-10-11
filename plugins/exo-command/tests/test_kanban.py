from exo_command import kanban


def test_boards_columns_and_limits(world):
    d = kanban.tasks(world["home"], done_limit=5)
    slugs = [b["slug"] for b in d["boards"]]
    assert slugs == ["default", "site"]
    default = d["boards"][0]
    cols = {c["status"]: c for c in default["columns"]}
    assert list(cols) == ["triage", "running", "blocked", "done"]
    assert cols["done"]["total"] == 25 and len(cols["done"]["tasks"]) == 5
    assert "archived" not in cols
    assert cols["blocked"]["tasks"][0]["comments"] == 1
    assert d["boards"][1]["name"] == "Website"
    assert d["open"] == 4
    assert d["needs_attention"] == 3  # triage + blocked + review


def test_no_boards(tmp_path):
    d = kanban.tasks(tmp_path)
    assert d["boards"] == [] and d["open"] == 0
