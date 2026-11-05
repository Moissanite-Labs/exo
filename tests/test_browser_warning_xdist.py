"""Recovered Chrome starts must remain visible when plugin warnings cross xdist."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_recovered_browser_warning_survives_aggregate_xdist(tmp_path):
    # Load the real aggregate collection and browser helper. Only startup is synthetic:
    # no Chrome, DevTools connection or sleep is needed to exercise its retry warning.
    (tmp_path / "recovered_start_probe.py").write_text('''
import pytest

def pytest_runtest_call(item):
    import browser
    attempts = []
    def start(self, *args):
        attempts.append(True)
        if len(attempts) == 1:
            raise browser.ChromeStartError("synthetic startup failure")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(browser, "find_chrome", lambda: "synthetic-chrome")
        patch.setattr(browser.Browser, "_start", start)
        patch.setattr(browser, "_gave_up", False)
        instance = browser.Browser()
        instance.close()
    assert len(attempts) == 2
''')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "plugins", "-q", "-n", "2",
         "-p", "recovered_start_probe", "-k", "test_healthy_start_lines_are_not_reported_as_a_cause"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "1 passed" in output, output
    assert "ChromeStartWarning: Chrome started after a failed start: synthetic startup failure" in output, output
    assert "INTERNALERROR" not in output, output
