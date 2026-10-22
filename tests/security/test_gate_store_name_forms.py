"""Store names split by an expansion (#225, #309, #341) or reached through `..` from run/jobs (#269), in the forms
main 39cafcba still allowed after #305 and #307.

#225: bash runs `cat .e$(:)nv` as `cat .env`. #307 reads each expansion in a secret-store name as empty and, between
literal text, as `*` (_unknown_forms). Here that reading also takes a substitution at either end of a word as `*`,
`$(( ))` and `$[ ]` as substitutions, one that starts a name as `.*`, and the default word of `${x:-word}` (#341);
and it reaches working-folder targets and the Exo-store write checks (#309). #269: from inside ~/.exo/run/jobs, `..`
is the run folder, whose principal key and token are credentials. #305 resolves such names (_exo_relative); here it
also reads a glob or a split word that could be one, and a folder that holds a run folder.

Every command string here is data for exo.gate.classify/decide; none is run."""
import os
import sys

import pytest

from exo import gate

LAYOUTS = ["default", "default-principal", "custom", "custom-principal"]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in ("EXO_HOME", "EXO_KEYS_FILE", "EXO_PRINCIPAL_STORE", "EXO_PRINCIPAL", "CLAUDE_CONFIG_DIR", "EXO_SEAT"):
        monkeypatch.delenv(k, raising=False)
    return home


@pytest.fixture(params=LAYOUTS)
def store(request, tmp_path, home, monkeypatch):
    """(the folder that holds `run`, its jobs folder) under the default ~/.exo or a custom EXO_HOME, the home's
    own or a principal's (principals/<name>/run)."""
    if request.param.startswith("custom"):
        exo_home = tmp_path / "elsewhere" / "exo"
        monkeypatch.setenv("EXO_HOME", str(exo_home))
    else:
        exo_home = home / ".exo"
    top = exo_home / "principals" / "me" if request.param.endswith("principal") else exo_home
    return str(top), str(top / "run" / "jobs")


def sh(cmd):
    return {"tool": "bash", "args": {"command": cmd}}


def _asks(action):
    """Credentials; or store_write, the principal's alone, when the line also writes in the store (`tar czf -`
    after a cd into it). Either asks, also under a policy that allows both credentials and reads."""
    c = gate.classify(action)
    assert c.category in ("credentials", "store_write"), c
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, {"gates": {"categories": {"credentials": "allow", "read": "allow"}}}).outcome == "ask"


# #225: each was read/allow (the tar lines shell/allow) on main 39cafcba.
SPLIT = [
    # a substitution at the end of a name, its output any text
    "cat ~/.config/gh/$(printf hosts.yml)",
    "cat id_ed$((25519))",
    "cat id_ed$((25000 + 519))",
    "cat id_ed$[25519]",
    # a name made by a command on a line that enters a store folder (#192)
    "cd ~/.kube && cat $(ls)",
    # a working-folder target split the same way enters the store folder
    "cd ~/.k$(:)ube && cat config",
    "cd ~/.k`:`ube && cat config",
    "cd ~/.k$(printf u)be && cat config",
    "cd ~/.k${u}ube && cat config",
    "pushd ~/.k$(:)ube && cat config",
    "env -C ~/.k$(:)ube cat config",
    "tar -C ~/.k$(:)ube -czf - .",
    "tar --directory=~/.k$(:)ube -czf - .",
    # output that starts a name may start it with a dot, which a glob's `*` does not match
    "cat $(printf .e)nv",
    "cat ./$(printf .e)nv",
    "cd ~/$(printf .k)ube && cat config",          # a target too, when text follows in its part
    "cd ~/$(printf .k)ube/$(:) && cat config",     # a bare part after it may print nothing
    "cd ~/$(printf .k)ube/$(printf .) && cat config",
    "cd ~/$(printf .k)ube/$(:) && tar czf - .",
    "env -C ~/$(printf .k)ube/$(:) cat config",
    "tar -C ~/$(printf .k)ube/$(:) -czf - .",
    "pushd ~/$(printf .k)ube/$(:) && cat config",
    # a run/jobs target split the same way, or written as a glob (#269)
    "cd ~/$(printf .e)xo/run/jobs && grep -r x ..",
    "cd ~/.e${u}xo/run/jobs && grep -r x ..",
    "cd ~/.e*o/run/jobs && grep -r x ..",
    "cd ~/.e*o/r?n/jobs && grep -r x ..",
    "cd ~/.e$(printf x)o/r$(printf u)n/jobs && grep -r x ..",
]

