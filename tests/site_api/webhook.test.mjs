// Signature verification, replay, size, repository, event filter and bots for POST /api/egg/webhook.
import { test } from "node:test";
import assert from "node:assert/strict";
import { handleWebhook, handleFeed } from "../../site/api/_lib/egg/http.mjs";
import { verifySignature, sign } from "../../site/api/_lib/egg/crypto.mjs";
import { makeDeps, hookReq, issue, user, state, feed, BOT, REPO_ID } from "./helpers.mjs";

const alice = user(501, "alice");

test("verifySignature: good, wrong secret, tampered body, malformed headers", () => {
  const body = Buffer.from('{"a":1}');
  const good = sign("s3cret", body);
  assert.equal(verifySignature("s3cret", body, good), true);
  assert.equal(verifySignature("s3cret", body, good.toUpperCase().replace("SHA256", "sha256")), true);
  assert.equal(verifySignature("other", body, good), false);
  assert.equal(verifySignature("s3cret", Buffer.from('{"a":2}'), good), false);
  assert.equal(verifySignature("s3cret", body, good.slice(0, -2)), false);
  assert.equal(verifySignature("s3cret", body, "sha1=" + good.slice(7)), false);
  assert.equal(verifySignature("s3cret", body, undefined), false);
  assert.equal(verifySignature("", body, good), false);
});

test("a good signature is accepted and the event reaches the feed", async () => {
  const d = makeDeps();
  const r = await handleWebhook(hookReq(d, "issues", issue("opened", 1, alice)), d);
  assert.equal(r.status, 200);
  const f = await feed(d, handleFeed);
  assert.equal(f.events.length, 1);
  assert.equal(f.events[0].type, "issue");
  assert.equal(f.egg.xp, 1);
});

test("a bad or missing signature gets 401 and stores nothing", async () => {
  const d = makeDeps();
  const bad = await handleWebhook(hookReq(d, "issues", issue("opened", 1, alice), { sig: sign("wrong", "{}") }), d);
  assert.equal(bad.status, 401);
  const none = await handleWebhook(hookReq(d, "issues", issue("opened", 2, alice), { sig: null }), d);
  assert.equal(none.status, 401);
  assert.equal(await d.store.get("egg:state"), null);
  assert.equal(await d.store.get("egg:count:hook-401"), "2");
});

test("a signature is checked before JSON is parsed", async () => {
  const d = makeDeps();
  const r = await handleWebhook(hookReq(d, "issues", "not json", { sig: sign("wrong", "not json") }), d);
  assert.equal(r.status, 401);
  const r2 = await handleWebhook(hookReq(d, "issues", "not json"), d);
  assert.equal(r2.status, 400);
});

test("a replayed delivery id is acknowledged and ignored", async () => {
  const d = makeDeps();
  const p = issue("opened", 1, alice);
  assert.equal((await handleWebhook(hookReq(d, "issues", p, { delivery: "same-id" }), d)).status, 200);
  const again = await handleWebhook(hookReq(d, "issues", issue("opened", 2, alice), { delivery: "same-id" }), d);
  assert.equal(again.status, 200);
  assert.equal((await again.json()).duplicate, true);
  assert.equal((await state(d)).events.length, 1);
  d.clock.t += 49 * 3600e3;                                            // the delivery id is kept for 48 hours
  assert.equal((await handleWebhook(hookReq(d, "issues", issue("opened", 3, alice), { delivery: "same-id" }), d)).status, 200);
  assert.equal((await state(d)).events.length, 2);
});

test("bodies over 1 MB get 413", async () => {
  const d = makeDeps();
  const p = issue("opened", 1, alice, { body: "x".repeat((1 << 20) + 10) });
  assert.equal((await handleWebhook(hookReq(d, "issues", p), d)).status, 413);
});

test("only the configured repository is accepted", async () => {
  const d = makeDeps();
  const p = issue("opened", 1, alice); p.repository = { id: REPO_ID + 1 };
  assert.equal((await handleWebhook(hookReq(d, "issues", p), d)).status, 403);
});

test("events outside the list get 204; ping gets 200", async () => {
  const d = makeDeps();
  assert.equal((await handleWebhook(hookReq(d, "push", { repository: { id: REPO_ID }, sender: alice }), d)).status, 204);
  assert.equal((await handleWebhook(hookReq(d, "issues", issue("reopened", 1, alice)), d)).status, 204);
  assert.equal((await handleWebhook(hookReq(d, "ping", { repository: { id: REPO_ID }, zen: "hi" }), d)).status, 200);
});

test("bots count nothing and do not animate, including ion by its account id", async () => {
  const d = makeDeps();
  const appBot = { id: 77, login: "dependabot[bot]", type: "Bot" };
  for (const [i, who] of [appBot, BOT, { id: 78, login: "helper[bot]", type: "User" }].entries()) {
    const r = await handleWebhook(hookReq(d, "issues", issue("opened", 10 + i, who)), d);
    assert.equal(r.status, 200);
  }
  const f = await feed(d, handleFeed);
  assert.equal(f.events.length, 0);
  assert.equal(f.egg.xp, 0);
});

test("without a secret or a store the hook answers 503", async () => {
  const d = makeDeps({ env: { EGG_WEBHOOK_SECRET: "" } });
  assert.equal((await handleWebhook(hookReq(d, "issues", issue("opened", 1, alice)), d)).status, 503);
  const d2 = makeDeps({ store: null });
  assert.equal((await handleWebhook(hookReq(d2, "issues", issue("opened", 1, alice)), d2)).status, 503);
});
