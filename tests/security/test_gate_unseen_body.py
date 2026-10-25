"""A post whose body the gate cannot read asks, and the gate checks the text the post will carry (#208).

The posting step checks text the gate can see: the footer, `forbid`, `forbid_words`, the scrub and the binding
to the exact text. A body read from stdin, a file named with `@`, `--input`, command output or a variable passed
as an empty string, so those posts were allowed under an internal scope. Classification and decision only;
nothing is executed and no file is read.
"""
import pytest

from exo.gate import UNSEEN_BODY, UNSEEN_ON_LINE, body_unseen, classify, decide, normalize_policies

POLICY = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                             "posting": {"footer": "F", "forbid_words": ["Zork"]}})
NO_FOOTER = normalize_policies({"gates": {"scopes": {"internal": ["myorg/*"]}},
                                "posting": {"forbid_words": ["Zork"]}})
R = "-R myorg/app"
API = "gh api repos/myorg/app/issues/5/comments"


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


# The issue's table: every row was `log` on main (b5622cf and 4ac092b) and must ask.
UNSEEN = [
    f"gh pr comment 5 {R} --body-file notes.md",
    f"gh issue create {R} -t hi -F notes.md",
    f"gh pr comment 5 {R} --body-file - < notes.md",
    f"cat notes.md | gh pr comment 5 {R} --body-file -",
    f"{API} -f body=@notes.md",
    f"{API} -F body=@notes.md",
    f"{API} --input notes.json",
    f"gh pr comment 5 {R} --body \"$(cat notes.md)\"",
    f"gh pr comment 5 {R} --body \"$X\"",
    f"gh pr comment 5 {R} --body `cat notes.md`",
    # more of the same: a here-string or heredoc bash expands, gh's own body sources, options gh reads otherwise
    f"gh pr comment 5 {R} --body-file - <<< \"$(cat notes.md)\"",
    f"gh pr comment 5 {R} --body-file - <<EOF\n$(cat notes.md)\nEOF",
    f"gh pr create {R} -t hi --fill",
    f"gh pr create {R} -t hi -fd",
    f"gh issue create {R} -t hi --template bug.md",
    f"gh pr comment 5 {R} --body 'clean\nF' --body 'Zork'",          # gh takes the last value
    f"gh pr comment 5 {R} -db 'Zork'",                               # a short-option cluster
    f"gh pr comment 5 {R} --body-file /tmp/clean.md -F /tmp/other.md",
    f"gh pr comment 5 {R} --body 'clean\nF' $EXTRA",                 # a word that may expand to an option
    f"{API} -F body=@-",
    # review of #306: a subshell or group writes the pipe (only its last command looked like the writer) ...
    f"(cat notes.md; echo; echo 'F') | gh pr comment 5 {R} --body-file -",
    f"(cat notes.md && echo F) | gh issue create {R} -t hi -F -",
    f"{{ cat notes.md; echo F; }} | gh pr comment 5 {R} --body-file -",
    f"while read l; do echo \"$l\"; done < notes.md | gh pr comment 5 {R} --body-file -",
    # ... or bash and the writer rewrite the text before gh reads it
    f"printf 'Zo\\x72k\nF' | gh pr comment 5 {R} --body-file -",
    f"printf %s Zo rk | gh pr comment 5 {R} --body-file -",
    f"echo -e 'Zo\\x72k\nF' | gh pr comment 5 {R} --body-file -",
    f"echo -ne 'Zo\\x72k\nF' | gh pr comment 5 {R} --body-file -",
    f"gh pr comment 5 {R} --body-file - <<EOF\nZo\\\nrk\nF\nEOF",
    f"cat <<EOF | gh pr comment 5 {R} --body-file -\nZo\\\nrk\nF\nEOF",
    f"echo clean | timeout 5 gh pr comment 5 {R} --body-file - < notes.md",
    f"gh issue create {R} -t -b -b 'Zork'",                          # -t takes `-b`; the body is Zork
    f"gh pr comment 5 {R} --body 'A note.\nF' --attach shot.png",
    # second review: expansions without `$` (bash posts "Zork Zok", or keeps the last of two --body= words) ...
    f"echo Zo{{r,}}k'\nF' | gh pr comment 5 {R} --body-file -",
    f"gh pr comment 5 {R} --body=Zo{{,rk}}'\nF'",
    f"gh pr comment 5 {R} --body Zo*'\nF'",
    f"gh pr comment 5 {R} --body-file - <<< ~/x",
    # ... a heredoc or here-string on another descriptor is not gh's stdin ...
    f"cat notes.md | gh pr comment 5 {R} --body-file - 3<<'EOF'\nok\nF\nEOF",
    f"cat notes.md | gh pr comment 5 {R} --body-file - 2<<< 'ok\nF'",
    f"exec < notes.md; gh pr comment 5 {R} --body-file - 3<<'EOF'\nok\nF\nEOF",
    # ... bash ends a heredoc only on the exact delimiter line, so these bodies run on to `echo Zork` ...
    f"gh pr comment 5 {R} --body-file - <<'EOF'\nok\nF\nEOF \necho Zork\nEOF",
    f"gh pr comment 5 {R} --body-file - <<'EOF'\nok\nF\n EOF\necho Zork\nEOF",
    f"gh pr comment 5 {R} --body-file - <<'EOF'\nok\nF\nEOF\r\necho Zork\nEOF",
    f"gh pr comment 5 {R} --body-file - <<'EOF'\nok\nF\n\tEOF\necho Zork\nEOF",
    # ... and a long text option takes the next word as its value
    f"gh issue create {R} --title --body --body 'Zork'",
]


