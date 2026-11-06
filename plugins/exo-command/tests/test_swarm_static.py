"""The Swarm view (static/swarm.js, swarm.css) and its wiring, checked two ways: static source rules, and the real
swarm.js + dom.js run in node against a small DOM and a scripted fetch (the pattern of test_approval_web.py and the
shell test). Nothing here reaches a network or a core; the API-shape tests feed the UI replies produced by the real
swarm_api.dispatch and replay the UI's own recorded requests back through it."""
import asyncio
import json
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest

from exo.protocol import schema as s
from exo_command import swarm_api as sw
from exo_command.server import CSP, STATIC
from exo_command.source import SourceError
from test_swarm_api import GID, GROUP, Centre, msg

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(not NODE, reason="Node is needed")


def text(name):
    return (STATIC / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------ static rules
def test_swarm_js_imports_only_dom_and_uses_no_forbidden_apis():
    js = text("swarm.js")
    assert re.findall(r'^import .* from "([^"]+)";', js, re.M) == ["./dom.js"]
    assert len(re.findall(r"^\s*import\b", js, re.M)) == 1
    code = "\n".join(re.sub(r"//.*", "", ln) for ln in js.splitlines())
    for bad in ("innerHTML", "outerHTML", "insertAdjacent", "document.write", "localStorage", "sessionStorage", "console.",
                "fetch(", "eval(", "new Function", "setTimeout", "setInterval", "EventSource", "WebSocket", "location",
                "style:", ".style", "dangerouslySet", "postMessage", "window.open"):
        assert bad not in code, bad
    assert "export function mount(root)" in js


def test_swarm_css_has_no_external_or_inline_loading():
    css = text("swarm.css")
    assert "url(" not in css and "@import" not in css and "expression(" not in css and "javascript:" not in css


def test_swarm_is_wired_and_served_under_the_csp():
    html, app = text("index.html"), text("app.js")
    assert '<link rel="stylesheet" href="/static/swarm.css">' in html and "<style" not in html
    assert 'import * as swarm from "./swarm.js";' in app and '["swarm", "Swarm", swarm]' in app
    assert (STATIC / "swarm.js").is_file() and (STATIC / "swarm.css").is_file()
    assert "style-src 'self'" in CSP and "script-src 'self'" in CSP and "default-src 'none'" in CSP


# ------------------------------------------------------------------ node harness
PRELUDE = r'''
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
process.on("unhandledRejection", (e) => { console.error("UNHANDLED", e); process.exit(3); });
const FORBIDDEN = new Set(["innerHTML", "outerHTML"]);
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.listeners = {}; this.parent = null;
    this.className = ""; this.value = ""; this.checked = false; this.disabled = false; this.text = null; this.style = {}; }
  set innerHTML(v) { throw new Error("innerHTML used"); }
  set outerHTML(v) { throw new Error("outerHTML used"); }
  insertAdjacentHTML() { throw new Error("insertAdjacentHTML used"); }
  setAttribute(k, v) { if (k === "style" || k.toLowerCase().startsWith("on")) throw new Error("forbidden attr " + k); this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(k, f) { (this.listeners[k] ||= []).push(f); }
  appendChild(c) { if (c.parent) c.parent.children = c.parent.children.filter((x) => x !== c); this.children.push(c); c.parent = this; return c; }
  replaceChildren(...xs) { for (const c of this.children) c.parent = null; this.children = []; xs.forEach((x) => this.appendChild(x)); }
  set textContent(v) { this.replaceChildren(Object.assign(new El("#text"), { text: String(v) })); }
  get textContent() { return this.text !== null ? this.text : this.children.map((x) => x.textContent).join(""); }
}
globalThis.document = { createElement: (t) => new El(t), createTextNode: (t) => Object.assign(new El("#text"), { text: String(t) }) };
globalThis.sessionStorage = { getItem: (k) => (k === "xc-token" ? "tok" : null) };
const walk = (x) => [x, ...x.children.flatMap(walk)];
const all = (root, pred) => walk(root).filter(pred);
const byClass = (root, c) => all(root, (e) => e.className.split(" ").includes(c));
const byTag = (root, t) => all(root, (e) => e.tagName === t.toUpperCase());
const fire = (el, type, extra = {}) => { const e = { target: el, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
  for (const f of el.listeners[type] || []) f(e); return e; };
const flush = () => new Promise((r) => setTimeout(r, 4));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const net = { calls: [] };
globalThis.fetch = (path, opt) => new Promise((resolve, reject) => {
  net.calls.push({ path, method: opt.method, headers: opt.headers, body: opt.body === undefined ? undefined : JSON.parse(opt.body), done: false,
    reply(status, body) { this.done = true; resolve({ ok: status >= 200 && status < 300, status, json: async () => body }); },
    fail(err) { this.done = true; reject(err || new TypeError("Failed to fetch")); } });
});
const open_ = (re) => net.calls.filter((c) => !c.done && re.test(c.path));
const posts = () => net.calls.filter((c) => c.method === "POST");
async function answer(re, status, body) { await flush(); const c = open_(re)[0]; assert.ok(c, "no pending call for " + re); c.reply(status, body); await flush(); return c; }

const G = (id, members = ["lead", "one"], extra = {}) => ({ id, owner: "kai", mode: "socratic", conductor: null, created: "2026-01-01T00:00:00Z",
  members: members.map((p, i) => ({ profile: p, session: "s_" + i })), ...extra });
const M = (seq, text = "t" + seq, extra = {}) => ({ seq, id: "m_" + seq, sender: "kai", sender_kind: "human", audience: ["lead", "one"], text, created: "2026-01-01T00:00:00Z", ...extra });
const page = (msgs, next) => ({ messages: msgs, next_after: next === undefined ? (msgs.length ? msgs[msgs.length - 1].seq : 0) : next });

const { mount } = await import("./swarm.mjs");
const item = (root, id) => byClass(root, "xc-sw-item").find((b) => b.textContent.includes(id));
const click = (el) => { if (!el.disabled) fire(el, "click"); };
const msgsShown = (root) => byClass(root, "xc-sw-seq").map((e) => e.textContent);
const area = (root) => byTag(root, "textarea")[0];
const sendBtn = (root) => byClass(root, "xc-primary")[0];
const type = (root, v) => { area(root).value = v; fire(area(root), "input"); };
const clickSend = (root) => { const b = sendBtn(root); if (b.disabled) return false; fire(byTag(root, "form")[0], "submit"); return true; };
const forceSubmit = (root) => fire(byTag(root, "form")[0], "submit");
const tick = (root, who) => { const cb = byTag(root, "input").find((i) => i.value === who); cb.checked = !cb.checked; fire(cb, "change"); };
const result = (root) => byClass(root, "xc-sw-result")[0].textContent;
async function boot(groups = [G("g_1"), G("g_2")]) {
  const root = new El("div"); const unmount = mount(root);
  await answer(/^\/api\/swarm\/groups$/, 200, { groups });
  return { root, unmount };
}
async function openGroup(ctx, id, msgs = [], members) {
  click(item(ctx.root, id));
  await answer(new RegExp("^/api/swarm/groups/" + id + "$"), 200, G(id, members));
  await answer(new RegExp("^/api/swarm/groups/" + id + "/messages"), 200, page(msgs));
}
'''


def run_node(tmp_path, body, name="t"):
    (tmp_path / "dom.mjs").write_text(text("dom.js"), encoding="utf-8")
    (tmp_path / "swarm.mjs").write_text(text("swarm.js").replace('"./dom.js"', '"./dom.mjs"'), encoding="utf-8")
    (tmp_path / (name + ".mjs")).write_text(PRELUDE + body + "\nconsole.log('OK');\n", encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / (name + ".mjs"))], capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@needs_node
