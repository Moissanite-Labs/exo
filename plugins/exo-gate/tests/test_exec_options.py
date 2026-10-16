"""D-038 follow-ups: ssh chains past the depth cap ask (#153), the exec options of allowlisted programs
are followed (#154), and environment dumps are seen behind runners and prefixes (#155).

Every string here is data for the classifier. Nothing is run; the payloads are placeholders.
"""
import time

import pytest

from gate_fixtures import TEMPLATE
from exo.gate import classify, decide, load_policies, normalize_policies

DENY = normalize_policies({"gates": {"deny": ["delete", "credentials", "vcs_push", "force_push"]}})
DENY_H0 = normalize_policies({"gates": {"deny": ["target:h0"]}})


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".exo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("EXO_HOME", str(h / ".exo"))
    for k in ("EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL"):
        monkeypatch.delenv(k, raising=False)
    return h


def sh(cmd):
    return ("bash", {"command": cmd})


def hops(prog, n, tail):
    return " ".join(f"{prog} h{i}" for i in range(n)) + " " + tail


# --- #153: ssh/mosh chains past the depth cap ------------------------------------------------

def test_four_hops_are_read_as_before():
    c = classify(sh(hops("ssh", 4, "rm -rf /srv/data")))
    assert c.category == "delete"
    assert c.exposure == "internal" and "remote host" in c.signals
    assert decide(c).outcome == "ask"
    assert decide(c, DENY).outcome == "deny"


@pytest.mark.parametrize("cmd", [
    hops("ssh", 5, "rm -rf /srv/data"),
    hops("ssh", 6, "git push --force origin main"),
    hops("mosh", 5, "rm -rf /srv/data"),
    hops("ssh", 5, "ls"),
])
def test_chain_past_the_cap_asks(cmd):
    c = classify(sh(cmd))
    assert c.category == "opaque_exec", c
    assert c.exposure == "internal" and "remote host" in c.signals and "indirection" in c.signals
    assert decide(c).outcome == "ask"
    assert decide(c, DENY).outcome != "allow"
    assert decide(c, load_policies(TEMPLATE)).outcome != "allow"


def test_chain_past_the_cap_keeps_the_first_host_as_target():
    c = classify(sh(hops("ssh", 5, "rm -rf /srv/data")))
    assert c.target == "h0"
    assert decide(c, DENY_H0).outcome == "deny"
    assert classify(sh("ssh h0 ls")).target == "h0"


# --- #154: exec options of allowlisted programs ----------------------------------------------

RM = "rm -rf /srv/data"
PUSH = "git push --force origin main"

