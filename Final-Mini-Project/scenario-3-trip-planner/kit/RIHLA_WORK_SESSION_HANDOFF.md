# Rihla: work session handoff

| | |
|---|---|
| **Session date** | 2026-10-30 |
| **Branch** | `feature/global-data-frontend` |
| **Latest work commit** | `7a30d3149b89c6517e1683e9c45e00e5b47ab342` (MCP, contract, memory). This handoff file is the commit right after it. |
| **Earlier today** | `1d1be11` (frontend and research fixes, audit, team handoff) · `c763202` (merge of planning and backend work) |
| **`main`** | Does **not** have this branch's last six commits yet (`50e5dab`, `9b71b6b`, `c763202`, `1d1be11`, `7a30d31` and this one). It needs a PR from this branch. |

Details live in three other files in `kit/`:
- [`RIHLA_PROJECT_AUDIT.md`](RIHLA_PROJECT_AUDIT.md): the full audit as of this morning.
- [`RIHLA_TEAM_HANDOFF.md`](RIHLA_TEAM_HANDOFF.md): backend and planning tasks, with current status at the top.
- [`RIHLA_MCP_IMPLEMENTATION.md`](RIHLA_MCP_IMPLEMENTATION.md): MCP design, evidence and compliance table.

## 1. Implemented today

**Frontend (`rihla-frontend/`, commit `1d1be11`)**
- Trip length limited to 21 days (matches the backend); a double submit is blocked.
- Dashboard and PDF: walking/driving times between stops (labelled estimates, long walks flagged), the verifier's findings, and budget confidence / price ranges / suggestions. A plan checked only against recorded data is labelled and never shown as verified.
- The approval client understands both the old and the contract response; approval is shown only with a server `trip_id`.
- Revision chat wired to `/api/chat` but **off** (`VITE_ENABLE_REVISION_CHAT`) until the backend supports revision.
- README for the frontend.

**Research (`researcher/`, `1d1be11` and `7a30d31`)**
- Planted-instruction detection on every listing text field (0 false positives on 365 cached listings).
- One malformed listing no longer fails the whole research step.
- The MCP client records every call per request (`recording()`, `stage()`, `_dispatch`).