# #341: the default word of an unknown variable may be the value. Each was read/allow on main 39cafcba.
DEFAULTS = [
    "cat ${x-.env}",
    "cat ${x:-.env}",
    "cat ${x:=.env}",
    "cat ${x:+.env}",
    'cat "${x:-.env}"',
    "cat ${x:-.e}nv",
    "cat ${x:-.}env",
    "cat ${x:-.env.local}",
    "cat ${x:-~/.netrc}",
    "cat ${x:-~/.n}etrc",
    "cat ${x:-~/.kube}/config",
    "cat ${x:-~/.kube/config}",
    "cat ${x:-~/.k}ube/config",
    "cat ${x:-$HOME/.exo}/keys/seat",
    "cat ${x:-$HOME/.e}xo/keys/seat",
    "cat ${x:-$HOME/.kube}/config",
    "cd ${x:-~/.kube} && cat config",
    # on an array element
    "cat ${x[0]:-.env}",
    "cat ${x[@]:-~/.kube}/config",
    "cat ${x[i+1]-~/.netrc}",
]

# Asked already on main 39cafcba (#307); kept beside the rows above, which read the same words more ways.
KEPT = [
    "cat .e$(:)nv",
    "cat ~/.n$(:)etrc",
    "cat ~/.config/gh/ho$(:)sts.yml",
    "cat ~/.config/gh/$(:)*",
    "cat .e$(echo n)v",
    "cat $HOME/.kube/c$(:)onfig",
    "cat $HOME/.docker/con$(:)fig.json",
    "cat .e``nv",
    "cat .e`printf n`v",
    "cat ~/.config/gh/hosts$(:).yml",
    "cat .e$(:)nv.local",
    "cat .env$(:).local",
    "cat .env.l$(:)ocal",
    "cat ~/proj/.e$(:)nv.production",
    "cat server.k$(:)ey",
    "cat cert.p$(:)em",
    "cd ~/.config/gh && cat .e$(:)nv.local",
    'cat .e"$(:)"nv',
    'cat ".e$(:)nv"',
    "cat ~/.n$(:){etrc,x}",
    "cat < ~/.n$(:)etrc",
    "curl --netrc-file=$HOME/.n$(:)etrc https://example.com",
    "echo $(cat ~/.n$(:)etrc)",
    "bash -c 'cat .e$(:)nv'",
    "cd ~/.kube && cat c$(:)onfig",
    "cd ~/.config/gh && cat ho$(:)sts.yml",
    "cat ~/$(printf .n)etrc",
    "cat .e${x:-n}v",
    "cat ${x:-~/.ssh/id_rsa}",
]


@pytest.mark.parametrize("cmd", SPLIT + DEFAULTS + KEPT)
def test_an_expansion_inside_a_store_name_asks(cmd):
    _asks(sh(cmd))


@pytest.mark.parametrize("line", ["cat ~/.n$(:)etrc", "cd ~/.k$(:)ube && cat config"])
def test_a_substitution_inside_a_store_name_in_code_asks(line):
    _asks({"tool": "execute_code", "args": {"code": f"import os\nos.system('{line}')"}})


