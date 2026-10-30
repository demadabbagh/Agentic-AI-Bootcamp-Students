# Rihla frontend

The traveller's web page for the Rihla trip planner: the trip form, the itinerary dashboard (verification,
budget, approval, PDF) and My Trips. It talks to the Rihla backend (`app/main.py`) through `/api/chat` and
`/api/approve`; Vite proxies `/api` to `http://127.0.0.1:8000` (`RIHLA_BACKEND_URL` overrides it).

## Run

```powershell
npm install
npm run dev          # http://localhost:5173, with the backend running on :8000
```

Start the travel API (`uvicorn services.travel_api:app --port 8103`) and the backend
(`uvicorn app.main:app --port 8000`) first; see `../README.md`.

## Settings (`.env.local`, not committed)

| Variable | Default | Effect |
|---|---|---|
| `VITE_ENABLE_LIVE_AI` | off | `true` lets the form call `/api/chat`. Off, the form explains that live planning is turned off and offers the sample trip, so no OpenRouter credits are spent by accident. |
| `VITE_ENABLE_REVISION_CHAT` | off | `true` sends dashboard chat messages to `/api/chat` as changes to the current trip. Leave it off until the backend's revision step is connected: today the backend answers `revision_not_supported` and clears the pending approval on every chat call. |
| `RIHLA_BACKEND_URL` | `http://127.0.0.1:8000` | Where the dev server proxies `/api` (read by `vite.config.js`, not by the page). |

## What the page promises

- A trip is shown only when the backend returned a complete itinerary (`services/tripResult.js`); a failed or
  timed-out request (5 minutes) never shows as a trip.
- "Verified" and the Approve button appear only for a server `save_trip` pending action with
  `verification_status: "verified"`. A plan that passed only against recorded data is labelled as such.
- Approval is shown as done only when the server returns a trip ID (`services/approval.js`).
- An unknown price is "Price unknown", never "Free"; a price range is shown as a range.
- Travel times between stops are route estimates (OpenStreetMap walking or driving), labelled as such.
- My Trips is stored in this browser only (localStorage). Sample trips are labelled and cannot be approved.

## Checks

```powershell
npm test             # node --test, no browser and no backend needed
npm run lint
npm run build
```
