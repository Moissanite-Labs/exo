"""A read-named tool that replaces text writes the store file it names, as `write_file` does (#651).

`search_replace` and `find_and_replace` read as reads: the store scan skipped a tool whose name carries a read
verb unless the name also carries a fetch-write word (`download`, `export`, #494), so
`mcp__fs__search_replace {"path": "~/.exo/seats/principal.toml", "search": "ask", "replace": "bypass"}` ran
with no ask in every permission mode, and a replace of the seat file could make bypass the seat default. The
name's replace, substitute, patch, edit, update and sed words count as fetch-write words now, and so do
arguments that carry the old text and the new beside the path they name, so a replace of the seat file, a
policy file or config.json is a `store_write` the principal alone answers: it asks by the store floor, observe
mode still enforces it (`no_grant`) and bypass keeps the floor ask. A plain read (`read_file`) of the same
paths stays a read.
Classification and decision only; nothing is executed.
"""
import pytest

from exo import gate
from exo.gate import classify, decide, normalize_policies


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The default ~/.exo store under a temp HOME, with the seat, the policy file and the keys the rows name."""
    h = tmp_path / "home"
    for d, files in {".ssh": ["id_rsa"], ".exo/policies": ["gates.yaml"], ".exo/seats": ["principal.toml"],
                     ".exo/keys": ["seat"]}.items():
        (h / d).mkdir(parents=True)
        for f in files:
            (h / d / f).write_text("")
    monkeypatch.setenv("HOME", str(h))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "CLAUDE_CONFIG_DIR", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    return h


def pol(**gates):
    return normalize_policies({"gates": gates})


@pytest.mark.parametrize('tool,args,path', [
    # the seat file, snake and camel spellings: a replace that turns the seat's default into bypass must ask
    ('mcp__fs__search_replace', {'path': '~/.exo/seats/principal.toml', 'search': 'ask', 'replace': 'bypass'},
     '~/.exo/seats/principal.toml'),
    ('mcp__fs__searchReplace', {'path': '~/.exo/seats/principal.toml', 'search': 'ask', 'replace': 'bypass'},
     '~/.exo/seats/principal.toml'),
    # a policy file, snake and camel
    ('search_replace', {'path': '~/.exo/policies/gates.yaml', 'search': 'ask', 'replace': 'bypass'},
     '~/.exo/policies/gates.yaml'),
    ('find_and_replace_file', {'path': '~/.exo/policies/gates.yaml', 'old_string': 'ask', 'new_string': 'bypass'},
     '~/.exo/policies/gates.yaml'),
    # config.json, snake and camel
    ('find_and_replace', {'path': '~/.exo/config.json', 'old_string': '"ask"', 'new_string': '"bypass"'},
     '~/.exo/config.json'),
    ('findAndReplace', {'path': '~/.exo/config.json', 'oldString': '"ask"', 'newString': '"bypass"'},
     '~/.exo/config.json'),
    # a name with no replace word at all: the old and new text in the arguments are the replace signal (#651)
    ('mcp__notes__transform', {'path': '~/.exo/seats/principal.toml', 'old_string': 'ask',
                               'new_string': 'bypass'}, '~/.exo/seats/principal.toml'),
])
def test_a_read_named_tool_that_replaces_a_store_file_writes_it(tool, args, path):
    c = classify({'tool': tool, 'args': args})
    assert c.category == 'store_write' and c.target == path, c
    assert decide(c).outcome == 'ask'                       # the store floor, as write_file asks
    assert gate.no_grant(c)                                 # observe mode still enforces it (D-033, D-038)
    assert decide(c, pol(categories={'store_write': 'deny'})).outcome == 'deny'
    # the tool's own reading stays a part, so its own policy entries still hold (#448)
    assert any(p.category in ('read', 'unknown') and p.target == path for p in c.parts), c


@pytest.mark.parametrize('tool,args', [
    ('search_notes', {'path': '~/.exo/policies/gates.yaml'}),
    ('find_notes', {'path': '~/.exo/seats/principal.toml'}),
    ('read_file', {'path': '~/.exo/config.json'}),
])
def test_a_read_with_no_replace_word_keeps_the_plain_read_of_the_store(tool, args):
    """A read-named tool with no replace word in its name and no old and new text keeps the plain read of the
    store, as before (#494, #651)."""
    c = classify({'tool': tool, 'args': args})
    assert c.category == 'read' and decide(c).outcome == 'allow' and not gate.no_grant(c), c


@pytest.mark.parametrize('tool', ['search_replace', 'find_and_replace', 'mcp__fs__searchReplace'])
def test_a_replace_of_a_plain_path_is_no_store_write(tool):
    """Outside the store the replace is not the gate's ask: #494's read-verb scan lifts store paths only, as it
    does for `download_file`, so an everyday `search_replace notes.md` keeps the read (#651)."""
    c = classify({'tool': tool, 'args': {'path': 'notes.md', 'search': 'a', 'replace': 'b'}})
    assert c.category == 'read' and not gate.no_grant(c), c


@pytest.mark.parametrize('path', ['~/.exo/seats/principal.toml', '~/.exo/policies/gates.yaml', '~/.exo/config.json'])
def test_a_plain_read_of_the_store_stays_a_read(path):
    """The guard the fix must not move: read_file of the same paths is a read, as before (#651)."""
    c = classify({'tool': 'read_file', 'args': {'path': path}})
    assert c.category == 'read' and c.parts == [] and c.floor is None, c
    assert decide(c).outcome == 'allow' and not gate.no_grant(c), c


def test_a_read_of_a_store_path_with_one_half_of_a_pair_stays_a_read():
    """A lone `search` argument names no replacement: only the old and new text together are the signal (#651)."""
    c = classify({'tool': 'read_file', 'args': {'path': '~/.exo/policies/gates.yaml', 'search': 'ask'}})
    assert c.category == 'read' and decide(c).outcome == 'allow', c