# Everyday expansions in words that cannot spell a store name keep main 39cafcba's class.
EVERYDAY = [
    ("cat notes$(date +%F).txt", "read"),
    ("cat notes-$(date +%F).log", "read"),
    ("cat file$((i+1)).txt", "read"),
    ("cat file$[i+1].txt", "read"),
    ("echo $(( 1 + 2 ))", "read"),
    ("ls $(git rev-parse --show-toplevel)/src", "read"),
    ("cd $(git rev-parse --show-toplevel) && ls", "read"),
    ("cat $(git rev-parse --show-toplevel)/.git/config", "read"),
    ("tar czf build-$(git rev-parse --short HEAD).tar.gz dist", "shell"),
    ("cp out.txt /tmp/out-$(date +%s).txt", "shell"),
    ("echo \"built $(date)\"", "read"),
    ("cat $(ls *.md)", "read"),
    ("diff <(sort a.txt) <(sort b.txt)", "read"),
    ("cd $(git rev-parse --show-toplevel) && cat README.md", "read"),
    ("cd ~/$(git rev-parse --show-prefix) && ls", "read"),
    ("cd $(git rev-parse --show-toplevel)/.git && cat config", "read"),     # a bare part enters no dot folder
    ("cd ~/$(whoami)/x && cat config.json", "read"),
    ("cp x ~/report-$(date +%F).txt", "shell"),
    ("cp x /home/$(whoami)/notes.txt", "shell"),
    ("cp a $(git rev-parse --show-toplevel)/b", "shell"),
    ("rm -rf build-$(date +%F)", "delete"),
    ("cp x out/$(date +%F).json", "shell"),            # could be seat.json: a short name, as for a glob
    ("cp x out/$(date +%F).yaml", "shell"),
    ("cp db.sqlite backup/$(date +%F).sqlite", "shell"),
    # a variable that starts or ends a word is read as empty only, as on main (`$OUT/config` is not `*/config`)
    ("cat $OUT/report.txt", "read"),
    ('cat "$OUT/config"', "read"),
    ("cd \"$PROJECT\" && cat config", "read"),
    ("cp x \"$OUT/report.txt\"", "shell"),
    ("mv x \"$dest/\"", "shell"),                        # `/` when $dest is empty: no named part, not the store's parent
    ("rsync -a src/ \"$DEST/\"", "shell"),
    # a default word that names no store
    ("cat ${OUT:-/tmp/out}/report.txt", "read"),
    ("cat ${1:-README.md}", "read"),
    ("cd ${DIR:-.} && cat config", "read"),
    ("cat ${x[0]:-README.md}", "read"),
    # a name that could be `..` outside the store
    ("grep -r x ./.?", "read"),
    ("cd ~/project && ls .?/..", "read"),
]


@pytest.mark.parametrize("cmd,main", EVERYDAY)
def test_an_everyday_expansion_keeps_mains_class(cmd, main):
    assert gate.classify(sh(cmd)).category == main


