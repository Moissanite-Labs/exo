"""Plain terminal summary of the core's read-only Exopet result."""


def summary(status: dict) -> str:
    if status.get("schema_version") != 1:
        return "Exopet status is unavailable from this core."
    lines = ["Exopet · egg · community hatch pending"]
    personal = status.get("personal") or {}
    xp, level = personal.get("xp"), personal.get("level")
    if isinstance(xp, (int, float)) and isinstance(level, int):
        lines.append(f"Personal level {level} · {xp:g} XP banked")
    else:
        lines.append("Personal growth unavailable")
    learned = status.get("learned_count")
    if isinstance(learned, int):
        lines.append(f"{learned} recorded promotions")
    verification = status.get("verification") or {}
    lines.append("Growth frozen at the last verified read." if status.get("stale") else
                 "Growth verified locally." if verification.get("state") == "verified" else "Growth is not verified.")
    return "\n".join(lines)
