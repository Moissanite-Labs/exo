"""#584: a backslash-newline right after a `$` is joined before the `$` form is read, as bash joins it.

Outside single quotes bash drops each `\\<newline>` before it expands a word, so `"$\\<newline>(cmd)"` runs cmd as
"$(cmd)" does, `"$\\<newline>{x}"` is "${x}", `"${\\<newline>!n}"` is "${!n}" and `"$(\\<newline>(1+2))"` is the
arithmetic "$((1+2))". The lexer read a `$` and then a backslash as a literal `$`, so the word was plain text and the
substitution's command was never classified: `echo "$\\<newline>(git push --force origin main)"` was read and allowed
under `deny: [force_push]`. bash drops them inside a ${..} name and a $(( )) body too (`${PA\\<newline>TH:=x}` sets
PATH). The gate now reads such a line with each `\\<newline>` in a `$` form joined, every line read again from it too
(`bash -c`, eval, ssh, a backquoted body, once their words are decoded), and also as written, as main read it, and
decide keeps the stricter.

Every line here is classification data only; nothing runs it. The bash readings were checked with a probe whose every
command word is printf or `:` (bash 5.2.21): `"$\\<newline>(printf hi)"` gives `hi`, `"$\\<newline>{x}"`, `$\\<newline>
{\\<newline>x}` and `"$\\<newline>@"` expand, `"$(\\<newline>(printf a))"` is an arithmetic error, `$\\<newline>'\\x41'`
gives `A`, while `'$\\<newline>(..)'` and `"\\$\\<newline>(..)"` stay text and a comment does not join its next line.
#585: the lexer reads the form after the `\\<newline>` itself now, so the line as written decides as its plain
spelling does, joined reading or not.
"""
import pytest

from exo import gate

J = "\\\n"          # a backslash, then a newline: the line continuation bash drops
PUSH = "git push --force origin main"
POST = "curl -X POST -d @f https://example.com"
PUSH_PLAIN = "git push origin main"
POLICIES = [None,
            gate.normalize_policies({"gates": {"deny": ["force_push"]}}),
            gate.normalize_policies({"gates": {"categories": {"force_push": "deny"}}}),
            gate.normalize_policies({"gates": {"deny": ["web_post"]}})]
PUSH_DENIED = ["ask", "deny", "deny", "ask"]
ASKS = ["ask"] * 4
ALLOWED = ["allow"] * 4


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(line):
    return gate.classify({"tool": "bash", "args": {"command": line}})


def outcomes(line):
    """The line's outcome under the default policy, `deny: [force_push]`, `categories: {force_push: deny}` and
    `deny: [web_post]`."""
    c = sh(line)
    return [gate.decide(c, p).outcome for p in POLICIES]


