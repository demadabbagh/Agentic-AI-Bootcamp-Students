"""A CA bundle setting that names a file this machine does not have is replaced by certifi's bundle.

A teammate's .env may set SSL_CERT_FILE / REQUESTS_CA_BUNDLE to a bundle on their own machine (for example
C:/Users/.../ca-bundle.pem, built for an HTTPS-scanning antivirus). On any other machine Python's ssl then fails
with FileNotFoundError before a single HTTPS request (every LLM call, every test that builds an HTTP client).

ensure_ca_bundle() runs before .env is loaded (load_dotenv never overrides a variable already set): a setting that
points to an existing file is kept as it is; one that points nowhere is set to certifi's bundle instead.
Certificate verification is never turned off: only a missing file is replaced, by the standard trusted roots.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CA_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def ensure_ca_bundle(env_file: Path | None = _ENV_FILE) -> dict[str, str]:
    """Replace CA bundle settings that name a missing file. Returns {variable: the missing path it replaced}."""
    try:
        from dotenv import dotenv_values
        from_file = dotenv_values(env_file) if env_file is not None and env_file.exists() else {}
    except Exception:
        from_file = {}
    replaced: dict[str, str] = {}
    for name in CA_VARIABLES:
        value = os.environ.get(name) or from_file.get(name)
        if not value or Path(value).is_file():
            continue
        try:
            import certifi
            bundle = certifi.where()
        except Exception:
            os.environ.pop(name, None)  # Python's own default trust store then applies
            if name in from_file:
                os.environ[name] = ""  # an empty value keeps load_dotenv from setting the missing path again
            replaced[name] = value
            continue
        os.environ[name] = bundle
        replaced[name] = value
    if replaced:
        logger.warning("CA bundle %s not found on this machine; using certifi's bundle instead.",
                       ", ".join(sorted(set(replaced.values()))))
    return replaced
