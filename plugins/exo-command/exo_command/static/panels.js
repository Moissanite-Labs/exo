// Renders one ui.panel payload. Every kind has a full form; anything unknown or malformed shows the
// plain-text fallback (`text`), which every panel carries. `act(value, label)` sends ui.respond "acted";
// a choice also sends its label as the user's reply (chat.js pickChoice).
import { h, empty, short } from "./dom.js";
import { renderMarkdown } from "./markdown.js";

export const KINDS = ["markdown", "diff", "table", "tasks", "approval", "taste", "choice", "file", "chart"];

function chip(k, label, onclick, go) {
  return h("button", { class: "xc-key" + (go ? " xc-key-go" : ""), onclick }, h("b", null, k), label);
}

function fallback(p) { return h("pre", { class: "xc-pre-body" }, p.text || p.title || ""); }

function diff(p) {
  const d = p.data || {};
  const files = Array.isArray(d.files) ? d.files : [{ path: d.path || "", diff: d.diff || d.patch || "" }];
  const body = files.filter((f) => f && typeof f.diff === "string" && f.diff);
  if (!body.length) return fallback(p);
  return h("div", { class: "xc-diff" }, body.map((f) => h("div", { class: "xc-diff-file" },
    f.path ? h("div", { class: "xc-diff-path" }, f.path) : null,
    h("pre", { class: "xc-code" }, f.diff.split("\n").map((ln) => {
      const c = ln.startsWith("+++") || ln.startsWith("---") ? "meta" : ln.startsWith("@@") ? "hunk"
        : ln.startsWith("+") ? "add" : ln.startsWith("-") ? "del" : "";
      return h("span", { class: "xc-dl" + (c ? " xc-dl-" + c : "") }, ln + "\n");
    })))));
}

function table(p) {
  const d = p.data || {};
  const rows = Array.isArray(d.rows) ? d.rows : [];
  let cols = Array.isArray(d.columns) ? d.columns.map((c) => (c && typeof c === "object" ? (c.label || c.key) : c)) : null;
  const keys = Array.isArray(d.columns) ? d.columns.map((c) => (c && typeof c === "object" ? c.key : null)) : null;
  if (!rows.length) return fallback(p);
  if (!cols && rows[0] && !Array.isArray(rows[0]) && typeof rows[0] === "object") cols = Object.keys(rows[0]);
  const cell = (r, i) => {
    if (Array.isArray(r)) return r[i];
    const k = (keys && keys[i]) || cols[i];
    return r[k];
  };
  const n = cols ? cols.length : (Array.isArray(rows[0]) ? rows[0].length : 0);
  return h("div", { class: "xc-table-wrap" }, h("table", { class: "xc-table" },
    cols ? h("thead", null, h("tr", null, cols.map((c) => h("th", null, String(c))))) : null,
    h("tbody", null, rows.map((r) => h("tr", null, Array.from({ length: n }, (_, i) => {
      const v = cell(r, i);
      return h("td", null, v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v));
    }))))));
}

function tasks(p) {
  const list = Array.isArray((p.data || {}).tasks) ? p.data.tasks : [];
  if (!list.length) return fallback(p);
  return h("ul", { class: "xc-tasklist" }, list.map((t) => h("li", { class: "xc-ts-" + (t.status || "queued") },
    h("span", { class: "xc-tstatus" }, t.status || "queued"), h("span", null, t.title || t.task_id || ""),
    t.detail ? h("span", { class: "xc-dim" }, short(t.detail, 160)) : null)));
}

function optionList(p) {
  const raw = (p.data || {}).options;
  if (!Array.isArray(raw)) return [];
  return raw.map((o, i) => (o && typeof o === "object"
    ? { key: String(o.key != null ? o.key : o.id != null ? o.id : i), label: String(o.label || o.text || o.key || "") }
    : { key: String(o), label: String(o) }));
}

function choice(p, act) {
  const opts = optionList(p);
  if (!opts.length) return fallback(p);
  const q = (p.data || {}).prompt;
  return h("div", { class: "xc-stack" }, q ? h("div", null, q) : null,
    h("div", { class: "xc-keys" }, opts.map((o, i) => chip(String(i + 1), o.label, () => act({ choice: o.key }, o.label)))));
}

function approval(p, act) {
  const opts = optionList(p);
  const labels = opts.length ? opts : [{ key: "allow", label: "allow" }, { key: "deny", label: "deny" }];
  return h("div", { class: "xc-stack" }, fallback(p),
    h("div", { class: "xc-keys" }, labels.map((o) => chip(o.label[0] || "?", o.label, () => act({ decision: o.key }), o.key === "allow"))));
}

