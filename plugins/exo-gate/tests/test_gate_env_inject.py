"""D-038, on top of #154's command-valued variables: environment assignments that load code or move where
programs and config are found (LD_PRELOAD, PATH, PYTHONPATH, HOME...), git config set through the
environment (GIT_CONFIG_KEY_<n>/VALUE_<n>, GIT_CONFIG_PARAMETERS), `git clone --config`/`-c`/`--template`,
NAME=VALUE arguments that env and sudo read from their own argv even when the shell quoted them, the same
variables set through read, printf -v, a for loop, a nameref or an exporter argument with a dynamic name,
git config that loads a file or a template (include.path, includeIf.*.path, init.templateDir),
`git --config-env=`, CDPATH, and the option variables MANOPT, ZIPOPT, LESS (with a `+` command) and
RIPGREP_CONFIG_PATH.

Every string here is data for the classifier. Nothing is run; the paths are placeholders.
"""
from pathlib import Path

import pytest

from gate_fixtures import TEMPLATE
from exo import gate
from exo.gate import classify, decide, load_policies, normalize_policies

DENY = normalize_policies({"gates": {"deny": ["delete", "credentials", "vcs_push", "force_push"]}})
RM = "rm -rf /srv/data"


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


def asks_naming(cmd, name):
    c = classify(sh(cmd))
    assert c.category == "opaque_exec" and "indirection" in c.signals, c
    assert c.summary.startswith(name + " "), c
    assert decide(c).outcome == "ask", c
    assert decide(c, load_policies(TEMPLATE)).outcome != "allow", c
    return c


# --- variables that load code or decide where programs, modules and config are found -----------

INJECT = ["LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "PATH",
          "GIT_EXEC_PATH", "GIT_TEMPLATE_DIR", "PYTHONSTARTUP", "PYTHONPATH", "PERL5OPT", "PERL5LIB", "RUBYOPT",
          "NODE_OPTIONS", "BASH_ENV", "ENV", "HOME"]
INJECT_AUDIT = ["GCONV_PATH", "DYLD_FRAMEWORK_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "DYLD_FALLBACK_FRAMEWORK_PATH",
                "OPENSSL_CONF", "OPENSSL_ENGINES", "OPENSSL_MODULES", "XDG_CONFIG_HOME", "ZDOTDIR",
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "PYTHONHOME", "PYTHONUSERBASE", "PERLLIB", "RUBYLIB",
                "NODE_PATH", "PSQLRC"]
FORMS = [
    "{v}=/tmp/x ls",
    "{v}=/tmp/x git log",
    "{v}=/tmp/x python -m json.tool f",          # before pytest PYTHONPATH adds nothing (see below)
    "FOO=1 {v}=/tmp/x ls",
    "{v}+=:/tmp/x ls",
    "env {v}=/tmp/x ls",
    "env -i {v}=/tmp/x git log",
    "sudo {v}=/tmp/x ls",
    "sudo -u root {v}=/tmp/x ls",
    "nice -n 5 {v}=/tmp/x ls",
    "timeout 5 {v}=/tmp/x ls",
    "export {v}=/tmp/x; ls",
    "export {v}=/tmp/x && git log",
    "export {v}=/tmp/x",
    "declare -x {v}=/tmp/x; ls",
    "{v}=/tmp/x; ls",
    "if true; then {v}=/tmp/x ls; fi",
]


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("var", INJECT)
def test_inject_variable_asks_and_names_it(var, form):
    asks_naming(form.format(v=var), var)


@pytest.mark.parametrize("var", INJECT_AUDIT)
def test_the_rest_of_the_audit_asks_too(var):
    asks_naming(f"{var}=/tmp/x git log", var)
    asks_naming(f"env {var}=/tmp/x ls", var)


def test_an_inject_variable_keeps_a_stronger_category_and_adds_the_indirection():
    c = classify(sh("LD_PRELOAD=/tmp/x.so git push origin main"))
    assert c.category == "vcs_push" and "indirection" in c.signals, c
    assert decide(c).outcome == "ask"
    assert classify(sh("PATH=/tmp/bin rm -rf /srv/data")).category == "delete"


# --- command-valued variables from the same audit (#154's rule: the value is a nested command) ----

@pytest.mark.parametrize("var", ["GIT_PROXY_COMMAND", "BROWSER", "GH_PAGER", "GH_EDITOR", "GH_BROWSER",
                                 "PSQL_PAGER", "PSQL_EDITOR", "SUDO_ASKPASS", "SUDO_EDITOR", "RSYNC_CONNECT_PROG"])
def test_more_command_variables_are_read_as_commands(var):
    c = classify(sh(f"{var}='{RM}' git fetch origin"))
    assert c.category == "delete", c
    assert decide(c).outcome == "ask" and decide(c, DENY).outcome == "deny", c


# --- git config through the environment ----------------------------------------------------------

def test_git_config_env_with_a_command_value_is_read_as_that_command():
    c = classify(sh(f"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.sshCommand GIT_CONFIG_VALUE_0='{RM}' git fetch o"))
    assert c.category == "delete" and "indirection" in c.signals, c
    assert decide(c).outcome == "ask" and decide(c, DENY).outcome == "deny", c


@pytest.mark.parametrize("cmd", [
    f"GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x GIT_CONFIG_KEY_1=alias.l "
    f"GIT_CONFIG_VALUE_1='!{RM}' git log",
    f"GIT_CONFIG_PARAMETERS=\"'core.sshCommand'='{RM}'\" git fetch o",
    f"env GIT_CONFIG_PARAMETERS=\"'core.pager'='{RM}' 'user.name'='x'\" git log",
    f"export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0='{RM}'; git log",
])
def test_git_config_env_value_is_classified_so_a_deny_fires(cmd):
    c = classify(sh(cmd))
    assert c.category == "delete", c
    assert decide(c, DENY).outcome == "deny", c


