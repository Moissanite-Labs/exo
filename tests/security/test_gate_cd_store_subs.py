"""Issue #465: after `cd` into the store, a write inside a $( ), backticks or <( ) on the line is a store write.

The `cd` context (store_cwd, fd_cwd) lived in one line's classification only, and each $( ) was classified as a
fresh line, so `cd ~/.exo/policies && echo $(cp /tmp/evil gates.yaml)` was an allowed read while the unwrapped
`cp` asked. Every command string here is data for exo.gate.classify/decide; none is run."""
import pytest

from exo import gate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "policies").mkdir(parents=True)
    (home / ".exo" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _bash(cmd):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


# The issue's rows: read allow (local_write allow for the last) on main 3ddd89e9.
ROWS = [
    "cd ~/.exo/policies && echo $(cp /tmp/evil gates.yaml)",
    "cd ~/.exo/policies && x=$(cp /tmp/evil gates.yaml)",
    "cd ~/.exo/policies && cat <(cp /tmp/evil gates.yaml)",
    "cd ~/.exo/policies && echo `cp /tmp/evil gates.yaml`",
    'cd ~/.exo/state && echo "$(cp /tmp/g.json approval-grants.json)"',
    "cd ~/.exo/policies && echo $(echo x > gates.yaml)",
    # nested, and other places a substitution runs
    "cd ~/.exo/policies && echo $(echo $(cp /tmp/evil gates.yaml))",
    "cd ~/.exo/policies && diff <(cp /tmp/evil gates.yaml) x",
    "cd ~/.exo/policies; echo $(cp /tmp/evil gates.yaml)",
    "pushd ~/.exo/state && echo $(cp /tmp/g.json approval-grants.json)",
]


@pytest.mark.parametrize("line", ROWS)
def test_a_write_in_a_substitution_after_cd_into_the_store_asks_the_principal(line):
    c = _bash(line)
    assert c.category == "store_write", (line, c.category)
    d = gate.decide(c)
    assert (d.outcome, d.rule) == ("ask", "floor"), (line, d)
    lifted = gate.normalize_policies({"gates": {"categories": {"store_write": "allow", "local_write": "allow"}}})
    assert gate.decide(c, lifted).outcome == "ask", line             # no policy lowers the store floor
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"store_write": ["seat:lead"]}}) \
        == ["principal"]
    assert gate.decide(c, gate.normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny", line


@pytest.mark.parametrize("line", ["cd /dev && echo $(cp /tmp/evil fd/3)", "cd /proc/self/fd && x=$(cp /tmp/evil 3)"])
def test_a_substitution_after_cd_into_a_descriptor_folder_is_a_store_write_there(line):
    c = _bash(line)
    assert c.category == "store_write", (line, c)
    assert any(p.category == "store_write" and p.target in ("/dev", "/proc/self/fd") for p in gate._each_part(c)), c
    d = gate.decide(c, gate.normalize_policies({"gates": {"deny": ["target:/dev*", "target:/proc*"]}}))
    assert d.outcome == "deny", (line, d)


# #320's fail-closed cost (D-038 amendment 2026-10-22): a word that is a substitution alone is read as `*`, any name its
# output may be, and on a line that enters a store folder `*` could name a store file there, as `cd ~/.exo/policies
# && echo *` is credentials on main. The reads in the substitution stay reads: none is a store write.
_STAR = "credentials"


@pytest.mark.parametrize("line,cat", [
    ("cd ~/.exo/policies && echo $(ls)", _STAR),                      # reads in the store are no store writes
    ("cd ~/.exo/policies && echo $(cat gates.yaml)", _STAR),
    ("cd ~/.exo/policies && git log -n1 $(git rev-parse HEAD)", _STAR),
    ("cd ~/.exo/policies && echo \"$(ls) files\"", "read"),           # not a word of its own: stays a read
    ("cd ~/project && echo $(cp a b)", "read"),                       # not a store folder
    ("echo $(cp a b)", "read"),
    ('git commit -m "$(date)"', "local_write"),
])
def test_controls_keep_their_class(line, cat):
    c = _bash(line)
    assert c.category == cat, line
    assert not any(p.category == "store_write" for p in gate._each_part(c)), line


def test_a_substitution_text_read_both_outside_and_inside_the_store_on_one_line():
    # within one call each text is classified once per depth (#334): the same $( ) at the same depth, first outside
    # the store and then in it, is read again there, and the first reading does not stand in for the second
    c = _bash("bash -c 'echo $(cp a b)'; bash -c 'cd ~/.exo/policies && echo $(cp a b)'")
    assert c.category == "store_write", c
    assert _bash("bash -c 'echo $(cp a b)'; bash -c 'cd ~/project && echo $(cp a b)'").category != "store_write"


def test_a_substitution_runs_where_its_command_runs():
    # bash runs a $( ) where its command runs: in the `cd` itself or before it, the line has not entered the store
    for line in ("cd ~/.exo/policies/$(cp a b)",
                 "cd ~/.exo/policies <<< $(cp a b)",              # its redirections expand before it runs
                 "cd /proc/$(docker inspect -f '{{.State.Pid}}' web) && cat status",
                 # bash expands a compound's own redirections before the compound runs
                 "while read -r f; do cd ~/.exo/state && cat \"$f\"; done < <(sort list.txt)",
                 "while read -r f; do cd ~/.exo/state && cat \"$f\"; done <<< \"$(sort list.txt)\"",
                 "{ cd ~/.exo/state; ls; } <<< \"$(sort list.txt)\"",
                 "if cd ~/.exo/state; then ls; fi < <(sort list.txt)",
                 "( cd ~/.exo/state; ls ) < <(sort list.txt)",
                 "while read -r p; do cd /proc/$p && cat status; done < <(ps -eo pid= | sort -n)"):
        assert _bash(line).category == "read", line
    # #320's cost: the output of one before the `cd`, a word or a value of its own, is `*`, which D-038 reads as a
    # name in the folder the line enters wherever it stands, as main reads `echo *; cd ~/.exo/policies` and `V=*; cd
    # ~/.exo && ls` (the value may be used after the `cd`). Its commands still run outside: none is a store write.
    for line in ("echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies",
                 "pid=$(docker inspect -f '{{.State.Pid}}' web); cd /proc/$pid && cat status",
                 "V=$(sed -n 1p VERSION); cd ~/.exo && ls"):
        c = _bash(line)
        assert c.category == _STAR, line
        assert not any(p.category == "store_write" for p in gate._each_part(c)), line
    # a here-doc body on an earlier line runs with its command, before the `cd`
    for line in ("cat <<EOF > notes.txt\n$(cp a b)\nEOF\ncd ~/.exo/policies && ls",
                 "cat > /tmp/x <<EOF &&\n$(cp a b)\nEOF\ncd ~/.exo/state && ls"):
        assert _bash(line).category != "store_write", line
    # after such a compound, in its body after the `cd`, and in a later compound's redirections, it runs in the store
    for line in ("while read -r f; do cd ~/.exo/state; done < <(sort list.txt); echo $(cp a b)",
                 "while read -r f; do cd ~/.exo/state; echo $(cp a b); done < <(sort list.txt)",
                 "while read l; do cd ~/.exo/state; cat <<EOF\n$(cp a b)\nEOF\ndone < <(sort x)",
                 "cd ~/.exo/state; { ls; } < <(sort list.txt)"):
        assert _bash(line).category == "store_write", line
    # the same text before and after the `cd` is read in each place, and a subshell fails closed, as for the line's
    # own commands. A here-doc body counts as after every command on its line, also one before the `cd`
    for line in ("echo $(cp a b); cd ~/.exo/policies; echo $(cp a b)",
                 "(cd ~/.exo/policies) && echo $(cp /tmp/evil gates.yaml)",
                 "cat <<EOF; cd ~/.exo/policies\n$(cp /tmp/evil gates.yaml)\nEOF"):
        assert _bash(line).category == "store_write", line


def test_a_copy_of_the_substitution_text_before_the_cd_does_not_turn_the_store_off():
    # the place of a $( ) in the line decides, not its text: a copy of it before the `cd` once matched it, as a
    # here-doc body is not a command's own text, and bash takes backslashes out of a backtick's text (#468 review)
    pol = gate.normalize_policies({"gates": {"deny": ["store_write"]}})
    for line in ("echo cp /tmp/evil gates.yaml; cd ~/.exo/policies && cat <<EOF\n$(cp /tmp/evil gates.yaml)\nEOF",
                 "echo cp /tmp/evil gates.yaml; cd ~/.exo/policies && cat <<EOF\n`cp /tmp/evil gates.yaml`\nEOF",
                 "echo cp /tmp/evil gates.yaml; cd ~/.exo/policies && { cat; } <<EOF\n$(cp /tmp/evil gates.yaml)\nEOF",
                 "echo cp /tmp/evil gates.yaml; cd ~/.exo/policies && cat <<-EOF\n\t$(cp /tmp/evil gates.yaml)\n\tEOF",
                 "echo $(cp /tmp/evil gates.yaml); cd ~/.exo/policies && cat <<EOF\n$(cp /tmp/evil gates.yaml)\nEOF",
                 r"echo cp /tmp/evil gates\.yaml; cd ~/.exo/policies && echo `cp /tmp/evil gates\\.yaml`",
                 r"echo cp /tmp/evil gates\.yaml; cd ~/.exo/policies && x=`cp /tmp/evil gates\\.yaml`",
                 r'echo cp /tmp/evil gates\.yaml; cd ~/.exo/policies && echo "`cp /tmp/evil gates\\.yaml`"',
                 r"echo cp /tmp/evil \$x; cd ~/.exo/policies && echo `cp /tmp/evil \\$x`"):
        c = _bash(line)
        assert c.category == "store_write", (line, c.category)
        assert gate.decide(c, pol).outcome == "deny", line
    dev = gate.normalize_policies({"gates": {"deny": ["target:/dev*"]}})
    for line in ("echo cp /tmp/evil fd/3; cd /dev && cat <<EOF\n$(cp /tmp/evil fd/3)\nEOF",
                 r"echo cp /tmp/evil fd\/3; cd /dev && echo `cp /tmp/evil fd\\/3`"):
        assert gate.decide(_bash(line), dev).outcome == "deny", line


def test_a_line_nested_in_a_substitution_keeps_its_own_folder():
    # an ssh remote command runs on the host, not in the local store folder; a local shell still runs there
    for line in ("cd ~/.exo/policies && diff gates.yaml <(ssh prod 'cat /etc/exo/gates.yaml | sort')",
                 "cd ~/.exo && diff a <(ssh prod 'ls /srv; sort /srv/x')",
                 "cd ~/.exo && ssh prod 'cd /srv && echo $(ls)'"):
        assert _bash(line).category == "read", line
    # #320's cost: the local `echo` word that is the substitution alone is `*` in the store folder; the remote
    # commands keep their own folder, so none is a store write
    c = _bash('cd ~/.exo && echo "$(ssh prod \'ls /srv; sort /srv/x\')"')
    assert c.category == _STAR, c
    assert not any(p.category == "store_write" for p in gate._each_part(c)), c
    assert _bash("cd ~/.exo/policies && echo $(bash -c 'cp /tmp/evil gates.yaml')").category == "store_write"


@pytest.mark.parametrize("line", ["cd ~/.exo/policies && declare -a x='($(cp /tmp/evil gates.yaml))'",
                                  "cd ~/.exo/policies && local -a x='(`cp /tmp/evil gates.yaml`)'"])
def test_a_value_bash_runs_again_after_cd_into_the_store_runs_there(line):
    c = _bash(line)
    assert c.category == "store_write", (line, c)
    assert gate.decide(c, gate.normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny"
    assert _bash("declare -a x='($(cp /tmp/evil gates.yaml))'; cd ~/.exo/policies").category != "store_write"


def test_a_command_in_a_substitution_keeps_the_decision_it_had_as_the_line_s_command():
    # with the substitution a store write, the push and the delete inside it are parts beside it: a deny they got
    # on main as the line's dominant command still holds (needs the #464 part pass, and the two checks below)
    gates = {"matrix": {"irreversible": {"private": "deny", "internal": "deny"}},
             "rules": [{"match": {"command": "^git push"}, "decision": "allow"}]}
    pol = gate.normalize_policies({"gates": gates})
    assert gate.decide(_bash("git push --force origin main"), pol).outcome == "allow"     # the rule names it alone
    # an anchored rule matches no compound line (#394), so it does not lift the push inside the line either
    for line in ("echo $(git push --force origin main)", "cd ~/.exo/policies && echo $(git push --force origin main)"):
        assert gate.decide(_bash(line), pol).outcome == "deny", line
    # a secret line takes the weight of an irreversible command, also one held by a store write in a $( ): the line
    # from its own parts, and a heavier one deeper in it as a credentials part beside it
    pol = gate.normalize_policies({"gates": {"matrix": {"irreversible": {"private": "deny"}}, "categories": {"delete": "allow"}}})
    for line in ("cd ~/.exo/keys && rm -f x", "cd ~/.exo/keys && echo $(rm -f x)"):
        c = _bash(line)
        assert c.category == "credentials" and gate.decide(c, pol).outcome == "deny", line
        assert any(p.category == "credentials" and p.reversibility == "irreversible" for p in [c, *c.parts]), c


@pytest.mark.parametrize("policy", [
    {"matrix": {"costly": {"private": "deny"}, "irreversible": {"private": "ask"}}},
    {"matrix": {"costly": {"private": "deny"}, "irreversible": {"private": "allow"}}},
    {"rules": [{"match": {"reversibility": "irreversible"}, "decision": "allow"}], "matrix": {"costly": {"private": "deny"}}},
])
@pytest.mark.parametrize("line", ["cat ~/.ssh/id_rsa; ssh prod 'rm -rf /'", "echo $(ssh prod 'rm -rf /'); cd ~/.exo/keys",
                                  "x=$(ssh prod 'rm -rf /'); cd ~/.exo/keys", "cat <(ssh prod 'rm -rf /'); cd ~/.exo/keys",
                                  "echo $(cat ~/.ssh/id_rsa); ssh prod 'rm -rf /'"])   # a secret part of its own
def test_a_deeper_irreversible_command_does_not_lift_the_secret_line_s_own_cell(policy, line):
    # #468 review: taking the line's weight from every part made it irreversible, so a policy whose costly cell is
    # stricter than its irreversible one asked where main denied. The line keeps the weight of its own parts.
    c = _bash(line)
    assert c.category == "credentials" and c.reversibility == "costly", (line, c)
    assert gate.decide(c, gate.normalize_policies({"gates": policy})).outcome == "deny", (line, policy)


@pytest.mark.parametrize("policy", [
    {"rules": [{"match": {"category": "credentials", "reversibility": "irreversible"}, "decision": "deny"}]},
    {"matrix": {"irreversible": {"private": "deny"}}},
    {"matrix": {"irreversible": {"private": "deny"}}, "categories": {"delete": "allow"}},
    {"rules": [{"match": {"target": "*keys*", "reversibility": "irreversible"}, "decision": "deny"}]},
])
@pytest.mark.parametrize("line", ["cd ~/.exo/state && cat ../keys/seat; echo $(rm -rf x)",
                                  "cd ~/.exo/state && cat ../keys/seat; echo `rm -rf x`",
                                  "cd ~/.exo/state && cat ../keys/seat; diff <(rm -rf x) y",
                                  "cd ~/.exo/state && cat ../keys/seat; echo $(git push --force origin main)",
                                  "cd ~/.exo/state && echo $(cat ../keys/seat; rm -rf x)",
                                  "cd ~/.exo/state && x=$(cat ../keys/seat) && echo $(rm -rf x)",
                                  "cd ~/.exo/state && cat ../keys/seat; git push --force origin main"])
def test_a_relative_key_read_on_a_store_write_line_weighs_as_a_secret_line(policy, line):
    # #468 review: the key read beside a store write was a credentials part at the default weight, so a deny on an
    # irreversible command in a $( ) on such a line asked, where main denied. It weighs as a secret line now.
    c = _bash(line)
    assert c.category == "store_write", (line, c.category)
    assert gate.decide(c, gate.normalize_policies({"gates": policy})).outcome == "deny", (line, policy)
