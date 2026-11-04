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

## Google sign-in setup

Sign-in is Firebase Authentication with the Google provider. The browser opens Google's popup and hands the
Firebase ID token to the backend once; the backend verifies it (signature, issuer, audience, expiry) and opens
its own HttpOnly session cookie. Nothing else is trusted.

**Where the browser gets the Firebase web config** (`src/auth/firebaseClient.js`):
1. from the backend, `GET /api/auth/config`, built from the backend's environment (preferred: one place, and the
   project always matches the one the backend verifies against);
2. else from this build's `.env.local` (`VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`,
   `VITE_FIREBASE_PROJECT_ID`, `VITE_FIREBASE_APP_ID`; see `.env.local.example`).

In development the backend also reads those public values from `rihla-frontend/.env.local`, so they only need to
be in one place. In production (`RIHLA_ENV=production`) it does not: set them in the backend's environment.

If neither is complete, the login window names the missing settings (names only, never values).

**Backend `.env`** (see `../.env.example`): `RIHLA_FIREBASE_PROJECT_ID`, `RIHLA_SESSION_SECRET` (32+ random
characters, never shared or committed), `RIHLA_FIREBASE_WEB_API_KEY`, `RIHLA_FIREBASE_APP_ID`,
`RIHLA_ALLOWED_ORIGINS` (the URL you open Rihla at, e.g. `http://localhost:5173`), and `RIHLA_AUTH_REQUIRED=true`
for the web app. Restart the backend after a change.

**Firebase console, once per project** (the project owner):
1. Authentication > Sign-in method > Google > Enable, choose a support email, Save.
2. Authentication > Settings > Authorized domains: `localhost` is there by default. Add every other host people
   open Rihla at (a deployed domain). `127.0.0.1` and LAN addresses are separate hosts: open `http://localhost:<port>`.
3. Project settings > General > Your apps: add a Web app if there is none, and copy its `apiKey` and `appId`
   into the backend `.env` as above.

**A teammate setting up their own copy** (the same Firebase project; nothing is copied from anyone's `.env`):

```powershell
copy .env.example .env                                    # in kit/
copy rihla-frontend\.env.local.example rihla-frontend\.env.local
python -c "import secrets; print(secrets.token_urlsafe(48))"   # your own RIHLA_SESSION_SECRET, never shared
python -m app.auth.check                                  # says what is still missing (names only, never values)
```

Fill in `RIHLA_FIREBASE_PROJECT_ID`, your `RIHLA_SESSION_SECRET` and `RIHLA_AUTH_REQUIRED=true` in `.env`, and the
four `VITE_FIREBASE_*` values in `.env.local`. Those four are public, so the project owner can share them. Any Google
account can then sign in; each gets its own private traveller and trips.

## What the page promises

- A trip is shown only when the backend returned a complete itinerary (`services/tripResult.js`); a failed or
  timed-out request (5 minutes) never shows as a trip.
- "Verified" and the Approve button appear only for a server `save_trip` pending action with
  `verification_status: "verified"`. A plan that passed only against recorded data is labelled as such.
- Approval is shown as done only when the server returns a trip ID (`services/approval.js`).
- An unknown price is "Price unknown", never "Free"; a price range is shown as a range.
- Travel times between stops are route estimates (OpenStreetMap walking or driving), labelled as such.
- My Trips lists the signed-in user's trips from the Rihla Trip Store: approved ("Approved · Verified"), accepted
  with limitations ("Saved · Not Fully Verified") or saved as an unverified draft ("Saved · Unverified Draft").
  With `TRAVEL_DB_PATH` set on the Trip Store they survive restarts (`../docs/trip_store_persistence.md`).
  Drafts never sent to the server stay in this browser only. Sample trips are labelled and cannot be approved.

## Checks

```powershell
npm test             # node --test, no browser and no backend needed
npm run lint
npm run build
```