@pytest.mark.parametrize("cmd,name", [
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.pager GIT_CONFIG_VALUE_0=x git log", "GIT_CONFIG_KEY_0"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=./h git commit -m m", "GIT_CONFIG_KEY_0"),
    ("GIT_CONFIG_KEY_0=core.pager git log", "GIT_CONFIG_KEY_0"),       # the value may come from the environment
    ("GIT_CONFIG_VALUE_0=x git log", "GIT_CONFIG_VALUE_0"),            # the key may come from the environment
    ("GIT_CONFIG_KEY_0=$K GIT_CONFIG_VALUE_0=x git log", "GIT_CONFIG_KEY_0"),
    ("env GIT_CONFIG_PARAMETERS=\"'core.pager'='x'\" git log", "GIT_CONFIG_PARAMETERS"),
    ("env GIT_CONFIG_PARAMETERS=\"'core.pager'='x\" git log", "GIT_CONFIG_PARAMETERS"),   # unbalanced quote
    ("env GIT_CONFIG_PARAMETERS=\"$P\" git log", "GIT_CONFIG_PARAMETERS"),
    ("sudo GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=diff.external GIT_CONFIG_VALUE_0=x git diff", "GIT_CONFIG_KEY_0"),
])
def test_git_config_env_exec_key_asks(cmd, name):
    asks_naming(cmd, name)


@pytest.mark.parametrize("cmd,category", [
    ("env GIT_CONFIG_PARAMETERS=\"'user.name'='x'\" git log", "read"),
    ("env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name 'GIT_CONFIG_VALUE_0=A B' git log", "read"),
])
def test_git_config_env_with_a_harmless_key_is_plain_git(cmd, category):
    c = classify(sh(cmd))
    assert c.category == category and decide(c).outcome == "allow", c


def test_one_exec_key_check_for_git_c_clone_and_the_environment():
    assert gate._git_config_kind("core.sshCommand", "x") == "exec"
    assert gate._git_config_kind("alias.l", "!sh -c x") == "exec"
    assert gate._git_config_kind("CORE.PAGER", "") == "exec"
    assert gate._git_config_kind("include.path", "/tmp/c") == "load"
    assert gate._git_config_kind("includeIf.gitdir:~/w/.path", "/tmp/c") == "load"
    assert gate._git_config_kind("init.templateDir", "/tmp/t") == "load"
    assert gate._git_config_kind("user.name", "x") == ""
    assert gate._git_config_kind("core.autocrlf", "false") == ""


# --- git clone's own config options, and git -c read the same way --------------------------------

@pytest.mark.parametrize("cmd", [
    "git clone --config core.sshCommand=x https://h/r",
    "git clone --config=core.sshCommand=x https://h/r",
    "git clone --conf core.sshCommand=x https://h/r",
    "git clone -c core.pager=x https://h/r",
    "git clone -ccore.pager=x https://h/r",
    "git clone -q -c core.hooksPath=./h https://h/r d",
    "git clone -c 'alias.x=!x' https://h/r",
    "git clone --template=./t https://h/r",
    "git clone --template ./t https://h/r",
    "git -C /tmp clone --bare --template=./t /tmp/r",
    "git -c \"$K=x\" log",
])
def test_clone_config_and_template_ask(cmd):
    c = classify(sh(cmd))
    assert c.category == "opaque_exec" and "indirection" in c.signals, c
    assert decide(c).outcome == "ask", c
    assert decide(c, load_policies(TEMPLATE)).outcome != "allow", c


@pytest.mark.parametrize("cmd", [
    f"git clone --config core.sshCommand='{RM}' https://h/r",
    f"git clone -c 'alias.x=!{RM}' https://h/r",
    f"git -c core.sshCommand='{RM}' fetch origin",
    f"git -c alias.l='!{RM}' l",
])
def test_clone_and_git_c_command_value_is_classified(cmd):
    c = classify(sh(cmd))
    assert c.category == "delete", c
    assert decide(c).outcome == "ask" and decide(c, DENY).outcome == "deny", c


# --- env and sudo read NAME=VALUE from their own argv ---------------------------------------------

def test_quoted_assignment_after_env_or_sudo_is_an_assignment():
    asks_naming("env 'LD_PRELOAD=/t/x.so' ls", "LD_PRELOAD")
    asks_naming("sudo 'PATH=/tmp/bin' ls", "PATH")
    asks_naming("env -i \"HOME=/tmp/h\" git log", "HOME")
    c = classify(sh("env 'FOO=bar baz' ls"))
    assert c.category == "read" and decide(c).outcome == "allow", c
    c = classify(sh("sudo 'FOO=bar' git push --force origin main"))
    assert c.category == "force_push" and decide(c, DENY).outcome == "deny", c


def asks(cmd, mention):
    c = classify(sh(cmd))
    assert c.category == "opaque_exec" and "indirection" in c.signals, c
    assert mention in c.summary, c
    assert decide(c).outcome == "ask", c
    assert decide(c, DENY).outcome != "allow", c
    assert decide(c, load_policies(TEMPLATE)).outcome != "allow", c
    return c


# --- a listed variable set by indirect or dynamic assignment --------------------------------------

