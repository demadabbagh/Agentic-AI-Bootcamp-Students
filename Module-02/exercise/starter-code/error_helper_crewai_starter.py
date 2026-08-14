"""
Module 2 Exercise - CrewAI STARTER

Same job as the LangGraph version. One Agent, two Tasks.

    Task 1: explain the error
    Task 2: suggest the fix, using Task 1's answer

Watch for two things while you build it:

  1. You never draw an edge. `Process.sequential` is the control flow.
  2. Task 2 gets Task 1's output through `context=[...]`, not through a
     variable you pass yourself.

Run:
    python error_helper_crewai_starter.py        # sample 1
    python error_helper_crewai_starter.py 2      # sample 2
"""

from __future__ import annotations

import os
import sys

from dotenv import find_dotenv, load_dotenv
from crewai import Agent, Crew, LLM, Process, Task

# Boilerplate, not the lesson. Finds your .env from either location.
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


# The same three errors as the LangGraph version.
SAMPLES = {
    "1": """Traceback (most recent call last):
  File "hello_langgraph.py", line 12, in <module>
    api_key=os.environ["OPENROUTER_API_KEY"],
            ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
KeyError: 'OPENROUTER_API_KEY'""",

    "2": """Traceback (most recent call last):
  File "study_guide.py", line 61, in <module>
    print(result["explanation"])
          ~~~~~~^^^^^^^^^^^^^^^
TypeError: 'NoneType' object is not subscriptable""",

    "3": """Traceback (most recent call last):
  File "hello_crewai.py", line 4, in <module>
    from crewai import Agent, Task, Crew, Process
ModuleNotFoundError: No module named 'crewai'""",
}


# TODO 1 - build the model client. Note the "openrouter/" prefix: CrewAI
#          reads the provider from the model name.
llm = LLM(
    model="openrouter/openai/gpt-4o-mini",
    temperature=0,
    api_key=api_key,
)


def build_crew() -> Crew:
    # TODO 2 - one Agent: a patient debugging helper.
    #          role      - who they are
    #          goal      - what a good answer looks like
    #          backstory - how they behave (plain language, no jargon,
    #                      never guesses at code it cannot see)
    helper = Agent(
        role="Patient debugging tutor",
        goal=(
            "Explain Python errors clearly in 2-3 plain-language sentences and"
            " provide a concise, actionable fix when requested."
        ),
        backstory=(
            "You're patient and avoid jargon. You never guess at code you can't"
            " see; instead ask for missing context. Keep answers short and"
            " focused on the error and how to fix it."
        ),
        llm=llm,          # <- keep this. Without it CrewAI looks for OPENAI_API_KEY.
        verbose=True,
    )

    # TODO 3 - TASK 1: explain the error.
    #          {error_text} is filled in from kickoff(inputs=...).
    #          Ask for 2-3 sentences, plain language, no fix yet.
    explain_task = Task(
        description="A student hit this error: {error_text}",
        expected_output=(
            "2-3 plain-language sentences describing the root cause of the"
            " error (what's happening and why). No fix yet."
        ),
        agent=helper,
    )

    # TODO 4 - TASK 2: suggest the fix.
    #          Pass Task 1 in with context=[explain_task].
    #
    #          Then try this experiment, in order:
    #            a) context=[explain_task]  -> task 2 gets task 1. Works.
    #            b) delete the context line -> STILL WORKS. In a sequential
    #               crew, a task with no context= automatically receives
    #               every earlier task's output. CrewAI wired it for you.
    #            c) context=[]              -> now task 2 gets NOTHING, and
    #               the answer falls apart.
    #
    #          So context= is not what turns the hand-off on. It is how you
    #          CONTROL it - how you say "only this task, not everything
    #          before me". Compare that with LangGraph, where nothing is
    #          connected until you draw the edge yourself.
    fix_task = Task(
        description="Based on the diagnosis above, provide a short fix or"
        " steps the student can take to resolve the error.",
        expected_output=(
            "A concise, actionable fix (code change or config step) and a"
            " one-line verification the student can run to confirm success."
        ),
        agent=helper,
        context=[explain_task],
    )

    # TODO 5 - assemble one sequential Crew with both tasks.
    return Crew(
        agents=[helper],
        tasks=[explain_task, fix_task],
        process=Process.sequential,   # <- this one word is your control flow
        verbose=True,
        tracing=False,                # keep this, or runs pause to ask about traces
    )


if __name__ == "__main__":
    choice = sys.argv[1] if len(sys.argv) > 1 else "1"
    error_text = SAMPLES.get(choice, SAMPLES["1"])

    crew = build_crew()
    result = crew.kickoff(inputs={"error_text": error_text})

    print("\n" + "=" * 60)
    print("HOW TO FIX IT")
    print("=" * 60)
    print(result)
