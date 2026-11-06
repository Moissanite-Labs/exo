// Swarm view (bounded slice): group list, one inspected group, its transcript and a send composer.
// Only h() for DOM (text nodes, never markup) and api() for requests; paths are fixed and the one dynamic part, a group
// id, comes from the server's own list and is checked against the API's id shape. No polling: the transcript moves only
// on selection, filter change, an explicit Refresh, or once after a send that came back saved.
// Send means saved to the group log only. Whether a member processed it or was woken is never known here.
// Controls (Check status, Pause, Resume, Stop) are explicit too: nothing reads status by itself. A 200 on a control means
// the request was accepted, not that members changed state. An unclear result locks the group's controls until a status
// read succeeds. Nothing is retried.
import { h, api } from "./dom.js";

const MAX_TEXT = 50000;        // swarm_api.MAX_TEXT, counted in UTF-16 units here (never fewer than code points)
const MAX_AUDIENCE = 64;       // swarm_api.MAX_AUDIENCE
const PAGE = 100;              // the API's default page; also how a full page is recognised
const MAX_SHOWN = 500;         // newest messages kept in the page; older ones are dropped from the view
const GROUP_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
const PROFILE = /^[A-Za-z][A-Za-z0-9_.@-]{0,63}$/;
const BASE = "/api/swarm/groups";
// A refusal before anything was stored. Every other failure (502, network, odd status) may have been saved.
const NOT_SAVED = [400, 403, 501];
// A control refused by the API itself (swarm_api.REFUSALS). Every other failure leaves the outcome unknown.
const REFUSED = [400, 403, 409, 501];
const OPS = { pause: "Pause", resume: "Resume", stop: "Stop" };     // the only control paths ever built
const STATES = ["idle", "queued", "running", "pausing", "paused", "completed", "stopped", "failed", "unknown"];   // swarm_api.STATES
const WHOLE = "@group";                                            // select value for the whole group. It is never a valid profile

