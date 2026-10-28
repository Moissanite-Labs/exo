// Small DOM helpers. Everything is built with text nodes, never innerHTML, so model text cannot inject markup.
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const k in (attrs || {})) {
    const v = attrs[k];
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "style") el.style.cssText = v;   // through the CSSOM: the CSP (#162) refuses style attributes
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

// Trade the one-time launch code `exo web` opened the browser on for the page token (#166). The only request without
// the token; any failure gives no token, with the server's reason when it sent one.
export async function redeem(code) {
  try {
    const r = await fetch("/api/launch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    const data = await r.json();
    return r.ok && typeof data.token === "string" ? { token: data.token } : { error: String(data.error || "") };
  } catch (e) { return { error: "" }; }
}

export async function api(method, path, body, signal) {
  const opt = { method, headers: { "X-Exo-Token": token() }, ...(signal ? { signal } : {}) };
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

// ---------- sub-page pieces (D-061): the site's section head, a designed empty state, status in form ----------
// A page head: a mono eyebrow, the title, one line of purpose, and an optional readout or controls on the right.
export function pageHead(o) {
  return h("header", { class: "xc-phead" },
    h("div", { class: "xc-phead-main" }, h("p", { class: "xc-eyebrow" }, o.eyebrow),
      h("h1", { class: "xc-ptitle" }, o.title), o.lede ? h("p", { class: "xc-plede" }, o.lede) : null),
    o.aside ? h("div", { class: "xc-phead-aside" }, o.aside) : null);
}

// A readout like the site hero's: label over value, an optional status led before the value. rows: [label, value, state]
export function readout(rows) {
  return h("dl", { class: "xc-readout" }, rows.filter(Boolean).map((r) => h("div", null, h("dt", null, r[0]),
    h("dd", null, r[2] ? h("span", { class: "xc-led", "data-s": r[2], "aria-hidden": "true" }) : null, h("span", { class: "xc-rv" }, r[1])))));
}

// A status pill. The state is carried by the led's form (filled, hollow, dashed, crossed, barred) and by the word,
// never by colour alone. States: run, queue, done, fail, stop, on, off, ok, known, pause, warn, alert.
export function pill(label, state) { return h("span", { class: "xc-st", "data-s": state || "off" }, label); }

// An empty state that says what will appear here and how to start. `mark` is an optional element (the egg).
export function emptyState(o) {
  return h("div", { class: "xc-void" + (o.mark ? " xc-void-has-mark" : ""), role: "status" }, o.mark || null,
    h("div", { class: "xc-void-text" }, h("p", { class: "xc-void-k" }, o.k || "Nothing yet"), h("h2", null, o.title),
      o.body ? h("p", { class: "xc-void-body" }, o.body) : null, o.hint ? h("p", { class: "xc-void-hint" }, o.hint) : null));
}

// A time stamp for people: "Tue 14 Oct, 07:00". Empty for anything that is not a date.
export function stamp(iso) {
  const t = typeof iso === "number" ? iso * 1000 : Date.parse(iso || "");
  if (!isFinite(t) || !t) return "";
  const d = new Date(t);
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + ", " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

// "in 3h", "in 12m", or the past form from ago().
export function until(iso) {
  const t = Date.parse(iso || "");
  if (isNaN(t)) return "";
  const d = (t - Date.now()) / 1000;
  if (d <= 0) return ago(t / 1000);
  return "in " + (d < 60 ? Math.round(d) + "s" : d < 3600 ? Math.round(d / 60) + "m" : d < 86400 ? Math.round(d / 3600) + "h" : Math.round(d / 86400) + "d");
}
