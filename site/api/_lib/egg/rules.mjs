/* The XP rules for x0 (D-058, docs/EXOPET.md section 3) and the detection rules for validation
 * (docs/LIVE-EGG.md section 3.3). Pure functions and constants; no I/O. */

export const INSTANT = { issue: 1, pr: 1, merge: 2, review: 1, release: 25 };
export const VALIDATED = { issue: 4, pr: 10, review: 4 };
export const LEVEL = { issue: 1, pr: 2, review: 2, merge: 3, release: 4 };
export const VALIDATED_LEVEL = { issue: 1, pr: 2, review: 2 };

export const CAPS = {
  accountDay: 3,              // instant XP per account per UTC day, across issues, opened PRs and reviews
  newAccountDays: 7,          // accounts younger than this earn no instant XP
  outsideHour: 15,            // outside instant XP per hour, across everyone
  outsideDay: 60,             // outside instant XP per UTC day, across everyone
  outsideLifeShare: 0.25,     // outside instant XP over x0's life: a quarter of the target
  coreDay: 40,                // core XP (instant and validated) per UTC day
  coreLifeShare: 0.25,        // core XP over x0's life: a quarter of the target
  reviewsPerAccountDay: 3,    // validated reviews per account per UTC day
  reviewsPerAccountPr: 1,     // validated reviews per account per pull request
  issuesPerMerge: 3,          // issues one merged pull request can validate
  salvagesPerMerge: 3,        // salvaged pull requests one merged pull request can validate
  releaseEveryDays: 7,        // at most one release counts per 7 days
  editWindowDays: 7,          // a merged pull request's body may add "Fixes" or "Salvaged-from" lines for 7 days
  reviewWindowDays: 30,       // reviews older than this are no longer candidates
  voidOwnCloseMs: 3600e3,     // closed by its own author within an hour: void
  publicPerHour: 600,         // public events per hour written to the feed
  feedKeep: 500,              // public events kept
  feedKeepMs: 7 * 864e5,      // and for at most 7 days
  backstopDays: 14            // validated XP waiting this long starts a meditation from the daily cron
};

export const VOID_LABELS = new Set(["spam", "invalid", "duplicate"]);
export const AGENT_LABEL = "agent";

export const DAY_MS = 864e5;
export function utcDay(ms) { return new Date(ms).toISOString().slice(0, 10); }
export function utcHour(ms) { return new Date(ms).toISOString().slice(0, 13); }
export function iso(ms) { return new Date(Math.floor(ms / 1000) * 1000).toISOString().replace(/\.\d{3}Z$/, "Z"); }

export function isBot(user, botId) {
  if (!user) return true;
  if (user.type === "Bot") return true;
  if (typeof user.login === "string" && /\[bot\]$/i.test(user.login)) return true;
  return !!botId && String(user.id) === String(botId);
}

/* "Core" is a setting: the founders, maintainers and the agents they run, by account id or login. */
export function isCore(user, cfg) {
  if (!user) return false;
  return cfg.core.has(String(user.id)) || cfg.coreLogins.has(String(user.login || "").toLowerCase());
}

/* Agent work carries a marker: the `agent` label, or a body whose last line is an agent attribution
   (a Co-Authored-By line naming a known agent, or a "Generated with" line). */
const AGENT_LINE = /^(co-authored-by:\s*.*\b(claude|codex|copilot|gpt|gemini|exo|agent|bot)\b.*|(\W+\s*)?generated (with|by)\b.*|agent:\s*\S.*)$/i;
export function isAgentWork(labels, body) {
  if ((labels || []).some((l) => String(l && l.name || l).toLowerCase() === AGENT_LABEL)) return true;
  const lines = String(body || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  return lines.length > 0 && AGENT_LINE.test(lines[lines.length - 1]);
}

export function hasVoidLabel(labels) {
  return (labels || []).some((l) => VOID_LABELS.has(String(l && l.name || l).toLowerCase()));
}

/* GitHub's closing keywords, any case, optional colon, then #N or owner/name#N. Returns numbers in order,
   deduplicated. References to another repository are skipped. */
export function closingRefs(text, repo) {
  const re = /\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+([\w.-]+\/[\w.-]+)?#(\d+)\b/gi;
  const out = [];
  let m;
  while ((m = re.exec(String(text || "")))) {
    if (m[2] && (!repo || m[2].toLowerCase() !== repo)) continue;
    const n = +m[3];
    if (!out.includes(n)) out.push(n);
  }
  return out;
}

/* One "Salvaged-from: #N" per line, any case. At most 3 lines are read. */
export function salvagedRefs(body) {
  const out = [];
  for (const line of String(body || "").split(/\r?\n/)) {
    const m = /^\s*salvaged-from\s*:\s*#(\d+)\s*$/i.exec(line);
    if (m && !out.includes(+m[1])) out.push(+m[1]);
    if (out.length >= CAPS.salvagesPerMerge) break;
  }
  return out;
}

/* Stage from XP (docs/LIVE-EGG.md section 3.6). */
export function stageFor(state, xp, target, now, crackAt) {
  if (state === "hatched") return 1;
  if (state === "cracking") return round3(0.8 + 0.15 * Math.min(1, Math.max(0, (now - crackAt) / DAY_MS)));
  return round3(0.8 * Math.min(1, Math.max(0, xp / target)));
}
export function round3(x) { return Math.round(x * 1000) / 1000; }
