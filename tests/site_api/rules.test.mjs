// The anti-gaming caps (docs/EXOPET.md section 3) and the validation rules (docs/LIVE-EGG.md section 3.3).
import { test } from "node:test";
import assert from "node:assert/strict";
import { handleWebhook, handleMeditate, handleFeed } from "../../site/api/_lib/egg/http.mjs";
import { closingRefs, salvagedRefs, isAgentWork } from "../../site/api/_lib/egg/rules.mjs";
import { emptyState, grantInstant } from "../../site/api/_lib/egg/engine.mjs";
import { makeDeps, hookReq, medReq, issue, pr, review, release, user, state, feed, CORE, BOT, T0 } from "./helpers.mjs";

const send = (d, ev, p) => handleWebhook(hookReq(d, ev, p), d);
const alice = user(501, "alice"), bob = user(502, "bob");
const xp = async (d) => (await state(d)).xp;
const pending = async (d) => (await feed(d, handleFeed)).egg.pending;

/* ---------- instant caps ---------- */
test("at most 3 instant XP per account per UTC day; every event still animates", async () => {
  const d = makeDeps();
  for (let n = 1; n <= 5; n++) await send(d, "issues", issue("opened", n, alice));
  assert.equal(await xp(d), 3);
  assert.equal((await state(d)).events.length, 5);
  d.clock.t += 864e5;                                                    // a new UTC day
  await send(d, "issues", issue("opened", 6, alice));
  assert.equal(await xp(d), 4);
});

test("accounts newer than 7 days earn no instant XP but still animate", async () => {
  const d = makeDeps();
  d.ages[alice.id] = T0 - 3 * 864e5;
  await send(d, "issues", issue("opened", 1, alice));
  assert.equal(await xp(d), 0);
  assert.equal((await state(d)).events.length, 1);
  d.ages[bob.id] = T0 - 8 * 864e5;
  await send(d, "issues", issue("opened", 2, bob));
  assert.equal(await xp(d), 1);
});

test("unknown account age fails closed", async () => {
  const d = makeDeps();
  d.github.accountCreated = async () => null;
  await send(d, "issues", issue("opened", 1, alice));
  assert.equal(await xp(d), 0);
});

test("outside instant XP: at most 15 an hour and 60 a day across everyone", async () => {
  const d = makeDeps();
  for (let i = 0; i < 20; i++) await send(d, "issues", issue("opened", i + 1, user(1000 + i)));
  assert.equal(await xp(d), 15);
  let n = 100;
  for (let h = 1; h <= 6; h++) {
    d.clock.t = T0 + h * 3600e3;
    for (let i = 0; i < 20; i++) await send(d, "issues", issue("opened", n++, user(2000 + n)));
  }
  assert.equal(await xp(d), 60);
});

test("outside instant XP over x0's life is capped at a quarter of the target", () => {
  const S = emptyState(), cfg = { target: 2048 };
  S.outsideLife = 511;
  assert.equal(grantInstant(S, cfg, T0, { type: "issue", author: "a", cls: "outside" }), 1);
  assert.equal(grantInstant(S, cfg, T0, { type: "issue", author: "b", cls: "outside" }), 0);
  assert.equal(S.outsideLife, 512);
});

test("merges have no per-account cap; reviews of one's own PR earn nothing", async () => {
  const d = makeDeps();
  for (let n = 1; n <= 4; n++) await send(d, "pull_request", pr("closed", n, alice, { merged: true }));
  assert.equal(await xp(d), 8);
  await send(d, "pull_request_review", review(9, alice, alice, 1));
  assert.equal(await xp(d), 8);
  assert.equal((await state(d)).events.filter((e) => e.type === "review").length, 1);
});

test("core: at most 40 XP a UTC day and 512 over x0's life; releases are outside the cap", async () => {
  const d = makeDeps();
  for (let n = 1; n <= 50; n++) await send(d, "pull_request", pr("opened", n, CORE));
  assert.equal(await xp(d), 40);
  await send(d, "release", release("exo-v1.0.0"));
  assert.equal(await xp(d), 65);
  const S = emptyState(), cfg = { target: 2048 };
  S.coreLife = 510;
  assert.equal(grantInstant(S, cfg, T0, { type: "merge", author: "c", cls: "core" }), 2);
  assert.equal(grantInstant(S, cfg, T0 + 864e5, { type: "merge", author: "c", cls: "core" }), 0);
});

test("core by login works too", async () => {
  const d = makeDeps();
  for (let n = 1; n <= 5; n++) await send(d, "issues", issue("opened", n, user(777, "Core-Two")));
  assert.equal(await xp(d), 5);                                          // core: no per-account 3, only the core cap
});

test("releases: exo-v tags count 25, at most one per 7 days; other tags only animate", async () => {
  const d = makeDeps();
  await send(d, "release", release("exo-v1.0.0"));
  await send(d, "release", release("exo-v1.0.1"));
  await send(d, "release", release("site-v1.2"));
  assert.equal(await xp(d), 25);
  assert.equal((await state(d)).events.filter((e) => e.type === "release").length, 3);
  d.clock.t += 7 * 864e5;
  await send(d, "release", release("exo-v1.1.0"));
  assert.equal(await xp(d), 50);
});

