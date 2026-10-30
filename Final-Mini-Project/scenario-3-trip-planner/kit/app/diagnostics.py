"""Console diagnostics for one planning run.

Only place names, decisions, reasons and counts: never prompts, keys,
traveller ids or other configuration.
"""
import os
import re
import traceback
from collections import Counter

MAX_REASON = 160
MAX_TRACEBACK = 12000

SECRET_ENV_VARS = ("OPENROUTER_API_KEY", "TRAVEL_API_KEY", "TRAVEL_TRAVELLER_ID")
_SECRET_PATTERNS = [
    re.compile(r"sk-or-[A-Za-z0-9_\-]{8,}"),  # OpenRouter keys
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),  # other provider keys
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)((?:x-api-key|api[_-]?key|authorization)['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"),
]


def redact(text: str) -> str:
    """Remove secrets: the configured values themselves, then anything shaped like a key or token."""
    for name in SECRET_ENV_VARS:
        value = os.getenv(name)
        if value and len(value) >= 4:
            text = text.replace(value, f"[{name} REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.re.groups else "") + "[REDACTED]", text)
    return text


def safe_traceback(exc: BaseException) -> str:
    """The full traceback with its causes (provider errors included), secrets redacted, for the server log
    only. Agent failures add their own evidence: guardrail rejection kinds and counts, never prompt text."""
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    seen, err = set(), exc
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        rejections = getattr(err, "rejections", None)
        if rejections:
            lines.append(f"{type(err).__name__} guardrail rejections: {rejections}\n")
        calls = getattr(err, "tool_calls", None)
        if calls:
            lines.append(f"{type(err).__name__} tool calls: {len(calls)}\n")
        err = err.__cause__ or err.__context__
    text = redact("".join(lines))
    return text if len(text) <= MAX_TRACEBACK else text[:MAX_TRACEBACK] + "\n... (truncated)"


def _clean(text) -> str:
    """One short line of untrusted text (place names, the agent's reasons)."""
    line = " ".join(str(text or "").split())
    return line if len(line) <= MAX_REASON else line[:MAX_REASON - 1] + "…"


def research_summary(bundle) -> list[str]:
    counts = Counter(place.category for place in bundle.places)
    return [
        "=== RESEARCH DIAGNOSTICS ===",
        f"Interest tags used: {', '.join(bundle.trip.interests) or 'none (no filter)'}",
        f"Attractions: {counts.get('attraction', 0)}",
        f"Restaurants: {counts.get('restaurant', 0)}",
        f"Tool failures: {len(bundle.failures)}",
        "============================",
    ]


def performance_summary(budget_report, planner_report, timings: dict | None) -> list[str]:
    """Where the time went: stage wall times, LLM guardrail retries, duplicate repairs, schedule search."""
    timings = timings if isinstance(timings, dict) else {}
    budget = getattr(budget_report, "performance", None)
    planner = getattr(planner_report, "performance", None)
    budget = budget if isinstance(budget, dict) else {}
    planner = planner if isinstance(planner, dict) else {}
    return [
        "=== PERFORMANCE ===",
        f"Budget Analyst: {timings.get('budget_analyst_ms', '?')} ms total, crew {budget.get('crew_ms', '?')} ms, "
        f"attempts {budget.get('attempts', '?')}, rejections {budget.get('rejections', [])}",
        f"Planner: {timings.get('planner_ms', '?')} ms total, crew {planner.get('crew_ms', '?')} ms, "
        f"attempts {planner.get('attempts', '?')}, rejections {planner.get('rejections', [])}",
        f"Duplicate repairs: {planner.get('duplicate_repair_runs', 0)} run(s), "
        f"{planner.get('duplicate_repairs_applied', 0)} applied, "
        f"{planner.get('repair_combinations_compared', 0)} combinations in {planner.get('repair_ms', 0)} ms",
        f"check_day calls: {planner.get('check_day_calls', '?')}, travel lookups: {planner.get('travel_lookups', '?')}",
        "===================",
    ]


def _items(title: str, items, *, reason_label: str) -> list[str]:
    lines = [f"{title} ({len(items)}):"]
    for item in items:
        lines.append(
            f"  - {_clean(item.name)} [{item.category}, cost {item.cost.status}]"
            f" {reason_label}: {_clean(item.reason) or '(none given)'}"
        )
    return lines


def budget_decisions(report) -> list[str]:
    """
    Who decided what. Selected, optional and excluded are the Budget
    Analyst LLM's choices; dropped and withheld are Python's rules.
    """
    lines = ["=== BUDGET DECISIONS ===", f"Status: {report.status}"]
    lines += _items("Selected (required + LLM include)", report.selected, reason_label="reason")
    lines += _items("Optional (LLM, not in plan)", report.optional, reason_label="reason")
    lines += _items("Excluded by LLM", report.excluded, reason_label="LLM reason")
    lines += _items("Dropped by Python budget rule (over budget)", report.dropped_for_budget,
                    reason_label="LLM reason before drop")
    lines.append(f"Withheld by Python safety rules ({len(report.withheld)}):")
    lines += [f"  - {_clean(w.name)}: {_clean(w.reason)}" for w in report.withheld]
    lines.append("========================")
    return lines


# ------------------------------------------------------------------ errors the API can explain
_ERROR_CLASSES = (
    # (exception class names anywhere in the cause chain, HTTP status, code, message, retryable)
    ({"APITimeoutError", "Timeout", "TimeoutError", "ReadTimeout", "ConnectTimeout"}, 504, "llm_timeout",
     "The AI provider took too long to answer. No trip was generated; please try again.", True),
    ({"RateLimitError"}, 429, "llm_rate_limited",
     "The AI provider is busy (rate limit). No trip was generated; please try again in a minute.", True),
    ({"AuthenticationError", "PermissionDeniedError"}, 503, "llm_unavailable",
     "The AI provider refused the request (check the API key configuration). No trip was generated.", False),
    ({"APIConnectionError"}, 503, "llm_unavailable",
     "The AI provider could not be reached. No trip was generated; please try again.", True),
    ({"BudgetAnalystError", "ItineraryPlannerError", "VerifierAgentError", "RevisionAgentError"}, 503,
     "planning_failed", "A planning step could not produce a valid result. No trip was generated.", True),
)


def _chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def classify_error(exc: BaseException) -> tuple[int, str, str, bool]:
    """(HTTP status, code, user-facing message, retryable) for an exception from the planning pipeline. Looks
    through the cause chain (CrewAI wraps provider errors); a provider's own 402 stays a 402."""
    chain = list(_chain(exc))
    for e in chain:
        if getattr(e, "status_code", None) == 402:
            return 402, "llm_payment_required", ("The AI provider declined the request (payment required). "
                                                 "No trip was generated."), False
    names = [type(e).__name__ for e in chain]
    for classes, status, code, message, retryable in _ERROR_CLASSES:
        if any(n in classes for n in names):
            return status, code, message, retryable
    return 503, "internal_error", "The trip planning service is temporarily unavailable. No trip was generated.", True
