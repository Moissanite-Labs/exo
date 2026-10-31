/* The live egg feed client (docs/LIVE-EGG.md 1.2). Kept apart from egg.js so the egg has no network code.
 *
 *   var feed = ExoEggFeed.connect(egg, {
 *     url: "/api/egg/feed",     // or "/brand/egg-feed.demo.json" (the page glue picks that for ?feed=demo)
 *     replay: true,             // play the replay window once on open (at most 8 s, 0.6 strength, one bloom)
 *     onState: function (eggBlock) {},   // the "egg" object after each good poll
 *     onEvent: function (ev) {},         // each new event, as it is handed to the egg
 *     stage: true,              // false: do not call egg.setStage (a page that drives the stage itself)
 *     demo: false               // true: read url once, replay it, then loop it forever (t is used only for order)
 *   });
 *   feed.stop(); feed.start();
 *
 * Polls every poll_s seconds (the server may raise it up to 300), passes the cursor back as ?since=, skips event
 * ids it has seen, and does not poll while the page is hidden. On an error it keeps the last good state and
 * retries after poll_s, or retry-after, doubling up to 300 s. Unknown fields are ignored. A feed with v higher than 1
 * still updates the egg block but its events are ignored.
 */
(function (root) {
  "use strict";
  var GAP = [0, 0.4, 1.2, 2.5, 2.5], REPLAY_S = 8, MAXQ = 50;

  function connect(egg, o) {
    o = o || {};
    var url = o.url || "/api/egg/feed", cursor = null, seen = {}, first = true, timer = 0, on = false, fails = 0, poll = 20;
    var demoAt = 0, demoList = null;

    function level(e) { var n = +e.level; return n >= 1 && n <= 4 ? n : ({ pr: 2, merge: 3, release: 4 }[e.type] || 1); }
    function hand(ev, replay) { try { egg.event(ev.type, { replay: replay, weight: ev.weight }); } catch (e) {} if (o.onEvent) o.onEvent(ev); }
    function replayWindow(list) {                                      // newest events that fit in 8 s at replay pacing, oldest first
      var keep = [], t = 0;
      for (var i = list.length - 1; i >= 0; i--) { t += GAP[level(list[i])] * 0.6; if (t > REPLAY_S) break; keep.unshift(list[i]); }
      return keep;
    }
    function apply(j) {
      if (!j || typeof j !== "object") return;
      if (j.egg) {
        if (o.stage !== false && typeof j.egg.stage === "number" && egg.setStage) egg.setStage(j.egg.stage);
        if (o.onState) o.onState(j.egg);
      }
      if (typeof j.cursor === "string") cursor = j.cursor;
      if (typeof j.poll_s === "number") poll = Math.min(300, Math.max(5, j.poll_s));
      if (!(j.v <= 1) || !Array.isArray(j.events)) return;
      var fresh = j.events.slice(0, MAXQ).filter(function (e) { return e && typeof e.id === "string" && !seen[e.id]; });
      fresh.forEach(function (e) { seen[e.id] = 1; });
      if (first) { first = false; if (!o.replay) return; fresh.forEach(function (e) { e._r = 1; }); fresh = replayWindow(fresh); }
      fresh.forEach(function (e) { hand(e, !!e._r); });
    }
    function next(ms) { clearTimeout(timer); if (on) timer = setTimeout(tick, ms); }
    function tick() {
      if (!on) return;
      if (document.hidden) return next(2000);
      if (o.demo) return demo();
      var u = url + (cursor ? (url.indexOf("?") < 0 ? "?" : "&") + "since=" + encodeURIComponent(cursor) : "");
      fetch(u, { headers: { accept: "application/json" } }).then(function (r) {
        if (!r.ok) { var e = new Error("http " + r.status); e.retry = +r.headers.get("retry-after") || 0; throw e; }
        return r.json();
      }).then(function (j) { fails = 0; apply(j); next(poll * 1000); })
        .catch(function (e) { fails++; var w = Math.max(e && e.retry || 0, Math.min(300, poll * Math.pow(2, fails))); next(w * 1000); });
    }
    function demo() {                                                  // read once, replay, then keep looping the same events
      if (!demoList) {
        return fetch(url).then(function (r) { return r.json(); }).then(function (j) {
          demoList = (j.events || []).slice(0, MAXQ); first = false;
          apply({ v: 1, egg: j.egg, events: [] });
          var r = replayWindow(demoList.slice(0, 12)); if (o.replay === false) r = [];
          r.forEach(function (e) { hand(e, true); }); demoAt = 12 % (demoList.length || 1); next(4000);
        }).catch(function () { next(30000); });
      }
      if (demoList.length) { hand(demoList[demoAt], false); demoAt = (demoAt + 1) % demoList.length; }
      next(3500 + Math.random() * 3500);
    }
    var api = {
      start: function () { if (!on) { on = true; tick(); } return api; },
      stop: function () { on = false; clearTimeout(timer); return api; }
    };
    return api.start();
  }
  root.ExoEggFeed = { connect: connect };
})(window);
