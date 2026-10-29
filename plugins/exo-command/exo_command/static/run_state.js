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
