"""Issue #311: a shell call runs in its own `cwd` (or `workdir`) argument, so a relative read there of a secret store
asks, as `cd <folder> && <line>` does (D-038, #192).

The line keeps its own classification. The store read is added to it as a credentials part, which the credentials
floor asks for and no standing grant covers. So the line's deny entries, target rules, matrix cell, posting checks
and reserved entries all still apply (exo-36's review of #415). Every command string here is data for
exo.gate.classify/decide; none is run."""
import json
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


def _repo(home) -> None:
    """~/repo, a git checkout, with a worktree in its own .claude folder and that folder's settings."""
    (home / "repo" / ".git").mkdir(parents=True)
    (home / "repo" / ".claude" / "worktrees" / "x" / "src").mkdir(parents=True)
    (home / "repo" / ".claude" / "worktrees" / "x" / ".git").write_text("gitdir: ../../../.git/worktrees/x\n")
    (home / "repo" / ".claude" / "settings.local.json").write_text("{}\n")


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
    # and so does a folder below such a store, as after a `cd` there
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
    # the folder is itself a store, as a `cd` word is: `ls` there lists it, as `ls ~/.ssh` does
    ("ls", "~/.ssh"),
    ("git status", "~/.ssh"),
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


# --- the folder never takes the line's place (exo-36's review of #452) -----------------------------------------

FORCE = "git push --force origin main"
SHELLS = [("terminal", "workdir"), ("exec_command", "workdir"), ("bash", "workdir"), ("bash", "cwd"),
          ("terminal", "cwd")]
HELD = ["~/.exo/run", "~/.exo/keys", "~/.claude/projects", "~/.claude"]    # every relative word there is a store hit


def _held(home) -> None:
    for d in (".exo/run", ".exo/keys", ".claude/projects/p"):
        (home / d).mkdir(parents=True, exist_ok=True)
    (home / ".exo" / "keys" / "seat.key").write_text("k\n")


@pytest.mark.parametrize("tool, key", SHELLS)
@pytest.mark.parametrize("folder", HELD)
@pytest.mark.parametrize("cmd", [f"tar czf /tmp/o.tgz . ; {FORCE}", f"cat * && {FORCE}", f"{FORCE} ; cat *"])
def test_a_folder_whose_every_word_is_a_store_keeps_the_lines_matrix_deny(cmd, folder, tool, key, _home):
    """A `workdir` in _Scope.cwds made the whole call credentials (#209): force_push deny became credentials ask.
    The folder now only adds a part, so the line's deny stands. Main did the same with `cwd`, which this ends."""
    _held(_home)
    c = _bash_in(cmd, folder, tool=tool, key=key)
    assert c.category == "force_push" and _store_parts(c), (tool, key, folder)
    assert gate.decide(c, MATRIX_DENY).outcome == "deny"
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("tool, key", SHELLS)
@pytest.mark.parametrize("folder", HELD)
def test_a_seat_that_answers_credentials_does_not_answer_the_force_push(folder, tool, key, _home):
    """approvers: {default: [principal], credentials: [principal, seat:builder]}. The call was credentials, so the
    seat could answer a force push only the principal answers."""
    _held(_home)
    table = gate.normalize_approvals({"approvers": {"default": ["principal"],
                                                    "credentials": ["principal", "seat:builder"]}})
    c = _bash_in(f"{FORCE} ; cat *", folder, tool=tool, key=key)
    assert gate.call_approvers(c, table) == ["principal"]


@pytest.mark.parametrize("raw", [
    {"gates": {"matrix": {"costly": {"private": "deny"}}}},             # the credentials cell, not the read's
    {"gates": {"categories": {"credentials": "deny"}}},
    {"gates": {"deny": ["credentials"]}},
])
def test_the_reading_from_before_still_decides_when_it_is_stricter(raw, _home):
    """`cat *` in ~/.exo/keys was credentials as a whole with `cwd` (#209), and a deny cell for that is still met.
    The line alone is a read, and its own cell would allow it. Since #464 the part's own cell denies it too."""
    _held(_home)
    for key in ("cwd", "workdir"):
        c = _bash_in("cat *", "~/.exo/keys", key=key)
        assert c.category == "read" and c.also is not None and c.also.category == "credentials"
        assert gate.decide(c, _pol(raw)).outcome == "deny", (raw, key)


