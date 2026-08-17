# The Evidence Desk

### Module 2 Bridge Project · due before Friday · ~3–4 hours

---

## What you are building

An agent that searches the web, decides whether what it found is **good enough**,
and then does **one of two different things**.

```
                    ┌──────────────┐
   your question ──▶│  plan_query  │  LLM · turn it into a search query
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  run_search  │  TOOL · a real web call. no model here.
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │    assess    │  LLM · is this good enough to answer?
                    └──────┬───────┘
                           │
                     ┌─────▼─────┐
                     │  ENOUGH?  │   ◀── the decision
                     └─┬───────┬─┘
                  yes  │       │  no
              ┌────────▼──┐ ┌──▼──────────┐
              │write_answer│ │ report_gap │
              └────────────┘ └────────────┘
               answer it,      do NOT answer.
               cite the URL    say what is missing.
```

**Everything you have built so far ran the same steps in the same order, every
time.** This one asks a question and goes a different way depending on the
answer. That is the line between a script and an agent.

---

## Pick ONE use case

<table>
<tr><th width="50%">A · The Fact-Check Desk</th><th width="50%">B · The Market Brief</th></tr>
<tr><td>Someone sends you a <b>claim</b>.<br>Is it true?</td>
    <td>Someone sends you a <b>topic</b>.<br>Brief me before my meeting.</td></tr>
<tr><td><code>"Anthropic was founded by former OpenAI employees"</code></td>
    <td><code>"agentic AI startups in Jordan"</code></td></tr>
<tr><td>A verdict <b>with source URLs</b>, or an honest "cannot verify".</td>
    <td>A short brief <b>with source URLs</b>, or a partial brief naming its gaps.</td></tr>
</table>

Both have the same shape. They differ in what *"good enough"* means — and that
difference is what you will write about at the end.

---

## What your agent must produce

### When the evidence is there

```
QUESTION : Anthropic was founded by former OpenAI employees

VERDICT : ENOUGH
ROUTE   : ANSWERED

This is supported. Anthropic was founded in 2021 by former OpenAI
staff, including Dario and Daniela Amodei.
source: https://en.wikipedia.org/wiki/Anthropic
```

### When it is not

```
QUESTION : the flurbotron 9000 was released in 2019

VERDICT : NOT_ENOUGH
ROUTE   : GAP REPORTED

This could not be verified. The search returned nothing for this
product, and nothing found refers to it.

NEXT SEARCH: flurbotron 9000 product release date
```

> ### Look at the second one again.
> **There is no date in it.**
>
> It did not guess. It did not hedge. It did not produce a
> plausible-sounding paragraph.
>
> **Refusing is not a failure here. It is the product.**

---

## Setup

**1 · Get your own API key.** Free at <https://openrouter.ai/keys>.

**2 · Create a file called `.env`** in the project folder:

```
OPENROUTER_API_KEY=sk-or-...
```

**3 · That's it.** The search tool needs **no key** and **no install** — it is
already written for you in `search_tools.py`. Read it if you're curious, then
leave it alone. The interesting part of this project is not HTTP.

> **If you see `401` or `AuthenticationError`** — your key is wrong, expired, or
> has a stray space. Make a new one. It is not your code.

### The tool can return two special strings

| It returns | It means |
|---|---|
| `NO_RESULTS` | the search ran, and genuinely found nothing |
| `SEARCH_UNAVAILABLE` | the search could not run at all |

**These are not the same thing.** *"I found nothing"* is a finding.
*"I could not look"* is not. Your agent must not report them the same way.

---

# Part A · LangGraph

Open `starter-code/research_langgraph_starter.py` — **13 TODOs.**

```
TODO 1 ....... the model client
TODO 2 ....... plan_query    ── question → one short search query
TODO 3 ....... run_search    ── call the tool   ⚠ two lines, NO LLM
TODO 4 ....... handle NO_RESULTS / SEARCH_UNAVAILABLE early
TODO 5 ....... assess        ── is the evidence enough?
TODO 6 ....... read_verdict  ── ⚠ there is a trap in here
TODO 7 ....... the router function
TODO 8 ....... write_answer  ── answer + cite a URL
TODO 9 ....... report_gap    ── ⚠ must NOT answer
TODO 10-13 ... nodes, entry point, THE CONDITIONAL EDGE, both → END
```

### The one line this whole project exists for

Everything you have written until now:

```python
graph.add_edge("assess", "write_answer")        # always go there next
```

Today:

```python
graph.add_conditional_edges(
    "assess",              # after this node runs
    choose_next,           # call this function
    {                      # and map what it returns to a node
        "write_answer": "write_answer",
        "report_gap":   "report_gap",
    },
)
```

The next step is chosen **at runtime**, from something the model just produced.

### ⚠ Three warnings

**TODO 3 is two lines, and it is not filler.** It proves something people get
wrong for months: **a node is just a Python function.** It does not have to call
a model. Yours calls a web API instead.

**TODO 4 is where reliability starts.** There is no point paying for a model
call to look at the word `NO_RESULTS`. Check in Python first, call the model
second.

**TODO 6 has a trap in it, and most of you will fall in.** Look carefully at the
two words your model is choosing between:

```
    ENOUGH
NOT_ENOUGH
```

