"""
Module 2 Bridge Project - CrewAI STARTER (hierarchical)

Same job as the LangGraph version. Build that one FIRST, then come back here.

WHAT IS DIFFERENT
    In LangGraph you drew the branch yourself, in one line you could point
    at. A hierarchical crew has no such line. Instead you hire a MANAGER and
    let it decide who works, in what order, and when the job is done.

    You are trading a decision you can READ for a decision you DELEGATE.

    Run both versions on the nonsense claim afterwards and watch carefully.
    In LangGraph, refusing is a code path - report_gap physically cannot
    produce an answer. Here, refusing is an instruction in a backstory. You
    are asking the manager nicely.

    Whether it listens is the most interesting result in this project, and
    it goes in your README either way.

Run:
    python research_crewai_starter.py "Anthropic was founded by ex-OpenAI staff"
    python research_crewai_starter.py "the flurbotron 9000 was released in 2019"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# CrewAI asks about execution traces on the first run in a new folder and
# blocks for 20 seconds waiting for an answer. This stops that.
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai import Agent, Crew, LLM, Process, Task  # noqa: E402
from crewai.tools import tool  # noqa: E402
from dotenv import find_dotenv, load_dotenv  # noqa: E402

try:
    from crewai.events.listeners.tracing.utils import mark_first_execution_done

    mark_first_execution_done()
except Exception:      # different CrewAI version - the prompt times out anyway
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from search_tools import web_search  # noqa: E402

load_dotenv()
load_dotenv(find_dotenv(usecwd=True))

api_key = os.environ.get("OPENROUTER_API_KEY")
if not api_key:
    raise RuntimeError(
        "OPENROUTER_API_KEY not found.\n"
        "Create a file named .env in the project folder containing:\n"
        "    OPENROUTER_API_KEY=sk-or-...\n"
        "then run this script again."
    )


# TODO 1 - build the model client. Same model as your LangGraph version, so
#          the comparison is honest. Note the "openrouter/" prefix.
# llm = LLM(
#     model="openrouter/openai/gpt-4o-mini",
#     temperature=0,
#     api_key=api_key,
# )


# ---------------------------------------------------------------------------
# THE TOOL
# ---------------------------------------------------------------------------
# In LangGraph the tool was a plain function call inside a node - YOU decided
# when it ran. Here you hand the tool to an agent and the agent decides.
#
# TODO 2 - finish the docstring below.
#
#          The docstring is NOT a comment. It is the description the model
#          reads when deciding whether to use this tool, and what to pass it.
#          Write it for the model, not for yourself.
#
#          It should say what the tool searches, that it returns findings with
#          source URLs, and that it can return the exact text NO_RESULTS or
#          SEARCH_UNAVAILABLE.
@tool("Web Search")
def search_web(query: str) -> str:
    """TODO: describe this tool for the model."""
    return web_search(query)


def build_crew() -> Crew:
    # TODO 3 - the researcher. This is the agent that HOLDS THE TOOL.
    #          role      - who they are
    #          goal      - what a good result looks like
    #          backstory - how they behave. Make it explicit that they report
    #                      what the search actually returned, keep the source
    #                      URLs, and say so plainly on NO_RESULTS instead of
    #                      filling the silence from memory.
    #          tools     - [search_web]
    #          llm       - keep this, or CrewAI goes looking for OPENAI_API_KEY
    #          max_iter  - a hard cap, so a confused agent cannot loop forever
    researcher = Agent(
        role="TODO",
        goal="TODO",
        backstory="TODO",
        tools=[search_web],
        llm=llm,
        verbose=True,
        max_iter=4,
    )

    # TODO 4 - the writer. No tools. It only ever sees what it is given.
    #          Its backstory should make clear it would rather hand back
    #          "we could not verify this" than something that reads well and
    #          might be wrong.
    writer = Agent(
        role="TODO",
        goal="TODO",
        backstory="TODO",
        llm=llm,
        verbose=True,
        max_iter=4,
    )

    # -----------------------------------------------------------------------
    # THE TASKS
    # -----------------------------------------------------------------------
    # Notice there is NO agent= on either task. In a hierarchical crew the
    # manager assigns the work. (In a sequential crew, leaving agent= off is
    # an error - try it once and read what CrewAI tells you.)

    # TODO 5 - the research task.
    #          Use {question}, which is filled in by kickoff(inputs=...).
    #          Tell it to use the Web Search tool, report exactly what came
    #          back including source URLs, and NOT to substitute its own
    #          knowledge if the search returned nothing.
    research_task = Task(
        description="TODO: find evidence about {question}",
        expected_output="TODO: say what 'done' looks like.",
    )

    # TODO 6 - the writing task. This is where the branch lives now.
    #          It must produce ONE of two things:
    #
    #            - if the evidence supports an answer: under 180 words,
    #              quoting at least one source URL
    #            - if it does NOT: no answer at all. One sentence saying it
    #              could not be verified, what is missing, and a final line
    #              in exactly this form:
    #                  NEXT SEARCH: <the one query you would run next>
    #
    #          Ask it to begin with either "VERDICT: ANSWERED" or
    #          "VERDICT: COULD NOT VERIFY" so you can log what happened.
    write_task = Task(
        description="TODO: write the final output for {question}",
        expected_output="TODO: say what 'done' looks like.",
    )

    # TODO 7 - a HIERARCHICAL crew.
    #
    #          Two rules CrewAI will enforce, loudly:
    #            - hierarchical needs manager_llm (or manager_agent)
    #            - the manager must NOT also appear in agents=
    #
    #          Pass manager_llm=llm and CrewAI builds the manager for you.
    return Crew(
        agents=[researcher, writer],
        tasks=[research_task, write_task],
        process=Process.sequential,   # TODO 7: change this
        verbose=True,
        tracing=False,
    )


def main() -> int:
    question = " ".join(sys.argv[1:]).strip() or \
        "Anthropic was founded by former OpenAI employees"

    result = str(build_crew().kickoff(inputs={"question": question}))

    print("\n" + "=" * 70)
    print(f"QUESTION : {question}")
    print("=" * 70)
    print(result)

    # TODO 8 - log which way the crew went.
    #
    #          In LangGraph you read state["route_taken"], written by whichever
    #          node actually ran. There is no such field here. The only way to
    #          know what this crew decided is to read its final text and guess.
    #
    #          Write that guess below - and put one sentence about it in your
    #          README, because it is the whole difference between the two.
    route = "TODO"

    print("\n" + "=" * 70)
    print(f"ROUTE : {route}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