# #269: read/allow (the tar lines shell/allow) on main 39cafcba, in every layout.
DOTDOT = [
    # a name split by a substitution (#225) or a glob that could be the run folder
    "tar -C {top} -czf - r$(:)un",
    "tar -C {top} -czf - r`:`un",
    "tar -C {top} -czf - ru?",
    "tar -C {top} -czf - ru[n]",
    "cd {top} && grep -r x r${{u}}un",
    "cd {jobs} && grep -r . .$(:).",
    "cd {jobs} && grep -r . .`:`.",
    "cd {jobs} && grep -r . .?",
    "cd {jobs} && grep -r x $(echo .).",
    "cd {jobs} && grep -r x ./.?",                 # after a `./`
    "cd {jobs} && ls ./.?/",
    "cd {jobs} && grep -r x .?/..",                # `.?/..` is `.`, or `../..` when `.?` is `..`
    "cd {jobs} && grep -r x ./.?/..",
    # a `cd ..` written as a glob or split by a substitution
    "cd {jobs} && cd .$(:). && grep -r .",
    "cd {jobs} && cd ..$(:) && grep -r .",
    "cd {jobs} && cd $(:).. && grep -r .",
    "cd {jobs} && cd .? && grep -r .",
    "cd {jobs} && cd .[.] && grep -r .",
    "cd {jobs} && cd .$(:). && cat jobs/1.log",      # as `cd {jobs} && cd .. && cat jobs/1.log` (JOBS below)
    # run/jobs reached by a split relative target below the folder that holds `run`
    "cd {top} && cd r$(:)un/jobs && grep -r x ..",
    # run/jobs, or a folder in it, spelt with a glob or a printing substitution below the store's own folder
    "cd {top}/r?n/jobs && grep -r x ..",
    "cd {top}/run/j?bs && grep -r x ..",
    "cd {top}/r*/jobs && ls ..",
    "cd {top}/ru*/jobs && grep -r x ..",
    "cd {top}/r$(printf u)n/jobs && grep -r x ..",
    "cd {top}/r`printf u`n/jobs && grep -r x ..",
    "cd {top}/r?n/jobs/sub && grep -r x ../..",
    "cd {top}/r?n && grep -r x .",
    "cd {top} && cd r?n/jobs && grep -r x ..",
    "cd {top} && cd r$(printf u)n/jobs && grep -r x ..",
    "tar -C {top}/r?n/jobs -czf - ..",
    "cd {jobs}/.? && grep -r x .",
    "cd {jobs}/.[.] && ls .",
    "cd {jobs}/* && ls ../..",
    # the folder that holds the run folder, by `..`
    "cd {jobs} && grep -r x ../..",
    "cd {jobs} && grep -r x ../../",
    "cd {jobs} && ls ../..",
]

# Asked already on main 39cafcba (#305); kept beside the rows above.
DOTDOT_KEPT = [
    "cd {jobs} && cat ../exod.principal.k*",
    "cd {jobs} && cat ../exod*",
    "cd {jobs} && cat ../agent-default*",
    "cd {jobs} && grep -r . ..",
    "cd {jobs} && grep -r . ../",
    "cd {jobs} && ls ..",
    "cd {jobs} && tar czf - ../../run",
    "tar -C {jobs} -czf - ..",
    "tar -C {top} -czf - run",
    "cd {top} && ls run",
    "cd {jobs} && cd .. && grep -r .",
    "cd {jobs} && cd ../.. && grep -r . run",
    "cd {jobs} && cd .. && cd .. && grep -r . run",
    "cd {jobs} && cd " + "/".join([".."] * 70) + " && grep -r .",      # past 64 levels: any number
    "cd {top} && cd run/jobs && grep -r x ..",
    "cd {top} && cd ./run/jobs && grep -r x ..",
    "cd {top} && cd run && cd jobs && grep -r x ..",
    "cd {top} && cd run/jobs && cd .. && grep -r .",
    "tar -C {top} -C run/jobs -czf - ..",
    "cd {jobs} && cd ${{PWD%/*}} && grep -r x .",
    "cd {top} && cd run/jobs && tar czf - ../..",
]


@pytest.mark.parametrize("shape", DOTDOT + DOTDOT_KEPT)
def test_the_run_folder_from_its_jobs_folder_or_its_parent_asks(shape, store):
    top, jobs = store
    _asks(sh(shape.format(top=top, jobs=jobs)))


@pytest.mark.parametrize("shape", DOTDOT + DOTDOT_KEPT)
def test_the_run_folder_by_home_spelling_asks(shape):
    _asks(sh(shape.format(top="~/.exo", jobs="~/.exo/run/jobs")))


