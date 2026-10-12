import os
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_SRC = PLUGIN_DIR.parents[1] / "src"

for p in (str(PLUGIN_DIR), str(REPO_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "exo-home"))
    for k in ("EXO_PRINCIPAL", "EXO_HANDOFF_DIR", "EXO_LAUNCH_TEMPLATE"):
        monkeypatch.delenv(k, raising=False)


RULING = "Never post anything publicly without my approval. Keep cost minimal: use the cheapest model that passes the tests."
CORRECTION = "No, I said the staging bucket, not production."
BRIEF = "Build the data importer for the billing project. Plain short sentences in every report."


def conversation():
    return [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": BRIEF},
        {"role": "assistant", "content": "Starting. I will write the importer first."},
        {"role": "user", "content": RULING},
        {"role": "assistant", "content": "Understood.", "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "write_file", "arguments": '{"path": "/srv/project/importer.py", "content": "x"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "Uploading the sample to the production bucket."},
        {"role": "user", "content": CORRECTION},
        {"role": "assistant", "content": "Switched to staging. Launched the indexer in the background (pid 4242)."},
        {"role": "user", "content": "Good."},
        {"role": "assistant", "content": "Importer works on the sample.\n\nNext steps:\n1. Run the migration on staging\n2. Write the docs\n\nShould I open the PR now or wait for the review?"},
    ]


@pytest.fixture
def convo():
    return conversation()