def test_the_reading_from_before_keeps_its_reserved_hits(_home):
    _held(_home)
    c = _bash_in("cat *", "~/.exo/keys")
    d = gate.decide(c, _pol({"gates": {"reserved": ["credentials", "read"]}}))
    was = gate.decide(c.also, _pol({"gates": {"reserved": ["credentials", "read"]}}))
    assert d.outcome == "ask" and set(was.reserved) <= set(d.reserved)


# --- with #399's readings (#439) ---------------------------------------------------------------------------------
# gh finds a subcommand past an option, and the gate also reads such a line as it did before #399 (its floor), and
# decides a post on each other target gh may land on (alt_targets). In a call with its own folder the floor is read
# in that folder too, so it can carry its own reading from before #311. Each row here is decided at least as strictly
# as on main 8c09c29c, which reads the floor but not the folder's part (nor `workdir`), and as on this branch before
# that merge, which reads the folder but not the floor.

GH_MOVED = "gh pr -R pub/repo comment 5 -b x"     # gh comments on pub/repo#5; before #399 the gate read `gh pr -R`
ORG_IN_BODY = "gh pr comment 5 -R pub/repo -b 'https://github.com/myorg/app/pull/9 Note SECRETX'"
FOLDER_FORMS = [("bash", "cwd"), ("terminal", "workdir")]


@pytest.mark.parametrize("tool, key", FOLDER_FORMS)
def test_a_gh_line_in_its_folder_is_read_every_way(tool, key, _home):
    _held(_home)
    c = _bash_in(f"cat * ; {GH_MOVED}", "~/.exo/keys", tool=tool, key=key)
    rs = gate.readings(c)
    assert [r.category for r in rs] == ["github_post", "credentials", "read", "credentials"]
    assert rs[0] is c and rs[1] is c.also and rs[2] is c.floor and rs[3] is c.floor.also
    assert gate.no_grant(c) and gate.binds_exact(c)


@pytest.mark.parametrize("tool, key", FOLDER_FORMS)
@pytest.mark.parametrize("folder", ["~/.kube", "~/proj"])
def test_the_floor_is_decided_in_the_folder_too(folder, tool, key):
    """Under a scope that makes myorg private and a matrix that allows a private force push, the line as read now
    is allowed. Main asks, from the floor, and so did this branch, which read only the floor's way."""
    raw = {"gates": {"scopes": {"private": ["myorg/*"]},
                     "matrix": {"reversible": {"private": "allow", "internal": "allow", "public": "allow"},
                                "costly": {"private": "allow", "internal": "allow", "public": "allow"},
                                "irreversible": {"private": "allow", "internal": "allow", "public": "ask"}}}}
    c = _bash_in(f"{FORCE} ; gh -R pub/repo pr comment 5 -b x", folder, tool=tool, key=key)
    assert c.floor is not None
    assert gate.decide(c, _pol(raw)).outcome == "ask"


@pytest.mark.parametrize("tool, key", FOLDER_FORMS)
@pytest.mark.parametrize("cmd, want", [
    # main: ('', $R) with cwd, ($R) with workdir; before the merge: ('', the folder)
    ("gh -R $R secret set X", {("credentials", ""), ("credentials", "$R"), ("credentials", "{keys}")}),
    # main: ('', both posts) with cwd, (both posts) with workdir; before the merge: ('', the folder, myorg/app#9)
    (ORG_IN_BODY, {("credentials", ""), ("credentials", "{keys}"), ("github_post", "myorg/app#9"),
                   ("github_post", "pub/repo#5")}),
])
def test_each_reading_keeps_its_reserved_hits(cmd, want, tool, key, _home):
    _held(_home)
    keys = str(_home / ".exo" / "keys")
    c = _bash_in(cmd, "~/.exo/keys", tool=tool, key=key)
    d = gate.decide(c, _pol({"gates": {"reserved": ["credentials", "github_post"]}}))
    assert d.outcome == "ask"
    assert {(cat, t.format(keys=keys)) for cat, t in want} <= set(d.reserved), d.reserved