/* ---------- agents ---------- */
test("agent issues earn nothing and never validate; agent PRs validate when merged", async () => {
  const d = makeDeps();
  await send(d, "issues", issue("opened", 1, CORE, { labels: [{ name: "agent" }] }));
  await send(d, "pull_request", pr("opened", 2, CORE, { body: "Change\n\nCo-Authored-By: Claude <noreply@anthropic.com>" }));
  assert.equal(await xp(d), 0);
  await send(d, "pull_request", pr("closed", 2, CORE, { merged: true, body: "Fixes #1\n\nCo-Authored-By: Claude <noreply@anthropic.com>" }));
  assert.equal(await xp(d), 2);                                          // the merge counts, inside the core cap
  assert.equal(await pending(d), 10);                                    // the PR validates; the agent's issue does not
});

test("agent markers: label, attribution line; a plain body is not agent work", () => {
  assert.equal(isAgentWork([{ name: "Agent" }], ""), true);
  assert.equal(isAgentWork([], "x\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>"), true);
  assert.equal(isAgentWork([], "x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"), true);
  assert.equal(isAgentWork([], "Fixes #3\nCo-Authored-By: Jane Doe <jane@example.com>"), false);
  assert.equal(isAgentWork([], "Generated with care is not the last line\nthanks"), false);
});

/* ---------- validation rules ---------- */
test("closing keywords: any case, optional colon, same repo only, deduplicated", () => {
  const t = "fixes #1, Closes: #2 and RESOLVED aska-digital/exo#3; fix other/repo#4; closes #1; prefix#5";
  assert.deepEqual(closingRefs(t, "aska-digital/exo"), [1, 2, 3]);
  assert.deepEqual(closingRefs("refs #9", "aska-digital/exo"), []);
});

test("Salvaged-from lines: one per line, any case, at most 3", () => {
  assert.deepEqual(salvagedRefs("x\nSalvaged-from: #4\nsalvaged-FROM:#5\nSalvaged-from: #6\nSalvaged-from: #7\ninline Salvaged-from: #8"), [4, 5, 6]);
});

test("a merged PR validates itself and the issues it fixes (at most 3, opened before the merge)", async () => {
  const d = makeDeps();
  for (let n = 1; n <= 5; n++) await send(d, "issues", issue("opened", n, user(600 + n)));
  d.clock.t += 3600e3;
  await send(d, "pull_request", pr("closed", 20, alice, { merged: true, mergedAt: d.clock.t, body: "Fixes #1\nfixes #2\nCloses #3\nresolves #4\nFixes #99" }));
  assert.equal(await pending(d), 10 + 3 * 4);
  const S = await state(d);
  assert.equal(S.queue.filter((q) => q.kind === "issue").length, 3);
});

test("an issue opened after the merge is not validated by it", async () => {
  const d = makeDeps();
  await send(d, "pull_request", pr("closed", 20, alice, { merged: true, mergedAt: T0 - 60e3, body: "Fixes #1" }));
  await send(d, "issues", issue("opened", 1, bob));
  await send(d, "pull_request", pr("edited", 20, alice, { merged: true, mergedAt: T0 - 60e3, body: "Fixes #1" }));
  assert.equal(await pending(d), 10);
});

test("a spam-labelled issue never validates; nor one its author closed within the hour", async () => {
  const d = makeDeps();
  await send(d, "issues", issue("opened", 1, alice));
  await send(d, "issues", issue("labeled", 1, alice, { label: { name: "spam" }, labels: [{ name: "spam" }], sender: CORE }));
  await send(d, "issues", issue("opened", 2, bob));
  d.clock.t += 600e3;
  await send(d, "issues", issue("closed", 2, bob));
  d.clock.t += 600e3;
  await send(d, "pull_request", pr("closed", 30, CORE, { merged: true, mergedAt: d.clock.t, body: "Fixes #1\nFixes #2" }));
  assert.equal(await pending(d), 10);
});

test("duplicate, deleted and transferred issues are void", async () => {
  const d = makeDeps();
  await send(d, "issues", issue("opened", 1, alice));
  await send(d, "issues", issue("opened", 2, bob));
  await send(d, "issues", issue("opened", 3, user(503)));
  await send(d, "issues", issue("closed", 1, alice, { labels: [{ name: "duplicate" }], sender: CORE }));
  await send(d, "issues", issue("deleted", 2, bob, { sender: CORE }));
  await send(d, "issues", issue("transferred", 3, user(503), { sender: CORE }));
  d.clock.t += 60e3;
  await send(d, "pull_request", pr("closed", 30, CORE, { merged: true, mergedAt: d.clock.t, body: "Fixes #1 fixes #2 fixes #3" }));
  assert.equal(await pending(d), 10);
});