def test_mount_select_filter_and_requests(tmp_path):
    run_node(tmp_path, r'''
const root = new El("div"); const unmount = mount(root);
assert.equal(typeof unmount, "function");
await flush();
assert.equal(net.calls.length, 1);
assert.deepEqual([net.calls[0].method, net.calls[0].path, net.calls[0].headers["X-Exo-Token"]], ["GET", "/api/swarm/groups", "tok"]);
assert.match(root.textContent, /Loading groups/);
await answer(/groups$/, 200, { groups: [G("g_1"), G("../x"), G("g_ok", ["bad profile<"]), G("g_2", ["a"]), { id: "g_3" }] });
assert.deepEqual(byClass(root, "xc-sw-id").map((e) => e.textContent), ["g_1", "g_2"]);       // malformed rows dropped
assert.match(root.textContent, /Select a group/);
click(item(root, "g_1")); await flush();
assert.equal(net.calls.at(-1).path, "/api/swarm/groups/g_1");
assert.match(root.textContent, /Loading group/);
await answer(/g_1$/, 200, G("g_1"));
assert.equal(net.calls.at(-1).path, "/api/swarm/groups/g_1/messages?after=0&limit=100");
assert.equal(net.calls.at(-1).headers["X-Exo-Token"], "tok");
await answer(/messages/, 200, page([M(1, "alpha"), M(2, "beta")]));
assert.deepEqual(msgsShown(root), ["#1", "#2"]);
assert.deepEqual(byClass(root, "xc-sw-member").map((e) => e.textContent), ["lead", "one"]);
assert.equal(item(root, "g_1").getAttribute("aria-pressed"), "true");
const n = net.calls.length; click(item(root, "g_1")); await flush(); assert.equal(net.calls.length, n);      // same group: no refetch
const sel = byTag(root, "select")[0];
sel.value = "lead"; fire(sel, "change"); await flush();
assert.equal(net.calls.at(-1).path, "/api/swarm/groups/g_1/messages?after=0&limit=100&member=lead");
assert.deepEqual(msgsShown(root), []);                                                                        // filter restarts the transcript
await answer(/member=lead/, 200, page([M(7, "only lead")]));
assert.deepEqual(msgsShown(root), ["#7"]);
sel.value = ""; fire(sel, "change"); await flush();
assert.equal(net.calls.at(-1).path, "/api/swarm/groups/g_1/messages?after=0&limit=100");
await answer(/limit=100$/, 200, page([M(1), M(2)]));
assert.deepEqual(msgsShown(root), ["#1", "#2"]);
assert.ok(!net.calls.some((c) => c.path.includes("..") || c.path.includes("g_3")));
assert.equal(posts().length, 0);
unmount();
''')


