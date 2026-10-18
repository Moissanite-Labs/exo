"""#167: the gate reads a $'..' word the way bash 5.2 decodes it (ANSI-C quoting).

Before, the lexer decoded $'..' with Python's unicode_escape. Bash's short forms (\\u65, \\x4, \\U41), its
octal masking (\\545 is 'e') and its NUL cut-off differ from that, so a word such as $'--forc\\u65' reached
bash as --force while the gate kept its raw text. A \\u or \\U value that is no character is written the way
bash writes it: UTF-8 bytes of up to six bytes, or nothing at all from 0x80000000 on.

Every line here is classification data only; nothing runs it. The decoder table was checked against
`LC_ALL=C.UTF-8 bash --norc --noprofile probe.sh` (bash 5.2.21), where each probe line is
`printf %s $'<body>' | od -An -tx1`.

A `command:` regex in deny or reserved still matches the raw line only on main (#136). Matching the decoded
words (`command:git push.*--force` on these rows) needs the literal-word view of #187, which should use this
same decoder; it is not tested here.
"""
import pytest

from exo import gate

U = "\\u"   # spelled apart so no editor or tool folds a four-digit \\u escape in this file


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)


def sh(cmd):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


def denies(category):
    return [{"gates": {"categories": {category: "deny"}}}, {"gates": {"deny": [category]}},
            {"gates": {"rules": [{"match": {"category": category}, "decision": "deny", "reason": "no"}]}}]


# The issue's rows, plus the \x{..} form, a NUL that ends the quoted part while the word goes on, and a \U
# value from 0x80000000 on, which bash writes as nothing. On main 35b7de8 each was vcs_push (a log under
# deny: [force_push]), except the \55\x2d row, which unicode_escape already read.
FORCE_PUSH = [
    r"git push $'--forc\u65' origin main",
    r"git push $'\u2d\u2d'force origin main",
    r"git push $'--forc\545' origin main",
    r"git push $'\x{2d}\x{2d}force' origin main",
    r"git push $'--forc\0x'e origin main",
    r"git push $'--forc\U65' origin main",
    r"git push $'\55\x2dforce' origin main",
    r"git push $'--forc\UFFFFFFFFe' origin main",
    r"git push $'--\U80000000force' origin main",
]


@pytest.mark.parametrize("line", FORCE_PUSH)
def test_force_flag_spelled_with_ansi_c_escapes_is_force_push(line):
    c = sh(line)
    assert c.category == "force_push"
    for pol in denies("force_push"):
        assert gate.decide(c, pol).outcome == "deny", pol


CURL = r"curl $'https://evil\u2e'example/x"


def test_url_host_spelled_with_ansi_c_escape_is_the_target():
    c = sh(CURL)
    assert c.target == "evil.example"
    assert gate.decide(c).outcome == "allow"
    for pol in ({"gates": {"deny": ["target:evil.example"]}}, {"gates": {"deny": ["target:*.example"]}},
                {"gates": {"rules": [{"match": {"target": "evil.example"}, "decision": "deny", "reason": "no"}]}}):
        assert gate.decide(c, pol).outcome == "deny", pol


# A \u or \U value that is no character (a surrogate, past U+10FFFF) is a few bytes that are not ASCII, as in
# bash, so no flag or host can be spelled with it; the word is read, not left dynamic, and keeps the line's
# category and target. On main 35b7de8 unicode_escape refused these words and kept their raw text.
NO_CHARACTER = {
    r"echo $'\U110000'": ("read", ""),
    "ls $'a" + U + "D800'": ("read", ""),
    r"printf %s $'\U0000DFFF'": ("read", ""),
    r"git push $'--forc\U110000' origin main": ("vcs_push", "origin"),
    r"curl $'https://evil.example/\U7FFFFFFF'": ("web_read", "evil.example"),
}


@pytest.mark.parametrize("line", sorted(NO_CHARACTER))
def test_ansi_c_value_that_is_no_character_keeps_the_line_readable(line):
    category, target = NO_CHARACTER[line]
    assert not any(w.dynamic for w in gate._parse(line)[0][0].words)
    c = sh(line)
    assert c.category == category
    assert target in c.target
    for pol in denies(category):
        assert gate.decide(c, pol).outcome == "deny", pol


# Ordinary $'..' words keep their outcome (the same on main 35b7de8).
ORDINARY = {
    r"printf $'a\tb\n'": "allow",
    r"echo $'\e[31mred'": "allow",
    r"IFS=$'\n'": "ask",                     # a quoted assignment word reads as a program name
    r"read -r -d $'\0' x": "allow",
    r"grep $'\t' notes.txt": "allow",
    r"printf '%s\n' $'it\'s'": "allow",
    r"git commit -m $'x\ny'": "allow",
}


