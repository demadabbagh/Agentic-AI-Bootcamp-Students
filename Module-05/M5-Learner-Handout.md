<!-- JHF-BRAND -->
<div align="center" style="padding:28px 20px; background:#ffffff; border:2px solid #e0e0e0; border-radius:12px;">
  <p style="margin:0 0 16px 0;">
    <img src="../assets/jhf-logo.png" alt="Jerusalem High-Tech Foundry (JHF)" height="54" style="vertical-align:middle; margin:0 22px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC - Cooperation for Development" height="72" style="vertical-align:middle; margin:0 22px;" />
  </p>
  <h1 style="color:#1a3c5e; margin:6px 0;">Agentic AI Bootcamp</h1>
  <h3 style="color:#0078d4; margin:4px 0; font-weight:600;">Module 5 &middot; Tools, APIs &amp; MCP &mdash; Learner Handout</h3>
  <hr style="border:0; border-top:1px solid #0078d4; width:60%; margin:16px auto;" />
  <p style="font-size:14px; color:#555; margin:6px 0;">
    <strong>Lead Trainer</strong><br/>
    <a href="https://www.linkedin.com/in/alaaldin-ahmed-260266150" target="_blank">Alaaldin Ahmed</a>
  </p>
  <p style="font-size:12.5px; color:#777; margin:8px 0 0 0;">
    Organized by <strong>Jerusalem High-Tech Foundry (JHF)</strong> &nbsp;&middot;&nbsp; In partnership with <strong>COMCEC</strong>
  </p>
</div>

# Module 5 — Tools, APIs & the Model Context Protocol (MCP)
## Learner Handout

> **Duration:** 5 hours · **Prereq:** M1–M4.
> **You need:** your env, LangGraph, MCP SDK, a public API key (or use the provided mock), key in `.env`, GitHub Copilot.
> Today your agent connects to the **real world** through **MCP**.

> 🧩 **Patterns in this module:** **Tool Use (Function Calling)** and **MCP** — see the shared **[Pattern Catalog → PATTERNS.md](../PATTERNS.md)**.

![Where we are in the stack — Tools](../assets/diagrams/augmented-llm-tools.png)

> *Book alignment: this module follows **Ch. 5 "Tooling, Learning & Protocols"**. Its core message: without tools an agent can only **think**, not **act** — the tool module is what lets it interact with the world.*

---

## Learning Objectives

After this module you can:
1. **Design robust tools** (typed inputs, validation, clear errors, idempotency awareness).
2. **Integrate a real external API** (rate limits, timeouts, auth, failure handling).
3. **Explain MCP architecture** (servers, tools/resources, transport, client–server split).
4. **Build a custom MCP server** and **consume it** from an agent as an MCP client.

---

## 1. Three kinds of tools (a map before the mechanics)

Before wiring anything, it helps to know **what kind of tool** you're adding. A widely-used taxonomy (Google's *Agents* whitepaper) splits tools into three:

| Kind | What it is | Runs where | Example in this course |
|---|---|---|---|
| **Functions** | Code the **agent decides to call**; **your app executes** it and feeds the result back. | Client-side (your code) | the `calculator` you built in M1; a Python function |
| **Extensions** | A live connection the **agent calls directly** at runtime (a bridge to an API). | Agent-side | an MCP tool the agent invokes itself |
| **Data Stores** | Read-only **knowledge** the agent retrieves from (vectors/DBs) — this is **RAG**. | External store | the vector memory you'll build in M6 |

> **Why this matters:** most confusion about "tools" disappears once you ask *"is this a Function (my code runs it), an Extension (the agent runs it live), or a Data Store (the agent retrieves knowledge)?"* **MCP** is the standard way to expose **Extensions** so any agent can discover and call them. **Data Stores** are covered as memory/RAG in **M6**.
>
> *Source: Google, "Agents" whitepaper — tool taxonomy. Written for this course; see `FURTHER-READING.md`.*

---

## 2. Tool Design Principles

A tool is an API your agent calls. Bad tools → unreliable agents.

| Principle | Why |
|---|---|
| **Typed inputs/outputs** | Clear schema the model can target reliably. |
| **Good description** | The model *reads* it to decide how to call — it's part of the prompt. |
| **Validation** | Reject bad args with a clear message; never crash the agent. |
| **Idempotency awareness** | Read tools are safe to retry; write tools have side-effects — handle differently. |
| **Structured error surfaces** | Return errors the agent can reason about ("rate limited"), don't throw. |
| **Single capability** | One tool = one clear job. Avoid mega-tools. |


