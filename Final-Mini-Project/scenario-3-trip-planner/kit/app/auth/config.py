"""Rihla sign-in settings, read from the environment on every use (a changed .env or a test's env takes effect).

RIHLA_AUTH_REQUIRED        true: /api/chat, /api/approve and /api/trips answer 401 without a signed-in session.
                           false (default): the official contract stays open for the eval runner and the hidden
                           tests; a signed-in user still gets their own identity and Trip Store traveller.
RIHLA_FIREBASE_PROJECT_ID  the Firebase project whose Google sign-ins are accepted (the ID token's audience).
RIHLA_SESSION_SECRET       at least 32 random characters: signs the HttpOnly session cookie. Never commit it.
RIHLA_SESSION_TTL_HOURS    how long a session lasts (default 12).
RIHLA_ALLOWED_ORIGINS      comma-separated origins allowed to send cookie-carrying writes (CSRF protection).
RIHLA_COOKIE_SECURE        true / false / auto (default): auto marks the cookie Secure except for a local http origin.

The Firebase *web* settings the browser needs to open Google's sign-in popup. They identify the project and are
public by design (they grant nothing; every sign-in is verified here). GET /api/auth/config hands them to the
frontend, so a copy of Rihla works without its own rihla-frontend/.env.local. The VITE_FIREBASE_* names (the
frontend's .env.local) are accepted too, so one set of lines can serve both:
RIHLA_FIREBASE_WEB_API_KEY  (or VITE_FIREBASE_API_KEY)
RIHLA_FIREBASE_APP_ID       (or VITE_FIREBASE_APP_ID)
RIHLA_FIREBASE_AUTH_DOMAIN  (or VITE_FIREBASE_AUTH_DOMAIN; default <project id>.firebaseapp.com)
The project id is always RIHLA_FIREBASE_PROJECT_ID: the one ID tokens are verified against.

Where the values are read from, first found wins (a real environment variable always beats a file):
1. the process environment (production: the only place that should hold them);
2. kit/.env, then a .env in each folder above it up to the repository root (all of them, not just the nearest:
   a developer's sign-in lines in the root .env used to vanish as soon as kit/.env existed);
3. in development only, rihla-frontend/.env.local, for the public web values and nothing else, and only when it
   names the same project. With RIHLA_ENV=production that file is never read.
"""
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from app.deployment_env import is_production

KIT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_ENV_FILE = KIT_DIR / "rihla-frontend" / ".env.local"


def _load_env_files() -> None:
    """Every .env from kit/ up to the repository root; the nearest wins and the real environment beats them all."""
    for folder in (KIT_DIR, *KIT_DIR.parents):
        if (folder / ".env").is_file():
            load_dotenv(folder / ".env", override=False)
        if (folder / ".git").exists():
            break


_load_env_files()

DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
MIN_SECRET_LENGTH = 32


@lru_cache(maxsize=4)
def _read_frontend_env(path: str, modified: float) -> dict[str, str]:
    return {k: (v or "").strip() for k, v in dotenv_values(path).items()}


def _frontend_env() -> dict[str, str]:
    """rihla-frontend/.env.local in development ({} in production, or without the file). Read again when it changes."""
    if is_production():
        return {}
    try:
        modified = FRONTEND_ENV_FILE.stat().st_mtime
    except OSError:
        return {}
    return _read_frontend_env(str(FRONTEND_ENV_FILE), modified)


# What a Firebase setting is, from its name, for the settings Rihla must have.
_KINDS = (("web_api_key", re.compile(r"(API_?KEY|WEB_?KEY)$"), "RIHLA_FIREBASE_WEB_API_KEY"),
          ("web_app_id", re.compile(r"APP_?ID$"), "RIHLA_FIREBASE_APP_ID"),
          ("firebase_project_id", re.compile(r"PROJECT_?ID$"), "RIHLA_FIREBASE_PROJECT_ID"))
