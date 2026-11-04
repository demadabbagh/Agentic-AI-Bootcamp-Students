"""The AI review of a fast plan: a second stage, in the background, that can only ever offer a better plan.

Stage 1 (app/orchestrator.py, the trip form) answers quickly with a complete plan from Python's own selection and day
planner, checked by the same verifier as any plan. Stage 2, here, runs the Budget Analyst, Planner and Verifier agents
on the same research and keeps their plan as an OFFER, like a WHAT IF proposal:

- the plan on screen is never replaced: the traveller applies the offer or keeps their plan;
- an offer is made only from a plan at least as well verified as the one on screen;
- applying goes through the same approval gate as any new plan: a new action, and the old action stops working;
- an offer for a plan that has moved on (revised, applied, saved, rejected) is stale and cannot be applied;
- a failed review changes nothing: stage 1's plan stands.

A job belongs to the session it was started for, and only that session's owner can read it (app/auth/middleware.py).
It holds no user identity: the traveller comes from the request that applies it.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger("uvicorn.error")

MAX_JOBS = 2  # agent reviews at once: bounded, so a burst of plans cannot exhaust the provider or the server
_POOL = ThreadPoolExecutor(max_workers=MAX_JOBS, thread_name_prefix="rihla-ai-review")
_LOCK = threading.Lock()
_RANK = {"PASS": 2, "UNKNOWN": 1, "FAIL": 0}


def enabled() -> bool:
    return os.getenv("RIHLA_AI_REVIEW", "on").strip().lower() not in {"0", "off", "false", "no"}


def differences(before: dict, after: dict) -> list[str]:
    """What the offer changes, day by day, in words the traveller can check."""
    old = {d.get("date"): [a.get("name") for a in d.get("activities") or []] for d in before.get("days") or []}
    new = {d.get("date"): [a.get("name") for a in d.get("activities") or []] for d in after.get("days") or []}
    all_old = {n for names in old.values() for n in names}
    all_new = {n for names in new.values() for n in names}
    lines = []
    added, removed = sorted(all_new - all_old), sorted(all_old - all_new)
    if added:
        lines.append("Adds " + ", ".join(added[:6]) + (f" and {len(added) - 6} more" if len(added) > 6 else "") + ".")
    if removed:
        lines.append("Removes " + ", ".join(removed[:6]) + (f" and {len(removed) - 6} more" if len(removed) > 6 else "")
                     + ".")
    moved = sum(1 for date in new if new[date] != old.get(date) and (set(new[date]) & all_old))
    if moved:
        lines.append(f"Reorganises {moved} day{'s' if moved != 1 else ''}.")
    return lines


def start(session: dict, *, base_version: str | None, base_result: dict, review: Callable[[], dict]) -> dict:
    """Start the review of the plan just made; `review` plans again with the agents and commits nothing."""
    job_id = uuid.uuid4().hex
    with _LOCK:
        session["refinement"] = {"id": job_id, "status": "running", "base_version": base_version}
    _POOL.submit(_run, session, job_id, base_result, review)
    return {"status": "running"}


def _finish(session: dict, job_id: str, update: dict) -> None:
    with _LOCK:
        job = session.get("refinement")
        if isinstance(job, dict) and job.get("id") == job_id:  # a newer plan started its own review: this one is dropped
            job.update(update)


def _run(session: dict, job_id: str, base_result: dict, review: Callable[[], dict]) -> None:
    try:
        result = review()
    except Exception as exc:  # the fast plan stands; the reason stays in the log
        logger.warning("AI review failed: %s", type(exc).__name__)
        _finish(session, job_id, {"status": "failed"})
        return
    state = result.pop("_plan_state", None)
    itinerary = result.get("itinerary")
    if state is None or not isinstance(itinerary, dict):
        _finish(session, job_id, {"status": "failed"})
        return
    before = (base_result.get("verification") or {}).get("status")
    after = (result.get("verification") or {}).get("status")
    changes = differences(base_result.get("itinerary") or {}, itinerary)
    if not changes or _RANK.get(after, -1) < _RANK.get(before, -1):
        _finish(session, job_id, {"status": "no_change", "verification_status": before})  # nothing better to offer
        return
    _finish(session, job_id, {"status": "ready", "changes": changes, "verification_status": after,
                              "result": result, "state": state})


def _current_version(session: dict) -> str | None:
    state = session.get("plan_state")
    return getattr(state, "version", None)


def _is_open(session: dict, job: dict, base_version: str | None) -> bool:
    """The offer still concerns the plan on screen, and that plan has not been decided on."""
    if job.get("base_version") != _current_version(session) or (base_version and base_version != job.get("base_version")):
        return False
    return session.get("pending_action") is not None or session.get("pending_acceptance") is not None


def status(session: dict, base_version: str | None) -> dict[str, Any]:
    """What the dashboard may show. Never the plan itself, and nothing internal."""
    with _LOCK:
        job = session.get("refinement")
        if not isinstance(job, dict):
            return {"status": "none"}
        if not _is_open(session, job, base_version):
            return {"status": "stale"}
        return {k: job[k] for k in ("status", "changes", "verification_status") if k in job}


def take(session: dict, base_version: str | None) -> dict | None:
    """The offer's result, made the session's plan; None when there is no open offer. The caller sends it through the
    approval gate (app/main.py _plan_response), which replaces the pending action."""
    with _LOCK:
        job = session.get("refinement")
        if not isinstance(job, dict) or job.get("status") != "ready" or not _is_open(session, job, base_version):
            return None
        state, result, changes = job["state"], dict(job["result"]), job["changes"]
        session["plan_state"] = state
        session["itinerary"] = state.itinerary.to_trip_store()
        session["refinement"] = {"id": job["id"], "status": "applied", "base_version": state.version}
    result["revision"] = {"outcome": "revised", "changes": changes}
    return result
