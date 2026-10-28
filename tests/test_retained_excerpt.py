from exo.retained_excerpt import excerpt


def test_repeated_log_is_bounded_and_deterministic():
    text = 'prefix\n' + 'ordinary output\n' * 1000 + 'suffix'
    result = excerpt(text, 512)
    assert result is not None and len(result) <= 512
    assert result.startswith('prefix') and result.endswith('suffix')
    assert 'omitted' in result
    assert excerpt(text, 512) == result


def test_preserves_distinct_diagnostics_and_source_labels():
    labels = ['[source external] untrusted', 'ERROR alpha', 'WARNING beta', 'exit code: 3']
    text = 'row\n' * 1000 + '\n'.join(labels + labels) + '\n' + 'row\n' * 1000
    result = excerpt(text, 512)
    assert result is not None
    assert all(label in result for label in labels)
    assert len(result) <= 512


def test_refuses_when_diagnostics_cannot_fit():
    assert excerpt('ERROR ' + 'x' * 600 + '\n' + 'ordinary\n' * 100, 512) is None


def test_does_not_modify_short_input():
    assert excerpt('ERROR short\n', 512) is None


def test_bounds_input_without_discarding_unscanned_tail():
    assert excerpt('x' * 1_048_577, 512) is None


def test_json_diff_unicode_and_malformed_text():
    for text in ['{"x":[' + '1,' * 1000 + ']}', '--- a\n+++ b\n' + '+x\n' * 1000,
                 '\ud800' * 1000, '☃' * 1000]:
        result = excerpt(text, 512)
        assert result is not None and len(result) <= 512
        assert 'omitted' in result


def test_invalid_budget_rejected():
    import pytest
    for budget in [True, 0, 255, 16001, 1.5]:
        with pytest.raises(ValueError):
            excerpt('x' * 1000, budget)


def test_real_result_preserves_pairing_error_trust_and_original():
    from exo.core.types import ToolResult
    from exo.retained_excerpt import excerpt_result
    text = '[source third-party: untrusted]\nERROR distinct\n' + 'log\n' * 1000
    original = ToolResult.of_text('call_exact', text, True)
    view = excerpt_result(original, 512)
    assert view.call_id == original.call_id and view.is_error is True
    assert '[source third-party: untrusted]' in view.text()
    assert 'ERROR distinct' in view.text()
    assert original.text() == text and view is not original


def test_mixed_result_is_not_flattened():
    from exo.core.types import ContentPart, ToolResult
    from exo.retained_excerpt import excerpt_result
    original = ToolResult('call_exact', [ContentPart.of_text('x' * 2000), ContentPart(type='image')])
    assert excerpt_result(original, 512) is None