EXEC_OPTIONS = [
    (f"tar -xf a.tar --to-command='{RM}'", "delete"),
    (f"tar -xf a.tar --to-command '{RM}'", "delete"),
    (f"tar -xf a.tar -I '{RM}'", "delete"),
    (f"tar -xf a.tar -xI '{RM}'", "delete"),
    (f"tar -cf a.tar --use-compress-program='{PUSH}' .", "force_push"),
    (f"tar -cf a.tar --use-compress-program '{PUSH}' .", "force_push"),
    (f"tar -cf a.tar --rsh-command='{RM}' -f h:a.tar .", "delete"),
    (f"tar -cf a.tar --rmt-command='{RM}' .", "delete"),
    (f"tar -cf a.tar --checkpoint=1 --checkpoint-action='exec={RM}' .", "delete"),
    (f"split --filter='{RM}' big.txt", "delete"),
    (f"split --filter '{RM}' big.txt", "delete"),
    (f"sort --compress-program='{RM}' big.txt", "delete"),
    (f"sort --compress-program '{RM}' big.txt", "delete"),
    (f"man -P '{RM}' ls", "delete"),
    (f"man --pager='{RM}' ls", "delete"),
    (f"man --pager '{RM}' ls", "delete"),
    (f"rsync -e '{RM}' a b", "delete"),
    (f"rsync -avze '{RM}' a h:b", "delete"),
    (f"rsync --rsh='{RM}' a b", "delete"),
    (f"rsync --rsh '{RM}' a b", "delete"),
    (f"rsync --rsync-path='{PUSH}' a h:b", "force_push"),
    (f"scp -S '{RM}' a h:b", "delete"),
    (f"ssh -o ProxyCommand='{RM}' h", "delete"),
    (f"ssh -o 'LocalCommand {RM}' -o PermitLocalCommand=yes h", "delete"),
    (f"scp -o ProxyCommand='{RM}' a h:b", "delete"),
    (f"rg --pre '{RM}' needle .", "delete"),
    (f"zip -T -TT '{RM}' a.zip", "delete"),
    (f"less +'!{RM}' f", "delete"),
    (f"sqlite3 x.db '.shell {RM}'", "delete"),
    (f"sqlite3 x.db '.system {RM}'", "delete"),
    (f"sqlite3 x.db '.sh {RM}'", "delete"),
    (f"sqlite3 -cmd '.shell {RM}' x.db", "delete"),
    (f"sqlite3 x.db \".once |{RM}\"", "delete"),
    (f"sqlite3 x.db \".output |{RM}\" 'select 1'", "delete"),
    (f"sqlite3 x.db \".import '|{RM}' t\"", "delete"),
    (f"sqlite3 x.db $'select 1;\\n.shell {RM}'", "delete"),
    (f"sqlite3 x.db <<EOF\n.shell {RM}\nEOF", "delete"),
    (f"duckdb x.db '.shell {RM}'", "delete"),
    (f"psql -c '\\! {RM}'", "delete"),
    (f"psql <<EOF\n\\! {RM}\nEOF", "delete"),
    (f"mysql -e 'system {RM}'", "delete"),
    (f"GIT_SSH_COMMAND='{RM}' git fetch origin", "delete"),
    (f"RSYNC_RSH='{RM}' rsync a h:b", "delete"),
    (f"PAGER='{RM}' man ls", "delete"),
    (f"env GIT_SSH_COMMAND='{RM}' git fetch origin", "delete"),
    (f"export GIT_SSH_COMMAND='{RM}'; git fetch origin", "delete"),
    (f"LESSOPEN='|{RM} %s' less f", "delete"),
    # git options that run a command
    (f"git rebase -x '{RM}' main", "delete"),
    (f"git rebase --exec '{RM}' main", "delete"),
    (f"git rebase --exec='{RM}' main", "delete"),
    (f"git -C /tmp/r rebase -x '{RM}' main", "delete"),
    (f"git bisect run {RM}", "delete"),
    (f"git submodule foreach '{RM}'", "delete"),
    (f"git submodule foreach --recursive '{RM}'", "delete"),
    (f"git clone --upload-pack='{RM}' /tmp/r", "delete"),
    (f"git clone -u '{RM}' /tmp/r", "delete"),
    (f"git fetch --upload-pack '{RM}' origin", "delete"),
    (f"git ls-remote --upload-pack='{RM}' /tmp/r", "delete"),
    (f"git push --receive-pack='{RM}' origin main", "delete"),
    (f"git archive --exec='{RM}' --remote=/tmp/r HEAD", "delete"),
    # unambiguous abbreviations of long options, as getopt_long takes them
    (f"tar -xf a.tar --to-comm='{RM}'", "delete"),
    (f"tar -xf a.tar --to-com '{RM}'", "delete"),
    (f"tar -cf a.tar --use-compress-prog='{RM}' .", "delete"),
    (f"tar -cf a.tar --use-compress='{RM}' .", "delete"),
    (f"tar -cf a.tar --rsh='{RM}' -f h:a.tar .", "delete"),
    (f"split --fil='{RM}' big.txt", "delete"),
    (f"sort --compress-pro='{RM}' big.txt", "delete"),
    (f"sort --compress='{RM}' big.txt", "delete"),
    (f"man --pag='{RM}' ls", "delete"),
    (f"man --pag '{RM}' ls", "delete"),
    # the value attached inside a short-option cluster
    (f"tar -xI'{RM}' -f a.tar", "delete"),
    (f"rsync -avze'{RM}' a h:b", "delete"),
    (f"scp -rS'{RM}' a h:b", "delete"),
    (f"man -aP'{RM}' ls", "delete"),
    # psql meta-commands anywhere on a line; mysql \!
    (f"psql <<EOF\nselect 1; \\! {RM}\nEOF", "delete"),
    (f"psql -c '\\o |{RM}'", "delete"),
    (f"psql -c '\\g |{RM}'", "delete"),
    (f"psql -c '\\w |{RM}'", "delete"),
    (f"psql <<EOF\nselect 1 \\g |{RM}\nEOF", "delete"),
    (f"psql <<EOF\nselect 1 \\gx |{RM}\nEOF", "delete"),
    (f"mysql -e '\\! {RM}'", "delete"),
    # ssh settings that run a command
    (f"ssh -o RemoteCommand='{RM}' h", "delete"),
    (f"ssh -o KnownHostsCommand='{RM}' h", "delete"),
]


