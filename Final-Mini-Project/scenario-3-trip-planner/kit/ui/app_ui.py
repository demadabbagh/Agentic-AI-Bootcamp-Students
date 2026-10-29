"""Starter UI for the Rihla Trip Planner.

This Gradio UI communicates with the FastAPI backend:
- POST /api/chat
- POST /api/approve

Start the backend first:
    python -m uvicorn app.main:app --reload --port 8000

Then start the UI:
    python ui/app_ui.py

Open:
    http://127.0.0.1:7860
"""

from __future__ import annotations

import os
import uuid

import gradio as gr
import httpx


# --------------------------------------------------
# Configuration
# --------------------------------------------------

APP = (os.getenv("APP_URL") or "http://127.0.0.1:8000").rstrip("/")

COLUMNS = [
    "day",
    "time",
    "place",
    "estimated cost",
    "source",
]


# --------------------------------------------------
# API communication
# --------------------------------------------------

def post(path: str, body: dict) -> dict:
    """Send a request to the FastAPI backend."""

    try:
        response = httpx.post(
            f"{APP}{path}",
            json=body,
            timeout=300,
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, dict):
            return data

        return {"reply": str(data)}

    except Exception as exc:
        return {
            "reply": (
                f"Could not complete the request to {APP}. "
                f"Error: {type(exc).__name__}: {exc}"
            )
        }


# --------------------------------------------------
# Itinerary formatting
# --------------------------------------------------

def itinerary_rows(resp: dict) -> list[list]:
    """Convert an itinerary into rows for the Gradio table."""

    itinerary = resp.get("itinerary")

    if not isinstance(itinerary, dict):
        pending = resp.get("pending_action") or {}
        payload = pending.get("payload") or {}
        itinerary = payload.get("itinerary")

    rows = []

    if not isinstance(itinerary, dict):
        return rows

    currency = itinerary.get("currency") or ""

    for day in itinerary.get("days") or []:
        for activity in day.get("activities") or []:

            start_time = activity.get("start_time")
            end_time = activity.get("end_time")

            when = "-".join(
                value
                for value in (start_time, end_time)
                if value
            )

            cost = activity.get("estimated_cost")

            formatted_cost = (
                ""
                if cost is None
                else f"~{cost} {currency}".strip()
            )

            rows.append([
                day.get("date", ""),
                when,
                activity.get("name", ""),
                formatted_cost,
                activity.get("source_url") or "",
            ])

    estimated_total = itinerary.get("estimated_total")

    if estimated_total is not None:
        rows.append([
            "",
            "",
            "Estimated total",
            f"~{estimated_total} {currency}".strip(),
            "",
        ])

    return rows


# --------------------------------------------------
# Response details
# --------------------------------------------------

def details(resp: dict) -> str:
    """Display the backend status and supporting information."""

    lines = []

    status = resp.get("status")

    if status:
        lines.append(f"**Trip status:** `{status}`")

    if status == "verification_required":
        lines.append(
            "⚠️ The itinerary was generated, but final "
            "verification is still required. "
            "Approval and saving are disabled."
        )

    elif status == "needs_clarification":
        lines.append(
            "ℹ️ Rihla needs additional information "
            "before planning the trip."
        )

    elif status == "verified":
        lines.append(
            "✅ The backend reported that verification passed."
        )

    if resp.get("result"):
        lines.append(f"**Saved:** `{resp['result']}`")

    pending = resp.get("pending_action")

    if pending:
        lines.append(
            "**Waiting for your approval:** "
            + str(pending.get("summary", ""))
        )

    if resp.get("state"):
        lines.append(f"State: `{resp['state']}`")

    for tool in resp.get("tool_calls") or []:
        name = tool.get("tool")
        success = "ok" if tool.get("ok") else "FAILED"
        duration = tool.get("ms", "?")

        lines.append(
            f"- tool `{name}` {success} ({duration} ms)"
        )

    for citation in resp.get("citations") or []:
        title = citation.get("title") or citation.get("id")
        url = citation.get("url")

        if url:
            lines.append(f"- source: [{title}]({url})")
        else:
            lines.append(f"- source: {title}")

    return "\n\n".join(lines) or "_No additional details._"


# --------------------------------------------------
# Display backend response
# --------------------------------------------------

def show(history: list, resp: dict, state: dict):
    """Update chat, itinerary table, and approval buttons."""

    # Support both the old and current backend response fields.
    reply = (
        resp.get("reply")
        or resp.get("message")
        or "(No message returned by the backend.)"
    )

    history = history + [
        {
            "role": "assistant",
            "content": str(reply),
        }
    ]

    pending = resp.get("pending_action")

    # Save pending approval information in the UI session.
    state = dict(
        state,
        pending=pending,
    )

    # Read itinerary directly from the backend response,
    # or from pending_action if available.
    rows = itinerary_rows(resp)

    # Preserve the previously displayed itinerary if
    # this response does not contain a new one.
    if not rows:
        rows = state.get("rows") or []

    state["rows"] = rows

    # Never enable approval for an unverified itinerary.
    # The backend must also validate every approval request.
    waiting = (
        resp.get("status") == "verified"
        and isinstance(pending, dict)
        and bool(pending.get("action_id"))
    )

    return (
        history,
        rows,
        details(resp),
        state,
        gr.update(interactive=waiting),
        gr.update(interactive=waiting),
    )


