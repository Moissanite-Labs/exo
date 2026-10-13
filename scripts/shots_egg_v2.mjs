#!/usr/bin/env node
/* Screenshots of x0's meditation on a local preview, driven over the Chrome DevTools protocol (no packages).
 *   node scripts/shots_egg_v2.mjs [url] [outdir]
 * Default url http://localhost:8767/?feed=demo. Captures: events (before meditating), mid-meditation (breathing,
 * folded), an absorb glow in flight, and the wake. Chrome is launched headless with a throwaway profile and killed. */
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const URL_ = process.argv[2] || "http://localhost:8767/?feed=demo";
const OUT = process.argv[3] || join(process.env.HOME, "Work/handoffs/exo-ecosystem/egg-v2-shots");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333 + Math.floor(Math.random() * 500);
mkdirSync(OUT, { recursive: true });
const prof = mkdtempSync(join(tmpdir(), "x0shots-"));
const chrome = spawn(CHROME, ["--headless=new", "--use-mock-keychain", "--password-store=basic", "--no-first-run",
  "--no-default-browser-check", "--hide-scrollbars", "--window-size=1400,900", "--remote-debugging-port=" + PORT,
  "--user-data-dir=" + prof, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws, id = 0;
const waiters = new Map();
function send(method, params = {}) {
  return new Promise((res, rej) => { const i = ++id; waiters.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
}
async function evaluate(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true });
  return r.result && r.result.value;
}
async function shot(name, clip) {
  const r = await send("Page.captureScreenshot", Object.assign({ format: "png" }, clip ? { clip: Object.assign({ scale: 1 }, clip) } : {}));
  const f = join(OUT, name + ".png");
  writeFileSync(f, Buffer.from(r.data, "base64"));
  console.log("wrote", f);
}
async function until(expr, ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await evaluate(expr)) return true; await sleep(100); }
  return false;
}
try {
  let targets;
  for (let i = 0; i < 50; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); if (targets.length) break; } catch (e) {}
    await sleep(200);
  }
  const page = targets.find((t) => t.type === "page");
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  ws.addEventListener("message", (m) => {
    const j = JSON.parse(m.data);
    if (j.id && waiters.has(j.id)) { const w = waiters.get(j.id); waiters.delete(j.id); j.error ? w.rej(new Error(j.error.message)) : w.res(j.result); }
  });
  await send("Page.enable"); await send("Runtime.enable");
  await send("Page.navigate", { url: URL_ });
  await until("!!(window.exoEgg && window.exoEgg.meditate)", 15000);
  const box = await evaluate(`(function(){var e=window.exoEgg;return {x:e.cx-e.R*1.15,y:e.cy-e.R*1.15,w:e.R*2.3,h:e.R*2.3};})()`);
  const clip = { x: Math.max(0, box.x), y: Math.max(0, box.y), width: Math.min(1400 - Math.max(0, box.x), box.w), height: Math.min(900 - Math.max(0, box.y), box.h) };
  await until("window.exoEgg.packets.some(function(p){return p.path && !p.cold})", 40000);
  await shot("01-event-packet", clip);
  await until("window.exoEgg.med && window.exoEgg.med.phase==='breathe' && window.exoEgg.breath>0.9", 90000);
  await shot("02-mid-meditation");
  await shot("02b-mid-meditation-egg", clip);
  await until("window.exoEgg.packets.some(function(p){return p.cold && (p.i>=1 || p.hop) && p.p>0.4})", 40000);
  await shot("03-absorb-in-flight", clip);
  await until("window.exoEgg.coreCold>0.9", 20000);
  await shot("04-absorb-reaches-core", clip);
  await shot("04b-absorb-full-page");
  await until("window.exoEgg.med && window.exoEgg.med.phase==='wake'", 120000);
  await sleep(1300);
  await shot("05-waking", clip);
  await until("!window.exoEgg.meditating()", 20000);
  await sleep(1500);
  await shot("06-awake");
  console.log("legend:", await evaluate("[].map.call(document.querySelectorAll('.egg-legend__xp,.egg-legend__live'),function(e){return e.textContent}).join(' | ')"));
} finally {
  try { ws && ws.close(); } catch (e) {}
  chrome.kill("SIGKILL");
  await sleep(300);
  rmSync(prof, { recursive: true, force: true });
}
