"""#143, #553 (PR #158) composed with #479 (PR #838, main a1dd4fec): a ${..} word or a $(( in a loop that enters the store.

bash runs a loop body again after its own `cd` into a store folder, so on the next pass every command in it runs there
(#479), and it runs the command in a ${..} word or a $(( it reads as a subshell (#143, #553). Main a1dd4fec reads the
loop's second pass but ends a ${..} word at its first `}` and reads nothing in it; #158 without #838 reads the word
but reads the loop body once. So `for i in 1 2; do echo ${v:-$(cp /tmp/evil gates.yaml)}; cd ~/.exo/policies; done`,
which rewrites the gate's policy file on the second pass, was an allowed read on both. Here each reading of the call
(bash's reading of the words, and the reading as before #143, ``flat``) has its own loop reading (#838's
_loop_reading inside _classify_read), so the word's command is read in the store on the second pass, and every line
decides at least as main a1dd4fec does and as the word's plain form (the word replaced by what it holds) does.

Every command string here is data for exo.gate.classify/decide; none is run.
"""
import pytest

from exo import gate
from exo.gate import (OUTCOMES, binds_exact, call_approvers, classify, decide, no_grant, normalize_approvals,
                      normalize_policies, readings)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".exo" / "policies").mkdir(parents=True)
    (home / ".exo" / "state").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)


def sh(command):
    return classify({"tool": "bash", "args": {"command": command}})


def dq(s):
    """``s`` in double quotes for the outer shell."""
    return '"' + "".join("\\" + ch if ch in '\\$"`' else ch for ch in s) + '"'