@pytest.mark.parametrize("tool, key", FOLDER_FORMS)
def test_each_reading_keeps_its_violations(tool, key, _home):
    """The post's own findings, and the same findings as the reading from before #311 names them after the post.
    Main lists them once with `workdir`, and this branch read the post on myorg/app#9 under the scope."""
    _held(_home)
    c = _bash_in(ORG_IN_BODY, "~/.exo/keys", tool=tool, key=key)
    d = gate.decide(c, _pol({"gates": {"scopes": {"internal": ["myorg/*"]}},
                             "posting": {"footer": "F", "forbid_words": ["SECRETX"]}}))
    assert (d.outcome, d.exposure) == ("ask", "public")
    for v in ("missing footer: F", "forbidden word: SECRETX"):
        assert sum(x.endswith(v) for x in d.violations) == 2, d.violations


@pytest.mark.parametrize("tool, key", FOLDER_FORMS)
@pytest.mark.parametrize("cmd", [GH_MOVED, f"cat config && {GH_MOVED}"])
def test_who_may_answer_reads_the_floor_in_the_folder(cmd, tool, key):
    """A seat that answers posts and credentials does not answer a line that was a read before #399, as on main."""
    table = gate.normalize_approvals({"approvers": {"default": ["principal"],
                                                    "github_post": ["principal", "seat:poster"],
                                                    "credentials": ["principal", "seat:poster"]}})
    c = _bash_in(cmd, "~/.kube", tool=tool, key=key)
    assert c.category == "github_post" and c.floor.category == "read"
    assert gate.call_approvers(c, table) == ["principal"]


@pytest.mark.parametrize("tool, key", FOLDER_FORMS)
@pytest.mark.parametrize("cmd, folder", [
    ("gh pr -R myorg/app view 5", "~/proj"),                                     # its floor
    ("gh pr view https://github.com/pub/x/pull/9 -R myorg/app", "~/proj"),       # another target
    ("cat config", "~/.kube"),                                                   # the folder's part
])
def test_a_call_read_more_than_one_way_binds_to_its_text(cmd, folder, tool, key):
    c = _bash_in(cmd, folder, tool=tool, key=key)
    d = gate.decide(c, _pol({"gates": {"scopes": {"private": ["myorg/*", "pub/*"]}}}))
    assert not gate.is_outward(c, d) and gate.binds_exact(c, d)
    plain = _bash_in("gh pr view 5 -R myorg/app", folder, tool=tool, key=key)
    assert not gate.binds_exact(plain, gate.decide(plain, _pol({"gates": {"scopes": {"private": ["myorg/*"]}}})))


def test_the_ledger_view_drops_the_reading_from_before_and_hides_a_targets_password(_home):
    _held(_home)
    secret = "u:tok" + "@ghe.example.com"         # split, so the privacy check does not read an address
    c = _bash_in(f"cat * ; gh pr comment 5 -R https://{secret}/pub/repo -b x", "~/.exo/keys")
    assert c.also is not None and any("tok" in t for t in c.alt_targets), c.alt_targets
    view = c.as_dict()
    assert "also" not in view and "floor" not in view and "tok" not in json.dumps(view), view


# --- a line that writes the Exo store (#464) ---------------------------------------------------------------------

