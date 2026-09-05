"""
===============================================================================
  ReAct FROM SCRATCH  —  no framework, no graph, just a while loop
===============================================================================

  Run me first:      python react_explained.py

  This file exists to teach ONE idea:  what a ReAct loop actually is.

  There is no LangGraph here. No nodes, no edges, no state graph.
  Just a Python while loop, a tool, and a list.

  Once you understand this file, the starter code is the SAME THING with
  LangGraph running the loop for you instead of you running it by hand.

-------------------------------------------------------------------------------
  THE WHOLE IDEA, BEFORE ANY CODE
-------------------------------------------------------------------------------

  Picture a man at a desk. He is excellent at reasoning, but he has an
  unusual condition: every 30 seconds he forgets EVERYTHING.

  On the desk there are three things:
      - a question, written on a card that never changes
      - a calculator  (he is not allowed to do arithmetic in his head)
      - a notepad, empty at first

  Every time he wakes up he does exactly four things:
      1. reads the question
      2. reads the notepad
      3. decides ONE next move
      4. does it, and writes the result on the notepad
  ... then he forgets everything and wakes up again.

  That is ReAct.  REASON -> ACT -> OBSERVE, repeated.

  The amnesia is not a metaphor. A language model genuinely has NO memory
  between calls. The only reason call #2 knows what happened in call #1 is
  that WE paste the notepad back into the prompt ourselves, every time.

===============================================================================
"""

import os
import re
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

load_dotenv()

# A hard limit on how many times he is allowed to wake up.
# NEVER rely on the model deciding to stop. This is your safety net.
MAX_STEPS = 6

llm = ChatOpenAI(
    model="openai/gpt-4o-mini",
    temperature=0,                       # same input -> same output, so we can debug
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)


# =============================================================================
# PIECE 1 of 3 — THE TOOL
# =============================================================================
# A "tool" is just a normal Python function that the model is allowed to ask
# us to run. There is no AI in here at all.
#
# Why bother? Because a language model predicts text. If you ask it for
# 23 * 19 it produces a plausible-looking number. With a tool, the number is
# COMPUTED by Python. The model chooses; the code calculates.
#
# Two rules every tool should follow:
#   1. validate the input BEFORE doing anything dangerous
#   2. return errors as a STRING, never raise
#
# Rule 2 is the important one. If this function raises, the whole program
# dies. If it RETURNS "ERROR: ...", that text goes onto the notepad, the
# model reads it next time round, and it can correct itself.
#
#      An error the agent can read is a hint.  An exception is a crash.
# =============================================================================

ONLY_MATHS = re.compile(r"^[0-9+\-*/().\s]+$")   # digits and operators only


def calculator(expression: str) -> str:
    """Evaluate a simple arithmetic expression. Never raises."""
    if not ONLY_MATHS.match(expression or ""):
        return "ERROR: that is not a plain arithmetic expression."
    try:
        # {"__builtins__": {}} strips Python's built-in functions, so even if
        # something odd got past the regex it could not reach open() or import.
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"ERROR: {e}"


# =============================================================================
# PIECE 2 of 3 — THE FORM  (this is what "structured output" means)
# =============================================================================
# The model can only produce text. So how does it tell our program
# "please run the calculator on 23 * 19"?
#
# In Module 1 we asked it for JSON in the prompt and then cleaned up the mess
# with regex and json.loads(), and it broke constantly.
#
# Instead, we hand it a FORM and it must fill the boxes in.
# That is all a "schema" is: a description of the shape an answer must have.
#
#   tool  ->  which of the two moves is it making?
#   expr  ->  filled in when tool == "calculator"
#   text  ->  filled in when tool == "final_answer"
#
# Literal[...] means the model is STRUCTURALLY UNABLE to invent a third tool.
# Not "asked politely not to" — unable.
#
# NOTE: give every field its own type. A catch-all `args: dict` looks tempting
# but it means "any keys, any values", which is not a schema at all, and the
# provider rejects it with an HTTP 400.
# =============================================================================