CP = "cp /tmp/evil gates.yaml"
STORE = "~/.exo/policies"
POLICIES = {
    "default": None,
    "deny_store": {"gates": {"deny": ["store_write"]}},
    "cat_store": {"gates": {"categories": {"store_write": "deny", "credentials": "deny"}}},
    "reserved": {"gates": {"reserved": ["store_write", "delete", "opaque_exec"]}},
    "deny_delete": {"gates": {"deny": ["delete", "force_push"]}},
    "opaque_allow": {"gates": {"categories": {"opaque_exec": "allow"}}},
    "rule_cp_allow": {"gates": {"rules": [{"match": {"command": "cp"}, "decision": "allow"}]}},
    "rule_target": {"gates": {"rules": [{"match": {"target": "*gates.yaml*"}, "decision": "deny"}]}},
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


def store_write(c):
    return any(p.category == "store_write" for r in readings(c) for p in gate._each_part(r))


# --- 1. Fix rows: main a1dd4fec and #158 without #838 (its commit on main 34b5b495) read each as no store write -----
# The command in the word runs in the store on the loop's second pass: a store write, the principal's alone, denied
# under `deny: [store_write]`, with no standing grant.

FIX = [
    f"for i in 1 2; do echo ${{v:-$({CP})}}; cd {STORE}; done",
    f"while :; do echo ${{v:=$({CP})}}; cd {STORE}; done",
    f"until false; do echo ${{v:-`{CP}`}}; cd {STORE}; done",
    f"for i in 1 2; {{ echo ${{v:-`{CP}`}}; cd {STORE}; }}",
    f"select x in a b; do echo ${{v:-<({CP})}}; cd {STORE}; done",
    f"for ((i=0;i<2;i++)); do echo ${{x:-'}}'}} ; {CP}; cd {STORE}; done",
    f"for i in 1 2; do echo $(( ${{v:-$({CP})}} )); cd {STORE}; done",
    f"for i in 1 2; do echo ${{v:-$(X='a b' {CP})}}; cd {STORE}; done",
    f"while :; do echo ${{x:-'}}'}} ; {CP}; cd {STORE}; done",
    f"echo ${{v:-$(for i in 1 2; do {CP}; cd {STORE}; done)}}",
    f"echo $(( ${{v:-$(for i in 1 2; do {CP}; cd {STORE}; done)}} ))",
    "X='a b' env -S " + dq(f"for i in 1 2; do echo ${{v:-$({CP})}}; cd {STORE}; done"),
    f"sudo -s <<'END'\nfor i in 1 2; do echo ${{v:-$({CP})}}; cd {STORE}; done\nEND",
    f"for i in 1 2; do echo ${{v:-$(rm -f gates.yaml)}}; cd {STORE}; done",
]
FIX_IDS = ["for-cmdsub", "while-assign", "until-backquote", "for-brace-body", "select-procsub", "arith-for",
           "arith-word", "quoted-prefix-in-word", "quote-paired", "loop-in-word", "loop-in-arith-word",
           "env-s-quoted-prefix", "sudo-s-heredoc", "rm-in-word"]


@pytest.mark.parametrize("line", FIX, ids=FIX_IDS)
def test_a_word_in_a_loop_that_enters_the_store_runs_there_on_the_next_pass(line):
    """Each row fails on main a1dd4fec (no command read in the word) and on #158 without #838 (no second pass)."""
    c = sh(line)
    assert store_write(c), [(r.category, [p.category for p in r.parts]) for r in readings(c)]
    assert decide(c, NORM["deny_store"]).outcome == "deny"
    assert decide(c, NORM["cat_store"]).outcome == "deny"
    assert no_grant(c) and binds_exact(c)
    assert call_approvers(c, DELEGATED) == ["principal"]
    for name in NORM:
        assert rank(decide(c, NORM[name])) >= OUTCOMES.index("ask"), name


# --- 2. Guards: main a1dd4fec already reads these so (the loop's own words, a quoted prefix, #546) -----------------

GUARDS = [
    f"for i in 1 2; do cp ${{v:-/tmp/evil}} gates.yaml; cd {STORE}; done",
    f"for i in 1 2; do cp /tmp/evil ${{n:-gates.yaml}}; cd {STORE}; done",
    f"for i in 1 2; do cp /tmp/evil gates$((0)).yaml; cd {STORE}; done",
    f"for i in 1 2; do cp $((1)) gates.yaml; cd {STORE}; done",
    f"for i in 1 2; do {CP}; cd {STORE}/$((0))/..; done",
    f"for i in 1 2; do {CP}; cd ${{d:-{STORE}}}; done",
    f"for i in 1 2; do {CP}; cd \"${{d:-$HOME/.exo/policies}}\"; done",
    f"for i in 1 2; do X='a b' cp ${{v:-/tmp/evil}} gates.yaml; X='a b' cd {STORE}; done",
    f"for i in 1 2; do X='a b' echo ${{v:-$({CP})}}; cd {STORE}; done",
    f"for i in 1 2; do echo ${{v:-$(X='a b' {CP})}}; X='a b' cd {STORE}; done",
    f"for ((i=0;i<2;i++)); do echo ${{v:-$(rm -f gates.yaml)}}; cd {STORE}; done",
    # main reads the `$( )` in these words of an arithmetic `for` body
    f"for ((i=0;i<2;i++)); do echo ${{a:-${{b:-$({CP})}}}}; cd {STORE}; done",
    f"for ((i=0;i<2;i++)); do echo ${{v:-$({CP})}}; cd {STORE}; done",
    f"for ((i=0;i<2;i++)); {{ echo ${{v:-`{CP}`}}; cd {STORE}; }}",
]


@pytest.mark.parametrize("line", GUARDS)
def test_a_word_in_a_loop_that_enters_the_store_keeps_mains_store_write(line):
    """Main a1dd4fec already reads each as a store write; #158 keeps it (and #158 alone missed some of them)."""
    c = sh(line)
    assert store_write(c)
    assert decide(c, NORM["deny_store"]).outcome == "deny"
    assert no_grant(c) and binds_exact(c) and call_approvers(c, DELEGATED) == ["principal"]


@pytest.mark.parametrize("line", [
    f"for i in 1 2; do {CP}; echo ${{v:-$(cd {STORE})}}; done",
    f"for i in 1 2; do {CP}; : ${{v:=$(cd {STORE}; pwd)}}; done",
    f"for i in 1 2; do {CP}; echo $(( ${{v:-$(cd {STORE})}} )); done",
])
def test_a_cd_in_a_word_runs_in_a_subshell_and_leaves_the_loop_where_it_was(line):
    """bash runs the `cd` in a word's `$( )` in a subshell, so the loop's next pass does not run in the store: no
    store write, as on main a1dd4fec (which reads none of the word) and on #158 alone. The `cp` stays a write in the
    folder the call runs in."""
    c = sh(line)
    assert not store_write(c)
    assert decide(c, NORM["deny_store"]).outcome != "deny"


def test_a_cd_in_a_word_s_subshell_still_reads_its_own_writes_in_the_store():
    """The `cp` after the `cd` in the same `$( )` runs in the store (#465), on every pass."""
    c = sh(f"for i in 1 2; do : ${{v:=$(cd {STORE}; {CP})}}; done")
    assert store_write(c) and decide(c, NORM["deny_store"]).outcome == "deny"
    assert call_approvers(c, DELEGATED) == ["principal"]


# --- 3. Each reading keeps its loop reading ------------------------------------------------------------------------

def test_both_readings_of_the_words_have_their_own_second_pass():
    """bash's reading of the word and the reading as before #143 (``flat``, main's) are each read by _classify_read,
    which reads a loop that enters the store a second time (#838): the flat reading is main a1dd4fec's own, with its
    second pass beside it (its own ``also``), and it ends the bash reading's ``also`` chain, so decide, readings,
    binds_exact, no_grant and call_approvers read both."""
    c = sh(f"for i in 1 2; do echo ${{v:-$({CP})}}; cp /tmp/evil x.yaml; cd {STORE}; done")
    assert c.flat is not None
    chain = [c]
    while chain[-1].also is not None:
        chain.append(chain[-1].also)
    at = next(i for i, x in enumerate(chain) if x is c.flat)
    assert at > 0 and all(x.flat is None for x in chain[at:])     # the flat reading and its own loop reading last
    listed = readings(c)
    assert all(any(x is y for y in listed) for x in chain)
    # main's reading: the plain `cp` is a store write on the second pass (its loop reading leads), the word's command
    # is not read; bash's reading has the word's `cp` in the store too
    assert c.flat.category == "store_write" and len(chain) - at >= 2
    assert c.category == "store_write"
    assert decide(c, NORM["deny_store"]).outcome == "deny"


def test_a_word_only_bashs_reading_reads_has_its_second_pass_in_bashs_reading():
    """Main's own reading (``flat``) of a loop whose only write is in a word has no store write; bash's reading has
    it, from its own second pass."""
    c = sh(f"for i in 1 2; do echo ${{v:-$({CP})}}; cd {STORE}; done")
    assert c.flat is not None and c.flat.category != "store_write"
    assert not any(p.category == "store_write" for x in readings(c.flat) for p in gate._each_part(x))
    assert c.category == "store_write"


# --- 4. At least as main reads the word's plain form ----------------------------------------------------------------

WORDS = ["${v:-$(P)}", "${v:=$(P)}", "${v:-`P`}", "${v:-<(P)}", "$(( ${v:-$(P)} ))", "${a:-${b:-$(P)}}"]
PLAIN = ["$(P)", "$(P)", "`P`", "<(P)", "$(( $(P) ))", "$(P)"]
BODIES = ["echo W; cd S", "echo W; cp /tmp/evil x.yaml; cd S", "cd /tmp; echo W; cd S"]
LOOPS = ["for i in 1 2; do B; done", "while :; do B; done", "for i in 1 2; { B; }", "for ((i=0;i<2;i++)); do B; done"]
PAYLOADS = [CP, "rm -f gates.yaml", "X='a b' " + CP]
ROWS = [(lp, bd, w, pl, p) for lp in LOOPS for bd in BODIES for w, pl in zip(WORDS, PLAIN) for p in PAYLOADS][::2]


def _line(loop, body, word, payload):
    return loop.replace("B", body.replace("W", word.replace("P", payload)).replace("S", STORE))


@pytest.mark.parametrize("loop, body, word, plain, payload", ROWS)
def test_a_word_in_a_loop_decides_at_least_as_its_plain_form(loop, body, word, plain, payload):
    """``echo ${v:-$(cmd)}`` runs cmd as ``echo $(cmd)`` does, so the loop line with the word decides at least as
    the one with the plain substitution, which main a1dd4fec reads on both passes."""
    at_least(_line(loop, body, word, payload), _line(loop, body, plain, payload))
