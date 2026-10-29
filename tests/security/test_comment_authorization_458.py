import pytest

from exo import gate


def decide(command, pattern):
    policy = gate.normalize_policies({'gates': {'categories': {'force_push': 'deny'},
        'rules': [{'match': {'command': pattern}, 'decision': 'allow'}]}})
    return gate.decide(gate.classify({'tool': 'bash', 'args': {'command': command}}), policy)


@pytest.mark.parametrize('comment', ['# --dry-run', '# allow-this', '# allow-this\ntrue'])
def test_comment_text_cannot_lift_a_category_deny(comment):
    pattern = '--dry-run' if '--dry-run' in comment else 'allow-this'
    assert decide('git push --force origin main ' + comment, pattern).outcome == 'deny'


def test_real_argument_can_still_match_authorization():
    assert decide('git push --force --dry-run origin main', '--dry-run').outcome == 'allow'


def test_quoted_hash_is_not_a_comment():
    text = 'echo "# literal"'
    assert gate._without_plain_comments(text) == text


def test_dynamic_or_nested_text_is_explicitly_outside_this_slice():
    text = 'echo $(printf x) # text'
    assert gate._without_plain_comments(text) == text
