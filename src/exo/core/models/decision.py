"""Decision models: TypeSafe Jev, local Laya, and a generative fallback (D-015, D-018).

Decision models return probabilities, not text (research/DECISION-MODELS.md).
Wire format, from https://docs.typesafe.ai/api.md and checked live:

    POST {base_url}/systemone        Authorization: Bearer <key>
    {"state": ..., "model": "jev-latest", "questions": {id: question}}
    question: {"type": "choice", "instructions": str, "criteria": {option: description | null}}
              {"type": "noul",   "instructions": str, "criteria": {"true": str, "false": str}}  (criteria optional)
    reply:    {"model", "usage": {input_tokens, output_tokens}, "answers": {id: answer}}
    answers:  choice {"choice", "probabilities": {option: p}, "confidence"}
              noul   {"noul": p_yes}

Public API for the gate and the learning loop (all async, routed through the router's ``decision``
role with fallback to aux then main):

    ask_choice_result(router, state, question, options, role="decision") -> ChoiceResult
        the typed result with ``probabilities``, ``source``, ``route``, ``confidence``.

When the route is a generative chat model the same API asks for a JSON answer and reports the chosen
option as probability 1.0 with ``source="generative"``. Per D-015 a decision model may only make a gate
stricter: callers pass its verdict through ``tighten_only``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Sequence

import httpx

from exo.core.models.base import RETRYABLE_STATUS, USER_AGENT, error_event, parse_retry_after
from exo.core.types import Message, ProviderEvent, ToolSpec

DEFAULT_MODEL = "jev-latest"
MAX_OPTIONS = 255
GATE_ORDER = ("allow", "log", "ask", "deny")  # loosest to strictest (exo.gate.OUTCOMES)


# --- errors and typed results ----------------------------------------------------------------

class DecisionError(RuntimeError):
    """A decision call failed. ``retryable`` says whether the same call may succeed later or on another
    route (429, 529, 5xx, network); 401 and 422 are not (fix the key or the question)."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float | None = None


@dataclass
class NoulAnswer:
    noul: float  # probability of yes


Answer = ChoiceAnswer | NoulAnswer


@dataclass
class DecisionReply:
    answers: dict[str, Answer]
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ChoiceResult:
    choice: str
    probabilities: dict[str, float]
    source: str  # "decision" (a probability model) or "generative" (a chat model fallback)
    route: str = ""
    confidence: float | None = None


# --- question builders -------------------------------------------------------------------------

def choice_question(instructions: Any, options: Sequence[str] | Mapping[str, Any]) -> dict[str, Any]:
    """``options`` is a list of names or a map of name to description."""
    crit = dict(options) if isinstance(options, Mapping) else {o: o for o in options}
    if not 2 <= len(crit) <= MAX_OPTIONS:
        raise ValueError(f"choice needs 2 to {MAX_OPTIONS} options, got {len(crit)}")
    return {"type": "choice", "instructions": instructions, "criteria": crit}


