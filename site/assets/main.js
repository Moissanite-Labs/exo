/* Project Exo site. No dependencies. */
(function () {
  "use strict";
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Reveal on scroll ---------- */
  var revealables = document.querySelectorAll(".reveal, .climb, .term");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    revealables.forEach(function (el, i) {
      el.style.transitionDelay = el.classList.contains("reveal") ? ((i % 4) * 60) + "ms" : "";
      io.observe(el);
    });
  } else {
    revealables.forEach(function (el) { el.classList.add("is-in"); });
  }

  /* ---------- Active nav link ---------- */
  var links = Array.prototype.slice.call(document.querySelectorAll('.nav__links a[href^="#"]'));
  if ("IntersectionObserver" in window && links.length) {
    var map = {};
    links.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var navIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && map[e.target.id]) {
          links.forEach(function (a) { a.classList.remove("is-active"); });
          map[e.target.id].classList.add("is-active");
        }
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(map).forEach(function (id) { var s = document.getElementById(id); if (s) navIO.observe(s); });
  }

  /* ---------- Ledger ticker (schematic) ---------- */
  var track = document.querySelector(".ticker__track");
  if (track) {
    var kinds = ["turn.user", "tool.call", "verdict.edit", "ruling", "belief.proposed", "eval.scored", "belief.accepted", "gate.approval", "verdict.accept", "summary.node", "taste.kept", "decision.choice"];
    var hex = "0123456789abcdef";
    var h = function (n) { var s = ""; for (var i = 0; i < n; i++) s += hex[(Math.random() * 16) | 0]; return s; };
    var parts = ["<b>LEDGER</b> &nbsp;schematic feed"];
    var prev = h(8);
    for (var i = 0; i < 26; i++) {
      var cur = h(8);
      parts.push("#" + (1000 + i) + " <i>" + kinds[i % kinds.length] + "</i> prev:" + prev + " hash:" + cur + " sig:ed25519");
      prev = cur;
    }
    track.innerHTML = parts.join(" &nbsp;&nbsp;/&nbsp;&nbsp; ");
  }

  /* ---------- Hero canvas: a shell of linked nodes around a core ---------- */
  var canvas = document.querySelector(".hero__canvas");
  if (!canvas || !canvas.getContext) return;

  /* ---------- Hero: the lattice egg (D-049, accepted) ----------
     The default everywhere. ?egg=2 picks a stage, ?egg=off brings back the old orb below. */
  var eggQ = /[?&]egg(?:=([^&]*))?/.exec(location.search);
  var eggOn = !(eggQ && eggQ[1] === "off");
  if (eggOn) {
    var stage = eggQ && eggQ[1] && !isNaN(+eggQ[1]) ? +eggQ[1] : 0;
    var s = document.createElement("script");
    var mainScript = document.querySelector('script[src$="assets/main.js"]');
    var sitePath = new URL("../", mainScript.src).pathname;
    s.src = sitePath + "brand/egg.js";
    s.onload = function () {
      window.exoEgg = window.ExoEgg.mount(canvas, { stage: stage, activity: 0.5, layout: function (W, H) {
        var narrow = W < 760;
        return { cx: narrow ? W * 0.78 : W * 0.72, cy: narrow ? H * 0.2 : H * 0.47,
                 r: narrow ? Math.min(W, H) * 0.42 : Math.min(W * 0.3, H * 0.4) };
      } });
      if (eggQ) { // a stage preview asked for in the address: label it
        var tag = document.createElement("a");
        tag.className = "egg-tag"; tag.href = sitePath + "brand/";
        tag.textContent = "Preview: the lattice egg, stage " + stage + " \u00b7 brand";
        (canvas.parentNode.querySelector(".hero__in") || canvas.parentNode).appendChild(tag);
      }
    };
    document.head.appendChild(s);
    return;
  }
  var ctx = canvas.getContext("2d");
  var W = 0, H = 0, dpr = 1, cx = 0, cy = 0, R = 0;
  var N = 220, pts = [], edges = [], pulses = [];
  var ACC = [255, 91, 20], FG = [236, 234, 228];

  // Fibonacci sphere
  var ga = Math.PI * (3 - Math.sqrt(5));
  for (var k = 0; k < N; k++) {
    var y = 1 - (k / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = ga * k;
    pts.push({ x: Math.cos(th) * r, y: y, z: Math.sin(th) * r, hot: Math.random() < 0.08 });
  }
  // Neighbour edges (precomputed once)
  for (var a = 0; a < N; a++) {
    for (var b = a + 1; b < N; b++) {
      var dx = pts[a].x - pts[b].x, dy = pts[a].y - pts[b].y, dz = pts[a].z - pts[b].z;
      if (dx * dx + dy * dy + dz * dz < 0.052) edges.push([a, b]);
    }
  }

  function size() {
    var rect = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = rect.width; H = rect.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var narrow = W < 760;
    cx = narrow ? W * 0.78 : W * 0.72;
    cy = narrow ? H * 0.2 : H * 0.47;
    R = narrow ? Math.min(W, H) * 0.42 : Math.min(W * 0.3, H * 0.4);
  }

  var rotY = 0.6, rotX = -0.35, t0 = performance.now(), proj = new Array(N);
  function frame(now) {
    var t = (now - t0) / 1000;
    if (!reduce) rotY = 0.6 + t * 0.08;
    var cY = Math.cos(rotY), sY = Math.sin(rotY), cX = Math.cos(rotX), sX = Math.sin(rotX);
    ctx.clearRect(0, 0, W, H);

    // Breathing radius: the shell grows slowly
    var rr = R * (1 + (reduce ? 0 : Math.sin(t * 0.5) * 0.015));
    for (var i = 0; i < N; i++) {
      var p = pts[i];
      var x = p.x * cY + p.z * sY, z = -p.x * sY + p.z * cY;
      var y2 = p.y * cX - z * sX, z2 = p.y * sX + z * cX;
      var s = 1.6 / (2.6 - z2);
      proj[i] = { x: cx + x * rr * s, y: cy + y2 * rr * s, z: z2 };
    }

    // Edges
    ctx.lineWidth = 1;
    for (var e = 0; e < edges.length; e++) {
      var A = proj[edges[e][0]], B = proj[edges[e][1]];
      var depth = (A.z + B.z) / 2; // -1 back, 1 front
      var al = 0.04 + (depth + 1) * 0.09;
      ctx.strokeStyle = "rgba(" + FG[0] + "," + FG[1] + "," + FG[2] + "," + al.toFixed(3) + ")";
      ctx.beginPath(); ctx.moveTo(A.x, A.y); ctx.lineTo(B.x, B.y); ctx.stroke();
    }

    // Spokes from hot nodes to the core
    for (var j = 0; j < N; j++) {
      if (!pts[j].hot) continue;
      var P = proj[j];
      if (P.z < -0.1) continue;
      var g = ctx.createLinearGradient(P.x, P.y, cx, cy);
      g.addColorStop(0, "rgba(255,91,20,0.35)"); g.addColorStop(1, "rgba(255,91,20,0)");
      ctx.strokeStyle = g; ctx.beginPath(); ctx.moveTo(P.x, P.y); ctx.lineTo(cx, cy); ctx.stroke();
    }

    // Nodes
    for (var n = 0; n < N; n++) {
      var Q = proj[n], d = (Q.z + 1) / 2;
      if (pts[n].hot) {
        ctx.fillStyle = "rgba(255,91,20," + (0.5 + d * 0.5).toFixed(3) + ")";
        ctx.fillRect(Q.x - 2.5, Q.y - 2.5, 5, 5);
      } else {
        ctx.fillStyle = "rgba(236,234,228," + (0.15 + d * 0.6).toFixed(3) + ")";
        var sz = 1 + d * 1.6;
        ctx.fillRect(Q.x - sz / 2, Q.y - sz / 2, sz, sz);
      }
    }

    // Pulses: signals travelling from the shell to the core
    if (!reduce) {
      if (pulses.length < 6 && Math.random() < 0.04) {
        var hot = [];
        for (var q = 0; q < N; q++) if (pts[q].hot && proj[q].z > 0) hot.push(q);
        if (hot.length) pulses.push({ i: hot[(Math.random() * hot.length) | 0], s: 0 });
      }
      for (var u = pulses.length - 1; u >= 0; u--) {
        var pu = pulses[u]; pu.s += 0.012;
        if (pu.s >= 1) { pulses.splice(u, 1); continue; }
        var S = proj[pu.i], px = S.x + (cx - S.x) * pu.s, py = S.y + (cy - S.y) * pu.s;
        ctx.fillStyle = "rgba(255,138,76," + (1 - pu.s).toFixed(3) + ")";
        ctx.beginPath(); ctx.arc(px, py, 2.2, 0, Math.PI * 2); ctx.fill();
      }
    }

    // Core: a hexagon that slowly turns
    var cr = R * 0.12, rot = reduce ? 0 : t * 0.25;
    ctx.save(); ctx.translate(cx, cy);
    for (var ring = 0; ring < 2; ring++) {
      var rad = cr * (ring ? 1.9 : 1), dir = ring ? -1 : 1;
      ctx.beginPath();
      for (var v = 0; v < 6; v++) {
        var ang = rot * dir + v * Math.PI / 3 + Math.PI / 6;
        var vx = Math.cos(ang) * rad, vy = Math.sin(ang) * rad;
        v ? ctx.lineTo(vx, vy) : ctx.moveTo(vx, vy);
      }
      ctx.closePath();
      if (ring) { ctx.strokeStyle = "rgba(255,91,20,0.45)"; ctx.lineWidth = 1; ctx.stroke(); }
      else {
        var glow = 0.75 + (reduce ? 0 : Math.sin(t * 1.4) * 0.2);
        ctx.fillStyle = "rgba(255,91,20," + glow.toFixed(3) + ")"; ctx.fill();
      }
    }
    ctx.restore();

    if (running && !reduce) raf = requestAnimationFrame(frame);
  }

  var raf = 0, running = true;
  function start() { if (!running) { running = true; t0 = performance.now() - lastT; raf = requestAnimationFrame(frame); } }
  var lastT = 0;
  function stop() { if (running) { running = false; lastT = performance.now() - t0; cancelAnimationFrame(raf); } }

  size();
  window.addEventListener("resize", function () { size(); if (reduce || !running) frame(performance.now()); }, { passive: true });
  raf = requestAnimationFrame(frame);

  if (!reduce) {
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (en) { en[0].isIntersecting ? start() : stop(); }).observe(canvas);
    }
    document.addEventListener("visibilitychange", function () { document.hidden ? stop() : start(); });
  }
})();
