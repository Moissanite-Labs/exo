// POST /api/egg/webhook: the GitHub webhook receiver for x0 (docs/LIVE-EGG.md section 3.2).
import { handleWebhook } from "../_lib/egg/http.mjs";
export function POST(request) { return handleWebhook(request); }
