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
  const m = /^\/([a-z][a-z0-9_-]*)(?:\s+(.*))?$/is.exec(text);
  if (!m) return null; // paths and punctuation are message text, not command typos
  return { name: m[1].toLowerCase(), args: (m[2] || "").trim() };
}
export function commandUsage({ name, args }) {
  if (name === "new" && !["", "private"].includes(args)) return "usage: /new [private]";
  if (name === "rename" && !args) return "usage: /rename <title>";
  if (name === "context" && args) return "usage: /context";
  if (name === "trace" && args && !/^\d+$/.test(args)) return "usage: /trace [count]";
  if (name === "findings" && args && !["all", "open", "fixed", "verified", "reopened", "count"].includes(args)) return "usage: /findings [state]";
  if (name === "permissions" && args && !["status", "bypass", "full", "full all", "ask"].includes(args)) return "usage: /permissions [status|bypass|full|full all|ask]";
  if (name === "layout" && args && !["inverse", "classic"].includes(args)) return "usage: /layout inverse|classic";
  return "";
}
// A stored message that another session (meta.peer), a bus seat (meta.bus) or a background job (meta.job) wrote,
// D-026 and D-040: a card with its real author, never the user's own line, as the terminal client draws it. It keys
// on the ingress msg_id, so the live peer.message card and the stored message are one card. null for anything else.
export function ingressItem(m) {
  const meta = (m && m.role === "user" && m.meta) || {};
  const kind = ["peer", "bus", "job"].find((k) => meta[k]);
  if (!kind) return null;
  const tags = typeof meta[kind] === "object" ? meta[kind] : { from: typeof meta[kind] === "string" ? meta[kind] : "" };
  const text = (m.content || []).filter((p) => p.type === "text").map((p) => p.text || "").join("");
  return { kind: "peer", id: tags.msg_id || m.id, label: { peer: "Peer", bus: "Bus", job: "Job" }[kind],
    sender: kind === "job" ? tags.id : tags.from, trust: kind === "job" ? "" : tags.trust, text };
}