**Backend MCP and contract (`app/`, commit `7a30d31`)**
- `tool_calls` lists every real MCP call (plus each agent's own tool calls), per request.
- Approved trips are saved with the **MCP `save_trip` tool** (idempotency key = `action_id`); the direct REST save is gone.
- Traveller memory over MCP: `get_preferences` once per session, `save_preferences` only on "remember…".
- Contract fields: `reply`, `state`, `result`, `citations`, `tool_calls`, `usage`, `pending_action.summary/payload`.
- `/api/approve` returns a ChatResponse; repeated approvals don't write again; failed saves are reported honestly.

## 2. Tested successfully (offline, no paid calls)

| Check | Result |
|---|---|
| Backend `pytest tests` | **900 passed, 10 skipped** (the paid live tests), 0 failed |
| Real MCP over stdio (`tests/test_mcp_integration.py`) | 12 passed |
| MCP contract, memory and approval (`tests/test_mcp_contract.py`) | 45 passed |
| Frontend `npm test` / `npm run lint` / `npm run build` | 91 passed / clean / OK |
| Official eval runner over HTTP, with only the LLM pipeline replaced by a stub | S3-02, S3-03, S3-08, S3-10 and a double-approval case: 5/5 |

**Not tested:** any run with the real LLM (OpenRouter), live travel data mode, Docker, a browser click-through.

## 3. Incomplete

1. **Replay-mode approval policy** (team decision): the real pipeline offers `save_trip` only with live evidence, so in replay mode (the kit default, likely the hidden-test setup) no plan is approvable. Proposal: `RIHLA_TEAM_HANDOFF.md` §3 P1.
2. **User revision** ("cut the budget by 20%"): not connected. Needs `RIHLA_TEAM_HANDOFF.md` B4 (backend) and P2 (planning).
3. **`/api/chat` clears the pending approval on every message** (B3), even a "remember…" message.
4. **The attractions source failing** still runs the paid planning stages (B5).
5. **No app Dockerfile** or `app:` service in `docker-compose.yml` (B9); `.env.example` lacks some variables (B8).
6. **Bootcamp deliverables:** `DESIGN.md`, portfolio README, own eval cases (≥10 total) with a scorecard and one fixed failure, architecture diagram, 3-minute video, `v1.0` tag.

## 4. Known bugs and risks

- Sample eval cases that need a real plan (S3-01, S3-04 to S3-07, S3-09) cannot pass until items 1 and 2 are done.
- Dietary preferences are reported in the reply but don't filter restaurants (`planning/` has no diet input).
- The Travel API you ran locally today was in `TRAVEL_MODE=record`, which writes new cache files (22 untracked under `services/cache/`, not committed).
- `rihla-frontend/src/pages/TripDashboard.jsx.bak` is an untracked backup, not committed.

## 5. What teammates can safely continue

| Owner | Task | Files |
|---|---|---|
| Planning / Budget / Verification | Replay approval policy (after the team agrees), the revision entry point (P2), price conflict text for the reply (P3) | `planning/verification.py`, `planning/revision.py`, `planning/revision_agent.py`, `planning/budget_analyst.py` |
| Backend / Coordinator | B3 (keep pending approval), B4 (wire revision, after P2), B5 (stop early on attractions failure), B8, B9 | `app/main.py`, `app/orchestrator.py`, `app/revision.py`, `Dockerfile`, `docker-compose.yml`, `.env.example` |
| Anyone | Own eval cases in `evals/`, DESIGN.md, README | `evals/`, docs |

## 6. Needs coordination (changed today, shared)

- `app/main.py`, `app/schemas.py`, `app/trip_store.py`, `app/orchestrator.py`, `app/planning_adapter.py`, `app/verification_adapter.py`: rewritten or extended today. **Pull this branch before editing them.**
- `researcher/mcp_client.py`: every MCP call now goes through `_dispatch`. New tool paths should use `call_read_tool` (agents) or `app/mcp_app_client.call_app_tool` (app only).
- `scripts/try_revision.py`: a 2-line fix to `only_hosts()` restoring `socket.create_connection` (tell its owner).
- If `pending_action.verification_status` gets a new value for recorded-data approval, `rihla-frontend/src/services/approval.js` and `tripResult.js` need a one-line change each.

## 7. Next steps for tomorrow (in order)

1. `git fetch origin` and check whether teammates pushed. Pull only after reviewing.
2. Agree on the replay-mode approval policy with the team (15 minutes), and assign P1, P2 + B4, B3.
3. Open a PR `feature/global-data-frontend → main` so `main` gets today's work.
4. When a few OpenRouter credits are acceptable: one live end-to-end trip (backend + Travel API + frontend), then `python evals/run_evals.py` in replay mode, and save the scorecard.
5. Start the deliverables: own eval cases, DESIGN.md, README with scorecard, architecture diagram.

## 8. Resuming: environment and services

From `Final-Mini-Project/scenario-3-trip-planner/kit/`. Values come from your own `.env` (not committed; see `.env.example`):

```powershell
.venv\Scripts\activate
# 1. The kit's travel API (replay = offline, no cache writes)
$env:TRAVEL_MODE = "replay"; uvicorn services.travel_api:app --port 8103
# 2. The Rihla backend (needs OPENROUTER_API_KEY in .env for real planning)
uvicorn app.main:app --port 8000
# 3. The frontend (VITE_ENABLE_LIVE_AI=true in rihla-frontend/.env.local to plan real trips)
cd rihla-frontend; npm run dev
```

Offline checks (no paid calls):

```powershell
$env:OPENROUTER_API_KEY = ""; python -m pytest -q tests        # 900 passed, 10 skipped expected
cd rihla-frontend; npm test; npm run lint; npm run build
```
