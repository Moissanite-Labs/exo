"""A store write or secret that sets a line's category keeps the line's other decisions (#464, #448).

`_combine` makes a `store_write` part the line's result (a credentials part outranks it), and a file tool on a
store or secret path was that category alone. The rest of the line then lost three kinds of policy entry: a part's
matrix cell (`decide` read the cell from the result's axes only), a `target:` entry on the descriptor folder a `cd`
entered, and a relative key read behind `tar -C` into a store. A file tool also lost its own category, so its
category deny, reserved entry and matrix cell no longer applied. Each line still asked the principal alone.
Classification and decision only; nothing is executed.
"""
import pytest

from exo import gate
from exo.gate import classify, decide, normalize_policies


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The default ~/.exo store under a temp HOME, with the key and the policy files the rows name."""
    h = tmp_path / "home"
    for d, files in {".ssh": ["id_rsa"], ".exo/policies": ["gates.yaml"], ".exo/keys": ["seat"], ".exo/state": []}.items():
        (h / d).mkdir(parents=True)
        for f in files:
            (h / d / f).write_text("")
    monkeypatch.setenv("HOME", str(h))
    for name in ("EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "CLAUDE_CONFIG_DIR", "EXO_SEAT"):
        monkeypatch.delenv(name, raising=False)
    return h


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def pol(**gates):
    return normalize_policies({"gates": gates})


DENY_IRREVERSIBLE = {"irreversible": {"public": "deny", "internal": "deny", "private": "deny"}}

# Each line is store_write: a matrix deny on its delete, force push or history rewrite asked on main (floor).
MATRIX_ROWS = [
    ("git push --force origin main ; echo x > ~/.exo/policies/a", "force_push"),
    ("rm -rf ~/work ; touch ~/.exo/state/x", "delete"),
    ("rm -rf ~/.exo/policies", "delete"),
    ("git -C ~/.exo/policies checkout .", "history_rewrite"),
]


@pytest.mark.parametrize("line,part", MATRIX_ROWS)
def test_a_parts_matrix_deny_holds_beside_a_store_write(line, part):
    c = classify(sh(line))
    assert c.category == "store_write", c
    d = decide(c, pol(matrix=DENY_IRREVERSIBLE))
    assert (d.outcome, d.rule) == ("deny", "matrix"), (line, d)
    assert d.reason.startswith("denied by policy (irreversible and "), d.reason      # names the part's cell
    assert any(p.category == part for p in gate._each_part(c)), c


@pytest.mark.parametrize("line,part", MATRIX_ROWS)
def test_the_store_floor_still_asks_without_a_deny_cell(line, part):
    for p in (None, pol(matrix={"irreversible": {"private": "ask", "internal": "ask"}})):
        d = decide(classify(sh(line)), p)
        assert (d.outcome, d.rule) == ("ask", "floor"), (line, d)


def test_the_store_write_alone_keeps_its_ask():
    for line in ("echo x > ~/.exo/policies/a", "touch ~/.exo/state/x"):
        d = decide(classify(sh(line)), pol(matrix=DENY_IRREVERSIBLE))
        assert (d.outcome, d.rule) == ("ask", "floor"), (line, d)


def test_a_category_line_or_rule_on_the_part_lifts_its_cell_as_it_would_alone():
    line = "rm -rf ~/work ; touch ~/.exo/state/x"
    assert decide(classify(sh("rm -rf ~/work")), pol(matrix=DENY_IRREVERSIBLE, categories={"delete": "ask"})).outcome \
        == "ask"
    for p in (pol(matrix=DENY_IRREVERSIBLE, categories={"delete": "allow"}),
              pol(matrix=DENY_IRREVERSIBLE, rules=[{"match": {"category": "delete"}, "decision": "allow"}])):
        d = decide(classify(sh(line)), p)
        assert (d.outcome, d.rule) == ("ask", "floor"), d


def test_a_command_rule_for_another_command_does_not_lift_the_cell():
    # #394: in the part pass an allow rule keyed on `command` must also name the part's own text
    p = pol(matrix=DENY_IRREVERSIBLE, rules=[{"match": {"command": "touch"}, "decision": "allow"}])
    d = decide(classify(sh("rm -rf ~/work ; touch ~/.exo/state/x")), p)
    assert (d.outcome, d.rule) == ("deny", "matrix"), d


def test_a_cell_below_the_dominant_parts_denies_the_line():
    # the push decides the line's cell (irreversible, internal), the delete alone is denied by its own cell
    p = pol(matrix={"irreversible": {"private": "deny", "internal": "allow", "public": "allow"}})
    assert decide(classify(sh("rm -rf ~/work")), p).outcome == "deny"
    c = classify(sh("git push --force origin main ; rm -rf ~/work"))
    assert c.category == "force_push", c
    d = decide(c, p)
    assert (d.outcome, d.reason) == ("deny", "denied by policy (irreversible and private: rm ~/work)"), d


def test_a_parts_cell_is_read_under_the_scopes():
    p = pol(matrix={"costly": {"public": "deny"}}, scopes={"internal": ["myorg/*"]})
    own = decide(classify(sh("echo x > ~/.exo/state/x; gh pr comment 1 -R myorg/app --body hi")), p)
    assert (own.outcome, own.rule) == ("ask", "floor"), own                       # the post is internal there
    other = decide(classify(sh("echo x > ~/.exo/state/x; gh pr comment 1 -R other/app --body hi")), p)
    assert (other.outcome, other.rule) == ("deny", "matrix"), other


def test_a_post_beside_a_store_write_keeps_its_matrix_deny():
    # the rows of the #437 comment: a post beside a store write or a descriptor path
    p = normalize_policies({"gates": {"matrix": {"costly": {"public": "deny"}}}, "posting": {"forbid_words": ["Zork"]}})
    for line in ("echo x > ~/.exo/state/x; gh pr comment 1 --body Zork", "eval /proc/self/fd/3; gh pr comment 1 --body hi",
                 "gh pr comment 5 --body-file /dev/stdin"):
        d = decide(classify(sh(line)), p)
        assert (d.outcome, d.rule) == ("deny", "matrix"), (line, d)


def test_the_default_policy_and_a_plain_line_are_unchanged():
    for line, want in (("rm -rf ~/work ; touch ~/.exo/state/x", ("ask", "floor")), ("rm -rf build", ("ask", "matrix")),
                       ("git status; rm -rf build", ("ask", "matrix")), ("ls; cat README.md", ("allow", "matrix"))):
        d = decide(classify(sh(line)))
        assert (d.outcome, d.rule) == want, (line, d)


# After `cd /dev` or `cd /proc/self/fd`, a write the gate pins to a store path took that path as its only target.
DEV_ROWS = [
    "cd /dev && cp x ~/.exo/policies/gates.yaml",
    "cd /proc/self/fd && cp 3 ~/.exo/state/approval-grants.json",
    "cd /dev && echo x > ~/.exo/policies/a",
    "cd /dev && printf x > ~/.exo/update/allowed_signers",
]


@pytest.mark.parametrize("line", DEV_ROWS)
def test_a_target_entry_on_the_descriptor_folder_still_matches(line):
    c = classify(sh(line))
    assert c.category == "store_write" and "/.exo/" in c.target, c               # the card still names the store path
    d = decide(c, pol(deny=["target:/dev*", "target:/proc*"]))
    assert (d.outcome, d.rule) == ("deny", "deny"), (line, d)
    assert decide(c).outcome == "ask", line


def test_the_descriptor_folder_is_a_target_only_after_cd():
    for line, want in (("cd /dev && cp x out/a.json", "deny"), ("cp x ~/.exo/policies/gates.yaml", "ask"),
                       ("cd ~/proj && cp x ~/.exo/policies/gates.yaml", "ask")):
        assert decide(classify(sh(line)), pol(deny=["target:/dev*", "target:/proc*"])).outcome == want, line


def test_a_key_read_behind_tar_into_a_store_is_a_credentials_part():
    line = "tar -C ~/.exo/policies -czf - ../keys/seat"
    c = classify(sh(line))
    assert c.category == "store_write", c                            # only the principal answers, as before
    assert [p.target for p in gate._each_part(c) if p.category == "credentials"] == ["../keys/seat"], c
    d = decide(c, pol(deny=["credentials"]))
    assert (d.outcome, d.rule) == ("deny", "deny"), d
    r = decide(c, pol(reserved=["credentials"]))
    assert r.outcome == "ask" and ("credentials", "../keys/seat") in r.reserved, r
    assert gate.approvers_for(c.category, {"default": ["seat:lead"], "categories": {"credentials": ["seat:lead"]}}) \
        == ["principal"]
    # the plain spelling was already credentials; a store file of its own is no key read
    assert decide(classify(sh("tar -C ~/.exo -czf - keys/seat")), pol(deny=["credentials"])).outcome == "deny"
    plain = classify(sh("tar -C ~/.exo/policies -czf - gates.yaml"))
    assert plain.category == "store_write" and not any(p.category == "credentials" for p in gate._each_part(plain))


# A file tool on a store or secret path took that category alone (#448).
FILE_ROWS = [
    ({"tool": "write_file", "args": {"path": "~/.exo/state/x", "content": "x"}}, "store_write", "local_write"),
    ({"tool": "edit_file", "args": {"path": "~/.exo/policies/gates.yaml", "old": "a", "new": "b"}}, "store_write",
     "local_write"),
    ({"tool": "patch", "args": {"path": "~/.exo/state/x", "patch": "x"}}, "store_write", "local_write"),
    ({"tool": "write_file", "args": {"path": "~/.ssh/config", "content": "x"}}, "credentials", "local_write"),
    ({"tool": "delete_file", "args": {"path": "~/.exo/state/x"}}, "store_write", "delete"),
    ({"tool": "read_file", "args": {"path": "~/.ssh/id_rsa"}}, "credentials", "read"),
    # the same calls under a camelCase name, which the name reading now read as its words (#515): main read it as
    # one word, so the delete of the folder that holds the store ran and a write named the unknown's reading
    ({"tool": "deleteFile", "args": {"path": "~/.exo"}}, "store_write", "delete"),
    ({"tool": "writeFile", "args": {"path": "~/.exo/policies/gates.yaml", "content": "x"}}, "store_write",
     "local_write"),
]


@pytest.mark.parametrize("action,cat,own", FILE_ROWS)
def test_a_file_tool_keeps_its_own_category_as_a_part(action, cat, own):
    c = classify(action)
    assert c.category == cat, c
    assert [(p.category, p.target) for p in c.parts] == [(own, action["args"]["path"])], c
    d = decide(c, pol(deny=[own]))
    assert (d.outcome, d.rule) == ("deny", "deny"), (action, d)
    d = decide(c, pol(categories={own: "deny"}))
    assert (d.outcome, d.rule) == ("deny", "category"), (action, d)
    r = decide(c, pol(reserved=[own]))
    assert r.outcome == "ask" and r.reserved == [(own, action["args"]["path"])], (action, r)
    assert decide(c).outcome == "ask", action                       # still the store or credentials ask
    assert c.category in gate.NO_GRANT                               # no standing grant covers it


def test_a_delete_tool_on_the_store_keeps_its_matrix_deny():
    c = classify({"tool": "delete_file", "args": {"path": "~/.exo/state/x"}})
    d = decide(c, pol(matrix=DENY_IRREVERSIBLE))
    assert (d.outcome, d.rule) == ("deny", "matrix"), d


# A camelCase tool name is read as its words beside the name's one word (#515), so it reads what its snake_case
# spelling reads. Main read the name as one word, and every camelCase row here was `unknown`.
CAMEL_FORMS = [
    ("deleteFile", "delete_file", "delete"),
    ("moveFile", "move_file", "local_write"),
    ("writeFile", "write_file", "local_write"),
    ("readFile", "read_file", "read"),
    ("sendMessage", "send_message", "message_send"),
    ("copyFile", "copy_file", "unknown"),
    ("openFile", "open_file", "unknown"),
    ("applyPatch", "apply_patch", "unknown"),
]


@pytest.mark.parametrize("name,snake,want", CAMEL_FORMS)
def test_a_camelcase_name_reads_as_its_snake_form(name, snake, want):
    assert gate.classify_by_name(name) == want, name
    if snake is not None:
        assert gate.classify_by_name(snake) == want, snake


def test_a_camelcase_delete_of_the_folder_that_holds_the_store_asks_the_principal():
    """`delete_file` on `~/.exo` asks; a camelCase name whose words are the same delete read as one word and ran
    (#515). Every decision on the snake spelling holds on the camel spelling."""
    for tool in ("delete_file", "deleteFile"):
        c = classify({"tool": tool, "args": {"path": "~/.exo"}})
        assert c.category == "store_write", (tool, c)
        assert c.category in gate.NO_GRANT, c
        assert decide(c).outcome == "ask", c
        loose = normalize_policies({"gates": {"categories": {"credentials": "allow", "read": "allow"}}})
        assert decide(c, loose).outcome == "ask", c


def test_a_camelcase_read_keeps_the_snake_forms_check():
    """`readFile` reads as `read_file` reads (#515), from the name: a `read`, so a camelCase read beside a secret
    is the same `credentials` call. Main read the camelCase name as one word, `unknown`; on a store file it asked
    the unnamed call's store-source reading, and `read_file`'s call rows keep that reading."""
    assert gate.classify_by_name("readFile") == gate.classify_by_name("read_file") == "read"
    assert gate.classify_by_name("listFiles") == gate.classify_by_name("list_files") == "read"


def test_a_camelcase_word_main_read_keeps_mains_class():
    """Every name main read stays read as it was, beside the words the humps add (#515): `apiKey` is the credential
    word, `checkOut` pays, `signUp` opens an account. Nothing main allowed reads lighter."""
    for name, want in (("apiKey", "credentials"), ("checkOut", "payment"), ("signUp", "account"),
                       ("logIn", "credentials"), ("payOut", "payment")):
        assert gate.classify_by_name(name) == want, name


def test_a_post_tool_on_a_store_path_keeps_its_text_for_the_posting_checks():
    # the tool's own outbound reading carries its text and title, so a forbidden word in either is listed
    p = normalize_policies({"posting": {"forbid_words": ["Zork"]}})
    for action, own in (({"tool": "mcp__slack__post_message", "args": {"channel": "C1", "text": "Zork",
                                                                       "file": "~/.exo/state/x"}}, "message_send"),
                        ({"tool": "mcp__github__create_issue", "args": {"repo": "o/r", "title": "Zork", "body": "hi",
                                                                        "file": "~/.exo/state/x"}}, "github_post")):
        c = classify(action)
        assert c.category == "store_write" and [q.category for q in c.parts] == [own], c
        d = decide(c, p)
        assert (d.outcome, d.rule) == ("ask", "posting") and any("forbidden word: Zork" in v for v in d.violations), d


def test_each_file_a_write_tool_writes_is_a_part():
    patch = ("*** Begin Patch\n*** Update File: notes.md\n@@\n-a\n+b\n*** Update File: ~/.exo/state/x\n@@\n-a\n+b\n"
             "*** End Patch\n")
    c = classify({"tool": "apply_patch", "args": {"patch": patch}})
    assert c.category == "store_write" and [q.target for q in c.parts] == ["notes.md", "~/.exo/state/x"], c
    d = decide(c, pol(deny=["target:notes.md"]))
    assert (d.outcome, d.rule) == ("deny", "deny"), d                # as `echo b > notes.md; echo x > ~/.exo/state/x`
    # a secret named in another argument sets the category, and the part is the file written
    c = classify({"tool": "write_file", "args": {"path": "notes.md", "source": "~/.ssh/id_rsa", "content": "x"}})
    assert c.category == "credentials" and [(q.category, q.target) for q in c.parts] == [("local_write", "notes.md")], c


def test_a_file_tool_on_a_plain_path_has_no_part():
    for action in ({"tool": "write_file", "args": {"path": "notes.md", "content": "x"}},
                   {"tool": "delete_file", "args": {"path": "build/x"}},
                   {"tool": "read_file", "args": {"path": "README.md"}}):
        assert classify(action).parts == [], action


def test_the_ledger_view_leaves_the_parts_out():
    c = classify({"tool": "delete_file", "args": {"path": "~/.exo/state/x"}})
    assert "parts" not in c.as_dict(), c.as_dict()
