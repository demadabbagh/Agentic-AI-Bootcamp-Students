import os
import time
from typing import Any

from crewai import LLM
from dotenv import load_dotenv

from planning.research import ResearchBundle
from planning.budget_analyst import run_budget_analyst
from planning.itinerary_planner import plan_itinerary


load_dotenv()


def create_planning_llm() -> LLM:
    """
    Create the shared LLM for the Budget Analyst
    and Itinerary Planner.
    """

    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY is missing from the environment."
        )

    model_name = os.getenv(
        "OPENROUTER_MODEL",
        "openai/gpt-4.1-mini",
    )

    base_url = os.getenv(
        "OPENROUTER_BASE_URL",
        "https://openrouter.ai/api/v1",
    )

    return LLM(
        model=f"openrouter/{model_name}",
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        # A ceiling, not a target: the Budget Analyst decides 30+ places (each with a rank, the interests it
        # serves and a short reason); 1500 cut that answer short in a live Paris run.
        max_tokens=4000,
    )


def run_planning_pipeline(
    research_bundle: ResearchBundle,
    llm: Any = None,
    *,
    travel_lookup=None,
) -> dict:
    """
    Run the real Budget Analyst and Itinerary Planner.

    The Research Agent is not connected yet.
    This function expects a prepared ResearchBundle.
    """

    if not isinstance(research_bundle, ResearchBundle):
        raise TypeError(
            "research_bundle must be a ResearchBundle instance."
        )

    if llm is None:
        llm = create_planning_llm()

    # STEP 1: Budget Analyst
    started = time.perf_counter()
    budget_report = run_budget_analyst(
        bundle=research_bundle,
        llm=llm,
    )
    budget_ms = round((time.perf_counter() - started) * 1000)

    # STEP 2: Itinerary Planner
    started = time.perf_counter()
    planner_report = plan_itinerary(
        report=budget_report,
        bundle=research_bundle,
        llm=llm,
        travel_lookup=travel_lookup,
    )
    planner_ms = round((time.perf_counter() - started) * 1000)

    # STEP 3: Prepare output
    itinerary = planner_report.itinerary

    return {
        "status": planner_report.status,
        "itinerary": (
            itinerary.to_trip_store()
            if itinerary is not None
            else None
        ),
        "budget_report": budget_report,
        "planner_report": planner_report,
        "itinerary_model": itinerary,
        "timings": {"budget_analyst_ms": budget_ms, "planner_ms": planner_ms},
    }