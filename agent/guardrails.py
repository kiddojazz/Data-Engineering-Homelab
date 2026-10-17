"""Input and output guardrails.

Input:  every message is classified before the agent sees it (data question / greeting / out of
        scope / write request / prompt injection). Only data questions reach the SQL agent.
Output: a deterministic grounding check. Every number and date in the final answer must appear
        in a query result (or in the user's question / the SQL itself). Anything else is treated
        as possibly invented and the answer is retried or withheld.
"""

import json
import re
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field

from .prompts import INPUT_GUARD_PROMPT

Category = Literal["data_question", "greeting_or_help", "out_of_scope", "write_request", "prompt_injection"]


class InputDecision(BaseModel):
    category: Category = Field(description="The single best category for the user's message.")
    reason: str = Field(description="One short sentence explaining the choice.")


def is_content_filter_error(exc: Exception) -> bool:
    """Azure OpenAI's content filter / Prompt Shields rejected the request (e.g. a jailbreak attempt)."""
    return getattr(exc, "code", None) == "content_filter" or "content_filter" in str(exc)


def classify_input(llm, question: str, history: list) -> InputDecision:
    """LLM classification of the user's message, with recent history for follow-ups."""
    recent = [m for m in history if isinstance(m, (HumanMessage, AIMessage)) and m.content][-6:]
    history_text = "\n".join(f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {str(m.content)[:300]}"
                             for m in recent) or "(none)"
    classifier = llm.with_structured_output(InputDecision, method="function_calling")
    return classifier.invoke([
        ("system", INPUT_GUARD_PROMPT.format(history=history_text)),
        ("human", question),
    ])


# ----------------------------------------------------------------------------- grounding check ----

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w.])|(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s")   # "1. ..." / "2) ..." list numbering is not a figure


def _collect(value, numbers: set, strings: list) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        numbers.add(float(value))
    elif isinstance(value, str):
        strings.append(value)
        for match in _NUMBER.finditer(value):
            numbers.add(float(match.group().replace(",", "")))
    elif isinstance(value, dict):
        for v in value.values():
            _collect(v, numbers, strings)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _collect(v, numbers, strings)


def evidence_from(messages: list, question: str) -> tuple[set, str]:
    """All numbers and text the answer is allowed to rely on: tool results, the SQL, the question."""
    numbers: set = set()
    strings: list = [question]
    _collect(question, numbers, strings)
    for message in messages:
        if isinstance(message, ToolMessage) and not str(message.content).startswith(("QUERY REJECTED", "QUERY FAILED")):
            try:
                _collect(json.loads(message.content), numbers, strings)
            except (TypeError, ValueError):
                _collect(str(message.content), numbers, strings)
        elif isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                _collect(call.get("args", {}), numbers, strings)
    return numbers, "\n".join(strings)


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _number_supported(token: str, evidence: set) -> bool:
    value = float(token.replace(",", ""))
    places = _decimals(token.replace(",", ""))
    for candidate in evidence:
        for v in (candidate, candidate * 100):          # also accept a fraction shown as a percentage
            if round(v, places) == round(value, places) or abs(v - value) < 1e-9:
                return True
    return False


def unverified_figures(answer: str, messages: list, question: str) -> list:
    """Numbers, dates and times in the answer that no query result (or the question) supports."""
    numbers, text = evidence_from(messages, question)
    problems = []
    remainder = _LIST_MARKER.sub(" ", answer)
    for date in _DATE.findall(answer):
        if date not in text:
            problems.append(date)
        remainder = remainder.replace(date, " ")
    for clock in _TIME.findall(remainder):
        if clock not in text and f"{int(clock.split(':')[0]):02d}:{clock.split(':')[1]}" not in text:
            problems.append(clock)
        remainder = remainder.replace(clock, " ")
    for match in _NUMBER.finditer(remainder):
        token = match.group()
        if not _number_supported(token, numbers):
            problems.append(token)
    return sorted(set(problems), key=problems.index)


def last_successful_result(messages: list) -> dict | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            try:
                payload = json.loads(message.content)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict) and "rows" in payload:
                return payload
    return None


def withheld_answer(messages: list) -> str:
    """Honest fallback when the answer can't be verified: say so and show the raw query result."""
    lines = ["I couldn't fully verify my answer against the data, so I won't give you figures I "
             "can't back up."]
    result = last_successful_result(messages)
    if result and result.get("rows"):
        lines.append("Here is what the datamart returned for your question:")
        columns = result.get("columns", [])
        for row in result["rows"][:10]:
            lines.append("• " + ", ".join(f"{c}: {v}" for c, v in zip(columns, row)))
        if len(result["rows"]) > 10:
            lines.append(f"…and {len(result['rows']) - 10} more rows.")
    else:
        lines.append("I didn't get a usable query result. Could you rephrase or narrow the question?")
    return "\n".join(lines)