@needs_node
def test_stale_inspect_messages_and_unmount_are_discarded(tmp_path):
    run_node(tmp_path, r'''
// two selections: the late reply for the first must not paint over the second, nor start its transcript
let ctx = await boot();
click(item(ctx.root, "g_1")); await flush();
click(item(ctx.root, "g_2")); await flush();
const a = open_(/g_1$/)[0], b = open_(/g_2$/)[0];
b.reply(200, G("g_2")); await flush();
a.reply(200, G("g_1")); await flush();
assert.equal(byClass(ctx.root, "xc-sw-gname")[0].textContent, "g_2");
assert.equal(net.calls.filter((c) => /g_1\/messages/.test(c.path)).length, 0);
await answer(/g_2\/messages/, 200, page([M(1, "from g2")]));
assert.match(ctx.root.textContent, /from g2/);
ctx.unmount();

// two filter changes: only the newest request may fill the log, in either arrival order
for (const order of ["newest-first", "oldest-first"]) {
  net.calls.length = 0;
  ctx = await boot(); await openGroup(ctx, "g_1", [M(1, "base")]);
  const sel = byTag(ctx.root, "select")[0];
  sel.value = "lead"; fire(sel, "change"); await flush();
  const first = open_(/member=lead/)[0];
  const sel2 = byTag(ctx.root, "select")[0];
  sel2.value = "one"; fire(sel2, "change"); await flush();
  const second = open_(/member=one/)[0];
  assert.ok(first && second && first !== second);
  if (order === "newest-first") { second.reply(200, page([M(5, "RIGHT")])); await flush(); first.reply(200, page([M(9, "STALE")])); }
  else { first.reply(200, page([M(9, "STALE")])); await flush(); second.reply(200, page([M(5, "RIGHT")])); }
  await flush();
  assert.ok(ctx.root.textContent.includes("RIGHT") && !ctx.root.textContent.includes("STALE"), order);
  assert.deepEqual(msgsShown(ctx.root), ["#5"]);
  ctx.unmount();
}

// unmount: pending list, inspect, messages and send replies do nothing and start nothing
net.calls.length = 0;
const root = new El("div"); const un = mount(root); await flush();
un(); open_(/groups$/)[0].reply(200, { groups: [G("g_1")] }); await flush();
assert.equal(byClass(root, "xc-sw-item").length, 0); assert.equal(net.calls.length, 1);
net.calls.length = 0;
ctx = await boot(); click(item(ctx.root, "g_1")); await flush();
ctx.unmount(); open_(/g_1$/)[0].reply(200, G("g_1")); await flush();
assert.equal(net.calls.length, 2);                                   // list + inspect only: no messages request after unmount
net.calls.length = 0;
ctx = await boot(); await openGroup(ctx, "g_1", [M(1)]);
type(ctx.root, "hello"); assert.ok(clickSend(ctx.root)); await flush();
const before = ctx.root.textContent, n = net.calls.length;
ctx.unmount(); posts()[0].reply(200, { message: M(2, "hello") }); await flush();
assert.equal(net.calls.length, n);                                  // no refresh after a send that returned post-unmount
assert.equal(ctx.root.textContent.includes("Saved to the group log"), false);
// a late messages reply after unmount
net.calls.length = 0;
ctx = await boot(); click(item(ctx.root, "g_1")); await answer(/g_1$/, 200, G("g_1"));
const pend = open_(/messages/)[0]; ctx.unmount(); pend.reply(200, page([M(1, "late")])); await flush();
assert.ok(!ctx.root.textContent.includes("late"));
''')


