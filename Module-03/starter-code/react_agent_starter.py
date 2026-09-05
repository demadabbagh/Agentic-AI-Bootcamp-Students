"""
Module 3 Lab — STARTER
ReAct + Planning agent in LangGraph with STRUCTURED OUTPUTS.

  >>> READ M3-Lab-Brief.md FIRST <<<
  It explains what you are building and why, in ten minutes.
  Then follow M3-Lab-Worksheet.md, which walks these TODOs in order.

WHAT YOU ARE BUILDING
  An agent that WORKS OUT numeric answers instead of guessing them:

      REASON  -> the model reads the question + everything found so far,
                 and decides ONE next move
      ACT     -> your Python runs the tool it asked for (no AI here)
      OBSERVE -> the result is written to state["scratchpad"]
      ... loop until it has enough to answer, or the budget runs out.

  Part 1 (Steps 1-5): that loop, as a LangGraph state graph.
  Part 2 (Steps 6-9): add a planner that breaks the goal into steps first.

THE ONE THING TO REMEMBER
  The model has NO memory between calls. It only knows what you put in the
  prompt. state["scratchpad"] IS the memory - if something isn't in there,
  the model cannot see it.

Fill in the TODOs. No manual JSON parsing — use with_structured_output().
Set your key in .env (OPENROUTER_API_KEY=sk-or-...).
"""

from typing import TypedDict, Literal, Optional
import re
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
# from langchain_openai import ChatOpenAI

load_dotenv()

MAX_STEPS = 8
MAX_REPLANS = 5

# llm = ChatOpenAI(
#     model="openai/gpt-4o-mini", temperature=0,
#     base_url="https://openrouter.ai/api/v1",
#     api_key=os.environ["OPENROUTER_API_KEY"],   # add `import os`
# )


# ---------------------------------------------------------------------------
# Tool: calculator (reuse M1 — safe, returns error string instead of raising)
# ---------------------------------------------------------------------------
_ALLOWED = re.compile(r"^[0-9+\-*/().\s]+$")

def calculator(expr: str) -> str:
    # TODO (Step 3): validate against _ALLOWED, eval safely, return result or error string.
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Schemas (structured outputs)
# ---------------------------------------------------------------------------
class Action(BaseModel):
    """Step 2: the schema-validated action the model must return.

    NOTE: every field is explicitly typed. Do NOT use `args: dict` - strict
    structured-output mode requires additionalProperties:false on every object,
    and a bare dict cannot express that (the provider returns HTTP 400).
    """
    tool: Literal["calculator", "final_answer"]
    expr: Optional[str] = Field(
        default=None, description="Arithmetic expression, when tool='calculator'.")
    text: Optional[str] = Field(
        default=None, description="The answer text, when tool='final_answer'.")


class Plan(BaseModel):
    """Step 6: an ordered list of concrete, tool-executable steps."""
    steps: list[str]


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------
class State(TypedDict):
    question: str
    scratchpad: list
    action: Optional[dict]
    answer: Optional[str]
    plan: list
    past_steps: list
    steps_used: int       # Step 5: the MAX_STEPS budget counter
    replans: int          # Step 8: the MAX_REPLANS budget counter


# ---------------------------------------------------------------------------
# PART 1 — ReAct nodes
# ---------------------------------------------------------------------------
def reason(state: State) -> State:
    # TODO (Step 4): call llm.with_structured_output(Action) with question + scratchpad,
    # store the chosen action dict in state["action"].
    raise NotImplementedError


def act(state: State) -> State:
    # TODO (Step 4): if tool == final_answer -> set state["answer"].
    #                if tool == calculator   -> run it, append observation to scratchpad.
    raise NotImplementedError


def is_done(state: State) -> str:
    # TODO (Step 5): return "end" if state["answer"] else "loop"
    raise NotImplementedError


# ---------------------------------------------------------------------------
# PART 2 — Planning nodes
# ---------------------------------------------------------------------------
def planner(state: State) -> State:
    # TODO (Step 6): produce a Plan from the question; store steps in state["plan"].
    raise NotImplementedError


def executor(state: State) -> State:
    # TODO (Step 7): take next plan step, run it via ReAct mechanics (reason+act),
    # append (step, observation) to state["past_steps"].
    raise NotImplementedError


def replan(state: State) -> State:
    # TODO (Step 8): decide finish (set state["answer"]) or update remaining plan.
    raise NotImplementedError


def replan_done(state: State) -> str:
    # TODO (Step 8): "end" if answer else "loop"
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------
def build_react_graph():
    """Part 1 graph: reason -> act -> (loop|END)."""
    g = StateGraph(State)
    g.add_node("reason", reason)
    g.add_node("act", act)
    g.set_entry_point("reason")
    g.add_edge("reason", "act")
    # TODO (Step 5): conditional edge after act -> {"end": END, "loop": "reason"}
    return g.compile()


def build_plan_execute_graph():
    """Part 2 graph: planner -> executor -> replan -> (loop|END)."""
    g = StateGraph(State)
    # TODO (Steps 6-8): add planner, executor, replan nodes; wire edges + conditional.
    return g.compile()


if __name__ == "__main__":
    # ---------------------------------------------------------------------
    # SETUP GATE - run this file as-is before you write any code.
    # It checks your environment. It does NOT run an agent yet, because
    # the TODOs below are still empty.
    # ---------------------------------------------------------------------
    import os

    print("Module 3 setup check")
    print("-" * 40)

    ok = True
    try:
        import langgraph
        print("  [ok]   langgraph imported")
    except Exception as e:
        ok = False
        print(f"  [FAIL] langgraph: {e}")

    try:
        from langchain_openai import ChatOpenAI  # noqa: F401
        print("  [ok]   langchain_openai imported")
    except Exception as e:
        ok = False
        print(f"  [FAIL] langchain_openai: {e}")

    if os.environ.get("OPENROUTER_API_KEY"):
        print("  [ok]   OPENROUTER_API_KEY found")
    else:
        ok = False
        print("  [FAIL] OPENROUTER_API_KEY missing - check your .env file")

    print("-" * 40)
    if ok:
        print("READY")
        print("  1. read M3-Lab-Brief.md      (what you're building, and why)")
        print("  2. then M3-Lab-Worksheet.md  (Step 1 onwards)")
    else:
        print("NOT READY - fix the [FAIL] lines above, then run this again.")

    # Once you have finished Part 2, delete everything above and use this:
    #
    # q = ("A team has 3 sprints of 12, 19, and 8 story points. "
    #      "What's the average per sprint, and is it above 12?")
    # app = build_plan_execute_graph()
    # result = app.invoke({
    #     "question": q, "scratchpad": [], "action": None, "answer": None,
    #     "plan": [], "past_steps": [], "steps_used": 0, "replans": 0,
    # })
    # print("ANSWER:", result.get("answer"))
    #
    # Step 9: visualize ->  print(app.get_graph().draw_mermaid())
