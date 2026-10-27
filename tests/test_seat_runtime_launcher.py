"""Manual launcher uses fake executables only; never touches a core or terminal UI."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run-seat-runtime.sh'


def test_manual_launcher_preserves_seat_args_cwd_and_restart_target(tmp_path):
    fake = tmp_path / 'fake runtime'
    fake.write_text('#!' + sys.executable + '\nimport json,os,sys\nprint(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd(),"restart":os.environ["EXO_RESTART_CMD"]}))\n')
    fake.chmod(0o755)
    folder = tmp_path / "work folder's files"
    folder.mkdir()
    wrapper = str(tmp_path / 'manual launcher')
    env = {'PATH': os.defpath, 'HOME': str(tmp_path), 'EXO_RUNTIME_EXO': str(fake),
           'EXO_SEAT': 'canary', 'EXO_RESTART_LAUNCHER': wrapper}
    for args, expected in (([str(folder), '--session', 'fake-session'], ['--session', 'fake-session']),
                           (['resume', str(folder)], ['--resume']),
                           (['--resume'], ['--resume'])):
        proc = subprocess.run(['bash', str(SCRIPT), *args], cwd=folder, env=env,
                              capture_output=True, text=True, check=True)
        result = json.loads(proc.stdout)
        assert result['args'] == ['tui', '--seat', 'canary', *expected]
        assert Path(result['cwd']) == folder
        assert shlex.split(result['restart']) == [wrapper, str(folder)]


def test_missing_runtime_fails_before_launch(tmp_path):
    proc = subprocess.run(['bash', str(SCRIPT)], env={'PATH': os.defpath, 'HOME': str(tmp_path),
                          'EXO_RUNTIME_EXO': str(tmp_path / 'missing'), 'EXO_SEAT': 'canary'}, capture_output=True)
    assert proc.returncode == 72 and not proc.stdout
