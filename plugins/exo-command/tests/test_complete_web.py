"""D-037 in the web client: the completion routes (commands, files), their guard, and static checks on the JS."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from exo_command import api
from exo_command.config import load_settings
from exo_command.server import STATIC, WebServer
from exo_command.service import CommandCentre
from exo_command.source import SourceError

TOKEN = "test-page-token-0123456789"


class Src:
    def __init__(self, cwd="", guidance=None, commands=None):
        self.cwd, self.guidance, self.commands, self.calls = cwd, guidance, commands, []

    async def call(self, body):
        self.calls.append(body)
        t = type(body).TYPE
        if t == "commands.list" and self.commands is not None:
            if isinstance(self.commands, Exception):
                raise self.commands
            return {"commands": self.commands}
        if t == "session.get":
            return {"session": {"id": body.session, "cwd": self.cwd if body.session == "s1" else ""}, "messages": []}
        if t == "config.get":
            if isinstance(self.guidance, Exception):
                raise self.guidance
            return {"key": body.key, "value": self.guidance}
        return {}

    async def stream(self, since=0):
        await asyncio.Event().wait()
        yield {}

    async def aclose(self):
        return None


def dispatch(src, path, q=None):
    c = CommandCentre(load_settings({}), src)
    return asyncio.run(api.dispatch(c, "GET", path, q or {}, {}))


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel in ("README.md", "src/app.py", "docs/guide.md", ".env", "keys/id_ed25519", ".aws/credentials"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    return tmp_path


# ---------------------------------------------------------------- routes
def test_commands_route_lists_what_the_composer_runs():
    code, d = dispatch(Src(), "/api/complete/commands")
    names = [c["name"] for c in d["commands"]]
    assert code == 200 and names == ["context", "model", "default", "findings", "trace", "new", "rename", "taste", "layout"]
    trace = next(c for c in d["commands"] if c["name"] == "trace")
    assert trace["args"] == "[last N turns]" and [x["value"] for x in trace["choices"]] == ["5", "10", "25"]
    assert d["guidance"] is True


def test_commands_route_names_the_core_commands_the_composer_holds():
    rows = [{"name": "context", "description": "d", "args": ""}, {"name": "grants", "description": "d", "args": ""}, {"description": "no name"}]
    assert dispatch(Src(commands=rows), "/api/complete/commands")[1]["core"] == ["context", "grants"]
    code, d = dispatch(Src(commands=ConnectionError("core restarting")), "/api/complete/commands")
    assert code == 200 and d["core"] is None and len(d["commands"]) == 8   # null: the page asks again
    # The terminal client's own names (its COMMANDS table), less what the page runs: held, not sent. Core plugin names
    # (/grants, /orchestra) are not the terminal client's: unknown unless commands.list has them, as in the TUI.
    from exo_tui.app import COMMANDS
    assert set(d["client"]) == set(COMMANDS) - set(api.WEB_COMMANDS)
    assert not {r["name"] for r in d["commands"]} & set(d["client"])


def test_commands_route_tells_an_older_core_from_one_that_cannot_answer_now():
    """Only a core without commands.list answers [] and stops the page asking; a core down or failing is null (#146)."""
    for exc, core in ((SourceError("unknown_command", "'commands.list'"), []),
                      (SourceError("unsupported", "commands.list is not supported yet"), []),
                      (SourceError("internal", "boom"), None), (ConnectionError("refused"), None),
                      (RuntimeError("Exo core is not reachable"), None)):
        code, d = dispatch(Src(commands=exc), "/api/complete/commands")
        assert code == 200 and d["core"] == core, exc


def test_guidance_is_unknown_while_config_get_fails_and_on_for_an_older_core():
    # null while the core cannot say (the page asks again); an older core without the key leaves the line on
    assert dispatch(Src(guidance=ConnectionError("down")), "/api/complete/commands")[1]["guidance"] is None
    assert dispatch(Src(guidance=SourceError("internal", "x")), "/api/complete/commands")[1]["guidance"] is None
    assert dispatch(Src(guidance=SourceError("unknown_command", "x")), "/api/complete/commands")[1]["guidance"] is True
    # the core answers not_found for an unset key: the default (on) holds, and the page stops asking
    unset = SourceError("not_found", "guidance.enabled is not set")
    assert dispatch(Src(guidance=unset), "/api/complete/commands")[1]["guidance"] is True


def test_a_real_core_with_a_fresh_home_settles_the_guidance_setting(tmp_path):
    """A real core answers config.get for the unset guidance.enabled with not_found; the route reads that as on."""
    from exo.core.engine import CoreEngine
    from exo.protocol import schema as s
    from exo.server.app import Dispatcher
    from exo_command.source import DispatcherSource

    eng = CoreEngine(tmp_path / "home", {"roles": {"main": ["fake:x"], "aux": ["fake:x"]}}, cwd=str(tmp_path), env={})
    src = DispatcherSource(Dispatcher(eng))
    code, d = dispatch(src, "/api/complete/commands")
    assert code == 200 and d["guidance"] is True and d["core"]
    asyncio.run(src.call(s.ConfigSet(key="guidance.enabled", value=False)))
    assert dispatch(src, "/api/complete/commands")[1]["guidance"] is False


def test_guidance_config_key_turns_the_line_off():
    assert dispatch(Src(guidance=False), "/api/complete/commands")[1]["guidance"] is False
    assert dispatch(Src(guidance=True), "/api/complete/commands")[1]["guidance"] is True


def test_files_route_lists_inside_the_session_folder_and_hides_secrets(repo):
    code, d = dispatch(Src(str(repo)), "/api/complete/files", {"session": ["s1"], "q": [""]})
    vals = [f["value"] for f in d["files"]]
    assert code == 200 and vals[:2] == ["@docs/", "@src/"] and "@README.md" in vals
    every = []
    for q in ("", "env", "id_ed", "cred", "keys/", ".aws/"):
        every += [f["value"] for f in dispatch(Src(str(repo)), "/api/complete/files", {"session": ["s1"], "q": [q]})[1]["files"]]
    assert "@.env" not in every and not any("id_ed25519" in e or "credentials" in e for e in every)


def test_files_route_is_cwd_bounded_and_needs_a_session(repo):
    for q in ("/etc/", "../", "~/", "src/../.."):
        assert dispatch(Src(str(repo)), "/api/complete/files", {"session": ["s1"], "q": [q]})[1]["files"] == []
    assert dispatch(Src(str(repo)), "/api/complete/files", {"q": [""]})[1]["files"] == []
    assert dispatch(Src(str(repo)), "/api/complete/files", {"session": ["nope"], "q": [""]})[1]["files"] == []


def test_explicit_required_source_does_not_reuse_ordinary_file_index(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from exo_command.source import DispatcherSource
    monkeypatch.delenv('EXO_TOOL_EXECUTOR_REQUIRED', raising=False)
    (tmp_path / 'safe.txt').write_text('safe')
    ordinary = Src(str(tmp_path))
    ordinary.executor_required = False
    api._INDEXES.clear()
    query = {'session': ['s1'], 'q': ['']}
    dispatch(ordinary, '/api/complete/files', query)
    required = DispatcherSource(SimpleNamespace(engine=SimpleNamespace(executor_required=True)))
    required.call = ordinary.call
    def no_git(*args, **kw): raise AssertionError('completion executed host Git')
    monkeypatch.setattr(api.cm.subprocess, 'run', no_git)
    code, result = dispatch(required, '/api/complete/files', query)
    assert code == 200 and [f['value'] for f in result['files']] == ['@safe.txt']
    assert (str(tmp_path), True) in api._INDEXES


def test_files_route_is_read_only(repo):
    c = CommandCentre(load_settings({}), Src(str(repo)))
    assert asyncio.run(api.dispatch(c, "POST", "/api/complete/files", {}, {}))[0] == 405
    assert asyncio.run(api.dispatch(c, "POST", "/api/complete/commands", {}, {}))[0] == 405


def test_files_route_over_http_needs_the_page_token(repo):
    async def go():
        web = WebServer(CommandCentre(load_settings({}), Src(str(repo))), token=TOKEN)
        port = await web.start("127.0.0.1", 0)

        async def get(path, headers):
            h = {"Host": f"127.0.0.1:{port}", **headers}
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write((f"GET {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items()) + "\r\n").encode())
            await w.drain()
            resp = await r.read()
            w.close()
            return int(resp.split(b" ", 2)[1]), json.loads(resp.split(b"\r\n\r\n", 1)[1])

        path = "/api/complete/files?session=s1&q="
        assert (await get(path, {}))[0] == 403
        assert (await get(path, {"X-Exo-Token": "wrong"}))[0] == 403
        assert (await get(path, {"X-Exo-Token": TOKEN, "Host": "evil.example"}))[0] == 403
        code, d = await get(path, {"X-Exo-Token": TOKEN})
        assert code == 200 and d["files"]
        assert (await get("/api/complete/commands", {}))[0] == 403   # D-039: every /api route needs the token
        assert (await get("/api/complete/commands", {"X-Exo-Token": TOKEN}))[0] == 200
        await web.close()

    asyncio.run(go())


# ---------------------------------------------------------------- static checks
def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_new_modules_are_wired_and_local():
    chat, comp = text("chat.js"), text("composer.js")
    assert 'from "./composer.js"' in chat and "help.typo(" in chat and "help.sent(" in chat and "help.stop()" in chat
    assert 'from "./complete.js"' in comp
    assert not re.search(r"^\s*import\b", text("complete.js"), re.M)          # pure, importable in node
    for name in ("complete.js", "composer.js"):
        js = text(name)
        assert "localStorage" not in js and "sessionStorage" not in js and "innerHTML" not in js and "console." not in js
        assert "eval(" not in js and "http://" not in js and "https://" not in js


def test_enter_is_taken_before_the_send_handler_and_files_carry_the_token():
    comp = text("composer.js")
    assert "stopImmediatePropagation" in comp and "}, true);" in comp          # capture phase, ahead of chat.js
    assert "/api/complete/files" in comp and '"X-Exo-Token": token()' in text("dom.js")   # D-039: on every call


def test_guidance_respects_reduced_motion_and_is_one_line():
    comp, css = text("composer.js"), text("style.css")
    assert "reducedMotion" in comp and "setInterval(pickTip" in comp and "reducedMotion ? 0" in comp
    assert "prefers-reduced-motion" in css.split("completion (D-037)")[1]
    block = css.split("completion (D-037)")[1]
    assert ".xc-guide" in block and "white-space: nowrap" in block.split(".xc-guide {")[1].split("}")[0]


def test_menu_uses_the_site_tokens_only():
    block = text("style.css").split("completion (D-037)")[1]
    assert "border-left: 2px solid" in block and "var(--accent)" in block and "var(--xc-mono)" in block
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block)                         # no new hard-coded colours


# ---------------------------------------------------------------- the JS logic, run in node
NODE = shutil.which("node")


def node(tmp_path, body):
    (tmp_path / "complete.mjs").write_text(text("complete.js"))
    (tmp_path / "t.mjs").write_text('import * as c from "./complete.mjs";\n' + body)
    out = subprocess.run([NODE, str(tmp_path / "t.mjs")], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


CMDS = ('const cmds = [{name:"context",description:"what the next call carries",args:"",choices:[]},'
        '{name:"findings",description:"open harness findings",args:"[count]",choices:[{value:"count",meta:"how many"}]},'
        '{name:"trace",description:"how the harness did",args:"[last N turns]",choices:[{value:"5",meta:"last 5"},{value:"10",meta:"last 10"}]},'
        '{name:"new",description:"start a new thread",args:"",choices:[]}];\n')


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_js_ranking_matches_the_python_engine(tmp_path):
    from exo import complete as cm
    got = node(tmp_path, CMDS + 'console.log(JSON.stringify({s: [c.score("mod","model"), c.score("del","model"), c.score("mdl","model"), c.score("b","foo-bar"), c.score("zz","model")],'
                               'slash: c.suggest("/", cmds, false).items.map(i=>i.value), co: c.suggest("/co", cmds, false).items.map(i=>i.value),'
                               'cnt: c.suggest("/tr", cmds, false).items.map(i=>i.value), meta: c.suggest("/harness", cmds, false).items.map(i=>i.value)}));')
    assert got["s"] == [cm.score("mod", "model"), cm.score("del", "model"), cm.score("mdl", "model"), cm.score("b", "foo-bar"), None]
    assert got["slash"] == ["context", "findings", "trace", "new"]
    assert got["co"] == ["context"] and got["cnt"] == ["trace"] and got["meta"] == ["findings", "trace"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_js_arguments_typo_ghost_and_completion(tmp_path):
    got = node(tmp_path, CMDS + '''
const a = c.suggest("/trace ", cmds, false), b = c.suggest("/findings ", cmds, false), d = c.suggest("/context ", cmds, false);
const sg = c.suggest("/tra", cmds, false);
console.log(JSON.stringify({
  trace: a.items.map(i=>i.value), traceHint: a.hint, find: b.items.map(i=>i.value), ctx: d.hint, ctxItems: d.items.length,
  fill: c.completeText("/tra", sg, sg.items[0]), argfill: c.completeText("/trace 1", c.suggest("/trace 1", cmds, false), c.suggest("/trace 1", cmds, false).items[0]),
  typo: [c.closest("comand", ["context","findings","trace","new"]), c.closest("tarce", ["context","findings","trace","new"]), c.closest("qqqqq", ["context","trace"]), c.closest("trace", ["trace"])],
  shell: c.suggest("!ls", cmds, false).mode, plain: c.suggest("hello", cmds, false).mode, multi: c.suggest("/tr\\nx", cmds, false).mode,
  at: c.suggest("see @src/a", cmds, true), noat: c.suggest("see @src/a", cmds, false).mode, mail: c.suggest("a@b.c", cmds, true).mode,
  ghHist: c.ghost("fix t", c.suggest("fix t", cmds, false), ["fix the test"]), ghCmd: c.ghost("/tra", sg, []), ghHint: c.ghost("/trace ", a, []),
}));''')
    assert got["trace"] == ["5", "10"] and got["traceHint"] == "[last N turns]"
    assert got["find"] == ["count"] and got["ctx"] == "" and got["ctxItems"] == 0
    assert got["fill"] == "/trace " and got["argfill"] == "/trace 10"
    assert got["typo"][1] == "trace" and got["typo"][0] in ("context", None) and got["typo"][2:] == [None, None]
    assert got["shell"] == "shell" and got["plain"] == "" and got["multi"] == ""
    assert got["at"]["mode"] == "file" and got["at"]["start"] == 4 and got["at"]["query"] == "@src/a"
    assert got["noat"] == "" and got["mail"] == ""
    assert got["ghHist"] == {"text": "he test", "ok": True} and got["ghCmd"] == {"text": "ce", "ok": True}
    assert got["ghHint"] == {"text": "[last N turns]", "ok": False}


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_js_typo_guess_is_the_tuis(tmp_path):
    """#146: the page holds a /word for the same guesses the TUI's cm.closest makes, so `/data is corrupted` is sent."""
    import random
    from exo import complete as cm
    pool = ["context", "model", "default", "findings", "trace", "new", "rename", "layout", "compact", "grants", "gate",
            "peers", "peer-release", "peer-deny", "exo", "exo-command", "orchestra", "help", "quit", "undo"]
    rnd = random.Random(146)
    words = ["data", "note", "repo", "users", "tmp", "home", "me", "grant", "contex", "tarce", "modle", "comand", "peer",
             "tex", "ord", "orch", "peerdeny", "lay"] + ["".join(rnd.choice("acdeghilmnoprstx-") for _ in range(rnd.randint(1, 9))) for _ in range(400)]
    for _ in range(400):   # near typos: a name with a letter or two dropped, changed or swapped
        w = list(rnd.choice(pool))
        for _ in range(rnd.randint(1, 2)):
            i = rnd.randrange(len(w))
            w[i:i + 2] = rnd.choice([[], [rnd.choice("aeinorst")], w[i:i + 2][::-1]])
        words.append("".join(w))
    got = node(tmp_path, f"console.log(JSON.stringify({json.dumps(words)}.map(w => c.closest(w, {json.dumps(pool)}))));")
    assert got == [cm.closest(w, pool) for w in words]
    assert [cm.closest(w, pool) for w in ("data", "note", "repo", "grant")] == [None, None, None, "grants"]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_js_guidance_rules_rate_limit_and_off(tmp_path):
    got = node(tmp_path, '''
let t = 1000; const g = new c.Guide({clock: () => t});
const out = [];
out.push(g.pick({busy: true, findings: 2}));
out.push(g.pick({findings: 2}).fill);
out.push(g.pick({findings: 2, turns: 20}));      // inside the gap
t += 30000; out.push(g.pick({findings: 2, turns: 20}).key);   // another rule speaks
g.used("findings"); t += 30000; out.push(g.pick({findings: 2}));
const off = new c.Guide({enabled: false, clock: () => t}); out.push(off.pick({providerError: true}));
const keys = new c.Guide({clock: () => t}); 
const lines = [{providerError:true},{jobDone:"j_1"},{findings:3},{longTurn:true},{turns:50}].map(s => keys.rules(s)[0].text.length <= 72);
console.log(JSON.stringify({out, lines}));''')
    assert got["out"] == [None, "/findings", None, "context", None, None]
    assert all(got["lines"])


def test_escape_never_stops_a_turn_in_the_page():
    """D-043: Escape closes/cancels local UI, never interrupts a turn. DOM stopPropagation is harmless."""
    static = Path(__file__).resolve().parents[1] / "exo_command" / "static"
    for js in static.glob("*.js"):
        for line in js.read_text(encoding="utf-8").splitlines():
            if "Escape" in line:
                assert not re.search(r"\b(?:interrupt|stopTurn)\s*\(|/interrupt", line), (js.name, line)