test("Salvaged-from: a PR closed without merge by another author validates; the same author does not", async () => {
  const d = makeDeps();
  await send(d, "pull_request", pr("opened", 5, alice));
  await send(d, "pull_request", pr("opened", 6, bob));
  d.clock.t += 2 * 3600e3;
  await send(d, "pull_request", pr("closed", 5, alice, { sender: CORE }));
  await send(d, "pull_request", pr("closed", 6, bob, { sender: CORE }));
  await send(d, "pull_request", pr("closed", 7, bob, { merged: true, mergedAt: d.clock.t, body: "Lands it.\nSalvaged-from: #5\nSalvaged-from: #6" }));
  assert.equal(await pending(d), 10 + 10);                               // #7 and #5; #6 is bob's own
});

test("edits within 7 days of the merge can add Fixes lines; later edits cannot", async () => {
  const d = makeDeps();
  await send(d, "issues", issue("opened", 1, alice));
  await send(d, "issues", issue("opened", 2, bob));
  d.clock.t += 60e3;
  const merged = d.clock.t;
  await send(d, "pull_request", pr("closed", 9, CORE, { merged: true, mergedAt: merged, body: "no refs" }));
  d.clock.t += 6 * 864e5;
  await send(d, "pull_request", pr("edited", 9, CORE, { merged: true, mergedAt: merged, body: "Fixes #1" }));
  assert.equal(await pending(d), 14);
  d.clock.t += 2 * 864e5;
  await send(d, "pull_request", pr("edited", 9, CORE, { merged: true, mergedAt: merged, body: "Fixes #1\nFixes #2" }));
  assert.equal(await pending(d), 14);
});

test("reviews validate only with the bot's rocket, once per account per PR, 3 per account per day", async () => {
  const d = makeDeps();
  await send(d, "pull_request_review", review(1, CORE, alice, 11));
  await send(d, "pull_request_review", review(1, CORE, alice, 12));      // second review on the same PR
  await send(d, "pull_request_review", review(2, CORE, bob, 13));        // no rocket from the bot
  for (let i = 0; i < 4; i++) await send(d, "pull_request_review", review(10 + i, CORE, user(503), 20 + i));
  ["PRR_11", "PRR_12", "PRR_20", "PRR_21", "PRR_22", "PRR_23"].forEach((n) => d.rockets.add(n));
  const r = await handleMeditate(medReq(d, { action: "start" }), d);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).total, 1 + 3);
});

test("a review by an agent or on one's own PR never validates", async () => {
  const d = makeDeps();
  await send(d, "pull_request_review", review(1, alice, alice, 31));
  await send(d, "pull_request_review", review(2, CORE, bob, 32, { body: "ok\n\nGenerated with Exo" }));
  d.rockets.add("PRR_31"); d.rockets.add("PRR_32");
  const r = await handleMeditate(medReq(d, { action: "start" }), d);
  assert.equal((await r.json()).total, 0);
});

test("core validated XP is capped at meditation, dated to its validation day", async () => {
  const d = makeDeps();
  for (let n = 1; n <= 38; n++) await send(d, "pull_request", pr("opened", n, CORE));   // 38 core XP today
  await send(d, "pull_request", pr("closed", 1, CORE, { merged: true }));              // +2 instant: 40, queues 10
  assert.equal(await xp(d), 40);
  const r = await handleMeditate(medReq(d, { action: "start" }), d);
  assert.equal((await r.json()).total, 1);
  d.clock.t += 60e3;
  const f = await feed(d, handleFeed);
  assert.equal(f.egg.xp, 40);                                             // validated on a full core day: 0 added
  assert.equal(f.events.at(-1).weight, 10);                               // the nominal weight is still shown
});

test("ion's GraphQL check matches the bot by account id, from users or apps", async () => {
  const { makeGithub } = await import("../../site/api/_lib/egg/github.mjs");
  let seen = null;
  const fake = async (url, init) => {
    seen = { url, init };
    return new Response(JSON.stringify({ data: { nodes: [
      { id: "R1", reactionGroups: [{ content: "ROCKET", reactors: { nodes: [{ __typename: "User", databaseId: 999 }] } }] },
      { id: "R2", reactionGroups: [{ content: "ROCKET", reactors: { nodes: [{ __typename: "User", databaseId: 100 }] } },
        { content: "HEART", reactors: { nodes: [{ __typename: "Bot", databaseId: 999 }] } }] },
      { id: "R3", reactionGroups: [{ content: "ROCKET", reactors: { nodes: [{ __typename: "Bot", databaseId: 999 }] } }] },
      null
    ] } }), { status: 200 });
  };
  const gh = makeGithub({ githubToken: "t", botId: String(BOT.id) }, fake);
  const hit = await gh.rocketReviews(["R1", "R2", "R3", "R4"]);
  assert.deepEqual([...hit].sort(), ["R1", "R3"]);
  assert.equal(seen.url, "https://api.github.com/graphql");
  assert.equal(JSON.parse(seen.init.body).variables.ids.length, 4);
  assert.equal(await makeGithub({ githubToken: "", botId: "1" }, fake).rocketReviews(["R1"]).then((s) => s.size), 0);
});
