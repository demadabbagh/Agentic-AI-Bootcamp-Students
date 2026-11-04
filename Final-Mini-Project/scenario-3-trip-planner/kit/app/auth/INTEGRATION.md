# Rihla sign-in — setup and integration

Google sign-in through **Firebase Authentication**, verified by this backend, kept as an **HttpOnly session
cookie**. Owner: Developer 2 (auth & frontend). Everything lives in `app/auth/`; `app/main.py` needs one small
change from its owner (below).

## How it works

```
browser                         Firebase / Google              Rihla backend (FastAPI)
Log In ──signInWithPopup──────▶ Google account chooser
       ◀──── Firebase ID token (1 h, RS256)
POST /api/auth/session {id_token} ───────────────────────────▶ verify: Google's key, aud = project, iss,
                                                                exp, sub, recent sign-in, provider google.com
       ◀──────────────────── Set-Cookie: rihla_session (HttpOnly, SameSite=Lax, Secure off localhost)
POST /api/chat, /api/approve, GET /api/trips (cookie) ───────▶ AuthMiddleware: user = the cookie's account
```

* **No service account.** ID tokens are checked with Google's public `securetoken` certificates, following
  Firebase's procedure for third-party JWT libraries (PyJWT + cryptography, already installed with CrewAI/MCP).
* **The browser never holds a credential**: Firebase uses in-memory persistence and is signed out right after
  the token is handed over; JavaScript cannot read the session cookie.
* **Stable user ID** = the Firebase `sub`. The Trip Store traveller is derived from it on the server
  (`RIHLA-` + 24 hex), never accepted from the browser.
* **Logout** revokes the session on the server (an old cookie stops working), then clears the cookie.
* **CSRF**: SameSite=Lax, plus every sign-in/logout and every cookie-carrying write must come from
  `RIHLA_ALLOWED_ORIGINS`. No CORS is opened (the web app reaches the API through the Vite proxy, same origin).
* **Isolation**: a planning `session_id` belongs to whoever used it first; another account (or an anonymous
  caller) gets `403 session_not_yours` on `/api/chat` and `/api/approve`.

## The official contract and the hidden tests

The hidden tests and `evals/run_evals.py` call `/api/chat` and `/api/approve` **without signing in**. So:

| `RIHLA_AUTH_REQUIRED` | Anonymous `/api/chat`, `/api/approve`, `/api/trips` | Use it for |
|---|---|---|
| `false` (default) | allowed, on the shared demo traveller `TRAVEL_TRAVELLER_ID` | the eval runner, the hidden tests (`v1.0` README run command) |
| `true` | `401 auth_required` | the web app demo, any real deployment |

A signed-in user gets their own traveller in both modes, and can never see the demo traveller's trips (or
another user's). **The team decides which mode the `v1.0` README starts.**

## Settings (repo-root `.env`, never committed)

| Variable | Example | Notes |
|---|---|---|
| `RIHLA_FIREBASE_PROJECT_ID` | `rihla-trip-planner` | the Firebase project (same as `VITE_FIREBASE_PROJECT_ID`) |
| `RIHLA_SESSION_SECRET` | 64 random characters | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `RIHLA_AUTH_REQUIRED` | `true` / `false` | see the table above |
| `RIHLA_SESSION_TTL_HOURS` | `12` | session length |
| `RIHLA_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | the default; add the deployed web origin |
| `RIHLA_COOKIE_SECURE` | `auto` | `auto`: Secure except for a local http origin; set `true` in production |

Frontend: `rihla-frontend/.env.local` from `rihla-frontend/.env.local.example` (`VITE_FIREBASE_*`). Check a
machine's setup with `python -m app.auth.check` (from `kit/`; prints names only, never values).

### Firebase console (once)

1. Create a Firebase project for Rihla (do not reuse SHE-NA's project or its users).
2. Authentication → Sign-in method → enable **Google**.
3. Authentication → Settings → Authorized domains: `localhost` is there by default; add `127.0.0.1` only if
   you open the app that way, and the deployed domain later.
4. Project settings → Your apps → add a **Web app**; copy apiKey, authDomain, projectId, appId into
   `rihla-frontend/.env.local`, and projectId into `RIHLA_FIREBASE_PROJECT_ID`.

No service-account key is needed. Never commit one.

## Running it now (before the `app/main.py` change)

```powershell
# from kit/, with .venv312
..\..\..\.venv312\Scripts\python.exe -m uvicorn app.auth_app:app --reload --port 8000
```

`app.auth_app` is `app.main`'s app with `install_auth` applied. Sign-in, `/api/auth/*`, `401`s,
CSRF and session ownership all work. **Per-user trips do not yet**: `app/main.py` still saves and lists trips
for `TRAVEL_TRAVELLER_ID`, so every signed-in user shares that traveller until the change below.

## The change `app/main.py` needs (Developer 1)

`main_integration.patch` in this folder (`git apply Final-Mini-Project/scenario-3-trip-planner/kit/app/auth/main_integration.patch`
from the repo root). Three small hunks:

1. `from app.auth import install_auth, traveller_id_for_request` and `install_auth(app)` right after
   `app = FastAPI(...)`.
2. In `approve()`: `traveller_id = traveller_id_for_request()` instead of `os.getenv("TRAVEL_TRAVELLER_ID", "")`.
3. In `_store_traveller()`: the same.

After it, `import os` in `main.py` is unused (remove it or keep it). Verified on a patched copy: the whole backend
suite passes (880 passed, 10 skipped), including the two isolation tests in `tests/test_auth.py` that are skipped
until the change is applied. With `RIHLA_AUTH_REQUIRED` unset, every existing behaviour and test is unchanged.
