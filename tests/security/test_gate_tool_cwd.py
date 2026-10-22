"""Issue #311: a shell call runs in its own `cwd` (or `workdir`) argument, so a relative read there of a secret store
asks, as `cd <folder> && <line>` does (D-038, #192).

The line keeps its own classification. The store read is added to it as a credentials part, which the credentials
floor asks for and no standing grant covers. So the line's deny entries, target rules, matrix cell, posting checks
and reserved entries all still apply (exo-36's review of #415). Every command string here is data for
exo.gate.classify/decide; none is run."""
import shlex
import time

import pytest

from exo import gate


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("EXO_HOME", str(home / ".exo"))
    for name in ("EXO_KEYS_FILE", "CLAUDE_CONFIG_DIR", "EXO_PRINCIPAL_STORE"):
        monkeypatch.delenv(name, raising=False)
    return home


def _bash(cmd: str):
    return gate.classify({"tool": "bash", "args": {"command": cmd}})


def _bash_in(cmd: str, folder: str, cwd: str | None = None, tool: str = "bash", key: str = "cwd"):
    action = {"tool": tool, "args": {"cmd" if tool == "exec_command" else "command": cmd, key: folder}}
    return gate.classify(action, cwd=cwd) if cwd else gate.classify(action)


def _store_parts(c) -> list:
    return [p for p in gate._each_part(c) if p.category == "credentials" and p is not c]


def _pol(raw: dict) -> dict:
    return gate.normalize_policies(raw)


MATRIX_DENY = _pol({"gates": {"matrix": {"irreversible": {"public": "deny", "internal": "deny"},
                                         "costly": {"public": "deny"}}}})
MATRIX_DENY_ALL = _pol({"gates": {"matrix": {"irreversible": {"private": "deny", "internal": "deny",
                                                              "public": "deny"},
                                             "costly": {"public": "deny"}}}})


# --- the store read asks ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd, folder", [
    # the issue rows: read or shell, allow on main
    ("cat config", "~/.kube"),
    ("cat *", "~/.ssh"),
    ("tar czf /tmp/o.tgz .", "~/.ssh"),
    ("cat hosts.yml", "~/.config/gh"),
    ("cat *", "~/.exo/run"),
    # the same folders spelt the other ways a cd target is spelt
    ("cat config", "$HOME/.kube"),
    ("cat config", "~/.kube/"),
    ("cat gh/hosts.yml", "~/.config"),
    # a key in ~/.ssh can have any name: the store folder itself counts every relative name, as `cd ~/.ssh` does
    ("cat deploy_key", "~/.ssh"),
    ("base64 github", "~/.ssh"),
    # and so does a folder below such a store, as after a `cd` there; below a .claude folder names count one by one
    ("cat deploy_key", "~/.ssh/old"),
    ("tar czf /tmp/o.tgz .", "~/.gnupg/private-keys-v1.d"),
    ("cat 4f3e9a.json", "~/.aws/cli/cache"),
    ("cat adc.json", "~/.config/gcloud/legacy_credentials/me"),
    ("cat deploy_key", "~/.ssh/.claude/x"),
    # wrappers, nested lines, substitutions and redirections reach the folder, as after a leading cd
    ("bash -c 'cat config'", "~/.kube"),
    ("sudo cat config", "~/.kube"),
    ("env X=1 cat config", "~/.kube"),
    ("timeout 5 cat config", "~/.kube"),
    ("echo $(cat config)", "~/.kube"),
    ("diff <(cat config) /tmp/x", "~/.kube"),
    ("(cat config)", "~/.kube"),
    ("ls && cat config", "~/.kube"),
    ("cat < config", "~/.kube"),
    ("wc -l < config > /tmp/n", "~/.kube"),
    ("cd gh && cat hosts.yml", "~/.config"),
    ("cat environ", "/proc/self"),
])
def test_a_tool_cwd_in_a_store_asks_as_the_cd_form_does(cmd, folder):
    c = _bash_in(cmd, folder)
    assert _store_parts(c), (cmd, folder)
    assert gate.decide(c).outcome == "ask"
    assert gate.no_grant(c)
    assert c.category == _bash(cmd).category          # the line keeps its own category
    assert gate.decide(_bash(f"cd {folder} && {cmd}")).outcome == "ask"


