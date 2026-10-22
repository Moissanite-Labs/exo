"""Coprocess opacity must not hide a visible destructive command (#131)."""

import pytest

from exo import gate


@pytest.mark.parametrize("command", [
    "coproc rm file",
    "coproc { rm file; }",
    "coproc worker { rm file; }",
    "coproc (rm file)",
    "coproc coproc rm file",
    "coproc sudo rm file",
    "coproc bash -c 'rm file'",
    "coproc bash <<'EOF'\nrm file\nEOF",
    "coproc rm 'file with spaces'",
    'coproc rm "{" -rf x',                 # a quoted opener is a word, so `rm` is the program (#207)
])
def test_coproc_visible_delete_is_denied(command):
    c = gate.classify_shell(command)
    assert c.category == "delete", c
    assert gate.decide(c, {"gates": {"deny": ["delete"]}}).outcome == "deny"


# The issue's own reproducer, and bash's `coproc NAME compound_command`: the name is skipped before a
# compound opener (`{`, `while`, `until`, `if`, ...), so the inner command is classified. The for, case
# and select forms pass without the skip too, because the lexer splits their bodies off at `;` and `)`.
# vcs_push outranks opaque_exec on exposure alone, so these pin that ordering too.
@pytest.mark.parametrize("command", [
    "coproc git push origin main",
    "coproc W { git push origin main; }",
    "coproc w while git push origin main; do break; done",
    "coproc w until git push origin main; do break; done",
    "coproc w if git push origin main; then :; fi",
    'coproc "$N" while git push origin main; do break; done',
    "coproc w for x in a; do git push origin main; done",
    "coproc w case $x in a) git push origin main;; esac",
    "coproc w select x in a; do git push origin main; done",
])
def test_coproc_visible_push_is_denied(command):
    c = gate.classify_shell(command)
    assert c.category == "vcs_push", c
    assert gate.decide(c, {"gates": {"deny": ["vcs_push"]}}).outcome == "deny"


# #207: bash takes an opener as a reserved word after `coproc NAME` only when the two tokens before it are
# `coproc` and the name. A redirection between them (before or after the name, with or without an fd, `<`, `<<<`,
# a heredoc, `2>&1`, `>&2`) gives a simple command that runs the name, and `rm` ignores the stray opener. Checked
# with bash 5.3 `declare -f` on echo payloads: `coproc echo >/dev/null { hi` is `coproc echo { hi > /dev/null`.
@pytest.mark.parametrize("command", [
    "coproc rm >/dev/null { -rf important",
    "coproc 2>/dev/null rm { -rf important",
    "coproc rm 2>/dev/null { -rf important",
    "coproc rm>/dev/null { -rf important",
    "coproc rm >/dev/null while -rf important",
    "coproc rm </dev/null if -rf important",
    "coproc rm >/dev/null until -rf important",
    "coproc rm >/dev/null for -rf important",
    "coproc rm >/dev/null select -rf important",
    "coproc rm >/dev/null [[ -rf important",
    "coproc rm >&2 case -rf important",
    "coproc rm 2>&1 { -rf important",
    "coproc rm <<<x { -rf important",
    "coproc <<<x rm { -rf important",
    "coproc rm <<'EOF' { -rf important\nbody\nEOF",
    "time coproc rm >/dev/null { -rf important",
    "! coproc rm >/dev/null { -rf important",
])
@pytest.mark.parametrize("policy", [{"gates": {"deny": ["delete"]}}, {"gates": {"categories": {"delete": "deny"}}}])
def test_coproc_redirection_before_the_opener_runs_the_name(command, policy):
    c = gate.classify_shell(command)
    assert c.category == "delete" and c.target == "important", c
    assert gate.decide(c, policy).outcome == "deny"


@pytest.mark.parametrize("command", [
    "coproc w { rm -rf important; } >/dev/null",   # the compound's own redirection, after its closer
    "coproc w { rm >/dev/null -rf important; }",   # inside the body
    "coproc { rm -rf important; } 2>/dev/null",
])
def test_coproc_redirection_after_the_opener_keeps_the_named_compound(command):
    c = gate.classify_shell(command)
    assert c.category == "delete" and "unknown program" not in c.signals, c
    assert gate.decide(c, {"gates": {"deny": ["delete"]}}).outcome == "deny"


def test_coproc_redirection_inside_the_body_does_not_run_the_name():
    # bash runs `echo` in a coprocess named rm: the name is not read as a program
    c = gate.classify_shell("coproc rm { echo >/dev/null hi; }")
    assert c.category == "opaque_exec", c
    assert gate.decide(c, {"gates": {"deny": ["delete"]}}).outcome == "ask"


def test_coproc_redirection_keeps_the_skipped_reading():
    # bash runs `w` here, but the reading from before #207 stays, so a delete that line denied is still denied
    c = gate.classify_shell("coproc w >/dev/null { rm -rf important")
    assert gate.decide(c, {"gates": {"deny": ["delete"]}}).outcome == "deny"


def test_function_redirection_is_not_read_as_a_program():
    # bash rejects a redirection before a function body (a syntax error, nothing runs), so the reading is unchanged
    c = gate.classify_shell("function rm >/dev/null { echo hi; }")
    assert c.category == "opaque_exec", c


