// Shared, DOM-free interpretation of durable chat state and structured core reports.
export function contextMetrics(r) {
  if (!r || r.schema_version !== 1 || !r.tokens || !r.tree || !r.nodes) return null;
  return { ...r.tokens, summaries: r.tree.summaries, nodes: r.tree.nodes,
    done: r.nodes.done, pending: r.nodes.pending, log: r.log_entries, frontier: r.frontier,
    capacity: Number.isInteger(r.capacity) && r.capacity > 0 ? r.capacity : null, model: r.model };
}
export function mergeTranscript(snapshot, previous) {
  const ids = new Set(snapshot.map((x) => x.id));
  // A live delta/tool is not yet a durable message; preserve it through a refresh.
  return snapshot.concat(previous.filter((x) => !ids.has(x.id) && (x.streaming || x.status === "running")));
}
export function slashInput(text) {
  const m = /^\/([a-z]+)(?:\s+(.*))?$/s.exec(text);
  if (!m) return null; // paths and punctuation are message text, not command typos
  return { name: m[1], args: (m[2] || "").trim() };
}
export function commandUsage({ name, args }) {
  if (name === "new" && !["", "private"].includes(args)) return "usage: /new [private]";
  if (name === "context" && args) return "usage: /context";
  if (name === "trace" && args && !/^\d+$/.test(args)) return "usage: /trace [count]";
  if (name === "findings" && args && !["all", "open", "fixed", "verified", "reopened", "count"].includes(args)) return "usage: /findings [state]";
  if (name === "layout" && args && !["inverse", "classic"].includes(args)) return "usage: /layout inverse|classic";
  return "";
}
