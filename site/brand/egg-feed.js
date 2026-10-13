/* The x0 feed client (docs/LIVE-EGG.md 1.3, feed v: 2). Kept apart from egg.js so the engine has no network code.
 *
 *   var feed = ExoEggFeed.connect(egg, {
 *     url: "/api/egg/feed",     // or "/brand/egg-feed.demo.json" with demo: true (the page glue picks it for ?feed=demo)
 *     replay: true,             // play the replay window once on open (at most 8 s, 0.6 strength, one bloom, no fold)
 *     onState: function (eggBlock) {},   // the "egg" object after each good poll, for counters and text
 *     onEvent: function (ev) {},         // each event as it is handed to the egg
 *     onArrive: function (ev) {},        // a validated event's glow reached the core (tick an XP counter by ev.weight)
 *     stage: true,              // false: do not call egg.setStage (a page that drives the stage itself)
 *     demo: false,              // true: url holds an array of responses, played one per poll, then looped
 *     pace: 1,                  // demo only: multiply poll_s (a faster demo)
 *     giveUp404: 3              // stop after this many 404s in a row (no feed deployed here); 0 never stops
 *   });
 *   feed.stop(); feed.start(); feed.poll();
 *
 * Each good response is applied in this fixed order: (1) egg.setStage(stage); (2) if state is "meditating" and the
 * egg is not, egg.meditate(true), with {immediate: true} on the page's first poll; (3) each new event to egg.event,
 * validated ones as egg.event("validated", {kind}); (4) if state is neither "meditating" nor "waking" and the egg is
 * meditating, egg.meditate(false), so the wake follows the last absorb; (5) onState.
 *
 * Polls again after poll_s, at once when the page becomes visible, and not at all while it is hidden. On an error it
 * keeps the last good state and retries after poll_s (or retry-after), doubling up to 300 s. Unknown fields are
 * ignored; a feed with v above 2 still updates the egg block, but its events are ignored. Event ids already seen are
 * skipped. On the first poll with the egg meditating, the absorbs that already happened are not replayed.
 */
