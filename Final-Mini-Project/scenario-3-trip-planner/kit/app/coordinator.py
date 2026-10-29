import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from crewai import Agent, Crew, LLM, Process, Task


load_dotenv()


class TripRequestInfo(BaseModel):
    intent: Literal[
        "create_trip",
        "revise_trip",
        "approve_trip",
        "other",
    ]

    destination: str | None = None

    duration_days: int | None = Field(
        default=None,
        gt=0,
    )

    budget: float | None = Field(
        default=None,
        ge=0,
    )

    currency: str | None = None

    travellers: int | None = Field(
        default=None,
        ge=1,
        le=50,
    )

    start_date: str | None = None

    end_date: str | None = None

    interests: list[str] | None = None

    required_places: list[str] | None = None

    budget_style: Literal[
        "budget",
        "mid-range",
        "splurge",
    ] | None = None

    max_walk_minutes: int | None = Field(
        default=None,
        ge=1,
        le=240,
    )

    day_start: str | None = None

    day_end: str | None = None


def create_coordinator_agent() -> Agent:
    """
    Create the Coordinator Agent using CrewAI and OpenRouter.
    """

    api_key = os.getenv("OPENROUTER_API_KEY")

    model_name = os.getenv(
        "OPENROUTER_MODEL",
        "openai/gpt-4.1-mini",
    )

    base_url = os.getenv(
        "OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1",
    )

    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing from the environment."
        )

    llm = LLM(
        model=f"openrouter/{model_name}",
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_tokens=700,
    )

    return Agent(
        role="Trip Request Coordinator",
        goal=(
            "Understand the user's travel request, identify "
            "their intent, and extract structured trip details."
        ),
        backstory=(
            "You are the Coordinator Agent for Rihla Trip Planner. "
            "You understand travel requests and conversation context. "
            "You identify whether the user wants to create, revise, "
            "or approve a trip. "
            "You extract travel dates, travellers, budget, "
            "preferences, and requested places. "
            "You never invent missing travel information."
        ),
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=3,
    )


def understand_trip_request(
    user_request: str,
    previous_trip_details: dict | None = None,
) -> dict:
    """
    Understand the current user message using previous trip details.

    Returns structured trip information, including:
    - intent
    - destination
    - duration_days
    - budget
    - currency
    - travellers
    - start_date
    - end_date
    - interests
    - required_places
    - budget_style
    - max_walk_minutes
    - day_start
    - day_end
    """

    previous_trip_details = previous_trip_details or {}

    context = f"""
Previous trip details:
{previous_trip_details}

Current user message:
{user_request}

Instructions:

1. Identify the user's intent.

2. Allowed intents:
   - create_trip
   - revise_trip
   - approve_trip
   - other

3. If the user is starting a new trip request,
   return intent="create_trip".

4. If previous trip details exist and the user provides
   additional missing information, treat the message
   as a continuation of the existing trip request.
   Return intent="create_trip".

5. If the user asks to modify an existing itinerary,
   return intent="revise_trip".

6. If the user explicitly approves a proposed itinerary,
   return intent="approve_trip".

7. If the message is unrelated to trip planning,
   return intent="other".

8. Extract only the trip details explicitly provided
   or updated in the CURRENT message.

9. Do not copy previous trip details into the output
   unless the user explicitly mentions them again.

10. Do not invent missing information.

11. Extract the following fields when explicitly provided:
    - destination
    - duration_days
    - budget
    - currency
    - travellers
    - start_date
    - end_date
    - interests
    - required_places
    - budget_style
    - max_walk_minutes
    - day_start
    - day_end

12. Dates must use YYYY-MM-DD format.
    Do not invent a date if the user did not provide one.

13. Times must use HH:MM format (24-hour clock).

14. Currency must use a three-letter uppercase code
    such as USD, EUR, ILS, or JOD.

15. If the user provides a list of interests,
    extract them as a list of strings.

16. If the user explicitly names places they want
    to visit, extract them into required_places.

17. Return structured output matching TripRequestInfo.
"""

    coordinator = create_coordinator_agent()

    task = Task(
        description=(
            "Analyze the following travel request "
            "and conversation context:\n\n"
            f"{context}"
        ),
        expected_output=(
            "A structured TripRequestInfo object containing "
            "intent, destination, duration_days, budget, "
            "currency, travellers, start_date, end_date, "
            "interests, required_places, budget_style, "
            "max_walk_minutes, day_start, and day_end."
        ),
        agent=coordinator,
        output_pydantic=TripRequestInfo,
    )

    crew = Crew(
        agents=[coordinator],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )

    result = crew.kickoff()

    if result.pydantic is None:
        raise ValueError(
            "Coordinator did not return valid structured output."
        )

    return result.pydantic.model_dump()