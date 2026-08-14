"""
Module 2 Exercise - LangGraph STARTER

Two nodes. That is the whole exercise.

    START -> explain_error -> suggest_fix -> END

Node 1 reads a Python traceback and says what went wrong in plain language.
Node 2 reads THAT EXPLANATION and suggests the fix.

Node 2 must not look at the raw traceback again. It works from node 1's
output. That hand-off is the only new idea here - on Friday your hello agent
had one node and nothing to hand over.

Run:
    python error_helper_langgraph_starter.py        # sample 1
    python error_helper_langgraph_starter.py 2      # sample 2
"""

from __future__ import annotations

import os
import sys
from typing import TypedDict

from dotenv import find_dotenv, load_dotenv
from langgraph.graph import END, StateGraph
from langchain_openai import ChatOpenAI

# Boilerplate, not the lesson. Finds your .env from either location.
load_dotenv()
load_dotenv(find_dotenv(usecwd=True))

api_key = os.environ.get("OPENROUTER_API_KEY")
base_url = os.environ.get("OPENROUTER_BASE_URL")
if not api_key or not base_url:
    missing = []
    if not api_key:
        missing.append("OPENROUTER_API_KEY")
    if not base_url:
        missing.append("OPENROUTER_BASE_URL")
    raise RuntimeError(
        f"Missing environment variables: {', '.join(missing)}.\n"
        "Create a file named .env in your project folder containing:\n"
        "    OPENROUTER_API_KEY=sk-or-...\n"
        "    OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n"
        "then run this script again."
    )


llm = ChatOpenAI(
    model="openai/gpt-4o-mini",
    temperature=0,
    base_url=base_url,
    api_key=api_key,
)


# Three real errors you will probably meet today.
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


# TODO 1 - build the model client. Same three lines as Friday.
# llm = ChatOpenAI(
#     model="openai/gpt-4o-mini",
#     temperature=0,
#     base_url="https://openrouter.ai/api/v1",
#     api_key=api_key,
# )


class HelperState(TypedDict):
    error_text: str    # what the user pasted in
    explanation: str   # node 1 writes here
    fix: str           # node 2 writes here


def explain_error(state: HelperState) -> HelperState:
    """Node 1: say what went wrong, in plain language."""
    prompt = (
        "You are an expert Python debugging assistant. Read the traceback below and "
        "explain what went wrong in plain language. Use 2-3 sentences, avoid jargon, "
        "and do not provide the fix yet. Only describe the error and its cause.\n\n"
        f"Traceback:\n{state['error_text']}"
    )
    response = llm.invoke(prompt)
    state["explanation"] = response.content.strip()
    return state


def suggest_fix(state: HelperState) -> HelperState:
    """Node 2: suggest the fix, based on node 1's explanation."""
    prompt = (
        "You are an expert Python debugging assistant. Based only on the explanation "
        "below, suggest the concrete steps to fix the problem. Do not look at the raw traceback. "
        "Write clear, actionable instructions.\n\n"
        f"Explanation:\n{state['explanation']}"
    )
    response = llm.invoke(prompt)
    state["fix"] = response.content.strip()
    return state


def build_graph():
    graph = StateGraph(HelperState)

    graph.add_node("explain_error", explain_error)
    graph.add_node("suggest_fix", suggest_fix)

    graph.set_entry_point("explain_error")
    graph.add_edge("explain_error", "suggest_fix")
    graph.add_edge("suggest_fix", END)

    return graph.compile()


def run_helper(error_text: str) -> HelperState:
    app = build_graph()
    initial_state: HelperState = {
        "error_text": error_text,
        "explanation": "",
        "fix": "",
    }
    return app.invoke(initial_state)


if __name__ == "__main__":
    choice = sys.argv[1] if len(sys.argv) > 1 else "1"
    error_text = SAMPLES.get(choice, SAMPLES["1"])

    result = run_helper(error_text)

    print("=" * 60)
    print("THE ERROR")
    print("=" * 60)
    print(result["error_text"])
    print("\n" + "=" * 60)
    print("WHAT WENT WRONG")
    print("=" * 60)
    print(result["explanation"])
    print("\n" + "=" * 60)
    print("HOW TO FIX IT")
    print("=" * 60)
    print(result["fix"])
