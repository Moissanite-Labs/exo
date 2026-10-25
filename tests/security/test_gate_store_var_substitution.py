"""#342: a pattern substitution on a store variable can add a store folder the plain value does not spell.

`${HOME/%//.kube}` is `$HOME/.kube` in bash: the `%` pattern matches at the end of the value and the
replacement is appended. The gate does not work out what a pattern matches, so a `/` substitution of a store
variable whose replacement is not empty fails closed: it asks as a store read or a store write would. One with
an empty replacement only removes text and reads as the value, as before. Every check classifies. Nothing is run."""
import time

import pytest

from exo import gate

POL = gate.DEFAULT_POLICIES
STORE = {"gates": {"categories": {"store_write": "allow"}}}
DENY = {"gates": {"categories": {"credentials": "deny"}}}
FILL = [f"p{i}" + "{a,b}" * 10 for i in range(3)] + ["q{1..1021}"]   # spends most of the call's brace budget


def outcome(command, policy=POL):
    c = gate.classify({"tool": "bash", "args": {"command": command}})
    return c.category, gate.decide(c, policy).outcome


def tool(name, path, policy=POL):
    c = gate.classify({"tool": name, "args": {"path": path, "content": "x"}})
    return c.category, gate.decide(c, policy).outcome


@pytest.fixture(params=[None, "/srv/exo-home"], ids=["EXO_HOME unset", "EXO_HOME set"])
def env(request, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EXO_PRINCIPAL_STORE", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    if request.param:
        monkeypatch.setenv("EXO_HOME", request.param)
    else:
        monkeypatch.delenv("EXO_HOME", raising=False)
    return request.param


def nest(form, levels):
    return "${EXO_PRINCIPAL_STORE:-" * levels + form + "}" * levels


@pytest.mark.parametrize("line", [
    "cat ${HOME/%//.kube}/config",
    "cat ${HOME/%//.config}/gh/hosts.yml",
    "cat ${HOME/%//.n}etrc",
    "cat ${HOME/%//.exo}/keys/seat",
    "cat ${EXO_HOME/%//keys}/seat",
    "cd ${HOME/%//.kube} && cat config",
    "cat ${HOME/$HOME/.kube/config}",
    "cat ${HOME/$HOME/.kube}/config",
    "cat ${HOME/#$HOME/~}/.kube/config",
    "ls ${EXO_HOME/%//run}",
    # a `#*` or `?*` pattern matches the whole value, so bash gives the replacement alone
    "cat ${HOME/#*/~/.kube}/config",
    "cat ${HOME/#*/~/.exo}/keys/seat",
    "cat ${HOME/#*/~/.config}/gh/hosts.yml",
    "cat ${HOME/#?*/~/.exo}/keys/seat",
    "cat ${HOME/#[/]*/~/.exo}/keys/seat",
    "cd ${HOME/*/~/.exo} && cat keys/seat",
    # a pattern that does not match leaves the value as it is (#421 review)
    "cat ${EXO_HOME/%x/y}/keys/seat",
    "cat ${EXO_HOME/%nomatch/zzz}/keys/seat",
    "cat ${EXO_HOME/#x/y}/keys/seat",
    "cat ${EXO_HOME//%/x}/keys/seat",
    "cat ${EXO_HOME/%[0-9]/x}/keys/seat",
    "cat ${EXO_HOME/%*/&}/keys/seat",
    "ls ${EXO_HOME/%x/y}/keys",
    "cd ${EXO_HOME/%x/y} && cat keys/seat",
    # a subscript, a backslash, `$'..'`, backticks or another form in the replacement, and nesting
    "cat ${HOME[0]/%//.exo}/keys/seat",
    "cat ${HOME[@]/%//.exo}/keys/seat",
    "cat ${HOME/%//.e\\xo}/keys/seat",
    "cat ${HOME/%/$'\\x2f.exo'}/keys/seat",
    "cat ${EXO_HOME/-home/-home\\/keys}/seat",      # a partial match
    "cat ${HOME/%/${x:-/.exo}}/keys/seat",
    "cat ${x:-${HOME/%//.exo}}/keys/seat",
    "cat " + nest("${EXO_HOME/%nomatch/y}", 8) + "/keys/seat",
    "cat " + nest("${EXO_HOME/%nomatch/y}", 20) + "/keys/seat",
    "bash -c 'cat ${HOME/%//.kube}/config'",
    "echo $(cat ${HOME/%//.kube}/config)",
    # a quoted `}` or one in a `$( )` does not end the form in bash, so where it ends is not known
    "cat ${HOME/%[!\"}\"]/u\\/.exo}/keys/seat",
    "cat \"${HOME/%[!'}']/u\\/.exo}/keys/seat\"",
    "cat ${HOME/%<(printf })/\\/.exo}/keys/seat",
    "cat ${HOME[$(printf '}' >/dev/null; printf 0)]/%//.exo}/keys/seat",
    # a `[` in the subscript hides its end: bash gives `$HOME/.exo`
    "cat ${HOME[a[0]]/%//.exo}/keys/seat",
    # a brace set builds the form: a `$` that ends one of its words starts it
    "cat {$,}{HOME/%//.exo}/keys/seat",
    "cat {x,$}{HOME/%//.kube}/config",
    "cd {$,x}{HOME/*/~/.exo} && cat keys/seat",
    "echo $(cat {$,}{HOME/%//.exo}/keys/seat)",
    # bash drops a line continuation before it reads the form
    "cat ${HOME\\\n/%//.exo}/keys/seat",
    "cat $\\\n{HOME/%//.exo}/keys/seat",
])
def test_a_substitution_spelling_of_the_store_asks(env, line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cat ${HOME}/.kube/config",
    "cat ${HOME%/}/.kube/config",
    "cat ${HOME//x/y}/.kube/config",
    "cat ${HOME/%//.ssh}/id_rsa",
    "cat ${HOME:0:2}/.kube/config",            # operators that only remove text still read as the value
    "cat ${HOME##*/}/.kube/config",
    "cat ${HOME/x}/.kube/config",              # so does a substitution with an empty replacement
    "cat ${HOME/%x/}/.kube/config",
    "cat ${EXO_HOME/%nomatch}/keys/seat",
    "cat " + nest("${EXO_HOME/%nomatch}", 8) + "/keys/seat",
    "tar czf - ${EXO_HOME/%x/y}/keys",
])
def test_controls_keep_asking(env, line):
    assert outcome(line) == ("credentials", "ask")


