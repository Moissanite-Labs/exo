import os

import pytest

from exo.core.sessions import SessionStore, canonical_cwd


def test_none_and_missing_path(tmp_path):
    assert canonical_cwd(None) is None and canonical_cwd("") == ""
    gone = str(tmp_path / "nope" / "deeper")
    assert canonical_cwd(gone) == os.path.realpath(gone)


def test_fixes_case_of_each_component(tmp_path):
    real = tmp_path / "MixedCase" / "SubDir"
    real.mkdir(parents=True)
    swapped = str(tmp_path / "mixedcase" / "SUBDIR")
    if os.path.exists(swapped) is False:
        pytest.skip("case-sensitive filesystem")
    assert canonical_cwd(swapped) == os.path.join(os.path.realpath(tmp_path), "MixedCase", "SubDir")


def test_create_session_stores_canonical(tmp_path):
    (tmp_path / "Proj").mkdir()
    store = SessionStore(tmp_path / "s.sqlite")
    info = store.create_session(cwd=str(tmp_path / "Proj"))
    assert info.cwd == os.path.join(os.path.realpath(tmp_path), "Proj")
    assert store.create_session().cwd is None