@needs_node
def test_empty_page_keeps_cursor_and_messages_are_deduped(tmp_path):
    run_node(tmp_path, r'''
const ctx = await boot(); await openGroup(ctx, "g_1", [M(1), M(2)]);
const refresh = () => byClass(ctx.root, "xc-btn").find((b) => b.textContent === "Refresh");
const last = () => net.calls.at(-1).path;
click(refresh()); await flush();
assert.equal(last(), "/api/swarm/groups/g_1/messages?after=2&limit=100");
await answer(/after=2/, 200, { messages: [], next_after: 0 });       // the real core's empty page
assert.deepEqual(msgsShown(ctx.root), ["#1", "#2"]);
click(refresh()); await flush();
assert.equal(last(), "/api/swarm/groups/g_1/messages?after=2&limit=100", "cursor must not rewind to 0");
await answer(/after=2/, 200, { messages: [M(2, "dup"), M(3, "c"), M(3, "dup in page"), M(1, "old")], next_after: 3 });
assert.deepEqual(msgsShown(ctx.root), ["#1", "#2", "#3"]);            // seq <= cursor and repeats are dropped
assert.ok(!ctx.root.textContent.includes("dup") && !ctx.root.textContent.includes("old"));
click(refresh()); await flush();
assert.equal(last(), "/api/swarm/groups/g_1/messages?after=3&limit=100");
await answer(/after=3/, 200, { messages: [M(4), M(5)], next_after: 5 });
assert.deepEqual(msgsShown(ctx.root), ["#1", "#2", "#3", "#4", "#5"]);
click(refresh()); await flush();
assert.match(last(), /after=5&limit=100$/);
await answer(/after=5/, 200, { messages: [] });                        // next_after missing
click(refresh()); await flush();
assert.match(last(), /after=5&limit=100$/);
await answer(/after=5/, 200, { messages: [M(6, "x", { text: 5 }), { seq: "7" }, null] , next_after: 6 });
assert.deepEqual(msgsShown(ctx.root), ["#1", "#2", "#3", "#4", "#5"]);         // malformed rows ignored
// a refresh while one is in flight is not doubled; an error keeps the transcript and the cursor
click(refresh()); await flush(); const n = net.calls.length; fire(refresh(), "click"); await flush(); assert.equal(net.calls.length, n);
await answer(/after=/, 500, { error: "boom" });
assert.match(byClass(ctx.root, "xc-error")[0].textContent, /boom/);
assert.deepEqual(msgsShown(ctx.root), ["#1", "#2", "#3", "#4", "#5"]);
click(refresh()); await flush(); assert.match(last(), /after=5&limit=100$/);
await answer(/after=5/, 200, page([]));
// full page notice, and the 500 message window keeps the cursor
const seqs = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => M(a + i));
let from = 6;
for (let i = 0; i < 6; i++) {
  click(refresh()); await flush();
  await answer(/messages/, 200, page(seqs(from, from + 99))); from += 100;
}
assert.equal(msgsShown(ctx.root).length, 500);
assert.match(ctx.root.textContent, /newest 500 messages/); assert.match(ctx.root.textContent, /last page was full/);
click(refresh()); await flush();
assert.match(last(), new RegExp("after=" + (from - 1) + "&limit=100$"));
''')


