/* Page glue for x0, the community exosphere: connects the page's egg to the feed (docs/LIVE-EGG.md, feed v: 2),
 * shows the legend, an XP readout that ticks as validated contributions are absorbed, and a text line for screen
 * readers and reduced motion.
 *
 *   ?feed=demo   play brand/egg-feed.demo.json (a recorded cycle: events, a meditation, the wake), looped.
 *                Also the default on staging.* hosts.
 *   ?feed=live   poll /api/egg/feed (or the url in data-feed on this script tag).
 *   ?feed=off    nothing, even on staging.
 *   otherwise    the live feed only when this script tag carries data-feed; no requests at all without it, so a
 *                host with no feed function makes none. The client also stops after three 404s in a row.
 *
 * The legend (and, in demo mode, the "demo activity" label) appears only once a feed answered.
 */
(function () {
  "use strict";
  var me = document.currentScript, custom = me && me.getAttribute("data-feed");
  var q = /[?&]feed=([^&]*)/.exec(location.search), mode = q ? q[1] : "";
  if (mode === "off") return;
  var demo = mode === "demo" || (!mode && !custom && /^staging\./.test(location.hostname));
  var live = !demo && (mode === "live" || !!custom);
  if (!demo && !live) return;
  var base = me && me.src ? new URL("../", me.src).href : "/";
  var url = demo ? base + "brand/egg-feed.demo.json" : (custom || "/api/egg/feed");
  var preview = /[?&]egg=/.test(location.search);                       // a stage preview owns the stage
  var onRoadmap = !!document.querySelector(".rm-hero__egg");            // the roadmap drives the stage by scroll
  var shown = false, log = [], lastText = 0, lastKind = "", inflight = 0, xpShown = null, med = null, target = 0;
  var NAMES = { issue: ["issue", "issues"], pr: ["pull request", "pull requests"], review: ["review", "reviews"],
    merge: ["merge", "merges"], release: ["release", "releases"] };

  function each(sel, fn) { document.querySelectorAll(sel).forEach(fn); }
  function show() {
    if (shown) return; shown = true;
    each(".egg-legend", function (el) {
      el.hidden = false;
      var d = el.querySelector(".egg-legend__demo"); if (d) d.hidden = !demo;
    });
  }
  function fmt(n) { return Math.round(n).toLocaleString("en-US"); }
  function readout() {
    if (xpShown == null) return;
    each(".egg-legend__xp", function (el) { el.textContent = "x0 · " + fmt(xpShown) + " XP of " + fmt(target); });
  }
  function say(text, kind, every) {                                     // aria-live: rate limited per kind of line
    var now = Date.now();
    if (kind === lastKind && now - lastText < every) return;
    lastText = now; lastKind = kind;
    each(".egg-legend__live", function (el) { el.textContent = (demo ? "Demo: " : "") + text; });
  }
  function hourLine() {
    var c = {}, cut = Date.now() - 3600000;
    log = log.filter(function (e) { return e.at > cut; });
    log.forEach(function (e) { c[e.type] = (c[e.type] || 0) + 1; });
    var parts = Object.keys(NAMES).filter(function (k) { return c[k]; })
      .map(function (k) { return c[k] + " " + NAMES[k][c[k] > 1 ? 1 : 0]; });
    return "In the last hour: " + (parts.length ? parts.join(", ") : "nothing yet") + ".";
  }
  function onState(E) {
    show();
    target = E.target || target;
    xpShown = Math.max(0, (E.xp || 0) - inflight);                      // reset to the feed, less glows still travelling
    readout();
    var m = E.meditation;
    if ((E.state === "meditating" || E.state === "waking") && m) {
      med = m;
      if (E.state === "meditating") {
        say("x0 is meditating: " + m.done + " of " + m.total + " contributions absorbed, " + fmt(m.gained) + " XP gained.", "med", 15000);
        return;
      }
    } else if (med) {
      say("x0 woke: " + med.total + " contributions validated, " + fmt(med.gained) + " XP gained.", "woke", 0);
      med = null; return;
    }
    if (lastKind !== "woke" || Date.now() - lastText > 60000) say(hourLine(), "hour", 60000);
  }
  function start(egg) {
    window.ExoEggFeed.connect(egg, {
      url: url, demo: demo, pace: 0.5, replay: true, stage: !preview && !onRoadmap,
      onState: onState,
      onEvent: function (ev) {
        if (ev.type === "validated") { inflight += +ev.weight || 0; return; }
        var type = NAMES[ev.type] ? ev.type : "issue";
        log.push({ type: type, at: demo || !ev.t ? Date.now() : Date.parse(ev.t) || Date.now() });
      },
      onArrive: function (ev) {                                         // a glow reached the core: tick the readout
        var w = +ev.weight || 0;
        inflight = Math.max(0, inflight - w);
        if (xpShown != null) { xpShown += w; readout(); }
      }
    });
  }
  var tries = 0;
  (function wait() {                                                    // the egg is mounted late on both pages
    if (window.exoEgg && window.ExoEggFeed) return start(window.exoEgg);
    if (++tries < 150) setTimeout(wait, 100);
  })();
})();
