"""
Module 2 Phase 1 Mini-Project — LangGraph starter.

Build the same three-task Study Guide Agent in LangGraph and CrewAI.
Read M2-Phase1-Mini-Project.md before filling in the TODOs.

Run it with your own topic:
    python study_guide_langgraph_starter.py "temperature in language models"
"""

from __future__ import annotations

import os
import sys
from typing import TypedDict

from dotenv import find_dotenv, load_dotenv
from langgraph.graph import END, StateGraph
from langchain_openai import ChatOpenAI

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
llm = ChatOpenAI(
    model="openai/gpt-4o-mini",
    temperature=0,
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)


class StudyGuideState(TypedDict):
    topic: str
    explanation: str
    example: str
    quiz: str


def explain_topic(state: StudyGuideState) -> StudyGuideState:
    """Task 1: explain the topic in plain language."""
    response = llm.invoke(
        f"""
Explain the topic "{state['topic']}" in 2-3 plain-language sentences.

If the topic is "Model Context Protocol", MCP refers to the open protocol
for connecting AI applications to external tools and data sources.

Requirements:
- Make it understandable for a beginner.
- Be technically accurate.
- Do not invent facts, statistics, definitions, or details.
- Do not create an example or quiz yet.
"""
    )

    return {
        **state,
        "explanation": response.content.strip(),
    }

def create_example(state: StudyGuideState) -> StudyGuideState:
    """Task 2: use the explanation to create an example and misconception."""
    # TODO 3
    response = llm.invoke(
        f"""
Topic:
{state['topic']}

Explanation:
{state['explanation']}

Using the topic and explanation above, provide:

1. One practical example.
2. One common misconception.

Clearly label the practical example and the misconception.
Do not create the quiz yet.
"""
    )

    return {
        **state,
        "example": response.content.strip(),
    }


def create_quiz(state: StudyGuideState) -> StudyGuideState:
    """Task 3: use earlier state to create three questions and answers."""
    # TODO 4
    response = llm.invoke(
        f"""
Create a mini-quiz for the following study material.

Topic:
{state['topic']}

Explanation:
{state['explanation']}

Example and misconception:
{state['example']}

Create exactly three questions followed by a matching answer key.
The questions must be based on the study material above.
"""
    )

    return {
        **state,
        "quiz": response.content.strip(),
    }


def build_graph():
    graph = StateGraph(StudyGuideState)

    # TODO 5
    graph.add_node("explain_topic", explain_topic)
    graph.add_node("create_example", create_example)
    graph.add_node("create_quiz", create_quiz)

    # TODO 6
    graph.set_entry_point("explain_topic")

    graph.add_edge("explain_topic", "create_example")
    graph.add_edge("create_example", "create_quiz")
    graph.add_edge("create_quiz", END)

    return graph.compile()


def run_study_guide(topic: str) -> StudyGuideState:
    app = build_graph()

    initial_state: StudyGuideState = {
        "topic": topic,
        "explanation": "",
        "example": "",
        "quiz": "",
    }

    return app.invoke(initial_state)


if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]).strip() or "Model Context Protocol"

    result = run_study_guide(topic)

    print(f"# Study Guide: {result['topic']}\n")
    print("## Explanation\n", result["explanation"])
    print("\n## Example and misconception\n", result["example"])
    print("\n## Quiz\n", result["quiz"])