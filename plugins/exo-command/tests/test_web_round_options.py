import pytest

from test_chat import FakeSource, call, centre


def test_web_send_forwards_round_override_and_omits_default():
    source = FakeSource()
    c = centre(source)
    assert call(c, 'POST', '/api/chat/sessions/s1/send', {'text': 'hello', 'max_rounds': 0})[0] == 200
    assert source.calls[-1].max_rounds == 0
    assert call(c, 'POST', '/api/chat/sessions/s1/send', {'text': 'hello'})[0] == 200
    assert source.calls[-1].max_rounds is None
    assert call(c, 'POST', '/api/chat/sessions/s1/send', {'text': 'hello', 'max_rounds': None})[0] == 200
    assert source.calls[-1].max_rounds is None


@pytest.mark.parametrize('limit', [-1, True, '0', 1.5])
def test_web_rejects_invalid_round_override(limit):
    source = FakeSource()
    assert call(centre(source), 'POST', '/api/chat/sessions/s1/send', {'text': 'hello', 'max_rounds': limit})[0] == 400
    assert source.calls == []


def test_web_rejects_override_on_steer_and_preserves_core_forbidden():
    source = FakeSource()
    c = centre(source)
    assert call(c, 'POST', '/api/chat/sessions/s1/send', {'text': 'hello', 'mode': 'steer', 'max_rounds': 0})[0] == 400
    assert source.calls == []
    denied = centre(FakeSource(fail={'turn.send': 'forbidden'}))
    assert call(denied, 'POST', '/api/chat/sessions/s1/send', {'text': 'hello', 'max_rounds': 0})[0] == 403