One **contains** the other. Get the order wrong and your agent will confidently
answer questions it has **no evidence for** — and nothing will raise, nothing
will warn, and the output will look perfectly fine.

Test it on purpose.

---

# Part B · CrewAI

Open `starter-code/research_crewai_starter.py` — **8 TODOs.**
**Build Part A first.**

A sequential crew cannot express that branch. So you do what CrewAI actually
offers: **hire a manager and let it decide.**

```
                    ┌───────────────────┐
                    │      MANAGER      │  built for you by manager_llm
                    └─────────┬─────────┘
                    ┌─────────┴─────────┐
                    ▼                   ▼
          ┌──────────────────┐  ┌──────────────┐
          │   Researcher     │  │    Writer    │
          │ holds the TOOL   │  │ holds nothing│
          └──────────────────┘  └──────────────┘
```

```
TODO 1 ... the model client
TODO 2 ... wrap web_search with @tool  ── the docstring is FOR THE MODEL
TODO 3 ... the Researcher (has tools=[search_web])
TODO 4 ... the Writer (no tools)
TODO 5 ... the research task   ⚠ no agent=
TODO 6 ... the writing task    ⚠ no agent=  ── the branch lives here now
TODO 7 ... Process.hierarchical + manager_llm
TODO 8 ... log which way it went
```

### Two rules CrewAI will shout at you about

| If you do this | You get |
|---|---|
| hierarchical with no `manager_llm` | `Attribute manager_llm or manager_agent is required` |
| the manager also listed in `agents=` | `Manager agent should not be included in agents list` |
| sequential with no `agent=` on a task | `Sequential process error: Agent is missing in the task` |

Those messages are precise. Read them — they tell you exactly which TODO you
have not done yet.

---

# Part C · Run the experiment

Run **both** versions on **both** of these:

| Query | What should happen |
|---|---|
| a real claim about something well known | **answered**, with a source URL |
| `the flurbotron 9000 was released in 2019` | **refused**, with a `NEXT SEARCH:` line |

The second is nonsense on purpose, and the search reliably returns nothing for
it. So there is exactly one honest response.

### 👀 Watch the CrewAI version on that second query

```
   LangGraph                          CrewAI
   ─────────                          ──────
   refusing is a CODE PATH            refusing is an INSTRUCTION
   report_gap has no prompt           you asked the manager nicely
   that could produce an answer       in a backstory

   it CANNOT invent                   it MIGHT invent
```

**If your crew invents an answer anyway, that is a real result — not a failure
in your homework.** Write down exactly what it did. It is the most valuable
thing you will find this week.

---

## Hand in

1. `research_langgraph.py`
2. `research_crewai.py`
3. **Four saved outputs** — 2 frameworks × 2 queries
4. `README.md` answering these four questions:

> **1.** Which framework would you ship for your use case, and why?
>
> **2.** On the nonsense query — did each version refuse? Paste what they
> actually produced.
>
> **3.** Where does the decision live in each one? Which could you *prove* to a
> customer who asks *"how do I know it will never make something up?"*
>
> **4.** Your `report_gap` ended with `NEXT SEARCH: ...`.
> **Why couldn't your agent run that search?**

Question 4 is not rhetorical. Answer it properly — in two sentences, say exactly
what is missing from your graph.

---

## Before you submit

- [ ] Both versions run on both test queries
- [ ] Your evidence contains a **real URL** (proves the tool actually ran)
- [ ] `NO_RESULTS` and `SEARCH_UNAVAILABLE` are handled **differently**
- [ ] The nonsense query produces **no invented facts** in the LangGraph version
- [ ] `report_gap` **never** answers the question
- [ ] Your LangGraph run **prints which route it took**
- [ ] `.env` is **not** committed
- [ ] README answers all four questions

---

## Stuck?

| Symptom | Almost certainly |
|---|---|
| `401` / `AuthenticationError` | bad or expired key — make a new one |
| `NameError: name 'llm' is not defined` | TODO 1 still commented out |
| `Graph must have an entrypoint` | TODO 11 — no `set_entry_point` |
| a section comes out **empty**, no error | a node forgot to `return state` |
| `OPENAI_API_KEY is required` | your Agent is missing `llm=llm` |
| `ModuleNotFoundError` | wrong virtual environment — activate `.venv` |
| it pauses ~20s asking about traces | press Enter, it times out to No |

Ask early. A stuck hour helps nobody.

---

## Optional stretch

Only after the required work runs.

1. **CrewAI Flows.** CrewAI *does* have a visible conditional branch — it lives
   in `crewai.flow` with a `@router` decorator, **not** in Crews. Rebuild Part B
   with it and compare all three approaches.
2. Add a third route: `PARTIAL` — answer the part you can, flag the rest.
3. Count model calls per route. Which path is cheaper, and why does that matter
   at ten thousand requests a day?

---

## One last thing

When your agent takes the `NOT_ENOUGH` path, it will tell you **exactly what it
would search for next**.

And then it will stop.

```
    NEXT SEARCH: flurbotron 9000 product release date
                            │
                            ▼
                          ( ? )
                    nowhere to go
```

It knows the next move. It has no way to make it.

Look at your graph and work out precisely what is missing — **one arrow, in one
place.**

Bring that answer on Friday.