WRITES = ["cat config; echo x > ~/.exo/policies/a", "cat config && touch ~/.exo/state/x", "cp config ~/.exo/state/c",
          "tar czf - . > ~/.exo/state/o.tgz", "cat * > ~/.exo/policies/gates.yaml"]


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir"), ("exec_command", "workdir")])
@pytest.mark.parametrize("folder", ["~/.kube", "~/.ssh", "~/.config/gh", "~/.exo/run"])
@pytest.mark.parametrize("cmd", WRITES)
def test_a_store_write_keeps_the_folders_read_as_a_part(cmd, folder, tool, key, _home):
    """Since #464 a store read after a `cd` on a store-write line is a credentials part beside the write. The
    folder's read was left off such a line, so `deny: [credentials]` denied the cd form and only asked for this
    one. The line stays store_write, so only the principal answers it, also where credentials go to a seat."""
    _held(_home)
    c = _bash_in(cmd, folder, tool=tool, key=key)
    assert c.category == "store_write" and _store_parts(c), (cmd, folder)
    cd = _bash(f"cd {folder} && {cmd}")
    for raw in ({"gates": {"deny": ["credentials"]}}, {"gates": {"categories": {"credentials": "deny"}}}):
        assert gate.decide(c, _pol(raw)).outcome == gate.decide(cd, _pol(raw)).outcome == "deny", raw
    assert gate.decide(c).outcome == "ask"
    table = gate.normalize_approvals({"approvers": {"default": ["principal"],
                                                    "credentials": ["principal", "seat:builder"]}})
    assert gate.call_approvers(c, table) == ["principal"]


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
def test_a_target_entry_on_the_folder_denies_a_store_write_there(tool, key):
    c = _bash_in("cat * > ~/.exo/policies/gates.yaml", "~/.ssh", tool=tool, key=key)
    assert gate.decide(c, _pol({"gates": {"deny": ["target:*/.ssh"]}})).outcome == "deny"


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("cmd", ["cat id_rsa", "cat ./id_rsa"])
def test_a_line_that_reads_a_secret_itself_keeps_its_own_target(cmd, tool, key):
    """A limit in GATE.md: the line's own secret read wins, and the folder adds no part to it, so a target entry
    on the folder only asks where the cd form denies. That is no lower than main. `deny: [credentials]` denies
    both."""
    c = _bash_in(cmd, "~/.ssh", tool=tool, key=key)
    cd = _bash(f"cd ~/.ssh && {cmd}")
    folder = _pol({"gates": {"deny": ["target:*/.ssh"]}})
    assert c.category == "credentials"
    assert (gate.decide(c, folder).outcome, gate.decide(cd, folder).outcome) == ("ask", "deny")
    assert gate.decide(c, _pol({"gates": {"deny": ["credentials"]}})).outcome == "deny"


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("cmd", ["cat id_rsa; export {PATH,X}=; ls", "cat id_rsa; printf {-v,X,-v,PATH} .; ls",
                                 "cat id_rsa; echo {a,b}; ls ~/.exo/{run,keys}"])
def test_the_line_alone_gets_every_brace_pool_a_call_gets(cmd, tool, key):
    """The line alone is read with brace budgets of its own (_line_alone). _brace_budget makes them, so a check
    that keeps a pool of its own (#438 adds one for a builtin's names) finds it there as in any call."""
    c = _bash_in(cmd, "~/.ssh", tool=tool, key=key)
    assert any(p.category == "credentials" for p in gate._each_part(c))
    assert gate.decide(c, _pol({})).outcome == "ask"


# --- the folder's part weighs as the cd form's (#468) ------------------------------------------------------------

WEIGHED = [f"cat *; touch ~/.exo/state/x; {FORCE}", "cat *; touch ~/.exo/state/x; rm -rf build",
           "cat *; echo x > ~/.exo/policies/a; echo $(rm -rf x)", "cat config; echo $(touch ~/.exo/state/x; rm -rf y)",
           f"cat *; {FORCE}", "cat *; rm -rf build", "cat *; echo $(rm -rf x)"]
