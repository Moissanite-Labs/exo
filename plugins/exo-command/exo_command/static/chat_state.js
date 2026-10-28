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
  if (name === "permissions" && args && !["status", "bypass", "ask"].includes(args)) return "usage: /permissions [status|bypass|ask]";
  if (name === "layout" && args && !["inverse", "classic"].includes(args)) return "usage: /layout inverse|classic";
  return "";
}
// Threads sidebar grouping (canonical port of exo-desktop #83): delegated scout sessions sit beneath their parent conversation.
// Ownership comes only from the session's own `parent` + `kind` metadata, as session.list reports it
// (delegate children are created with parent=<parent session> and kind="subagent"). A title that starts
// with "scout:" is never used to decide ownership: it is only the child's own title, shown as-is.
// Forks also carry a parent, but kind="thread", so they stay top-level, unchanged.
// A session label with no model call: a manual title wins; otherwise "Untitled thread/scout" plus the
// full session id, so an unnamed conversation is never just a raw id and the id stays discoverable.
export function isScoutChild(s) {
  return !!(s && typeof s.parent === "string" && s.parent !== "" && s.parent !== s.id && s.kind === "subagent");
}
export function threadLabel(s) {
  if (!s) return "Untitled thread";
  const title = typeof s.title === "string" ? s.title.trim() : "";
  if (title) return title;
  const id = typeof s.id === "string" && s.id ? s.id : "";
  return (s.kind === "subagent" ? "Untitled scout" : "Untitled thread") + (id ? " · " + id : "");
}
// Group a session.list into a tree, in the list's own order, so the sidebar can render parents with
// their scouts beneath them. Returns [{session, children: [...], orphan: bool}]. A scout whose parent
// is not in the list (deleted, beyond the list limit, or from an older store) is an orphan: it is
// returned top-level, still openable, never dropped and never attached to a lookalike parent.
// Cycles (A parent of B, B parent of A) and sessions left unvisited by the first pass are swept to
// top-level with no children, so no session can disappear from the sidebar.
export function groupThreads(sessions) {
  const list = Array.isArray(sessions) ? sessions.filter((s) => s && typeof s.id === "string" && s.id) : [];
  const byId = new Map();
  for (const s of list) if (!byId.has(s.id)) byId.set(s.id, s);
  const kids = new Map();
  for (const s of list) if (isScoutChild(s) && byId.has(s.parent)) {
    if (!kids.has(s.parent)) kids.set(s.parent, []);
    if (!kids.get(s.parent).includes(s)) kids.get(s.parent).push(s);
  }
  const visited = new Set();
  function node(s, ancestors) {
    visited.add(s.id);
    const children = [];
    if (ancestors.size < 20) {
      for (const c of kids.get(s.id) || []) {
        if (visited.has(c.id) || ancestors.has(c.id)) continue; // a cycle, or a duplicate id: already rendered above
        children.push(node(c, new Set([...ancestors, c.id])));
      }
    }
    return { session: s, children, orphan: false };
  }
  const out = [];
  for (const s of list) {
    if (visited.has(s.id)) continue;
    if (isScoutChild(s) && !byId.has(s.parent)) {
      const n = node(s, new Set([s.id]));
      n.orphan = true;
      out.push(n);
    } else if (!isScoutChild(s)) {
      out.push(node(s, new Set([s.id])));
    }
  }
  for (const s of list) if (!visited.has(s.id)) { // cycle members the first pass could not reach
    visited.add(s.id);
    out.push({ session: s, children: [], orphan: true });
  }
  return out;
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
