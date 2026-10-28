"""Path aliases and nested operations retain secret/store floors. Tool calls are never executed."""
import pytest
from exo import gate


@pytest.mark.parametrize('tool,args', [
    ('write_file', {'filePath': '~/.exo/policies/gates.yaml'}),
    ('write_file', {'relative_path': '.exo/policies/gates.yaml'}),
    ('move_file', {'sourcePath': '~/.exo', 'destinationPath': '/tmp/x'}),
    ('move_file', {'source': '/tmp/x', 'targetPath': '~/.exo/policies/gates.yaml'}),
    ('move_file', {'source': '/tmp/x', 'newPath': '~/.exo/policies/gates.yaml'}),
    ('mcp__filesystem__create_file', {'relative_path': '.exo/policies/gates.yaml'}),
    ('download_file', {'url': 'https://example.invalid/x', 'save_path': '~/.exo/policies/gates.yaml'}),
    ('export_report', {'output_path': '~/.exo/policies/gates.yaml'}),
    ('rename_file', {'new_name': '~/.exo/policies/gates.yaml'}),
    ('move_file', {'from_path': '~/.exo', 'to_path': '/tmp/x'}),
    ('move_file', {'operations': [{'source': '~/.exo', 'destination': '/tmp/x'}]}),
    ('write_file', {'operations': [{'file-path': '~/.exo/policies/gates.yaml'}]}),
])
def test_path_spellings_keep_store_floor(tool, args, tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.delenv('EXO_HOME', raising=False)
    c = gate.classify({'tool': tool, 'args': args}, cwd=str(tmp_path))
    assert gate.decide(c, {'gates': {'deny': ['store_write']}}).outcome == 'deny'


def test_nested_secret_read_asks_but_store_read_stays_read():
    c = gate.classify({'tool': 'read_file', 'args': {'operations': [{'filePath': '~/.ssh/id_rsa'}]}})
    assert gate.decide(c).outcome == 'ask'
    c = gate.classify({'tool': 'read_file', 'args': {'filePath': '~/.exo/policies/gates.yaml'}})
    assert c.category == 'read'


def test_content_text_is_not_a_path_argument():
    c = gate.classify({'tool': 'write_file', 'args': {'path': '/tmp/out', 'content': '~/.exo/policies/gates.yaml'}})
    assert c.category == 'local_write'
    c = gate.classify({'tool': 'write_file', 'args': {'path': '/tmp/out', 'source': '~/.exo/policies/gates.yaml'}})
    assert c.category == 'local_write'


def test_shared_container_keeps_its_path_context():
    shared = ['~/.exo/policies/gates.yaml']
    c = gate.classify({'tool': 'write_file', 'args': {'save_path': shared, 'metadata': shared}})
    assert gate.decide(c, {'gates': {'deny': ['store_write']}}).outcome == 'deny'


def test_new_destination_keeps_the_prior_secret_source_reserved_part():
    c = gate.classify({'tool': 'apply_patch', 'args': {'source': '~/.ssh/id_rsa',
                                                    'destination': '~/.exo/policies/gates.yaml'}})
    d = gate.decide(c, {'gates': {'reserved': ['target:*/.ssh/*']}})
    assert ('local_write', '~/.ssh/id_rsa') in [tuple(hit) for hit in d.reserved]


def test_deep_and_cyclic_argument_containers_do_not_hide_a_store_path():
    args = {'filePath': '~/.exo/policies/gates.yaml'}
    for _ in range(1200):
        args = {'operations': [args]}
    args['cycle'] = args
    c = gate.classify({'tool': 'write_file', 'args': args})
    assert gate.decide(c, {'gates': {'deny': ['store_write']}}).outcome == 'deny'
