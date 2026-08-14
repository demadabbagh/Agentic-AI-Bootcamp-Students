"""
Module 2 Phase 1 Mini-Project — CrewAI starter.

Build one Agent with three sequential Tasks. Do not create three agents:
multi-agent collaboration is covered later in Module 7.

Run it with your own topic:
    python study_guide_crewai_starter.py "temperature in language models"
"""

from __future__ import annotations

import os
import sys

from dotenv import find_dotenv, load_dotenv
from crewai import Agent, Crew, LLM, Process, Task

load_dotenv()
load_dotenv(find_dotenv(usecwd=True))

api_key = os.environ.get("OPENROUTER_API_KEY")
if not api_key:
    raise RuntimeError(
        "OPENROUTER_API_KEY not found.\n"
        "Create a file named .env in your project folder containing:\n"
        "    OPENROUTER_API_KEY=sk-or-...\n"
        "then run this script again."
    )


# TODO 1
llm = LLM(
    model="openrouter/openai/gpt-4o-mini",
    temperature=0,
    api_key=api_key,
)


def build_crew() -> Crew:
    # TODO 2
    teacher = Agent(
        role="Patient Study Guide Teacher",
        goal=(
            "Create accurate, clear, and easy-to-understand study material "
            "without inventing facts."
        ),
        backstory=(
            "You are a patient teacher who explains technical topics in plain "
            "language. You avoid unnecessary jargon and never invent facts, "
            "statistics, or details you are unsure about."
        ),
        llm=llm,
        verbose=True,
    )

    # TODO 3
    explain_task = Task(
    description=(
        "Explain {topic} in 2-3 plain-language sentences. "
        "If the topic is 'Model Context Protocol', MCP refers to the open protocol "
        "for connecting AI applications to external tools and data sources. "
        "Make the explanation understandable for a beginner and technically accurate. "
        "Do not invent facts, statistics, definitions, or details. "
        "Do not create an example or quiz yet."
    ),
    expected_output=(
        "A technically accurate, clear 2-3 sentence plain-language "
        "explanation of the topic."
    ),
    agent=teacher,
)

    # TODO 4
    example_task = Task(
        description=(
            "Using the explanation from the previous task, create one practical "
            "example of {topic} and one common misconception. Clearly label and "
            "distinguish the example from the misconception."
        ),
        expected_output=(
            "One clearly labeled practical example and one clearly labeled "
            "common misconception."
        ),
        agent=teacher,
        context=[explain_task],
    )

    # TODO 5
    quiz_task = Task(
        description=(
            "Using the explanation and example from the earlier tasks, assemble "
            "the complete study guide for {topic}. Preserve the earlier material "
            "and include exactly these sections:\n"
            "## Explanation\n"
            "## Example and misconception\n"
            "## Quiz\n\n"
            "The Quiz section must contain exactly three questions followed by "
            "a matching answer key."
        ),
        expected_output=(
            "A complete study guide containing an Explanation section, an "
            "Example and misconception section, and a Quiz section with exactly "
            "three questions and a matching answer key."
        ),
        agent=teacher,
        context=[explain_task, example_task],
    )

    # TODO 6
    return Crew(
        agents=[teacher],
        tasks=[explain_task, example_task, quiz_task],
        process=Process.sequential,
        verbose=True,
        tracing=False,
    )


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]).strip() or "Model Context Protocol"

    crew = build_crew()
    result = crew.kickoff(inputs={"topic": topic})
    print(result)