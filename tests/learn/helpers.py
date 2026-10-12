"""Builders shared by the learn tests: ledger events, typed proposals, eval items and a scripted model."""
import json
from pathlib import Path

from exo.consolidate import propose_typed

AUTHOR = "exo-learn"


def utter(rt, text, session, role="user"):
    return rt.ledger.append("utterance", {"text": text, "role": role}, "principal" if role == "user" else "agent",
                            session=session)


def correction(rt, said, session, answer="old output"):
    out = utter(rt, answer, session, "assistant")
    return rt.ledger.append("verdict", {"verdict": "edit", "target": out["id"], "text": said}, "principal",
                            session=session)


def ruling(rt, text, session="s-rule"):
    return rt.ledger.append("ruling", {"text": text}, "principal", session=session)


def propose(rt, ptype="taste", *, evidence, claim=None, wants="plain status lines", avoid="decorations",
            domain="mind", **extra):
    item = {"type": ptype, "claim": claim or f"The principal wants {wants}, not {avoid}, because they said so.",
            "wants": wants, "avoid": avoid, "because": "they said so", "domain": domain, "evidence": list(evidence)}
    item.update(extra)
    (res,) = propose_typed(rt.store, rt.ledger, [item], author=AUTHOR)
    return res


def write_qa(root, n=24, domain="mind"):
    f = Path(root) / "training" / "eval-qa.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(json.dumps({"id": f"q{k}", "question": f"Write status line {k}",
                                       "gold": f"Status ok {k}", "domain": domain}) for k in range(n)) + "\n")


def scripted_model(*, good_marker="plain status", bad_marker="emoticons", base_good=False):
    """complete(role, prompt, system): the `main` role answers well when the system text holds `good_marker`
    and badly when it holds `bad_marker` (with `base_good` it also answers well when neither is there); the `eval` role is a judge that scores 1 on an exact match."""
    def complete(role, prompt, system):
        if role == "main":
            k = prompt.rsplit(" ", 1)[-1]
            sys_text = system or ""
            if bad_marker in sys_text:
                return f"Status ok {k} :)"
            return f"Status ok {k}" if (base_good or good_marker in sys_text) else f"Status fine {k}"
        want = prompt.split("What they wanted: ", 1)[1].split("\n", 1)[0].strip()
        got = prompt.split("Answer to grade: ", 1)[1].split("\n", 1)[0].strip()
        return "1" if want == got else "0"
    return complete
