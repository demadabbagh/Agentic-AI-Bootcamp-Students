"""The ChatResponse fields of the API contract (common/CONTRACT.md §2), built from what really happened:

- tool_calls: every MCP call (recorded by researcher.mcp_client._dispatch) and every agent's own Python tool call
  (logged by the tool itself), each {"tool", "ok", "ms"} plus "agent" and "via". Nothing is added or estimated.
- usage: the token counts CrewAI reports for the Coordinator and the planning agents; cost_usd is an estimate from
  the model's list price, or null when the price is not known.
- citations: one per source URL behind the itinerary's activities.
- reply: plain text written by Python from the itinerary and the checks; the LLM writes none of it.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import os
import re
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

from researcher.mcp_client import record_call

# USD per million tokens (input, output), for an ESTIMATE; the provider's bill is the authority.
MODEL_PRICES = {"openai/gpt-4.1-mini": (0.40, 1.60)}

_USAGE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("rihla_llm_usage", default=None)
USAGE_KEYS = ("successful_requests", "prompt_tokens", "completion_tokens")


# ------------------------------------------------------------------ tool_calls
def record_agent_tools(agent: str, calls: Any) -> None:
    """Add an agent's own tool calls (check_selection, check_day, ...) to this request's tool_calls."""
    for call in calls or []:
        if not isinstance(call, dict) or not isinstance(call.get("tool"), str):
            continue
        ms = call.get("ms")
        record_call({"tool": call["tool"], "ok": call.get("ok") is True,
                     "ms": ms if isinstance(ms, int) and not isinstance(ms, bool) else None,
                     "agent": agent, "via": "python"})


# ------------------------------------------------------------------ usage
@contextmanager
def usage_recording() -> Iterator[dict]:
    totals = {k: 0 for k in USAGE_KEYS}
    token = _USAGE.set(totals)
    try:
        yield totals
    finally:
        _USAGE.reset(token)


def record_llm_usage(metrics: Any) -> None:
    """Add CrewAI's UsageMetrics (or a dict of the same counters) for an LLM use outside the planning pipeline."""
    totals = _USAGE.get()
    if totals is None or metrics is None:
        return
    for key in USAGE_KEYS:
        value = metrics.get(key) if isinstance(metrics, dict) else getattr(metrics, key, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            totals[key] += value


def build_usage(extra: dict, performance: dict | None) -> dict:
    """The contract's usage: tokens CrewAI counted in this request (Coordinator + planning pipeline)."""
    pipeline = (performance or {}).get("llm_total") if isinstance(performance, dict) else None
    totals = {k: int(extra.get(k) or 0) + int((pipeline or {}).get(k) or 0) for k in USAGE_KEYS}
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
    price = MODEL_PRICES.get(model)
    cost = (None if price is None else
            round(totals["prompt_tokens"] / 1e6 * price[0] + totals["completion_tokens"] / 1e6 * price[1], 6))
    return {"prompt_tokens": totals["prompt_tokens"], "completion_tokens": totals["completion_tokens"],
            "cost_usd": cost, "llm_calls": totals["successful_requests"], "model": model,
            "note": "Token counts from CrewAI; cost_usd is an estimate from the model's list price."}


# ------------------------------------------------------------------ citations
def _source_title(url: str) -> str:
    parsed = urlparse(url)
    page = unquote(parsed.path.rsplit("/wiki/", 1)[-1]).replace("_", " ").strip("/") or parsed.netloc
    section = unquote(parsed.fragment).replace("_", " ")
    site = "Wikivoyage" if "wikivoyage" in parsed.netloc else parsed.netloc
    return f"{site}: {page}" + (f" ({section})" if section else "")


def build_citations(itinerary: dict | None) -> list[dict]:
    """One citation {id, title, url} per distinct source URL of the planned activities, in plan order."""
    seen: dict[str, dict] = {}
    for day in (itinerary or {}).get("days") or []:
        for activity in day.get("activities") or []:
            url = activity.get("source_url")
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                seen[url] = {"id": f"SRC-{len(seen) + 1}", "title": _source_title(url), "url": url}
    return list(seen.values())


# ------------------------------------------------------------------ reply
def _money(value: Any, currency: str) -> str:
    return f"{value:,.2f} {currency}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "unknown"


def _weekday(iso: str) -> str:
    try:
        return dt.date.fromisoformat(iso).strftime("%A")
    except (TypeError, ValueError):
        return ""


def _price_note(notes: Any) -> str | None:
    """The price derivation of an activity whose sources disagree (it carries both prices in local currency)."""
    if not isinstance(notes, str) or "disagree" not in notes.casefold():
        return None
    for part in notes.split("; "):
        if re.search(r"\d", part) and ("converted" in part or "disagree" in part.casefold()):
            return part.strip()
    return None


def itinerary_reply(itinerary: dict, summary: dict | None, verification: dict | None, budget: dict | None,
                    approvable: bool) -> str:
    cur = itinerary.get("currency") or "USD"
    days = itinerary.get("days") or []
    travellers = itinerary.get("travellers")
    unknown = sum(1 for d in days for a in d.get("activities") or [] if a.get("estimated_cost") is None)
    legs = {}
    for i, day in enumerate((summary or {}).get("days") or []):
        for j, a in enumerate(day.get("activities") or []):
            legs[(i, j, a.get("name"))] = (a.get("travel_minutes") if a.get("travel_minutes") is not None
                                           else a.get("walk_minutes"), a.get("travel_mode"))
    lines = [f"Here's a {len(days)}-day plan for {itinerary.get('destination')}"
             + (f", {travellers} traveller{'s' if travellers != 1 else ''}" if isinstance(travellers, int) else "")
             + f": estimated at {_money(itinerary.get('estimated_total'), cur)}"
             + (f" of your {_money(itinerary.get('budget_total'), cur)}" if itinerary.get("budget_total") is not None
                else "")
             + (f", plus {unknown} place(s) whose price is unknown" if unknown else "")
             + ". All prices are estimates from the sources shown, for entry fees and planned meals only."]
    for i, day in enumerate(days):
        stops = []
        for j, a in enumerate(day.get("activities") or []):
            minutes, mode = legs.get((i, j, a.get("name")), (None, None))
            if j and isinstance(minutes, int):
                stops.append(f"{minutes}-minute {'drive' if mode == 'driving' else 'walk'}")
            cost = a.get("estimated_cost")
            price = ("price unknown" if cost is None else "free" if cost == 0 else f"est. {_money(cost, cur)}")
            note = _price_note(a.get("notes"))
            stops.append(f"{a.get('start_time') or '--:--'} {a.get('name')} ({price}"
                         + (f"; {note}" if note else "") + ")")
        weekday = _weekday(day.get("date"))
        lines.append(f"Day {i + 1} · {day.get('date')}" + (f" ({weekday})" if weekday else "") + ": "
                     + (" → ".join(stops) if stops else "nothing planned"))
    for line in (summary or {}).get("limitations") or []:
        if line.startswith("Not scheduled:") or "walk(s)" in line or "No usable price" in line:
            lines.append(line)
    for line in (budget or {}).get("warnings") or []:
        if "disagree" in line or "the listing gives a range" in line:
            lines.append(line)
    problems = [f.get("explanation") for f in (verification or {}).get("findings") or []
                if f.get("state") in ("confirmed_problem", "unverifiable") and f.get("explanation")]
    lines += [f"Check: {p}" for p in problems[:6]]
    lines.append("Nothing has been booked or bought.")
    lines.append("Shall I save this plan? Approve or reject it." if approvable else
                 "This plan cannot be approved yet: " + {
                     "supported_by_records": "every check passed, but only against recorded data, not live sources.",
                     "confirmed_problem": "the verifier found problems it could not repair.",
                     "unverifiable": "some facts could not be checked.",
                 }.get((verification or {}).get("state"), "it has not passed verification."))
    return "\n".join(lines)


def build_reply(*, status: str, message: str | None, itinerary: dict | None, summary: dict | None,
                verification: dict | None, budget: dict | None, approvable: bool,
                memory_notes: list[str] | None = None) -> str:
    parts = list(memory_notes or [])
    if itinerary:
        parts.append(itinerary_reply(itinerary, summary, verification, budget, approvable))
    elif message:
        parts.append(message)
    else:
        parts.append({"needs_clarification": "I need a few more details to plan this trip.",
                      "unsupported_request": "Please send me a trip planning request."}.get(
            status, "I could not produce a plan for this request."))
    return "\n".join(p for p in parts if p)


def pending_summary(itinerary: dict, title: str, traveller_id: str | None) -> str:
    days = itinerary.get("days") or []
    start, end = (days[0].get("date"), days[-1].get("date")) if days else (None, None)
    return (f"Save '{title}' ({start} to {end}, about {_money(itinerary.get('estimated_total'), itinerary.get('currency') or 'USD')}"
            f" in known costs) for {traveller_id or 'an unidentified traveller'} in the Rihla Trip Store. "
            "Nothing is booked or paid.")