# Job output in run/jobs stays readable after a cd into it.
JOBS = [
    ("cd {jobs} && cat 1.log", "read"),
    ("cd {jobs} && tail -f 3.log", "read"),
    ("cd {jobs} && cat *.log", "read"),
    ("cd {jobs} && cat $f", "read"),
    ("cd {jobs} && ls", "read"),
    ("cd {jobs} && ls .", "read"),
    ("tail -f 3.log", "read"),
    ("cd {top} && cd run/jobs && cat 1.log", "read"),
    ("cd {top} && cd run/jobs && tail -f 3.log", "read"),
    ("cd {top}/r?n/jobs && cat 1.log", "read"),
    ("cd {top} && cd r?n/jobs && tail -f 3.log", "read"),
    ("cd {jobs}/* && cat 1.log", "read"),
    # main (#305) checks the run folder a `cd ..` reaches as its path, so these ask (fail closed)
    ("cd {jobs} && cd .. && cat jobs/1.log", "credentials"),
    ("cd {jobs} && cd .. && cd jobs && tail -f 3.log", "credentials"),
]


@pytest.mark.parametrize("shape,main", JOBS)
def test_job_output_after_a_cd_into_jobs_keeps_mains_class(shape, main, store):
    top, jobs = store
    assert gate.classify(sh(shape.format(top=top, jobs=jobs))).category == main
    assert gate.classify(sh(shape.format(top="~/.exo", jobs="~/.exo/run/jobs"))).category == main


# Everyday lines that now ask, failing closed: read/allow (the cp line shell/allow) on main 39cafcba. GATE.md lists
# them with the cost.
FAIL_CLOSED = [
    "cd ~/.exo/run/jobs && tail -f $(ls -t | head -1)",
    "cd ~/.kube && echo $(date)",
    "cd ~/.kube && kubectl get pods -n $(cat ns.txt)",
    "cd ~/.exo && ls principals",
    "cat ${x}rc",                                     # `.*rc` could be .netrc
    "cp x state/$(date +%F).json",                   # could be state/approval-grants.json
]


@pytest.mark.parametrize("cmd", FAIL_CLOSED)
def test_an_everyday_line_that_could_reach_a_store_asks(cmd):
    _asks(sh(cmd))


# A principal's store, which holds its run folder, by a relative name or target: read/allow (the tar line
# shell/allow) on main 39cafcba.
PRINCIPALS = [
    "cd {home}/principals && grep -r x m$(:)e/r$(:)un",
    "cd {home} && grep -r x principals/me",
    "cd {home}/principals/me && grep -r x .",
    "cd {home}/principals/me && cd .. && grep -r x .",
    "tar -C {home}/principals/me -czf - .",
    "cd {home}/principals/me/run/jobs && grep -r x ../..",
    "cd {home}/principals/m?/run/jobs && grep -r x ..",
    "cd {home}/p*/me/r?n/jobs && grep -r x ..",
    "cd {home}/principals/*/r?n/jobs && grep -r x ../..",
    "cd {home}/principals && cd m?/r?n/jobs && grep -r x ..",
]

# Asked already on main 39cafcba (#305); kept beside the rows above.
PRINCIPALS_KEPT = [
    "cd {home}/principals && grep -r x me/run",
    "cd {home} && cd principals && grep -r x me/run",
    "cd {home} && cd principals/me && grep -r x run",
    "cd {home} && cd principals/me/run/jobs && grep -r x ..",
    "tar -C {home}/principals -czf - me/run",
    "cd {home} && grep -r x principals/me/run",
    "cd {home} && grep -r x ./principals/me/run/",
    "cd {home}/run/jobs && grep -r x ../../principals/me/run",
]


@pytest.mark.parametrize("shape", PRINCIPALS + PRINCIPALS_KEPT)
@pytest.mark.parametrize("custom", [False, True], ids=["default", "custom"])
def test_a_principals_run_folder_by_a_relative_name_asks(shape, custom, tmp_path, monkeypatch):
    exo_home = "~/.exo"
    if custom:
        exo_home = str(tmp_path / "elsewhere" / "exo")
        monkeypatch.setenv("EXO_HOME", exo_home)
    _asks(sh(shape.format(home=exo_home)))


