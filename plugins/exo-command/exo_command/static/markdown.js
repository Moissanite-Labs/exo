// A small, safe markdown renderer for panels and completed chat replies: headings, lists, code fences, block
// quotes, bold, italics, inline code, pipe tables and http(s) links. It builds DOM nodes, so nothing the model
// writes can become markup. Line breaks inside a paragraph are kept, as the streamed plain text shows them.
import { h } from "./dom.js";

// Limits that keep a reply cheap to draw (drawLog renders every reply on each open). GFM pads short rows to the
// header width, so a few KB of pipes could build millions of cells: a table wider than MAX_COLUMNS, one that
// would take the whole render past MAX_CELLS, or one with more padded cells than source characters (as cmark-gfm
// bounds them, so many replies in one thread stay cheap too) is shown as its source text. Quotes nest at most
// MAX_DEPTH deep and lists one more; past that a ">" line is text and an indented item is a sibling.
const MAX_COLUMNS = 64, MAX_CELLS = 10000, MAX_DEPTH = 8;
const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const QUOTE = /^ {0,3}>/;
const BLOCK = /^ {0,3}(#{1,6}\s|[-*]\s|\d+\.\s|>)/;   // a line that opens another block cannot be a table row
const indentOf = (line) => /^ */.exec(line)[0].length;

function inline(text) {
  const out = [];
  // Link text excludes "[" and the URL stops at one, so no scan runs past the next link start: an unclosed "[" or
  // "[a](http://..." would otherwise scan to the end of the text from every start.
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\s][^*]*\*)|(\[[^\[\]]+\]\((https?:\/\/[^)\s\[]+)\))/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(h("code", null, m[1].slice(1, -1)));
    else if (m[2]) out.push(h("strong", null, m[2].slice(2, -2)));
    else if (m[3]) out.push(h("em", null, m[3].slice(1, -1)));
    else out.push(h("a", { href: m[5], target: "_blank", rel: "noopener noreferrer" }, m[4].slice(1, m[4].indexOf("]"))));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Split only unescaped pipes. Outer pipes are optional; an escaped pipe stays cell text. GFM unescapes only "\|"
// here, so a code span in a cell keeps its backslashes.
function tableRow(line) {
  const cells = [];
  let text = "", pipes = 0;
  const src = line.trim();
  for (let i = 0; i < src.length; i++) {
    if (src[i] === "\\" && src[i + 1] === "|") { text += src[++i]; }
    else if (src[i] === "|") { cells.push(text.trim()); text = ""; pipes++; }
    else text += src[i];
  }
  cells.push(text.trim());
  if (!pipes) return null;
  if (src.startsWith("|")) cells.shift();
  if (src.endsWith("|") && cells[cells.length - 1] === "") cells.pop();
  return cells.length ? cells : null;
}

function tableStart(lines, i) {
  const head = tableRow(lines[i]), rule = i + 1 < lines.length ? tableRow(lines[i + 1]) : null;
  if (!head || !rule || head.length !== rule.length || !rule.every(x => /^:?-+:?$/.test(x))) return null;
  return { head, aligns: rule.map(x => x.startsWith(":") ? (x.endsWith(":") ? "center" : "left") : (x.endsWith(":") ? "right" : "left")) };
}

// A fenced code block opening at lines[i], or null. CommonMark forbids a backtick in a backtick fence's info
// string, so "```git status``` shows ..." stays a paragraph.
function codeBlock(lines, i) {
  const m = FENCE.exec(lines[i]);
  if (!m || (m[1][0] === "`" && m[2].includes("`"))) return null;
  const close = new RegExp("^ {0,3}" + m[1][0] + "{" + m[1].length + ",}\\s*$"), buf = [];
  for (i++; i < lines.length && !close.test(lines[i]); i++) buf.push(lines[i]);
  return { pre: h("pre", { class: "xc-code" }, buf.join("\n")), next: i + 1 };
}

// The lines after a list item that belong to it: those indented at least `own` spaces, with the blank lines between
// them, without their common indentation. A sub-list, fence, table, quote or text there renders inside the item.
function itemBody(lines, i, own) {
  let end = i;
  for (let j = i; j < lines.length; j++) {
    if (!lines[j].trim()) continue;
    if (indentOf(lines[j]) < own) break;
    end = j + 1;
  }
  const body = lines.slice(i, end), cut = body.reduce((n, x) => (x.trim() ? Math.min(n, indentOf(x)) : n), Infinity);
  return body.map((x) => x.slice(cut));
}