def noul_question(instructions: Any, true: str | None = None, false: str | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def parse_answer(raw: Mapping[str, Any]) -> Answer:
    kind = raw.get("type")
    try:
        if kind == "choice":
            return ChoiceAnswer(str(raw["choice"]), {str(k): float(v) for k, v in raw["probabilities"].items()},
                                _num(raw.get("confidence")))
        if kind == "noul":
            return NoulAnswer(float(raw["noul"]))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise DecisionError(f"malformed {kind} answer: {type(e).__name__}", None, False) from None
    raise DecisionError(f"unknown answer type {kind!r}", None, False)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


# --- the gate rule -----------------------------------------------------------------------------

def tighten_only(current: str, proposed: str, order: Sequence[str] = GATE_ORDER) -> str:
    """D-015: a decision model may make a gate stricter, never looser. Returns the stricter of the two.
    An unknown ``proposed`` value is ignored (the current outcome stands); an unknown ``current`` raises."""
    if current not in order:
        raise ValueError(f"unknown current outcome {current!r}; expected one of {', '.join(order)}")
    if proposed not in order:
        return current
    return proposed if order.index(proposed) > order.index(current) else current


# --- transport ---------------------------------------------------------------------------------

class DecisionProvider:
    """Adapter for /systemone-style decision APIs (TypeSafe Jev, a local ``laya-serve``)."""

    path = "/systemone"
    is_decision = True

    def __init__(self, base_url: str, api_key: str | None = None, *, name: str = "decision",
                 client: Any = None, headers: dict[str, str] | None = None, timeout: float = 30.0,
                 **_: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.name = name
        self.extra_headers = dict(headers or {})
        self._client = client
        self._timeout = timeout

    def headers(self) -> dict[str, str]:
        h = {"content-type": "application/json", "user-agent": USER_AGENT, **self.extra_headers}
        if self.api_key:
            h["authorization"] = f"Bearer {self.api_key}"
        return h

    async def decide(self, state: Any, questions: Mapping[str, Mapping[str, Any]], *,
                     model: str = DEFAULT_MODEL) -> DecisionReply:
        """Ask typed questions about ``state``. Raises DecisionError (see its ``retryable``)."""
        body = {"state": state, "model": model, "questions": {k: dict(v) for k, v in questions.items()}}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.post(self.base_url + self.path, json=body, headers=self.headers())
        except httpx.HTTPError as e:
            raise DecisionError(f"{type(e).__name__}: {e}", None, True) from None
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code >= 400:
            raise DecisionError(f"HTTP {resp.status_code}: {_error_text(resp)}", resp.status_code,
                                resp.status_code in RETRYABLE_STATUS,
                                parse_retry_after(resp.headers.get("retry-after")))
        try:
            data = resp.json()
            raw_answers = data["answers"]
        except (ValueError, KeyError, TypeError):
            raise DecisionError("reply has no 'answers'", resp.status_code, True) from None
        usage = data.get("usage") or {}
        return DecisionReply({k: parse_answer(v) for k, v in raw_answers.items()}, str(data.get("model") or ""),
                             int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))

    async def stream(self, messages: list[Message], tools: list[ToolSpec],
                     **opts: Any) -> AsyncIterator[ProviderEvent]:
        yield error_event(f"decision provider {self.name!r} returns probabilities, not chat text; "
                          "falling back", None, retryable=True)


def _error_text(resp: httpx.Response) -> str:
    try:
        d = resp.json()
        err = d.get("error", d.get("detail", d)) if isinstance(d, dict) else d
        return (err.get("message") or str(err) if isinstance(err, dict) else str(err))[:500]
    except ValueError:
        return resp.text[:500]


# --- routed API --------------------------------------------------------------------------------

async def _run(router: Any, role: str, state: Any, question: dict[str, Any]) -> tuple[ChoiceAnswer, str, str]:
    """Try each route of ``role`` in order. Returns (answer, source, route ref)."""
    last: DecisionError | None = None
    tried = 0
    for route in router.chain(role):
        picked = router._pick(route, router.clock())
        while picked is not None:
            cred, sole = picked
            tried += 1
            try:
                if getattr(cred.provider, "is_decision", False):
                    reply = await cred.provider.decide(state, {"q": question}, model=route.model)
                    ans = reply.answers.get("q")
                    if not isinstance(ans, ChoiceAnswer):
                        raise DecisionError("reply lacks a choice answer", None, True)
                    router.record_success(cred.id, route.model)
                    return ans, "decision", route.ref
                ans = await _generative(router, route, cred, sole, state, question)
                return ans, "generative", route.ref
            except DecisionError as e:
                last = e
                if e.status in (400, 413, 422):  # the question itself is bad; another route will fail too
                    raise
                router.record_failure(cred.id, route.model, e.status, e.retry_after, sole)
            picked = router._pick(route, router.clock())
    if last is not None:
        raise DecisionError(f"all routes for {role!r} failed; last error: {last}", last.status,
                            last.retryable, last.retry_after)
    raise DecisionError(f"no decision route available for role {role!r}" + ("" if tried else
                        " (no credentials: set the provider key or start the local server)"), None, False)


async def _generative(router: Any, route: Any, cred: Any, sole: bool, state: Any,
                      question: dict[str, Any]) -> ChoiceAnswer:
    """Ask a chat model for a JSON answer and map it onto the decision shapes (probability 1.0)."""
    messages = [Message.system(_SYSTEM_PROMPT), Message.user(_user_prompt(state, question))]
    text: list[str] = []
    failed: list[ProviderEvent] = []
    async for ev in router._attempt(route, cred, sole, messages, [], {"max_tokens": 200, "temperature": 0}, failed):
        if ev.kind == "text":
            text.append(ev.text)
        elif ev.kind == "error":
            raise DecisionError(ev.error or "model error", ev.status, bool(ev.retryable), ev.retry_after)
        elif ev.kind == "done" and ev.message is not None and not text:
            text.append(ev.message.text())
    if failed:
        f = failed[0]
        raise DecisionError(f.error or "model error", f.status, bool(f.retryable), f.retry_after)
    return _parse_generative("".join(text), question)


_SYSTEM_PROMPT = ("You are a decision function. Answer with one JSON object and nothing else, "
                  'shaped {"choice": "<exactly one option name>"}.')


def _user_prompt(state: Any, question: dict[str, Any]) -> str:
    st = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    crit = question.get("criteria")
    crit_s = "\n".join(f"- {k}: {v}" if v not in (None, k) else f"- {k}" for k, v in crit.items())
    ins = question["instructions"]
    ins = ins if isinstance(ins, str) else json.dumps(ins, ensure_ascii=False)
    return f"State:\n{st}\n\nQuestion: {ins}\n\n" + (f"Options:\n{crit_s}\n" if crit_s else "")


def _parse_generative(text: str, question: dict[str, Any]) -> ChoiceAnswer:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        obj = json.loads(m.group(0)) if m else {}
    except ValueError:
        obj = {}
    if not isinstance(obj, dict) or "choice" not in obj:
        raise DecisionError("model did not return the requested JSON answer", None, True)
    val = obj["choice"]
    options = list(question["criteria"])
    pick = next((o for o in options if o == val), None) or next((o for o in options if str(o).lower() == str(val).strip().lower()), None)
    if pick is None:
        raise DecisionError(f"model chose {str(val)[:60]!r}, not one of the options", None, True)
    return ChoiceAnswer(pick, {o: 1.0 if o == pick else 0.0 for o in options}, None)


async def ask_choice_result(router: Any, state: Any, question: Any, options: Sequence[str] | Mapping[str, Any], *,
                            role: str = "decision") -> ChoiceResult:
    ans, source, route = await _run(router, role, state, choice_question(question, options))
    return ChoiceResult(ans.choice, ans.probabilities, source, route, ans.confidence)
