// Small DOM helpers. Everything is built with text nodes, never innerHTML, so model text cannot inject markup.
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const k in (attrs || {})) {
    const v = attrs[k];
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  add(el, kids);
  return el;
}

export function add(el, c) {
  if (c == null || c === false) return;
  if (Array.isArray(c)) { c.forEach((x) => add(el, x)); return; }
  el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
}

export function short(s, n) { s = s == null ? "" : String(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

export function ago(sec) {
  if (!sec) return "never";
  const d = Math.max(0, Date.now() / 1000 - sec);
  return (d < 60 ? Math.round(d) + "s" : d < 3600 ? Math.round(d / 60) + "m" :
    d < 86400 ? Math.round(d / 3600) + "h" : Math.round(d / 86400) + "d") + " ago";
}

export function when(iso) {
  const t = Date.parse(iso || "");
  return isNaN(t) ? "" : ago(t / 1000);
}

// Every request carries the page token (D-039): the server refuses any /api/ call without it.
export function token() { return sessionStorage.getItem("xc-token") || ""; }

// An event stream with the token as a query parameter: a browser EventSource cannot send headers.
export function stream(path) {
  return new EventSource(path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token()));
}

export async function api(method, path, body) {
  const opt = { method, headers: { "X-Exo-Token": token() } };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) {
    const err = new Error(data.error || ("HTTP " + r.status));
    err.status = r.status;
    throw err;
  }
  return data;
}

// Plain text from a protocol message part list.
export function partsText(parts) {
  return (parts || []).filter((p) => p.type === "text").map((p) => p.text || "").join("");
}

export function tag(text, tone) { return h("span", { class: "xc-tag" + (tone ? " xc-t-" + tone : "") }, text); }
export function empty(t) { return h("div", { class: "xc-empty" }, t); }
export function errorBox(t) { return h("div", { class: "xc-error" }, t); }