STORES = ["~/.kube", "~/.ssh", "~/.exo/run", "~/.exo/keys"]
IRREVERSIBLE = [      # (policy, the folders whose calls it denies)
    ({"gates": {"matrix": {"irreversible": {"private": "deny"}}}}, STORES),
    ({"gates": {"rules": [{"match": {"category": "credentials", "reversibility": "irreversible"},
                           "decision": "deny"}]}}, STORES),
    ({"gates": {"rules": [{"match": {"target": "*keys*", "reversibility": "irreversible"}, "decision": "deny"}]}},
     ["~/.exo/keys"]),
]


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir"), ("exec_command", "workdir")])
@pytest.mark.parametrize("folder", STORES)
@pytest.mark.parametrize("cmd", WEIGHED)
def test_the_folders_part_weighs_as_the_cd_forms(cmd, folder, tool, key, _home):
    """#468 weighs the credentials part a store read after a `cd` adds: the weight of the line's own parts, and a
    heavier command deeper in it as a part beside. The folder's part kept the credentials default (costly), so
    under an irreversible deny cell or rule `cd ~/.kube && cat *; touch ~/.exo/state/x; git push --force origin
    main` was denied and the same line with ~/.kube as `cwd` only asked. The line keeps its category and card."""
    _held(_home)
    c = _bash_in(cmd, folder, tool=tool, key=key)
    cd = _bash(f"cd {folder} && {cmd}")
    assert (c.category, c.summary) == (_bash(cmd).category, _bash(cmd).summary) and _store_parts(c)
    for raw, denied in IRREVERSIBLE:
        want = "deny" if folder in denied else "ask"
        assert gate.decide(c, _pol(raw)).outcome == gate.decide(cd, _pol(raw)).outcome == want, raw
    assert gate.decide(c).outcome == "ask"
    table = gate.normalize_approvals({"approvers": {"default": ["principal"],
                                                    "credentials": ["principal", "seat:builder"]}})
    assert gate.call_approvers(c, table) == ["principal"]


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("folder", STORES)
@pytest.mark.parametrize("cmd", ["cat *", "cat *; touch ~/.exo/state/x", "cat *; echo $(ls)"])
def test_a_line_with_nothing_heavier_than_the_read_keeps_its_weight(cmd, folder, tool, key, _home):
    """Controls: with no command heavier than the read, the part keeps the credentials weight. An irreversible deny
    does not reach it in either form, and a costly deny cell denies both."""
    _held(_home)
    c = _bash_in(cmd, folder, tool=tool, key=key)
    cd = _bash(f"cd {folder} && {cmd}")
    assert [p.reversibility for p in _store_parts(c)] == ["costly"]
    for raw, _ in IRREVERSIBLE:
        assert gate.decide(c, _pol(raw)).outcome == gate.decide(cd, _pol(raw)).outcome == "ask", raw
    costly = _pol({"gates": {"matrix": {"costly": {"private": "deny"}}}})
    assert gate.decide(c, costly).outcome == gate.decide(cd, costly).outcome == "deny"


@pytest.mark.parametrize("cmd", WEIGHED)
def test_a_folder_that_reads_no_store_adds_no_weight(cmd, _home):
    _held(_home)
    c = _bash_in(cmd, "~/proj")
    assert not _store_parts(c)
    for raw, _ in IRREVERSIBLE:
        assert gate.decide(c, _pol(raw)).outcome == gate.decide(_bash(cmd), _pol(raw)).outcome, raw


@pytest.mark.parametrize("folder", ["~/.kube", "~/.ssh"])
@pytest.mark.parametrize("cmd", [f"cat *; {FORCE}", "cat *; rm -rf build", "cat *; echo $(rm -rf x)"])
def test_the_read_keeps_its_own_cell_beside_the_weight(cmd, folder, _home):
    """The part for the read keeps the credentials weight, and a heavier command's weight is a second part beside
    it. So a matrix whose costly cell is stricter than its irreversible one still denies the folder form, as it did
    before this weight."""
    c = _bash_in(cmd, folder)
    assert sorted(p.reversibility for p in _store_parts(c)) == ["costly", "irreversible"]
    pol = _pol({"gates": {"matrix": {"costly": {"private": "deny"}, "irreversible": {"private": "allow"}}}})
    assert gate.decide(c, pol).outcome == "deny"