@needs_node
def test_pending_send_suppresses_duplicates_across_clicks_enter_and_groups(tmp_path):
    run_node(tmp_path, r'''
const ctx = await boot(); await openGroup(ctx, "g_1", [M(1)]);
type(ctx.root, "hello");
assert.ok(clickSend(ctx.root)); await flush();
assert.equal(clickSend(ctx.root), false);                              // button disabled while pending
assert.equal(sendBtn(ctx.root).textContent, "Sending...");
forceSubmit(ctx.root); forceSubmit(ctx.root); await flush();            // Enter / script submit still guarded in code
assert.equal(posts().length, 1);
// another group cannot send while one is pending (one global pending flag)
await openGroup(ctx, "g_2", []);
type(ctx.root, "other"); assert.equal(clickSend(ctx.root), false); forceSubmit(ctx.root); await flush();
assert.equal(posts().length, 1);
assert.match(ctx.root.textContent, /not sent twice/);
posts()[0].reply(200, { message: M(2, "hello"), persisted: true }); await flush();
assert.equal(posts().length, 1);
await wait(30); assert.equal(posts().length, 1);
type(ctx.root, "other");                                              // (see test_send_button_resyncs_in_other_group_...)
assert.equal(sendBtn(ctx.root).disabled, false);                        // g_2 draft can now go, once
assert.ok(clickSend(ctx.root)); await flush(); assert.equal(posts().length, 2);
assert.equal(posts()[1].path, "/api/swarm/groups/g_2/send");
// the one refresh after a saved send happens for the group in view
posts()[1].reply(200, { message: M(3, "other") }); await flush();
assert.match(net.calls.at(-1).path, /g_2\/messages\?after=0/);
ctx.unmount();
''')


@needs_node
def test_send_button_resyncs_in_other_group_when_pending_send_finishes(tmp_path):
    run_node(tmp_path, r'''
const ctx = await boot(); await openGroup(ctx, "g_1", [M(1)]);
type(ctx.root, "hello"); assert.ok(clickSend(ctx.root)); await flush();
await openGroup(ctx, "g_2", []);
type(ctx.root, "other");
posts()[0].reply(200, { message: M(2, "hello"), persisted: true }); await flush(); await wait(30);
assert.equal(sendBtn(ctx.root).disabled, false);
assert.ok(!/in progress/.test(ctx.root.textContent));
ctx.unmount();
''')


@needs_node
def test_drafts_survive_switching_failure_and_edits_during_send(tmp_path):
    run_node(tmp_path, r'''
const ctx = await boot(); await openGroup(ctx, "g_1", []);
type(ctx.root, "draft one\nline 2");
await openGroup(ctx, "g_2", []);
assert.equal(area(ctx.root).value, "");
type(ctx.root, "draft two");
click(item(ctx.root, "g_1")); await answer(/g_1$/, 200, G("g_1")); await answer(/g_1\/messages/, 200, page([]));
assert.equal(area(ctx.root).value, "draft one\nline 2");
click(item(ctx.root, "g_2")); await answer(/g_2$/, 200, G("g_2")); await answer(/g_2\/messages/, 200, page([]));
assert.equal(area(ctx.root).value, "draft two");
// a refused send keeps the draft and says it was not sent
assert.ok(clickSend(ctx.root)); await flush();
posts()[0].reply(400, { error: "<b>The swarm core rejected the request</b>", code: "bad_request" }); await flush();
assert.equal(area(ctx.root).value, "draft two");
assert.match(result(ctx.root), /Not sent: .*Your draft is kept/); assert.equal(sendBtn(ctx.root).textContent, "Send");
assert.equal(byTag(ctx.root, "b").length, 0);
// an edit made while a send is in flight survives a saved reply
type(ctx.root, "one"); assert.ok(clickSend(ctx.root)); await flush();
type(ctx.root, "one and more");
posts()[1].reply(200, { message: M(1, "one") }); await flush();
assert.equal(area(ctx.root).value, "one and more");
click(item(ctx.root, "g_1")); await answer(/g_1$/, 200, G("g_1")); await answer(/g_1\/messages/, 200, page([]));
click(item(ctx.root, "g_2")); await answer(/g_2$/, 200, G("g_2")); await answer(/g_2\/messages/, 200, page([]));
assert.equal(area(ctx.root).value, "one and more", "edited draft kept in memory after switching");
// an unedited draft is cleared by a saved reply and stays cleared when the group is reopened
assert.ok(clickSend(ctx.root)); await flush();
posts()[2].reply(200, { message: M(2, "one and more") }); await flush();
assert.equal(area(ctx.root).value, ""); assert.match(result(ctx.root), /Saved to the group log/);
click(item(ctx.root, "g_1")); await answer(/g_1$/, 200, G("g_1")); await answer(/g_1\/messages/, 200, page([]));
click(item(ctx.root, "g_2")); await answer(/g_2$/, 200, G("g_2")); await answer(/g_2\/messages/, 200, page([]));
assert.equal(area(ctx.root).value, "");
assert.equal(byClass(ctx.root, "xc-sw-count")[0].textContent, "0 / 50000");
ctx.unmount();
''')


