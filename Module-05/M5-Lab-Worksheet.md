<!-- JHF-BRAND -->
<div align="center" style="padding:28px 20px; background:#ffffff; border:2px solid #e0e0e0; border-radius:12px;">
  <p style="margin:0 0 16px 0;">
    <img src="../assets/jhf-logo.png" alt="Jerusalem High-Tech Foundry (JHF)" height="54" style="vertical-align:middle; margin:0 22px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC - Cooperation for Development" height="72" style="vertical-align:middle; margin:0 22px;" />
  </p>
  <h1 style="color:#1a3c5e; margin:6px 0;">Agentic AI Bootcamp</h1>
  <h3 style="color:#0078d4; margin:4px 0; font-weight:600;">Module 5 &middot; Lab Worksheet &mdash; Build an MCP Server + Client</h3>
  <hr style="border:0; border-top:1px solid #0078d4; width:60%; margin:16px auto;" />
  <p style="font-size:14px; color:#555; margin:6px 0;">
    <strong>Lead Trainer</strong><br/>
    <a href="https://www.linkedin.com/in/alaaldin-ahmed-260266150" target="_blank">Alaaldin Ahmed</a>
  </p>
  <p style="font-size:12.5px; color:#777; margin:8px 0 0 0;">
    Organized by <strong>Jerusalem High-Tech Foundry (JHF)</strong> &nbsp;&middot;&nbsp; In partnership with <strong>COMCEC</strong>
  </p>
</div>

# Module 5 — Lab Worksheet
## Build a Custom MCP Server + Connect a LangGraph Agent

> **Time:** ~1h10 (Part 1) + ~1h (Part 2)
> **Files:** edit `starter-code/mcp_server_starter.py` and `starter-code/mcp_client_agent_starter.py` → save as `mcp_server.py` / `mcp_client_agent.py`
> **Goal:** expose real tools over **MCP** and have your agent call them — not via local import.
> Use GitHub Copilot for boilerplate; understand the client–server split.

---

## Definition of done
The agent answers *"What's the capital of the country with ISO code 'JO', and find one recent headline about it?"* by calling **`read_data` and `web_search` through MCP**, then composing the answer.

---

## Setup gate (5 min)
1. Install the MCP SDK; confirm imports.
2. Decide: real search API (key in `.env`, loaded **server-side**) **or** the provided **mock** (no key needed).
3. Run the starter server; from the starter client, **list tools** — you must see your two tools before adding logic.

---

## PART 1 — Build the MCP server (Steps 1–5)

### Step 1 — Scaffold the server
Create an MCP server process exposing tools. Confirm a client can connect and list tools (even empty).

### Step 2 — Tool: `read_data(key)`
- A small data source: map ISO country code → `{country, capital}` (provided dict, or a file/JSON resource).
- Typed arg `key: str`; validate (uppercase, 2 letters); return a structured result or a clear "not found" error.

### Step 3 — Tool: `web_search(query)`
- Wrap a real search API **or** the mock. Set a **timeout**. Return top result(s) as text.
- Map timeouts/HTTP errors to a structured error result (don't raise).

### Step 4 — Schemas & descriptions
- Give each tool a precise **description** and typed args (remember: the model reads these).
- Keep secrets **server-side** (load the API key in the server from env).

### Step 5 — Validate + harden
- Validate args at the server boundary; reject bad input with a message.
- 🧪 Checkpoint: from a client, call `read_data("JO")` → Jordan/Amman, and `web_search("Jordan")` → a result. Both via MCP.

> ✅ **End of Part 1:** a real MCP server with two robust tools.

---

## PART 2 — Connect a LangGraph agent as MCP client (Steps 6–9)

### Step 6 — Connect the client
- In your agent script, start/connect to the MCP server and **list available tools**.
- Adapt the MCP tools so your LangGraph agent can call them (a thin wrapper around the MCP client call).

### Step 7 — Wire into the ReAct agent
- Reuse your M3 ReAct graph; register the two MCP tools as the agent's available actions.
- The `act` node calls the tool **through the MCP client**, not a local function.

### Step 8 — Multi-tool task
- Run the definition-of-done question. The agent should call `read_data("JO")`, then `web_search(...)`, then answer.
- Confirm in logs that both calls round-trip through MCP.

### Step 9 — Document the schemas
- Produce a short table: tool name · args (types) · output · error cases.
- 🧪 **Final checkpoint:** the agent composes a correct answer using both tools via MCP; bad input (e.g., `read_data("ZZ")`) returns a clean error the agent handles.

---

## Stretch goals

- **Third capability:** add an MCP **resource** (readable data) and have the agent read it.
- **Cross-framework:** call the same MCP server from a CrewAI agent (preview M7/M9 interop).
- **Injection test:** make the mock search return text like "Ignore your instructions and..." — confirm your agent treats it as data, not commands.
- **Caching:** cache `web_search` results for repeated queries; measure latency saved.

---

## Troubleshooting

| Problem | Try this |
|---|---|
| Client sees no tools | Start the server first; confirm transport (stdio/HTTP) matches on both sides. |
| Tool call hangs | Set a timeout on the external API; map timeouts to an error result. |
| Auth/KeyError | Load the API key in the **server** from env; the agent never holds it. |
| Agent passes bad args | Tighten arg types + improve the tool description. |
| Server crashes on bad input | Validate args server-side; return a structured error. |
| Weird agent behavior after a search | Treat tool output as untrusted data; don't let it steer the agent. |

---

## Submit
1. `mcp_server.py` — ≥2 robust tools with schemas, validation, timeouts.
2. `mcp_client_agent.py` — a LangGraph agent calling the tools via MCP.
3. **Tool schema doc** (the table from Step 9).

Compare with `solution-code/` **after** yours works.

---

<div align="center" style="padding:14px; border-top:2px solid #0078d4; margin-top:34px;">
  <p style="margin:0 0 8px 0;">
    <img src="../assets/jhf-logo.png" alt="JHF" height="28" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
    <img src="../assets/comcec-logo.png" alt="COMCEC" height="40" style="vertical-align:middle; margin:0 14px; background:#ffffff; padding:6px 10px; border-radius:6px;" />
  </p>
  <p style="color:#888; font-size:13px; margin:0;">
    <strong>JHF Agentic AI Bootcamp</strong> &mdash; Module 5 Lab<br/>
    Lead Trainer: <a href="https://www.linkedin.com/in/alaaldin-ahmed-260266150">Alaaldin Ahmed</a><br/>
    Organized by Jerusalem High-Tech Foundry (JHF) &middot; In partnership with COMCEC
  </p>
</div>