const clip = (s, n) => { s = s == null ? "" : String(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
const when = (iso) => { const t = Date.parse(iso || ""); return isNaN(t) ? clip(iso, 32) : new Date(t).toLocaleString(); };
const goodGroup = (g) => !!g && typeof g.id === "string" && GROUP_ID.test(g.id) && Array.isArray(g.members) &&
  g.members.every((m) => m && typeof m.profile === "string" && PROFILE.test(m.profile));
const goodMessage = (m) => !!m && Number.isSafeInteger(m.seq) && m.seq > 0 && typeof m.text === "string" &&
  typeof m.sender === "string" && Array.isArray(m.audience);
const plain = (s, n) => clip(String(s == null ? "" : s).replace(/[\u0000-\u001f\u007f-\u009f]+/g, " ").trim(), n);
const goodStatus = (d, id) => !!d && d.id === id && Array.isArray(d.members) && d.members.length > 0 &&
  d.members.every((m) => m && typeof m.profile === "string" && PROFILE.test(m.profile) && STATES.indexOf(m.state) >= 0) &&
  new Set(d.members.map((m) => m.profile)).size === d.members.length &&
  typeof d.local_runtime === "boolean" && typeof d.reconciliation_required === "boolean";
const failText = (e, what) => e && e.status ? clip(e.message, 300) : what + " The server could not be reached.";

export function mount(root) {
  let alive = true;
  let listGen = 0, selGen = 0;          // bumped to void any request still in flight for an older list or selection
  let groups = [], listErr = "", listLoading = false;
  let sel = null, selErr = "", inspecting = false;     // sel: the inspected group (members come from inspect)
  let member = "";                                     // transcript filter: "" = everyone
  let msgs = [], seen = new Set(), cursor = 0, full = false, loadErr = "", loadingGen = -1, dropped = false;
  let sending = false;
  const drafts = new Map();             // group id -> draft text, in memory only
  const audiences = new Map();          // group id -> Set of profile ids ticked
  const results = new Map();            // group id -> {cls, text} from the last send
  let ui = null;                        // the composer's live elements while a group is shown
  // Controls. ctlBusy and statBusy are single global flags (one request of each kind at a time, whatever group is shown).
  let ctlBusy = false, statBusy = false;
  let statGen = 0;                      // bumped when the group changes or on unmount: voids a status read in flight
  let ctlEpoch = 0;                     // bumped at the start and end of every control request
  let target = "";                      // "" = none chosen, WHOLE, or a member profile of the shown group
  let confirming = null;                // {id, target} while Stop waits for Confirm stop or Cancel
  let status = null, statusErr = "";    // the last good status read for the shown group, and the last status failure
  const uncertain = new Set();          // group ids whose last control result is unknown: controls stay off until a status read
  const ctlResults = new Map();         // group id -> {cls, text} from the last control request
  let cui = null;                       // the control panel's live elements while a group is shown

  const listEl = h("div", { class: "xc-sw-list" });
  const headEl = h("div", { class: "xc-sw-head" });
  const filterEl = h("div", { class: "xc-sw-filter" });
  const noteEl = h("div", { class: "xc-sw-notes", role: "status" });
  const logEl = h("div", { class: "xc-sw-log", role: "log", "aria-label": "Group messages", tabindex: "0" });
  const ctlEl = h("section", { class: "xc-sw-ctl", "aria-label": "Group controls" });
  const sendEl = h("div", { class: "xc-sw-send" });
  const refreshGroups = h("button", { class: "xc-btn", type: "button", onclick: () => loadList() }, "Refresh groups");
  root.replaceChildren(h("div", { class: "xc-sw" },
    h("header", { class: "xc-sw-top" }, h("h1", { class: "xc-sw-title" }, "Swarm"),
      h("p", { class: "xc-sw-lede" }, "Groups of members this seat runs. Read the transcript and send a message to the group log.")),
    h("div", { class: "xc-sw-grid" },
      h("aside", { class: "xc-sw-side", "aria-label": "Groups" }, h("div", { class: "xc-sw-sidehead" }, h("h2", null, "Groups"), refreshGroups), listEl),
      h("section", { class: "xc-sw-main", "aria-label": "Selected group" }, headEl, filterEl, ctlEl, noteEl, logEl, sendEl))));

  // ------------------------------------------------------------ groups
  async function loadList() {
    const gen = ++listGen;
    listLoading = true; listErr = ""; drawList();
    try {
      const d = await api("GET", BASE);
      if (!alive || gen !== listGen) return;
      groups = ((d && d.groups) || []).filter(goodGroup);
    } catch (e) {
      if (!alive || gen !== listGen) return;
      listErr = failText(e, "The group list could not be read.");
    }
    listLoading = false;
    drawList();
    if (sel && !groups.some((g) => g.id === sel.id)) { clearSel(); drawMain(); }
  }

  function drawList() {
    const rows = groups.map((g) => h("button", { class: "xc-sw-item" + (sel && sel.id === g.id ? " is-on" : ""), type: "button",
      "aria-pressed": sel && sel.id === g.id ? "true" : "false", onclick: () => select(g.id) },
      h("span", { class: "xc-sw-id" }, g.id),
      h("span", { class: "xc-sw-meta" }, clip(g.mode, 12) + " · " + g.members.length + " member" + (g.members.length === 1 ? "" : "s"))));
    listEl.replaceChildren(...(listErr ? [h("div", { class: "xc-error", role: "alert" }, listErr)] : []),
      ...(listLoading && !groups.length ? [h("div", { class: "xc-sw-empty" }, "Loading groups...")] : []),
      ...(!listLoading && !listErr && !groups.length ? [h("div", { class: "xc-sw-empty" }, "No groups yet.")] : []), ...rows);
    refreshGroups.disabled = listLoading;
  }

  function clearSel() {
    selGen++; statGen++; sel = null; selErr = ""; inspecting = false; member = "";
    target = ""; confirming = null; status = null; statusErr = ""; cui = null;
    msgs = []; seen = new Set(); cursor = 0; full = false; loadErr = ""; loadingGen = -1; dropped = false; ui = null;
  }

  async function select(id) {
    if (!GROUP_ID.test(id) || (sel && sel.id === id && !selErr)) return;
    clearSel();
    const gen = selGen;
    inspecting = true; sel = { id, members: [] };
    drawList(); drawMain();
    try {
      const g = await api("GET", BASE + "/" + encodeURIComponent(id));
      if (!alive || gen !== selGen) return;
      if (!goodGroup(g) || g.id !== id) throw new Error("The group reply was not understood.");
      sel = g;
    } catch (e) {
      if (!alive || gen !== selGen) return;
      selErr = failText(e, "The group could not be read.");
    }
    inspecting = false;
    drawMain();
    if (!selErr) loadMessages();
  }

  // ------------------------------------------------------------ transcript
  async function loadMessages() {
    if (!sel || selErr || inspecting) return;
    const gen = selGen, id = sel.id, who = member;
    if (loadingGen === gen) return;
    loadingGen = gen; loadErr = ""; drawLog();
    let path = BASE + "/" + encodeURIComponent(id) + "/messages?after=" + cursor + "&limit=" + PAGE;
    if (who) path += "&member=" + encodeURIComponent(who);
    try {
      const d = await api("GET", path);
      if (!alive || gen !== selGen) return;
      const page = Array.isArray(d && d.messages) ? d.messages : [];
      let fresh = 0;
      for (const m of page) {
        if (!goodMessage(m) || m.seq <= cursor || seen.has(m.seq)) continue;   // dedup, and never move backwards
        seen.add(m.seq); msgs.push(m); fresh++;
      }
      // An empty page reports next_after 0: that is "nothing new", not "rewind", so the cursor stays where it was.
      const nxt = d && Number.isSafeInteger(d.next_after) ? d.next_after : 0;
      if (fresh && nxt > cursor) cursor = nxt;
      else if (fresh) cursor = Math.max(cursor, msgs[msgs.length - 1].seq);
      full = page.length >= PAGE;
      if (msgs.length > MAX_SHOWN) {
        msgs = msgs.slice(-MAX_SHOWN); seen = new Set(msgs.map((m) => m.seq)); dropped = true;
      }
    } catch (e) {
      if (!alive || gen !== selGen) return;
      loadErr = failText(e, "Messages could not be read.");
    }
    loadingGen = -1;
    drawLog();
  }

  function reset(newMember) {
    confirming = null;
    selGen++; member = newMember; msgs = []; seen = new Set(); cursor = 0; full = false; loadErr = ""; loadingGen = -1; dropped = false;
    drawMain();
    loadMessages();
  }

  function msgEl(m) {
    const aud = m.audience.map((a) => clip(a, 64));
    return h("article", { class: "xc-sw-msg" + (m.sender_kind === "human" ? " is-human" : "") },
      h("header", { class: "xc-sw-msghead" },
        h("span", { class: "xc-sw-seq" }, "#" + m.seq),
        h("span", { class: "xc-sw-from" }, clip(m.sender, 64)),
        h("span", { class: "xc-sw-kind" }, clip(m.sender_kind, 12)),
        h("span", { class: "xc-sw-to" }, "to " + aud.join(", ")),
        h("time", { class: "xc-sw-when" }, when(m.created))),
      h("p", { class: "xc-sw-text" }, m.text));
  }

  function drawLog() {
    if (!sel || selErr) return;
    const busy = loadingGen === selGen;
    const kids = [];
    if (dropped) kids.push(h("div", { class: "xc-sw-notice" }, "Showing the newest " + MAX_SHOWN + " messages; earlier ones are hidden."));
    if (!msgs.length) kids.push(h("div", { class: "xc-sw-empty" }, busy ? "Loading messages..." : "No messages" + (member ? " for " + member : "") + " yet."));
    logEl.replaceChildren(...kids, ...msgs.map(msgEl));
    noteEl.replaceChildren(...(loadErr ? [h("div", { class: "xc-error", role: "alert" }, loadErr)] : []),
      ...(full && !busy ? [h("div", { class: "xc-sw-notice" }, "The last page was full. Refresh for more.")] : []));
    if (ui) ui.refresh.disabled = busy;
  }

  // ------------------------------------------------------------ main pane
  function drawMain() {
    if (!sel) {
      headEl.replaceChildren(h("div", { class: "xc-sw-empty" }, "Select a group."));
      filterEl.replaceChildren(); ctlEl.replaceChildren(); noteEl.replaceChildren(); logEl.replaceChildren(); sendEl.replaceChildren();
      return;
    }
    if (inspecting || selErr) {
      headEl.replaceChildren(h("h2", { class: "xc-sw-gname" }, sel.id));
      filterEl.replaceChildren(); ctlEl.replaceChildren(); sendEl.replaceChildren(); logEl.replaceChildren();
      noteEl.replaceChildren(selErr ? h("div", { class: "xc-error", role: "alert" }, selErr) : h("div", { class: "xc-sw-empty" }, "Loading group..."));
      return;
    }
    headEl.replaceChildren(h("h2", { class: "xc-sw-gname" }, sel.id),
      h("p", { class: "xc-sw-meta" }, clip(sel.mode, 12) + (sel.conductor ? " · conductor " + clip(sel.conductor, 64) : "") + " · created " + when(sel.created)),
      h("ul", { class: "xc-sw-members", "aria-label": "Members" }, sel.members.map((m) => h("li", { class: "xc-sw-member" }, clip(m.profile, 64)))));
    const pick = h("select", { class: "xc-field", "aria-label": "Filter transcript by member", onchange: (e) => reset(e.target.value) },
      h("option", { value: "", selected: !member }, "All members"),
      sel.members.map((m) => h("option", { value: m.profile, selected: m.profile === member }, m.profile)));
    const refresh = h("button", { class: "xc-btn", type: "button", onclick: () => loadMessages() }, "Refresh");
    filterEl.replaceChildren(h("label", { class: "xc-sw-lab" }, h("span", null, "Member"), pick), refresh);
    drawControls();
    drawCompose(refresh);
    drawLog();
  }

  // ------------------------------------------------------------ controls
  const targetLabel = (id, t) => t === WHOLE ? "the whole group " + id : "member " + t + " of group " + id;

  function drawControls() {
    if (!sel) return;
    const id = sel.id;
    const pick = h("select", { class: "xc-field", "aria-label": "Control target", onchange: (e) => setTarget(e.target.value) },
      h("option", { value: "", selected: !target }, "Choose a target"),
      h("option", { value: WHOLE, selected: target === WHOLE }, "Whole group"),
      sel.members.map((m) => h("option", { value: m.profile, selected: m.profile === target }, m.profile)));
    const check = h("button", { class: "xc-btn", type: "button", onclick: () => checkStatus() }, "Check status");
    const btn = (op) => h("button", { class: "xc-btn", type: "button", onclick: () => (op === "stop" ? askStop() : runControl(op)) }, OPS[op]);
    const pause = btn("pause"), resume = btn("resume"), stop = btn("stop");
    cui = { pick, check, pause, resume, stop,
      confirmEl: h("div", { class: "xc-sw-confirm", role: "group", "aria-label": "Confirm stop" }),
      hint: h("p", { class: "xc-sw-hint" }),
      out: h("div", { class: "xc-sw-ctlout", role: "status" }),
      stateEl: h("div", { class: "xc-sw-state", role: "status" }) };
    ctlEl.replaceChildren(h("h3", { class: "xc-sw-ctlhead" }, "Controls"),
      h("p", { class: "xc-sw-hint" }, "Nothing here refreshes by itself. Use Check status to read the current member states."),
      h("div", { class: "xc-sw-ctlrow" }, check, h("label", { class: "xc-sw-lab" }, h("span", null, "Target"), pick)),
      h("div", { class: "xc-sw-ctlrow" }, pause, resume, stop),
      cui.confirmEl, cui.hint, cui.out, cui.stateEl);
    syncControls();
  }

  function syncControls() {
    if (!cui || !sel) return;
    const id = sel.id, locked = uncertain.has(id), idle = !ctlBusy && !locked, chosen = !!target;
    cui.check.disabled = statBusy;
    cui.check.textContent = statBusy ? "Checking status..." : "Check status";
    cui.pick.disabled = ctlBusy;
    cui.pause.disabled = cui.resume.disabled = !idle || !chosen;
    cui.stop.disabled = !idle || !chosen || !!confirming;
    cui.hint.textContent = ctlBusy ? "A control request is in progress; it is not sent twice."
      : locked ? "The last request for this group has an unknown result. Controls stay off until Check status succeeds."
      : chosen ? "" : "Choose a target to turn on Pause, Resume and Stop.";
    const ask = confirming && confirming.id === id && !ctlBusy ? confirming : null;
    cui.confirmEl.replaceChildren(...(ask ? [
      h("p", { class: "xc-sw-warn" }, "Stop " + targetLabel(id, ask.target) + "? This cannot be undone here."),
      h("div", { class: "xc-sw-ctlrow" },
        h("button", { class: "xc-btn xc-sw-danger", type: "button", onclick: () => runControl("stop", true) }, "Confirm stop"),
        h("button", { class: "xc-btn", type: "button", onclick: () => cancelStop() }, "Cancel"))] : []));
    const r = ctlResults.get(id);
    cui.out.className = "xc-sw-ctlout" + (r && r.cls ? " " + r.cls : "");
    cui.out.textContent = r ? r.text : "";
    drawStatus();
  }

  function drawStatus() {
    const kids = [];
    if (statusErr) kids.push(h("div", { class: "xc-error", role: "alert" }, statusErr));
    if (status) {
      if (status.stale) kids.push(h("div", { class: "xc-sw-notice" }, "This status may be out of date. Use Check status to read it again."));
      if (status.reconciliation) kids.push(h("div", { class: "xc-sw-notice" }, "The server reports that this group needs reconciliation."));
      kids.push(h("p", { class: "xc-sw-meta" }, "Local runtime: " + (status.local ? "yes" : "no")));
      kids.push(h("ul", { class: "xc-sw-states", "aria-label": "Member states" }, status.members.map((m) =>
        h("li", { class: "xc-sw-mstate is-" + m.state }, h("span", { class: "xc-sw-mname" }, m.profile), h("span", { class: "xc-sw-mval" }, m.state)))));
    }
    cui.stateEl.replaceChildren(...kids);
  }

  function setTarget(v) {
    if (!sel || ctlBusy) { syncControls(); return; }
    const ok = v === "" || v === WHOLE || sel.members.some((m) => m.profile === v);
    target = ok ? v : "";
    confirming = null;
    syncControls();
  }

  function askStop() {
    if (ctlBusy || !sel || !target || uncertain.has(sel.id)) return;
    confirming = { id: sel.id, target };
    syncControls();
  }

  function cancelStop() { confirming = null; syncControls(); }

  async function checkStatus() {
    if (statBusy || !sel || inspecting || selErr || !GROUP_ID.test(sel.id)) return;
    const gen = statGen, id = sel.id, epoch = ctlEpoch;
    statBusy = true; statusErr = "";
    syncControls();
    let got = null, err = "";
    try {
      const d = await api("GET", BASE + "/" + encodeURIComponent(id) + "/status");
      if (goodStatus(d, id)) got = { members: d.members.map((m) => ({ profile: m.profile, state: m.state })), local: d.local_runtime, reconciliation: d.reconciliation_required, stale: false };
      else err = "The status reply was not understood.";
    } catch (e) {
      err = e && e.status ? plain(e.message, 300) : "The status could not be read. The server could not be reached.";
    }
    statBusy = false;
    if (!alive) return;
    if (gen !== statGen || !sel || sel.id !== id) { syncControls(); return; }    // another group is shown now: drop this reply
    if (got) {
      status = got;
      if (epoch === ctlEpoch && !ctlBusy) uncertain.delete(id);          // a control request ran meanwhile: this read may predate it
      else status.stale = true;
    } else {
      statusErr = err;
      if (status) status.stale = true;
    }
    syncControls();
  }

  async function runControl(op, confirmed) {
    if (ctlBusy || !sel || !Object.prototype.hasOwnProperty.call(OPS, op)) return;
    const id = sel.id, t = target;
    if (!GROUP_ID.test(id) || uncertain.has(id) || !t) return;
    if (op === "stop" && !(confirmed && confirming && confirming.id === id && confirming.target === t)) return;
    if (t !== WHOLE && !sel.members.some((m) => m.profile === t)) return;
    const body = t === WHOLE ? {} : { member: t };
    const label = targetLabel(id, t), name = OPS[op].toLowerCase();
    ctlBusy = true; ctlEpoch++; confirming = null;
    if (status) status.stale = true;
    ctlResults.set(id, { cls: "", text: "Sending the " + name + " request for " + label + "..." });
    syncControls();
    let res, lock = false;
    try {
      const d = await api("POST", BASE + "/" + encodeURIComponent(id) + "/" + op, body);
      if (d && d.id === id && d.action === op && d.accepted === true && (d.member == null ? null : d.member) === (t === WHOLE ? null : t)) {
        res = { cls: "is-ok", text: "The " + name + " request for " + label + " was accepted. This does not mean members have changed state yet. Use Check status to see the current states." };
      } else {
        lock = true;
        res = { cls: "is-bad", text: "The reply to the " + name + " request for " + label + " was not understood. The result is unknown. Nothing is retried. Use Check status before using the controls again." };
      }
    } catch (e) {
      if (e && REFUSED.indexOf(e.status) >= 0) {
        res = { cls: "is-bad", text: "The " + name + " request for " + label + " was refused: " + plain(e.message, 300) };
      } else {
        lock = true;
        res = { cls: "is-bad", text: "Could not confirm the " + name + " request for " + label + (e && e.status ? " (HTTP " + e.status + ")" : " (network failure)") +
          ". It may or may not have been applied. Nothing is retried. Use Check status before using the controls again." };
      }
    }
    ctlBusy = false; ctlEpoch++;
    if (!alive) return;
    if (lock) uncertain.add(id);
    ctlResults.set(id, res);
    if (status) status.stale = true;
    if (sel) syncControls();                      // the busy flag is global: always restore whatever group is on screen
  }

  // ------------------------------------------------------------ composer
  function checkedSet(id) { if (!audiences.has(id)) audiences.set(id, new Set()); return audiences.get(id); }

  function drawCompose(refresh) {
    const id = sel.id, set = checkedSet(id);
    const area = h("textarea", { class: "xc-field xc-sw-area", rows: "4", maxlength: String(MAX_TEXT), spellcheck: "true",
      "aria-label": "Message to group " + id, placeholder: "Message to the group",
      oninput: () => { drafts.set(id, area.value); sync(); } });
    area.value = drafts.get(id) || "";
    const boxes = sel.members.map((m) => {
      const cb = h("input", { type: "checkbox", value: m.profile, onchange: () => { cb.checked ? set.add(m.profile) : set.delete(m.profile); sync(); } });
      cb.checked = set.has(m.profile);
      return h("label", { class: "xc-sw-check" }, cb, h("span", null, m.profile));
    });
    const count = h("span", { class: "xc-sw-count" });
    const btn = h("button", { class: "xc-btn xc-primary", type: "submit" }, "Send");
    const hint = h("p", { class: "xc-sw-hint" });
    const out = h("div", { class: "xc-sw-result", role: "status" });
    ui = { area, count, btn, hint, out, refresh };
    sendEl.replaceChildren(h("form", { class: "xc-sw-form", onsubmit: (e) => { e.preventDefault(); send(); } },
      h("fieldset", { class: "xc-sw-aud" }, h("legend", null, "Audience"), h("div", { class: "xc-sw-checks" }, boxes),
        h("p", { class: "xc-sw-hint" }, "Tick none to send to the whole group (no audience is sent).")),
      area, h("div", { class: "xc-sw-actions" }, count, btn), hint, out));
    sync(); showResult();
  }

  function problem(text, set) {
    if (!text.trim()) return "Write a message first.";
    if (text.length > MAX_TEXT) return "Text is over " + MAX_TEXT + " characters.";
    if (text.indexOf("\u0000") >= 0) return "Text has an invalid character.";
    if (set.size > MAX_AUDIENCE) return "At most " + MAX_AUDIENCE + " audience members.";
    return "";
  }

  function sync() {
    if (!ui || !sel) return;
    const text = ui.area.value, set = checkedSet(sel.id), p = problem(text, set);
    ui.count.textContent = text.length + " / " + MAX_TEXT;
    ui.btn.disabled = sending || !!p;
    ui.btn.textContent = sending ? "Sending..." : (results.get(sel.id) || {}).unknown ? "Send again" : "Send";
    ui.hint.textContent = sending ? "A send is in progress; it is not sent twice." : text.trim() ? p : "";
  }

  function showResult() {
    if (!ui || !sel) return;
    const r = results.get(sel.id);
    ui.out.className = "xc-sw-result" + (r ? " " + r.cls : "");
    ui.out.textContent = r ? r.text : "";
  }

  async function send() {
    if (sending || !sel || !ui) return;
    const id = sel.id, text = ui.area.value, set = checkedSet(id);
    if (problem(text, set)) return;
    const members = sel.members.map((m) => m.profile);
    const body = { text };
    const aud = members.filter((p) => set.has(p));
    if (aud.length) body.audience = aud;              // none ticked: the key is omitted, never an empty list
    sending = true;
    results.set(id, { cls: "", text: "Sending..." });
    sync(); showResult();
    let res;
    try {
      await api("POST", BASE + "/" + encodeURIComponent(id) + "/send", body);
      res = { cls: "is-ok", text: "Saved to the group log. Whether any member processed it or was woken is not known." };
    } catch (e) {
      if (e && NOT_SAVED.indexOf(e.status) >= 0) {
        res = { cls: "is-bad", text: "Not sent: " + clip(e.message, 300) + " Your draft is kept." };
      } else {
        res = { cls: "is-bad", unknown: true, text: "Could not confirm the send" + (e && e.status ? " (HTTP " + e.status + ")" : " (network failure)") +
          ". The message may or may not be saved. Refresh the transcript and check the group log before sending again. Your draft is kept and nothing is resent automatically." };
      }
    }
    sending = false;
    if (!alive) return;
    results.set(id, res);
    if (res.cls === "is-ok" && drafts.get(id) === text) drafts.delete(id);   // keep anything typed while it was sending
    if (sel && ui) {
      if (sel.id === id) {
        if (res.cls === "is-ok" && ui.area.value === text) ui.area.value = "";
        showResult();
        if (res.cls === "is-ok") loadMessages();
      }
      sync();                                   // the pending flag is global: always re-sync whatever group is on screen
    }
  }

  drawList();
  drawMain();
  loadList();
  return () => { alive = false; listGen++; selGen++; statGen++; confirming = null; cui = null; };
}
