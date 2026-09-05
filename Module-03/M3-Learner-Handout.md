<!-- JHF-BRAND -->
<div align="center" style="padding:28px 20px; background:#ffffff; border:2px solid #e0e0e0; border-radius:12px;">
  <p style="margin:0 0 16px 0;">
    <img src="../assets/jhf-logo.png" alt="Jerusalem High-Tech Foundry (JHF)" height="54" style="vertical-align:middle; margin:0 22px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC - Cooperation for Development" height="72" style="vertical-align:middle; margin:0 22px;" />
  </p>
  <h1 style="color:#1a3c5e; margin:6px 0;">Agentic AI Bootcamp</h1>
  <h3 style="color:#0078d4; margin:4px 0; font-weight:600;">Module 3 &middot; ReAct, Planning &amp; Tool-Use &mdash; Learner Handout</h3>
  <hr style="border:0; border-top:1px solid #0078d4; width:60%; margin:16px auto;" />
  <p style="font-size:14px; color:#555; margin:6px 0;">
    <strong>Lead Trainers</strong><br/>
    <a href="https://www.linkedin.com/in/alaaldin-ahmed-260266150" target="_blank">Alaaldin Ahmed</a> &nbsp;|&nbsp;
    <a href="https://il.linkedin.com/in/mohammad-abu-alhalawe" target="_blank">Mohammed Abu Alhalaweih</a>
  </p>
  <p style="font-size:12.5px; color:#777; margin:8px 0 0 0;">
    Organized by <strong>Jerusalem High-Tech Foundry (JHF)</strong> &nbsp;&middot;&nbsp; In partnership with <strong>COMCEC</strong>
  </p>
</div>

# Module 3 — ReAct, Planning & Tool-Use Patterns
## Learner Handout

> **Duration:** 5 hours · **Prereq:** M1 (hand-rolled loop) & M2 (LangGraph + CrewAI "hello agents").
> **You need:** your M2 environment, LangGraph + a model integration, key in `.env`, GitHub Copilot.
> Today you rebuild the agent **properly in LangGraph** — with **structured outputs** and a **planner**.

---

## Learning Objectives

After this module you can:
1. **Implement ReAct** (reason → act → observe) as an explicit LangGraph state graph.
2. **Use structured outputs** so the model returns schema-validated actions — no more fragile JSON parsing.
3. **Implement Planning** (plan-and-execute): decompose a goal, run steps, and replan.
4. **Engineer reliability**: retries, validation, and graceful failure.

---

## 1. ReAct as an Explicit Graph

In M1 you wrote the loop by hand (`while` + `if/else`). LangGraph lets you **declare** that loop as a graph, and the framework runs it.

```
    START ─▶ REASON ──structured action──▶ ACT ──observe──┐
              │ (LLM picks an action)      (run tool)      │
              │                                            │
       final_answer? ── yes ─▶ END        ◀── result into state
              │                                            │
              └────────────── no · loop ───────────────────┘
```

| Concept | Meaning | Replaces (from M1) |
|---|---|---|
| **State** | The shared object flowing through the graph (question, scratchpad of observations, chosen action, answer) | your `messages` list |
| **Node** | A step / function (e.g., `reason`, `act`) | bodies of your loop |
| **Edge** | A fixed transition between nodes | the next line of code |
| **Conditional edge** | A runtime branch (e.g., `final_answer?` → END or loop) | your `if/else` |

> The framework owns the **loop**; you own the **nodes** and the **routing**. Same mental model as M1 — just declared instead of hand-run.

---

## 2. Structured Outputs — the Reliability Upgrade

In M1 you fought `JSONDecodeError` and stripped ``` fences. Today we delete that whole class of bug.

- **Structured outputs** make the model return data that matches a **schema** (a Pydantic model). The framework validates and parses it for you.
- Define an action schema once, e.g.:
  ```python
  class Action(BaseModel):
      tool: Literal["calculator", "final_answer"]
      expr: Optional[str] = None   # the sum, when tool="calculator"
      text: Optional[str] = None   # the answer, when tool="final_answer"
  ```
  Then: `structured_llm = llm.with_structured_output(Action)`.

> ⚠️ **Give every field its own type.** A catch-all `args: dict` looks tempting, but it
> becomes `additionalProperties: true` in the JSON Schema — and strict structured-output
> mode rejects that with an **HTTP 400**. A bare `dict` isn't a loose schema; it's the
> absence of one.

| M1 (manual) | M3 (structured) |
|---|---|
| `json.loads(strip_fences(raw))` | `llm.with_structured_output(Action)` |
| Crashes on bad JSON | Validated to the schema |
| Brittle prompt about "ONLY JSON" | Schema enforces shape |

> ⚠️ **Structured output guarantees the *shape*, not the *truth*.** The model can still pick the wrong tool or bad args — so you still validate, retry, and (next module) reflect.

---

## 3. Planning — Plan-and-Execute

Plain ReAct can wander on complex goals. **Planning** adds an explicit decomposition step.

```
  START ─▶ PLANNER ─▶ EXECUTOR ─▶ REPLAN ─┐
          goal →      run next     done? → finish ─▶ END
          [steps]     step (ReAct  else → update plan ─┐
                      + tools)                          │
              ▲───────────────────────────────────loop─┘