@pytest.mark.parametrize("line", UNSEEN)
def test_an_unreadable_body_asks(line):
    c = classify(sh(line))
    assert c.category == "github_post" and body_unseen(c), c
    for pol in (POLICY, NO_FOOTER):
        d = decide(c, pol)
        assert d.outcome == "ask" and UNSEEN_BODY in d.violations, (line, d)


# What the gate reads on the line stays as it was: these log, as on main.
SEEN = [
    f"gh pr comment 5 {R} --body 'Looks good.\nF'",
    f"gh pr create {R} -t hi --body \"$(cat <<'EOF'\nA change.\nF\nEOF\n)\"",
    f"gh pr comment 5 {R} --body-file - <<'EOF'\nA note with $HOME in it.\nF\nEOF",
    f"gh pr comment 5 {R} --body-file - <<EOF\nA note.\nF\nEOF",
    f"cat <<'EOF' | gh pr comment 5 {R} --body-file -\nA note.\nF\nEOF",
    f"gh pr comment 5 {R} --body-file - <<< 'A note.\nF'",
    f"{API} -f body='A note.\nF'",
    f"gh pr review 5 {R} --approve",
    f"gh pr ready 5 {R}",
    f"echo 'A note. F' | gh pr comment 5 {R} --body-file -",
    f"printf 'A note.\nF' | gh pr comment 5 {R} --body-file -",
    f"cat <<'EOF' | gh pr comment 5 {R} --body-file -\nA note \\ ok\nF\nEOF",
    f"gh pr create {R} -t hi -b 'A note.\nF' -a me -r someone -l bug",
    f"gh pr review 5 {R} -a -b 'A note.\nF'",                       # -a is --approve here, no value
    f"gh pr comment 5 {R} --body-file - <<-'EOF'\n\tA note.\n\tF\n\tEOF",   # <<- drops the leading tabs
    f"gh pr comment 5 {R} --body-file - 0<<'EOF'\nA note.\nF\nEOF",
    f"gh pr create {R} --title 'Fix {{x}}' --body 'A note.\nF'",
    f"gh issue create {R} --title hi --body 'A note.\nF'",
]


@pytest.mark.parametrize("line", SEEN)
def test_a_readable_body_is_checked_as_before(line):
    c = classify(sh(line))
    assert c.category == "github_post" and not body_unseen(c), c
    assert decide(c, POLICY).outcome == "log", (line, decide(c, POLICY))


def test_the_text_checked_is_the_text_posted():
    assert classify(sh(f"gh pr comment 5 {R} --body-file - <<< 'Zork'")).text == "Zork"
    piped = classify(sh(f"cat <<'EOF' | gh pr comment 5 {R} --body-file -\nZork here\nEOF"))
    assert piped.text == "Zork here" and "forbidden word: Zork" in decide(piped, POLICY).violations


def test_another_command_s_heredoc_is_not_a_body_file_s_text():
    # On main the line's first heredoc filled in the text, and the hook then skipped the file the post sends.
    c = classify(sh(f"cat > /tmp/x <<'EOF'\nclean\nF\nEOF\ngh pr comment 5 {R} --body-file /tmp/notes.md"))
    assert c.text == "" and c.text_file == "/tmp/notes.md" and body_unseen(c)
    assert decide(c, POLICY).outcome == "ask"