class Action(BaseModel):
    """One move the agent wants to make."""
    tool: Literal["calculator", "final_answer"]
    expr: Optional[str] = Field(default=None, description="Arithmetic, if tool='calculator'.")
    text: Optional[str] = Field(default=None, description="The answer, if tool='final_answer'.")


# This is the line that does the magic. From here on, .invoke() hands us back
# a real Action object that has already been validated. No parsing anywhere.
decide_next_move = llm.with_structured_output(Action)


# =============================================================================
# PIECE 3 of 3 — THE NOTEPAD AND THE LOOP
# =============================================================================
# Everything above was setup. THIS is ReAct.
# =============================================================================

def run_agent(question: str) -> str:
    # THE NOTEPAD. Starts empty. This is the agent's entire memory.
    # If something is not in this list, the model cannot see it. Not "might
    # miss it" — cannot. Every model call is a brand new mind.
    notepad: list[str] = []

    for step in range(1, MAX_STEPS + 1):
        print(f"\n{'=' * 70}\nWAKE-UP {step}\n{'=' * 70}")

        # ------------------------------------------------------------------
        # REASON  — rebuild his whole world from scratch, then ask him
        # ------------------------------------------------------------------
        # Look closely at what changes between iterations:
        #   the question is ALWAYS THE SAME
        #   the observations GROW BY ONE LINE each time
        # That growth is the entire mechanism. Nothing else persists.
        observations = "\n".join(notepad) if notepad else "(nothing yet)"

        prompt = (
            "Answer the question below, one step at a time.\n"
            "You cannot do arithmetic yourself - use the calculator tool.\n"
            "\n"
            "RULES:\n"
            "- Read the observations FIRST.\n"
            "- Never repeat a calculation that is already there; reuse the result.\n"
            "- As soon as the observations contain everything you need,\n"
            "  use final_answer.\n"
            "\n"
            f"QUESTION: {question}\n"
            "\n"
            f"OBSERVATIONS SO FAR:\n{observations}"
        )

        print("  the notepad he is reading:")
        for line in (notepad or ["(empty)"]):
            print(f"      {line}")

        move = decide_next_move.invoke(prompt)     # <-- the ONLY model call
        print(f"  he decides: tool={move.tool!r}  expr={move.expr!r}  text={move.text!r}")

        # ------------------------------------------------------------------
        # ACT  — run whatever he asked for. No AI in this part.
        # ------------------------------------------------------------------
        if move.tool == "final_answer":
            print("  -> he is finished")
            return move.text or "(no answer given)"

        if move.tool == "calculator":
            result = calculator(move.expr or "")
            print(f"  -> calculator({move.expr!r}) = {result}")

            # --------------------------------------------------------------
            # OBSERVE — write it on the notepad.
            # This single line is why the next wake-up is smarter than this
            # one. Delete it and the agent loops forever, recomputing the
            # same thing, because it never learns anything.
            # --------------------------------------------------------------
            notepad.append(f"calculator({move.expr}) = {result}")

    # If we get here he used up all his wake-ups without finishing.
    # ALWAYS have a graceful exit. Never return None to a caller who
    # expected an answer.
    return "(gave up - ran out of steps)"


# =============================================================================
# RUN IT
# =============================================================================

