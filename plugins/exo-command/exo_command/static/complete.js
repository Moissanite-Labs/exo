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

function edits(a, b) {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++)
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
  return d[a.length][b.length];
}

// The command a mistyped /name most likely meant, or null.
export function closest(name, names) {
  name = name.toLowerCase();
  if (!name || names.includes(name)) return null;
  const pre = names.filter((n) => n.startsWith(name)).sort((a, b) => a.length - b.length);
  if (pre.length) return pre[0];
  let best = null, bestD = 99;
  for (const n of names) {
    const d = edits(name, n);
    if (d < bestD) { best = n; bestD = d; }
  }
  return best && bestD <= Math.max(1, Math.ceil(Math.max(name.length, best.length) / 3)) ? best : null;
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
    if (st.providerError) out.push({ key: "providers", text: "that model call failed: check your keys on the Providers page", fill: "" });
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
      const rule = tip.key.split(":")[0];
      if (this.retired.has(tip.key) || tip.key === this.lastKey || (this.shown[rule] || 0) >= 2) continue;
      this.shown[rule] = (this.shown[rule] || 0) + 1;
      this.lastAt = now; this.lastKey = tip.key;
      return tip;
    }
    return null;
  }
}