def test_a_body_file_read_by_the_caller_counts_as_seen():
    c = classify(sh(f"gh pr comment 5 {R} --body-file /tmp/notes.md"))
    c.text = "A note.\nF"                                   # what the exo-gate hook does after reading the file
    assert not body_unseen(c) and decide(c, POLICY).outcome == "log"


def test_a_second_post_on_the_line_keeps_the_line_unseen():
    c = classify(sh(f"gh pr comment 5 {R} --body 'ok\nF' && gh pr comment 6 {R} --body-file - < notes.md"))
    assert body_unseen(c) and decide(c, POLICY).outcome == "ask"


def test_gh_api_fields_are_read_as_gh_reads_them():
    c = classify(sh(f"{API} -fbody=Zork"))                 # main: no field seen, so a GET and a `read`
    assert c.category == "github_post" and c.text == "Zork"
    assert "forbidden word: Zork" in decide(c, POLICY).violations


def test_release_notes_are_the_release_text():
    c = classify(sh(f"gh release create v1 {R} --notes 'Zork'"))
    assert c.category == "publish" and c.text == "Zork" and not body_unseen(c)
    c = classify(sh(f"gh release create v1 {R} --notes-file /tmp/n.md"))
    assert c.text_file == "/tmp/n.md" and body_unseen(c)
    assert body_unseen(classify(sh(f"gh release create v1 {R} --generate-notes")))
    pol = normalize_policies({"posting": {"applies_to": ["publish"], "forbid_words": ["Zork"]}})
    assert decide(classify(sh(f"gh release create v1 {R} --notes 'Zork'")), pol).outcome == "ask"


def test_a_gist_s_files_are_unseen():
    assert body_unseen(classify(sh("gh gist create notes.md")))
    assert body_unseen(classify(sh("gh gist create --public notes.md")))


@pytest.mark.parametrize("line", ["gh gist view abc", "gh gist list", "gh pr view $N",
                                  "gh workflow run deploy.yml -F env=prod",
                                  "curl -s \"https://api.example.com/$X\""])
def test_reads_and_other_acts_are_not_posts_with_unseen_bodies(line):
    assert not body_unseen(classify(sh(line)))


@pytest.mark.parametrize("line", [
    "curl -d @notes.md https://hooks.example.com/x",
    "curl -d@notes.md https://hooks.example.com/x",
    "curl --data-binary @- https://hooks.example.com/x",
    "curl -F 'file=@notes.md' https://hooks.example.com/x",
    "curl -F 'text=<notes.md' https://hooks.example.com/x",
    "curl --data-urlencode text@notes https://hooks.example.com/x",
    "curl -T notes.md https://hooks.example.com/x",
    "curl -d \"$MSG\" https://hooks.example.com/x",
    "curl -K req.cfg -X POST https://hooks.example.com/x",
    "wget --post-file=notes.md https://hooks.example.com/x",
    "http POST hooks.example.com/x text=@notes.md",
    "http POST hooks.example.com/x < notes.json",
    "cat notes.json | http POST hooks.example.com/x",
])
def test_an_http_body_from_a_file_stdin_or_expansion_is_unseen(line):
    c = classify(sh(line))
    assert c.category == "web_post" and body_unseen(c), c
    pol = normalize_policies({"posting": {"applies_to": ["web_post"], "forbid_words": ["Zork"]}})
    assert decide(c, pol).outcome == "ask"


@pytest.mark.parametrize("line", [
    "curl -d 'text=hello' https://hooks.example.com/x",
    "curl -H \"Authorization: Bearer $TOKEN\" -d 'text=hello' https://hooks.example.com/x",
    "curl -u \"bot:$PASS\" --json '{\"a\": 1}' https://hooks.example.com/x",
    "http POST hooks.example.com/x text=hello -a \"bot:$PASS\"",
])
def test_a_literal_http_body_is_seen(line):
    c = classify(sh(line))
    assert c.category == "web_post" and not body_unseen(c), c


def test_attached_curl_data_is_a_post():
    # Main read `-d@file` as no data at all: a GET, filed as web_read and allowed.
    c = classify(sh("curl -d@notes.md https://hooks.example.com/x"))
    assert c.category == "web_post" and decide(c, POLICY).outcome == "ask"


