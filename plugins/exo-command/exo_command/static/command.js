// The read-mostly command centre view (approvals, decisions, sessions, tasks, projects, cost).
// Drawn as an instrument panel (D-061): a page head, six gauges, then one hairline section per source.
// mount(el) draws into el; the returned function stops its timers and stream.
import { api, stream, pageHead, readout, pill } from "./dom.js";

export function mount(app) {
  "use strict";
  // It reads /api/snapshot and re-reads it whenever /api/stream says the core published something.
  // The only write is answering an approval (POST /api/approvals/<id>). Allow always is a standing grant, so it takes a
  // separate Confirm always click, as on the Chat card; `confirming` names the one request asking it.
  // `keyed` holds this draw's approval buttons by request and choice. render() replaces the page on every core event,
  // so it gives focus back to the button with the same key.
  var snap = null, err = null, view = "all", timer = null, confirming = null, keyed = {};
  var page = h("div", { class: "xc-pg xc-cmd" });
  app.replaceChildren(page);

  function h(tag, attrs) {
    var el = document.createElement(tag);
    for (var k in (attrs || {})) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), v);
      else if (k === "style") el.style.cssText = v;   // the CSP (#162) refuses style attributes
      else el.setAttribute(k, v);
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  function ago(sec) {
    if (!sec) return "never";
    var d = Math.max(0, Date.now() / 1000 - sec);
    return (d < 60 ? Math.round(d) + "s" : d < 3600 ? Math.round(d / 60) + "m" :
      d < 86400 ? Math.round(d / 3600) + "h" : Math.round(d / 86400) + "d") + " ago";
  }
  function usd(n) { return n == null ? "n/a" : n === 0 ? "$0" : n < 0.01 ? "<$0.01" : "$" + n.toFixed(n < 10 ? 2 : 0); }
  function tok(n) { n = n || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n); }
  function short(s, n) { s = s || ""; return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function tag(text, tone) { return h("span", { class: "xc-tag" + (tone ? " xc-t-" + tone : "") }, text); }
  function panel(id, title, meta, body, bad) {
    return h("section", { class: "xc-panel", id: "xc-" + id },
      h("header", { class: "xc-panel-head" }, h("h2", null, title), meta ? h("span", { class: "xc-meta" }, meta) : null),
      bad ? h("div", { class: "xc-error" }, bad) : body);
  }
  function empty(t, how) { return h("div", { class: "xc-empty" }, h("span", null, t), how ? h("span", { class: "xc-empty-how" }, how) : null); }
  var TASK_FORM = { running: "run", queued: "queue", failed: "fail", done: "done", cancelled: "stop" };

  function kpis(s) {
    var d = (s.decisions.counts || {}), a = s.approvals.counts || {}, se = s.sessions.counts || {};
    var t = s.tasks || {}, p = (s.projects.counts || {}), c = s.costs || {};
    var items = [
      ["approvals", "Approvals", a.pending || 0, "pending", a.pending ? "red" : ""],
      ["decisions", "Waiting on you", d.waiting || 0, (d.overdue || 0) + " overdue", d.overdue ? "red" : (d.waiting ? "amber" : "")],
      ["sessions", "Live", se.active || 0, (se.subagents || 0) + " subagents", se.active ? "green" : ""],
      ["tasks", "Tasks open", t.open || 0, (t.needs_attention || 0) + " failed", t.needs_attention ? "amber" : ""],
      ["projects", "Projects", p.projects || 0, (p.sessions || 0) + " sessions", ""],
      ["costs", "Cost " + (c.window_days || 7) + "d", usd(c.total_usd || 0), (c.priced_sessions || 0) + " priced", ""]];
    var LED = { red: "alert", amber: "warn", green: "ok" };
    return h("div", { class: "xc-kpis" }, items.map(function (it) {
      return h("button", { class: "xc-kpi" + (it[4] ? " xc-k-" + it[4] : "") + (view === it[0] ? " on" : ""), "aria-pressed": view === it[0] ? "true" : "false",
        onclick: function () { view = it[0]; confirming = null; render(); } },
        h("span", { class: "xc-kpi-label" }, h("span", { class: "xc-led", "data-s": LED[it[4]] || "off", "aria-hidden": "true" }), it[1]),
        h("span", { class: "xc-kpi-value" }, it[2]), h("span", { class: "xc-kpi-sub" }, it[3]));
    }));
  }

  function respond(id, decision) {
    api("POST", "/api/approvals/" + encodeURIComponent(id), { decision: decision })
      .then(function () { load(); })
      .catch(function (e) { err = e.message || String(e); render(); });
  }
  function key(a, k, button) { keyed[a.request_id + " " + k] = button; return button; }
  // Cancel and Escape put focus back on allow always.
  function cancel(a) { confirming = null; render(a.request_id + " allow_always"); }
  function choices(a) {
    if (confirming === a.request_id) return [
      h("span", { role: "status", "aria-live": "polite" }, "Allow always? This saves a standing grant."),
      // e.detail > 1: the second click of a double click on "allow always" is not a confirmation
      key(a, "confirm", h("button", { class: "xc-btn", onclick: function (e) { if (e.detail > 1 || confirming !== a.request_id) return; confirming = null; respond(a.request_id, "allow_always"); } }, "Confirm always")),
      key(a, "cancel", h("button", { class: "xc-btn", onclick: function () { cancel(a); } }, "Cancel"))];
    return (a.options || ["allow", "deny"]).map(function (o) {
      // The step opens on Cancel, the safe default: a second Enter cancels instead of saving the grant.
      return key(a, o, h("button", { class: "xc-btn" + (o === "deny" ? " xc-danger" : ""),
        onclick: o === "allow_always" ? function () { confirming = a.request_id; render(a.request_id + " cancel"); } : function () { confirming = null; respond(a.request_id, o); } }, o.replace("_", " ")));
    });
  }
  function approvals(d) {
    if (confirming && !d.pending.some(function (a) { return a.request_id === confirming; })) confirming = null;   // closed elsewhere
    var body = d.pending.length ? h("div", { class: "xc-stack" }, d.pending.map(function (a) {
      return h("article", { class: "xc-card xc-dec xc-p-high" },
        h("div", { class: "xc-card-top" }, h("h3", null, a.summary), h("div", { class: "xc-tags" }, pill(a.risk + " risk", a.risk === "high" ? "alert" : "warn"))),
        a.call ? h("div", { class: "xc-tool" }, h("span", null, "tool"), " ", a.call.name) : null,
        a.call ? h("pre", { class: "xc-pre-body xc-args" }, JSON.stringify(a.call.arguments || {}, null, 2)) : null,   // the whole call, never cut or boxed: a redraw resets a scroll box
        h("div", { class: "xc-foot", onkeydown: function (e) { if (e.key === "Escape" && confirming === a.request_id) cancel(a); } },
          h("span", null, a.requested_at ? "asked " + ago(a.requested_at) : ""), a.session_title ? h("span", null, a.session_title) : null, choices(a)));
    })) : empty("No approvals waiting.", "When an agent asks before it acts, the request shows here and in Chat.");
    return panel("approvals", "Approvals", "answered here or in any other client", body, d.ok === false ? d.error : null);
  }

  function decisions(d) {
    var body = !d.configured ? empty("No decisions queue configured.", "Set principal_store in the config, or EXO_PRINCIPAL_STORE, to see what waits on you.")
      : !d.waiting.length ? empty("Nothing is waiting on you.", "Decisions an agent queues for you appear here, oldest first.")
      : h("div", { class: "xc-stack" }, d.waiting.map(function (x) {
        return h("article", { class: "xc-card xc-dec xc-p-" + x.priority + (x.overdue ? " xc-overdue" : "") },
          h("div", { class: "xc-card-top" }, h("h3", null, x.title), h("div", { class: "xc-tags" },
            x.overdue ? pill("overdue", "alert") : null, x.priority !== "normal" ? pill(x.priority, x.priority === "high" ? "warn" : "off") : null,
            x.reversible === false ? pill("irreversible", "fail") : null)),
          x.context ? h("div", { class: "xc-pre-body" }, short(x.context, 400)) : null,
          (x.options || []).length ? h("ol", { class: "xc-opts" }, x.options.map(function (o) { return h("li", null, h("span", { class: "xc-opt-key" }, o.key), o.label); })) : null,
          h("div", { class: "xc-foot" }, h("span", null, "opened " + ago(x.opened_at)), x.asked_by && x.asked_by.agent ? h("span", null, "from " + x.asked_by.agent) : null));
      }));
    return panel("decisions", "Decisions waiting on you", d.path || "from the core's decision.queued events", body, d.ok === false ? d.error : null);
  }

  function sessionCard(a) {
    var form = { running: "run", waiting: "warn", idle: "off", done: "done", interrupted: "stop", errored: "fail" }[a.status] || "off";
    return h("article", { class: "xc-card xc-agent xc-s-" + a.status },
      h("div", { class: "xc-card-top" }, h("div", { class: "xc-tags" }, pill(a.status, form), tag(a.kind), a.seat ? tag(a.seat) : null, a.model ? tag(short(a.model, 28)) : null),
        h("span", { class: "xc-dim" }, ago(a.ended_at || a.last_activity_at))),
      h("h3", { title: a.goal || "" }, short(a.title || a.goal || a.session_id, 140)),
      a.tool ? h("div", { class: "xc-tool" }, h("span", null, "tool"), " ", short(a.tool, 180)) : null,
      a.output ? h("div", { class: "xc-pre-body" }, short(a.output, 360)) : null,
      h("div", { class: "xc-foot" }, h("span", null, a.messages + " msgs"), h("span", null, a.tool_calls + " tools"),
        a.cost_usd ? h("span", null, usd(a.cost_usd)) : null, h("code", null, short(a.session_id, 26))));
  }
  function sessions(d) {
    var body = h("div", null,
      d.active.length ? h("div", { class: "xc-grid" }, d.active.map(sessionCard)) : empty("No session moved in the live window.", "Threads and subagents that move show here with their tool, output and cost."),
      d.past.length ? h("details", { class: "xc-details" }, h("summary", null, "Finished subagents (" + d.past.length + ")"),
        h("div", { class: "xc-grid" }, d.past.map(sessionCard))) : null);
    return panel("sessions", "Live sessions", "live = moved in the last " + Math.round(d.live_window_seconds / 60) + " min", body, d.ok === false ? d.error : null);
  }

  function tasks(d) {
    var body = !d.columns.length ? empty("No delegated tasks yet.", "Open tasks show here by status; the Tasks tab has the full board.")
      : h("div", { class: "xc-board", style: "--n:" + d.columns.length }, d.columns.map(function (col) {
      var f = TASK_FORM[col.status] || "off";
      return h("section", { class: "xc-lane2 xc-ks-" + col.status, "aria-label": col.status + " tasks" },
        h("header", { class: "xc-lane2-head" }, pill(col.status, f), h("span", { class: "xc-n" }, col.total)),
        h("div", { class: "xc-lane2-body" }, col.tasks.map(function (t) {
          return h("article", { class: "xc-tcard", "data-s": f }, h("h3", { title: t.title }, short(t.title, 120)),
            t.detail ? h("p", { class: "xc-tcard-detail" }, short(t.detail, 200)) : null,
            h("footer", { class: "xc-tcard-foot" }, h("code", null, short(t.task_id, 18)), h("span", null, ago(t.finished_at || t.updated_at))));
        })));
    }));
    return panel("tasks", "Tasks", d.open + " open, from task.updated", body, d.ok === false ? d.error : null);
  }

  function projects(d) {
    if (d.ok === false) return panel("projects", "Projects", null, null, d.error);
    var lanes = d.lanes.filter(function (l) { return l.id !== "done" && l.id !== "scrapped" && l.cards.length; });
    var body = lanes.length ? h("div", { class: "xc-lanes" }, lanes.map(function (l) {
      return h("div", { class: "xc-lane xc-lane-" + l.id }, h("div", { class: "xc-lane-head" }, h("span", null, l.label), h("span", { class: "xc-n" }, l.cards.length)),
        l.cards.slice(0, 40).map(function (p) {
          return h("article", { class: "xc-card xc-proj" }, h("h3", null, p.name),
            h("div", { class: "xc-tags" }, tag(p.band), tag(p.session_count + " sess")),
            h("div", { class: "xc-foot" }, h("span", null, p.lifecycle_name), h("span", null, "active " + ago(p.last_activity))),
            p.next_action ? h("div", { class: "xc-next" }, h("span", null, "next"), " ", short(p.next_action, 160)) : null);
        }));
    })) : empty("No projects yet.", "Sessions that share a folder or goal cluster into projects here.");
    return panel("projects", "Projects", d.counts.projects + " clusters from " + d.counts.sessions + " sessions", body);
  }

  function bars(rows, key) {
    var max = 0; rows.forEach(function (r) { if (r.cost_usd > max) max = r.cost_usd; });
    return h("ul", { class: "xc-bars" }, rows.map(function (r) {
      return h("li", null, h("span", { class: "xc-bar-label" }, short(r[key], 26)),
        h("span", { class: "xc-bar" }, h("span", { style: "width:" + (max ? Math.max(1, Math.round(100 * r.cost_usd / max)) : 0) + "%" })),
        h("span", { class: "xc-bar-val" }, usd(r.cost_usd)));
    }));
  }
  function costs(d) {
    if (d.ok === false) return panel("costs", "Cost per session", null, null, d.error);
    if (!d.top.length && !d.by_seat.length) return panel("costs", "Cost per session", "last " + d.window_days + "d",
      empty("No priced sessions in this window.", "Each session's tokens and cost show here once a model call is priced."));
    var body = h("div", null,
      h("div", { class: "xc-cost-grid" }, h("div", null, h("h4", null, "By seat"), bars(d.by_seat, "seat")), h("div", null, h("h4", null, "By model"), bars(d.by_model, "model"))),
      h("div", { class: "xc-table-wrap" }, h("table", { class: "xc-table" },
        h("thead", null, h("tr", null, ["Session", "Seat", "Model", "Tokens in/out", "Msgs", "Cost", "Active"].map(function (x, i) { return h("th", { class: i >= 3 && i <= 5 ? "xc-num" : null }, x); }))),
        h("tbody", null, d.top.map(function (s) {
          return h("tr", null, h("td", { title: s.session_id }, short(s.title || s.session_id, 48)), h("td", null, s.seat), h("td", null, short(s.models.join(", "), 28)),
            h("td", { class: "xc-num" }, tok(s.input_tokens) + " / " + tok(s.output_tokens)), h("td", { class: "xc-num" }, s.messages),
            h("td", { class: "xc-num" + (s.priced && !s.rolled_up ? "" : " xc-faint"), title: s.rolled_up ? "Counted in its parent session's cost" : null },
              s.priced ? usd(s.cost_usd) : "unpriced", s.rolled_up ? " in parent" : null), h("td", { class: "xc-dim" }, ago(s.last_activity)));
        })))));
    return panel("costs", "Cost per session", "last " + d.window_days + "d, " + usd(d.total_usd) + " over " + d.priced_sessions + " priced sessions", body);
  }

  var VIEWS = [["all", "All"], ["approvals", "Approvals"], ["decisions", "Decisions"], ["sessions", "Sessions"], ["tasks", "Tasks"], ["projects", "Projects"], ["costs", "Cost"]];
  var PANELS = { approvals: approvals, decisions: decisions, sessions: sessions, tasks: tasks, projects: projects, costs: costs };

  function render(want) {   // want: the key of the button to focus once drawn
    for (var k in keyed) if (!want && keyed[k] === document.activeElement) want = k;
    keyed = {};
    var top = pageHead({ eyebrow: "04 / Command", title: "Command",
      lede: "What waits on you and what is running, read from the core. Approvals can be answered here.",
      aside: h("div", { class: "xc-phead-read" }, readout([
        ["Core", snap ? (snap.connected ? "connected" : "not reachable") : "reading", snap ? (snap.connected ? "ok" : "fail") : "queue"],
        ["Seq", snap ? String(snap.seq) : "-"]]),
        h("button", { class: "xc-btn", onclick: function () { confirming = null; load(true); } }, "Refresh")) });
    var seg = h("div", { class: "xc-seg", role: "tablist", "aria-label": "Sections" }, VIEWS.map(function (v) {
      return h("button", { class: view === v[0] ? "on" : "", role: "tab", "aria-selected": view === v[0] ? "true" : "false",
        onclick: function () { view = v[0]; confirming = null; render(); } }, v[1]);
    }));
    var body = snap ? h("div", { class: "xc-body" + (view === "all" ? " xc-all" : "") },
      Object.keys(PANELS).filter(function (k) { return view === "all" || view === k; }).map(function (k) { return PANELS[k](snap[k]); })) : null;
    page.replaceChildren.apply(page, [top, err ? h("div", { class: "xc-error" }, err) : null,
      snap && snap.error ? h("div", { class: "xc-error" }, snap.error) : null, snap ? kpis(snap) : h("div", { class: "xc-loading" }, "Reading the core..."), seg, body]
      .filter(Boolean));
    if (keyed[want]) keyed[want].focus();
  }

  function load(fresh) {
    return api("GET", "/api/snapshot" + (fresh ? "?fresh=true" : ""))
      .then(function (d) { snap = d; err = null; render(); })
      .catch(function (e) { err = e.message || String(e); render(); });
  }
  function later() { clearTimeout(timer); timer = setTimeout(function () { load(false); }, 150); }

  var es = null, poll = null;
  load(false);
  try {
    es = stream("/api/stream");
    es.onmessage = later;
  } catch (e) { poll = setInterval(function () { if (!document.hidden) load(false); }, 5000); }
  return function unmount() { clearTimeout(timer); clearInterval(poll); if (es) es.close(); };
}
