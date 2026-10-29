// Human guidance for the chat status strip: one plain sentence about what the run state means for
// the person at the keyboard. Pure function; chat.js calls it from drawStatus() and only updates the
// persistent line when the result changes, so token telemetry never re-announces the same sentence.

export function runGuidance({ connected = true, webUsable = true, coreState = "connected", stale = false, loading = false, approval = false, busy = false } = {}) {
  if (!connected) {
    // A reconnect already underway is neutral; a hard loss must say work may still be running.
    return webUsable && coreState === "connecting" ? "Connection unavailable."
      : "Connection lost. Activity may still be running; sending is paused.";
  }
  if (stale) return loading ? "Checking activity before sending." : "Activity is unknown. Retry activity before sending.";
  if (busy && approval) return "Review the approval request to continue. You can deny it or stop the run.";
  if (busy) return "Send a message to steer this run at its next tool round, or stop it.";
  return "Ready for your next message.";
}

// Transcript excerpts are reports, not verified outcomes. Never infer success from tool output.
export function currentWork({ items = [], connected = true, fresh = true, busy = false, approval = false } = {}) {
  let start = -1;
  for (let i = items.length - 1; i >= 0; i--) if (items[i].kind === "user") { start = i; break; }
  const recent = start < 0 ? [] : items.slice(start + 1);
  const excerpt = (text) => { const s = String(text || "").replace(/\s+/g, " ").trim(); return s.length > 240 ? s.slice(0, 239) + "…" : s; };
  const report = [...recent].reverse().find(i => i.kind === "assistant" && i.complete && i.text);
  const tools = recent.filter(i => i.kind === "tool" && i.status === "running");
  const streaming = recent.some(i => i.kind === "assistant" && i.streaming && !i.complete);
  let activity = "Idle. No active turn reported.";
  if (!connected || !fresh) activity = "Activity unknown. Waiting for a fresh connection.";
  else if (approval) activity = "Waiting for your approval.";
  else if (busy && tools.length) activity = tools.some(i => /(^|[.:_])delegate$/.test(i.name || ""))
    ? "Delegation tool running. Child progress is not confirmed here."
    : "Tool running: " + tools.map(i => i.name || "unnamed tool").join(", ").slice(0, 160);
  else if (busy && streaming) activity = "Receiving response text.";
  else if (busy) activity = "Turn active. Waiting for the next observed event.";
  return { request: start < 0 ? "No request in this thread yet." : excerpt(items[start].text),
    activity, report: report ? excerpt(report.text) : "No completed agent report since this request." };
}
