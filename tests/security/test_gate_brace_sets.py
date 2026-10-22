"""Issue #184: brace sets are expanded as bash does before secret-store paths are matched.

Every command string here is data for exo.gate.classify/decide; none is run."""
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


@pytest.mark.parametrize("cmd", [
    # read/allow on main 35b7de8
    "cat ~/.config/gh/{*,}",
    "cat ~/.config/gh/{hosts,x}.yml",
    "cat ~/.docker/{*,}",
    "cat ~/.kube/{*,}",
    "cat ~/{.netrc,x}",
    "cat {.env,x}",
    "cat ~/.config/gh/hosts.ym{k..m}",
    "cat < ~/.config/gh/{hosts,x}.yml",
    "echo $(cat ~/.docker/{config,x}.json)",
    "cat ~/.config/gh/{,a}{,b}{,c}{,d}{,e}{,f}{,g}{,h}{,i}{,j}hosts.yml",   # 1,024 words, the cap
    # a `}` before the group's first comma does not close it in bash
    "cat ~/.config/gh/{},hosts}.yml",
    "cat ~/.config/gh/{a},hosts}.yml",
    "cat ~/.docker/{x},config.json}",
    "cat ~/.kube/{},config}",
    "cat ~/{},.netrc}",
    # a quote before `{` means the `{` is not at the start of the word: bash expands the group
    "cat ''{},.env}",
    "cat ''{},$HOME/.config/gh/hosts.yml}",
    'cat ""{},$HOME/.kube/config}',
    "cat $''{},.env}",
    "cp ''{},$HOME/.config/gh/hosts.yml} /tmp/x",
    "cat --file=''{},.env}",
    "cat < ''{},.env}",
    # bash reads $"..." as "..." (no message catalog): `$""{.env,x}` is `.env x`
    'cat $""{.env,x}',
    'cat $""{},.env}',
    'cat $"".e{nv,x}',
    'cd ~ && cat $""{.netrc,x}',
    'cp $""{.env,x} /tmp/y',
    'cat $".e"nv',
    "cat ~/\\\n{},.netrc}",                      # a line continuation is gone before brace expansion
    # sequences expand to their members
    "cat ~/.{n..n}{e..e}{t..t}{r..r}{c..c}",
    "cat .{d..f}{m..o}{u..w}",
    "cat .e{m..o}{u..w}",
    "cat .env.{1..3}",
    "cat /srv/id_ed2551{0..9}",
    # padding: exact under the cap, a hit past it
    "cat ~/{.netrc," + ",".join(f"a{i}" for i in range(70)) + "}",
    "cat {.env," + ",".join(f"a{i}" for i in range(70)) + "}",
    "cat ~/{.netrc," + ",".join(f"a{i}" for i in range(1100)) + "}",
    "cat ~/" + "{y," * 20 + "{.netrc,x}" + "}" * 20,
    "cat ~/" + "{y," * 33 + "{.netrc,x}" + "}" * 33,
])
def test_brace_set_reaching_a_secret_store_is_credentials(cmd):
    c = _bash(cmd)
    assert c.category == "credentials", cmd
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("cmd", [
    "bash -c 'cat ~/.config/gh/{hosts,x}.yml'",                                      # opaque_exec on main
    "python3 -c \"import subprocess; subprocess.run('cat ~/.kube/{config,x}', shell=True)\"",  # opaque_exec on main
])
def test_brace_set_inside_inner_shell_text_is_credentials(cmd):
    assert _bash(cmd).category == "credentials"


def test_brace_set_in_execute_code_literal_is_credentials():
    c = gate.classify({"tool": "execute_code", "args": {"code": "import os; os.system('cat ~/.config/gh/{hosts,x}.yml')"}})
    assert c.category == "credentials"                                               # code on main


@pytest.mark.parametrize("tool,path", [
    ("read_file", "~/.config/gh/{hosts,x}.yml"),       # read on main
    ("write_file", "~/.docker/{config.json,x}"),        # local_write on main
])
def test_brace_set_in_tool_path_argument_is_credentials(tool, path):
    c = gate.classify({"tool": tool, "args": {"path": path, "content": "x"}})
    assert c.category == "credentials"
    assert gate.decide(c).outcome == "ask"


@pytest.mark.parametrize("cmd", [
    "cat ~/.ssh/{*,}",
    "cat ~/.exo/keys/{*,}",
    "cat ~/.config/gh/[h{]osts.ym[l}]",   # a brace inside a bracket expression: the original word still counts
])
def test_folder_stores_and_bracket_form_stay_credentials(cmd):
    c = _bash(cmd)
    assert c.category == "credentials"
    assert gate.decide(c).outcome == "ask"


_OBJS = ", ".join(['{"a":1,"b":2}'] * 11)