// `budget.cells` is shared by every table of one render; `depth` counts the quotes and list items around `lines`.
function blocks(root, lines, budget, depth) {
  let para = [], list = null, i = 0;
  const flushPara = () => {
    if (!para.length) return;
    // Inline markup may wrap across lines. A trailing backslash breaks the line when another line follows it.
    // The backslash is dropped from plain text only, after inline(), so a code span that wraps keeps it.
    const brk = (x) => x.split("\n").map((t, k, all) => [k ? h("br") : null, (k < all.length - 1 ? t.replace(/\\$/, "") : t) || null]);
    root.appendChild(h("p", null, inline(para.join("\n")).map((x) => (typeof x === "string" ? brk(x) : x))));
    para = [];
  };
  const flushList = () => { if (list) { root.appendChild(list); list = null; } };
  while (i < lines.length) {
    const ln = lines[i];
    const code = codeBlock(lines, i);
    if (code) { flushPara(); flushList(); root.appendChild(code.pre); i = code.next; continue; }
    const start = tableStart(lines, i);
    if (start) {
      flushPara(); flushList();
      const rows = [];
      let j = i + 2;
      for (; j < lines.length && lines[j].trim() && !BLOCK.test(lines[j]) && !FENCE.test(lines[j]); j++) {
        const row = tableRow(lines[j]);
        if (!row) break;
        rows.push(row);
      }
      const cells = start.head.length * (rows.length + 1);
      const padded = rows.reduce((n, r) => n + Math.max(0, start.head.length - r.length), 0);
      if (start.head.length > MAX_COLUMNS || cells > budget.cells || padded > lines.slice(i, j).join("\n").length) {
        root.appendChild(h("pre", { class: "xc-code" }, lines.slice(i, j).join("\n")));
      } else {
        budget.cells -= cells;
        const cell = (tag, text, column) => h(tag, { "data-align": start.aligns[column], ...(tag === "th" ? { scope: "col" } : {}) }, h("div", { class: "xc-table-cell" }, inline(text)));
        const table = h("table", null, h("thead", null, h("tr", null, start.head.map((text, column) => cell("th", text, column)))),
          h("tbody", null, rows.map((row) => h("tr", null, start.head.map((_, column) => cell("td", row[column] || "", column))))));
        root.appendChild(h("div", { class: "xc-table-scroll", role: "region", "aria-label": "Table", tabindex: "0" }, table));
      }
      i = j;
      continue;
    }
    if (depth < MAX_DEPTH && QUOTE.test(ln)) {
      flushPara(); flushList();
      const buf = [];
      while (i < lines.length && QUOTE.test(lines[i])) buf.push(lines[i++].replace(/^ {0,3}> ?/, ""));
      const quote = h("blockquote");
      blocks(quote, buf, budget, depth + 1);
      root.appendChild(quote);
      continue;
    }
    const hd = /^(#{1,4})\s+(.*)$/.exec(ln), li = /^(\s*)([-*]|(\d+)\.)\s+(.*)$/.exec(ln);
    if (hd) { flushPara(); flushList(); root.appendChild(h("h" + (hd[1].length + 2), null, inline(hd[2]))); }
    else if (li) {
      flushPara();
      const ordered = !!li[3], item = h("li", null, inline(li[4]));
      if (!list || (list.tagName === "OL") !== ordered) { flushList(); list = h(ordered ? "ol" : "ul", ordered && +li[3] !== 1 ? { start: +li[3] } : null); }
      list.appendChild(item);   // an <ol> starts at its source number, not 1
      const body = depth < MAX_DEPTH ? itemBody(lines, i + 1, li[1].length + 2) : [];
      if (body.length) { blocks(item, body, budget, depth + 1); i += body.length; }
    } else if (!ln.trim()) flushPara();   // a list stays open across a blank line: the next item continues it
    else { flushList(); para.push(ln.trimEnd()); }   // leading indentation is kept, as the streamed text showed it
    i++;
  }
  flushPara(); flushList();
}

export function renderMarkdown(src) {
  const root = h("div", { class: "xc-md" });
  blocks(root, String(src || "").replace(/\r/g, "").split("\n"), { cells: MAX_CELLS }, 0);
  return root;
}