def test_coproc_named_compound_keeps_credential_floor():
    c = gate.classify_shell("coproc w if muse login; then :; fi")
    assert c.category == "credentials", c
    assert gate.decide(c, {"gates": {"deny": ["credentials"]}}).outcome == "deny"


@pytest.mark.parametrize("command", ["coproc w echo hi", "coproc w rm file"])
def test_coproc_name_before_simple_command_is_the_program(command):
    # bash runs `w` here (with `rm file` as its arguments), so `rm` is not a visible delete
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert "unknown program" in c.signals
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("command", ["coproc w select x in a b", "coproc w [[ -f x ]]"])
def test_coproc_name_before_select_or_test_is_not_the_program(command):
    # `select` and `[[` open a compound command too, so `w` names the coprocess and is not run
    c = gate.classify_shell(command)
    assert "unknown program" not in c.signals, c
    assert gate.decide(c).outcome == "ask"


# D-075 (#126): a deny entry matches any simple command on a line, not only the most severe one. The tie
# case below needs it, so it is an expected failure until D-075 is present and a plain assertion after.
_D075 = gate.decide(gate.classify_shell("git push origin main && rm file"),
                    {"gates": {"deny": ["vcs_push"]}}).outcome == "deny"


@pytest.mark.xfail(not _D075, strict=True, reason="a tie with opaque_exec is denied only once D-075 checks every part")
def test_coproc_tie_category_is_denied():
    c = gate.classify_shell("coproc systemctl enable foo")
    assert gate.decide(c, {"gates": {"deny": ["schedule"]}}).outcome == "deny"


def test_function_keyword_body_is_inspected():
    # `function NAME { ...; }` has the same name-then-compound shape; `f() { ...; }` was already read
    c = gate.classify_shell("function f { git push origin main; }; f")
    assert c.category == "vcs_push", c
    assert gate.decide(c, {"gates": {"deny": ["vcs_push"]}}).outcome == "deny"


@pytest.mark.parametrize("command", [
    "coproc printenv GITHUB_TOKEN",
    "coproc env",
    "coproc { env; }",
    "coproc w { printenv GITHUB_TOKEN; }",
    "coproc w while env; do :; done",
    "coproc w if sudo printenv GITHUB_TOKEN; then :; fi",
    "function f { env; }; f",
])
def test_coproc_environment_dump_is_credentials(command):
    # as for `eval env` and `bash -c env`: credentials, which no grant or category setting lowers
    c = gate.classify_shell(command)
    assert c.category == "credentials" and "secret store" in c.signals, c
    assert gate.decide(c, {"gates": {"deny": ["credentials"]}}).outcome == "deny"
    assert gate.decide(c, {"gates": {"categories": {"opaque_exec": "allow"}}}).outcome == "ask"


@pytest.mark.parametrize("command", ["coproc env FOO=1 make", "coproc w { printenv HOME; }", "coproc w env"])
def test_coproc_environment_without_a_dump_is_not_credentials(command):
    assert gate.classify_shell(command).category == "opaque_exec"


@pytest.mark.parametrize("command", [
    "coproc",
    "coproc unknown-program",
    "coproc $PROGRAM file",
    "coproc echo hello",
    'coproc "rm file"',
    'coproc echo "x; rm file"',
    "coproc cat <<'EOF'\nrm file\nEOF",
    'coproc worker "{" rm file',
    'coproc worker "while" rm file',
])
def test_coproc_retains_opacity_without_inventing_commands(command):
    c = gate.classify_shell(command)
    assert c.category == "opaque_exec", c
    assert gate.decide(c).outcome == "ask"


def test_coproc_depth_cap_retains_opacity():
    c = gate.classify_shell("coproc rm file", depth=gate._DEPTH)
    assert c.category == "opaque_exec"
    assert gate.decide(c).outcome == "ask"


def test_coproc_quoted_target_is_preserved():
    assert gate.classify_shell('coproc rm "file with spaces"').target == "file with spaces"


def test_coproc_redirection_and_store_floors():
    c = gate.classify_shell("coproc echo hello > output.txt")
    assert c.category == "opaque_exec"
    assert gate.decide(c).outcome == "ask"
    c = gate.classify_shell("coproc echo hello > ~/.exo/principals/me/policies/gates.yaml")
    assert c.category == "store_write"
    assert gate.decide(c).outcome == "ask"


IRREVERSIBLE_DENY = {"gates": {"rules": [{"match": {"reversibility": "irreversible"}, "decision": "deny",
                                          "reason": "no irreversible actions"}]}}


@pytest.mark.parametrize("command", ["coproc env; rm -rf x", "function f { env; }; rm -rf x",
                                     "rm -rf x; coproc w { printenv GITHUB_TOKEN; }"])
def test_environment_dump_beside_an_irreversible_command_keeps_its_weight(command):
    # the credentials part wins the category, but the line still carries the delete's reversibility
    c = gate.classify_shell(command)
    assert c.category == "credentials" and c.reversibility == "irreversible", c
    assert gate.decide(c, IRREVERSIBLE_DENY).outcome == "deny"