# #309: a store write split by an expansion. Each was shell/allow on main 39cafcba (the `cd` lines read/allow), or
# asked as delete, opaque_exec (the redirection) or history_rewrite, which a policy may allow.
STORE_WRITES = [
    "cp x ~/.exo/polic$(:)ies/gates.yaml",
    "cp x ~/.exo/polic``ies/gates.yaml",
    "mv x ~/.exo/polic${u}ies/gates.yaml",
    "tee ~/.exo/polic$(:)ies/gates.yaml < x",
    "echo x > ~/.exo/polic$(:)ies/gates.yaml",
    "cp x ~/.exo/polic$(:)ies/",
    "cp x ~/.exo/polic${u}ies/",
    "cp x ~/.exo/pol$(:)icies/default.yaml",
    "cp x ~/.e$(:)xo/policies/default.yaml",
    "cp x ~/.exo/$(printf policies)/default.yaml",
    "cp x ~/$(printf .exo)/policies/default.yaml",
    "cp x ~/.exo/principals/me/k$(:)eys/a",
    "cp x ~/.exo/{pol$(:)icies,x}/a",
    "cp x .exo/pol$(:)icies/a",
    "cp x policies/gat$(:)es.yaml",
    "cp x led$(:)ger.sqlite",
    "cp x led$(printf g)er.sqlite",
    "tee ~/.e$(:)xo/policies/default.yaml",
    "ln -s x ~/.exo/polic``ies/y",
    "ln -sf /tmp/p ~/.e$(:)xo/policies",
    "mv x ~/.exo/sea$(:)ts/",
    "rm -rf ~/.e$(:)xo/policies",
    "rm -rf ~/.exo/pol$(:)icies",
    "dd if=x of=~/.exo/pol$(:)icies/a",
    "cd ~/.e$(:)xo && cp x policies/a",
    "cd ~/.e$(printf x)o && cp x policies/a",
    "cd ~/.e${u}xo && cp x policies/a",
    "git -C ~/.e$(:)xo/policies checkout .",
    # the default word of an unknown variable (#341)
    "cp x ${x:-$HOME/.exo}/policies/a",
    'cp x "${x:-$HOME/.exo}/policies/a"',
    "cp x ${x[0]:-$HOME/.exo}/policies/a",
]


@pytest.mark.parametrize("cmd", STORE_WRITES)
def test_an_expansion_inside_a_store_write_asks(cmd):
    c = gate.classify(sh(cmd))
    assert c.category == "store_write", c
    loose = {"gates": {"categories": {k: "allow" for k in ("shell", "delete", "local_write", "opaque_exec",
                                                            "history_rewrite")}}}
    assert gate.decide(c).outcome == "ask"
    assert gate.decide(c, loose).outcome == "ask"


@pytest.mark.parametrize("cmd,cat", [("tee ~/.exo/key$(:)s/a", "credentials"), ("cp x ~/.exo/ke$(:)ys/a", "credentials"),
                                     ("cp x ~/.exo/$(printf keys)/a", "store_write"),
                                     ("tee ~/.exo/k$(printf e)ys/a", "store_write")])
def test_a_substitution_inside_a_write_to_the_keys_folder_asks(cmd, cat):
    """keys/ is a secret store as well as protected: a write there that the empty reading names is credentials, as
    unsplit (`cp x ~/.exo/keys/a`); one only the `*` reading reaches is store_write, the principal's alone. The
    last two were shell/allow on main 39cafcba."""
    _asks(sh(cmd))
    assert gate.classify(sh(cmd)).category == cat
    assert gate.classify(sh("cp x ~/.exo/keys/a")).category == "credentials"


