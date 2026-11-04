"""AuthMiddleware: identifies the signed-in user on every request and enforces Rihla's access rules.

1. Identity: the session cookie is checked (signature, expiry, logout) and the user is made available to the
   endpoints through app.auth.identity. A forged or expired cookie counts as no session.
2. Access: with RIHLA_AUTH_REQUIRED=true, the trip endpoints answer 401 without a session, however they are called.
3. CSRF: a write that carries the session cookie, and any sign-in or logout, must come from an allowed origin.
4. Isolation: a planning session (session_id) belongs to whoever used it first, so another account, or an
   anonymous caller, cannot read, revise or approve it by sending its session_id.
"""
import json
import threading
from collections import OrderedDict

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth import sessions
from app.auth.config import settings
from app.auth.identity import _current_user

PROTECTED_PREFIXES = ("/api/chat", "/api/approve", "/api/accept", "/api/trips", "/api/voice", "/api/whatif",
                      "/api/plan")
SESSION_BOUND_PATHS = ("/api/chat", "/api/approve", "/api/accept", "/api/whatif", "/api/whatif/apply", "/api/whatif/options",
                       "/api/plan/refinement", "/api/plan/refinement/apply")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_BOUND_BODY = 1_000_000
MAX_TRACKED_SESSIONS = 20_000

_owners: "OrderedDict[str, str]" = OrderedDict()  # planning session_id -> owner uid ("" = anonymous)
_owners_lock = threading.Lock()


def _is_protected(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in PROTECTED_PREFIXES)


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if referer and "://" in referer:
        scheme, rest = referer.split("://", 1)
        return f"{scheme}://{rest.split('/', 1)[0]}"
    return None


def claim_session(session_id: str, owner: str) -> bool:
    """Record the planning session's owner on first use; False when it belongs to someone else."""
    with _owners_lock:
        known = _owners.get(session_id)
        if known is None:
            _owners[session_id] = owner
            if len(_owners) > MAX_TRACKED_SESSIONS:
                _owners.popitem(last=False)
            return True
        _owners.move_to_end(session_id)
        return known == owner


def forget_sessions() -> None:
    """Tests only: forget every planning session's owner."""
    with _owners_lock:
        _owners.clear()


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status, headers={"Cache-Control": "no-store"})


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        s = settings()
        request = Request(scope)
        path, method = scope["path"], scope["method"].upper()
        has_cookie = sessions.COOKIE_NAME in request.cookies
        user = sessions.read(request.cookies.get(sessions.COOKIE_NAME), s)

        if method not in SAFE_METHODS and (has_cookie or path.startswith("/api/auth/")):
            if _request_origin(request) not in s.allowed_origins:
                await _error(403, "origin_not_allowed",
                             "This request did not come from the Rihla web app, so it was refused.")(scope, receive, send)
                return

        if s.required and user is None and _is_protected(path) and method != "OPTIONS":
            detail = ("Your session has expired. Please log in again." if has_cookie
                      else "Please log in to plan, revise, approve or view trips.")
            await _error(401, "auth_required", detail)(scope, receive, send)
            return

        if method == "POST" and path in SESSION_BOUND_PATHS:
            body, receive = await _buffer(receive)
            if body is None:
                await _error(413, "request_too_large", "The request is too large.")(scope, receive, send)
                return
            session_id = _session_id(body)
            if session_id and not claim_session(session_id, user["uid"] if user else ""):
                await _error(403, "session_not_yours",
                             "This planning session belongs to another account. Start a new trip instead."
                             )(scope, receive, send)
                return

        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)


async def _buffer(receive):
    """Read the whole request body (to see its session_id) and hand the endpoint a receive that replays it."""
    chunks, size, more = [], 0, True
    while more:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BOUND_BODY:
            return None, receive
        chunks.append(chunk)
        more = message.get("more_body", False)
    body = b"".join(chunks)
    replayed = False

    async def replay():
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay


def _session_id(body: bytes) -> str | None:
    try:
        value = json.loads(body or b"null")
    except ValueError:
        return None  # FastAPI answers 422 for it
    session_id = value.get("session_id") if isinstance(value, dict) else None
    return session_id if isinstance(session_id, str) and session_id else None
