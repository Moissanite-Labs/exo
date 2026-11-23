// Completion logic for the web composer (D-037). Pure functions, no DOM, no imports, so a test can run them in
// node. They follow src/exo/complete.py: the same ranking, the same argument slots, the same guidance rules.
// A menu that opens on "/" and ghost text from history were first seen in the Hermes CLI; this is our own build.

// Lower is better, null means no match: exact, prefix, word start, contains, letters in order.
export function score(query, text) {
  const q = query.toLowerCase(), t = text.toLowerCase();
  if (!q || t === q) return 0;
  if (t.startsWith(q)) return 1 + t.length - q.length;
  for (const m of t.matchAll(/[-_./: ]/g)) if (t.startsWith(q, m.index + 1)) return 200 + m.index + 1;
  const pos = t.indexOf(q);
  if (pos >= 0) return 400 + pos;
  let at = -1, gaps = 0;
  for (const ch of q) {
    const nxt = t.indexOf(ch, at + 1);
    if (nxt < 0) return null;
    gaps += at >= 0 ? nxt - at - 1 : nxt;
    at = nxt;
  }
  return 1000 + gaps;
}

export function rank(query, items, limit = 40) {
  const out = [];
  items.forEach((it, i) => {
    let s = score(query, it.label);
    if (s === null && it.meta && query.length > 2) {
      const ms = score(query, it.meta);
      s = ms === null || ms >= 1000 ? null : 2000 + ms;
    }
    if (s !== null) out.push([s, i, it]);
  });
  out.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return out.slice(0, limit).map((x) => x[2]);
}

// difflib.SequenceMatcher(None, a, b).ratio(): twice the letters in the matching blocks over both lengths.
function ratio(a, b) {
  const matched = (alo, ahi, blo, bhi) => {   // the longest match (earliest in a, then in b), then each side of it
    let bi = alo, bj = blo, k = 0;
    for (let i = alo; i < ahi; i++) for (let j = blo; j < bhi; j++) {
      let n = 0;
      while (i + n < ahi && j + n < bhi && a[i + n] === b[j + n]) n++;
      if (n > k) { bi = i; bj = j; k = n; }
    }
    return k ? k + matched(alo, bi, blo, bj) + matched(bi + k, ahi, bj + k, bhi) : 0;
  };
  return a.length + b.length ? 2 * matched(0, a.length, 0, b.length) / (a.length + b.length) : 1;
}

// The command a mistyped /name most likely meant, or null: cm.closest, so the page and the TUI hold the same lines.
// A prefix, else difflib.get_close_matches(name, names, 1, 0.6), else the one name that contains it.
export function closest(name, names) {
  const pool = [...new Set(names)].sort();
  name = name.toLowerCase();
  if (!name || pool.includes(name)) return null;
  const pre = pool.filter((n) => n.startsWith(name)).sort((a, b) => a.length - b.length);
  if (pre.length) return pre[0];
  let best = null, bestR = 0.6;
  for (const n of pool) {
    const r = ratio(n, name);
    if (r > bestR || (r === bestR && (best === null || n > best))) { best = n; bestR = r; }
  }
  if (best) return best;
  const sub = pool.filter((n) => name.length >= 3 && score(name, n) !== null && score(name, n) < 1000);
  return sub.length === 1 ? sub[0] : null;
}

// What to show for composer text. cmds = [{name, description, args, choices:[{value, meta}]}].
// Returns {mode: "command"|"arg"|"file"|"shell"|"", items, start, hint}.
export function suggest(text, cmds, wantFiles) {
  const none = { mode: "", items: [], start: 0, hint: "" };
  if (text.includes("\n")) return none;
  if (text.startsWith("!")) return { mode: "shell", items: [], start: 0, hint: "! asks the agent to run this in its shell tool and show the output; the gate may ask first" };
  const at = /(?:^|\s)(@\S*)$/.exec(text);
  if (at && wantFiles && !text.startsWith("/")) return { mode: "file", items: [], start: at.index + at[0].length - at[1].length, hint: "", query: at[1] };
  if (!text.startsWith("/")) return none;
  const sp = text.indexOf(" ");
  if (sp < 0) {
    const rows = cmds.map((c) => ({ value: c.name, label: "/" + c.name, meta: c.description, args: c.args }));
    return { mode: "command", items: rank(text.slice(1), rows), start: 0, hint: "" };
  }
  const cmd = text.slice(1, sp).toLowerCase(), spec = cmds.find((c) => c.name === cmd);
  const words = text.slice(sp + 1).split(" ");
  const partial = words[words.length - 1], done = words.slice(0, -1).filter(Boolean);
  const start = text.length - partial.length;
  if (!spec) return { mode: "arg", items: [], start, hint: "" };
  const pool = done.length ? [] : (spec.choices || []).map((c) => ({ value: c.value, label: c.value, meta: c.meta || "" }));
  return { mode: "arg", items: rank(partial, pool), start, hint: partial || done.length ? "" : spec.args || "" };
}

// The composer text after taking an item.
export function completeText(text, sug, item) {
  const head = text.slice(0, sug.start);
  if (sug.mode === "command") return head + "/" + item.value + " ";
  if (sug.mode === "file") return head + item.value + (item.value.endsWith("/") ? "" : " ");
  return head + item.value;
}

