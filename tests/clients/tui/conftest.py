import pytest


@pytest.fixture(autouse=True)
def classic_layout_by_default(monkeypatch):
    """The experiment makes `inverse` the default (D-046). The older tests describe the classic order, so they pin it;
    tests of the inverse layout set EXO_LAYOUT or call /layout themselves."""
    monkeypatch.setenv("EXO_LAYOUT", "classic")