# --------------------------------------------------
# Chat
# --------------------------------------------------

def chat(
    message: str,
    history: list,
    state: dict,
    traveller: str,
):
    """Send a user message to the Coordinator."""

    if not message.strip():
        return (
            "",
            history,
            state.get("rows") or [],
            "",
            state,
            gr.update(),
            gr.update(),
        )

    body = {
        "session_id": state["session"],
        "message": message,
    }

    if traveller.strip():
        body["user"] = {
            "traveller_id": traveller.strip()
        }

    history = history + [
        {
            "role": "user",
            "content": message,
        }
    ]

    response = post("/api/chat", body)

    return ("",) + show(history, response, state)


# --------------------------------------------------
# Trip planning
# --------------------------------------------------

def plan(
    dest,
    start,
    end,
    people,
    budget,
    currency,
    interests,
    traveller,
    history,
    state,
):
    """Build a trip request from the form."""

    message = (
        f"Plan a trip to {dest} "
        f"from {start} to {end} "
        f"for {int(people or 1)} traveller(s). "
        f"Total budget: {budget} {currency}. "
        f"Interests: {interests}."
    )

    return chat(
        message,
        history,
        state,
        traveller,
    )[1:]


# --------------------------------------------------
# Approval / rejection
# --------------------------------------------------

def decide(
    approved: bool,
    history: list,
    state: dict,
):
    """Send an approval or rejection decision."""

    pending = state.get("pending")

    if not pending:
        return show(
            history,
            {"reply": "Nothing is waiting for approval."},
            state,
        )

    response = post(
        "/api/approve",
        {
            "session_id": state["session"],
            "action_id": pending.get("action_id"),
            "approved": approved,
        },
    )

    history = history + [
        {
            "role": "user",
            "content": "Approve" if approved else "Reject",
        }
    ]

    return show(history, response, state)


# --------------------------------------------------
# Gradio interface
# --------------------------------------------------

with gr.Blocks(title="Rihla trip planner") as demo:

    state = gr.State({
        "session": f"ui-{uuid.uuid4().hex[:8]}",
        "pending": None,
        "rows": [],
    })

    gr.Markdown(
        "## 🌍 Rihla Trip Planner\n"
        "Plan your trip with AI agents.\n\n"
        "**Important:** Costs are estimates. "
        "Nothing is booked or purchased."
    )

    # Trip information
    with gr.Row():

        dest = gr.Textbox(
            "Istanbul",
            label="Destination",
        )

        start = gr.Textbox(
            "2026-11-02",
            label="From (YYYY-MM-DD)",
        )

        end = gr.Textbox(
            "2026-11-04",
            label="To (YYYY-MM-DD)",
        )

        people = gr.Number(
            1,
            label="Travellers",
            precision=0,
        )

        budget = gr.Number(
            700,
            label="Total budget",
        )

        currency = gr.Dropdown(
            ["USD", "EUR", "TRY", "JOD"],
            value="USD",
            label="Currency",
        )

    # Interests and traveller
    with gr.Row():

        interests = gr.Textbox(
            "history, local food, nature",
            label="Interests",
            scale=3,
        )

        traveller = gr.Textbox(
            "",
            label="Traveller id (optional, e.g. TRV-101)",
        )

        plan_btn = gr.Button(
            "Plan my trip",
            variant="primary",
        )

    # Conversation
    chatbot = gr.Chatbot(
        label="Conversation",
        height=380,
    )

    msg = gr.Textbox(
        label="Ask for changes",
        placeholder=(
            "Make day 2 cheaper / "
            "Cut the budget by 20% / ..."
        ),
    )

    # Approval buttons
    with gr.Row():

        approve = gr.Button(
            "Approve and save",
            variant="primary",
            interactive=False,
        )

        reject = gr.Button(
            "Reject",
            variant="stop",
            interactive=False,
        )

    # Itinerary table
    table = gr.Dataframe(
        headers=COLUMNS,
        label="Proposed itinerary",
        interactive=False,
        wrap=True,
    )

    # Backend status and details
    info = gr.Markdown()

    # Outputs shared by UI actions
    outs = [
        chatbot,
        table,
        info,
        state,
        approve,
        reject,
    ]

    # Plan trip
    plan_btn.click(
        plan,
        [
            dest,
            start,
            end,
            people,
            budget,
            currency,
            interests,
            traveller,
            chatbot,
            state,
        ],
        outs,
    )

    # Chat messages
    msg.submit(
        chat,
        [
            msg,
            chatbot,
            state,
            traveller,
        ],
        [msg] + outs,
    )

    # Approve
    approve.click(
        lambda h, s: decide(True, h, s),
        [chatbot, state],
        outs,
    )

    # Reject
    reject.click(
        lambda h, s: decide(False, h, s),
        [chatbot, state],
        outs,
    )


# --------------------------------------------------
# Run application
# --------------------------------------------------

if __name__ == "__main__":
    demo.launch()