NAME_RULE = {"gates": {"rules": [{"match": {"category": "credentials", "target": "config"}, "decision": "deny"}]}}
PATH_ENTRY = {"gates": {"deny": ["target:*/.kube/*"]}}
LIFT = {"gates": {"matrix": {"irreversible": {"private": "deny"}},
                  "rules": [{"match": {"command": "git push"}, "decision": "allow"}]}}


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("cmd", ["cat config", f"cat config; {FORCE}"])
def test_a_rule_on_the_bare_name_reaches_only_the_cd_form(cmd, tool, key):
    """A limit in GATE.md: the part's target is the folder joined with the name, and the cd form's is the name
    alone. So a rule on `config` denies only the cd form, and an entry on the path only the folder form. The other
    form still asks. Main allows `cat config` here."""
    c = _bash_in(cmd, "~/.kube", tool=tool, key=key)
    cd = _bash(f"cd ~/.kube && {cmd}")
    assert (gate.decide(c, _pol(NAME_RULE)).outcome, gate.decide(cd, _pol(NAME_RULE)).outcome) == ("ask", "deny")
    assert (gate.decide(c, _pol(PATH_ENTRY)).outcome, gate.decide(cd, _pol(PATH_ENTRY)).outcome) == ("deny", "ask")


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir"), ("exec_command", "workdir")])
@pytest.mark.parametrize("folder", ["~/.ssh", "~/.exo/keys"])
def test_a_rule_that_names_a_line_of_one_command_lifts_its_parts_deny(folder, tool, key, _home):
    """A limit in GATE.md: the call's text has no `cd`, so an allow rule keyed on `command` that names a line of one
    command lifts the matrix deny on its parts, as on any such line (#394). The call still asks. Its cd form is
    denied, since the rule does not name the `cd`. On a line of two commands the deny stands."""
    _held(_home)
    c = _bash_in(FORCE, folder, tool=tool, key=key)
    assert _store_parts(c) and gate.decide(c, _pol(LIFT)).outcome == "ask"
    assert gate.decide(_bash(f"cd {folder} && {FORCE}"), _pol(LIFT)).outcome == "deny"
    assert gate.decide(_bash_in(f"cat *; {FORCE}", folder, tool=tool, key=key), _pol(LIFT)).outcome == "deny"


EVERYDAY = ["ls *", "ls config", "wc -l *", "grep -r foo *", "du -sh *", "rg -n config src", "file *"]


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("cmd", EVERYDAY)
def test_everyday_commands_in_a_repos_claude_worktree_change_nothing(cmd, tool, key, _home):
    """A checkout at <repo>/.claude/worktrees/x holds the repo's files, as StoreSkip judges it (#241): `*` there
    cannot reach .claude/settings.local.json, so these keep their class and outcome, as in ~/repo."""
    _repo(_home)
    c = _bash_in(cmd, "~/repo/.claude/worktrees/x", tool=tool, key=key)
    plain = _bash(cmd)
    assert not _store_parts(c), cmd
    assert (c.category, gate.decide(c).outcome) == (plain.category, gate.decide(plain).outcome)


@pytest.mark.parametrize("cmd, folder", [
    ("cat settings.local.json", "~/repo/.claude"),       # the repo's .claude folder itself is a store
    ("cat *", "~/repo/.claude"),
    ("ls *", "~/norepo/.claude/worktrees/x"),            # no .git beside it: not a repo's
    ("cat *", "~/.claude/projects"),                     # Claude's own config folder
])
def test_a_claude_folder_that_is_not_a_repos_worktree_still_asks(cmd, folder, _home):
    _repo(_home)
    (_home / "norepo" / ".claude" / "worktrees" / "x").mkdir(parents=True)
    (_home / ".claude" / "projects").mkdir(parents=True)
    c = _bash_in(cmd, folder)
    assert _store_parts(c) and gate.decide(c).outcome == "ask", (cmd, folder)


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("link, to, cmd", [(link, to, cmd) for link, to in [("link-ssh", ".ssh"), ("sshabs", None)]
                                           for cmd in ("cat deploy_key", "cat *", "cat config")]
                         + [("kubelink", ".kube", "cat config"), ("kubelink", ".kube", "cat *")])
def test_a_folder_that_is_a_link_into_a_store_asks(cmd, link, to, tool, key, _home):
    """The folder goes through the checks a `cd` word gets, which resolve links (`sshabs` is an absolute link). `cd
    ~/link-ssh && cat deploy_key` asks; the folder form was allowed. A link to ~/.kube, which is not a store itself,
    is read where it leads, so its store names count (the #312 family, for the folder argument only)."""
    (_home / ".ssh").mkdir()
    (_home / ".kube").mkdir()
    (_home / link).symlink_to(to or str(_home / ".ssh"))
    c = _bash_in(cmd, f"~/{link}", tool=tool, key=key)
    assert _store_parts(c) and gate.decide(c).outcome == "ask", (cmd, link)