def test_the_seats_folder_is_read_once_per_call(home, monkeypatch):
    """_run_secrets reads up to 4,096 seats/ entries; one classify call reads them once, not once per glob word
    (the change came to main with #305; this guards it)."""
    seats = home / ".exo" / "seats"
    seats.mkdir(parents=True)
    for i in range(8):
        (seats / f"s{i}.toml").write_text("")
    real, calls = os.scandir, []

    def scandir(path="."):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(gate.os, "scandir", scandir)
    gate.classify(sh("ls " + " ".join(f"g{i}*" for i in range(50))))
    assert sum(p.endswith("seats") for p in calls) == 1, calls
    calls.clear()
    gate.classify(sh("ls a*"))
    assert sum(p.endswith("seats") for p in calls) == 1        # a new call reads them again


def _line(n: int) -> str:
    return ("cd ~/.kube && cat " + " ".join(f"d{i}/n$(:)o{i}.t`:`xt f{i}$((i+1)).log" for i in range(n)) +
            " ~/x/" + "a$(:)" * n + "b")


def _write_line(n: int) -> str:
    return "cp " + " ".join(f"d{i}/n$(:)o{i}.t`:`xt ~/w{i}/a$(:)b/$(:)c" for i in range(n)) + " ~/x/" + "a$(:)" * n + "b"


def _jobs_line(n: int) -> str:
    return ("cd ~/.exo/run/jobs && cd j$(:)obs && cat " + " ".join(f"{i}.l$(:)og j{i}/.$(:)./{i}.log" for i in range(n)) +
            " " + "../" * n + "x")


def _names_line(n: int) -> str:
    return ("cd ~/.exo/principals && cat " + " ".join(f"p{i}/principals/q$(date){i}/{i}.log" for i in range(n)) +
            " " + "p/principals/" * (n // 4) + "p/x.log")


def _vars_line(n: int) -> str:
    return ("cp " + " ".join(f"d{i}/n${{u}}o{i}.t${{x:-x}}t f$[{i}+1] ~/w{i}/a${{v}}b/" for i in range(n)) +
            " ~/x/" + "a${u}" * n + "b")


def _globs_line(n: int) -> str:
    return " && ".join(f"cd ~/.exo/p*/m?/r?n/j?bs/{i}/*/.?/l?g{i}" for i in range(n)) + " && cat 1.log"


@pytest.mark.parametrize("line,cat", [(_line, "read"), (_write_line, "shell"), (_jobs_line, "read"),
                                      (_names_line, "read"), (_vars_line, "shell"), (_globs_line, "credentials")],
                         ids=["read", "write", "jobs", "names", "vars", "globs"])
def test_many_expansions_in_path_words_stay_linear(line, cat, monkeypatch):
    """Each word with an expansion is read in a few more forms, each one string: one pass per word, no splitting
    into every way the gaps could be filled. The work is counted (_unknown_forms and _store_glob calls, and every
    Python call), not timed: twice the line is about twice the work."""
    count = {"forms": 0, "glob": 0, "calls": 0}
    real_forms, real_glob = gate._unknown_forms, gate._store_glob

    def forms(*a, **kw):
        count["forms"] += 1
        return real_forms(*a, **kw)

    def glob(*a, **kw):
        count["glob"] += 1
        return real_glob(*a, **kw)

    def calls(frame, event, arg):
        count["calls"] += event in ("call", "c_call")

    monkeypatch.setattr(gate, "_unknown_forms", forms)
    monkeypatch.setattr(gate, "_store_glob", glob)
    seen = []
    for n in (150, 300):
        count.update(forms=0, glob=0, calls=0)
        sys.setprofile(calls)
        try:
            c = gate.classify(sh(line(n)))
        finally:
            sys.setprofile(None)
        assert c.category == cat, c
        seen.append(dict(count))
        # the hit check, the #192 check and the store-write check, a few times per word: once per word for the
        # line's own words, and once more for each substitution's own text (`:`)
        assert count["forms"] <= 8 * (3 * n + 3), (n, count)
    for k in ("forms", "glob", "calls"):
        assert seen[1][k] <= 2.2 * seen[0][k] + 50, seen
