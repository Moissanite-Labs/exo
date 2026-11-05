/* Roadmap page: the flywheel, the status tally, and the egg that grows as you read. No dependencies. */
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SVGNS = "http://www.w3.org/2000/svg";
  function el(name, attrs, parent) {
    var n = document.createElementNS(SVGNS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  /* ---------- Status tally, counted from the chart itself ---------- */
  var order = ["built", "progress", "designed", "exploring"], counts = {};
  document.querySelectorAll(".rm-road .rm-chip").forEach(function (c) { counts[c.dataset.s] = (counts[c.dataset.s] || 0) + 1; });
  var bar = document.querySelector(".rm-tally__bar");
  order.forEach(function (s) {
    var n = counts[s] || 0;
    var b = document.querySelector('[data-count="' + s + '"]'); if (b) b.textContent = n;
    if (bar && n) { var sp = document.createElement("span"); sp.dataset.s = s; sp.style.flexGrow = n; sp.title = s + ": " + n; bar.appendChild(sp); }
  });
  var labels = { built: "built", progress: "in progress", designed: "designed", exploring: "exploring" };
  document.querySelectorAll(".rm-chip").forEach(function (c) { c.setAttribute("title", labels[c.dataset.s] || ""); });

  /* ---------- Flywheel ---------- */
  var svg = document.querySelector(".rm-fly__svg");
  if (svg) {
    var CX = 320, CY = 320, R = 220, N = 6, GAP = 16;
    var names = ["Harness", "Work", "Forge", "Credit", "Hub", "Learning"];
    var arcsG = svg.querySelector(".rm-fly__arcs"), nodesG = svg.querySelector(".rm-fly__nodes");
    var packet = svg.querySelector(".rm-fly__packet");
    var items = Array.prototype.slice.call(document.querySelectorAll(".rm-fly__list li"));
    var pt = function (deg, r) { var a = deg * Math.PI / 180; return [CX + (r || R) * Math.cos(a), CY + (r || R) * Math.sin(a)]; };
    var arcs = [], nodes = [];
    for (var i = 0; i < N; i++) {
      var a0 = -90 + i * 60 + GAP, a1 = -90 + (i + 1) * 60 - GAP, p0 = pt(a0), p1 = pt(a1);
      arcs.push(el("path", { "class": "rm-fly__arc", d: "M" + p0[0].toFixed(1) + "," + p0[1].toFixed(1) + " A" + R + "," + R + " 0 0 1 " + p1[0].toFixed(1) + "," + p1[1].toFixed(1), "marker-end": "url(#arr)" }, arcsG));
    }
    for (i = 0; i < N; i++) {
      var p = pt(-90 + i * 60), href = items[i] && items[i].querySelector("h3 a");
      var host = nodesG;
      if (href) {
        host = el("a", { "class": "rm-fly__a", href: href.getAttribute("href"), "aria-label": names[i] + ": read how it works" }, nodesG);
        (function (j) {
          host.addEventListener("mouseenter", function () { hover = j; light(j); });
          host.addEventListener("mouseleave", function () { hover = -1; });
          host.addEventListener("focus", function () { hover = j; light(j); });
          host.addEventListener("blur", function () { hover = -1; });
        })(i);
      }
      var g = el("g", { "class": "rm-fly__node", transform: "translate(" + p[0].toFixed(1) + "," + p[1].toFixed(1) + ")" }, host);
      el("rect", { x: -78, y: -30, width: 156, height: 60 }, g);
      var t1 = el("text", { "class": "rm-fly__nn", x: 0, y: -8, "text-anchor": "middle" }, g); t1.textContent = "0" + (i + 1);
      var t2 = el("text", { x: 0, y: 17, "text-anchor": "middle" }, g); t2.textContent = names[i];
      nodes.push(g);
    }
    var current = -1, hover = -1;
    function light(k) {
      if (k === current) return; current = k;
      nodes.forEach(function (n, j) { n.classList.toggle("is-on", j === k); });
      items.forEach(function (li, j) { li.classList.toggle("is-on", j === k); });
    }
    items.forEach(function (li, j) {
      li.addEventListener("mouseenter", function () { hover = j; light(j); });
      li.addEventListener("mouseleave", function () { hover = -1; });
      li.addEventListener("focusin", function () { hover = j; light(j); });
      li.addEventListener("focusout", function () { hover = -1; });
    });
    if (reduce) { light(0); packet.style.display = "none"; arcs.forEach(function (a) { a.classList.add("is-on"); }); }
    else {
      var PERIOD = 15, t = 0, last = 0, running = false, raf = 0;
      var frame = function (now) {
        if (!running) return;
        var dt = Math.min(0.05, (now - last) / 1000); last = now; t = (t + dt) % PERIOD;
        var deg = -90 + 360 * t / PERIOD, q = pt(deg);
        packet.setAttribute("cx", q[0].toFixed(1)); packet.setAttribute("cy", q[1].toFixed(1));
        var rel = ((deg + 90) % 360 + 360) % 360;
        arcs.forEach(function (a, j) { a.classList.toggle("is-on", rel > j * 60 && rel < (j + 1) * 60); });
        if (hover < 0) light(Math.round(rel / 60) % N);
        raf = requestAnimationFrame(frame);
      };
      var start = function () { if (!running) { running = true; last = performance.now(); raf = requestAnimationFrame(frame); } };
      var stop = function () { running = false; cancelAnimationFrame(raf); };
      if ("IntersectionObserver" in window) new IntersectionObserver(function (e) { e[0].isIntersecting ? start() : stop(); }).observe(svg);
      else start();
    }
  }

  /* ---------- The egg: hero, and a small one that follows the reader ---------- */
  function eggs() {
    if (!window.ExoEgg) return;
    var hero = document.querySelector(".rm-hero__egg"), heroSec = document.querySelector(".rm-hero");
    var big = hero && window.ExoEgg.mount(hero, { stage: 0, activity: 0.55, seed: 9, layout: function (W, H) {
      var narrow = W < 760;
      return { cx: narrow ? W * 0.8 : W * 0.72, cy: narrow ? H * 0.2 : H * 0.48, r: narrow ? Math.min(W, H) * 0.4 : Math.min(W * 0.28, H * 0.42) };
    } });
    if (big) window.exoEgg = big;
    var aside = document.querySelector(".rm-pet"), cv = aside && aside.querySelector(".rm-pet__cv");
    var nEl = aside && aside.querySelector(".rm-pet__n"), nameEl = aside && aside.querySelector(".rm-pet__name");
    var small = null, shown = false;
    var STAGES = window.ExoEgg.STAGES || [];
    function setShown(on) {
      if (!aside || on === shown) return; shown = on;
      aside.classList.toggle("is-on", on);
      if (on && !small) small = window.ExoEgg.mount(cv, { stage: stage, activity: 0.6, seed: 4, shell: true, spin: 1.3 });
      else if (small && !reduce) { on ? small.resume() : small.pause(); }
    }
    var stage = 0, ticking = false;
    function update() {
      ticking = false;
      var max = document.documentElement.scrollHeight - innerHeight;
      var p = max > 0 ? Math.min(1, Math.max(0, scrollY / max)) : 0;
      var s = Math.round(p * 3 * 20) / 20;
      if (s !== stage) {
        stage = s;
        if (big) big.setStage(s);
        if (small) small.setStage(s);
        if (!reduce && big && Math.random() < 0.25) big.pulse(2);
      }
      if (nEl) nEl.textContent = stage.toFixed(1);
      if (nameEl) { var k = STAGES[Math.min(STAGES.length - 1, Math.floor(stage + 0.001))]; nameEl.textContent = k ? k.name : ""; }
      if (heroSec) setShown(heroSec.getBoundingClientRect().bottom < 0);
    }
    addEventListener("scroll", function () { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    addEventListener("resize", update, { passive: true });
    update();
  }
  if (window.ExoEgg) eggs(); else addEventListener("load", eggs);
})();