@pytest.mark.parametrize("tool, key", [
    ("terminal", "workdir"),          # Hermes' terminal tool
    ("exec_command", "workdir"),      # Codex's exec_command (`cmd`)
    ("terminal", "cwd"),
])
def test_the_folder_argument_of_other_shell_tools_counts(tool, key):
    c = _bash_in("cat config", "~/.kube", tool=tool, key=key)
    assert _store_parts(c) and gate.decide(c).outcome == "ask"


def test_a_call_with_both_folder_arguments_checks_each():
    for args in ({"cwd": "~/.kube", "workdir": "/srv/app"}, {"cwd": "/srv/app", "workdir": "~/.kube"}):
        c = gate.classify({"tool": "terminal", "args": {"command": "cat config", **args}})
        assert _store_parts(c) and gate.decide(c).outcome == "ask", args


def test_a_tool_cwd_absolute_or_joined_to_the_session_folder(_home):
    assert gate.decide(_bash_in("cat config", str(_home / ".kube"))).outcome == "ask"
    assert gate.decide(_bash_in("cat config", ".kube", cwd=str(_home))).outcome == "ask"


@pytest.mark.parametrize("raw", [
    {"gates": {"categories": {"read": "allow", "credentials": "allow"}}},
    {"gates": {"rules": [{"match": {"command": ".*"}, "decision": "allow"}]}},
    {"gates": {"rules": [{"match": {"category": "read"}, "decision": "allow"}]}},
    {"gates": {"matrix": {"reversible": {"private": "allow"}}, "escalate": {"categories": []}}},
    {"gates": {"scopes": {"private": ["*"]}}},
])
def test_no_policy_lowers_the_store_read(raw):
    """The credentials floor reads every part, so no category line, rule, matrix cell or scope lowers it."""
    assert gate.decide(_bash_in("cat config", "~/.kube"), _pol(raw)).outcome == "ask"


@pytest.mark.parametrize("raw", [{"gates": {"categories": {"credentials": "deny"}}},
                                 {"gates": {"deny": ["credentials"]}},
                                 {"gates": {"rules": [{"match": {"category": "credentials"}, "decision": "deny"}]}}])
def test_a_deny_on_credentials_denies_the_store_read(raw):
    assert gate.decide(_bash_in("cat config", "~/.kube"), _pol(raw)).outcome == "deny"


# --- the line keeps its own classification (exo-36's review of #415) -------------------------------------------

def test_a_target_deny_on_the_line_still_holds(_home):
    """#415 made the substitution's `cat config` the line's credentials result, so the target became `config` and
    `target:*/.ssh/*` no longer matched: deny to ask."""
    c = _bash_in("cat ~/.ssh/id_rsa <(cat config)", "~/.kube")
    assert c.target == str(_home / ".ssh" / "id_rsa")
    assert gate.decide(c, _pol({"gates": {"deny": ["target:*/.ssh/*"]}})).outcome == "deny"


def test_a_post_keeps_its_posting_checks():
    """#415 reclassified the post as credentials, so the posting checks never ran: no unseen-body violation."""
    c = _bash_in("gh pr comment 3 --body-file notes.md", "~/.ssh")
    assert c.category == "github_post" and _store_parts(c)
    d = gate.decide(c)
    assert d.outcome == "ask" and gate.UNSEEN_BODY in d.violations
    d = gate.decide(_bash_in("gh pr comment 3 --body hi", "~/.ssh"), _pol({"posting": {"footer": "-- bot"}}))
    assert d.outcome == "ask" and "missing footer: -- bot" in d.violations


@pytest.mark.parametrize("cmd, cat", [
    ("git push --force origin main", "force_push"),
    ("kubectl apply -f -", "deploy_production"),
    ("cd ~/.kube && echo '{\"kind\": \"Pod\", \"x\": 1}' | kubectl apply -f -", "deploy_production"),
])
def test_a_matrix_deny_cell_still_holds(cmd, cat):
    """The line keeps its axes, so a matrix cell that denies it still does. In ~/.ssh every relative word counts as
    a name there, so the call also has a store part and asks under the default policy."""
    c = _bash_in(cmd, "~/.ssh")
    assert c.category == cat and _store_parts(c)
    assert gate.decide(c, MATRIX_DENY_ALL).outcome == "deny"
    assert gate.decide(c).outcome == "ask"
    if cat == "force_push":
        assert gate.decide(c, MATRIX_DENY).outcome == "deny"