_READ_NAMES = frozenset({"RIHLA_FIREBASE_PROJECT_ID", "RIHLA_FIREBASE_WEB_API_KEY", "RIHLA_FIREBASE_APP_ID",
                         "RIHLA_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_API_KEY", "VITE_FIREBASE_APP_ID",
                         "VITE_FIREBASE_AUTH_DOMAIN"})


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthSettings:
    required: bool
    firebase_project_id: str
    session_secret: str
    session_ttl_s: int
    allowed_origins: tuple[str, ...]
    cookie_secure: bool | None  # None: decided per request
    web_api_key: str = ""
    web_app_id: str = ""
    web_auth_domain: str = ""
    hints: tuple[str, ...] = ()  # why a setting that looks present is not used (names only, never values)

    def web_config(self) -> dict | None:
        """The Firebase web config for the browser, or None while any part is missing."""
        config = {"apiKey": self.web_api_key, "authDomain": self.web_auth_domain,
                  "projectId": self.firebase_project_id, "appId": self.web_app_id}
        return config if all(config.values()) else None

    def missing_web(self) -> list[str]:
        """The web settings still missing (names only, never values)."""
        missing = []
        if not self.firebase_project_id:
            missing.append("RIHLA_FIREBASE_PROJECT_ID")
        if not self.web_api_key:
            missing.append("RIHLA_FIREBASE_WEB_API_KEY")
        if not self.web_app_id:
            missing.append("RIHLA_FIREBASE_APP_ID")
        return missing

    @property
    def configured(self) -> bool:
        return bool(self.firebase_project_id) and len(self.session_secret) >= MIN_SECRET_LENGTH

    def missing(self) -> list[str]:
        """The settings sign-in still needs (names only, never values)."""
        missing = []
        if not self.firebase_project_id:
            missing.append("RIHLA_FIREBASE_PROJECT_ID")
        if len(self.session_secret) < MIN_SECRET_LENGTH:
            missing.append(f"RIHLA_SESSION_SECRET (at least {MIN_SECRET_LENGTH} characters)")
        return missing


def settings() -> AuthSettings:
    try:
        ttl_hours = float(os.getenv("RIHLA_SESSION_TTL_HOURS", "") or 12)
    except ValueError:
        ttl_hours = 12
    origins = tuple(
        o.strip().rstrip("/") for o in os.getenv("RIHLA_ALLOWED_ORIGINS", "").split(",") if o.strip()
    ) or DEFAULT_ORIGINS
    secure = os.getenv("RIHLA_COOKIE_SECURE", "auto").strip().lower()
    project = os.getenv("RIHLA_FIREBASE_PROJECT_ID", "").strip()

    frontend = _frontend_env()
    hints = []
    # Another project's API key cannot open a sign-in this backend would accept: such a file is not borrowed from.
    other_project = frontend.get("VITE_FIREBASE_PROJECT_ID", "")
    if project and other_project and other_project != project:
        frontend = {}
        hints.append("rihla-frontend/.env.local names a different Firebase project than RIHLA_FIREBASE_PROJECT_ID, "
                     "so its settings are not used: make the two project ids the same.")

    def either(name: str, vite_name: str) -> str:
        return (os.getenv(name, "") or os.getenv(vite_name, "") or frontend.get(vite_name, "")).strip()

    web_api_key = either("RIHLA_FIREBASE_WEB_API_KEY", "VITE_FIREBASE_API_KEY")
    web_app_id = either("RIHLA_FIREBASE_APP_ID", "VITE_FIREBASE_APP_ID")
    found = {"web_api_key": web_api_key, "web_app_id": web_app_id, "firebase_project_id": project}
    # Set under a name Rihla does not read, while the real setting is still empty: say which name to use.
    present = {n for n, v in {**frontend, **os.environ}.items() if "FIREBASE" in n.upper() and str(v).strip()}
    for name in sorted(present - _READ_NAMES):
        for field, pattern, use in _KINDS:
            if pattern.search(name.upper()) and not found[field]:
                hints.append(f"{name} is not a name Rihla reads: use {use}.")

    return AuthSettings(
        hints=tuple(hints),
        web_api_key=web_api_key,
        web_app_id=web_app_id,
        web_auth_domain=either("RIHLA_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_AUTH_DOMAIN")
        or (f"{project}.firebaseapp.com" if project else ""),
        required=_flag("RIHLA_AUTH_REQUIRED", False),
        firebase_project_id=project,
        session_secret=os.getenv("RIHLA_SESSION_SECRET", "").strip(),
        session_ttl_s=max(300, int(ttl_hours * 3600)),
        allowed_origins=origins,
        cookie_secure=None if secure in {"", "auto"} else secure in {"1", "true", "yes", "on"},
    )