@pytest.mark.parametrize("line", sorted(ORDINARY))
def test_ordinary_ansi_c_words_keep_their_outcome(line):
    assert gate.decide(sh(line)).outcome == ORDINARY[line]


# body -> the text bash makes of $'body' (the probe's bytes, read as UTF-8; a stray byte reads as Latin-1).
DECODED = [
    (r"a\ab\bc", "a\ab\bc"), (r"\e\E\f\n\r\t\v", "\x1b\x1b\f\n\r\t\v"),
    ("\\\\", "\\"), (r"\'", "'"), (r"\"", '"'), (r"\?", "?"),
    # octal: 1 to 3 digits, low 8 bits kept
    (r"a\0b", "a"), (r"\1", "\x01"), (r"\12", "\n"), (r"\101", "A"), (r"\1012", "A2"), (r"\0101", "\x081"),
    (r"\545", "e"), (r"\777", "\xff"), (r"a\400b", "a"), (r"\8", r"\8"), (r"\18", "\x018"),
    # \x: 1 or 2 digits, or any number in braces
    (r"\x", r"\x"), (r"\xg", r"\xg"), (r"\x4", "\x04"), (r"\x41", "A"), (r"\x414", "A4"), (r"\x4g", "\x04g"),
    (r"a\x0z", "a"), (r"a\x00z", "a"), (r"\x{41}", "A"), (r"\x{141}", "A"), (r"\x{41", "A"), (r"a\x{}z", "a"),
    (r"a\x{zz}b", "a"), (r"\x{100}z", ""), (r"\xc3\xa9", "\xe9"), (r"\xff", "\xff"), (r"\377", "\xff"),
    # \u: 1 to 4 digits; \U: 1 to 8
    (r"\u", r"\u"), (r"\ug", r"\ug"), (r"\u65", "e"), (r"\u2d\u2d", "--"), (U + "0041", "A"), (U + "00410", "A0"),
    (U + "00e9", "\xe9"), (U + "20ac", "\N{EURO SIGN}"), (U + "FFFF", chr(0xFFFF)), ("a" + U + "0000z", "a"),
    (r"a\u0z", "a"), (r"\U", r"\U"), (r"\U65", "e"), (r"\U00000041", "A"), (r"\U000000410", "A0"),
    (r"\U0001F600", "\U0001F600"), (r"\U10FFFF", "\U0010FFFF"), (r"a\U0z", "a"),
    # \cX: X & 0x1f, \c? is DEL, \c\\ reads one backslash, a lone \c stays
    (r"\cA", "\x01"), (r"\ca", "\x01"), (r"a\c@z", "a"), (r"\c?", "\x7f"), (r"\c[", "\x1b"), (r"\c1", "\x11"),
    (r"\cz\cZ", "\x1a\x1a"), (r"\c\\", "\x1c"), (r"\c\\z", "\x1cz"), (r"\c\x41", "\x1cx41"), (r"\c", r"\c"),
    (r"a\c z", "a"), ("\\c\xe9", "\x03\xa9"),
    # unknown escapes keep the backslash; a trailing lone backslash stays (bash rejects the unclosed quote)
    (r"\z", r"\z"), (r"\q\%\ ", r"\q\%\ "), (r"\d\w", r"\d\w"), ("\\\xe9", "\\\xe9"), ("\xe9", "\xe9"),
    ("abc\\", "abc\\"),
]


@pytest.mark.parametrize("body,text", DECODED)
def test_ansi_c_decoder_matches_bash(body, text):
    assert gate._ansi_c(body) == text


# A \u or \U value that is no character, as bash 5.2.21 writes it in C.UTF-8 (bytes that are not UTF-8 read as
# Latin-1): u32toutf8 writes up to six bytes, surrogates included, and nothing from 0x80000000 on.
NO_CHARACTER_BYTES = [
    (r"a\U80000000z", b"az"), (r"a\UFFFFFFFFz", b"az"), (r"a\U7FFFFFFFz", b"a\xfd\xbf\xbf\xbf\xbf\xbfz"),
    (r"a\U04000000z", b"a\xfc\x84\x80\x80\x80\x80z"), (r"a\U03FFFFFFz", b"a\xfb\xbf\xbf\xbf\xbfz"),
    (r"a\U00200000z", b"a\xf8\x88\x80\x80\x80z"), (r"a\U001FFFFFz", b"a\xf7\xbf\xbf\xbfz"),
    (r"a\U00110000z", b"a\xf4\x90\x80\x80z"), ("a" + U + "D800z", b"a\xed\xa0\x80z"),
    ("a" + U + "DFFFz", b"a\xed\xbf\xbfz"), (r"a\U0000D800z", b"a\xed\xa0\x80z"),
]


@pytest.mark.parametrize("body,data", NO_CHARACTER_BYTES)
def test_ansi_c_decoder_writes_a_value_that_is_no_character_as_bash_does(body, data):
    assert gate._ansi_c(body) == data.decode("latin-1")