@pytest.mark.parametrize("cmd,mention", [
    ("X=LD_PRELOAD; export $X=/tmp/x; ls", "export sets a variable whose name"),
    ("X=LD_PRELOAD=/tmp/x; export $X; ls", "export sets a variable whose name"),
    ('export "$N=/tmp/x"; ls', "export sets a variable whose name"),
    ("declare -x ${N}=/tmp/x; ls", "declare sets a variable whose name"),
    ("local `echo PATH`=/tmp/b", "local sets a variable whose name"),
    ("read -r PATH <<< /tmp/b; ls", "PATH "),
    ("IFS= read -r HOME < f; git log", "HOME "),
    ("while read -r LD_PRELOAD; do ls; done < f", "LD_PRELOAD "),
    ("read -a PATH < f; ls", "PATH "),
    ("read -ra HOME < f; git log", "HOME "),
    ("read -p prompt -r HOME; git log", "HOME "),
    ("read -r GIT_SSH_COMMAND < f; git fetch", "GIT_SSH_COMMAND "),
    ('read -r "$v" < f; ls', "read sets a variable whose name"),
    ("printf -v PATH %s /tmp/b; ls", "PATH "),
    ("printf -vPATH %s /tmp/b; ls", "PATH "),
    ('printf -v "$n" %s /tmp/b; ls', "printf sets a variable whose name"),
    ("for HOME in /tmp/h; do git log; done", "HOME "),
    ("for PATH in /tmp/b; do ls; done", "PATH "),
    ("for GIT_CONFIG_PARAMETERS in x; do git log; done", "GIT_CONFIG_PARAMETERS "),
    ("declare -n r=PATH; r=/tmp/b; ls", "PATH "),
    ("local -n r=HOME; r=/tmp/h; git log", "HOME "),
    ("typeset -n r=$X; r=/tmp/b; ls", "typeset sets a variable whose name"),
    ("declare -n r; r=PATH; r=/tmp/b; ls", "declare -n r makes a reference"),
])
def test_indirect_or_dynamic_assignment_of_a_listed_variable_asks(cmd, mention):
    asks(cmd, mention)


# --- git config that loads another config file or a template --------------------------------------

@pytest.mark.parametrize("cmd,mention", [
    ("git -c include.path=/tmp/c log", "include.path"),
    ("git clone -c include.path=/tmp/c https://h/r", "include.path"),
    ("git clone --config include.path=/tmp/c https://h/r", "include.path"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=include.path GIT_CONFIG_VALUE_0=/tmp/c git log", "include.path"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=includeIf.gitdir:/.path GIT_CONFIG_VALUE_0=/tmp/c git log",
     "includeIf.gitdir:/.path"),
    ("env GIT_CONFIG_PARAMETERS=\"'include.path'='/tmp/c'\" git log", "GIT_CONFIG_PARAMETERS sets include.path"),
    ("git -c init.templateDir=/tmp/t clone https://h/r", "init.templateDir"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=init.templateDir GIT_CONFIG_VALUE_0=/tmp/t git clone https://h/r",
     "init.templateDir"),
    ("git config include.path /tmp/c", "include.path"),
])
def test_git_config_that_loads_a_file_or_template_asks(cmd, mention):
    asks(cmd, mention)


# --- git --config-env, attached or not: the value comes from a variable the gate does not see -----

@pytest.mark.parametrize("cmd,mention", [
    ("git --config-env=core.pager=V log", "core.pager"),
    ("V=less git --config-env=core.pager=V log", "core.pager"),
    ("git --config-env=alias.l=V l", "alias.l"),
    ("git --config-env=include.path=V log", "include.path"),
])
def test_git_config_env_option_takes_the_exec_key_check(cmd, mention):
    asks(cmd, mention)
    asks(cmd.replace("--config-env=", "--config-env "), mention)   # the separate form, which #164 read


# --- CDPATH moves a relative cd into the store where the gate cannot see it ------------------------

def test_cdpath_into_the_store_is_not_allowed(tmp_path, monkeypatch):
    store = tmp_path / "store-home"            # no `.exo` component, so only the path itself marks it
    (store / "memory").mkdir(parents=True)
    monkeypatch.setenv("EXO_HOME", str(store))
    c = classify(sh(f"cd {store}/memory && touch notes.md"))
    assert c.category == "store_write", c      # the direct form, for contrast
    asks(f"CDPATH={store} cd memory && touch notes.md", "CDPATH ")
    asks(f"export CDPATH={tmp_path}; cd store-home && touch notes.md", "CDPATH ")
    asks("read -r CDPATH < f; cd memory && touch notes.md", "CDPATH ")


# --- option variables #164 already reads on the command line ---------------------------------------

@pytest.mark.parametrize("cmd,name", [
    ("MANOPT=-Px man ls", "MANOPT"),
    ("MANOPT=--pager=x man ls", "MANOPT"),
    ("LESS=+!x less f", "LESS"),
    ("LESS=-R+!x less f", "LESS"),
    ("export LESS='-R +!x'; git log", "LESS"),
    ("RIPGREP_CONFIG_PATH=/tmp/c rg foo", "RIPGREP_CONFIG_PATH"),
    ("ZIPOPT=-TTx zip a.zip f", "ZIPOPT"),
])
def test_option_variables_ask(cmd, name):
    asks_naming(cmd, name)


# --- round 2: a listed variable set through parameter-expansion assignment (${NAME:=v} / ${NAME=v}) ---