@needs_node
@pytest.mark.parametrize("kind,expect_unknown", [("network", True), (500, True), (502, True), (504, True), (404, True), (409, True),
                                                 (401, True), (400, False), (403, False), (501, False)])
def test_send_error_ambiguity_and_no_automatic_retry(tmp_path, kind, expect_unknown):
    run_node(tmp_path, r'''
const kind = %s, expectUnknown = %s;
const ctx = await boot(); await openGroup(ctx, "g_1", [M(1)]);
type(ctx.root, "important");
assert.ok(clickSend(ctx.root)); await flush();
if (kind === "network") posts()[0].fail(new TypeError("Failed to fetch")); else posts()[0].reply(kind, { error: "x <script>y</script>", code: "c", persisted: "unknown" });
await flush();
const r = result(ctx.root);
assert.equal(area(ctx.root).value, "important");                         // draft kept for every failure
if (expectUnknown) {
  assert.match(r, /may or may not be saved/); assert.match(r, /check the group log before sending again/);
  assert.match(r, /nothing is resent automatically/); assert.equal(sendBtn(ctx.root).textContent, "Send again");
  assert.doesNotMatch(r, /^Not sent/);
  if (kind === "network") assert.match(r, /network failure/); else assert.match(r, new RegExp("HTTP " + kind));
} else {
  assert.match(r, /^Not sent: /); assert.doesNotMatch(r, /may or may not/); assert.equal(sendBtn(ctx.root).textContent, "Send");
}
assert.equal(byTag(ctx.root, "script").length, 0);
await wait(60); await flush();
assert.equal(posts().length, 1, "no automatic retry");
assert.equal(net.calls.filter((c) => c.method === "POST").length, 1);
assert.equal(sendBtn(ctx.root).disabled, false);                         // the person may decide to send again, explicitly
assert.ok(clickSend(ctx.root)); await flush(); assert.equal(posts().length, 2);
ctx.unmount();
''' % (json.dumps(kind), json.dumps(expect_unknown)), name="amb_%s" % kind)


@needs_node
def test_untrusted_text_is_only_ever_text(tmp_path):
    run_node(tmp_path, r'''
const EVIL = '<img src=x onerror=alert(1)><script>alert(2)</script>"><svg/onload=1>';
const OK_TAGS = new Set(["DIV", "H1", "H2", "P", "HEADER", "ASIDE", "SECTION", "BUTTON", "SPAN", "UL", "LI", "SELECT", "OPTION", "LABEL",
  "INPUT", "TEXTAREA", "FORM", "FIELDSET", "LEGEND", "ARTICLE", "TIME", "#TEXT"]);
const root = new El("div"); const un = mount(root); await flush();
await answer(/groups$/, 200, { groups: [G("g_1", ["lead"], { mode: EVIL })] });
click(item(root, "g_1"));
await answer(/g_1$/, 200, G("g_1", ["lead", "one"], { mode: EVIL, conductor: EVIL, created: EVIL }));
await answer(/messages/, 200, page([M(1, EVIL, { sender: EVIL, sender_kind: EVIL, audience: [EVIL, "<b>x</b>"], created: EVIL }), M(2, "plain")]));
assert.ok(root.textContent.includes(EVIL) || root.textContent.includes("<img src=x"), "text is shown literally");
for (const e of walk(root)) assert.ok(OK_TAGS.has(e.tagName), "unexpected element " + e.tagName);
for (const e of walk(root)) for (const k of Object.keys(e.attrs)) assert.ok(!k.startsWith("on") && k !== "style" && k !== "src" && k !== "href", k);
assert.equal(byClass(root, "xc-sw-text")[0].children.length, 1);
assert.equal(byClass(root, "xc-sw-text")[0].children[0].tagName, "#TEXT");
// server error text (e.g. an echoed value) is a text node too, and is clipped
click(item(root, "g_1")); // already selected: no-op
type(root, "hi"); assert.ok(clickSend(root)); await flush();
posts()[0].reply(400, { error: EVIL + "z".repeat(1000), code: "bad_request" }); await flush();
assert.ok(result(root).includes("<img src=x") && result(root).length < 450);
for (const e of walk(root)) assert.ok(OK_TAGS.has(e.tagName), e.tagName);
// inspect failure shows the server text, as text
const r2 = new El("div"); const un2 = mount(r2); await flush();
await answer(/groups$/, 200, { groups: [G("g_9")] }); click(item(r2, "g_9"));
await answer(/g_9$/, 500, { error: EVIL }); assert.ok(r2.textContent.includes("<script>"));
assert.equal(byTag(r2, "script").length, 0); assert.equal(byClass(r2, "xc-error")[0].getAttribute("role"), "alert");
// a reply for a different group id than asked is rejected
click(item(r2, "g_9"));
await answer(/g_9$/, 200, G("g_other")); assert.equal(byTag(r2, "textarea").length, 0); assert.match(byClass(r2, "xc-error")[0].textContent, /could not be read/);
un(); un2();
''')