if __name__ == "__main__":
    QUESTION = "What is (23 * 19) + 100?"

    print("\n" + "#" * 70)
    print(f"#  QUESTION: {QUESTION}")
    print(f"#  budget: {MAX_STEPS} wake-ups")
    print("#" * 70)

    answer = run_agent(QUESTION)

    print(f"\n{'#' * 70}")
    print(f"#  ANSWER: {answer}")
    print("#" * 70)

    # =========================================================================
    # NOW GO BACK AND LOOK AT THE OUTPUT
    # =========================================================================
    #
    # Notice these three things:
    #
    #   1. On wake-up 1 the notepad was empty, so he called the calculator.
    #
    #   2. On wake-up 2 the notepad had one line on it - and that was enough
    #      for him to finish. The ONLY difference between the two prompts was
    #      that single line.
    #
    #   3. He called the calculator ONCE, for 23 * 19, and then did the
    #      "+ 100" in his own head. The prompt told him not to. He did it
    #      anyway, and happened to be right.
    #
    #          A tool is an OPTION, not a rule.
    #
    #      If you truly need the tool to be used, you check it in CODE -
    #      e.g. refuse a final_answer whose arithmetic never appeared in the
    #      notepad. Asking nicely in the prompt is not a guarantee.
    #
    # =========================================================================
    # THREE EXPERIMENTS - try each one, then undo it
    # (all three were measured on this exact file before it was written)
    # =========================================================================
    #
    #   EXPERIMENT 1 - break his memory          << the important one
    #       Comment out the notepad.append(...) line near the bottom of
    #       run_agent(), then run again.
    #
    #       Measured result: 6 wake-ups, 6 identical calculator calls,
    #       and the answer "(gave up - ran out of steps)".
    #
    #       He computes 23 * 19, forgets it, computes it again, forgets it
    #       again... forever, until the budget stops him. Nothing is wrong
    #       with the loop, the tool or the schema. He simply has no memory.
    #
    #       THE NOTEPAD IS THE MEMORY. That one line is the whole mechanism.
    #
    #   EXPERIMENT 2 - give him a genuinely multi-step question
    #       Change QUESTION to:
    #           "Compute 84 * 27. Then subtract 396 from that. Then divide
    #            the result by 12. Report all three numbers."
    #
    #       Measured result: 4 wake-ups, 3 calculator calls, answer
    #       "2268, 1872, and 156.0".
    #
    #       Watch the notepad grow one line at a time, and notice that each
    #       new decision depends on what the previous one produced. THAT is
    #       why the loop has to exist - you could not have written those
    #       three calculator calls in advance without doing the maths first.
    #
    #   EXPERIMENT 3 - starve him of steps
    #       Keep the harder question above and set MAX_STEPS = 2.
    #
    #       Measured result: he uses both wake-ups, never finishes, and
    #       returns "(gave up - ran out of steps)".
    #
    #       Note what did NOT happen: no crash, no exception, no None.
    #       A budget that is too small should degrade gracefully, and the
    #       caller should always get a real string back.
    #
    # -------------------------------------------------------------------------
    #   AN HONEST NOTE ABOUT PROMPTS
    #
    #   You might expect that deleting the RULES from the prompt would break
    #   this agent. On the model we are using it mostly does not - it still
    #   finishes, just occasionally with one extra step.
    #
    #   That is worth knowing, and worth not pretending otherwise:
    #   prompt sensitivity depends on the model, the task and the wording.
    #   A stronger model forgives a vaguer prompt. A weaker one will not.
    #
    #   Which is exactly why the things that protect you here are CODE, not
    #   wording: the schema, the budget, the tool validation, the graceful
    #   exit. Those work the same on every model, every time.
    #
    #                       Prompts ask.  Code enforces.
    # =========================================================================
    # HOW THIS MAPS ONTO THE STARTER CODE
    # =========================================================================
    #
    # The starter does exactly what this file does. The only difference is
    # that LangGraph runs the loop instead of you.
    #
    #   this file                     starter-code/react_agent_starter.py
    #   -------------------------     -------------------------------------
    #   notepad = []                  state["scratchpad"]
    #   for step in range(...)        the conditional edge (loop / end)
    #   the REASON section            def reason(state)
    #   the ACT section               def act(state)
    #   `return` when finished        def is_done(state)  ->  "end"
    #   MAX_STEPS                     MAX_STEPS, checked inside is_done()
    #
    # Same loop. Same notepad. Same budget.
    # You stop RUNNING it and start DESCRIBING it - and once it is described,
    # the framework can draw it, pause it, resume it and stream it.
    #
    # =========================================================================
