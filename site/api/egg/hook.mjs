// POST /api/egg/hook: the same receiver under the name docs/LIVE-EGG.md section 3.2 uses.
import { handleWebhook } from "../_lib/egg/http.mjs";
export function POST(request) { return handleWebhook(request); }