@pytest.mark.parametrize("cmd", ["cat deploy_key", "tar czf /tmp/o.tgz .", "ls"])
def test_a_target_entry_on_the_folder_denies(cmd, _home):
    """Under deny: [target:*/.ssh], `cd ~/.ssh && cat deploy_key` is denied. The part's target was the relative
    name (`deploy_key`, or `czf` for the tar line), so the folder form only asked."""
    c = _bash_in(cmd, "~/.ssh")
    [part] = _store_parts(c)
    assert part.target == str(_home / ".ssh") and "czf" not in part.summary
    assert gate.decide(c, _pol({"gates": {"deny": ["target:*/.ssh"]}})).outcome == "deny"
    assert gate.decide(_bash(f"cd ~/.ssh && {cmd}"), _pol({"gates": {"deny": ["target:*/.ssh"]}})).outcome == "deny"


def test_a_name_read_in_the_folder_is_the_parts_target(_home):
    [part] = _store_parts(_bash_in("cat config", "~/.kube"))
    assert part.target == str(_home / ".kube" / "config")
    assert gate.decide(_bash_in("cat config", "~/.kube"), _pol({"gates": {"deny": ["target:*/.kube/*"]}})).outcome \
        == "deny"


@pytest.mark.parametrize("cwd, workdir", [("~/proj", "~/.exo"), ("~/.exo", "~/proj")])
def test_the_target_is_the_name_in_the_folder_it_resolves_in(cwd, workdir, _home):
    """With both folder arguments, the part named the first folder joined with the name, even when the name
    resolves into a store from the other one, so a target entry on the store missed the call."""
    _held(_home)
    (_home / "proj").mkdir()
    (_home / ".exo" / "keys" / "k1").write_text("k\n")
    c = gate.classify({"tool": "terminal", "args": {"command": "cat keys/k1", "cwd": cwd, "workdir": workdir}})
    [part] = _store_parts(c)
    assert part.target == str(_home / ".exo" / "keys" / "k1")
    assert gate.decide(c, _pol({"gates": {"deny": ["target:*/.exo/keys/*"]}})).outcome == "deny"


def _links(home) -> None:
    """In ~/proj, the session's folder: sdir -> ~/.exo/state, kl -> ~/.ssh, and xl -> a folder elsewhere."""
    for d in (".exo/state", ".exo/keys", ".exo/policies", ".ssh", "elsewhere/d", "proj"):
        (home / d).mkdir(parents=True, exist_ok=True)
    for name, to in (("sdir", ".exo/state"), ("kl", ".ssh"), ("xl", "elsewhere/d")):
        (home / "proj" / name).symlink_to(home / to)


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("folder", ["sdir/../policies", "sdir"])
def test_a_folder_through_a_link_is_read_past_the_link(folder, tool, key, _home):
    """The shell runs in `sdir/../policies` through the link sdir, as the kernel reads it: in ~/.exo/policies, where
    `../keys/seat` is ~/.exo/keys/seat. The reading from before #311 read the folder folded (~/proj/policies), so it
    found no store there, and the part named ~/proj/keys/seat, which a target entry on the store missed. #340's
    store-write checks (#527) read the folder joined too."""
    _links(_home)
    c = _bash_in("cat ../keys/seat", folder, cwd=str(_home / "proj"), tool=tool, key=key)
    assert [r.category for r in gate.readings(c)] == ["read", "credentials"]
    [part] = _store_parts(c)
    assert part.target == str(_home / ".exo" / "keys" / "seat")
    assert gate.decide(c, _pol({"gates": {"deny": ["target:*/.exo/*"]}})).outcome == "deny"


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
def test_a_folder_through_a_link_is_read_folded_too(tool, key, _home):
    """`xl/../kl` is ~/proj/kl, a link to ~/.ssh, as bash's `cd` folds it, and a folder beside xl's target as the
    kernel reads it. #209 read it folded, so `tar czf /tmp/o.tgz .` with it as `cwd` asked on main. The folder is
    read both ways, so a reading joined only would not lose that."""
    _links(_home)
    c = _bash_in("tar czf /tmp/o.tgz .", "xl/../kl", cwd=str(_home / "proj"), tool=tool, key=key)
    assert "credentials" in [r.category for r in gate.readings(c)] and gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("folder", ["~/.ssh\0", "\0", "~/repo/.claude/worktrees/x\0"])