@needs_node
def test_audience_payload_member_order_omitted_when_none_and_text_limits(tmp_path):
    run_node(tmp_path, r'''
const ctx = await boot([G("g_1", ["lead", "one", "two"])]); await openGroup(ctx, "g_1", [], ["lead", "one", "two"]);
async function sendNow(t) { type(ctx.root, t); assert.ok(clickSend(ctx.root)); await flush(); const p = posts().at(-1);
  p.reply(200, { message: M(9, t) }); await flush(); await answer(/messages/, 200, page([])); return p; }
let p = await sendNow("  keep \n spaces  ");
assert.equal(p.path, "/api/swarm/groups/g_1/send"); assert.equal(p.method, "POST");
assert.equal(p.headers["Content-Type"], "application/json"); assert.equal(p.headers["X-Exo-Token"], "tok");
assert.deepEqual(p.body, { text: "  keep \n spaces  " }); assert.deepEqual(Object.keys(p.body), ["text"]);   // nothing ticked: no audience key
tick(ctx.root, "two"); tick(ctx.root, "lead");                                                           // ticked out of order
p = await sendNow("a"); assert.deepEqual(p.body, { text: "a", audience: ["lead", "two"] });
tick(ctx.root, "lead"); p = await sendNow("b"); assert.deepEqual(p.body, { text: "b", audience: ["two"] });
tick(ctx.root, "two"); p = await sendNow("c"); assert.deepEqual(Object.keys(p.body), ["text"]);
for (const who of ["lead", "one", "two"]) tick(ctx.root, who);
p = await sendNow("all"); assert.deepEqual(p.body.audience, ["lead", "one", "two"]);
// audience choice is per group and survives a switch
// blocked inputs never reach the network
const n = posts().length;
for (const bad of ["", "   \n\t", "x\u0000y", "x".repeat(50001)]) { type(ctx.root, bad); assert.equal(clickSend(ctx.root), false); forceSubmit(ctx.root); await flush(); }
assert.equal(posts().length, n);
p = await sendNow("y".repeat(50000)); assert.equal(p.body.text.length, 50000);
assert.equal(area(ctx.root).getAttribute("maxlength"), "50000");
ctx.unmount();
''')


# ------------------------------------------------------------------ API shapes: real swarm_api in both directions
def api(centre, method, path, q=None, body=None):
    return asyncio.run(sw.dispatch(centre, method, path, q or {}, body or {}))


