"""A write into a trusted bin folder asks, on the store's terms (#331).

`_SYSTEM_BIN` trusts a program by path in eight system folders (`/bin`, `/usr/bin`, `/usr/local/bin`,
`/opt/homebrew/bin`, `/sbin`, `/usr/sbin`, `/usr/local/sbin`, `/opt/local/bin`), and a `PATH` naming
those folders is trusted too. A Homebrew install makes them owned and writable by the user, not
root, so the write side was the hole: `cp /tmp/x /opt/homebrew/bin/rg` was `shell` and
`echo x > /opt/homebrew/bin/jq` was `local_write`, both allowed, and the planted file then ran
as `read` under a trusted name. Classification and decision only; nothing is executed.

The repair keeps the call's own category beside `bin_write`, so a deny on it still denies, and
counts only positions the command writes to, so reading a bin path stays a read.
"""
import pytest

from exo.gate import NO_GRANT, PRINCIPAL_ONLY, approvers_for, classify, decide, normalize_policies

POLICY = normalize_policies({})
LOWERED = normalize_policies({"gates": {"categories": {"bin_write": "allow"}}})
DENY_LOCAL = normalize_policies({"gates": {"categories": {"local_write": "deny"}}})
DENY_DELETE = normalize_policies({"gates": {"categories": {"delete": "deny"}}})
DENY_EMAIL = normalize_policies({"gates": {"categories": {"email_send": "deny"}}})
DENY_PUBLISH = normalize_policies({"gates": {"categories": {"publish": "deny"}}})
DENY_PAYMENT = normalize_policies({"gates": {"categories": {"payment": "deny"}}})
DENY_IRREV = normalize_policies({"gates": {"matrix": {"irreversible": {"private": "deny", "internal": "deny", "public": "deny"}}}})
RULE_DENY_COSTLY = normalize_policies({"gates": {"rules": [
    {"match": {"category": ["local_write", "shell", "read"]}, "decision": "deny", "reason": "costly denied"}]}})


def sh(command):
    return {"tool": "bash", "args": {"command": command}}


def outcome(call, policy=POLICY):
    c = classify(call)
    return c.category, decide(c, policy).outcome


ASK = [
    "cp /tmp/x /opt/homebrew/bin/rg",                       # the issue's rows
    "echo x > /opt/homebrew/bin/jq",
    "cp /tmp/x /usr/local/bin/rg",
    "echo x >> /opt/local/bin/jq",
    "mv /tmp/x /opt/homebrew/bin/rg",
    "install -m 755 /tmp/x /opt/homebrew/bin/rg",
    "dd if=/tmp/x of=/opt/homebrew/bin/rg",
    "ln -s /tmp/x /opt/homebrew/bin/rg",
    "echo x | tee /usr/local/bin/jq",
    "cp /tmp/x /usr/bin/rg",
    "cp /tmp/x /opt/homebrew/bin/",                         # the folder itself is the destination
    "cp -t /opt/homebrew/bin /tmp/x",
    "sed -i s/a/b/ /opt/homebrew/bin/jq",
    "curl -o /opt/homebrew/bin/x https://example.com/x",
    "gcc -o /opt/homebrew/bin/x /tmp/main.c",
    "echo x > /opt/homebrew/bin/../bin/jq",                 # the same folder spelled through ..
    "echo x > /opt/homebrew/bin/{jq,rg}",                  # a brace set naming files in it
    "cd /opt/homebrew/bin && cp /tmp/x rg",                 # a relative name after cd into it
    "cd /opt/homebrew/bin && echo x > jq",
]

ALLOW = [
    "/opt/homebrew/bin/rg x",                               # running from there is the trusted path
    "rg x",
    "cat /opt/homebrew/bin/rg",
    "file /opt/homebrew/bin/rg",
    "ls /opt/homebrew/bin",
    "cp /opt/homebrew/bin/rg /tmp/x",                       # copying out is a read of it
    "rsync /opt/homebrew/bin/rg /tmp/x",
    "ln -s /opt/homebrew/bin/rg /tmp/link",                 # a link pointing at it writes elsewhere
    "cp /tmp/x /tmp/y",
    "echo x > /tmp/jq",
    "echo x > /opt/homebrew/bin/sub/jq",                    # a subfolder is not a trusted folder
    "echo x > /home/user/bin/jq",                           # a user's own bin folder is not trusted
]


@pytest.mark.parametrize("line", ASK)
def test_a_write_into_a_trusted_bin_folder_asks(line):
    assert outcome(sh(line)) == ("bin_write", "ask"), line


