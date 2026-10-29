"""#143, #553 (PR #158) composed with #471 (PR #546, main 34b5b495): a quoted prefix assignment and a ${..} word.

Bash reads `X='a b' cmd` as an assignment and runs cmd (#471), and it runs the command in a ${..} word or a $(( it
reads as a subshell (#143, #553). Main 34b5b495 reads the quoted prefix both ways (`Classification.before`: the
quoted word as the program, as main read it before #471) but ends a ${..} word at its first `}` and reads nothing
in it; the isolated head of #158 (63143c1f) reads the word but takes the quoted word for the program. So
`X='a b' echo ${v:-$(rm -rf /srv/data)}` asks under `deny: [delete]` on both, and `echo ${v:-$(X='a b' rm -rf
/srv/data)}` is an allowed read on main. Here classify reads the quoted prefix both ways around main's `env -S`
readings, and each of those both ways again for the words (as bash reads them, and as before #143 at the end of the
reading's `also` chain), so the command in the word is read behind, inside and around a quoted prefix, in an `env
-S` text and in a sudo shell's stdin. Each line also decides at least as it does with a plain prefix (`X=ab`) and
with the word quoted whole (`'X=a b'`, the program on every tree, as main read the quoted prefix before #471).

Every command string here is data for exo.gate.classify/decide; none is run.
"""
import pytest