@needs_node
def test_ui_accepts_real_api_replies_and_its_requests_are_accepted_by_the_api(tmp_path):
    base = "/api/swarm/groups"
    sent = msg(9, "hello", audience=("lead",))
    fixtures = {
        "list": api(Centre({"groups": [GROUP]}), "GET", base)[1],
        "inspect": api(Centre(GROUP), "GET", base + "/" + GID)[1],
        "observe": api(Centre({"messages": [msg(1, "first"), msg(2, "second", kind="human")], "next_after": 2}),
                       "GET", base + "/" + GID + "/messages")[1],
        "send": api(Centre(sent), "POST", base + "/" + GID + "/send", None, {"text": "hello", "audience": ["lead"]})[1],
        "refused": {code: api(Centre(fail=SourceError(code, "secret")), "POST", base + "/" + GID + "/send", None, {"text": "hello"})
                    for code in ("forbidden", "bad_request", "not_found", "conflict")},
        "unavailable": api(Centre(fail=RuntimeError("x")), "POST", base + "/" + GID + "/send", None, {"text": "hello"}),
        "badreply": api(Centre({"nope": 1}), "POST", base + "/" + GID + "/send", None, {"text": "hello"}),
    }
    assert fixtures["list"]["groups"][0]["id"] == GID and fixtures["send"]["persisted"] is True
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps(fixtures))
    out = run_node(tmp_path, r'''
const fx = JSON.parse(readFileSync("fx.json", "utf8"));
const ID = fx.list.groups[0].id;
const rec = [];
const ctx = await boot([]); ctx.unmount();
net.calls.length = 0;
const root = new El("div"); const un = mount(root); await flush();
net.calls[0].reply(200, fx.list); await flush();
assert.equal(byClass(root, "xc-sw-id")[0].textContent, ID);
assert.match(byClass(root, "xc-sw-meta")[0].textContent, /socratic · 2 members/);
click(item(root, ID)); await answer(new RegExp(ID + "$"), 200, fx.inspect);
assert.match(root.textContent, /created \d/);
await answer(/messages/, 200, fx.observe);
assert.deepEqual(msgsShown(root), ["#1", "#2"]); assert.ok(root.textContent.includes("first") && root.textContent.includes("second"));
assert.ok(root.textContent.includes("to lead, one"));
tick(root, "lead"); type(root, "hello"); assert.ok(clickSend(root)); await flush();
posts()[0].reply(200, fx.send); await flush();
assert.match(result(root), /Saved to the group log/); assert.equal(area(root).value, "");
assert.match(result(root), /not known/);                                  // processed/woken are "unknown" in the reply
// observe after send: the next cursor is 2 -> real reply for after=2 is an empty page with next_after 0
await answer(/messages/, 200, fx.observe);
// real error replies
for (const [code, r] of Object.entries(fx.refused)) {
  type(root, "again"); assert.ok(clickSend(root)); await flush();
  posts().at(-1).reply(r[0], r[1]); await flush();
  const t = result(root);
  if (r[0] === 409) assert.match(t, /may or may not/); else assert.match(t, /^Not sent: /, code + " " + r[0]);
  assert.ok(!t.includes("secret"));
}
for (const r of [fx.unavailable, fx.badreply]) {
  assert.equal(r[0], 502);
  type(root, "again"); assert.ok(clickSend(root)); await flush();
  posts().at(-1).reply(r[0], r[1]); await flush();
  assert.match(result(root), /may or may not be saved/); assert.equal(sendBtn(root).textContent, "Send again");
}
writeFileSync("req.json", JSON.stringify(net.calls.map((c) => ({ method: c.method, path: c.path, body: c.body }))));
un();
''', name="shapes")
    assert out.strip() == "OK"
    reqs = json.loads((tmp_path / "req.json").read_text())
    assert reqs[0]["path"] == base
    seen = set()
    for r in reqs:
        u = urlsplit(r["path"])
        q = parse_qs(u.query, keep_blank_values=True)
        if r["method"] == "POST":
            c = Centre(msg(9, r.get("body")["text"], audience=tuple(r.get("body").get("audience") or ("lead", "one"))))
        elif u.path.endswith("/messages"):
            c = Centre({"messages": [], "next_after": 0})
        elif u.path == base:
            c = Centre({"groups": [GROUP]})
        else:
            c = Centre(GROUP)
        status, data = api(c, r["method"], u.path, q, r.get("body") or {})
        assert status == 200, (r, status, data)
        seen.add((r["method"], re.sub(GID, "<id>", u.path)))
        assert len(c.calls) == 1 and isinstance(c.calls[0], s.CommandRun) and c.calls[0].name == "group"
        if r["method"] == "POST":
            assert set(r.get("body")) <= {"text", "audience"}
    assert seen == {("GET", base), ("GET", base + "/<id>"), ("GET", base + "/<id>/messages"), ("POST", base + "/<id>/send")}
    obs = [r for r in reqs if "/messages" in r["path"]]
    assert obs[0]["path"].endswith("/messages?after=0&limit=100")
    assert any("after=2&limit=100" in r["path"] for r in obs)
