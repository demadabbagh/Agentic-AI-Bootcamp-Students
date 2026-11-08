"""Rihla application package."""
from app.ssl_env import ensure_ca_bundle

ensure_ca_bundle()  # before any module loads .env: a CA bundle path from another machine must not break TLS
