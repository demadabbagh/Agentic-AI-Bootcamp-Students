<!-- JHF-BRAND -->
<div align="center" style="padding:28px 20px; background:#ffffff; border:2px solid #e0e0e0; border-radius:12px;">
  <p style="margin:0 0 16px 0;">
    <img src="../assets/jhf-logo.png" alt="Jerusalem High-Tech Foundry (JHF)" height="54" style="vertical-align:middle; margin:0 22px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC - Cooperation for Development" height="72" style="vertical-align:middle; margin:0 22px;" />
  </p>
  <h1 style="color:#1a3c5e; margin:6px 0;">Agentic AI Bootcamp</h1>
  <h3 style="color:#0078d4; margin:4px 0; font-weight:600;">Module 3 &middot; Lab Worksheet &mdash; ReAct + Planning in LangGraph</h3>
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

# Module 3 — Lab Worksheet
## Build a ReAct + Planning Agent in LangGraph (structured outputs)

> ### 📖 Read [`M3-Lab-Brief.md`](M3-Lab-Brief.md) first
> It explains **what you're building and why** — the problem, the loop, and what
> changed since Module 2. This worksheet is the *how*: nine steps, in order.
> Ten minutes on the brief saves an hour of writing code you don't understand.

> **Time:** ~1h (Part 1) + ~45 min (Part 2)
> **Files:** edit `starter-code/react_agent_starter.py` → save as `react_agent.py`
> **Goal:** rebuild the agent the *right* way — explicit graph, schema-validated actions, and a planner that decomposes multi-step goals.
> **Copilot:** see [`M3-Copilot-Guide.md`](M3-Copilot-Guide.md) — use it to understand, not to paste.

---

## Definition of done
For: *"A team has 3 sprints of 12, 19, and 8 story points. What's the average per sprint, and is it above 12?"*
your agent **plans → executes with the calculator → replans → answers "Average = 13, yes, above 12"**, and you can display the compiled graph.

---

## Setup gate (5 min)
1. Activate your M2 venv.
2. Run the starter file **as-is**: `python starter-code/react_agent_starter.py`
   It runs a setup check and should print **READY**. It does not run an agent yet —
   the TODOs are still empty.
3. If any line says `[FAIL]`, fix that first. Raise your hand if you're stuck.

---

## PART 1 — ReAct graph with structured actions (Steps 1–5)

### Step 1 — Define the graph State
Create a typed state (TypedDict or Pydantic) with at least:
- `question: str`
- `scratchpad: list` (observations so far)
- `action: dict | None`
- `answer: str | None`

### Step 2 — Define the Action schema (structured output)
```python
class Action(BaseModel):
    tool: Literal["calculator", "final_answer"]
    expr: Optional[str] = None   # the sum, when tool="calculator"
    text: Optional[str] = None   # the answer, when tool="final_answer"
```
Bind it: `structured_llm = llm.with_structured_output(Action)`.

> ⚠️ **Give every field its own type.** A catch-all `args: dict` becomes
> `additionalProperties: true` in the JSON Schema, and strict structured-output mode
> rejects that with an **HTTP 400**. A bare `dict` isn't a loose schema — it's no schema.

### Step 3 — The calculator tool
Reuse M1's safe calculator: allow only digits/operators/parentheses; return an **error string** (don't raise) on bad input.

### Step 4 — Build the `reason` and `act` nodes
- `reason(state)`: call `structured_llm` with the question + scratchpad; store the returned `Action` in `state["action"]`.
- `act(state)`: if `tool == "final_answer"` → write `state["answer"]`; if `tool == "calculator"` → run it and **append the observation** to `state["scratchpad"]`.

### Step 5 — Wire the graph + conditional edge
- Entry point → `reason` → `act`.
- Add a **conditional edge** after `act`: if an answer exists → `END`, else loop back to `reason`.
- Carry a `MAX_STEPS` guard (recursion/step cap) so it can't loop forever.
- 🧪 Checkpoint: solve *"What is (23 × 19) + 100?"* — the agent reasons, uses the calculator, and returns **537** in 2–4 steps. No manual JSON parsing anywhere.

