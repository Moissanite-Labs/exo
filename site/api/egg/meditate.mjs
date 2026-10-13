// POST /api/egg/meditate: signed start or stop of a meditation (docs/LIVE-EGG.md section 3.4).
import { handleMeditate } from "../_lib/egg/http.mjs";
export function POST(request) { return handleMeditate(request); }
