"""
Module 2 Bridge Project - LangGraph STARTER

Build an agent that searches the web, decides whether what it found is good
enough, and then does ONE OF TWO different things.

    question
       |
    plan_query    (LLM)   turn the question into a good search query
       |
    run_search    (TOOL)  a real web call - no model in this node at all
       |
    assess        (LLM)   is this evidence good enough to answer honestly?
       |
       +--- ENOUGH -----> write_answer   answer, and cite the source URLs
       |
       +--- NOT_ENOUGH -> report_gap     do NOT answer. say what is missing.

This is the first thing you have built where the path is not decided in
advance. Everything before today ran the same steps in the same order every
single time. This one asks a question and goes a different way depending on
the answer.

The search tool is already written for you in ../search_tools.py. You do not
need an API key for it and you do not need to install anything.

Run:
    python research_langgraph_starter.py "Anthropic was founded by ex-OpenAI staff"
    python research_langgraph_starter.py "the flurbotron 9000 was released in 2019"

The second one is nonsense on purpose. Your agent must refuse it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal, TypedDict

from dotenv import find_dotenv, load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from search_tools import NO_RESULTS, SEARCH_UNAVAILABLE, web_search  # noqa: E402

load_dotenv()
load_dotenv(find_dotenv(usecwd=True))

api_key = os.environ.get("OPENROUTER_API_KEY")
base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
if not api_key:
    raise RuntimeError(
        "OPENROUTER_API_KEY not found.\n"
        "Create a file named .env in the project folder containing:\n"
        "    OPENROUTER_API_KEY=sk-or-...\n"
        "then run this script again."
    )


# TODO 1 - build the model client, same as every week.
# llm = ChatOpenAI(
#     model="openai/gpt-4o-mini",
#     temperature=0,
#     base_url=base_url,
#     api_key=api_key,
# )


# ---------------------------------------------------------------------------
# STATE - the luggage that travels the route.
# ---------------------------------------------------------------------------
class DeskState(TypedDict):
    question: str      # the claim or topic that came in
    query: str         # plan_query writes here
    evidence: str      # run_search writes here
    verdict: str       # assess writes here  <- THIS DRIVES THE ROUTE
    reasoning: str     # assess writes here
    output: str        # write_answer OR report_gap writes here
    route_taken: str   # so you can PROVE which path ran


def plan_query(state: DeskState) -> DeskState:
    """Node 1: turn the question into something worth searching for."""
    # TODO 2 - build a prompt from state["question"], call the model, store the
    #          result in state["query"], and RETURN state.
    #
    #          Ask for ONE short query and nothing else. If the model replies
    #          "Sure! Here is a good query: ..." you will search for that
    #          whole sentence.
    raise NotImplementedError


def run_search(state: DeskState) -> DeskState:
    """Node 2: the tool. There is no model call in this node at all."""
    # TODO 3 - call web_search(state["query"]), store the result in
    #          state["evidence"], and RETURN state.
    #
    #          Two lines of code. But notice what it proves: a node is just a
    #          python function. It does not have to talk to an LLM.
    raise NotImplementedError


def assess(state: DeskState) -> DeskState:
    """Node 3: is this evidence actually good enough to answer with?"""
    # TODO 4 - handle the two failure markers BEFORE you call the model.
    #
    #          web_search can return exactly SEARCH_UNAVAILABLE or NO_RESULTS.
    #          There is no point paying for a model call to look at the word
    #          "NO_RESULTS". Set the verdict to "NOT_ENOUGH" yourself, write a
    #          short reason into state["reasoning"], and return early.
    #
    #          Keep them separate. "I found nothing" and "I could not look"
    #          are not the same thing and your report should not pretend
    #          they are.

    # TODO 5 - now ask the model whether the evidence answers the question.
    #
    #          Ask for 1-3 short bullets, then exactly one word on the final
    #          line: ENOUGH or NOT_ENOUGH.
    #          Store the verdict in state["verdict"] using read_verdict below,
    #          keep the full text in state["reasoning"], and RETURN state.
    raise NotImplementedError


def read_verdict(raw: str) -> str:
    """Pull the verdict out of the model's free text.

    TODO 6 - return "ENOUGH" or "NOT_ENOUGH".

    Read this carefully. There are two traps in four lines.

      1. The model does not reliably obey "one word on the last line". You
         will see "FINAL: NOT_ENOUGH", a trailing full stop, a whole polite
         sentence. Take the last non-empty line and look inside it.

      2. There is a much nastier one hiding in the two words themselves.
         Look at them. Really look:

             ENOUGH
             NOT_ENOUGH

         If you get this wrong, your agent will confidently answer questions
         it has no evidence for, and NOTHING will error. No exception, no
         warning. Just a wrong answer delivered with total confidence.

    Also decide what to do when you cannot tell. There is a safe direction
    and an unsafe one. Pick the safe one.
    """
    raise NotImplementedError


def choose_next(state: DeskState) -> Literal["write_answer", "report_gap"]:
    """THE ROUTER. Runs no model, writes no state. It only names the next node."""
    # TODO 7 - return "write_answer" if the verdict is ENOUGH, else "report_gap".
    raise NotImplementedError


def write_answer(state: DeskState) -> DeskState:
    """Node 4a: the ENOUGH path."""
    # TODO 8 - answer the question using ONLY state["evidence"].
    #
    #          Rules to put in your prompt:
    #            - every claim must come from the evidence, invent nothing
    #            - quote at least one source URL
    #            - if part of the question is not covered, say so
    #            - under 180 words
    #
    #          Store it in state["output"], set state["route_taken"], return state.
    raise NotImplementedError


def report_gap(state: DeskState) -> DeskState:
    """Node 4b: the NOT_ENOUGH path. This node must NOT answer the question."""
    # TODO 9 - report the gap instead of answering.
    #
    #          Your prompt must produce:
    #            - one sentence saying plainly this could not be verified
    #            - one or two bullets on what is missing
    #            - a final line in exactly this form:
    #                NEXT SEARCH: <the one query you would run next>
    #
    #          Store it in state["output"], set state["route_taken"], return state.
    #
    #          That last line matters more than it looks. Come back to it when
    #          you write your README.
    raise NotImplementedError


def build_graph():
    graph = StateGraph(DeskState)

    # TODO 10 - register all five nodes.

    # TODO 11 - the fixed part of the route:
    #           entry point is plan_query, then
    #           plan_query -> run_search -> assess

    # TODO 12 - THE LINE.
    #
    #           Everything you have built so far used add_edge: "after this
    #           node, always go there". This is different.
    #
    #               graph.add_conditional_edges(
    #                   "assess",              # after this node runs
    #                   choose_next,           # call this function
    #                   {                      # and map what it returns
    #                       "write_answer": "write_answer",
    #                       "report_gap":   "report_gap",
    #                   },
    #               )
    #
    #           The next step is chosen at RUNTIME, from something the model
    #           produced. That is the difference between a script and an agent.

    # TODO 13 - send BOTH write_answer and report_gap to END.
    #           Only one of them will ever run in a single execution.

    return graph.compile()


def run(question: str) -> DeskState:
    return build_graph().invoke({
        "question": question,
        "query": "",
        "evidence": "",
        "verdict": "",
        "reasoning": "",
        "output": "",
        "route_taken": "",
    })


def main() -> int:
    question = " ".join(sys.argv[1:]).strip() or \
        "Anthropic was founded by former OpenAI employees"

    result = run(question)

    print("=" * 70)
    print(f"QUESTION : {result['question']}")
    print("=" * 70)
    print(f"\n[1] SEARCH QUERY\n{result['query']}")
    print(f"\n[2] EVIDENCE ({len(result['evidence'])} chars)")
    print(result["evidence"][:700])
    print(f"\n[3] ASSESSMENT\n{result['reasoning']}")
    print("\n" + "=" * 70)
    print(f"VERDICT : {result['verdict']}")
    print(f"ROUTE   : {result['route_taken'].upper()}")
    print("=" * 70)
    print(result["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
