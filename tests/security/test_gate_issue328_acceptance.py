"""Full #328 family: exact delimiter boundaries, named descriptors, and assignment tildes."""
import pytest

from exo.gate import classify, decide, normalize_policies


def decision(line, policy=None):
    c = classify({'tool': 'bash', 'args': {'command': line}})
    return c, decide(c, normalize_policies(policy))


@pytest.mark.parametrize('delimiter,end', [("'EOF '", 'EOF '), ('"EOF "', 'EOF '), ("' EOF'", ' EOF'), ('EOF\\ ', 'EOF ')])
def test_exact_spaced_heredoc_terminator_exposes_following_force_push(delimiter, end):
    c, d = decision(f'cat <<{delimiter}\nhi\n{end}\ngit push --force origin main\n{end}')
    assert d.outcome in {'ask', 'deny'}
    assert c.category != 'read'


@pytest.mark.parametrize('redirect', ["{x}<<'EOF'\nF\nEOF", '{x}<<< F'])
def test_named_descriptor_does_not_replace_the_post_stdin(redirect):
    _, d = decision(f'cat notes.md | gh pr comment 5 -R myorg/app --body-file - {redirect}')
    assert d.outcome in {'ask', 'deny'}
    assert d.violations


@pytest.mark.parametrize('line', ['gh api repos/myorg/app/issues/5/comments -f body=~/F',
                                'echo body=~/F | gh pr comment 5 -R myorg/app --body-file -'])
def test_assignment_tilde_body_is_scrubbed_when_the_exposure_is_enabled(line):
    policy = {'gates': {'scopes': {'internal': ['myorg/*']}},
              'posting': {'scrub': {'exposures': ['internal', 'public']}}}
    _, d = decision(line, policy)
    assert any('home path' in v for v in d.violations)
    assert d.outcome in {'ask', 'deny'}
    # Internal scope with public-only scrub is intentionally not a scrub violation.
    policy['posting']['scrub']['exposures'] = ['public']
    _, scoped = decision(line, policy)
    assert not any('home path' in v for v in scoped.violations)
