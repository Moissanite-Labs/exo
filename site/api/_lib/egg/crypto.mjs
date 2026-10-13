/* Signatures, keyed hashes and feed ids for the x0 functions. Node's crypto only. */
import { createHmac, timingSafeEqual, randomBytes, randomInt } from "node:crypto";

/* Verify "sha256=<hex>" over the raw body, in constant time. Returns false for anything malformed. */
export function verifySignature(secret, rawBody, header) {
  if (!secret || typeof header !== "string") return false;
  const m = /^sha256=([0-9a-f]{64})$/i.exec(header.trim());
  if (!m) return false;
  const want = createHmac("sha256", secret).update(rawBody).digest();
  const got = Buffer.from(m[1].toLowerCase(), "hex");
  return got.length === want.length && timingSafeEqual(got, want);
}

export function sign(secret, rawBody) {
  return "sha256=" + createHmac("sha256", secret).update(rawBody).digest("hex");
}

/* A keyed hash for the private store: numbers and accounts are never stored in the clear. */
export function keyed(key, kind, value) {
  return createHmac("sha256", key || "unset").update(kind + ":" + value).digest("base64url").slice(0, 22);
}

/* An opaque public id: 8 lowercase hex characters, random, so it links to nothing. */
export function feedId(taken) {
  for (;;) {
    const id = randomBytes(4).toString("hex");
    if (!taken || !taken.has(id)) return id;
  }
}

/* Fisher-Yates with a cryptographic source, so the order of validated events says nothing. */
export function shuffle(list) {
  const a = list.slice();
  for (let i = a.length - 1; i > 0; i--) { const j = randomInt(i + 1); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}
