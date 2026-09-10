# Bridge Project — Borrow Somebody Else's Tools

**Time:** ~2 hours · **Needs an API key?** Yes, your `OPENROUTER_API_KEY` · **Needs internet?** Yes

> ### 👉 Start with the picture
> Open **[`THE-BRIEF.html`](THE-BRIEF.html)** in a browser and keep it open while you build.
> It shows the problem, the loop, the three scenarios side by side, and exactly what you change.
> This README is the detail underneath it.

---

## The point

Everything you have built so far, you wrote yourself. Your tools, your server, your agent.

**Not this time.** In this project you will not write a single tool. You will connect your
agent to a **real, published MCP server written by strangers**, ask it what it can do, and
put it to work.

That is what MCP is actually for. The skill is not writing tools — it is *borrowing* them.

---

## Choose ONE scenario

Three briefs below. **Pick one.** Each borrows a different real server from the public MCP
ecosystem. None needs an API key of its own.

| | Scenario | The server you borrow | Tools |
|---|---|---|---|
| **A** | The Release Smoke Test | `@playwright/mcp` | 24 |
| **B** | The Tech Radar | `@devabdultech/hn-mcp-server` | 9 |
| **C** | The Data Story | `@antv/mcp-server-chart` | 27 |

They are deliberately different shapes. A **drives a real browser**. B **pulls live data off
the internet**. C **chooses one right tool out of twenty-seven**.

---

## Scenario A — The Release Smoke Test

> Every release, somebody on your team opens the site and clicks around to check nothing is
> obviously broken. It takes fifteen minutes, it is boring, and it gets skipped when people
> are busy. Automate it.

Your agent gets a **real headless browser**. It can navigate, read the page, click, fill
forms, and read the console.

Build an agent that visits a page and reports:

1. The page title
2. The headline of the main item on the page
3. **Whether the browser logged any console errors**
4. Whether a specific link or button is actually present

**Non-negotiable:** every statement must come from something it *observed*. If it could not
verify something, it must say so rather than guess. An automated check that quietly invents
a pass is worse than no check at all.

**Why this is harder than it looks.** 24 tools, and the page is a live web page that changes
between runs. A snapshot of a real site is enormous — the agent has to *search* it rather
than read it all.

**Try to break it.** Point it at a page that is genuinely broken. Does it report the problem,
or does it report success anyway?

> ⚠️ **One-time setup, ~1 minute:** `npx playwright install chromium`

---

## Scenario B — The Tech Radar

> Your CTO wants ten minutes of "what is the industry talking about this week" every Monday.
> Right now a senior engineer skims Hacker News and writes it up by hand.

Build an agent that reads **live Hacker News** and produces a digest:

1. The three most significant stories right now
2. For each: the title, and **one line on why it matters to an engineering team**
3. The single theme connecting them — **or an honest statement that there isn't one**

**Why this is harder than it looks.** "Most significant" is a judgement, not a lookup. Points
and comment counts measure popularity, not importance. And the server can fetch comment
threads — which is where the real signal often is, and also where the noise is.

**Try to break it.** Run it twice an hour apart. Does the digest change? Should it have?

---

## Scenario C — The Data Story

> You are the analyst on a team that reports numbers upward every week. Somebody hands you
> raw figures in a message and wants "a chart and a sentence" before the stand-up.

Build an agent that takes a small set of real numbers and produces:

1. **A chart** — a real image URL you can open in a browser
2. **One sentence** on why that chart type suits this data
3. **One sentence** of insight from the numbers themselves

Use your own numbers, or these:

```
AZ-900 Azure Fundamentals   16 registrations
AWS Cloud Practitioner      11 registrations
```

**Why this is harder than it looks.** This server offers **27 tools** — column, bar, line,
pie, radar, sankey, treemap, fishbone, mind map and more. Nothing tells the agent which to
use. It has to read 27 descriptions and choose. That choice *is* the exercise.

**Try to break it.** Give it data that suits a line chart (something over time), then data
that suits a pie (parts of a whole). Does it switch, or reach for the same chart every time?

---

## Start here — three commands

**1. See what your server can do.** This is the habit worth building:

```bash
cd Module-05/bridge-project
python explore_server.py qa            # or: radar | chart
```

