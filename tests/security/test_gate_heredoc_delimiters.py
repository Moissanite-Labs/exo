"""A quoted heredoc delimiter with a space (or tab, or other character) hides nothing after it (#328).

Bash ends a here-document on the first line that equals the delimiter exactly, where the delimiter is
the opener word after quote removal, so a space or tab in it is part of it: `cat <<'EOF '` ends on the
line `EOF ` (trailing space), never on bare `EOF`. The gate used to compare each line stripped of its
surrounding whitespace, so a delimiter with a space never matched and the whole rest of the line was
read as heredoc body -- the commands after the real terminator were never classified, and a force push,
a delete or a merge ran under the default policy as an allowed `cat`.

These command strings are data for the classifier; nothing here is executed and no file is read. Bash's
own parse of each delimiter form was checked out of band with `declare -f` on echo-only scripts, under
bash 5.3 and /bin/bash 3.2 (both expand a `$'..'` delimiter and both end the body on the exact line).
"""
import pytest

from exo.gate import body_unseen, classify, decide, normalize_policies, split_commands

DENY = normalize_policies({"gates": {"deny": ["force_push", "delete", "github_post"]}})
POST = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                          "posting": {"footer": "F", "forbid_words": ["Zork"]}})
DENY_MERGE = normalize_policies({"gates": {"deny": ["force_push", "delete", "github_post", "vcs_merge"]}})
DEFAULT = normalize_policies({})

PUSH = "git push --force origin main"
RM = "rm -rf build"
MERGE = "gh pr merge 5 -R myorg/app"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


# The issue's table. Each was `allow` (a bare `read`) on main; each must now classify the command after
# the terminator. `\ ` is a backslash-escaped space, another spelling of a trailing-space delimiter.
ISSUE_LINES = [
    (f"cat <<'EOF '\nhi\nEOF \n{PUSH}\nEOF ", "force_push"),
    (f'cat <<"EOF "\nhi\nEOF \n{RM}', "delete"),
    (f"cat <<' EOF'\nhi\n EOF\n{RM}", "delete"),
    (f"cat <<EOF\\ \nhi\nEOF \n{PUSH}\nEOF ", "force_push"),
]


@pytest.mark.parametrize("line,category", ISSUE_LINES)
def test_issue_lines_deny_and_ask(line, category):
    c = classify(sh(line))
    assert c.category == category, (line, c.category)
    assert decide(c, DENY).outcome == "deny", (line, decide(c, DENY))
    assert decide(c, DEFAULT).outcome == "ask", (line, decide(c, DEFAULT))


def test_issue_merge_line_is_no_longer_hidden():
    # `gh pr merge` is `vcs_merge`, not `github_post`, so the policy that denies github_post logs it; the
    # fix is that the merge is seen at all instead of vanishing into a `cat` body.
    line = f"true <<'X '\nX \n{MERGE}"
    c = classify(sh(line))
    assert c.category == "vcs_merge", c.category
    cmds, _ = split_commands(line)
    assert ["gh", "pr", "merge", "5", "-R", "myorg/app"] in cmds, cmds
    assert decide(c, DENY_MERGE).outcome == "deny", decide(c, DENY_MERGE)


# Every quoting form of the delimiter, with the terminator written exactly as bash's quote removal makes
# it, so bash ends the body there and the force push that follows is a real command.
QUOTING_FORMS = [
    ("<<'EOF '", "EOF "),           # single quotes keep the trailing space
    ('<<"EOF "', "EOF "),           # double quotes keep it too
    ("<<' EOF'", " EOF"),           # a leading space
    ("<<EOF\\ ", "EOF "),           # a backslash-escaped space
    ("<<E\"O\"F", "EOF"),           # mixed quoting -> EOF
    ("<<'E'\"OF\"", "EOF"),         # two adjacent quotings -> EOF
    ('<<"E\\$F"', "E$F"),           # a double-quoted \\$ is a literal $ in the delimiter
    ("<<'E\\OF'", "E\\OF"),         # a backslash is literal inside single quotes
    ("<<E\\OF", "EOF"),             # an unquoted backslash escapes the next char
    ("<<-'EOF '", "\tEOF "),        # <<- drops leading tabs on the terminator; the space stays
]


