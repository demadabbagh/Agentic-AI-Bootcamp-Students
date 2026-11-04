"""Sign-in endpoints. The browser signs in with Google through Firebase, sends the resulting ID token once, and
from then on is identified by the HttpOnly session cookie set here.

POST /api/auth/session  {"id_token": "..."}  verify the Google sign-in, open a session  -> {"user": {...}}
GET  /api/auth/me                             the signed-in user, or 401                -> {"user": {...}}
POST /api/auth/logout                         end the session (revoked on the server)   -> {"status": "logged_out"}
GET  /api/auth/config                         whether sign-in is required / configured  (no secrets)
"""
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import sessions
from app.auth.config import AuthSettings, settings
from app.auth.firebase_tokens import FirebaseTokenVerifier, TokenError

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api/auth", tags=["auth"])
NO_STORE = {"Cache-Control": "no-store"}


class SessionRequest(BaseModel):
    id_token: str = Field(min_length=20, max_length=8192)


def _verifier(request: Request, project_id: str) -> FirebaseTokenVerifier:
    """One verifier per project (it caches Google's keys); tests put their own on app.state."""
    state = request.app.state
    verifier = getattr(state, "rihla_token_verifier", None)
    if verifier is None or verifier.project_id != project_id:
        verifier = FirebaseTokenVerifier(project_id)
        state.rihla_token_verifier = verifier
    return verifier


def _secure_cookie(request: Request, s: AuthSettings) -> bool:
    if s.cookie_secure is not None:
        return s.cookie_secure
    origin = request.headers.get("origin") or ""
    host = (urlparse(origin).hostname or "").lower()
    return not (origin.startswith("http://") and host in {"localhost", "127.0.0.1"})


def _set_cookie(response: JSONResponse, request: Request, s: AuthSettings, token: str) -> None:
    response.set_cookie(sessions.COOKIE_NAME, token, max_age=s.session_ttl_s, httponly=True,
                        secure=_secure_cookie(request, s), samesite="lax", path="/")


def _clear_cookie(response: JSONResponse, request: Request, s: AuthSettings) -> None:
    response.delete_cookie(sessions.COOKIE_NAME, path="/", httponly=True, secure=_secure_cookie(request, s),
                           samesite="lax")


@router.get("/config")
def auth_config():
    """What the login modal needs: whether sign-in is set up here, and the Firebase web config for Google's popup
    (public by design: it identifies the project and grants nothing). `missing` names what the server still needs
    and `hints` why a setting that looks present is not used, never a value: the secret stays on the server.
    `project_id` lets a build with its own config check that it signs in to the project verified here."""
    s = settings()
    return JSONResponse({"required": s.required, "configured": s.configured, "provider": "google",
                         "web": s.web_config(), "project_id": s.firebase_project_id or None,
                         "missing": s.missing() + [m for m in s.missing_web() if m not in s.missing()],
                         "hints": list(s.hints)},
                        headers=NO_STORE)


@router.post("/session")
def open_session(body: SessionRequest, request: Request):
    s = settings()
    if not s.configured:
        logger.warning("Sign-in refused: the server is missing %s", ", ".join(s.missing()))
        return JSONResponse({"detail": "Sign-in is not set up on this server yet.", "code": "auth_not_configured"},
                            status_code=503, headers=NO_STORE)
    try:
        claims = _verifier(request, s.firebase_project_id).verify(body.id_token)
    except TokenError as exc:
        return JSONResponse({"detail": str(exc), "code": "invalid_sign_in"}, status_code=401, headers=NO_STORE)

    user = sessions.user_from_claims(claims, s.firebase_project_id)
    token, expires_at = sessions.issue(user, s)
    response = JSONResponse({"user": sessions.public_user({**user, "expires_at": expires_at})}, headers=NO_STORE)
    _set_cookie(response, request, s, token)
    return response


@router.get("/me")
def me(request: Request):
    s = settings()
    user = sessions.read(request.cookies.get(sessions.COOKIE_NAME), s)
    if user is None:
        response = JSONResponse({"detail": "Not signed in.", "code": "not_signed_in"}, status_code=401,
                                headers=NO_STORE)
        if sessions.COOKIE_NAME in request.cookies:
            _clear_cookie(response, request, s)
        return response
    return JSONResponse({"user": sessions.public_user(user)}, headers=NO_STORE)


@router.post("/logout")
def logout(request: Request):
    s = settings()
    user = sessions.read(request.cookies.get(sessions.COOKIE_NAME), s)
    if user is not None:
        sessions.revoke(user["session_id"], user["expires_at"])
    response = JSONResponse({"status": "logged_out"}, headers=NO_STORE)
    _clear_cookie(response, request, s)
    return response