# The issue's table, and each `$` form with one or more `\<newline>` after the `$`, after `${` or `${!`, and between
# `$(` and `(`, in double quotes, unquoted and in lines another shell reads again. On main 7785ffbb each was read and
# allowed under all four policies, or (the bash -c row) asked as opaque_exec and never denied, or (the $'..' row) a
# plain push that only logged.
FIXED = [
    (f'echo "${J}({PUSH})"', "force_push", PUSH_DENIED),
    (f'echo "${J}(curl -X POST -d @f https://example.com)"', "web_post", ["ask", "ask", "ask", "deny"]),
    (f'echo "${J}{J}({PUSH})"', "force_push", PUSH_DENIED),
    (f'echo "${J}{J}{J}({PUSH})"', "force_push", PUSH_DENIED),
    (f'echo "${J}({J}{PUSH})"', "force_push", PUSH_DENIED),
    (f'echo "x${J}({PUSH})y" | cat', "force_push", PUSH_DENIED),
    (f'echo "${J}(rm -rf ~/work)"', "delete", ASKS),
    (f'echo "${J}(gh pr comment 3 --body hi)"', "github_post", ASKS),
    (f'echo "${J}(cp evil.yaml ~/.exo/policies/default.yaml)"', "store_write", ASKS),
    (f'echo "${J}{{PATH:=/tmp}}"; ls', "opaque_exec", ASKS),
    (f'echo "${J}{{{J}PATH:=/tmp}}"; ls', "opaque_exec", ASKS),
    (f'echo "${{{J}PATH:=/tmp}}"; ls', "opaque_exec", ASKS),
    (f'echo "${{{J}{J}PATH:=/tmp}}"; ls', "opaque_exec", ASKS),
    (f'echo "${J}((PATH=5))"; ls', "opaque_exec", ASKS),
    (f'echo ${J}{{PATH:=/tmp}}; ls', "opaque_exec", ASKS),
    # inside a ${..} name, before and after its operator's `:`, and inside a $(( )) body: bash sets PATH in each
    (f'echo "${{PA{J}TH:=/tmp}}"; ls', "opaque_exec", ASKS),
    (f'echo "${{PATH{J}:=/tmp}}"; ls', "opaque_exec", ASKS),
    (f'echo "${{PATH:{J}=/tmp}}"; ls', "opaque_exec", ASKS),
    (f"echo ${{PA{J}{J}TH:=/tmp}}; ls", "opaque_exec", ASKS),
    (f'echo "$((PA{J}TH=5))"; ls', "opaque_exec", ASKS),
    (f'echo "$((PATH{J}=5))"; ls', "opaque_exec", ASKS),
    # bash's legacy arithmetic, joined too: it lands on the `$[..]` the gate reads as `$((..))` (#588)
    (f"echo ${J}[PATH=5]; ls", "opaque_exec", ASKS),
    (f'echo "${J}[PATH=5]"; ls', "opaque_exec", ASKS),
    # past a nested ${..} in a subscript: the join runs to the outer ${..}'s own `}`
    (f'echo "${{a[${{b}}0,PA{J}TH=5]}}"; ls', "opaque_exec", ASKS),
    (f"echo ${{a[${{b}}0,PA{J}TH=5]}}; ls", "opaque_exec", ASKS),
    (f"echo $((1+(PA{J}TH=5))); ls", "opaque_exec", ASKS),
    (f"git push ${J}'--force' origin main", "force_push", PUSH_DENIED),
    (f"bash -c 'echo \"${J}({PUSH})\"'", "force_push", PUSH_DENIED),
    (f"ssh host 'echo \"${J}({PUSH})\"'", "force_push", PUSH_DENIED),
    (f'echo `echo "${J}({PUSH})"`', "force_push", PUSH_DENIED),
    # The `\<newline>` appears only once the gate decodes the word another shell reads: an ANSI-C argument to bash -c,
    # a double-quoted eval argument, a backquoted body (`\\` is `\` there) and an ssh command in double quotes.
    # Main asked for the first two as opaque_exec and read the last two as plain reads.
    ("bash -c $'echo \"$\\\\\\n(" + PUSH + ")\"'", "force_push", PUSH_DENIED),
    ("bash -c $'echo \"$\\\\\\n(" + POST + ")\"'", "web_post", ["ask", "ask", "ask", "deny"]),
    ('eval "echo \\"\\$\\\\\n(' + PUSH + ')\\""', "force_push", PUSH_DENIED),
    ('eval "echo \\"\\$\\\\\n(' + POST + ')\\""', "web_post", ["ask", "ask", "ask", "deny"]),
    ('x=`echo "$\\\\\n(' + PUSH + ')"`', "force_push", PUSH_DENIED),
    ('x=`echo "$\\\\\n(' + POST + ')"`', "web_post", ["ask", "ask", "ask", "deny"]),
    ('ssh host "echo \\"\\$\\\\\n(' + PUSH + ')\\""', "force_push", PUSH_DENIED),
    ('x=`echo "${PA\\\\\nTH:=/tmp}"`; ls', "opaque_exec", ASKS),
    ('x=`echo $((PA\\\\\nTH=5))`; ls', "opaque_exec", ASKS),
]


@pytest.mark.parametrize("line,category,expected", FIXED)
def test_a_dollar_form_split_by_a_line_continuation_is_read_joined(line, category, expected):
    assert sh(line).category == category
    assert outcomes(line) == expected