# Spellings the first repair missed: an option value attached to its letter, a short option
# abbreviation, an option after the destination, a cd into an ancestor, an env -C, a tool cwd,
# and a variable naming the folder.
ASK_MORE = [
    "cp /tmp/x //opt/homebrew/bin/rg",                 # a leading // survives normpath
    "echo x > //opt/homebrew/bin/jq",
    "curl -o/opt/homebrew/bin/x https://example.com/x",
    "curl -sLo/usr/local/bin/x https://example.com/x",
    "wget -O/usr/local/bin/x https://example.com/x",
    "tar -C/opt/homebrew/bin -xf x.tar",
    "tar -xf x.tar -C /opt/homebrew/bin",
    "tar -C /opt/homebrew -xf x.tar",                  # an ancestor of the folder
    "tar xf x.tar -C /opt/homebrew/bin",               # old-style mode letters
    "tar -cf /opt/homebrew/bin/x.tar files",
    "cp --t=/opt/homebrew/bin /tmp/x",
    "cp /tmp/x -t /opt/homebrew/bin",
    "cp -rvt /opt/homebrew/bin /tmp/x",
    "install /tmp/x /opt/homebrew/bin/rg -m 755",
    "install -t /opt/homebrew/bin /tmp/x",
    "rsync -a /tmp/x /opt/homebrew/bin/ -e ssh",
    "rsync -e ssh -a /tmp/x /opt/homebrew/bin/",
    "rsync /tmp/x /opt/homebrew/bin/ --exclude '*.o'",
    "cd /opt/homebrew && cp /tmp/x bin/rg",
    "cd /opt/homebrew && cd bin && cp /tmp/x rg",
    "cd /opt/homebrew/bin && cd .. && cp /tmp/x bin/rg",
    "env -C /opt/homebrew/bin cp /tmp/x rg",
    "env -C=/opt/homebrew/bin cp /tmp/x rg",
    "sudo -D /opt/homebrew/bin cp /tmp/x rg",
    "command cp /tmp/x /opt/homebrew/bin/rg",
    'cp /tmp/x "$HOMEBREW_PREFIX/bin/rg"',
    "cp /tmp/x $HOMEBREW_PREFIX/bin/rg",
    "cd $HOMEBREW_PREFIX/bin && cp /tmp/x rg",
    "curl --output-dir /opt/homebrew/bin -O https://example.com/x",
    "curl -D /opt/homebrew/bin/headers https://example.com/x",
    "wget -P /opt/homebrew/bin https://example.com/x",
    "wget -o /opt/homebrew/bin/log https://example.com/x",
    "ln -s -t /opt/homebrew/bin x",
    "cd /opt/homebrew/bin && ln -s x rg",
    "cp /tmp/x /opt/homebrew/bin/{a,b}",
    "echo x > /opt/homebrew/bin/sub/../jq",
    "dd of=/opt/homebrew/bin/rg if=/tmp/x",
    "truncate -s 0 /opt/homebrew/bin/rg",
    "sed -i -e s/a/b/ /opt/homebrew/bin/jq",
    "gcc /tmp/main.c -o /opt/homebrew/bin/x",
    "cd /opt/homebrew/bin && gcc /tmp/main.c",          # no -o: a.out lands where it runs
    "env -C /opt/homebrew/bin cp /tmp/x rg && cp /tmp/x rg2",
    "cd /tmp && cd /opt/homebrew/bin && cd - && cd - && cp /tmp/x rg",
    "cd /tmp && cd /opt/homebrew/bin && cd -- - && cd - && cp /tmp/x rg",
    "pushd /opt/homebrew/bin && pushd /tmp && pushd && cp /tmp/x rg",
    "cd /opt/homebrew/bin && cd $D && cp /tmp/x rg",     # unpinned: keeps the folder, fails closed
    "install /tmp/x /opt/homebrew/bin/rg -S .bak",
    "rsync /tmp/x /opt/homebrew/bin/ -B 1024",
]


@pytest.mark.parametrize("line", ASK_MORE)
def test_more_plant_spellings_ask(line):
    assert outcome(sh(line)) == ("bin_write", "ask"), line


def test_a_relative_name_after_a_tool_cwd_asks():
    call = {"tool": "bash", "args": {"command": "cp /tmp/x rg", "cwd": "/opt/homebrew/bin"}}
    assert outcome(call) == ("bin_write", "ask")


@pytest.mark.parametrize("line", ALLOW)
def test_reads_runs_and_copies_out_stay_allowed(line):
    category, result = outcome(sh(line))
    assert category != "bin_write" and result == "allow", line


# Reads that write elsewhere, and a cd that leaves: only the write position counts, and the
# folder a cd leaves is the folder after it, not the bin folder before it.
ALLOW_MORE = [
    "cat /usr/local/bin/x > /tmp/y",
    "sha256sum /usr/local/bin/rg > /tmp/sums",
    "grep -r foo /opt/homebrew/bin > /tmp/out",
    "tar -czf /tmp/bin.tgz /usr/local/bin",
    "tar -tf x.tar -C /opt/homebrew/bin",
    "cd /opt/homebrew/bin && cd /tmp && echo x > y",
    "cd /opt/homebrew/bin && cd /tmp && cp /tmp/x rg",
    "cd $D && cp /tmp/x rg",                            # a bare variable names no folder
    "sort /usr/local/bin/x",
    "truncate -r /opt/homebrew/bin/rg -s 0 /tmp/x",     # the reference is read, not written
    "sed s/a/b/ /opt/homebrew/bin/jq",                  # no -i: a read
    "curl -O https://example.com/x",                    # the name comes from the URL, not a path
    "install -d /opt/homebrew/bin",                     # creating the folder is not a plant
    "cd /tmp && gcc /tmp/main.c",                       # a.out lands outside any bin folder
    "cd /opt/homebrew/bin && cd - && cp /tmp/x rg",     # back out: the session folder, not a plant
    "pushd /tmp && pushd /opt/homebrew/bin && pushd && cp /tmp/x rg",
    "rsync /tmp/x /tmp/y -B 1024",                      # the value after a plain destination is not a plant
]


