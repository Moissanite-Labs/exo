import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def run_dir():
    d = tempfile.mkdtemp(prefix="exop", dir="/tmp")  # unix socket paths must stay short
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)