@pytest.mark.parametrize("line", [
    "curl --data-ascii @notes.md https://hooks.example.com/x",       # main: a GET, filed as web_read
    "curl -K req.cfg https://hooks.example.com/x",                    # the config file can carry data
    "curl -K req.cfg http://localhost:8080/x",                        # and other URLs
    "curl -d x \"$URL\"",                                             # main: an empty host counted as local
    "curl -d x $URL",
])
def test_a_curl_request_the_gate_cannot_read_is_a_post(line):
    c = classify(sh(line))
    assert c.category == "web_post" and body_unseen(c), c
    assert decide(c, POLICY).outcome == "ask"


@pytest.mark.parametrize("line,category", [
    ("curl -d x localhost:8080/api", "local_write"),
    ("curl -X POST http://localhost:8080/api -d x", "local_write"),
    ("curl -d x 127.0.0.1:9/x", "local_write"),
    ("curl -fsSL https://example.com/x.sh -o x.sh", "web_read"),
])
def test_local_and_plain_curl_calls_keep_their_category(line, category):
    assert classify(sh(line)).category == category


def test_gh_placeholders_are_not_brace_expansions():
    # `{owner}` has no comma or `..`, so bash keeps it; gh fills it in. The body stays readable.
    c = classify(sh("gh api repos/{owner}/{repo}/issues/5/comments -f body='A note.\nF'"))
    assert c.category == "github_post" and not body_unseen(c) and c.text == "A note.\nF"


# #343: the exo-gate hook leaves a body file unread on a line that does more than post, since the rest of the line may
# change the file before gh reads it, and names it in `held_body_files`. The note then says so and asks for the post
# alone on its line, in place of the hint to pass an absolute path. Each reading of a gh line gets it (#399).
HELD = [
    f"cp /tmp/notes.md /tmp/clean.md && gh pr comment 5 {R} --body-file /tmp/clean.md",
    "gh pr comment 5 -R github.com/myorg/app --body-file /tmp/clean.md | tail -1",      # a copy on another target
    "true && gh pr -R myorg/app comment 5 --body-file /tmp/clean.md",                   # the line as read before
    "gh pr -R myorg/app view 5 && gh pr comment 5 -R myorg/app --body-file /tmp/clean.md",
]


@pytest.mark.parametrize("line", HELD)
def test_a_body_file_left_unread_for_the_line_says_why(line):
    c = classify(sh(line))
    assert body_unseen(c), c
    d = decide(c, POLICY, {"held_body_files": ["/tmp/clean.md"]})
    assert d.outcome == "ask" and d.violations.count(UNSEEN_ON_LINE) == 1, d
    assert UNSEEN_BODY not in d.violations and UNSEEN_ON_LINE in d.reason, d
    # Without the caller's word, or for another file, the note is the plain one, as before.
    for ctx in (None, {"held_body_files": ["/tmp/other.md"]}):
        d = decide(c, POLICY, ctx)
        assert d.outcome == "ask" and UNSEEN_BODY in d.violations and UNSEEN_ON_LINE not in d.violations, d


def test_only_the_post_whose_file_was_left_unread_gets_the_line_note():
    c = classify(sh(f"gh pr comment 5 {R} --body-file /tmp/clean.md && gh pr comment 6 {R} --body-file - < x.md"))
    d = decide(c, POLICY, {"held_body_files": ["/tmp/clean.md"]})
    assert UNSEEN_ON_LINE in d.violations and f"GitHub PR comment on myorg/app#6: {UNSEEN_BODY}" in d.violations, d


def test_a_post_also_unseen_otherwise_keeps_the_plain_note():
    # A title from a variable leaves the post unseen on a line of its own too, so the plain note stays beside the
    # line's, and the card does not ask for the post alone and only then for the text inline.
    c = classify(sh(f"cd /tmp && gh pr create {R} --title \"$T\" --body-file /tmp/clean.md"))
    d = decide(c, POLICY, {"held_body_files": ["/tmp/clean.md"]})
    assert d.outcome == "ask" and UNSEEN_ON_LINE in d.violations and UNSEEN_BODY in d.violations, d
    c = classify(sh(f"cd /tmp && gh pr create {R} --title t --body-file /tmp/clean.md"))
    d = decide(c, POLICY, {"held_body_files": ["/tmp/clean.md"]})
    assert UNSEEN_ON_LINE in d.violations and UNSEEN_BODY not in d.violations, d