```

| Role | Job | Output |
|---|---|---|
| **Planner** | Decompose the goal into ordered sub-steps | `Plan(steps: list[str])` |
| **Executor** | Run the current step using ReAct + tools | observation appended to state |
| **Replan** | Decide: finish (give answer) or update the plan and loop | `Response` or updated `Plan` |

> **Trade-off:** planning boosts reliability on hard, multi-step tasks but **costs more** (extra LLM calls). For a single lookup, plain ReAct — or even one call — is better. *Use the least agency that works* (from M2).

---

## 4. Reliability Engineering

- **Retries:** on a failed parse or tool call, retry once with a corrective nudge before giving up.
- **Validation:** check tool args before running (e.g., calculator gets only arithmetic). Bad args → an *error observation*, not a crash.
- **Hard caps:** keep M1's `max_steps` (and add a replan budget). Always have a graceful "couldn't complete" exit.

These are previews of **M4** (reflection), **M10** (evaluation), **M11** (guardrails).

---

## 5. Today's Lab (preview)

You'll build a **ReAct + Planning** agent in LangGraph:
- **Part 1:** a ReAct graph with a structured `Action` interface and a calculator tool.
- **Part 2:** add a **planner** (decompose) and **replan** (finish/loop), then **visualize** the graph.

Full steps: **`M3-Lab-Worksheet.md`**. Starter: **`starter-code/react_agent_starter.py`**.

**Definition of done:** for *"A team has 3 sprints of 12, 19, and 8 story points. What's the average per sprint, and is it above 12?"* your agent **plans → executes with tools → replans → answers "13, yes"**, and you can show the graph diagram.

---

## 6. Deliverables (graded)

1. **`react_agent.py`** — a working ReAct + Planning graph using structured outputs, with a step cap.
2. **Graph visualization** — an image/Mermaid of your compiled graph (planner → executor → replan with the loop).
3. **Failure-mode note (½ page)** — one bug you hit (e.g., runaway executor, vague plan) and how you fixed it.

---

## 7. Key Terms

| Term | Meaning |
|---|---|
| ReAct | Reason → Act → Observe loop pattern. |
| State (LangGraph) | The shared object threaded through graph nodes. |
| Node / edge / conditional edge | A step / a fixed transition / a runtime branch. |
| Structured output | Schema-constrained model output (validated & parsed). |
| Plan-and-execute | Planner decomposes → executor runs steps → replan finishes/updates. |
| Replan | Deciding to finish or revise the plan after progress. |
| Scratchpad | The running record of steps/observations in state. |

---

## 8. Quiz (5 min)

1. What do Reason, Act, and Observe mean in practice?
2. In LangGraph: define a node, an edge, and a conditional edge.
3. What M1 problem do structured outputs eliminate?
4. True/False: structured outputs guarantee the answer is correct.
5. Describe the three roles in plan-and-execute.
6. Give one task where planning beats plain ReAct, and one where it's overkill.
7. Name two reliability techniques for action selection.
8. What replaces M1's `messages` list in a LangGraph agent?

---

<div align="center" style="padding:14px; border-top:2px solid #0078d4; margin-top:34px;">
  <p style="margin:0 0 8px 0;">
    <img src="../assets/jhf-logo.png" alt="JHF" height="28" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC" height="40" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
  </p>
  <p style="color:#888; font-size:13px; margin:0;">
    <strong>JHF Agentic AI Bootcamp</strong> &mdash; Module 3<br/>
    Lead Trainers: <a href="https://www.linkedin.com/in/alaaldin-ahmed-260266150">Alaaldin Ahmed</a> &amp; <a href="https://il.linkedin.com/in/mohammad-abu-alhalawe">Mohammed Abu Alhalaweih</a><br/>
    Organized by Jerusalem High-Tech Foundry (JHF) &middot; In partnership with COMCEC
  </p>
</div>

