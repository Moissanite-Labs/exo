# Anti-Loop Discipline

Prevents thinking loops where agents repeat the same work without gaining new information.

## The Rules

When you need to do something, ask: "Will this return new information?"

If the answer is no, stop. Do not repeat it.

**Rule 1: One load, then use**
Load a skill once in a turn. Use its content. Do not re-load it for verification.

**Rule 2: One read, then act**
Read a file once. Extract what you need. Act on it. Do not re-read to double-check.

**Rule 3: One plan, then execute**
Plan an action once. Execute it. Move to the next action. Do not re-plan an action already executed.

**Rule 4: Tool output is the receipt**
When a tool returns success, the output IS the verification. Do not re-read the target to confirm.

**Exception:** Published artifacts and live pages require read-back because the deployed state can differ from the local state.

## Verification Nuance

| Operation | Verification Method | Re-read Required? |
|-----------|---------------------|-------------------|
| Patch (write_file, patch tool) | Tool success + content hash | NO |
| Skill manage (create, patch) | Tool success | NO |
| Deployment (push, publish) | Read-back of live target | YES |
| Config change | Read-back of config | YES |
| File creation | Tool success | NO |

Local operations trust tool output. External operations (deployments, live pages) require read-back.

## When You Spot a Loop

If you notice you are re-reading or re-planning:

1. Stop. Do not continue the current cycle.
2. Check tool output. Did the tool already return success? If yes, move on.
3. Check what you already know. What did the first read tell you? Use that.
4. Act on existing knowledge. Do not re-read to confirm.

## Evidence Class

[VERIFIED — internal operating record] Grounded in the team's shared doctrine and dated session history. These are the team's own derived rules.