// Dim text after the caret: history first, then the top menu item that continues the typing, then the arg shape.
export function ghost(text, sug, history) {
  if (text && !text.includes("\n")) {
    for (let i = history.length - 1; i >= 0; i--) {
      const e = history[i];
      if (e.length > text.length && e.startsWith(text) && !e.includes("\n")) return { text: e.slice(text.length), ok: true };
    }
  }
  if (sug.mode === "command" && sug.items.length && text.startsWith("/") && !text.includes(" ")) {
    const top = sug.items[0].value;
    if (top.startsWith(text.slice(1).toLowerCase()) && top !== text.slice(1).toLowerCase()) return { text: top.slice(text.length - 1), ok: true };
  }
  if (sug.mode === "arg" && sug.hint && text.endsWith(" ")) return { text: sug.hint, ok: false };
  return { text: "", ok: false };
}

// #756: what a recorded model error is evidence of. "auth" only when the text reads as an authentication or
// permission failure, "transport" for a network, stream or timeout failure, "" when the text says nothing we can
// read. The guide never sends someone to their keys on a connection error. #855: the router words its own refusals
// ("rejected the key", "refused the login", "no credentials for role"), a status counts only as `HTTP 401` or `HTTP 403`
// (so `HTTP 404: model gpt-403` and a proxy's 407 are not the keys), and httpx names its timeouts `ReadTimeout`.
const AUTH_ERROR = /\bHTTP\s+40[13]\b|\berror code:?\s*40[13]\b|authentication[ _]?(?:error|failed|failure)|permission[_ ]?denied|unauthori[sz]ed|invalid[_ -]?api[_ -]?key|incorrect api key|invalid x-api-key|api key (?:is )?(?:invalid|missing|required|not set)|rejected the key|refused the login|no credentials for role/i;
const TRANSPORT_ERROR = /\b(?:read|connect|write|pool|remoteprotocol)error\b|\b(?:read|connect|write|pool)?timeout\b|\btimed? ?out\b|stream ended without a response|\beconnreset\b|\beconnrefused\b|\beconnaborted\b|\beai_again\b|network|unreachable|connection (?:refused|reset|closed|aborted)|peer closed|closed the connection|no route to host/i;

export function providerErrorKind(error) {
  const text = typeof error === "string" ? error : "";
  if (!text) return "";
  if (AUTH_ERROR.test(text)) return "auth";
  if (TRANSPORT_ERROR.test(text)) return "transport";
  return "other";
}

// #855: the text to classify for one model.call body. A failure before any output is retried, and while the route
// cools down the router answers "all routes for 'main' failed or are cooling down", so the cause (a ReadError, a 401)
// is only in the attempts. "" when the call did not fail.
export function providerErrorText(call) {
  if (!call || !call.error) return "";
  const tries = Array.isArray(call.attempts) ? call.attempts.map((a) => (a && typeof a.error === "string" ? a.error : "")) : [];
  return [String(call.error), ...tries].filter(Boolean).join("\n");
}

// One dim line, local rules only. At most one new tip per `gap` ms, each rule twice a session, nothing while busy.
export class Guide {
  constructor(opts = {}) {
    this.enabled = opts.enabled !== false;
    this.gap = opts.gap || 25000;
    this.manyTurns = opts.manyTurns || 12;
    this.clock = opts.clock || (() => Date.now());
    this.shown = {}; this.retired = new Set(); this.lastAt = null; this.lastKey = "";
  }
  rules(st) {
    const out = [];
    // The suggestion follows the error itself: a refusal can be the key, a transport failure cannot (chat.js passes the text).
    const bad = st.providerError ? providerErrorKind(providerErrorText({ error: st.providerError, attempts: st.providerAttempts })) || "other" : "";
    // Each kind keeps its own twice-a-session count (`rule`), so two transport failures do not use up a refusal's line.
    if (bad === "auth") out.push({ key: "providers:auth", rule: "providers:auth", text: "that model call was refused: check your keys on the Providers page", fill: "" });
    if (bad === "transport") out.push({ key: "providers:transport", rule: "providers:transport", text: "the model could not be reached: send it again, or /trace", fill: "/trace" });
    if (bad === "other") out.push({ key: "providers:error", rule: "providers:error", text: "that model call failed: /trace shows the error", fill: "/trace" });
    if (st.jobDone) out.push({ key: "job:" + st.jobDone, text: "job " + st.jobDone + " finished: ask for its job_output", fill: "show me the job_output for " + st.jobDone });
    if (st.findings) out.push({ key: "findings", text: st.findings + " open finding(s): /findings", fill: "/findings" });
    if (st.longTurn) out.push({ key: "trace", text: "that turn was slow: /trace shows where the time went", fill: "/trace" });
    if (st.turns >= this.manyTurns) out.push({ key: "context", text: "a long thread: /context shows what the next call carries", fill: "/context" });
    return out;
  }
  used(cmd) { if (["findings", "trace", "context"].includes(cmd)) this.retired.add(cmd); }
  pick(st) {
    if (!this.enabled || st.busy) return null;
    const now = this.clock();
    if (this.lastAt !== null && now - this.lastAt < this.gap) return null;
    for (const tip of this.rules(st)) {
      const rule = tip.rule || tip.key.split(":")[0];
      if (this.retired.has(tip.key) || tip.key === this.lastKey || (this.shown[rule] || 0) >= 2) continue;
      this.shown[rule] = (this.shown[rule] || 0) + 1;
      this.lastAt = now; this.lastKey = tip.key;
      return tip;
    }
    return null;
  }
}
