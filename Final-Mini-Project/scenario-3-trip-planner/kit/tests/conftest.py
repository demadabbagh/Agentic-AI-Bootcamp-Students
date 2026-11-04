"""Test-wide settings.

The suite tests the official contract, which the eval runner and the hidden tests call without signing in, so it
runs with sign-in optional, whatever a developer's local .env says (CrewAI loads the repo-root .env on import, and
the web app's .env sets RIHLA_AUTH_REQUIRED=true). tests/test_auth.py switches it on per test with monkeypatch.
A value already in the environment wins over .env (load_dotenv never overrides), so this is set before any import.
"""
import os

os.environ["RIHLA_AUTH_REQUIRED"] = "false"

# The background AI review (app/refinement.py) would call a real LLM after a test has ended; tests/test_refinement.py
# runs it explicitly.
os.environ["RIHLA_AI_REVIEW"] = "off"