def test_a_target_rule_on_the_line_still_holds():
    """#415 counted `600` as the name read from ~/.ssh, so a rule on the line's target `config` no longer matched."""
    rule = _pol({"gates": {"rules": [{"match": {"target": "config"}, "decision": "deny"}]}})
    c = _bash_in("cd ~/.kube && chmod 600 config", "~/.ssh")
    assert c.target == "config"
    assert gate.decide(c, rule).outcome == "deny"


def test_a_reserved_entry_still_names_the_lines_target():
    """`cat config` runs on the remote host, not in the call's folder: the line is read as `cd ~/.kube && ssh ..`
    is. #415 read it in the folder too, and the reserved hit lost its host."""
    c = _bash_in("ssh host 'cat config'", "~/.kube")
    d = gate.decide(c, _pol({"gates": {"reserved": ["read"]}}))
    assert d.outcome == "ask" and ("read", "host") in d.reserved
    assert not _store_parts(c)


def test_a_line_that_already_reads_a_store_is_left_as_it_is():
    c = _bash_in("cd ~/.kube && cat config", "~/.ssh")
    assert c.category == "credentials" and not _store_parts(c)


# --- what stays as it was --------------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd, folder", [
    ("git status", "~/project"),          # the issue's control: an ordinary repo stays a read
    ("cat config", "~/project"),
    ("cat readme.md", "~"),
    ("ls", "~/.ssh"),
    ("cat README.md", "~/.exo/work"),
    # a checkout below a store folder checks names one by one, so routine commands there stay as they are
    ("ls src", "~/repo/.claude/worktrees/x"),
    ("cat README.md", "~/repo/.claude/worktrees/x"),
    ("pytest -q tests", "~/repo/.claude/worktrees/x"),
    ("git status", "~/repo/.claude/worktrees/x"),
    ("tar czf /tmp/src.tgz .", "~/repo/.claude/worktrees/x"),
    ("cat notes.md", "~/.kube/cache"),        # below ~/.kube names count one by one, as after a `cd` there
])
def test_a_tool_cwd_with_no_store_read_changes_nothing(cmd, folder):
    c = _bash_in(cmd, folder)
    assert not _store_parts(c), (cmd, folder)
    plain = _bash(cmd)
    assert (c.category, gate.decide(c).outcome) == (plain.category, gate.decide(plain).outcome)


def test_a_file_tools_cwd_argument_is_not_a_shell_cwd():
    # Only a shell call runs in its own folder argument; a file tool still reads from the session's folder.
    for key in ("cwd", "workdir"):
        c = gate.classify({"tool": "read_file", "args": {"path": "config", key: "~/.kube"}})
        assert c.category == "read" and not _store_parts(c)


# --- one check per call ----------------------------------------------------------------------------------------

def _nest(n: int) -> str:
    text = "cat " + " ".join(f"f{i}" for i in range(40))
    for _ in range(n):
        text = f"echo x y z $({text}) w"
    return text


@pytest.mark.parametrize("folder", ["~/.exo/work", "~/.kube"])
def test_a_nested_line_is_checked_once_for_the_tool_folder(folder, _home):
    """#415 checked the tool's folder again in every nested line. With a folder in $EXO_HOME each check spent the
    call's #269 budget again: a 12-level `$( )` line took 10x to 20x longer and then asked as `too many to resolve`.
    The folder is checked once, on the whole line. CPU time, the best of three, against the same line with no
    folder: a ratio with a margin, not a limit."""
    (_home / ".exo" / "work").mkdir(parents=True)
    line = _nest(12)
    took = []
    for args in ({"command": line, "cwd": folder}, {"command": line}):
        runs = []
        for _ in range(3):
            start = time.process_time()
            c = gate.classify({"tool": "bash", "args": args})
            runs.append(time.process_time() - start)
        took.append(min(runs))
        assert not _store_parts(c) and "too many to resolve" not in c.summary
    assert took[0] < 2 * took[1] + 0.05, took


def test_a_shell_text_nested_many_levels_still_reaches_the_folder():
    text = "cat config"
    for _ in range(4):
        text = "bash -c " + shlex.quote(text)
    c = _bash_in(text, "~/.kube")
    assert _store_parts(c) and gate.decide(c).outcome == "ask"
