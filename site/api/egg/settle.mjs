// GET /api/egg/settle: the daily cron; folds, prunes, and starts the 14-day backstop meditation (section 3.5).
import { handleSettle } from "../_lib/egg/http.mjs";
export function GET(request) { return handleSettle(request); }