@pytest.mark.parametrize("opener,terminator", QUOTING_FORMS)
def test_delimiter_quoting_forms(opener, terminator):
    line = f"cat {opener}\nbody\n{terminator}\n{PUSH}"
    c = classify(sh(line))
    assert c.category == "force_push", (line, c.category)
    assert decide(c, DENY).outcome == "deny", (line, decide(c, DENY))
    cmds, _ = split_commands(line)
    assert ["git", "push", "--force", "origin", "main"] in cmds, (line, cmds)


# bash does NOT expand $'..' or $".." the way dash does, so the body ends on different lines in the two
# shells. The gate cannot be sure, so it reads the body as commands too: a force push there is caught.
DOLLAR_FORMS = ["<<$'EOF'", '<<$"EOF"']


@pytest.mark.parametrize("opener", DOLLAR_FORMS)
def test_dollar_quoted_delimiter_reads_body_as_commands(opener):
    line = f"cat {opener}\n{PUSH}\nEOF"
    c = classify(sh(line))
    assert c.category == "force_push", (line, c.category)
    assert decide(c, DENY).outcome == "deny", (line, decide(c, DENY))


def test_unterminated_heredoc_fails_closed():
    # No line equals the delimiter, so the gate cannot tell where the body ends: it must read the body as
    # commands, never skip it silently. The force push in the body is caught.
    for line in (f"cat <<'EOF '\n{PUSH}", f"cat <<EOF\\ \n{PUSH}\nhi", f"cat <<' EOF'\n{PUSH}"):
        c = classify(sh(line))
        assert c.category == "force_push", (line, c.category)
        assert decide(c, DENY).outcome == "deny", (line, decide(c, DENY))


# Controls: a normal heredoc still reads as before. Its body is cat's input, not commands, so a dangerous
# word in a terminated quoted body is data and is not classified or denied.
def test_normal_heredoc_body_is_not_commands():
    for line in (f"cat <<'EOF'\n{PUSH}\nEOF", f"cat <<'EOF'\n{RM}\nEOF",
                 "cat <<EOF\nhello world\nEOF"):
        c = classify(sh(line))
        assert c.category == "read", (line, c.category)
        assert decide(c, DENY).outcome != "deny", (line, decide(c, DENY))


def test_normal_heredoc_then_command_is_seen():
    # A terminated heredoc followed by a real command: the body is data, the command is classified.
    line = f"cat <<EOF\nhello\nEOF\n{PUSH}"
    c = classify(sh(line))
    assert c.category == "force_push", (line, c.category)
    assert decide(c, DENY).outcome == "deny", (line, decide(c, DENY))
    cmds, _ = split_commands(line)
    assert ["cat"] in cmds and ["git", "push", "--force", "origin", "main"] in cmds, cmds


# A `{var}<<` heredoc or `{var}<<<` here-string is on a new descriptor in bash 4.1+ (verified with
# declare -f on bash 5.3 and 3.2), not stdin, consistent with #306's rule for a numbered fd (`3<<`). So
# a post whose real body comes from a pipe is not mistaken for the heredoc, and its body stays unseen.
R = "-R myorg/app"
NAMED_FD = [
    f"cat notes.md | gh pr comment 5 {R} --body-file - {{x}}<<'EOF'\nA note.\nF\nEOF",
    f"cat notes.md | gh pr comment 5 {R} --body-file - {{var}}<<< 'A note.\nF'",
    f"gh pr comment 5 {R} --body-file - {{fd0}}<<'EOF'\nA note.\nF\nEOF",
]


@pytest.mark.parametrize("line", NAMED_FD)
def test_named_descriptor_heredoc_is_not_stdin(line):
    c = classify(sh(line))
    assert c.category == "github_post", (line, c.category)
    assert body_unseen(c), (line, c)
    assert decide(c, POST).outcome == "ask", (line, decide(c, POST))


def test_brace_word_before_heredoc_is_still_stdin():
    # `{a,b}` is a brace expansion word, not an fd name, so the heredoc is stdin as before.
    line = f"gh pr comment 5 {R} --body-file - <<'EOF'\nA note.\nF\nEOF"
    c = classify(sh(line))
    assert not body_unseen(c) and decide(c, POST).outcome == "log", (line, c)


def test_indented_terminator_for_dash_heredoc():
    # <<- lets the terminator be indented with tabs; a plain <<  heredoc's terminator is not.
    seen = f"cat <<-EOF\n\tbody\n\tEOF\n{PUSH}"             # tab-indented EOF ends it
    c = classify(sh(seen))
    assert c.category == "force_push", c.category
    assert decide(c, DENY).outcome == "deny", decide(c, DENY)