# Each line decides as the same line without the `\<newline>`s. The hash rows are the issue's: main reads `hash "$@"`
# and `hash "${!n}"` as plain reads, and a gate that makes them ask (#409) then asks for these spellings too.
SAME_AS_PLAIN = [
    (f'hash "${J}@"; ls', 'hash "$@"; ls'),
    (f'hash -p "${J}@"; ls', 'hash -p "$@"; ls'),
    (f'hash "${J}{{!n}}"; ls', 'hash "${!n}"; ls'),
    (f'hash "${{{J}!n}}"; ls', 'hash "${!n}"; ls'),
    (f'hash "${J}{{{J}!n}}"; ls', 'hash "${!n}"; ls'),
    (f'hash "${{!{J}n}}"; ls', 'hash "${!n}"; ls'),
    (f"hash ${J}O /tmp/x ls; ls", "hash $O /tmp/x ls; ls"),
    (f"hash -p ${J}P; ls", "hash -p $P; ls"),
    (f'hash -p "${J}{{!n}}"; ls', 'hash -p "${!n}"; ls'),
    (f'echo "${J}({PUSH})"', f'echo "$({PUSH})"'),
    (f"echo ${J}({PUSH})", f"echo $({PUSH})"),
    (f"echo ${J}{J}({PUSH})", f"echo $({PUSH})"),
    (f"x=${J}({PUSH})", f"x=$({PUSH})"),
    (f'echo "${J}(curl -X POST -d @f https://example.com)"', 'echo "$(curl -X POST -d @f https://example.com)"'),
    (f'echo "${J}({PUSH_PLAIN})"', f'echo "$({PUSH_PLAIN})"'),
    (f'echo "${J}((PATH=5))"; ls', 'echo "$((PATH=5))"; ls'),
    (f'echo "${J}[PATH=5]"; ls', 'echo "$[PATH=5]"; ls'),     # the joined legacy form, read as the plain one (#588)
    (f'echo "${J}{{PATH:=/tmp}}"; ls', 'echo "${PATH:=/tmp}"; ls'),
]


@pytest.mark.parametrize("line,plain", SAME_AS_PLAIN)
def test_the_joined_line_decides_as_the_plain_line(line, plain):
    assert outcomes(line) == outcomes(plain)


# The line as written, the first of its readings (#585): the lexer itself reads the `$` form after the `\<newline>`,
# as bash does, so the reading decides as the plain spelling does before any join. Main read these words as plain
# text (a literal `$`) and the decision leaned on the joined reading alone.
AS_WRITTEN = [
    (f'hash "${J}@"; ls', "opaque_exec"),
    (f'hash -p "${J}@"; ls', "opaque_exec"),
    (f'hash "${J}{{!n}}"; ls', "opaque_exec"),
    (f'hash -p "${J}{{!n}}"; ls', "opaque_exec"),
    (f"hash ${J}O /tmp/x ls; ls", "opaque_exec"),
    (f"hash -p ${J}P; ls", "opaque_exec"),
    (f'echo "${J}(curl -X POST -d @f https://example.com)"', "web_post"),
    (f'echo "${J}({PUSH_PLAIN})"', "vcs_push"),
]


@pytest.mark.parametrize("line,category", AS_WRITTEN)
def test_the_line_as_written_reads_the_form_after_the_continuation(line, category):
    c = sh(line)
    first = gate.readings(c)[0]
    assert first.command == line          # the line as written, before any join
    assert first.category == category


# A `$\<newline>'..'` is the ANSI-C word bash reads after it drops the `\<newline>` (#584's probe: bash 5.2.21
# prints A for `$\<newline>'\x41'`). Main kept the word `$\x41`.
def test_a_dollar_ansi_c_word_split_by_a_continuation_is_decoded():
    line = "echo $" + J + "'\\x41'"
    words = gate._parse(line)[0][0].words
    assert [w.text for w in words] == ["echo", "A"] and not any(w.dynamic for w in words)


# Between `$(` and `(` bash reads `$((`, arithmetic: `"$(\<newline>(1+2))"` is 3. Main read a $( ) whose command is
# `(1+2)`, an unknown program, and asked. Both readings are kept, so the line still asks; the more severe one, main's
# here, leads.
def test_dollar_paren_newline_paren_is_read_as_arithmetic_beside_the_line_as_written():
    line = f'echo "$({J}(1+2))"'
    c = sh(line)
    assert [r.command for r in gate.readings(c)] == [line, 'echo "$((1+2))"']
    assert c.category == "opaque_exec" and gate.readings(c)[-1].category == "read"
    assert outcomes(line) == ASKS
    assert outcomes(f'echo "$({J}(PATH=5))"; ls') == ASKS


