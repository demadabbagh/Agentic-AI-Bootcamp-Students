<!-- JHF-BRAND -->
<div align="center" style="padding:28px 20px; background:#ffffff; border:2px solid #e0e0e0; border-radius:12px;">
  <p style="margin:0 0 16px 0;">
    <img src="../assets/jhf-logo.png" alt="Jerusalem High-Tech Foundry (JHF)" height="54" style="vertical-align:middle; margin:0 22px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC - Cooperation for Development" height="72" style="vertical-align:middle; margin:0 22px;" />
  </p>
  <h1 style="color:#1a3c5e; margin:6px 0;">Agentic AI Bootcamp</h1>
  <h3 style="color:#0078d4; margin:4px 0; font-weight:600;">Module 3 &middot; Using GitHub Copilot to solve the lab &mdash; without cheating yourself</h3>
  <hr style="border:0; border-top:1px solid #0078d4; width:60%; margin:16px auto;" />
  <p style="font-size:12.5px; color:#777; margin:8px 0 0 0;">
    Organized by <strong>Jerusalem High-Tech Foundry (JHF)</strong> &nbsp;&middot;&nbsp; In partnership with <strong>COMCEC</strong>
  </p>
</div>

# Using GitHub Copilot on the Module 3 Lab

> **Read this before Step 1.** It takes four minutes and will save you an hour.

---

## The one rule

You are allowed — encouraged — to use Copilot for every part of this lab.

But there is a test at the end of each step, and it is not optional:

> ### 🔑 If you cannot explain a line, delete it.
>
> Not "I sort of get it." Explain it out loud, to the person next to you,
> without looking. If you can't, that line does not belong in your file yet.

Your deliverable is graded on whether **you** can explain your agent's failure modes.
Copilot cannot do that part for you.

---

## Why "just paste the TODO" fails

The lazy move is to copy a TODO comment into Copilot and paste back whatever it returns.

Three things go wrong, and you will see all three today:

| What happens | Why |
|---|---|
| It writes `args: dict` | It was trained on older ReAct examples. That exact line returns **HTTP 400**. |
| It invents a `search` tool | It pattern-matches "ReAct agent" and adds tools we don't have. |
| It writes a different `State` | It doesn't know your file unless you show it. |

You will spend longer debugging its guess than you would have spent writing it.

---

## Set Copilot up so it stops guessing

Copilot answers from what it can **see**. Before you ask anything:

1. **Open these three files as tabs** and leave them open:
   - `react_agent_starter.py` (the one you're editing)
   - `M3-Lab-Worksheet.md`
   - `M3-Learner-Handout.md`
2. **Select the code you're asking about** before opening Chat — it becomes the context.
3. **Reference files explicitly** when you need to: `#file:react_agent_starter.py`

> **Test it.** Ask Copilot: `In #file:react_agent_starter.py what is MAX_STEPS set to?`
> If it can't answer that, it isn't seeing your file, and everything else it says is a guess.

### The commands you'll use

| Command | What it does |
|---|---|
| **Ctrl + I** | Inline chat — edits code right where your cursor is |
| **Ctrl + Alt + I** | Chat panel — for discussion and explanation |
| `/explain` | Explains the selected code |
| `/fix` | Proposes a fix for the selected code or error |
| `/tests` | Generates tests for the selection |

---

## The four-move loop

Use this on **every step**. It is the difference between finishing the lab and learning it.

```
   1. UNDERSTAND  →  ask Copilot to explain the CONCEPT   (no code)
   2. PLAN        →  ask for the SHAPE, not the solution
   3. WRITE       →  you write it. Copilot autocompletes.
   4. CHALLENGE   →  ask Copilot to attack what you wrote
```

Move 4 is the one everybody skips, and it's where most of the learning is.

---
---

# PART 1 — the ReAct graph

## Step 1 · Define the State

**❌ Don't ask this**
```
Write the State class for a LangGraph ReAct agent
```
You'll get someone else's state with fields your code never uses.

**✅ Ask this instead**

**1 · Understand**
```
In LangGraph, what is the difference between the State object and a normal
Python variable that I keep outside the graph? Answer in 3 sentences, no code.
```

**2 · Plan**
```
I'm building a ReAct loop with a calculator tool. Reading
#file:M3-Lab-Worksheet.md Step 1, list ONLY the field names and types my
State needs, and one sentence each on why. Do not write the class.
```

**3 · Write** — type it yourself. It's five lines.

**4 · Challenge**
```
Here is my State. Which field would break first if I forgot to update it
inside a node, and what would the symptom look like?
```

> 💬 **Explain-back check:** which field grows during a run, and which never changes?

---

## Step 2 · The Action schema

⚠️ **This is the step where Copilot is most likely to hand you a broken answer.** Watch for it.

**1 · Understand**
```
What does llm.with_structured_output(MySchema) actually do to the request
that gets sent to the model? Explain in plain English, no code.
```

**2 · The trap — go and find it deliberately**
```
Write me an Action pydantic schema for a ReAct agent with a calculator tool
and a final_answer tool.
```

Look hard at what it gives you. **If it contains `args: dict`, you've just found the bug
we talked about.** Now ask:

```
I tried args: dict and the provider returned:
  400 - 'additionalProperties' is required to be supplied and to be false.
Why does a bare dict fail strict structured-output mode, and what should I use instead?
```

> That exchange — wrong answer, real error, ask why — will teach you more about schemas
> than any correct answer would have.

**3 · Write** — explicit fields, one per tool argument.

**4 · Challenge**
```
With my Action schema, is it possible for the model to return a tool name
that isn't in my list? Explain exactly what prevents it.
```

> 💬 **Explain-back check:** why is `Literal[...]` stronger than writing
> "only use these two tools" in the prompt?

---

## Step 3 · The calculator tool

**1 · Understand**
```
My calculator tool gets bad input. What is the difference, for an AI agent,
between raising an exception and returning a string starting with "ERROR:"?
Which one lets the agent recover, and why?
```

**2 · Plan**
```
I'm reusing my Module 1 calculator. List the safety checks it should have,
in the order they should run. Don't write the function.
```

**3 · Write** — you already wrote this in M1. Adapt it.

**4 · Challenge** — select your function, then:
```
/tests write tests that try to break this calculator, including
injection attempts and division by zero
```
Run them. Fix anything that raises instead of returning a string.

> 💬 **Explain-back check:** what does `{"__builtins__": {}}` stop someone from doing?

---

## Step 4 · `reason()` and `act()`

Do these **one at a time**. Asking for both at once gets you a blur.

### `reason()`

**1 · Understand** — the most important question in the whole lab:
```
The model has no memory between calls. In a ReAct loop, what exactly must
I put in the prompt each time so it knows what already happened? Explain
the mechanism, don't write code.
```

**2 · Plan**
```
List the steps reason() must perform, in order, as comments only.
Context: it reads state["scratchpad"], calls a structured LLM, and stores
the chosen action in state["action"].
```

**3 · Write** — fill in the comments it gave you.

**4 · Challenge**
```
Read my reason(). If the scratchpad already contains the result of a
calculation, what stops the model from just doing that same calculation
again? Be specific about which part of my prompt handles it.
```
If the honest answer is "nothing" — you've found the bug that causes infinite loops.

### `act()`

**1 · Plan**
```
act() takes state["action"] and does one of two things. Write the if/else
structure as comments, with no implementation.
```

**2 · Write** it.

**3 · Challenge**
```
There is no AI in my act() function. Is that correct for the ReAct pattern,
or have I misunderstood something?
```

> 💬 **Explain-back check:** which of the two functions talks to the model, and why only one?

---

## Step 5 · Wire the graph ⭐ the checkpoint

**1 · Understand**
```
In LangGraph, what is the difference between add_edge and
add_conditional_edges? Give me one situation where using the wrong one
creates an infinite loop.
```

**2 · Plan** — do NOT ask it to write `build_react_graph()`:
```
I have nodes called reason and act, and a router called is_done.
Draw the graph as ASCII showing which node goes where, including the
loop-back. No Python.
```
Sketch it on paper from that. Then write the six lines yourself.

**3 · Challenge** — this one catches a real bug:
```
My is_done() returns "end" or "loop". What happens if I return True/False
instead? Show me the exact error.
```

**🧪 CHECKPOINT — run it**
```
QUESTION: What is (23 * 19) + 100?
Expect:   an answer containing 537, in 2–4 steps
```

> **Note on the worksheet:** it says the agent "calls calculator twice."
> In practice it usually calls it **once** for `23 * 19` and does `+ 100` in its head.
> **Both are correct.** Ask yourself why that's allowed to happen — it's the
> "a tool is an option, not a rule" point from the session.

**If it loops forever:**
```
My agent calls the calculator with the same expression repeatedly until it
runs out of steps. Here is my reason() prompt: [paste it].
What is missing from the prompt?
```

---
---

# PART 2 — adding the planner

Part 2 is harder, and Copilot is **less** reliable here because plan-and-execute has more
variants in the wild. Lean more on moves 1 and 4.

## Step 6 · The Plan schema + planner

**1 · Understand**
```
What is the difference between a plan-and-execute agent and a plain ReAct
agent? Give me one task where planning wins and one where it's wasteful.
```

**2 · Plan**
```
My planner takes a question and produces a list of steps. What makes a plan
step "good" for an executor that only has a calculator? Give me 3 rules for
the planner prompt.
```

**3 · Write** the schema and node.

**4 · Challenge**
```
Run this question through my planner mentally: "A team has 3 sprints of
12, 19 and 8 story points. What's the average, and is it above 12?"
Would my prompt produce steps my calculator can actually execute?
```

---

## Step 7 · The executor ⚠️ the step everyone gets wrong

**Start with move 1, and be honest in your answer:**

```
My executor runs plan step 2, which says "divide the total by 3".
The total was computed in step 1. The model has no memory between calls.
What exactly do I have to pass into step 2 for this to work?
```

Sit with that answer before writing anything. **This is the bug that will cost you
20 minutes if you skip it.**

**2 · Plan**
```
List what the executor must do, as comments: which step to take, what
context to build from past_steps, and what to record afterwards.
```

**3 · Write** it.

**4 · Challenge** — before you run it:
```
Add a single print statement to my executor that would immediately reveal
whether step 2 can see step 1's result.
```
Run it. **Look at what actually got passed.** If the context is empty, you've found it.

> 💬 **Explain-back check:** what is the difference between `state["scratchpad"]` and
> `state["past_steps"]`, and why do both exist?

---

## Step 8 · Replan + its conditional edge

**1 · Understand**
```
Why does a plan-and-execute agent need a "replan" step at all? What can it
do that a simple for-loop over the plan cannot?
```

**2 · The gotcha — ask before you write:**
```
In LangGraph, if I set state["answer"] inside a conditional-edge routing
function, does that value persist? Explain what happens to writes made in
a router.
```
The answer to that is why your budget fallback silently returns `None` if you put it in
the wrong place.

**3 · Write** the node and the router — remembering **nodes write, routers only read**.

**4 · Challenge**
```
Trace my replan + replan_done. Give me one scenario where this agent
never terminates, and one where it terminates without an answer.
```

---

## Step 9 · Visualise

```
Show me how to print my compiled LangGraph as a mermaid diagram, and
tell me where I can paste the output to see it rendered.
```

**🧪 FINAL CHECKPOINT**
```
QUESTION: A team has 3 sprints of 12, 19, and 8 story points.
          What's the average per sprint, and is it above 12?
Expect:   an answer containing 13 and "above 12" / "yes"
          the plan printed before execution
          the diagram showing planner → executor → replan with the loop
```

---
---

# Prompts that make Copilot genuinely useful

Keep these for the rest of the course.

### When you hit an error
```
/explain
```
with the error selected. Then:
```
I got this error. Before you fix it, tell me what it means and which of my
assumptions was wrong.
```
> Asking "what did I misunderstand?" teaches you more than "fix it."

### When it works but you don't know why
Select the code:
```
/explain line by line, and tell me which line would break first if the
model returned something unexpected
```

### When you want a review, not an answer
```
Review my reason() function as if you were marking it. Do not rewrite it.
List what's wrong or fragile, worst first.
```

### When you're stuck and tempted to give up
```
Don't give me the code. Give me the ONE next thing I should check,
and how to check it.
```

---

## Prompts to avoid

| ❌ Don't | Why | ✅ Instead |
|---|---|---|
| "Do the whole lab" | You'll get a plausible file that doesn't match your State, and you'll debug someone else's design | Work step by step |
| "Fix all my errors" | You learn nothing, and it may fix the symptom not the cause | `/explain` first, fix second |
| "Write a ReAct agent" | It'll use `args: dict` and invent tools | Ask about **your** file |
| Pasting code you don't read | This is the one that fails the explain-back test | Read every line before accepting |

---

## Before you submit

Your deliverables are `react_agent.py`, the graph image, and a **½-page failure-mode note**.

Run this on your own finished file:

```
Read my react_agent.py. Ask me five questions about it that I should be
able to answer. Do not answer them yourself.
```

Answer all five, out loud. Any you can't answer is a gap — go back to that step.

Then, for your note:

```
I hit this bug: [describe it in your own words].
Ask me three questions that would help me explain the root cause clearly.
```

> **Write the note in your own words.** It is the one deliverable Copilot genuinely
> cannot do for you, because it's about what *you* got wrong and what *you* learned.

---

<div align="center" style="padding:14px; border-top:2px solid #0078d4; margin-top:34px;">
  <p style="color:#888; font-size:13px; margin:0;">
    <strong>JHF Agentic AI Bootcamp</strong> &mdash; Module 3<br/>
    Organized by Jerusalem High-Tech Foundry (JHF) &middot; In partnership with COMCEC
  </p>
</div>