@pytest.mark.parametrize("line", [
    "cp x ${HOME/%//.exo}/policies/default.yaml",
    "cp x ${EXO_HOME/%x/y}/policies/default.yaml",
    "cp x ${EXO_HOME/%nomatch/zzz}/policies/a.yaml",
    "cp x ${EXO_HOME/%home/home}/policies/a.yaml",
    "cp x ${HOME/#*/~/.exo}/policies/default.yaml",
    "cp x ${HOME/*/~/.exo}/policies/default.yaml",
    "cp x ${HOME/*/&/.exo}/policies/default.yaml",
    "tee ${HOME/*/~/.exo}/policies/default.yaml",
    "cp x ${HOME[0]/%//.exo}/policies/default.yaml",
    "cp x ${HOME/%//.e\\xo}/policies/default.yaml",
    "echo x > ${HOME/#*/~/.exo}/policies/default.yaml",
    "cd ${HOME/*/~/.exo} && cp x policies/default.yaml",
    "tar czf - ${HOME/#*/~/.exo}/keys",
    "cp x ${HOME/%[!\"}\"]/u\\/.exo}/policies/default.yaml",
    "cp x ${EXO_HOME/%x/y}/plugins/foo/__init__.py",       # the plugins folder is a store folder (#435)
    "cp x ${HOME/%//.exo}/plugins/foo/__init__.py",
    "cp x ${HOME[a[0]]/%//.exo}/policies/default.yaml",
    "cp x {$,x}{HOME/%//.exo}/policies/default.yaml",
    "cd {$,x}{HOME/*/~/.exo} && cp x policies/default.yaml",
    "cp x ${HOME\\\n/%//.exo}/policies/default.yaml",
])
def test_a_substituted_store_write_asks(env, line):
    assert outcome(line) == ("store_write", "ask")


