/* The only GitHub API calls the functions make, with the read-only EGG_GITHUB_TOKEN (server side only).
 *
 *   rocketReviews(nodeIds)  -> Set of review node ids that carry a ROCKET reaction from the bot's account id
 *   accountCreated(id)      -> ms since epoch the account was created, or null when unknown
 *
 * GitHub sends no webhook for reactions, so they are read at meditation start through GraphQL. The bot is matched
 * by its numeric account id (databaseId), never by login, and both user and app (Bot) reactors are covered.
 */
const GQL = "https://api.github.com/graphql";

export function makeGithub(cfg, fetchImpl = globalThis.fetch) {
  const headers = {
    authorization: "Bearer " + cfg.githubToken,
    "content-type": "application/json",
    accept: "application/vnd.github+json",
    "user-agent": "x0-egg-feed"
  };
  return {
    async rocketReviews(nodeIds) {
      const hit = new Set();
      if (!cfg.githubToken || !cfg.botId || !nodeIds.length) return hit;
      for (let i = 0; i < nodeIds.length; i += 50) {
        const ids = nodeIds.slice(i, i + 50);
        const query = `query($ids:[ID!]!){nodes(ids:$ids){... on PullRequestReview{id reactionGroups{content
          reactors(first:100){nodes{__typename ... on User{databaseId} ... on Bot{databaseId}}}}}}}`;
        const r = await fetchImpl(GQL, { method: "POST", headers, body: JSON.stringify({ query, variables: { ids } }) });
        if (!r.ok) throw new Error("github " + r.status);
        const j = await r.json();
        for (const n of (j.data && j.data.nodes) || []) {
          if (!n || !n.id) continue;
          const g = (n.reactionGroups || []).find((x) => x.content === "ROCKET");
          const who = (g && g.reactors && g.reactors.nodes) || [];
          if (who.some((u) => u && String(u.databaseId) === String(cfg.botId))) hit.add(n.id);
        }
      }
      return hit;
    },
    async accountCreated(id) {
      if (!cfg.githubToken || !/^\d+$/.test(String(id))) return null;
      const r = await fetchImpl("https://api.github.com/user/" + id, { headers });
      if (!r.ok) return null;
      const j = await r.json();
      const t = Date.parse(j.created_at);
      return isNaN(t) ? null : t;
    }
  };
}