It prints every tool, every argument, whether each is required, and what the server says
each one is for. **You never read the server's source.** You ask it, and it tells you.

> First run downloads the server and can take a couple of minutes. After that it is cached.

**2. Fill in three TODOs** in `bridge_agent_starter.py`:

| TODO | Where | What |
|---|---|---|
| **1** | top of file | `SCENARIO = "..."` — one line |
| **2** | top of file | `QUESTION = "..."` — the task you are setting |
| **3** | inside `reason()` | the rules that stop the loop running forever |

**3. Run it:**

```bash
python bridge_agent_starter.py
```

---

## Writing a good QUESTION (TODO 2)

This is most of the project, and it is not a coding problem.

A good question **needs several tool calls** and cannot be answered by guessing. Compare:

```
BAD    "What is on the Hacker News front page?"
       Vague. The agent makes one call and paraphrases.

GOOD   "Get the current top stories, pick the three that matter most to an
        engineering team, say why each matters in one line, and name the theme
        that connects them - or say plainly that there isn't one."
       Now it has to fetch, judge, compare, and be honest when there is no pattern.
```

If your agent answers without calling a tool, **your question is too easy** — not your agent.

---

## Five traps, all of which we hit while building this

These are real. Every one of them happened on this exact code.

### 1. A stranger's server throws at you

Your own server returned `{"error": ...}` politely because you wrote it that way. A
third-party server owes you nothing. Send `pageId` as `"79706"` when it wants `79706` and it
**raises** — which without a `try/except` kills your agent mid-run.

The starter already catches this. Read that block and understand why it is there.

### 2. Windows paths break the call before it is sent

`{"path": "C:\Users\..."}` is invalid JSON — `\U` is not a valid escape. The call fails
before it ever reaches the server, and the error mentions nothing about paths.

**Use forward slashes**, or relative paths like `./src`.

### 3. What you don't show the model, it cannot use

Our first version of this agent truncated each tool description to 110 characters. Scenario C
then failed on **ten calls in a row** — because the exact data shape the chart server wants,
`[{ category: 'A', value: 10 }]`, is documented in the *argument's* description, and we were
throwing it away.

Showing the required arguments' descriptions fixed it in **one call**. The catalogue you
build for the model decides whether it can use the server at all.

### 4. Big nested arguments hit the output limit

Ask for a deeply nested object in one call and the model can run out of room mid-JSON. If you
see `LengthFinishReasonError`, do the work in **smaller batches**.

### 5. A borrowed server does not speak your terminal's language

The chart server documents its own tools **in Chinese**. Printing that on a default Windows
console raises `UnicodeEncodeError` — and it looks exactly like the server is broken, when
the connection was fine and only the *printing* failed.

Both scripts here already force UTF-8 output. If you write your own client, do the same:

```python
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

---

## What to submit

Open a GitHub issue, same as the last project:

1. **Which scenario** you chose, and why
2. Your `bridge_agent_starter.py` — the three TODOs filled in
3. **The full transcript** of a successful run — every tool call and the final answer
4. Scenario A: what it could **not** verify. Scenario B: your digest. Scenario C: the **image URL**
5. **One paragraph:** something your agent got *wrong* at first, and what you changed. This
   is the part we will actually read. "It worked first time" is almost never true, and a
   good failure story is worth more than a clean run.

---

## Stretch — find your own server

The three above are a starting point, not the ecosystem. There are hundreds of public MCP
servers; a good index is **`awesome-mcp-servers`** on GitHub.

Point `explore_server.py` at any of them without touching the code:

```bash
python explore_server.py --raw npx -y @modelcontextprotocol/server-memory
python explore_server.py --raw npx -y @modelcontextprotocol/server-sequential-thinking
```

If you find one you like and it needs no API key, use it — just say so in your submission.

**Two things to check before you commit to a server:**

- **Does it need an API key?** Many of the well-known ones (GitHub, Slack, Brave Search,
  Google Maps) do. Those are excellent servers and completely unusable for this project.
- **Does it actually work?** We ruled several out by testing, not by reading their READMEs:
  one rate-limited us on the very first call, one returned an empty string from three of its
  four tools, and every Python-based server was unreachable from this network. **A published
  server is not a working server.** Run `explore_server.py` against it and call one tool
  before you build anything on top of it.