> Good tool design = reliable action selection (M3). The schema + description are exactly what the model sees.

---

## 3. Integrating Real APIs

Real APIs misbehave — engineer for it:

- **Auth:** keys via env/secrets, never hardcoded.
- **Rate limits:** respect them; back off and retry on `429`.
- **Timeouts:** always set one — a hung tool hangs the agent.
- **Failure handling:** map network/HTTP errors to clean error observations.
- **Cost/latency:** external calls add latency; cache when sensible (M11).

---

## 4. MCP Architecture (the core of this module)

**MCP (Model Context Protocol)** standardizes how an agent discovers and calls tools and data — "USB-C for tools." Write the integration **once**, reuse it across agents and frameworks.

```
   AGENT (MCP CLIENT)  ◀──── MCP protocol ────▶  MCP SERVER
   LangGraph reasoner       list tools,           exposes TOOLS + RESOURCES
   picks & calls tools      call tool,            wraps real APIs / data / files
                            read resources                  │ calls
                                                   real APIs / DB / filesystem
```

| Term | Meaning |
|---|---|
| **Server** | Exposes **tools** (actions) and **resources** (readable data); wraps APIs/data. |
| **Client** | Your agent — discovers (lists) and calls the server's tools. |
| **Transport** | How they talk (stdio for local, HTTP/SSE for remote). |
| **Tool vs resource** | Tool = an action to call; resource = data the model can read. |

**Why it matters:** MCP decouples tool implementation from the agent. Swap servers without touching agent code; share one server across teams.

> ⚠️ **MCP = agent ↔ tools. A2A (M9) = agent ↔ agent.** Different layers — don't conflate.

---

## 5. Security of Tool Access

- **Least privilege:** expose only the tools the agent needs; scope keys minimally.
- **Secrets stay server-side:** the agent/model never sees raw API keys — the MCP server holds them.
- **Untrusted output:** treat tool/API results (e.g., web search) as **data, not instructions** — a prompt-injection risk handled in M11.
- **Validate args at the server boundary** too — don't trust the agent blindly.

---

## 6. Today's Lab (preview)

You'll:
- **Part 1:** build an **MCP server** exposing two tools — `web_search(query)` and `read_data(key)` — with typed schemas, validation, timeouts, and error surfaces.
- **Part 2:** point a **LangGraph agent** at the server as an **MCP client**; it lists tools, then answers a question needing both.

Full steps: **`M5-Lab-Worksheet.md`**. Starters: **`starter-code/`** (mock-API fallback included).

**Definition of done:** the agent answers *"What's the capital of the country with ISO code 'JO', and one recent headline about it?"* by calling both tools **through MCP** — not local imports.

---

## 7. Deliverables (graded)

1. **MCP server** (`mcp_server.py`) — ≥2 tools with typed schemas, validation, timeouts, error handling.
2. **MCP client agent** (`mcp_client_agent.py`) — a LangGraph agent that lists + calls the tools live.
3. **Tool schema doc** — a short table documenting each tool's args, output, and error cases.

---

## 8. Key Terms

| Term | Meaning |
|---|---|
| Tool | An action capability the agent can call. |
| Resource | Readable data exposed to the model. |
| MCP server | Exposes tools/resources; wraps APIs/data; holds secrets. |
| MCP client | The agent that discovers and calls tools. |
| Transport | The channel between client and server (stdio/HTTP). |
| Least privilege | Expose/scope only what's needed. |
| Untrusted output | Tool/API results treated as data, never instructions. |

---

## 9. Quiz (5 min)

1. Name three properties of a well-designed tool.
2. Why is the tool description important to the model?
3. In MCP, what's the difference between a server and a client?
4. What does MCP standardize, in one sentence?
5. MCP vs A2A — one line each.
6. Why should API keys live in the MCP server, not the agent?
7. A web-search result should be treated as ___, not ___.
8. Give two ways a real API can break an agent and a defense for each.

---

<div align="center" style="padding:14px; border-top:2px solid #0078d4; margin-top:34px;">
  <p style="margin:0 0 8px 0;">
    <img src="../assets/jhf-logo.png" alt="JHF" height="28" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC" height="40" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
  </p>
  <p style="color:#888; font-size:13px; margin:0;">
    <strong>JHF Agentic AI Bootcamp</strong> &mdash; Module 5<br/>
    Lead Trainer: <a href="https://www.linkedin.com/in/alaaldin-ahmed-260266150">Alaaldin Ahmed</a><br/>
    Organized by Jerusalem High-Tech Foundry (JHF) &middot; In partnership with COMCEC
  </p>
</div>