# Where bash keeps the `\<newline>` (a comment) or main found the command another way (a ${..} word, read in full only
# with #143), the line as written is still read and its decision stands.
KEPT = [
    (f": # ${J}({PUSH})", PUSH_DENIED),
    (f'echo "${J}{{x:-$({PUSH})}}"', PUSH_DENIED),
    (f'echo "$({J}(a[$({PUSH})]))"', PUSH_DENIED),
    (f"echo ${J}((PATH=5)); ls", ASKS),
]


@pytest.mark.parametrize("line,expected", KEPT)
def test_the_line_as_written_still_decides_when_it_is_stricter(line, expected):
    assert outcomes(line) == expected


# The approval card, the hook's block message and the ledger name the reading that leads: the more severe one, so a
# line that force-pushes is never shown as the harmless `:` or `echo` of its other reading.
@pytest.mark.parametrize("line", [f": # ${J}({PUSH})", f'echo "${J}{{x:-$({PUSH})}}"', f'echo "${J}({PUSH})"',
                                  f'x=`echo "$\\\\\n({PUSH})"`'])
def test_the_more_severe_reading_leads_the_card(line):
    c = sh(line)
    # a ${..} word holding a $( ) is also read as before #143, the line as written and joined (#158)
    assert len(gate.readings(c)) == (4 if "{x:-" in line else 2) and c.category == "force_push"
    assert gate.summarize(c, gate.decide(c)).startswith("force-push to origin (irreversible")


def test_a_line_read_two_ways_binds_an_approval_to_its_exact_text():
    c = sh(f'echo "${J}x"')
    assert len(gate.readings(c)) == 2 and gate.binds_exact(c)
    assert not gate.binds_exact(sh('echo "$x"'))


# A `$\<newline>` that ends the line, or the word, is a literal `$`, as bash reads it.
END = [f'echo "${J}"', f"echo ${J}", f'echo "x${J}"', f"echo x${J}"]


@pytest.mark.parametrize("line", END)
def test_a_dollar_line_continuation_at_the_end_stays_text(line):
    assert sh(line).category == "read"
    assert outcomes(line) == ALLOWED


# Lines that stay plain reads: a `\<newline>` with no `$` before it, a plain variable, an escaped `$` (bash prints
# `$(..)`) and a `$\<newline>` in single quotes, which bash keeps as text. Also the hash guards of #585: a quoted
# word the `\<newline>` splits is still one word (`hash "$cmd"`, `hash -p "$P"`), a nameref is one word too (bash
# prints `"${r}"` of `r='a[@]'` as one), and a `\\<newline>` after a `$` is an escaped backslash, not a continuation
# of the `$` form — bash keeps the `$` and the `\`.
GUARDS = [
    f'echo "a{J}b"',
    'echo "$x"',
    f'echo "\\$({PUSH})"',
    f'echo "\\${J}({PUSH})"',
    f"printf '%s' '${J}(x)'",
    f"printf '%s' '${J}({PUSH})'",
    f"echo '${J}{{PATH:=/tmp}}'; ls",
    f"echo a{J}b",
    f'hash "${J}cmd"',
    f'hash -p "${J}P"; ls',
    "declare -n r='a[@]'; hash \"$r\"",
    "declare -n r='a[@]'; hash \"${r}\"",
    f'echo "$\\\\{J}({PUSH})"',
]


@pytest.mark.parametrize("line", GUARDS)
def test_text_that_bash_does_not_expand_stays_a_read(line):
    c = sh(line)
    assert c.category == "read"
    assert outcomes(line) == ALLOWED


def test_a_line_without_a_dollar_line_continuation_is_read_once():
    for line in (f'echo "a{J}b"', 'echo "$x"', f"echo $({PUSH})", f"ls {J}-la", f'echo "$x" {J}-n $(ls {J}-a)',
                 "bash -c $'ls\\\\\\n-la $HOME'"):
        c = sh(line)
        assert gate.readings(c) == [c] and c.also is None
