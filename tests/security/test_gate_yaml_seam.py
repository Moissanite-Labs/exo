"""The extracted parser keeps the legacy import and exception contract."""
import pickle

import pytest

from exo import gate, gate_yaml


@pytest.mark.parametrize("text,expected", [
    ("", None),
    ("deny: [delete, force_push]\n", {"deny": ["delete", "force_push"]}),
    ('key: {nested: [1, true, null, "two"]}\n', {"key": {"nested": [1, True, None, "two"]}}),
    ("message: |-\n  a\n  b\n", {"message": "a\nb"}),
    ("key: 'literal # and ''quote''' # comment\n", {"key": "literal # and 'quote'"}),
])
def test_legacy_parser_values(text, expected):
    assert gate.parse_yaml(text) == expected
    assert gate.parse_yaml is gate_yaml.parse_yaml


@pytest.mark.parametrize("text", ["x: [one", "x: {one}", "x: [one] trailing", "  x: y\nz: q\n"])
def test_legacy_policy_error_identity_and_serialization(text):
    assert gate.PolicyError is gate_yaml.PolicyError
    with pytest.raises(gate.PolicyError) as caught:
        gate.parse_yaml(text)
    restored = pickle.loads(pickle.dumps(caught.value))
    assert isinstance(restored, gate.PolicyError)
    assert str(restored) == str(caught.value)