@pytest.mark.parametrize("cmd,category", EXEC_OPTIONS)
def test_exec_option_value_is_classified(cmd, category):
    c = classify(sh(cmd))
    assert c.category == category, c
    assert decide(c, DENY).outcome == "deny"
    assert decide(c).outcome in ("ask", "deny")


EXEC_OPAQUE = [
    "sqlite3 x.db '.output out.txt' 'select 1'",
    "sqlite3 x.db '.once out.txt' 'select 1'",
    "sqlite3 x.db '.read setup.sql'",
    "sqlite3 x.db '.load ./ext'",
    "sqlite3 -init setup.sql x.db",
    "psql -c \"copy t from program 'cat f'\"",
    "tar -cf a.tar --info-script=./next.sh .",
    "tar -cf a.tar -F ./next.sh .",
    "tar -xf a.tar --to-command=\"$CMD\"",
    "sort --compress-program=\"$PROG\" big.txt",
    "less +'|$cmd' f",
    "TAR_OPTIONS=--to-command=cat tar -xf a.tar",
    "psql -c \"copy t to program $$cat f$$\"",
    "psql -c \"copy t to program E'cat f'\"",
    "ssh -o PKCS11Provider=./lib.so h",
    "ssh -I ./lib.so h",
]


@pytest.mark.parametrize("cmd", EXEC_OPAQUE)
def test_exec_option_the_gate_cannot_read_is_opaque(cmd):
    c = classify(sh(cmd))
    assert c.category == "opaque_exec" and "indirection" in c.signals, c
    assert decide(c).outcome == "ask"


def test_exec_option_past_the_cap_is_opaque():
    cmd = hops("ssh", 4, "tar -xf a.tar --to-command='rm -rf /srv/data'")
    c = classify(sh(cmd))
    assert c.category == "opaque_exec" and decide(c).outcome == "ask"


def test_ssh_remote_command_keeps_the_remote_host_exposure():
    c = classify(sh("ssh -o RemoteCommand='rm -rf /srv/data' h"))
    assert c.exposure == "internal" and "remote host" in c.signals


@pytest.mark.parametrize("prog", ["psql", "sqlite3", "mysql"])
def test_long_stdin_text_is_read_in_linear_time(prog):
    body = "   \n" * 20000 + ".shell rm -rf /srv/data\n"
    start = time.perf_counter()
    c = classify(sh(f"{prog} x.db <<EOF\n{body}EOF"))
    assert time.perf_counter() - start < 2.0
    if prog == "sqlite3":
        assert c.category == "delete"