from exo.gate import (OUTCOMES, binds_exact, call_approvers, classify, decide, no_grant, normalize_approvals,
                      normalize_policies, readings)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    (h / ".ssh").mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(k, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


def dq(s):
    """``s`` in double quotes for the outer shell."""
    return '"' + "".join("\\" + ch if ch in '\\$"`' else ch for ch in s) + '"'


RM = "rm -rf /srv/data"
PUSH = "git push --force origin main"
POST = "curl -X POST https://example.invalid/x -d @/etc/hostname"
CONFIG = "cp /tmp/x ~/.exo/config.json"
GATES = "~/.exo/policies/gates.yaml"
QUOTED = ["X='a b' ", 'X="$Y" ', "X=a\\ b "]

POLICIES = {
    "default": None,
    "deny4": {"gates": {"deny": ["force_push", "vcs_push", "delete", "web_post"]}},
    "cat_push": {"gates": {"categories": {"force_push": "deny"}}},
    "cat_delete": {"gates": {"categories": {"delete": "deny"}}},
    "cat_post": {"gates": {"categories": {"web_post": "deny"}}},
    "cat_store": {"gates": {"categories": {"store_write": "deny", "credentials": "deny"}}},
    "opaque_allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
    "opaque_deny": {"gates": {"categories": {"opaque_exec": "deny"}}},
    "reserved": {"gates": {"reserved": ["delete", "force_push", "store_write", "web_post", "opaque_exec"]}},
    "echo_allow": {"gates": {"rules": [{"match": {"command": "echo"}, "decision": "allow"}]}},
    "posting": {"gates": {"scopes": {"internal": ["myorg/*"]}}, "posting": {"footer": "F", "forbid_words": ["Zork"]}},
}
NORM = {k: normalize_policies(v) if v else None for k, v in POLICIES.items()}
DELEGATED = normalize_approvals({"approvers": {"default": ["principal", "seat:ops"]}})


def rank(d):
    return OUTCOMES.index(d.outcome)


def at_least(line, other):
    """``line`` decides at least as ``other`` under each policy: the outcome, each reserved hit and violation, the
    exact binding, no grant and the approvers."""
    c, o = sh(line), sh(other)
    for name, pol in NORM.items():
        d, e = decide(c, pol), decide(o, pol)
        assert rank(d) >= rank(e), (line, other, name, d, e)
        assert not [h for h in e.reserved if h not in d.reserved], (line, other, name, d.reserved, e.reserved)
        assert not [v for v in e.violations if v not in d.violations], (line, other, name)
        assert binds_exact(c, d) or not binds_exact(o, e), (line, other, name)
    assert no_grant(c) or not no_grant(o), (line, other)
    assert set(call_approvers(c, DELEGATED)) <= set(call_approvers(o, DELEGATED)), (line, other)


# --- 1. Fix rows: main 34b5b495 decides each lower (it reads no command in the word) ---------------------------------
# (line, policy, outcome). The rows marked "both" are decided lower by the isolated head 63143c1f too, which read the
# quoted word as the program and so never read the word after it, nor a quoted prefix inside a word.

FIX = [
    # a quoted prefix in front of the word's command
    (f"X='a b' echo ${{v:-$({RM})}}", "deny4", "deny"),
    (f"X='a b' echo ${{v:-$({PUSH})}}", "cat_push", "deny"),
    (f"X=\"$Y\" echo ${{v:=$({POST})}}", "cat_post", "deny"),
    (f"X=a\\ b echo ${{v:-`{RM}`}}", "cat_delete", "deny"),
    (f"X='a b' echo ${{x:-'}}'}} ; {RM}", "cat_delete", "deny"),
    # a quoted prefix inside the word's default (both)
    (f"echo ${{v:-$(X='a b' {RM})}}", "cat_delete", "deny"),
    (f"echo ${{a:-${{b:-$(X='a b' {PUSH})}}}}", "deny4", "deny"),
    (f"X=\"$Y\" echo ${{v:-$(X='a b' {PUSH})}}", "cat_push", "deny"),
    (f"git push origin feature && echo ${{v:=$(X='a b' {PUSH})}}", "cat_push", "deny"),
    (f"X='a b' cp ${{v:=$(X='a b' {RM})}} {GATES}", "cat_delete", "deny"),
    # in an `env -S` text (#591, #707)
    ("X='a b' env -S " + dq(f"echo ${{v:-$({RM})}}"), "deny4", "deny"),
    ("env -S " + dq(f"X='a b' echo ${{v:-$({PUSH})}}"), "cat_push", "deny"),
    ("X='a b' env -S " + dq(f"X='a b' echo ${{v:-<({PUSH})}}"), "cat_push", "deny"),
    ("env -S " + dq(f"X=\"$Y\" echo ${{v:-`X='a b' {PUSH}`}}"), "deny4", "deny"),
    # in a sudo -s or -i shell's stdin (#491)
    ("sudo -s <<< " + dq(f"X='a b' echo ${{v:-$({RM})}}"), "cat_delete", "deny"),
    (f"X='a b' sudo -s <<'END'\necho ${{v:-$({RM})}}\nEND", "cat_delete", "deny"),
    (f"sudo -i <<'END'\necho ${{v:-<(X='a b' {PUSH})}}\nEND", "cat_push", "deny"),
    (f"sudo -i <<'END'\nX='a b' cp ${{a:-${{b:-$(X='a b' {RM})}}}} {GATES}\nEND", "deny4", "deny"),
    # a word in an arithmetic $(( (#553): bash expands the word, and runs its command, before it does the arithmetic
    (f"X='a b' echo $(( ${{v:-$({RM})}} ))", "deny4", "deny"),
    (f"X='a b' echo $(( ${{v:-`{RM}`}} ))", "cat_delete", "deny"),
    (f"echo $(( ${{v:-$(X='a b' {RM})}} ))", "cat_delete", "deny"),
    ("X='a b' env -S " + dq(f"echo $(( ${{v:-$({RM})}} ))"), "deny4", "deny"),
    (f"X='a b' sudo -s <<'END'\necho $(( ${{v:-$({RM})}} ))\nEND", "cat_delete", "deny"),
    ("sudo -i <<< " + dq(f"X='a b' echo $(( ${{v:-$({PUSH})}} ))"), "cat_push", "deny"),
]
FIX_IDS = ["prefix-cmdsub", "prefix-cmdsub-push", "dq-prefix-assign-post", "escaped-prefix-backquote",
           "prefix-quote-paired", "in-word-delete", "in-nested-word", "both-sides",
           "after-push-in-word", "store-write-line-in-word", "before-env-s", "in-env-s", "both-env-s-procsub",
           "env-s-dq-backquote", "sudo-s-herestring", "before-sudo-s-heredoc", "sudo-i-procsub-in-word",
           "sudo-i-store-line", "arith-word", "arith-word-backquote", "arith-word-prefix-inside", "arith-env-s",
           "arith-sudo-s-heredoc", "arith-sudo-i-herestring"]


@pytest.mark.parametrize("line, policy, out", FIX, ids=FIX_IDS)
def test_a_word_behind_or_holding_a_quoted_prefix_is_read(line, policy, out):
    """Each row fails on main 34b5b495. The deny holds, the approval binds to the exact text, and the line asks at
    least under every other policy here but a rule that allows `echo` or `opaque_exec` (main's own limits)."""
    c = sh(line)
    assert decide(c, NORM[policy]).outcome == out, decide(c, NORM[policy])
    assert binds_exact(c, decide(c, NORM["default"]))
    for name in ("default", "deny4", "cat_push", "cat_delete", "cat_post", "cat_store", "reserved"):
        assert rank(decide(c, NORM[name])) >= OUTCOMES.index("ask"), name


@pytest.mark.parametrize("line", [
    # fix rows: main 34b5b495 reads none of the word (it asks, with no_grant False)
    f"echo ${{v:-$(X='a b' {CONFIG})}}",
    f"X='a b' sudo -s <<'END'\necho ${{v:=$({CONFIG})}}\nEND",
    f"echo $(( ${{v:-$(X='a b' {CONFIG})}} ))",
    # guards: main's store scan already finds the name in the text it reads
    f"X='a b' echo ${{v:-$({CONFIG})}}",
    "X='a b' env -S " + dq(f"echo ${{v:-$({CONFIG})}}"),
    "sudo -i <<< " + dq(f"X=\"$Y\" echo ${{v:-<({CONFIG})}}"),
])
def test_a_store_write_in_a_word_behind_or_holding_a_quoted_prefix_is_answered_by_the_principal(line):
    """The config-store reading (D-074) of a word's command behind or inside a quoted prefix: a store write in some
    reading, no standing grant, only the principal answers, and a store deny denies."""
    c = sh(line)
    assert "store_write" in {p.category for r in readings(c) for p in [r, *r.parts]}
    assert no_grant(c) and binds_exact(c)
    assert call_approvers(c, DELEGATED) == ["principal"]
    assert decide(c, NORM["cat_store"]).outcome == "deny"


@pytest.mark.parametrize("line, out", [
    # guards: main 34b5b495 already decides these so, through #546's reading of the command after the prefix
    (f"X='a b' cp ${{v:-x}} {GATES}", "deny"),
    (f"X='a b' cp ${{v:-$({CONFIG})}} {GATES}", "deny"),
    (f"X=\"$Y\" cp ${{x:-'}}'}} {GATES}", "deny"),
])
def test_a_store_write_behind_a_quoted_prefix_with_a_word_keeps_mains_deny(line, out):
    c = sh(line)
    assert decide(c, NORM["cat_store"]).outcome == out
    assert no_grant(c) and binds_exact(c) and call_approvers(c, DELEGATED) == ["principal"]


@pytest.mark.parametrize("line", [f"X='a b' echo $((echo \"((\" ; {PUSH}) )",
                                  f"sudo -s <<'END'\nX='a b' echo $((echo '((' ; {PUSH}) )\nEND",
                                  f"echo $(( $(X='a b' {PUSH}) ))",
                                  f"X='a b' echo $(( x[$({PUSH})] + 1 ))"])
def test_a_subshell_dollar_paren_behind_a_quoted_prefix_keeps_mains_deny(line):
    """A guard: main 34b5b495 reads a `$((` bash runs as a subshell (#553), and a `$( )` in an arithmetic `$((`,
    behind or inside a quoted prefix (#546) already. The isolated head 63143c1f asked for the third (it read the
    quoted word as the program)."""
    c = sh(line)
    assert decide(c, NORM["cat_push"]).outcome == "deny" and binds_exact(c)


# --- 2. The composition itself ----------------------------------------------------------------------------------------

def _chain(r):
    out = [r]
    while out[-1].also is not None:
        out.append(out[-1].also)
    return out


def test_the_reading_as_before_471_is_read_both_ways_too():
    """``before`` (main's own reading of the quoted prefix, #546) and each of its `env -S` readings end their `also`
    chain with the reading before #143 (``flat``), as the call and its own split readings do; readings() lists each,
    so decide, binds_exact, no_grant and call_approvers read them all."""
    c = sh("X='a b' true; env -S " + dq(f"echo ${{v:-$({PUSH})}}"))
    assert c.before is not None and c.split_readings and c.before.split_readings
    listed = readings(c)
    for r in (c, *c.split_readings, c.before, *c.before.split_readings):
        chain = _chain(r)
        assert r.flat is not None and chain[-1] is r.flat, [x.category for x in chain]
        assert all(any(x is y for y in listed) for x in chain)
        assert r.category == "force_push" and r.flat.category != "force_push"   # main reads no push in the word
    assert decide(c, NORM["cat_push"]).outcome == "deny"


def test_a_word_only_the_reading_as_before_471_reaches_keeps_its_own_flat_reading():
    """Behind a quoted prefix the reading as before #471 reads the quoted word as the program, so an `env -S` text
    after it is an argument there and no word is read in it; the reading after the prefix reads the text both ways."""
    c = sh("X='a b' env -S " + dq(f"echo ${{v:-$({PUSH})}}"))
    assert c.before is not None and c.before.flat is None and "unknown program" in c.before.signals
    assert c.flat is not None and c.category == "force_push"
    assert decide(c, NORM["cat_push"]).outcome == "deny"


def test_the_flat_reading_is_main_s_own_and_is_not_in_the_ledger_view():
    c = sh(f"echo ${{v:-$({PUSH})}}")
    assert c.flat is not None and c.flat.category == "read" and c.category == "force_push"
    assert "flat" not in c.as_dict() and "before" not in c.as_dict()
    plain = sh("echo hi")
    assert plain.flat is None and plain.before is None


# --- 3. At least as the plain prefix and as the word quoted whole -----------------------------------------------------

WORDS = ["${v:-$(P)}", "${v:=$(P)}", "${v:-`P`}", "${v:-<(P)}", "${v//x/$(P)}", "${a:-${b:-$(P)}}",
         '$((echo "((" ; P) )']
PAYLOADS = [RM, PUSH, POST, CONFIG, "cat ~/.ssh/id_rsa"]
REST = [f"echo {w.replace('P', p)}" for w in WORDS for p in PAYLOADS] + \
    [f"cp {w.replace('P', p)} {GATES}" for w in WORDS[:3] for p in (RM, PUSH)] + [f"echo ${{x:-'}}'}} ; {RM}"]
CONTEXTS = ["{q}{r}", "{q}env -S {dr}", "{q}sudo -s <<'END'\n{r}\nEND", "git push origin feature && {q}{r}"]
COMPOSED = [(ctx, r) for ctx in CONTEXTS for r in REST]


def _line(ctx, prefix, rest):
    return ctx.format(q=prefix, r=rest, dr=dq(rest))


@pytest.mark.parametrize("ctx, rest", COMPOSED)
def test_a_word_behind_a_quoted_prefix_decides_at_least_as_with_a_plain_one(ctx, rest):
    at_least(_line(ctx, "X='a b' ", rest), _line(ctx, "X=ab ", rest))


@pytest.mark.parametrize("ctx, rest", COMPOSED[::3])
def test_a_word_behind_a_quoted_prefix_decides_at_least_as_main_read_the_prefix(ctx, rest):
    at_least(_line(ctx, "X='a b' ", rest), _line(ctx, "'X=a b' ", rest))


@pytest.mark.parametrize("prefix", QUOTED)
@pytest.mark.parametrize("payload", [RM, PUSH, CONFIG])
def test_a_quoted_prefix_inside_a_word_decides_at_least_as_a_plain_one(prefix, payload):
    for w in WORDS:
        if "`" in w and "\\" in prefix:
            continue        # below: an escaped blank in a backquote, main's own limit
        at_least(f"echo {w.replace('P', prefix + payload)}", f"echo {w.replace('P', 'X=ab ' + payload)}")


@pytest.mark.parametrize("line", [f"echo `X=a\\ b {PUSH}`", f"echo ${{v:-`X=a\\ b {PUSH}`}}"])
def test_an_escaped_blank_in_a_backquote_asks(line):
    """A known limit of main 34b5b495: in a backquote the gate reads `X=a\\ b cmd` as the program `b`, where bash
    keeps the backslash and runs cmd. The plain backquote asks there (an unknown program), also under a deny on
    cmd's category, and a rule allowing `opaque_exec` allows it; in a word it asks the same here (main reads none
    of the word and allows the line)."""
    c = sh(line)
    assert decide(c, NORM["deny4"]).outcome == "ask"
    assert binds_exact(c)
