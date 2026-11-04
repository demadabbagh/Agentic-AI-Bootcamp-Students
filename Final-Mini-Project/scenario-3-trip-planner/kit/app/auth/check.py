"""Is Google sign-in set up on this machine?  python -m app.auth.check  (from kit/; exit 0 = ready).

Reads the settings exactly as the running backend does (app/auth/config.py) and says what is missing and why. It
prints names and yes/no only, never a value, so its output is safe to paste into a team chat.
"""
from __future__ import annotations

import sys

from app.auth.config import FRONTEND_ENV_FILE, settings


def report() -> tuple[bool, list[str]]:
    """(ready, lines to print)."""
    s = settings()
    missing = s.missing() + [m for m in s.missing_web() if m not in s.missing()]
    lines = [f"Backend can verify sign-ins:  {'yes' if s.configured else 'NO'}",
             f"Firebase web settings found:  {'yes' if s.web_config() else 'NO'}",
             f"rihla-frontend/.env.local:    {'found' if FRONTEND_ENV_FILE.is_file() else 'not found'}",
             f"Sign-in required on the API:  {'yes' if s.required else 'no (open contract mode)'}",
             f"Allowed web origins:          {', '.join(s.allowed_origins)}"]
    lines += [f"Missing: {name}" for name in missing]
    lines += [f"Note: {hint}" for hint in s.hints]
    ready = s.configured and s.web_config() is not None
    lines.append("Ready: restart the backend, then sign in." if ready else
                 "Not ready: see .env.example and rihla-frontend/.env.local.example, then run this again.")
    return ready, lines


def main() -> int:
    ready, lines = report()
    print("\n".join(lines))
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
