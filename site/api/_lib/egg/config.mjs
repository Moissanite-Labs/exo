/* Configuration for the x0 functions (docs/LIVE-EGG.md section 3). Everything comes from the environment;
 * nothing here is a secret and no value is ever logged. Files under api/_lib are not routes on Vercel.
 *
 *   EGG_WEBHOOK_SECRET    GitHub webhook secret (HMAC-SHA256, X-Hub-Signature-256)
 *   EGG_MEDITATE_SECRET   secret the x0-meditate workflow signs with (X-Egg-Signature-256)
 *   EGG_HASH_KEY          key for the private store's hashes of numbers and accounts
 *   EGG_REPO_ID           numeric id of the one repository accepted
 *   EGG_REPO              owner/name of that repository (for "owner/name#N" closing references)
 *   EGG_CORE_ACCOUNTS     comma-separated GitHub account ids (preferred: survive renames) or logins of the core
 *                         team and the agents it runs. A setting, never a file in the repo.
 *   EGG_REVIEWER_BOT_ID   numeric GitHub account id of ion, the org's review bot
 *   EGG_GITHUB_TOKEN      fine-grained, read-only token (pull requests: read, metadata: read), server side only
 *   CRON_SECRET           Vercel cron bearer secret for /api/egg/settle
 *   EGG_TARGET            XP at which x0 cracks (default 2048)
 *   EGG_STORE             "upstash" (default when a REST URL is set) or "memory" (tests and local runs only)
 *   KV_REST_API_URL, KV_REST_API_TOKEN            the Upstash Redis REST endpoint (Vercel Marketplace names), or
 *   UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN
 */
export function readConfig(env = process.env) {
  const ids = (s) => new Set(String(s || "").split(",").map((x) => x.trim()).filter((x) => /^\d+$/.test(x)));
  return {
    webhookSecret: env.EGG_WEBHOOK_SECRET || "",
    meditateSecret: env.EGG_MEDITATE_SECRET || "",
    hashKey: env.EGG_HASH_KEY || "",
    repoId: String(env.EGG_REPO_ID || ""),
    repo: String(env.EGG_REPO || "").toLowerCase(),
    core: ids(env.EGG_CORE_ACCOUNTS),
    coreLogins: new Set(String(env.EGG_CORE_ACCOUNTS || "").split(",").map((x) => x.trim().toLowerCase())
      .filter((x) => x && !/^\d+$/.test(x))),
    botId: String(env.EGG_REVIEWER_BOT_ID || ""),
    githubToken: env.EGG_GITHUB_TOKEN || "",
    cronSecret: env.CRON_SECRET || "",
    target: Math.max(1, parseInt(env.EGG_TARGET || "2048", 10) || 2048),
    store: env.EGG_STORE || "",
    kvUrl: env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL || "",
    kvToken: env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN || ""
  };
}
