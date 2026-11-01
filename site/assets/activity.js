/* Page glue for the live egg: connects the page's egg to the feed (docs/LIVE-EGG.md) and shows the legend.
 *
 *   ?feed=demo   read brand/egg-feed.demo.json and loop it. Also the default on staging.* hosts.
 *   ?feed=off    nothing, even on staging.
 *   otherwise    the real feed at /api/egg/feed, or the url in data-feed on this script tag.
 *
 * The legend (and, in demo mode, the "demo activity" label) appears only once a feed is live, so production
 * with no feed shows no animation and no label.
 */
(function () {
  "use strict";
  var me = document.currentScript, custom = me && me.getAttribute("data-feed");
  var q = /[?&]feed=([^&]*)/.exec(location.search), mode = q ? q[1] : "";
  if (/[?&]egg-demo(?:=([^&]*))?/.test(location.search) && mode !== "off") mode = "demo";
  if (mode === "off") return;
  var demo = mode === "demo" || (!mode && !custom && /^staging\./.test(location.hostname));
  var base = me && me.src ? new URL("../", me.src).href : "/";
  var url = demo ? base + "brand/egg-feed.demo.json" : (custom || "/api/egg/feed");
  var preview = /[?&]egg=/.test(location.search);                       // a stage preview owns the stage
  var onRoadmap = !!document.querySelector(".rm-hero__egg");            // the roadmap drives the stage by scroll
  var empty = true, log = [], shown = false, lastText = 0;

  function show(on) {
    document.querySelectorAll(".egg-legend").forEach(function (el) {
      el.hidden = !on;
      var d = el.querySelector(".egg-legend__demo"); if (d) d.hidden = !demo;
    });
  }
  function summary() {                                                  // for screen readers and reduced motion: at most once a minute
    var now = Date.now(); if (now - lastText < 60000 && !empty) return; lastText = now;
    var c = { issue: 0, pr: 0, merge: 0, release: 0 }, cut = now - 3600000;
    log = log.filter(function (e) { return e.at > cut; }); log.forEach(function (e) { c[e.type]++; });
    var parts = [[c.issue, "issue"], [c.pr, "pull request"], [c.merge, "merge"], [c.release, "release"]].filter(function (p) { return p[0]; })
      .map(function (p) { return p[0] + " " + p[1] + (p[0] > 1 ? "s" : ""); });
    var t = (demo ? "Demo: " : "") + "In the last hour: " + (parts.length ? parts.join(", ") : "nothing yet") + ".";
    empty = !parts.length;
    document.querySelectorAll(".egg-legend__live").forEach(function (el) { el.textContent = t; });
  }
  function start(egg) {
    window.ExoEggFeed.connect(egg, {
      url: url, demo: demo, replay: true, stage: !preview && !onRoadmap,
      onState: function () { if (!shown) { shown = true; show(true); } summary(); },
      onEvent: function (ev) {
        var type = { issue: 1, pr: 1, merge: 1, release: 1 }[ev.type] ? ev.type : "issue";
        log.push({ type: type, at: demo || !ev.t ? Date.now() : Date.parse(ev.t) || Date.now() }); summary();
      }
    });
  }
  var tries = 0;
  (function wait() {                                                    // the egg is mounted late on both pages
    if (window.exoEgg && window.ExoEggFeed) return start(window.exoEgg);
    if (++tries < 150) setTimeout(wait, 100);
  })();
})();