> ℹ️ **You may see the calculator called only once.** Models often compute the final
> `+ 100` themselves rather than making a second tool call. **Both are correct** — check
> the *answer*, not the call count. Worth thinking about: the prompt told it to use the
> calculator for arithmetic, and it partly ignored that. **A tool is an option, not a rule.**
> If you ever need a guarantee, you enforce it in code, not in the prompt.

> ✅ **End of Part 1:** you have ReAct as a real graph with a schema-validated action interface.

---

## PART 2 — Add Planning (plan-and-execute) (Steps 6–9)

### Step 6 — The Plan schema + planner node
```python
class Plan(BaseModel):
    steps: list[str]   # each step = one concrete action the tools can perform
```
- `planner(state)`: produce a `Plan` from the question. Prompt rule: *"each step must be a single concrete action."*
- Store the plan and an empty `past_steps` in state.

### Step 7 — The executor node
- `executor(state)`: take the **next** plan step, run it using your Part-1 ReAct mechanics (reason+act with tools), and append `(step, observation)` to `past_steps`.

### Step 8 — The replan node + conditional edge
- `replan(state)`: given `past_steps`, decide:
  - **finish** → produce a final `Response(answer=...)`, or
  - **continue** → update the remaining plan.
- Conditional edge: if finished → `END`, else → `executor` (loop).
- Add a `MAX_REPLANS` budget.

### Step 9 — Visualize the graph
- Render the compiled graph (e.g., `app.get_graph().draw_mermaid_png()` or print the Mermaid).
- 🧪 **Final checkpoint:** run the sprint-average question. Confirm: a plan is produced, steps execute with the calculator, replan terminates, and the answer is "13, yes." Save the graph image for your deliverable.

---

## Stretch goals

- **Second tool:** add a `search`/knowledge stub so the planner must pick the right tool per step.
- **Cost readout:** count LLM calls across planner + executor + replan; print the total — feel planning's price.
- **Retry-on-bad-output:** if structured parsing fails, retry once with a corrective nudge before failing the step.
- **Bad plan recovery:** force a vague plan and watch replan recover — note what made it work.

---

## Troubleshooting

| Problem | Try this |
|---|---|
| `with_structured_output` returns None / errors | Simplify the schema; confirm the model supports tool/function calling; add a retry. |
| Graph won't compile | Check entry point, that every conditional branch maps to a real node or `END`. |
| Executor loops forever | Ensure `replan` can return "finish"; add/verify `MAX_REPLANS` and the step cap. |
| Plan steps are too vague to run | Tighten the planner prompt: "one concrete, tool-executable action per step." |
| Node ignores earlier results | Confirm each node returns updated state and reads `scratchpad`/`past_steps`. |
| Tool crashes the run | Validate args inside the tool; return an error observation instead of raising. |

---

## Submit
1. `react_agent.py` — ReAct + Planning graph, structured outputs, step cap.
2. Your **graph visualization** image/Mermaid.
3. Your **½-page failure-mode note**.

Compare with `solution-code/react_agent_solution.py` **after** you have your own version working.

---

<div align="center" style="padding:14px; border-top:2px solid #0078d4; margin-top:34px;">
  <p style="margin:0 0 8px 0;">
    <img src="../assets/jhf-logo.png" alt="JHF" height="28" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC" height="40" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
  </p>
  <p style="color:#888; font-size:13px; margin:0;">
    <strong>JHF Agentic AI Bootcamp</strong> &mdash; Module 3 Lab<br/>
    Lead Trainers: <a href="https://www.linkedin.com/in/alaaldin-ahmed-260266150">Alaaldin Ahmed</a> &amp; <a href="https://il.linkedin.com/in/mohammad-abu-alhalawe">Mohammed Abu Alhalaweih</a><br/>
    Organized by Jerusalem High-Tech Foundry (JHF) &middot; In partnership with COMCEC
  </p>
</div>