@pytest.mark.parametrize("line", [
    "gh pr comment 1 -R o/r --body-file ${EXO_HOME/%x/y}/keys/seat",
    "gh -R o/r pr comment 1 --body-file ${EXO_HOME/%x/y}/keys/seat",      # read two ways (#399): both ask
    "gh -R o/r pr comment 1 --body-file ${HOME/#*/~/.exo}/keys/seat",
])
def test_a_gh_post_of_a_substituted_store_file_asks(env, line):
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    assert "credentials" in {p.category for x in (c, c.floor) if x for p in gate._each_part(x)}
    assert gate.decide(c, {"gates": {"categories": {"github_post": "allow"}}}).outcome == "ask"


def test_a_store_write_keeps_the_read_beside_it(env):
    """The replacement may name a secret store as well, so a credentials part stands beside the store write, as for a
    relative read after a `cd` into a store (D-038): a policy that allows store writes still asks, and a deny on
    credentials denies it."""
    deny = {"gates": {"categories": {"credentials": "deny"}}}
    for line in ("cp x ${HOME/%//.kube}/config", "cp {$,x}{HOME/%//.kube}/config /tmp/x",
                 "tar czf - {$,x}{HOME/%//.exo}/keys"):
        c = gate.classify({"tool": "bash", "args": {"command": line}})
        assert c.category == "store_write"
        assert "credentials" in {p.category for p in gate._each_part(c)}
        assert gate.decide(c, STORE).outcome == "ask"
        assert gate.decide(c, deny).outcome == "deny"
    assert outcome("rm -rf ${HOME/%//.exo}/policies", STORE)[1] == "ask"