# (line, category, outcome) measured on main 35b7de8
@pytest.mark.parametrize("cmd,category,outcome", [
    ("cat config.{yaml,yml}", "read", "allow"),
    ("cp file{,.bak}", "shell", "allow"),
    ("echo {a,b}", "read", "allow"),
    ("mkdir -p src/{a,b}", "shell", "allow"),
    ("cat src/{a,b}.py", "read", "allow"),
    ("awk '{print $1}' x", "read", "allow"),
    ("jq '{a: .b}' f.json", "read", "allow"),
    ("cat ${HOME}/x", "read", "allow"),
    ("echo {a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}", "read", "allow"),
    ("python3 -c \"root='/srv'; print(f'{root}/config.json')\"", "opaque_exec", "ask"),
    # bash expands only unquoted braces: quoted or escaped ones stay literal, however many
    ("echo " + " ".join(["'{a,b}'"] * 11), "read", "allow"),
    ("echo " + " ".join(['"{a,b}"'] * 11), "read", "allow"),
    ("jq '[" + ", ".join(["{a:.x,b:.y}"] * 11) + "]' f.json", "read", "allow"),
    ("echo '[" + _OBJS + "]' > out.json", "local_write", "allow"),
    ("curl -d '[" + _OBJS + "]' https://example.com/api", "web_post", "ask"),
    ("cat '/srv/{.netrc,x}'", "read", "allow"),
    ('cat "/srv/{.netrc,x}"', "read", "allow"),
    ("cat \\{.netrc,x\\}", "read", "allow"),
    ("echo 'x{1..5000}'", "read", "allow"),
    # bash: `x.env` and `xy`, so not a store read; a non-empty $"..." may be translated (#136), so it asks
    ('cat $"x"{.env,y}', "read", "ask"),
])
def test_ordinary_brace_lines_keep_their_class(cmd, category, outcome):
    c = _bash(cmd)
    assert (c.category, gate.decide(c).outcome) == (category, outcome)


@pytest.mark.parametrize("code,category", [
    ("root = '/srv'\np = f'{root}/config.json'\nprint(p)", "code"),
    ("base = '/srv'\nacct = 'a'\np = f'{base}/.unii/{acct}/token'\nprint(p)", "credentials"),
])
def test_execute_code_f_strings_keep_their_class(code, category):
    assert gate.classify({"tool": "execute_code", "args": {"code": code}}).category == category


# Past the word cap or 32 levels of nesting the word counts as a store; a group that never closes stays literal.
@pytest.mark.parametrize("arg,category", [
    ("{a," * 3000 + "}" * 3000, "credentials"),
    ("{a,b}" * 3000, "credentials"),
    ("{" * 20000, "read"),
    ("{" + ",".join(["xyz"] * 5000) + "}", "credentials"),
    ("{a}" * 5000, "read"),
    ("{a}," * 5000 + "}", "credentials"),                 # one group of 5,001 alternatives
    ("{x," + "{}" * 5000 + "}", "read"),
], ids=["nested", "flat", "open", "wide", "commaless", "late-close", "empty-groups"])
def test_long_brace_input_classifies_in_bounded_time(arg, category):
    t = time.process_time()
    c = _bash("cat ~/notes/" + arg)
    assert time.process_time() - t < 20
    assert c.category == category


# One classify call expands at most 4,096 words in all (a glob word counts four times); past that budget a
# brace word counts as a store.
@pytest.mark.parametrize("tool,text,category", [
    ("bash", "echo " + " ".join(f"{{a..z}}{{a..z}}{i}" for i in range(6)), "read"),             # 4,056 words
    ("bash", "echo " + " ".join(f"{{a..z}}{{a..z}}{i}" for i in range(7)), "credentials"),
    ("bash", "echo *[a]{a..z}{a..z}0* *[a]{a..m}{a..z}1*", "read"),                           # 1,014 globs
    ("bash", "echo " + " ".join(f"{{a..z}}{{a..z}}{i}*" for i in range(100)), "credentials"),
    ("bash", "echo " + " ".join(f"{{a..z}}{{a..z}}{i}*" for i in range(400)), "credentials"),
    ("bash", "ls " + " ".join(f"~/x{i}/abc{{1..1000}}*" for i in range(100)), "credentials"),
    ("bash", "ls " + " ".join(f"~/x{i}/{{1..1000}}" for i in range(100)), "credentials"),
    ("bash", "cat " + " ".join(f"~/notes/{{a,b}}{{c,d}}{{e,f}}{{g,h}}{{i,j}}{{k,l}}/x{i}" for i in range(300)),
     "credentials"),
    ("execute_code", "\n".join(f"p{i} = 'abc{i}{{1..1000}}*'" for i in range(200)), "credentials"),
], ids=["6x676", "7x676", "1014-globs", "100x676", "400x676", "100x1000-glob", "100x1000", "300x64", "code-200x1000"])
def test_many_brace_words_in_one_call_classify_in_bounded_time(tool, text, category):
    t = time.process_time()
    c = gate.classify({"tool": tool, "args": {"command": text} if tool == "bash" else {"code": text}})
    assert time.process_time() - t < 5
    assert (c.category, gate.decide(c).outcome) == (category, "ask" if category == "credentials" else "allow")