@pytest.mark.parametrize("cmd,name", [
    (": ${PATH:=.}; ls", "PATH"),
    (": ${PATH=/x}; ls", "PATH"),                        # the no-colon assign form
    ("echo ${HOME:=/tmp/h}", "HOME"),
    (": ${LD_PRELOAD:=/t/x.so}; ls", "LD_PRELOAD"),
    (": ${BASH_ENV:=/t/e}; ls", "BASH_ENV"),
    (": ${PYTHONPATH:=/t}; python -c 1", "PYTHONPATH"),
    ('echo "${CDPATH:=$EXO_HOME}"', "CDPATH"),           # also inside double quotes
    ('echo "${PATH:=x}"', "PATH"),
    (": ${CDPATH:=dir}; cd memory", "CDPATH"),
    ("ls <<< ${PATH:=/x}", "PATH"),                      # also expanded in a here-string
    ('cat <<< "${HOME:=/tmp/h}"; git log', "HOME"),
])
def test_parameter_expansion_assignment_of_a_listed_variable_asks(cmd, name):
    asks_naming(cmd, name)


@pytest.mark.parametrize("cmd", [
    f": ${{GIT_PAGER:='{RM}'}}; git log",                # the value a command-valued variable gets is read
    f": ${{GIT_SSH_COMMAND:='{RM}'}}; git fetch origin",
    ": ${GIT_SSH_COMMAND:=rm' -rf /srv/data'}; git fetch origin",   # partly quoted: bash removes the quotes
    ': ${GIT_PAGER:="rm -rf"\' /srv/data\'}; git log',
])
def test_a_command_value_in_a_parameter_expansion_is_classified(cmd):
    c = classify(sh(cmd))
    assert c.category == "delete", c
    assert decide(c, DENY).outcome == "deny", c


# --- round 2: a glob in an assigned name is a name the gate cannot resolve -------------------------

@pytest.mark.parametrize("cmd,mention", [
    ("export PAT?=.; ls", "export sets a variable whose name"),      # a file PATH=. makes PAT?=. expand to it
    ("export LD_PRELOA?=x.so; ls", "export sets a variable whose name"),
    ("export LD_PRELOAD*; ls", "export sets a variable whose name"),
    ("read PAT? < f; ls", "read sets a variable whose name"),
    ("read HOM[E] < f; git log", "read sets a variable whose name"),
    ("printf -v PAT? %s /tmp/b; ls", "printf sets a variable whose name"),
    ("printf -v HOM[E] %s /tmp/h; git log", "printf sets a variable whose name"),
    ("declare -x HOM?=/tmp/h; git log", "declare sets a variable whose name"),
])
def test_a_glob_in_an_assigned_name_asks(cmd, mention):
    asks(cmd, mention)


# --- round 2: -p exempts only declare/typeset/local; export -p and readonly -p still assign --------

@pytest.mark.parametrize("cmd,mention", [
    ("X=PATH; export -p $X=.; ls", "export sets a variable whose name"),
    ("X=CDPATH; export -p $X=$EXO_HOME; cd memory && touch notes.md", "export sets a variable whose name"),
    ("readonly -p $X=ro; ls", "readonly sets a variable whose name"),
    ("export -pn $X=pn; ls", "export sets a variable whose name"),
])
def test_p_does_not_exempt_export_or_readonly(cmd, mention):
    asks(cmd, mention)


def test_export_minus_p_with_a_literal_name_still_asks():
    asks_naming("export -p PATH=/tmp/b", "PATH")


# --- round 2: arithmetic, array-subscript and {NAME}> redirection assignments (integer-valued) -----

@pytest.mark.parametrize("cmd,name", [
    (": $((PATH=10)); ls", "PATH"),
    ("echo $((PATH=10)); ls", "PATH"),
    (": $((HOME=1)); git log", "HOME"),
    ("a[PATH=10]=1; ls", "PATH"),                        # an indexed-array subscript is arithmetic
    ("echo ${a[PATH=10]}; ls", "PATH"),
    ("true {PATH}>/dev/null; ls", "PATH"),               # the fd number lands in PATH
    (": {CDPATH}>/dev/null; cd memory", "CDPATH"),
])
def test_arithmetic_and_fd_redirection_assignments_ask(cmd, name):
    asks_naming(cmd, name)


# --- round 3: a subscript is arithmetic wherever bash reads the word as a variable name -------------

@pytest.mark.parametrize("cmd,name", [
    ("declare 'a[PATH=10]=1'; ls", "PATH"),              # the builtin parses its quoted argument itself
    ("local a[HOME=1]=x; git log", "HOME"),
    ("unset 'a[PATH=10]'; ls", "PATH"),
    ("read -r 'a[PATH=10]' < f; ls", "PATH"),
    ("printf -v 'a[HOME=1]' %s x; git log", "HOME"),
    ("declare -n r='a[PATH=10]'; r=1; ls", "PATH"),
    ("test -v 'a[PATH=10]' && ls", "PATH"),
    ("echo ${#a[PATH=10]}; ls", "PATH"),
    ("echo ${!a[HOME=1]}; git log", "HOME"),
    ('echo "${a[PATH=10]}"; ls', "PATH"),               # double quotes still expand
    ('echo "$((PATH=10))"; ls', "PATH"),
    ("x=${X:-$((PATH=10))}; ls", "PATH"),                # arithmetic inside another expansion
    ("echo ${a[PATH=${i}]}; ls", "PATH"),                # a nested ${..} does not end the subscript
    ('echo "${a[PATH=${i}]}"; ls', "PATH"),
    ("echo ${#a[PATH=${i}]}; ls", "PATH"),
    ("echo ${!a[HOME=${i}]}; git log", "HOME"),
    ("x=${a[PATH=${i}]}; ls", "PATH"),
    ("echo ${a[${b[0]},PATH=1]}; ls", "PATH"),           # nor does a nested subscript
    ("echo ${PATH[${i}]:=.}; ls", "PATH"),               # PATH[0] is PATH
    ("echo ${PATH[${b[0]}]:=.}; ls", "PATH"),            # a subscript ends at the ] that matches its [
])
def test_a_subscript_where_bash_reads_a_name_asks(cmd, name):
    asks_naming(cmd, name)