def test_claude_config_dir_reads_the_same_way(env, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert outcome("cp x ${CLAUDE_CONFIG_DIR/%nomatch/z}/settings.json") == ("credentials", "ask")
    assert outcome("cat ${CLAUDE_CONFIG_DIR/#*/~/.kube}/config") == ("credentials", "ask")


def test_the_file_tools_read_the_substitution_too(env):
    for path in ("${HOME/%//.kube}/config", "${HOME/#*/~/.exo}/keys/seat", "${EXO_HOME/%x/y}/keys/seat",
                 "${HOME[0]/%//.exo}/keys/seat", "${HOME/%[!\"}\"]/u\\/.exo}/keys/seat",
                 "${HOME[a[0]]/%//.exo}/keys/seat", "{$,}{HOME/#*/~/.exo}/keys/seat", "${!x/%//.kube}/config"):
        assert tool("read_file", path) == ("credentials", "ask")
    assert tool("grep", "${HOME/%//.kube}") == ("credentials", "ask")
    for path in ("${HOME/%//.exo}/policies/x.yaml", "${EXO_HOME/%x/y}/policies/x.yaml",
                 "${HOME/*/~/.exo}/policies/x.yaml", "${HOME/#*/~/.exo}/policies/x.yaml",
                 "${HOME[a[0]]/%//.exo}/policies/x.yaml", "{$,}{HOME/#*/~/.exo}/policies/x.yaml"):
        assert tool("write_file", path) == ("store_write", "ask")


def test_any_replacement_on_a_store_variable_asks(env):
    """The cost of failing closed: the gate does not read what the pattern matches, so these ask too."""
    assert outcome("cp x ${HOME/%//backup}/a") == ("store_write", "ask")
    assert outcome("cat ${HOME/%//notes}/x.txt") == ("credentials", "ask")
    assert outcome("cat ${HOME//x/y}/notes.txt") == ("credentials", "ask")
    assert outcome("x=PATH; echo ${!x//:/ }") == ("credentials", "ask")   # an indirect name may be one


def test_other_variables_and_removals_keep_their_reading(env):
    assert outcome("echo ${PWD/#$HOME/~}") == ("read", "allow")
    assert outcome("echo ${PATH//:/ }") == ("read", "allow")
    assert outcome("echo ${name/foo/bar}") == ("read", "allow")
    assert outcome("cat ${name/foo/bar}/x") == ("read", "allow")
    assert outcome("cat ${HOME//x}/notes.txt") == ("read", "allow")
    assert outcome("cat ${HOME/%x/}/notes.txt") == ("read", "allow")
    assert outcome("cp x ${HOME%/}/backup/a") == ("shell", "allow")
    assert outcome("cat {$,x}{HOME//x}/notes.txt") == ("read", "allow")
    assert outcome("awk '{print $1,$2}' notes.txt") == ("read", "allow")


@pytest.mark.parametrize("text, form", [
    ("${HOME/%//.kube}/config", "${HOME/%//.kube}"),
    ("${HOME//x/y}", "${HOME//x/y}"),
    ("${HOME///y}", "${HOME///y}"),
    ("${HOME/\\//y}", "${HOME/\\//y}"),
    ("${HOME[$i]/%//.exo}", "${HOME[$i]/%//.exo}"),
    ("${x:-${EXO_HOME/#*/z}}", "${EXO_HOME/#*/z}"),
    ("${HOME/#*$(: })/~/.exo}", "${HOME/#*$(: }"),     # a `$( )` may hide where it ends
    ("${HOME/%[!\"}\"]/x}", "${HOME/%[!\"}"),          # so may a quote, `<( )` or backticks
    ("${HOME/%[!'}']/x}", "${HOME/%[!'}"),
    ("${HOME/%<(: })/x}", "${HOME/%<(: }"),
    ("${HOME/%`:`}", "${HOME/%`:`}"),
    ("${HOME[$(: })]/%//x}", "${HOME[$(: }"),
    ("${HOME[\"0\"]}", "${HOME[\"0\"]}"),
    ("${HOME[a[0]]/%//x}", "${HOME[a[0]]/%//x}"),      # a `[` may hide where the subscript ends
    ("${HOME[a[0]]}", "${HOME[a[0]]}"),
    ("${HOME\\\n/%//x}", "${HOME/%//x}"),              # a line continuation is dropped first
    ("${HOME/%/x", "${HOME/%/x"),                      # not closed on its line
    ("${HOME[0]/%/x", "${HOME[0]/%/x"),
    ("${HOME[0}", ""),
    ("${HOME[0]}", ""),
    ("${HOME[0]:-a/b}", ""),
    ("${HOME/x}", ""),
    ("${HOME/%x/}", ""),
    ("${HOME//}", ""),
    ("${HOME:-a/b}", ""),
    ("${HOME%/x}", ""),
    ("${PWD/#$HOME/~}", ""),
    ("${XDG_CONFIG_HOME/%//gh}", ""),
    ("${!x/%//.exo}/keys", "${!x/%//.exo}"),          # an indirect name may be a store variable
    ("${!x_1//u/v}", "${!x_1//u/v}"),
    ("${!12/#/x}", "${!12/#/x}"),
    ("${!*/%//x}", "${!*/%//x}"),
    ("${!@/%//x}", "${!@/%//x}"),
    ("${!x[0]/%//x}", "${!x[0]/%//x}"),
    ("${!x/%/}", ""),
    ("${!x}", ""),
    ("${!x:-a/b}", ""),
    ("${!x%/y}", ""),
    ("${!HOME*}", ""),
    ("${!a[@]}", ""),
])
def test_which_forms_fail_closed(text, form):
    assert gate._store_subst(text) == form


@pytest.mark.parametrize("words, form", [
    (["{$,}{HOME/%//.exo}/keys"], "${HOME/%//.exo}"),
    (["x", "{x,$}{HOME/#*/~}"], "${HOME/#*/~}"),
    (["{$,\\\n}{HOME/%//x}"], "${HOME/%//x}"),
    (["{$,}{HOME//x}", "{a,b}${HOME}", "'{$,}{HOME/%//.exo}'", "{$,}{PATH/%//x}"], ""),
])
def test_a_brace_set_can_build_the_form(words, form):
    """A `$` that ends one of a brace set's words starts a form after the set, as bash expands braces first."""
    assert gate._brace_store_subst(words) == form


def test_a_word_past_the_brace_cap_counts():
    """It counts as the other callers of _brace_words count it: the gate cannot tell what it makes."""
    word = "{$,}" + "{a,b}" * 10 + "{HOME/%//x}"
    assert gate._brace_store_subst(["x", word]) == word
    assert gate._brace_store_subst(["{a,b}" * 11]) == ""        # no `$` ends a word in it, so it is not expanded


@pytest.mark.parametrize("line", [
    "cat {$,}{HOME/%//.kube}/config " + " ".join(FILL),
    "cat {$,}{HOME/%//.kube}/config " + " ".join(FILL).replace("1021", "1022"),
    "cat {$,}{HOME/%//.exo}/keys/seat " + " ".join(FILL),
    "cat " + " ".join(FILL) + " {$,}{HOME/%//.kube}/config",
])
def test_a_spent_brace_budget_does_not_hide_a_built_form(env, line):
    """The secret-store checks of a call share one brace budget. When other words leave too little of it for the
    word that builds the form, that word counts, as a word past the cap does (abu-aska's review of #534)."""
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    assert (c.category, gate.decide(c, POL).outcome, gate.decide(c, DENY).outcome) == ("credentials", "ask", "deny")
    c = gate.classify({"tool": "bash", "args": {"command": line.replace("cat ", "cp x ", 1)}})
    assert "credentials" in {p.category for p in gate._each_part(c)}


@pytest.mark.parametrize("first", [True, False], ids=["form first", "form last"])
def test_a_read_tool_with_a_spent_brace_budget_asks(env, first):
    built = "{$,}{HOME/%//.kube}/config"
    c = gate.classify({"tool": "read_file", "args": {"paths": [built, *FILL] if first else [*FILL, built]}})
    assert (c.category, gate.decide(c, POL).outcome, gate.decide(c, DENY).outcome) == ("credentials", "ask", "deny")


@pytest.mark.parametrize("line", [
    "x=HOME; cat ${!x/%//.kube}/config",
    "x=HOME; cat ${!x/%//.exo}/keys/seat",
    "x=EXO_HOME; cat ${!x/%x/y}/keys/seat",
    "x=HOME; cat ${!x/*/~/.exo}/keys/seat",
    "x=HOME; cat ${!x//e/e\\/.exo}/keys/seat",
    "x=HOME; cd ${!x/%//.exo} && cat keys/seat",
    "set -- HOME; cat ${!1/%//.exo}/keys/seat",
    "set -- HOME; cat ${!*/%//.exo}/keys/seat",
    "set -- HOME; cat ${!@/%//.exo}/keys/seat",
    "x=HOME; cat ${!x[0]/%//.exo}/keys/seat",
    "x=HOME; cat {$,}{!x/%//.exo}/keys/seat",
])
def test_an_indirect_name_reads_as_a_store_variable(env, line):
    """`${!x}` is the variable whose name `$x` holds. The gate does not track that name, so it may be a store variable
    (exo-36's review of #534): bash gives `$HOME/.exo/keys/seat` for `x=HOME; cat ${!x/%//.exo}/keys/seat`."""
    assert outcome(line) == ("credentials", "ask")
    assert outcome(line.replace("cat", "cp x", 1).replace("keys/seat", "policies/default.yaml")
                   .replace("config", "policies/default.yaml"))[0] == "store_write"


@pytest.mark.parametrize("call", [
    {"tool": "write_file", "args": {"path": "${HOME/%//.kube}/config", "content": "x"}},
    {"tool": "edit_file", "args": {"path": "{$,}{HOME/%//.kube}/config", "content": "x"}},
    {"tool": "apply_patch", "args": {"patch": "*** Begin Patch\n*** Update File: a.txt\n"
                                              "*** Move to: ${HOME/%//.kube}/config\n@@\n-a\n+b\n*** End Patch\n"}},
    {"tool": "write_file", "args": {"path": "${!x/%//.kube}/config", "content": "x"}},
    {"tool": "delete_file", "args": {"path": "${HOME/%//.kube}/config"}},
    {"tool": "move_file", "args": {"source": "a.txt", "destination": "${EXO_HOME/%x/y}/policies/x.yaml"}},
    {"tool": "upload_file", "args": {"path": "${HOME/#*/~/.kube}/config"}},
])
def test_a_file_tool_write_keeps_the_read_beside_it(env, call):
    """As on a shell line, a write or delete of a path the gate does not spell out keeps a credentials part beside the
    store write, so a deny on credentials denies it as it denies `write_file ~/.kube/config` (abu-aska's review of
    #534). A plain store write has no such part."""
    c = gate.classify(call)
    assert c.category == "store_write"
    assert "credentials" in {p.category for p in gate._each_part(c)}
    assert (gate.decide(c, POL).outcome, gate.decide(c, DENY).outcome) == ("ask", "deny")
    plain = gate.classify({"tool": call["tool"], "args": {"path": "~/.exo/policies/x.yaml", "content": "x"}})
    assert "credentials" not in {p.category for p in gate._each_part(plain)}


@pytest.mark.parametrize("call", [
    "cp ${HOME/%//projects}/a ~/.exo/policies/x.yaml",
    "mv ${HOME/%//.kube}/config ~/.exo/policies/x.yaml",
    "tee ${HOME/%//x} ~/.exo/policies/x.yaml < a.txt",
    "cp a ~/.exo/policies/x.yaml > ${HOME/%//x}/log",
    "cp {$,}{HOME/%//x} ~/.exo/policies/x.yaml",
    "x=HOME; cp ${!x/%//p}/a ~/.exo/policies/x.yaml",
    "git -C ${HOME/%//p} --work-tree ~/.exo/policies commit -m x",
    {"tool": "move_file", "args": {"source": "${HOME/%//.kube}/config", "destination": "~/.exo/policies/x.yaml"}},
    {"tool": "copy_file", "args": {"src": "${HOME/%//projects}/a.md", "dest": "~/.exo/policies/x.yaml"}},
    {"tool": "delete_file", "args": {"paths": ["${HOME/%//projects}/a.md", "~/.exo/policies/x.yaml"]}},
    {"tool": "apply_patch", "args": {"patch": "*** Begin Patch\n*** Update File: ${HOME/%//projects}/a.md\n@@\n-a\n+b\n"
                                              "*** Update File: ~/.exo/policies/x.yaml\n@@\n-a\n+b\n*** End Patch\n"}},
])
def test_a_spelled_store_path_stays_the_target(env, call):
    """A call that names a store path only by a substitution, and another one the gate spells, keeps the spelled one
    as its target, as main does. So a deny or reserved entry on it still reaches the call (the independent check of
    #534: `deny: [target:*/.exo/*]` asked on these where main denied). The substitution still fails closed, and stays
    a store write part beside it."""
    c = gate.classify({"tool": "bash", "args": {"command": call}} if isinstance(call, str) else call)
    assert c.category == "store_write" and c.target.endswith(("/.exo/policies/x.yaml", "/.exo/policies"))
    assert gate.decide(c, {"gates": {"deny": ["target:*/.exo/*"]}}).outcome == "deny"
    assert gate.decide(c, {"gates": {"reserved": ["target:*/.exo/*"]}}).reserved
    assert gate.decide(c, DENY).outcome == "deny"
    assert any(p.category == "store_write" and "${" in p.target for p in gate._each_part(c))


def test_a_substitution_alone_stays_the_target(env):
    assert gate.classify({"tool": "bash", "args": {"command": "cp a ${HOME/%//p}/b"}}).target == "${HOME/%//p}/b"
    c = gate.classify({"tool": "move_file", "args": {"source": "/tmp/a", "destination": "${HOME/%//p}/b"}})
    assert (c.category, c.target) == ("store_write", "${HOME/%//p}/b")


def test_a_word_past_the_budget_keeps_the_substitution_beside_it(env):
    """A word past the call's brace budget is the target, as on main, and the form found before it stays a store
    write part, so a deny entry on its path still denies."""
    c = gate.classify({"tool": "bash", "args": {"command": "cp a {$,}{HOME/%//.exo}/keys/seat " + " ".join(FILL)}})
    assert ("store_write", "${HOME/%//.exo}/keys/seat") in {(p.category, p.target) for p in gate._each_part(c)}
    assert gate.decide(c, {"gates": {"deny": ["target:*.exo*"]}}).outcome == "deny"


@pytest.mark.parametrize("call", [
    {"tool": "delete_file", "args": {"paths": FILL + ["{$,}{HOME/%//.exo}/keys/seat", "~/.exo/policies/x.yaml"]}},
    {"tool": "delete_file", "args": {"paths": ["{$,}{!x/%//.kube}/config"] + FILL + ["~/.exo/policies/x.yaml"]}},
    {"tool": "move_file", "args": {"source": "{$,}{HOME/%//.exo}/keys/seat", "destination": "~/.exo/policies/x.yaml",
                                   "files": FILL}},
    {"tool": "frobnicate", "args": {"files": FILL, "path": "{$,}{HOME/%//.exo}/keys/seat",
                                    "dest": "~/.exo/policies/x.yaml"}},
    {"tool": "apply_patch", "args": {"patch": "*** Begin Patch\n" + "".join(
        f"*** Update File: {p}\n@@\n-a\n+b\n" for p in FILL + ["{$,}{HOME/%//.exo}/keys/seat", "~/.exo/policies/x.yaml"])
        + "*** End Patch\n"}},
])
def test_a_spent_brace_budget_keeps_each_store_path(env, call):
    """Once the call's other paths spend the brace budget, the gate cannot tell which store path it spells, so a form
    a brace set builds may stay the target. Each other store path is then a store write part, so a deny or reserved
    entry on it still reaches the call (a third check of #534: `deny: [target:*/.exo/*]` asked where main denies)."""
    c = gate.classify(call)
    assert c.category == "store_write"
    assert ("store_write", "~/.exo/policies/x.yaml") in {(p.category, p.target) for p in gate._each_part(c)}
    assert gate.decide(c, {"gates": {"deny": ["target:*/.exo/*"]}}).outcome == "deny"
    assert len(gate.decide(c, {"gates": {"reserved": ["target:*/.exo/*"]}}).reserved) >= 1
    assert gate.decide(c, DENY).outcome == "deny"


@pytest.mark.parametrize("line", [
    "ssh h 'cat ${HOME/%//x}/a'; cp x ~/.ssh/id_rsa",
    "cp x ~/.ssh/id_rsa; ssh h 'cat ${HOME/%//x}/a'",
    "x=HOME; ssh h 'cat ${!x/%//p}/a'; cat ~/.ssh/id_rsa",
    "bash -c 'cat ${HOME/%//x}/a'; cat ~/.ssh/id_rsa",
    "cat $(echo {$,}{HOME/%//x}); cp x ~/.ssh/id_rsa",
    "eval 'cat ${EXO_HOME/%x/y}/a'; cat ~/.ssh/id_rsa",
    "sh -c \"ls ${HOME/#*/~/p}\" && tee ~/.ssh/id_rsa < /dev/null",
    "bash -c 'cd {$,}{HOME/%//.exo} && cat keys/seat'; cp x ~/.ssh/id_rsa",
    "bash -c 'cat {$,}{HOME/%//x} " + " ".join(FILL) + "'; cat ~/.ssh/id_rsa",
])
def test_a_spelled_secret_stays_the_line_target(env, line):
    """A nested line (an ssh remote command, `bash -c`, a `$( )`) that holds a substitution is `credentials` and led
    the line, so a secret path the gate spells beside it was no longer the target: `deny: [target:*/.ssh/*]` asked
    where main denies (a third check of #534). The spelled path stays the target, as on main, and the nested line
    stays a part, so an entry on either reaches the line."""
    c = gate.classify({"tool": "bash", "args": {"command": line}})
    assert c.category == "credentials" and c.target.endswith("/.ssh/id_rsa")
    assert c.signals.count("secret store") == 1
    assert gate.decide(c, {"gates": {"deny": ["target:*/.ssh/*"]}}).outcome == "deny"
    assert gate.decide(c, {"gates": {"reserved": ["target:*/.ssh/*"]}}).reserved
    assert any(p.category == "credentials" and p.target != c.target for p in gate._each_part(c))


def test_only_a_form_gives_up_the_target(env):
    """A nested form with no secret path beside it keeps its target. A nested line that is `credentials` for another
    reason keeps the target as on main, also beside a secret path (`ssh h env` dumps the host's environment)."""
    c = gate.classify({"tool": "bash", "args": {"command": "bash -c 'cat ${HOME/%//x}/a'; ls"}})
    assert (c.category, c.target) == ("credentials", "${HOME/%//x}")
    c = gate.classify({"tool": "bash", "args": {"command": "ssh h env; cp x ~/.ssh/id_rsa"}})
    assert (c.category, c.target) == ("credentials", "h")


def test_nested_forms_are_not_spelt_out(monkeypatch):
    """The fail-closed check reads a nest in one pass, so a deep or wide one costs what main's reading costs: the
    review's 138 KB line of 9 levels of 3 forms each took 10 s to classify when each replacement was spelt out."""
    calls = []
    real = gate._expand_text
    monkeypatch.setattr(gate, "_expand_text", lambda *a, **k: calls.append(1) or real(*a, **k))

    def tree(depth, width):
        return "/a" if depth == 0 else "${HOME/%/" + tree(depth - 1, width) * width + "}"
    for depth, width in ((9, 3), (4, 15)):
        calls.clear()
        start = time.process_time()
        c = gate.classify({"tool": "bash", "args": {"command": "cat " + tree(depth, width) + "/keys/seat"}})
        assert time.process_time() - start < 5
        assert c.category == "credentials" and len(calls) < 50


@pytest.mark.parametrize("line", [
    "${HOME:-" * 15000 + "x" + "}" * 15000,
    "${HOME[0]:-'" * 11000 + "x" + "'}" * 11000,
    "${HOME[" * 17000,
    "${HOME/x}" * 15000 + "'",
])
def test_the_check_reads_a_hostile_nest_in_one_pass(line):
    """Each form looks up its quotes and its subscript's end in lists made once, so a 130 KB nest costs milliseconds."""
    start = time.process_time()
    gate._store_subst(line)
    assert time.process_time() - start < 1


def test_many_substitutions_are_bounded():
    for line in ("cat " + "${HOME/%//.kube}" * 3000 + "/config", "cat " + "{$,}" * 40 + "{HOME/%//.exo}/keys/seat"):
        start = time.process_time()
        c = gate.classify({"tool": "bash", "args": {"command": line}})
        assert time.process_time() - start < 5
        assert c.category == "credentials"
