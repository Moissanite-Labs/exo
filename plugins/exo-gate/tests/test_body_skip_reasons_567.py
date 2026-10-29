
import pytest

from exo_gate.hook import _body_path


@pytest.mark.parametrize('kind,fragment', [('large', '256 KiB'), ('process', 'process')])
def test_skip_reason_is_safe_and_does_not_read_body(tmp_path, kind, fragment):
    path = tmp_path / 'body'
    if kind == 'large':
        path.write_bytes(b'x' * (300 * 1024))
    elif kind == 'directory':
        path.mkdir()
    text = 'relative-file' if kind == 'relative' else '/dev/stdin' if kind == 'process' else str(path)
    reasons = {}
    assert _body_path(text, reasons) is None
    assert fragment in reasons[text]
    assert str(tmp_path) not in reasons[text]


def test_eligible_regular_file_has_no_skip_reason(tmp_path):
    path = tmp_path / 'body'
    path.write_text('body')
    reasons = {}
    assert _body_path(str(path), reasons) == path
    assert reasons == {}


def test_process_symlink_has_the_process_reason(tmp_path):
    path = tmp_path / 'alias'
    path.symlink_to('/dev/stdin')
    reasons = {}
    assert _body_path(str(path), reasons) is None
    assert 'process' in reasons[str(path)]
