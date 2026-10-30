# Rihla: team handoff (backend and planning work)

**Date:** 2026-10-30 · **Branch:** `feature/global-data-frontend` (uncommitted work on top of `c763202`)
**Read with:** [`RIHLA_PROJECT_AUDIT.md`](RIHLA_PROJECT_AUDIT.md) (the full audit) and `../../common/CONTRACT.md` (the API contract the hidden tests use).

This file lists the problems that need changes in **`app/**`** (Backend / Coordinator) or **`planning/**`** (Planning / Budget / Verification). Those folders were not touched in this round. Every claim below was checked in the current code; file and line numbers refer to `kit/`.

The frontend and research changes already made, and what they expect from you, are in [section 4](#4-what-the-frontend-and-research-side-already-did).

---

## 1. Priorities at a glance

| # | P | Owner | Task | Blocks |
|---|---|---|---|---|
| B1 | **P0** | Backend | `/api/chat` speaks the contract: `reply`, `citations`, `tool_calls`, `usage`, `result`, `pending_action.summary` | Every sample eval case; hidden tests |
| B2 | **P0** | Backend | `/api/approve` returns a ChatResponse with `result.trip_id` and `reply` | S3-02, S3-04, S3-05, S3-09 |
| P1 | **P0** | Planning + Backend | Approval reachable in replay mode, honestly labelled (policy in §3) | S3-01, 02, 04, 05, 08, 09; demo scenario 1 |
| B3 | **P0** | Backend | Do not clear the pending approval before a new plan replaces it | Approve after any chat message |
| P2 + B4 | **P0** | Planning + Backend | User revision ("cut the budget by 20%", "make day 2 cheaper") | S3-04; demo scenario 2 |
| B5 | P1 | Backend | Stop before the LLM stages when `find_attractions` failed | S3-07; wasted OpenRouter calls |
| B6 | P1 | Backend | Traveller identity from `user.traveller_id`; persistent preferences | S3-02 (TRV-200), S3-08 |
| B7 | P1 | Backend | Repeat approval of the same `action_id` answers with the same trip | Hidden "double click" test |
| B8 | P1 | Backend | `trip_store.py` default URL and `.env.example` | Fresh-clone start |
| B9 | P1 | Backend | App Dockerfile + `app:` service in `docker-compose.yml` | DoD #6 |

Rough effort: B1 + B2 about one day; P1 half a day; B3 one hour; P2 + B4 one to two days; B5–B8 half a day together; B9 two to three hours.

---

## 2. Backend / Coordinator (`app/**`)

### B1 · P0 · `/api/chat` does not return the contract fields

- **Files:** `app/schemas.py:37-52` (ChatResponse), `app/main.py:155-168`, `app/orchestrator.py`.
- **Current:** the response has `status, itinerary, pending_action, message, coverage, plan_quality, performance, summary, verification, budget`. It has **no `reply`, `citations`, `tool_calls`, `usage` or `result`**. The eval runner reads exactly those (`evals/run_evals.py:192-260`): `reply_contains_*`, `citations_min`, `tools_used_include` and `result_absent` all fail today, whatever the plan.
- **Expected (`common/CONTRACT.md` §2):**

```jsonc
{
  "session_id": "s-1",
  "reply": "Here's a 3-day plan … Day 1 · 09:00 Hagia Sophia (est. $28, estimate) → 4-minute walk → … Shall I save it?",
  "state": "await_confirmation",                      // optional
  "pending_action": {                                   // only when approvable
    "action_id": "…", "type": "save_trip",
    "summary": "Save 'Istanbul, 3 days' (2026-11-02 to 2026-11-04, about 418 USD) for TRV-200",
    "payload": {"traveller_id": "TRV-200", "title": "Istanbul, 3 days", "itinerary": {…}}
  },
  "result": null,
  "citations": [{"id": "…", "title": "…", "url": "https://en.wikivoyage.org/…"}],
  "tool_calls": [{"tool": "find_attractions", "ok": true, "ms": 132}],
  "usage": {"prompt_tokens": 1830, "completion_tokens": 212, "cost_usd": 0.0011}
}
```

- **Recommended fix (additive, so the React app keeps working):**
  1. Add `reply`, `citations`, `tool_calls`, `usage`, `result`, `state` to `ChatResponse`. Keep every existing field.
  2. Build `reply` **in Python** from the final itinerary: "Day 1", "Day 2"… with times, place names, each cost marked "estimate", unknown prices said out loud, walking minutes, and closed-day moves taken from the verification findings. For non-itinerary statuses, `reply = message`. S3-01 checks `"day 1"`, `"day 2"`, `"day 3"` and one of `"estimate" | "approx" | "about"`. S3-06 needs both Galata Tower prices (`650` and `1300`) in the reply.
  3. `citations`: the itinerary's `source_url`s plus `budget_report.citations` (FX), de-duplicated by URL. Every read result already carries a `citation` object (kit README).
  4. `tool_calls`: **the research step already provides them.** `ResearchResult.calls` is `[{tool, ok, ms, error?}]` (new, see §4). `app/research_adapter.py:run_research_pipeline` currently returns only `result.bundle`, so return the calls too. Add the opening-hours and travel-time lookups from `app/mcp_lookup_adapter.call_lookup` (time each call), plus one entry per agent stage if wanted.
  5. `usage`: from `performance.llm_total` and `performance.estimated_cost_usd` (`app/orchestrator.py:155-185`). Note that the Coordinator's tokens are not counted there yet.
  6. `pending_action.summary` and `payload.traveller_id` / `payload.title`: the summary is what a human approves.
- **Tests:** extend `tests/test_api.py`. Then run `python evals/run_evals.py --only S3-10 -v` and `--only S3-07 -v` (cheap: little or no planning).
- **Verify:** `S3-10` passes; `tool_calls` lists `find_attractions` for any planned trip.

### B2 · P0 · `/api/approve` answer shape

- **Files:** `app/main.py:171-269`.
- **Current:** returns `{status: "approved", message, trip}` or `{status: "rejected", message}`. Errors are HTTP 404/400/403/503 with `detail`.
- **Expected:** a ChatResponse. On success: `result: {"trip_id": "TRIP-…"}` copied from the Trip Store, and a `reply` containing the ID ("Saved as TRIP-3001. Nothing was booked or paid; prices are estimates."). On rejection: `result: null` and a reply that says nothing was saved. On an unknown or already-used `action_id`: a clear reply, `result: null`, nothing written (CONTRACT §3). On a store failure (409/503): an honest reply, `result: null`, and the pending action kept for a retry (the code already keeps it).
- **Frontend status:** `rihla-frontend/src/services/approval.js` **already accepts both shapes**. It reports approval only when the server returns a `trip_id`, so you can switch without breaking the UI. Keep a `status: "approved" | "rejected"` field if you like; it is harmless.
- **Tests:** `tests/test_api.py`; eval cases S3-02 and S3-03.
- **Verify:** S3-02 and S3-03 pass; `GET {TRAVEL_API}/admin/trips` has one trip after approve and none after reject.

### B3 · P0 · Every chat message destroys the open approval

- **File:** `app/main.py:71-73`: `session["pending_action"] = None` runs for **every** `/api/chat` call, before knowing whether the call produced a new plan.
- **Current:** a traveller with an approvable plan who asks a question, or whose revision fails (today every revision returns `revision_not_supported`), loses the approval. A later Approve gets 404.
- **Expected:** the pending action is replaced only when this call returns a new approvable plan, and cleared only when it returns a new (unapprovable) plan for the same session. Any reply without an itinerary keeps the previous pending action.
- **Fix:** move the reset into the branches that set a new `itinerary`.
- **Frontend status:** until this is fixed the dashboard assumes the worst. After any chat message it hides the Approve button and says the approval is no longer open (`interpretRevision().approvalLost`).
- **Test:** chat → pending; chat "what's the weather?" → approve with the first `action_id` → 200 and saved.

### B4 · P0 · User revision is not connected (needs P2)

- **Files:** `app/orchestrator.py:257-312`, `app/revision.py`.
- **Current:** `revise_itinerary(...)` is called without a `revision_agent` (`orchestrator.py:265`), so it always raises `RevisionNotAvailableError` and the answer is `revision_not_supported`. Even when it is connected, the branch returns `verification_required` and never runs the verifier, so a revised plan could never be approved.
- **Expected:** "Cut the budget by 20%" changes the session's plan: new `budget_total` (Python arithmetic), items dropped or replaced to fit, untouched days identical. The new plan is re-verified and offered as a new `pending_action`. S3-04 checks the saved trip has `budget_total: 560` and `within_budget: true`.
- **Fix:**
  1. Keep the session's `ResearchBundle`, `BudgetReport` and itinerary model (not only the store dict) after planning. Today only `coverage_context` is kept.
  2. Classify the request into a structured change (`budget_scale`, `day_cheaper(date)`, `exclude_kind(date, kind)`, `remove_place`). Do this with the Coordinator's structured output, then validate in Python.
  3. Call the planning entry point from P2, then `run_verification_pipeline(...)` as the main path does, then the same trusted-verification gate in `main.py`.
- **Frontend status:** the dashboard chat **is wired** to `POST /api/chat {session_id, message}` (no `trip` form, so it is read as a change) and handles every status. It is switched off by default because today it would only spend a Coordinator call and destroy the approval (B3). Turn it on with `VITE_ENABLE_REVISION_CHAT=true` in `rihla-frontend/.env.local` once B3 and B4 work.
- **Verify:** `python evals/run_evals.py --only S3-04 -v`. In the UI: chat "cut the budget by 20%" → lower total, other days unchanged, Approve offered again.

### B5 · P1 · A failed attractions source still runs the paid planning stages

- **Files:** `app/orchestrator.py:383-420`, `app/research_adapter.py`.
- **Current:** research records the failure in `bundle.failures` and carries on. Nothing in `app/` reads `bundle.failures`. The Budget Analyst and Planner LLMs then run on restaurants only, and the traveller gets a coverage message instead of "the attractions source is unavailable".
- **Expected (S3-07):** an honest reply ("could not", "unavailable"…), no `pending_action`, no invented sights, and ideally no LLM call.
- **Fix:** after research, if `find_attractions` failed and no attraction was found, return `research_unavailable` with a message naming the failed source. `ResearchResult.calls` (§4) shows which call failed and its error code.
- **Verify:** with the fault switched on (`POST /admin/faults {"target": "attractions", "mode": "error_503", "count": 5}`, from the test, never from the app), run S3-07. `performance.llm_total.successful_requests` should be 0.

### B6 · P1 · Traveller identity and persistent memory

- **Files:** `app/schemas.py:33-36` (`ChatRequest` has no `user`; extra fields are silently ignored), `app/main.py:239` (traveller from the `TRAVEL_TRAVELLER_ID` env variable).
- **Current:** every trip is saved for one configured traveller. `get_preferences` / `save_preferences` are never called (DoD #3: persistent memory is missing).
- **Expected:** use `user.traveller_id` from the request (the eval cases send `TRV-…`). Read preferences on the first message of a session and use them (diet, interests, walking limit, currency). Save a lasting fact the traveller states ("remember I'm vegetarian") from code, not as an LLM tool. S3-08 checks `tools_used_include: ["get_preferences"]` and "vegetarian" in the reply.
- **Note:** `researcher/mcp_client.READ_ONLY_TOOLS` deliberately blocks `save_preferences`. Write a separate, app-only call for it, as `trip_store.py` does for `save_trip`. Do not add it to the agents' allowlist.

### B7 · P1 · Approving the same action twice

- **File:** `app/main.py:181-185`.
- **Current:** after a successful save the pending action is cleared, so a second identical approve returns 404.
- **Expected (CONTRACT §3, the runner's `approve_again` turn):** the same `action_id` again → the same `trip_id`, nothing new written. Keep `{action_id: trip_id}` in the session after saving. The Trip Store's idempotency key would also return the same trip if called again.

### B8 · P1 · Configuration defaults

- `app/trip_store.py:52-55`: the default `TRAVEL_API_URL` is `http://127.0.0.1:8001`; the kit's API is on **8103**.
- `.env.example` lacks `TRAVEL_TRAVELLER_ID` (until B6), `OPENROUTER_MODEL` (the code reads `OPENROUTER_MODEL`, but `.env.example` names it `MODEL`), `RIHLA_LLM_TIMEOUT_SECONDS`, `RIHLA_LLM_MAX_RETRIES`, `RIHLA_MAX_WALK_MINUTES`.

### B9 · P1 · One-command start

- `docker-compose.yml:20-30`: the `app:` service is commented out and there is no root `Dockerfile` for the FastAPI app. DoD #6 requires `docker compose up`.

---

## 3. Planning / Budget / Verification (`planning/**`)

### P1 · P0 · No plan can be approved in replay mode: evidence policy

- **Files:** `planning/verification.py:16-18, 190-194, 301, 1016`; the gate is `app/main.py:97-106`.
- **Current:** `verified` is true only when every opening-hours and travel fact is **fresh** (a lookup that says `mode: "live"`). `/api/chat` creates a `pending_action` only when `verified` is true. In replay mode (the kit default, and very likely the hidden-test setup) nothing is fresh, so **no plan is ever approvable**. The only recorded real run (`rihla-frontend/src/data/recordedAmmanResult.js`) also ended `verification_required`.
- **Do not** solve this by calling recorded data "verified". The rule that `verified` means live evidence is right, and the frontend now relies on it.
- **Proposed policy (team decision needed):**

| Evidence | Verifier status | `verified` | `verification.state` | Approvable? | Traveller sees |
|---|---|---|---|---|---|
| Every hours/route fact from a live lookup, all checks pass | PASS | **true** | `confirmed_valid` | Yes | "Verified · Awaiting your approval" |
| All checks pass, some or all facts from the recorded cache or the research listing | PASS | **false** (unchanged) | `supported_by_records` | **Yes, with the basis stated** in `pending_action.summary`, the reply and the dashboard | "Checked against recorded data from \<date\>, not live sources" |
| Any confirmed problem left after repair | FAIL | false | `confirmed_problem` | No | the problems |
| A required fact is missing (hours unknown for a scheduled place, a route not found) | UNKNOWN | false | `unverifiable` | No, or yes only for non-critical unknowns if the team decides so, each listed | what could not be checked |

- **Implementation sketch:**
  1. Keep `VerificationReport.verified` exactly as is.
  2. Add an explicit approvability rule in `app/main.py`: `status == "PASS" and coverage_ok and evidence_basis in {"fresh", "recorded", "mixed"}`. Put `verification_basis: "live" | "recorded"` on the pending action, and refuse in `/api/approve` anything that was not PASS (the hash check stays).
  3. Mention the basis in the reply and the `summary` the human approves.
- **Frontend status:** already labels `supported_by_records` as "Not verified · Checked against recorded data only" and never shows it as verified. It offers Approve **only** when the server sends a `save_trip` pending action with `verification_status: "verified"`. If you approve recorded-data plans, either send `verification_status: "verified_on_records"` and tell the frontend owner (`approval.js:approvalOptions` and `tripResult.js:getVerificationState` need one line each), or keep `"verified"` and rely on `verification.state` for the label (the dashboard already shows it).
- **Tests:** `tests/planning/test_verification.py` (a PASS on recorded evidence stays `verified=False`); `tests/test_api.py` (PASS + recorded → pending action with basis "recorded"; FAIL → none).
- **Verify:** replay mode, S3-01 → `pending_action: save_trip`; S3-05 → Topkapı saved, not on Tuesday.

### P2 · P0 · A revision entry point for traveller requests (with B4)

- **Files:** `planning/revision.py`, `planning/revision_agent.py`.
- **Current:** targeted revision repairs **verifier findings** only (`VerificationReport.revision_targets` → steps). There is no entry for "make day 2 cheaper", "cut the budget by 20%" or "no museums on day 3".
- **Expected:** a function such as `revise_for_request(itinerary, bundle, budget_report, change, *, travel_lookup, hours_lookup)`, where `change` is a validated structure (`budget_scale(0.8)`, `cheaper_day(date)`, `exclude(date, kind)`, `remove(place_id)`). It reuses the existing primitives (re-select within a new cap, reschedule touched dates, copy untouched dates verbatim) and returns a `RevisionResult` that is then re-verified.
- **Tests:** untouched dates are byte-identical; new `estimated_total ≤ new budget_total`; a cut that cannot be met is reported as infeasible with the closest plan (hidden test "impossible budget"); no LLM is needed for the arithmetic.

### P3 · P1 · Price conflict must reach the reply

The Budget Analyst already writes "Galata Tower: sources disagree, 13.07-26.14 USD…" (`planning/budget_analyst.py:882-887`). S3-06 checks that the **reply** contains `650` and `1300` (the lira prices). Expose the original-currency texts for a range item, so B1's reply builder can say "650–1300 TL (sources disagree)".

---

## 4. What the frontend and research side already did

All changes are uncommitted, in `rihla-frontend/**`, `researcher/**` and `tests/research/**` only.

| Change | Where | What it gives you |
|---|---|---|
| `ResearchResult.calls`: `[{tool, ok, ms, error?}]` per MCP call | `researcher/validate.py`, `researcher/tools.py` | Ready-made `tool_calls` for B1 (return it from `run_research_pipeline`) |
| One malformed listing no longer fails the whole research step (bad coordinates, non-object price/hours/citation) | `researcher/validate.py` | Fewer `research_unavailable` failures in live mode |
| Planted instructions caught in any listing text (description, name, price, hours), not only in synthetic overlays; 0 false positives on all 365 cached listings | `researcher/validate.py` | Hidden test "a planted instruction" |
| Approval client accepts the contract shape and today's shape | `rihla-frontend/src/services/approval.js` | B2 can change the shape without breaking the UI |
| Revision chat wired to `/api/chat`, off by default | `rihla-frontend/src/pages/TripDashboard.jsx`, `services/tripApi.js:reviseTrip`, `services/tripResult.js:interpretRevision` | Flip `VITE_ENABLE_REVISION_CHAT=true` after B3 + B4 |
| Dashboard shows `verification` findings, `budget` confidence / price ranges / suggestions, and the walking/driving minutes from `summary.days` | `TripDashboard.jsx`, `itineraryAdapter.js`, `tripResult.js`, PDF | Keep sending `summary.days[].activities[].travel_minutes / travel_mode / kind`, `verification.findings`, `budget.warnings` |

**Please keep these response fields stable** (the frontend reads them): `status`, `itinerary` (Trip Store shape), `pending_action.{action_id,type,verification_status}`, `message`, `coverage`, `plan_quality`, `summary.{limitations,interest_coverage,days}`, `verification.{state,findings}`, `budget.{status,confidence,confidence_basis,warnings,suggestions}`.

---

## 5. Dependencies between teammates

```
P1 (evidence policy, planning)  ──┐
B1 (contract fields)  ────────────┼──> run sample evals in replay ──> scorecard (DoD #5)
B2 (approve shape)  ──────────────┘
B3 (keep pending approval) ──┐
P2 (revision entry, planning)┼──> B4 (wire revision) ──> frontend: VITE_ENABLE_REVISION_CHAT=true ──> demo scenario 2
B5, B6, B7, B8, B9: independent
```

- B1 and P1 can start in parallel. P1 is a team decision first (15 minutes), then about 3–4 hours of work.
- B4 cannot finish before P2 and B3.
- The frontend needs no change for B1, B2, B3, B5–B9. For B4 it only needs the flag. For P1 it needs one line only if the `verification_status` value changes.

## 6. How to verify everything together

1. `python -m pytest -q tests` (offline; set `OPENROUTER_API_KEY=` to be sure no paid call happens). Today: **843 passed, 10 skipped**.
2. `cd rihla-frontend && npm test && npm run lint && npm run build`. Today: **91 passed**, lint clean, build OK.
3. Replay mode, fresh start (`POST /admin/reset` from the runner only): `python evals/run_evals.py`. These cases cost OpenRouter credits (about 10 plans; budget accordingly). Paste `evals/scorecard.md` into the README.
4. UI walk-through: plan → Approve → `TRIP-` ID shown; plan → Reject → nothing in `/admin/trips`; chat "cut the budget by 20%" (after B4) → new total, Approve again.
