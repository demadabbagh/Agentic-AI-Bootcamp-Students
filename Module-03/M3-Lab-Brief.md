<!-- JHF-BRAND -->
<div align="center" style="padding:28px 20px; background:#ffffff; border:2px solid #e0e0e0; border-radius:12px;">
  <p style="margin:0 0 16px 0;">
    <img src="../assets/jhf-logo.png" alt="Jerusalem High-Tech Foundry (JHF)" height="54" style="vertical-align:middle; margin:0 22px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC - Cooperation for Development" height="72" style="vertical-align:middle; margin:0 22px;" />
  </p>
  <h1 style="color:#1a3c5e; margin:6px 0;">Agentic AI Bootcamp</h1>
  <h3 style="color:#0078d4; margin:4px 0; font-weight:600;">Module 3 &middot; Lab Brief</h3>
  <hr style="border:0; border-top:1px solid #0078d4; width:60%; margin:16px auto;" />
  <p style="font-size:12.5px; color:#777; margin:8px 0 0 0;">
    Organized by <strong>Jerusalem High-Tech Foundry (JHF)</strong> &nbsp;&middot;&nbsp; In partnership with <strong>COMCEC</strong>
  </p>
</div>

# The Working-Out Agent

### Module 3 lab · read this before you open the starter code

> **Read this first.** The worksheet tells you *how*. This tells you *what* and *why*.
> Ten minutes here will save you an hour of writing code you don't understand.

---

## The problem we are solving

Ask a language model a question with numbers in it:

> *"A team has 3 sprints of 12, 19 and 8 story points. What's the average per sprint,
> and is it above 12?"*

It will answer instantly, confidently, and **it might be wrong.**

That's not a flaw you can prompt your way out of. A language model predicts likely text.
It has no calculator inside it. When it says "the average is 13", that number was
*predicted*, not *computed*.

For a homework question, fine. For anything that matters — a bill, a deadline, a medical
dose, a payroll run — **"confidently wrong" is the worst possible failure mode**, because
nothing in the output tells you it happened.

---

## What you are building

A small agent that **works the answer out instead of guessing it.**

Given a question, it will:

1. **Think** about what it needs next
2. **Use a real calculator** for any arithmetic — Python does the maths, not the model
3. **Write down** what it found
4. Repeat, until it has enough to answer
5. **Show its working**, so you can check every number

```
        "What is (23 × 19) + 100?"
                    │
                    ▼
        ┌───────────────────────┐
        │        REASON         │   the model reads the question
        │  "what do I do next?" │   and everything found so far
        └───────────┬───────────┘
                    │  "use the calculator on 23 * 19"
                    ▼
        ┌───────────────────────┐
        │          ACT          │   YOUR PYTHON runs it.
        │   calculator("23*19") │   no AI in this part.
        └───────────┬───────────┘
                    │  → 437
                    ▼
        ┌───────────────────────┐
        │        OBSERVE        │   write it on the notepad
        │  notepad.append(437)  │
        └───────────┬───────────┘
                    │
              ┌─────▼─────┐
              │  ENOUGH?  │   ◀── the decision
              └─┬───────┬─┘
           no   │       │  yes
      ┌─────────▘       └────────┐
      │ loop back to REASON      │  give the final answer
      │ (now with a fuller       │  "537"
      │  notepad)                │
      └──────────────────────────┘
```

That loop has a name: **ReAct** — *Reason, Act, Observe*.

---

## Why you cannot just write a script

This is the part worth stopping on.

You might think: *"just write the calculation."* But look at the two questions you'll be
given:

| Question | Steps needed |
|---|---|
| "What is (23 × 19) + 100?" | multiply, then add |
| "3 sprints of 12, 19, 8 — average, and is it above 12?" | add three, divide by 3, compare |

**Different questions need different steps, in a different order, and you don't know
which until you read the question.** You cannot hardcode that.

So instead of writing the steps, you write a **loop that decides the next step each time**
— and the results of step 1 change what step 2 should be.