(function (root) {
  "use strict";
  var GAP = [0, 0.4, 1.2, 2.5, 2.5], REPLAY_S = 8, MAXQ = 50, KNOWN_V = 2;
  var LEVEL = { issue: 1, pr: 2, review: 2, merge: 3, release: 4 }, VLEVEL = { issue: 1, pr: 2, review: 2 };
  var STATES = { filling: 1, meditating: 1, waking: 1, cracking: 1, hatched: 1 };
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

  function connect(egg, o) {
    o = o || {};
    var url = o.url || "/api/egg/feed", cursor = null, seen = {}, seenList = [], first = true, timer = 0, on = false;
    var fails = 0, notFound = 0, poll = 10, busy = false, demoList = null, demoAt = 0, giveUp = o.giveUp404 == null ? 3 : +o.giveUp404;
    var doc = root.document;

    function level(e) {
      if (e.type === "validated") return has(VLEVEL, e.kind) ? VLEVEL[e.kind] : 1;
      return has(LEVEL, e.type) ? LEVEL[e.type] : 1;
    }
    function hand(e, replay) {
      try {
        if (e.type === "validated") {
          egg.event("validated", { kind: has(VLEVEL, e.kind) ? e.kind : "issue", replay: replay, weight: e.weight,
            onArrive: o.onArrive ? function () { o.onArrive(e); } : undefined });
        } else egg.event(has(LEVEL, e.type) ? e.type : "issue", { replay: replay, weight: e.weight });
      } catch (err) { /* a bad event never stops the feed */ }
      if (o.onEvent) o.onEvent(e);
    }
    function replayWindow(list) {                                      // newest events that fit in 8 s at replay pacing
      var keep = [], t = 0;
      for (var i = list.length - 1; i >= 0; i--) { t += GAP[level(list[i])] * 0.6; if (t > REPLAY_S) break; keep.unshift(list[i]); }
      return keep;
    }
    function remember(id) {
      seen[id] = 1; seenList.push(id);
      if (seenList.length > 2000) seenList.splice(0, 1000).forEach(function (x) { delete seen[x]; });
    }
    function apply(j) {
      if (!j || typeof j !== "object" || !j.egg || typeof j.egg !== "object") return false;
      var E = j.egg, st = has(STATES, E.state) ? E.state : "filling", firstPoll = first;
      first = false;
      if (typeof j.cursor === "string") cursor = j.cursor;
      if (typeof j.poll_s === "number" && j.poll_s > 0) poll = Math.min(300, Math.max(2, j.poll_s));
      // 1. the stage
      if (o.stage !== false && typeof E.stage === "number" && egg.setStage) egg.setStage(E.stage);
      // 2. meditation begins (immediately if the page opened mid-meditation)
      if (st === "meditating" && egg.meditate && !egg.meditating()) egg.meditate(true, firstPoll ? { immediate: true } : undefined);
      // 3. new events, in order
      var evs = !(j.v > KNOWN_V) && Array.isArray(j.events) ? j.events : [];
      var fresh = evs.slice(0, MAXQ).filter(function (e) { return e && typeof e.id === "string" && !seen[e.id]; });
      fresh.forEach(function (e) { remember(e.id); });
      if (firstPoll) {
        if (!o.replay) fresh = [];
        else {
          if (st === "meditating") fresh = fresh.filter(function (e) { return e.type !== "validated"; });
          fresh = replayWindow(fresh);
        }
      }
      fresh.forEach(function (e) { hand(e, firstPoll); });
      // 4. the wake, after the last absorb was queued
      if (st !== "meditating" && st !== "waking" && egg.meditating && egg.meditating()) egg.meditate(false);
      // 5. counters and text
      if (o.onState) o.onState(E);
      return true;
    }
    function next(ms) { clearTimeout(timer); if (on) timer = setTimeout(tick, ms); }
    function tick() {
      if (!on || busy) return;
      if (doc && doc.hidden) return;                                   // resumes on visibilitychange
      if (o.demo) return demoTick();
      busy = true;
      var u = url + (cursor ? (url.indexOf("?") < 0 ? "?" : "&") + "since=" + encodeURIComponent(cursor) : "");
      fetch(u, { headers: { accept: "application/json" } }).then(function (r) {
        if (r.status === 404) notFound++; else notFound = 0;
        if (!r.ok) { var e = new Error("http " + r.status); e.retry = +r.headers.get("retry-after") || 0; throw e; }
        return r.json();
      }).then(function (j) {
        busy = false; fails = 0;
        apply(j);
        next(poll * 1000);
      }).catch(function (e) {
        busy = false;
        if (giveUp && notFound >= giveUp) { api.stop(); api.gaveUp = true; return; }   // no feed here: stop asking
        fails++;
        next(1000 * Math.max(e && e.retry || 0, Math.min(300, poll * Math.pow(2, fails))));
      });
    }
    function demoTick() {                                              // one response per poll, then loop
      if (!demoList) {
        busy = true;
        return fetch(url).then(function (r) { return r.json(); }).then(function (j) {
          busy = false; demoList = Array.isArray(j) ? j : [j]; demoAt = 0; demoTick();
        }).catch(function () { busy = false; next(30000); });
      }
      if (!demoList.length) return;
      if (demoAt >= demoList.length) { demoAt = 0; seen = {}; seenList = []; }   // the loop replays the same ids
      apply(JSON.parse(JSON.stringify(demoList[demoAt++])));
      next(poll * 1000 * (o.pace > 0 ? o.pace : 1));
    }
    function onVis() { if (!doc.hidden && on) { clearTimeout(timer); tick(); } }
    var api = {
      start: function () {
        if (!on) { on = true; if (doc) doc.addEventListener("visibilitychange", onVis); tick(); }
        return api;
      },
      stop: function () {
        on = false; clearTimeout(timer);
        if (doc) doc.removeEventListener("visibilitychange", onVis);
        return api;
      },
      poll: function () { if (on) { clearTimeout(timer); tick(); } return api; },
      gaveUp: false
    };
    return api.start();
  }
  root.ExoEggFeed = { connect: connect };
})(window);
