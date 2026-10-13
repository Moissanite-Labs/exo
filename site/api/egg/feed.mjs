// GET /api/egg/feed?since=: the public x0 feed, v: 2 (docs/LIVE-EGG.md section 1.1).
import { handleFeed } from "../_lib/egg/http.mjs";
export function GET(request) { return handleFeed(request); }
export function HEAD(request) { return handleFeed(request); }