def test_a_folder_with_a_nul_never_raises(folder, tool, key, _home):
    """classify never raises on odd arguments. A NUL in the folder made os.path.realpath raise, so the hook asked
    as for an unclassified call, which any seat may answer, where the line alone is denied."""
    _repo(_home)
    c = _bash_in(f"{FORCE} ; cat *", folder, tool=tool, key=key)
    assert c.category == "force_push" and gate.decide(c, MATRIX_DENY).outcome == "deny"


def test_a_relative_folder_when_the_gates_own_folder_is_gone(tmp_path, monkeypatch):
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    for tool, key in (("bash", "cwd"), ("terminal", "workdir")):
        c = _bash_in(f"{FORCE} ; cat *", ".ssh", tool=tool, key=key)
        assert c.category == "force_push" and gate.decide(c, MATRIX_DENY).outcome == "deny"


@pytest.mark.parametrize("tool, key", [("bash", "cwd"), ("terminal", "workdir")])
@pytest.mark.parametrize("sub, cmd", [(".kube", "cat config"), (".config/gh", "cat hosts.yml"),
                                      (".docker", "cat config.json"), ("src/.kube", "ls *")])
def test_a_store_folder_inside_a_repos_claude_worktree_asks(sub, cmd, tool, key, _home):
    """A checkout in <repo>/.claude/worktrees is the repo's, but a store folder by its name inside it is read as
    a `cd` there is, so `cat config` in x/.kube asks, as `cd ~/repo/.claude/worktrees/x/.kube && cat config` does."""
    _repo(_home)
    folder = f"~/repo/.claude/worktrees/x/{sub}"
    (_home / folder[2:]).mkdir(parents=True)
    c = _bash_in(cmd, folder, tool=tool, key=key)
    assert _store_parts(c) and gate.decide(c).outcome == "ask", (sub, cmd)
    assert gate.decide(_bash(f"cd {folder} && {cmd}")).outcome == "ask"


# --- what stays as it was --------------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd, folder", [
    ("git status", "~/project"),          # the issue's control: an ordinary repo stays a read
    ("cat config", "~/project"),
    ("cat readme.md", "~"),
    ("cat README.md", "~/.exo/work"),
    # a checkout in a repo's own .claude folder holds the repo's files, as for the search tools (#241)
    ("ls src", "~/repo/.claude/worktrees/x"),
    ("cat README.md", "~/repo/.claude/worktrees/x"),
    ("pytest -q tests", "~/repo/.claude/worktrees/x"),
    ("git status", "~/repo/.claude/worktrees/x"),
    ("tar czf /tmp/src.tgz .", "~/repo/.claude/worktrees/x"),
    ("cat notes.md", "~/.kube/cache"),        # below ~/.kube names count one by one, as after a `cd` there
])
def test_a_tool_cwd_with_no_store_read_changes_nothing(cmd, folder, _home):
    _repo(_home)
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
        # #320 reads the word that is a `$( )` alone as `*` in the folder, as after `cd` into it: one store read
        assert len(_store_parts(c)) == ("cwd" in args)
        assert not any("too many to resolve" in p.summary for p in gate._each_part(c))
    assert took[0] < 2 * took[1] + 0.05, took


def test_a_shell_text_nested_many_levels_still_reaches_the_folder():
    text = "cat config"
    for _ in range(4):
        text = "bash -c " + shlex.quote(text)
    c = _bash_in(text, "~/.kube")
    assert _store_parts(c) and gate.decide(c).outcome == "ask"