> ### 🔑 What's new since Module 2
>
> In Module 2 your agent ran the same nodes in the same order, every time —
> `plan_query → run_search → assess → answer`. Four nodes, always four.
>
> Remember what happened when the evidence wasn't good enough? `report_gap` wrote down
> *the exact search it wanted to run next* — and then **stopped**, because there was no
> arrow back to `run_search`.
>
> **Today you build that arrow.** Your agent can go round again, as many times as it
> needs, and decide for itself when to stop.

---

## The two things that make this hard

### 1 · The model has no memory

This surprises everyone. Between one call and the next, the model forgets **everything**.

So how did it know, on the second pass, that 23 × 19 = 437?

**Because you told it again.** You keep a list — the *notepad*, or in code,
`state["scratchpad"]` — and you paste the whole thing into every prompt.

> **If it isn't on the notepad, the model cannot see it.**
> Not "might miss it" — *cannot*. This one sentence explains most of the bugs you'll hit.

### 2 · The loop must be able to stop

An agent that decides its own next step can decide badly, forever. Yours will have a
hard cap (`MAX_STEPS`) and a graceful exit.

Nothing in this lab relies on the model choosing to stop. **You** stop it.

---

## What you will hand in

Two parts, built on top of each other.

### Part 1 — the ReAct loop  *(~60 min)*

An agent that answers **"What is (23 × 19) + 100?"** by reasoning, using the calculator,
and returning **537**.

**Done when:** it returns 537 in 2–4 steps, with no manual JSON parsing anywhere in your file.

### Part 2 — add a planner  *(~45 min)*

Plain ReAct decides one step at a time, which wanders on longer tasks. So you add a
**planner** that breaks the goal into steps *before* any of them run, and a **replan** step
that can revise the plan based on what actually happened.

```
   PLANNER ──▶ EXECUTOR ──▶ REPLAN ──finish──▶ done
   goal →      run ONE      still more
   [steps]     step         to do? ──────┐
      ▲                                  │
      └──────────────────────────────────┘
              revised plan
```

**Done when:** it answers *"A team has 3 sprints of 12, 19 and 8 story points. What's the
average per sprint, and is it above 12?"* with **13, yes** — and you can show the graph
diagram.

---

## Your three deliverables

1. **`react_agent.py`** — the working agent, with a step cap
2. **The graph diagram** — an image or the mermaid text
3. **A ½-page failure-mode note** — one bug you hit, and how you fixed it

> The third one is the one that gets read most carefully. It is also the only one an AI
> assistant cannot write for you, because it is about what *you* got wrong and what *you*
> learned. Keep notes as you go — you'll forget the details by the end.

---

## How to work

| | |
|---|---|
| **The brief** | this file — what and why |
| **The worksheet** | `M3-Lab-Worksheet.md` — the nine steps, in order |
| **The handout** | `M3-Learner-Handout.md` — the concepts, if you want the theory |
| **Copilot** | `M3-Copilot-Guide.md` — how to use it so you actually learn |
| **The code** | `starter-code/react_agent_starter.py` — fill in the TODOs |

**Start here:**

```bash
cd Module-03/starter-code
python react_agent_starter.py      # should print READY
```

If it prints anything other than READY, fix that before writing a line of code.

---

## One thing to watch for

When your Part 1 agent works, look closely at what it actually did.

You told it, in the prompt, to use the calculator for arithmetic. It will often use the
calculator for `23 × 19` and then do the `+ 100` **in its own head**.

It gets the right answer, so nothing looks broken. On bigger numbers it wouldn't be.

> ### A tool is an option, not a rule.
>
> The model *chooses* whether to reach for it. If you need a guarantee, you enforce it in
> **code** — not by asking more politely in the prompt.
>
> **Prompts ask. Code enforces.** Remember that one; it comes back in every module after this.

---

<div align="center" style="padding:14px; border-top:2px solid #0078d4; margin-top:34px;">
  <p style="color:#888; font-size:13px; margin:0;">
    <strong>JHF Agentic AI Bootcamp</strong> &mdash; Module 3 Lab Brief<br/>
    Organized by Jerusalem High-Tech Foundry (JHF) &middot; In partnership with COMCEC
  </p>
</div>
