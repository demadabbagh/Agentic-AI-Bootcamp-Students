"""
The Vendor Onboarding Desk - Module 3 bridge project - STARTER

Read INSTRUCTIONS.md first, then REQUEST.md.

Before you touch this file:
    python verify_tools.py        <- must print "all 10 checks passed"

Everything here is scaffolding you have seen before. What is new is the
BUDGET: you cannot afford to check everything, so something has to decide
what is worth checking. That decision is the project.

Run it now - it will check your setup and tell you what is missing.
"""

from __future__ import annotations

import os
import sys
from typing import Literal, Optional, TypedDict

# the tools live one level up, in bridge-project/ - this lets you run the file
# from either folder without thinking about it
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from registry_tools import (
    LOOKUP_UNAVAILABLE,
    NO_RECORDS,
    NO_SANCTIONS_MATCH,
    gleif_lookup,
    sanctions_screen,
)
from search_tools import web_search

load_dotenv()

# ===========================================================================
# THE CONSTRAINT
# ===========================================================================
# This rations the EXPENSIVE work: establishing which legal entity a supplier
# actually is. Sanctions screening is a mandatory baseline control - run it for
# everyone, always; it does not come out of this budget.
# Do not raise this number. Working inside it IS the project.
MAX_LOOKUPS = 9

BUDGET = {"used": 0}


def spend(what: str) -> bool:
    """Call before every lookup. Returns False when the budget is gone."""
    if BUDGET["used"] >= MAX_LOOKUPS:
        return False
    BUDGET["used"] += 1
    print(f"  [{BUDGET['used']:2}/{MAX_LOOKUPS}] {what}")
    return True


# ===========================================================================
# 1 - THE SCHEMAS
# ===========================================================================
# Three decisions, three schemas - same shape as the Module 3 solution.
# Explicit fields only. `args: dict` will earn you an HTTP 400.

class Supplier(BaseModel):
    """One line out of Rana's email."""
    name: str
    # TODO: she mentioned annual value, jurisdiction, and that Procurement
    #       typed the names by hand. Which of those does your PLAN need in
    #       order to spend the budget well? Add only those.


class Plan(BaseModel):
    """What to check, in what order, given a budget that will run out."""
    # TODO (Step 1): a bare list of names is not a plan - that is the input.
    #                What makes this worth having?
    ...


class Verdict(BaseModel):
    """The decision Rana acts on."""
    supplier: str
    verdict: Literal["APPROVE", "CONDITIONS", "REJECT", "INSUFFICIENT"]
    reason: str = Field(description="Why. In language Rana can repeat to Procurement.")
    # TODO: CONDITIONS is useless without naming the specific thing required.
    #       REJECT is useless without one clear sentence.
    #       INSUFFICIENT is useless without saying what would settle it.
    #       Add the field(s) that make each verdict actionable.


# ===========================================================================
# 2 - THE STATE
# ===========================================================================

class State(TypedDict):
    request: str              # the raw email
    queue: list               # suppliers not yet screened
    evidence: list            # what the registries actually returned
    verdicts: list            # one per supplier
    skipped: list             # named, never hidden
    # TODO: anything else your graph needs to see. If it is not in here,
    #       no node can read it.


# ===========================================================================
# 3 - YOUR NODES
# ===========================================================================

def triage(state: State) -> State:
    """
    Read the email. Pull out the suppliers. Decide the order.

    TODO (Step 2): extracting seven names is the easy half. The half that
                   counts is deciding which ones get the budget - because it
                   will run out before the list does.

                   Think about which check you would never skip, whatever the
                   supplier. That thought is worth more than the parsing.
    """
    raise NotImplementedError


def screen(state: State) -> State:
    """
    Gather evidence on the NEXT supplier in the queue.

    TODO (Step 3): this is where your Module 3 ReAct agent earns its keep.
                   Do not write a new reason/act loop here - hand ONE supplier
                   to the agent you already built and let it decide which
                   registry to ask and when it has seen enough.

                   Call spend() before every lookup. If it returns False,
                   stop - do not silently exceed the budget.

                   Note: when a registry returns NO_RECORDS you have not
                   learned nothing. You have learned something. What?
    """
    raise NotImplementedError


def decide(state: State) -> State:
    """
    Turn the evidence for one supplier into one of the four verdicts.

    TODO (Step 4): read the four traps in INSTRUCTIONS.md before you write
                   this prompt. At least three of them live in this function.
    """
    raise NotImplementedError


def budget_left(state: State) -> str:
    """
    Router. Reads only - anything written here is discarded.

    TODO (Step 5): "next" while there is queue AND budget, else "memo".
    """
    raise NotImplementedError


def write_memo(state: State) -> State:
    """
    The deliverable.

    TODO (Step 6): every supplier gets a line, INCLUDING the ones you never
                   checked. Print the lookup count. Hiding what you skipped
                   is the one unforgivable bug in this project.
    """
    raise NotImplementedError


def build_graph():
    """
    TODO (Step 7): wire it. triage -> screen -> decide -> (loop | memo)

    The loop-back edge is the whole point, same as Module 3. If you find
    yourself writing `for s in suppliers:` inside a node, stop and reread
    the executor in the Module 3 solution.
    """
    raise NotImplementedError


# ===========================================================================
if __name__ == "__main__":
    print("Vendor Onboarding Desk - setup check\n")

    ok = True

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("  [ ] OPENROUTER_API_KEY missing - copy .env.example to .env")
        ok = False
    else:
        print("  [x] API key found")

    try:
        gleif_lookup("Maersk A/S")
        print("  [x] gleif_lookup is implemented")
    except NotImplementedError:
        print("  [ ] gleif_lookup not written yet - run: python verify_tools.py")
        ok = False

    if not os.path.exists("REQUEST.md"):
        print("  [ ] REQUEST.md not found - cd into bridge-project/ and run:")
        print("      python starter-code/vendor_desk_starter.py")
        ok = False
    else:
        print("  [x] REQUEST.md found")

    print()
    if ok:
        print("READY. Now fill in the TODOs, top to bottom.")
        print("Start with triage(). Get it printing the 7 suppliers in a")
        print("sensible order before you write anything else.")
    else:
        print("Fix the boxes above first.")
