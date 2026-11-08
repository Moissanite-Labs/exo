// Image input for the composer: paste, drop or attach. Images live in memory only (never in browser storage) and
// are sent as base64 with the next message. The server checks the same limits; these checks just fail early.
import { h } from "./dom.js";

export const MAX_IMAGES = 4;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
// Matches api.py: the core caps a send at 8 MiB, so all images together stay under 5 MB raw.
export const MAX_IMAGES_TOTAL = 5 * 1024 * 1024;
export const IMAGE_MIMES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => { const s = String(r.result || ""); resolve(s.slice(s.indexOf(",") + 1)); };
    r.onerror = () => reject(new Error("could not read the image"));
    r.readAsDataURL(file);
  });
}

// attachImages({ input, wrap, flash }) -> { take(), restore(list), count() }
export function attachImages({ input, wrap, flash }) {
  let items = [], generation = 0, pending = 0, reads = Promise.resolve();   // memory belongs to this composer generation
  const kb = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");
  const tray = h("div", { class: "xc-images", hidden: true, "aria-label": "Attached images" });
  const picker = h("input", { type: "file", accept: IMAGE_MIMES.join(","), multiple: true, hidden: true });
  const button = h("button", { class: "xc-btn xc-attach", type: "button", title: "Attach images (or paste or drop them)",
    "aria-label": "Attach images", onclick: () => picker.click() }, "+ Image");

  function draw() {
    // Text chips, not thumbnails: the CSP refuses data: and blob: image sources on purpose (test_csp_web.py).
    tray.replaceChildren(...items.map((it, i) => h("div", { class: "xc-image", title: it.name },
      h("span", { class: "xc-image-name" }, it.name || "image"),
      h("span", { class: "xc-image-size" }, kb(it.size)),
      h("button", { class: "xc-image-x", type: "button", "aria-label": "Remove image",
        onclick: () => { items.splice(i, 1); draw(); } }, "\u00D7"))));
    tray.hidden = !items.length;
  }

  function addFiles(files) {
    const owner = generation;
    pending++;
    reads = reads.then(async () => {
    for (const f of files) {
      if (owner !== generation) break;
      if (items.length >= MAX_IMAGES) { flash("at most " + MAX_IMAGES + " images per message"); break; }
      if (!IMAGE_MIMES.includes(f.type)) { flash("only png, jpeg, gif or webp images"); continue; }
      if (f.size > MAX_IMAGE_BYTES) { flash("image is larger than 5 MB"); continue; }
      if (items.reduce((n, it) => n + it.size, 0) + f.size > MAX_IMAGES_TOTAL) { flash("images together are larger than 5 MB"); continue; }
      try {
        const data = await readBase64(f);
        if (owner !== generation) break;
        // A failed send may have restored images while this file was being read.
        if (items.length >= MAX_IMAGES || items.reduce((n, it) => n + it.size, 0) + f.size > MAX_IMAGES_TOTAL) {
          flash("image did not fit after restoring the failed send"); continue;
        }
        items.push({ mime: f.type, data, name: f.name || "pasted image", size: f.size });
      }
      catch (e) { if (owner === generation) flash(e.message); }
    }
    if (owner === generation) draw();
    }).finally(() => { if (owner === generation) pending--; });
  }

  const imageFiles = (list) => Array.from(list || []).filter((f) => f && f.type && f.type.startsWith("image/"));
  picker.addEventListener("change", () => { addFiles(Array.from(picker.files || [])); picker.value = ""; });
  input.addEventListener("paste", (e) => {
    const files = imageFiles(e.clipboardData && Array.from(e.clipboardData.items || [])
      .filter((x) => x.kind === "file").map((x) => x.getAsFile()));
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  wrap.addEventListener("dragover", (e) => {
    if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) { e.preventDefault(); wrap.classList.add("xc-drop"); }
  });
  wrap.addEventListener("dragleave", () => wrap.classList.remove("xc-drop"));
  wrap.addEventListener("drop", (e) => {
    wrap.classList.remove("xc-drop");
    const files = imageFiles(e.dataTransfer && e.dataTransfer.files);
    if (files.length) { e.preventDefault(); addFiles(files); }
  });

  return {
    tray, button, picker,
    count: () => items.length,
    pending: () => pending > 0,
    // Hands the images to a send and clears the tray. restore() puts them back if the send fails.
    take() { const out = items; generation++; pending = 0; items = []; draw(); return out; },
    restore(list) {
      // Failed sends retain their attachments; additions that no longer fit are refused explicitly.
      const newer = items; items = list.slice();
      for (const it of newer) {
        if (items.length < MAX_IMAGES && items.reduce((n, x) => n + x.size, 0) + it.size <= MAX_IMAGES_TOTAL) items.push(it);
        else flash("newer image did not fit after restoring the failed send");
      }
      draw();
    },
    payload: (list) => list.map((it) => ({ mime: it.mime, data: it.data })),
  };
}