@pytest.mark.parametrize("line", ALLOW_MORE)
def test_reads_and_leaving_cds_stay_allowed(line):
    category, result = outcome(sh(line))
    assert category != "bin_write" and result == "allow", line


# These ask on main as opaque_exec, not as bin_write: running or naming a bin path is not a plant.
@pytest.mark.parametrize("line", [
    "git config core.editor /usr/bin/vim",
    "python3 /usr/local/bin/script.py",
    "ldd /usr/bin/ls",
])
def test_runs_and_names_are_not_bin_writes(line):
    category, _ = outcome(sh(line))
    assert category != "bin_write", line


def test_the_bin_floor_holds_under_a_policy_that_allows_the_category():
    c = classify(sh("cp /tmp/x /opt/homebrew/bin/rg"))
    assert decide(c, LOWERED).outcome == "ask"


def test_a_bin_write_is_the_principals_alone_and_never_granted():
    assert "bin_write" in PRINCIPAL_ONLY and "bin_write" in NO_GRANT
    assert approvers_for("bin_write", None) == ["principal"]


def test_a_file_write_tool_into_a_trusted_bin_folder_asks():
    call = {"tool": "write_file", "args": {"path": "/opt/homebrew/bin/rg", "content": "x"}}
    assert outcome(call) == ("bin_write", "ask")
    call = {"tool": "write_file", "args": {"path": "/tmp/rg", "content": "x"}}
    assert outcome(call) == ("local_write", "allow")


def test_a_file_write_tool_into_a_bin_folder_spelled_with_two_slashes_asks():
    call = {"tool": "write_file", "args": {"path": "//opt/homebrew/bin/rg", "content": "x"}}
    assert outcome(call) == ("bin_write", "ask")


def test_a_downloading_tool_into_a_bin_folder_asks():
    call = {"tool": "download_file", "args": {"url": "https://example.com/x", "path": "/opt/homebrew/bin/x"}}
    assert outcome(call) == ("bin_write", "ask")


def test_a_move_tool_counts_only_its_destination():
    call = {"tool": "move_file", "args": {"source": "/opt/homebrew/bin/rg", "dest": "/tmp/rg"}}
    category, _ = outcome(call)
    assert category != "bin_write"
    call = {"tool": "move_file", "args": {"source": "/tmp/x", "dest": "/opt/homebrew/bin/rg"}}
    assert outcome(call) == ("bin_write", "ask")


def test_a_copy_tool_from_a_bin_folder_is_not_a_bin_write():
    call = {"tool": "copy_file", "args": {"source": "/opt/homebrew/bin/rg", "dest": "/tmp/rg"}}
    category, _ = outcome(call)
    assert category != "bin_write"


@pytest.mark.parametrize("call,policy", [
    ({"tool": "write_file", "args": {"path": "/opt/homebrew/bin/rg", "content": "x"}}, DENY_LOCAL),
    ({"tool": "delete_file", "args": {"path": "/usr/local/bin/rg"}}, DENY_DELETE),
    ({"tool": "send_email", "args": {"files": ["/usr/local/bin/x"]}}, DENY_EMAIL),
    ({"tool": "publish_package", "args": {"path": "/usr/local/bin/rg"}}, DENY_PUBLISH),
    ({"tool": "pay_invoice", "args": {"amount": 500, "path": "/usr/local/bin/x"}}, DENY_PAYMENT),
])
def test_a_deny_on_the_calls_own_category_survives_the_bin_floor(call, policy):
    assert outcome(call, policy)[1] == "deny", call


@pytest.mark.parametrize("line", [
    "ssh host 'dd if=/tmp/x of=/opt/homebrew/bin/rg'",
    "rm -rf /usr/local/bin/rg",
    "sort /usr/local/bin/x; git push --force origin main",
])
def test_a_matrix_deny_on_a_part_survives_the_bin_floor(line):
    assert outcome(sh(line), DENY_IRREV)[1] == "deny", line


def test_a_rule_deny_on_the_calls_own_category_survives_over_ssh_and_sudo():
    line = "sudo ssh host 'cp /tmp/x /usr/local/bin/rg'"
    assert outcome(sh(line), RULE_DENY_COSTLY)[1] == "deny"
    assert outcome(sh(line)) == ("bin_write", "ask")
