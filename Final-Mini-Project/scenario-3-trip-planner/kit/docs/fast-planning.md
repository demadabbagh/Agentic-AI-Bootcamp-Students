# Fast planning

The trip form answers in seconds. The agents then review that plan in the background, and anything better they find is
an offer the traveller can take or leave. Chat requests (the official contract and its evals) keep the agents in line
and are not changed by any of this.

## The two stages

**Stage 1, the plan on screen.** Real research, then each agent's Python counterpart: `python_decision` picks the
places, `propose` builds the days, the Python verifier gives the verdict. No LLM call. The rules, the budget fit, the
schedule and the verification are the same code the agents' plans go through; only the ranking and the day assignment
are Python's.

**Stage 2, the AI review (`app/refinement.py`).** The agents plan the same trip on a bounded pool. A result is offered
only if it differs and is at least as well verified. It never replaces the plan on its own: the traveller applies it,
and it then goes through the normal approval policy. An offer for a plan that has moved on or been saved is stale and
cannot be applied.

## The time budget

`FAST_TOTAL_SECONDS` in `app/orchestrator.py` (7 s) is shared by the steps, counted from the start of research. The
rest of the 10 seconds is for the network and the page.

A step that runs out of time does not guess. A travel leg past the limit is `not_looked_up`, exactly like a leg past
the lookup limit: the schedule uses the labelled planning buffer, and the verifier reports the fact as unknown. So a
slow source can cost a plan its PASS, and never gives it one. `FAST_VERIFY_SECONDS` is kept back so a plan is always
checked.

## Why it is fast

| Cause, as measured | Before | Fix |
|---|---|---|
| A new HTTP client for each MCP call | 431 ms per call, on the event loop | One shared client (`services/travel_mcp.py`) |
| City listings rebuilt for every lookup | about 90 ms per lookup | `city_listings` memo, and one build for parallel callers |
| District pages one by one | 10.8 s for Istanbul | One MediaWiki query (`wv_pages_prefetch`): 1.6 s |
| Routing at one request a second | a minute or more per trip | One multi-stop route (`warm_routes`): 70 stops in 0.14 s |
| Sequential agents, ReAct loops, guardrail retries | 11 to 84 s of LLM time | Moved to the background review |

The first read of a city still takes 4 to 10 seconds, so the form asks for it as soon as a destination is picked
(`POST /api/plan/prepare`). This is a read-only head start: bounded, remembered for ten minutes, and nothing depends on
it.

## Routing warm-up notes

- Use `/route` with many stops, not `/table`. The public server took 6 to 9 s for a table of 40 or more places, and
  answered 429 under load. Each leg of a chained route equals the single route for that pair (checked on real legs).
- A chain fails as a whole (`NoRoute`) when two stops have no road between them, such as an island by car, and the
  answer does not say which stops. Those legs are then asked one by one, inside the time budget.
- A warm-up never waits out a busy server: it has its own budget (`WARM_BUDGET_S`), because the server's limits are
  to be respected, not retried through.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `RIHLA_FAST_PLANNING` | `on` | `off`: the trip form uses the agents in line, as chat does |
| `RIHLA_AI_REVIEW` | `on` | `off`: no background review, so no LLM cost after a fast plan |

## Measuring

Every `/api/chat` answer carries `performance.trace`: LLM calls and milliseconds per stage, MCP calls and milliseconds
per tool, and retry and cache counts. The same summary is logged as `rihla.perf`. Names and times only, never arguments
or results.