def test_tool_path_arguments_share_the_budget():
    t = time.process_time()
    one = gate.classify({"tool": "read_file", "args": {"path": "/srv/abc{1..1000}*"}})       # expanded once
    many = gate.classify({"tool": "read_file", "args": {"paths": ["~/x{1..1000}"] * 1000}})
    assert time.process_time() - t < 5
    assert (one.category, many.category) == ("read", "credentials")


# Fail closed: a word past the cap (here an ordinary loop) asks as a store path; a sequence bound too long for
# int() does the same, and classify never raises.
@pytest.mark.parametrize("action", [
    {"tool": "bash", "args": {"command": "echo {1..2000}"}},
    {"tool": "bash", "args": {"command": "for i in {1..2000}; do echo $i; done"}},
    {"tool": "bash", "args": {"command": "cat {" + "9" * 5000 + "..1}"}},
    {"tool": "bash", "args": {"command": "echo {1..9999999999999999999999999}"}},
    {"tool": "read_file", "args": {"path": "~/notes/x{" + "9" * 5000 + "..1}"}},
], ids=["seq-2000", "for-2000", "bash-5000-digits", "bash-25-digits", "read_file-5000-digits"])
def test_over_cap_brace_words_ask_as_a_store(action):
    c = gate.classify(action)
    assert (c.category, gate.decide(c).outcome) == ("credentials", "ask")


@pytest.mark.parametrize("word,words", [
    ("x{a{b}c,d}y", {"xa{b}cy", "xdy"}),
    ("x{},a}", {"x}", "xa"}),
    ("{},a}", set()),                               # a lone {} at the start of a word is not a group
    ("x{a..{b,c}}", {"xa..b", "xa..c"}),
    ("x{05..1}", {"x05", "x04", "x03", "x02", "x01"}),
    ("x{1..10..3}", {"x1", "x4", "x7", "x10"}),
    # a leading zero on either bound pads every member to the longer bound
    ("x{007..1000..993}", {"x0007", "x1000"}),
    ("x{-01..1}", {"x-01", "x000", "x001"}),
    ("x{08..011}", {"x008", "x009", "x010", "x011"}),
    ("x{1..010..3}", {"x001", "x004", "x007", "x010"}),
    ("x{+01..3}", {"x1", "x2", "x3"}),
    ("x{02147483647..02147483647}", {"x02147483647"}),
    ("x{-02147483648..-02147483648}", {"x-02147483648"}),
    ("x{2147483648..2147483648}", {"x2147483648"}),
    ("x{a..e..2}", {"xa", "xc", "xe"}),
    ("x{a..1}", set()),
    ("'{a,b}'", set()),
    ("\\{a,b}", set()),
    ("${x:-{a,b}}", set()),
    ("$(echo {a,b})", set()),
    ("''{},x}", {"''}", "''x"}),                   # bash: `}` and `x`
    ("$''{},x}", {"$''}", "$''x"}),
    ("a'{'b,c}", set()),
])
def test_brace_words_follow_bash(word, words):
    assert set(gate._brace_words(word)) == words | {word}


# bash prints a zero-padded member as a C int, so past int32 it wraps; the gate does not follow it there and
# counts the word as a store path instead.
@pytest.mark.parametrize("word", [
    "x{04294967296..04294967296}", "x{02147483648..02147483648}", "x{-02147483649..-02147483649}",
    "x{1..02147483648..2147483647}",
])
def test_zero_padded_sequence_past_int32_is_over_the_limit(word):
    assert gate._brace_words(word) is None


@pytest.mark.parametrize("home,cmd", [
    ("/srv/exo0007", "ls /srv/exo{007..1000..993}/keys"),             # bash: /srv/exo0007/keys, /srv/exo1000/keys
    ("/srv/exo00000000000", "ls /srv/exo{04294967296..04294967296}/keys"),   # bash: /srv/exo00000000000/keys
    ("/srv/exo-2147483648", "ls /srv/exo{02147483648..02147483648}/keys"),   # bash: /srv/exo-2147483648/keys
])
def test_zero_padded_sequence_reaching_the_store_is_credentials(monkeypatch, home, cmd):
    monkeypatch.setenv("EXO_HOME", home)
    c = _bash(cmd)
    assert (c.category, gate.decide(c).outcome) == ("credentials", "ask")


def test_shell_words_keep_their_source_text():
    cmds, _, _ = gate._parse("cat ''{},.env} \"$HOME\"/{a,b} < x'{'y,z}")
    assert [w.raw for w in cmds[0].words] == ["cat", "''{},.env}", '"$HOME"/{a,b}']
    assert [t.raw for _, t in cmds[0].redirs] == ["x'{'y,z}"]
    assert gate._dequote("''.env") == [".env"]
    assert ".env" in gate._dequote('$"".env')