# Everyday uses of the same options stay allow: the value is a known program.
EXEC_EVERYDAY = [
    "tar -xf a.tar --to-command=cat",
    "tar -xf a.tar -I zstd",
    "tar -xf a.tar -I 'zstd -T0'",
    "tar -cf a.tar --use-compress-program=pigz .",
    "sort --compress-program=gzip big.txt",
    "split -l 100 big.txt",
    "man -P cat ls",
    "rsync -e ssh a h:b",
    "rsync -avze 'ssh -p 22' a h:b",
    "sqlite3 x.db '.tables'",
    "sqlite3 x.db 'select 1'",
    "sqlite3 x.db '.schema t'",
    "sqlite3 x.db '.system ls'",
    "sqlite3 -cmd 'select 1' x.db",
    "psql -c 'select 1'",
    "mysql -e 'select 1'",
    "PAGER=cat git log -3",
    "GIT_EDITOR=true git rebase --continue",
    "tar -xf a.tar -C /tmp/x",
    "sort big.txt",
    "less f",
    # an argument that looks like an assignment is not a command-valued variable
    "grep -rn 'EDITOR=vim' docs/",
    "printf 'GIT_SSH_COMMAND=%s\\n' 'ssh -i k'",
    "echo EDITOR=code",
    "export EDITOR=vim",
    "export GIT_EDITOR='code --wait'",
    # data loads, common compressors and other options that share a prefix
    "sqlite3 x.db '.import data.csv t'",
    "sqlite3 x.db '.import --csv data.csv t'",
    "sqlite3 x.db '.output'",
    "tar -I unpigz -xf a.tar.gz",
    "tar --use-compress-program=zstdmt -xf a.tar.zst",
    "tar --totals -cf a.tar .",
    "tar --checkpoint=100 -cf a.tar .",
    "sort --check big.txt",
    "ssh -o SecurityKeyProvider=internal h",
    "git rebase -i main",
    "git fetch -u origin",
    "git submodule update --init",
    "git bisect good",
]


@pytest.mark.parametrize("cmd", EXEC_EVERYDAY)
def test_everyday_exec_option_uses_stay_allow(cmd):
    c = classify(sh(cmd))
    assert decide(c).outcome == "allow", c
    assert decide(c, load_policies(TEMPLATE)).outcome == "allow", c


# --- #155: environment dumps behind runners and prefixes ---------------------------------------

ENV_DUMPS = [
    "uv run printenv GITHUB_TOKEN",
    "uv run --with rich printenv API_KEY",
    "uv run -- printenv GITHUB_TOKEN",
    "poetry run printenv GITHUB_TOKEN",
    "pdm run printenv GITHUB_TOKEN",
    "pipenv run printenv GITHUB_TOKEN",
    "hatch run printenv GITHUB_TOKEN",
    "coproc printenv GITHUB_TOKEN",
    "sudo printenv GITHUB_TOKEN",
    "sudo -u root printenv GITHUB_TOKEN",
    "nice printenv GITHUB_TOKEN",
    "nice -n 5 printenv GITHUB_TOKEN",
    "timeout 5 printenv GITHUB_TOKEN",
    "env printenv GITHUB_TOKEN",
    "env FOO=1 printenv",
    "ionice -c 3 printenv GITHUB_TOKEN",
    "stdbuf -oL printenv GITHUB_TOKEN",
    "uv run env",
    "uv run env FOO=1",
    "poetry run set",
    "uv run export -p",
    "uv run declare -x",
    "timeout 5 env",
    "sh -c 'uv run printenv GITHUB_TOKEN'",
    "if true; then printenv GITHUB_TOKEN; fi",
    "for x in 1; do printenv GITHUB_TOKEN; done",
    "while true; do printenv GITHUB_TOKEN; done",
]


@pytest.mark.parametrize("cmd", ENV_DUMPS)
def test_env_dump_behind_a_runner_is_credentials(cmd):
    c = classify(sh(cmd))
    assert c.category == "credentials" and "secret store" in c.signals, c
    assert decide(c, DENY).outcome == "deny"
    assert decide(c).outcome in ("ask", "deny")


@pytest.mark.parametrize("cmd", ["uvx printenv GITHUB_TOKEN", "pipx run printenv GITHUB_TOKEN",
                                 "npx printenv GITHUB_TOKEN"])
def test_opaque_runner_still_asks(cmd):
    c = classify(sh(cmd))
    assert c.category == "opaque_exec" and decide(c).outcome == "ask"


@pytest.mark.parametrize("cmd", [
    "uv run pytest -q", "uv run ls", "poetry run printenv HOME", "uv run env FOO=1 pytest", "timeout 5 pytest -q",
    "nice -n 5 pytest", "command -v printenv", "env -i pytest -q", "sudo -u root ls",
])
def test_runner_with_a_benign_command_is_unchanged(cmd):
    c = classify(sh(cmd))
    assert c.category != "credentials", c
    assert decide(c).outcome == "allow", c