# --- round 4: an expansion runs to the } bash closes it with, and what bash expands inside it counts --------

@pytest.mark.parametrize("cmd,name", [
    ('echo "${a:-\'${PATH:=5}\'}"', "PATH"),            # in double quotes, :- still expands single-quoted text
    ('echo "${a:-${b:-\'${PATH:=5}\'}}"', "PATH"),
    ('echo "${a:-${b}\'$((PATH=1))\'}"', "PATH"),
    ('echo "${a:-${b}\'}\'${PATH:=7}}"', "PATH"),           # a quoted } does not close it
    ("echo ${a:-\"'${PATH:=5}'\"}", "PATH"),            # a single quote inside double quotes is a character
    ('echo "${a:-${b}" ${PATH:=5} "}"', "PATH"),          # the inner double-quoted string is part of it
    ("echo \"${a:-$'${PATH:=.}'}\"", "PATH"),            # and so is $'..' where '..' would be
    ("echo \"${a-$'${PATH:=.}'}\"", "PATH"),
    ("echo \"${a:-$'$((HOME=1))'}\"", "HOME"),
])
def test_what_bash_expands_inside_an_expansion_asks(cmd, name):
    asks_naming(cmd, name)


# --- everything else stays as it was on #164 -----------------------------------------------------

UNCHANGED = [
    # parameter expansion that only substitutes, reads or edits a listed variable never assigns it
    ("echo ${PATH:-/usr/bin}", "read", "allow"),
    ("echo ${PATH-/usr/bin}", "read", "allow"),
    ("echo ${PATH:?unset}", "read", "allow"),
    ("echo ${PATH:+set}", "read", "allow"),
    ("echo ${#PATH}", "read", "allow"),
    ("echo ${PATH#/x}", "read", "allow"),
    ("echo ${PATH/foo/bar}", "read", "allow"),
    ("echo ${PATH}:=x", "read", "allow"),               # $PATH then the literal text :=x, no assignment
    ('test -n "${PATH}" && ls', "read", "allow"),
    (": ${PORT:=8080}; echo $PORT", "read", "allow"),   # a default for a variable the gate does not list
    ("echo ${EDITOR:=vim}", "read", "allow"),           # a command-valued default to a known editor
    (": ${PAGER:=less}; git log", "read", "allow"),
    (": ${GIT_PAGER:=cat}; git log", "read", "allow"),
    # integer assignment to a variable nobody lists, and declare/typeset -p which only print
    ("x=$((n=5)); echo $x", "read", "allow"),
    ("echo $((1 + 2))", "read", "allow"),
    ("echo ${arr[i]}", "read", "allow"),
    ('echo "${arr[${#arr[@]}-1]}"', "read", "allow"),
    ("echo ${files[@]:${i}:2}", "read", "allow"),
    ('echo "${JSON:-{}}"', "read", "allow"),
    ("echo ${x:-\\{}'${PATH:=.}'", "read", "allow"),     # an escaped { does not count, the rest is quoted
    # a nested ${..} does not make the scan read on into quoted or escaped text: bash counts neither
    ("echo ${a:-${b}\\{}'${PATH:=.}'", "read", "allow"),
    ("echo ${a:-${b}'{'}'${PATH:=.}'", "read", "allow"),
    ('echo "${a:-${b}\'{\'}"\'${PATH:=.}\'', "read", "allow"),
    ('echo ${a:-${b}"{"}\'${PATH:=.}\'', "read", "allow"),
    ("echo ${a:-${b}\\{}'$((PATH=1))'", "read", "allow"),
    ("echo ${a:-${b}\\{}${c}'${PATH:=.}'", "read", "allow"),
    ("echo ${a:-${b}'${PATH:=.}'}", "read", "allow"),
    ('echo "${a:-${b} \\${PATH:=.}}"', "read", "allow"),
    ("echo ${a:-'${PATH:=.}'}", "read", "allow"),          # single-quoted inside an unquoted expansion
    ("echo ${a:-'$((PATH=1))'}", "read", "allow"),
    ("echo \"${f%'${PATH:=.}'}\"", "read", "allow"),       # a pattern quotes it even in double quotes
    ("echo \"${f/x/'${PATH:=.}'}\"", "read", "allow"),
    ("echo \"${f%$'${PATH:=.}'}\"", "read", "allow"),      # $'..' after a pattern: quoted, as '..' is
    ("echo ${a:-$'${PATH:=.}'}", "read", "allow"),          # $'..' inside an unquoted expansion
    ('echo "${a:-\'\\${PATH:=5}\'}"', "read", "allow"),
    ("echo ${a:-`echo '${PATH:=.}'`}", "read", "allow"),
    ("true {fd}>/dev/null", "read", "allow"),
    ("typeset -p PATH", "read", "allow"),
    ("declare -p $X", "read", "allow"),
    ("PAGER=cat git log", "read", "allow"),
    ("GIT_PAGER= git log", "read", "allow"),
    ("GIT_EDITOR=true git rebase --continue", "local_write", "allow"),
    ("GIT_SEQUENCE_EDITOR=: git rebase -i main", "local_write", "allow"),
    ('while IFS= read -r line; do echo "$line"; done < f', "read", "allow"),
    ('IFS=, read -r a b <<< "x,y"', "read", "allow"),
    ("FOO=bar ls", "read", "allow"),
    ("NO_COLOR=1 git log", "read", "allow"),
    ("LANG=C sort f", "shell", "allow"),
    ("LC_ALL=C sort f", "shell", "allow"),
    ("TZ=UTC date", "read", "allow"),
    ("COLUMNS=200 ls", "read", "allow"),
    ("GIT_AUTHOR_NAME=x git commit -m m", "local_write", "allow"),
    ("PYTHONUNBUFFERED=1 python -c 'print(1)'", "opaque_exec", "ask"),
    ("PYTHONDONTWRITEBYTECODE=1 pytest -q", "shell", "allow"),
    ("CI=1 pytest -q", "shell", "allow"),
    ("DEBUG=1 ls", "read", "allow"),
    ("CDPATH= cd src", "read", "allow"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x git log", "read", "allow"),
    ("GIT_CONFIG_COUNT=1 git log", "read", "allow"),
    ("git clone https://h/r", "local_write", "allow"),
    ("git clone --depth 1 https://h/r", "local_write", "allow"),
    ("git clone -c user.name=x https://h/r", "local_write", "allow"),
    ("git clone --config core.autocrlf=false https://h/r", "local_write", "allow"),
    ("git clone --template= https://h/r", "local_write", "allow"),
    ("git -c user.name=x commit -m m", "local_write", "allow"),
    ("git -c color.ui=always log", "read", "allow"),
    ("env FOO=1 ls", "read", "allow"),
    ("env -u PATH ls", "read", "allow"),
    ("export FOO=bar", "read", "allow"),
    ("export PATH", "read", "allow"),
    ("export EDITOR=vim", "read", "allow"),
    ("GH_PAGER=cat gh pr view 3", "read", "allow"),
    ("PSQL_PAGER=cat psql -c 'select 1'", "shell", "allow"),
    ("BROWSER=false gh pr view 3", "read", "allow"),
    ("export GH_EDITOR=vim", "read", "allow"),
    ("EDITOR=vim", "read", "allow"),
    ("PAGER=less; git log", "read", "allow"),
    ("x=1; echo $x", "read", "allow"),
    ("echo PATH=/tmp/x", "read", "allow"),
    ("grep -rn 'LD_PRELOAD=' docs/", "read", "allow"),
    # read, printf -v, for and namerefs into variables nobody lists, and harmless option variables
    ("read -r name < f", "read", "allow"),
    ("read -r -d '' x < f", "read", "allow"),
    ('read -t 5 -p "ok? " reply', "read", "allow"),
    ('while read -r f; do wc -l "$f"; done < list', "read", "allow"),
    ('for f in *.py; do echo "$f"; done', "read", "allow"),
    ("printf -v padded '%05d' 3", "read", "allow"),
    ("printf '%s\\n' \"$PATH\"", "read", "allow"),
    ("declare -n r=arr", "read", "allow"),
    ("declare -p PATH", "read", "allow"),
    ('declare -p "$v"', "read", "allow"),
    ("export -f fn", "read", "allow"),
    ('export FOO="$BAR"', "read", "allow"),
    ("local -r x=1", "read", "allow"),
    ('CDPATH= cd -P -- "$(dirname -- "$0")" && pwd -P', "read", "allow"),
    ("LESS=-R less f", "read", "allow"),
    ("LESS=-FRX git log", "read", "allow"),
    ("LESS=FRX git log", "read", "allow"),
    ("git --config-env=user.email=EMAIL commit -m m", "local_write", "allow"),
    ("git config --get include.path", "local_write", "allow"),
    ("git config user.name x", "local_write", "allow"),
    # a quoted NAME=VALUE is still the program outside env and sudo, as the shell runs it
    ("FOO='bar baz' ls", "opaque_exec", "ask"),
    ("nice 'FOO=bar' ls", "opaque_exec", "ask"),
    ("timeout 5 'FOO=bar' ls", "opaque_exec", "ask"),
    # bash expands nothing in single quotes or a quoted here-doc, and a bracket outside a subscript is a glob
    ("git commit -m 'Gate: read ${PATH:=.} assignments'", "local_write", "allow"),
    ("git commit -m 'Gate: read $((PATH=10)) assignments'", "local_write", "allow"),
    ("git commit -m 'docs: a[PATH=10]=1 is arithmetic'", "local_write", "allow"),
    ('git commit -m "docs: a[PATH=10]=1 is arithmetic"', "local_write", "allow"),
    ("grep -F '${PATH:=' src/exo/gate.py", "read", "allow"),
    ("rg -F '${HOME:=' .", "read", "allow"),
    ("printf '%s\\n' '[HOME=x]'", "read", "allow"),
    ("echo [HOME=x]", "read", "allow"),
    ('echo "[HOME=x]"', "read", "allow"),
    ("ls -d [HOME=x]*", "read", "allow"),
    ("pytest -q 'tests/test_x.py::test_env[HOME=/tmp]'", "shell", "allow"),
    ("pytest -q 'tests/t.py::test_x[a[PATH=10]=1; ls-PATH]'", "shell", "allow"),
    ("echo '${CDPATH:=/tmp}'", "read", "allow"),
    ("echo \\${PATH:=x}", "read", "allow"),
    ("echo 'a[PATH=10]=1'", "read", "allow"),
    ("cat <<< '${PATH:=x}'", "read", "allow"),
    ("cat <<< '$((PATH=10))'", "read", "allow"),
    ("git log --grep='${GIT_PAGER:=rm -rf /}'", "read", "allow"),
    ("echo '{HOME}' > f", "local_write", "allow"),
    ("cat <<'EOF'\n${PATH:=x} $((HOME=1)) a[PATH=1]=2\nEOF", "read", "allow"),
    ("git commit -m \"$(cat <<'EOF'\nGate: read ${PATH:=.} and $((PATH=10))\nEOF\n)\"", "local_write", "allow"),
    ("printf -v out '%s' 'a[PATH=1]'", "read", "allow"),
    ("read -p 'a[HOME=1]? ' reply", "read", "allow"),
    ("test -v PATH && ls", "read", "allow"),
]


@pytest.mark.parametrize("cmd,category,outcome", UNCHANGED)
def test_everything_else_is_unchanged(cmd, category, outcome):
    c = classify(sh(cmd))
    assert (c.category, decide(c).outcome) == (category, outcome), c


# On #164 a parametrized node id, quoted for pytest, was shell, or credentials or store_write when the id
# names a secret file or the Exo store. None of them is an assignment.
ON_164 = {("shell", "allow"), ("credentials", "ask"), ("store_write", "ask")}


def test_a_quoted_pytest_node_id_is_never_read_as_an_assignment(request):
    """Every parametrized node id of this suite collected alongside this test (all of them when the suite
    runs as one), passed as `pytest -q '<node id>'`, classifies as it did on #164."""
    here = Path(__file__).resolve().parent
    ids = [i.nodeid for i in request.session.items if "[" in i.nodeid and Path(i.path).resolve().parent == here]
    if not ids:
        pytest.skip("no parametrized node ids collected with this test; run it with the suite")
    for nid in ids:
        c = classify(sh("pytest -q '" + nid.replace("'", "'\\''") + "'"))
        assert "indirection" not in c.signals and (c.category, decide(c).outcome) in ON_164, (nid, c)


# --- review of e7f7b19: `git config` reads its subcommand and option values as git does --------------

@pytest.mark.parametrize("cmd,mention", [
    ("git config set include.path /tmp/c", "include.path"),
    ("git config -f .git/config include.path /tmp/c", "include.path"),
    ("git config --file .git/config --add include.path /tmp/c", "include.path"),
    ("git config --type bool includeIf.gitdir:/.path /tmp/c", "includeIf.gitdir:/.path"),
    ("git config --default x include.path /tmp/c", "include.path"),
    ("git config set --comment hi core.editor vim", "core.editor"),
    ("git config -zf .git/config core.pager less", "core.pager"),
    ("git config rename-section x include", "include"),     # x.path becomes include.path
    ("git config --rename-section x alias", "alias"),
    ("git init --template=/tmp/t", "template /tmp/t"),       # the hooks in a template run, as for clone
    ("git init --template /tmp/t repo", "template /tmp/t"),
])
def test_git_config_subcommand_and_option_values_do_not_hide_the_key(cmd, mention):
    asks(cmd, mention)


@pytest.mark.parametrize("cmd", [
    f"git config set core.pager '{RM}'",
    f"git config -f .git/config core.fsmonitor '{RM}'",
    f"git config --type bool core.pager '{RM}'",
    f"git config --type=bool core.pager '{RM}'",
    f"git config --file=.git/config set alias.x '!{RM}'",
    f"git config --replace-all core.sshCommand '{RM}'",
])
def test_git_config_command_value_is_classified_so_a_deny_fires(cmd):
    c = classify(sh(cmd))
    assert c.category == "delete", c
    assert decide(c).outcome == "ask" and decide(c, DENY).outcome == "deny", c


# --- review of e7f7b19: more keys that run a command, and the switches for ext:: URLs ---------------

@pytest.mark.parametrize("cmd", [
    f"git -c gpg.format=ssh -c gpg.ssh.defaultKeyCommand='{RM}' commit -S -m x",
    f"git -c interactive.diffFilter='{RM}' add -p",
    f"git -c core.alternateRefsCommand='{RM}' fetch origin",
    f"git -c remote.origin.uploadpack='{RM}' fetch origin",
    f"git -c remote.origin.receivepack='{RM}' push origin HEAD",
    f"git -c sendemail.sendmailCmd='{RM}' send-email x.patch",
    f"git clone -c mergetool.x.path='{RM}' https://h/r",
    f"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=difftool.x.path 'GIT_CONFIG_VALUE_0={RM}' git difftool",
])
def test_more_exec_keys_are_read_as_commands(cmd):
    c = classify(sh(cmd))
    assert c.category == "delete", c
    assert decide(c).outcome == "ask" and decide(c, DENY).outcome == "deny", c


@pytest.mark.parametrize("cmd,mention", [
    ("git -c protocol.ext.allow=always clone 'ext::sh -c touch% /tmp/p' x", "protocol.ext.allow"),
    ("git -c protocol.allow=always clone 'ext::sh -c touch% /tmp/p' x", "protocol.allow"),
    ("GIT_ALLOW_PROTOCOL=ext git clone 'ext::sh -c touch% /tmp/p' y", "GIT_ALLOW_PROTOCOL"),
    ("export GIT_ALLOW_PROTOCOL=ext:https; git fetch", "GIT_ALLOW_PROTOCOL"),
    ("git -c 'url.ext::sh -c touch% /tmp/p.insteadOf=https://h/' clone https://h/r", "url.ext::"),
    ("git config url.ext::x.pushInsteadOf https://h/", "url.ext::x.pushInsteadOf"),
    ("git config remote.origin.url 'ext::sh -c touch% /tmp/p'", "remote.origin.url"),
    ("git -c remote.origin.pushurl='ext::sh -c x' push origin HEAD", "remote.origin.pushurl"),
])
def test_ext_transport_switches_ask(cmd, mention):
    asks(cmd, mention)


@pytest.mark.parametrize("cmd,category,outcome", [
    ("git config user.name Bob", "local_write", "allow"),
    ("git config set user.name Bob", "local_write", "allow"),
    ("git config --global user.email bob@example.com", "local_write", "allow"),
    ("git config get core.pager", "local_write", "allow"),
    ("git config --get core.editor", "local_write", "allow"),
    ("git config -l", "local_write", "allow"),
    ("git config --list --show-origin", "local_write", "allow"),
    ("git config unset core.pager", "local_write", "allow"),
    ("git config --unset alias.st", "local_write", "allow"),
    ("git config -f .gitmodules submodule.x.url https://h/r", "local_write", "allow"),
    ("git config remote.origin.url https://github.com/x/y", "local_write", "allow"),
    ("git config remove-section alias", "local_write", "allow"),
    ("git -c protocol.file.allow=always submodule update --init", "local_write", "allow"),
    ("git -c url.https://example.com/.insteadOf=ex: fetch", "read", "allow"),
])
def test_git_config_that_runs_nothing_is_unchanged(cmd, category, outcome):
    c = classify(sh(cmd))
    assert (c.category, decide(c).outcome) == (category, outcome), c


# --- review of e7f7b19: PS4 is expanded before every traced command, also in a non-interactive shell -----

@pytest.mark.parametrize("cmd", [
    f"export PS4='$({RM}) '; set -x; ls",
    f"declare -x PS4='$({RM})'; set -o xtrace; ls",
    f"PS4='`{RM}`' bash -xc ls",
    f": ${{PS4:='$({RM})'}}; set -x; ls",
    f"export PS4='\\$({RM})'; set -x; ls",                # bash decodes the prompt's \$ before it expands
])
def test_ps4_command_substitution_is_classified(cmd):
    c = classify(sh(cmd))
    assert c.category == "delete", c
    assert decide(c).outcome == "ask" and decide(c, DENY).outcome == "deny", c


def test_ps4_without_a_substitution_and_an_unseen_ps4():
    c = classify(sh("export PS4='+ '; set -x; ls"))
    assert (c.category, decide(c).outcome) == ("read", "allow"), c
    asks("read -r PS4 < f; set -x; ls", "PS4 is expanded")


# --- review of e7f7b19: arithmetic assigns with ++, -- and the shift operators too ----------------

@pytest.mark.parametrize("cmd", ["echo $(( PATH++ ))", "echo $(( ++PATH ))", "echo $(( PATH-- ))",
                                 "echo $((--HOME))", "echo $(( PATH<<=1 ))", "echo $((PATH>>=1))",
                                 "echo ${a[PATH++]}"])
def test_increment_and_shift_assignments_ask(cmd):
    asks(cmd, "PATH" if "PATH" in cmd else "HOME")


# --- review of e7f7b19: values that change nothing keep the line's class; the rest ask on purpose -------

@pytest.mark.parametrize("cmd,category", [
    ("LD_PRELOAD= ls", "read"),
    ("LD_LIBRARY_PATH= ls", "read"),
    ("DYLD_INSERT_LIBRARIES= ls", "read"),
    ("GIT_CONFIG_GLOBAL=/dev/null git status", "read"),
    ("GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL=/dev/null git log", "read"),
    ("PATH=$PATH:./node_modules/.bin eslint .", "shell"),
    ("PATH=${PATH}:node_modules/.bin eslint .", "shell"),
    ('export PATH="$PWD/.venv/bin:$PATH"', "read"),
    ("PATH=/usr/local/bin:/usr/bin:/bin ls", "read"),
    ("PATH+=:/usr/local/bin ls", "read"),
    ("PYTHONPATH=src python -m pytest -q", "shell"),
    ("PYTHONPATH=src pytest -q", "shell"),
    ("PYTHONPATH=/tmp/x python3 -m pytest -q", "shell"),   # conftest.py already runs the project's code
    ("env PYTHONPATH=src:tests pytest -q", "shell"),
])
def test_a_value_that_changes_nothing_keeps_the_class(cmd, category):
    c = classify(sh(cmd))
    assert (c.category, decide(c).outcome) == (category, "allow"), c


@pytest.mark.parametrize("cmd,name", [
    ("PATH= ls", "PATH"),                                  # an empty element is the current folder
    ("PATH=$PATH: ls", "PATH"),
    ("PATH=:$PATH ls", "PATH"),
    ("PATH=$PATH::/usr/bin ls", "PATH"),
    ('export PATH="$HOME/.local/bin:$PATH"', "PATH"),      # a folder the agent can write to, first
    ("PATH=$PATH:/tmp/bin rg x", "PATH"),                  # appended: it supplies what the machine lacks
    ("PATH+=/x ls", "PATH"),                               # glued onto the last folder
    ("PATH=$X:$PATH ls", "PATH"),
    ("LD_LIBRARY_PATH=: ls", "LD_LIBRARY_PATH"),
    ("LD_PRELOAD=$X ls", "LD_PRELOAD"),
    ("GIT_CONFIG_GLOBAL=/tmp/g git status", "GIT_CONFIG_GLOBAL"),
    ("PYTHONPATH=src python3 -m json.tool f", "PYTHONPATH"),
    ("export PYTHONPATH=src; pytest -q", "PYTHONPATH"),
    ("PYTHONPATH=src python -B -m pytest", "PYTHONPATH"),
    ("CDPATH+= cd src", "CDPATH"),
])
def test_values_that_can_change_what_runs_still_ask(cmd, name):
    asks_naming(cmd, name)