function taste(p, act) {
  const d = p.data || {};
  const rules = Array.isArray(d.rules) ? d.rules : d.rule ? [typeof d.rule === "object" ? d.rule : { text: String(d.rule) }] : [];
  if (!rules.length) return fallback(p);
  return h("div", { class: "xc-stack" }, rules.map((r, i) => h("div", { class: "xc-pb" },
    h("div", null, r.text || r.rule || ""),
    h("div", { class: "xc-keys" },
      chip("k", "keep", () => act({ rule_id: r.rule_id || r.id || String(i), verdict: "accept" }), true),
      chip("d", "drop", () => act({ rule_id: r.rule_id || r.id || String(i), verdict: "reject" }))))));
}

function file(p) {
  const d = p.data || {};
  if (typeof d.content !== "string") return fallback(p);
  const lines = d.content.split("\n");
  const start = Number(d.start_line) || 1;
  return h("div", null, d.path ? h("div", { class: "xc-diff-path" }, d.path) : null,
    h("pre", { class: "xc-code xc-file" }, lines.map((ln, i) => h("span", { class: "xc-dl" },
      h("span", { class: "xc-ln" }, String(start + i)), ln + "\n"))));
}

// A bar chart from data.series [{label, value}] (or data.labels + data.values), or a line from data.points [{x, y}].
function chart(p) {
  const d = p.data || {};
  let series = Array.isArray(d.series) ? d.series.map((s) => ({ label: String(s.label || s.x || ""), value: Number(s.value != null ? s.value : s.y) })) : null;
  if (!series && Array.isArray(d.labels) && Array.isArray(d.values)) series = d.labels.map((l, i) => ({ label: String(l), value: Number(d.values[i]) }));
  if (series && series.some((s) => !isFinite(s.value))) series = null;
  if (series && series.length) {
    const max = Math.max(...series.map((s) => Math.abs(s.value)), 1e-9);
    return h("ul", { class: "xc-bars" }, series.map((s) => h("li", null,
      h("span", { class: "xc-bar-label" }, short(s.label, 26)),
      h("span", { class: "xc-bar" }, h("span", { style: "width:" + Math.max(1, Math.round(100 * Math.abs(s.value) / max)) + "%" })),
      h("span", { class: "xc-bar-val" }, String(s.value)))));
  }
  const pts = Array.isArray(d.points) ? d.points.map((q) => [Number(q.x != null ? q.x : q[0]), Number(q.y != null ? q.y : q[1])]) : [];
  if (pts.length > 1 && pts.every((q) => isFinite(q[0]) && isFinite(q[1]))) {
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const sx = (x) => 4 + (296 * (x - x0)) / ((x1 - x0) || 1), sy = (y) => 76 - (72 * (y - y0)) / ((y1 - y0) || 1);
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 304 80");
    svg.setAttribute("class", "xc-line");
    const line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", pts.map((q) => sx(q[0]).toFixed(1) + "," + sy(q[1]).toFixed(1)).join(" "));
    svg.appendChild(line);
    return svg;
  }
  return fallback(p);
}

const RENDER = { diff, table, tasks, choice, approval, taste, file, chart };

export function renderBody(p, act) {
  try {
    if (p.kind === "markdown") return p.data && typeof p.data.markdown === "string" ? renderMarkdown(p.data.markdown)
      : p.text ? renderMarkdown(p.text) : empty("Empty panel.");
    const fn = RENDER[p.kind];
    return fn ? fn(p, act) : fallback(p);
  } catch (e) {
    return fallback(p);
  }
}

// A panel card: title, reason, body, and a dismiss button.
export function renderPanel(p, handlers) {
  const act = (value, label) => handlers.act(p, value, label);
  return h("section", { class: "xc-sidepanel", "data-panel": p.panel_id, "data-kind": p.kind },
    h("header", { class: "xc-sidehead" },
      h("h3", null, p.kind === "approval" ? "Approval \u00B7 " + (p.title || "") : (p.kind === "taste" ? "Taste \u00B7 " : "") + (p.title || p.kind)),
      h("button", { class: "xc-btn xc-x", title: "Dismiss", "aria-label": "Dismiss", onclick: () => handlers.dismiss(p) }, "\u00D7")),
    p.reason ? h("div", { class: "xc-pm" }, p.reason) : null,
    renderBody(p, act));
}
