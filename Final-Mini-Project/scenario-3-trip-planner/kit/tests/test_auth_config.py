"""GET /api/auth/config: the Firebase web settings the login modal needs, whichever way a developer provides them.

A copy of Rihla works whether the web settings are in the backend's environment, in a .env file, or (in development)
only in rihla-frontend/.env.local, where the frontend already needs them. The project is always the one ID tokens are
verified against. Missing settings are named, wrongly named ones are pointed out, and no value of a secret ever
appears in the answer. Placeholder values only. Each test uses its own .env.local, never the developer's.
"""
import os

import pytest
from fastapi.testclient import TestClient

import app.auth.check as auth_check
import app.auth.config as auth_config
from test_auth import PROJECT, auth_env, real_app  # noqa: F401  (auth_env: autouse env)

SECRET = "s" * 48  # what auth_env sets as RIHLA_SESSION_SECRET
WEB_NAMES = ("RIHLA_FIREBASE_WEB_API_KEY", "RIHLA_FIREBASE_APP_ID", "RIHLA_FIREBASE_AUTH_DOMAIN",
             "VITE_FIREBASE_API_KEY", "VITE_FIREBASE_APP_ID", "VITE_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_PROJECT_ID")


@pytest.fixture(autouse=True)
def frontend_file(tmp_path, monkeypatch):
    """A .env.local of this test's own (absent until written), and no web setting in the environment."""
    path = tmp_path / ".env.local"
    monkeypatch.setattr(auth_config, "FRONTEND_ENV_FILE", path)
    monkeypatch.delenv("RIHLA_ENV", raising=False)
    for name in WEB_NAMES:
        monkeypatch.delenv(name, raising=False)
    return path


def config(monkeypatch, **env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    r = TestClient(real_app()).get("/api/auth/config")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert SECRET not in r.text
    return r.json()


# ------------------------------------------------------------------ valid
def test_the_web_config_comes_from_the_server_with_the_verified_project(monkeypatch):
    body = config(monkeypatch, RIHLA_FIREBASE_WEB_API_KEY="web-key", RIHLA_FIREBASE_APP_ID="1:2:web:3")
    assert body["configured"] is True and body["missing"] == [] and body["hints"] == []
    assert body["project_id"] == PROJECT
    assert body["web"] == {"apiKey": "web-key", "authDomain": f"{PROJECT}.firebaseapp.com", "projectId": PROJECT,
                           "appId": "1:2:web:3"}


def test_the_frontend_vite_names_are_accepted_but_never_change_the_project(monkeypatch):
    body = config(monkeypatch, VITE_FIREBASE_API_KEY="web-key", VITE_FIREBASE_APP_ID="1:2:web:3",
                  VITE_FIREBASE_AUTH_DOMAIN="custom.example.com", VITE_FIREBASE_PROJECT_ID="someone-else")
    assert body["web"]["projectId"] == PROJECT and body["web"]["authDomain"] == "custom.example.com"


def test_a_developer_with_only_the_frontend_env_local_needs_nothing_more(monkeypatch, frontend_file):
    # the reported failure: the web settings were on the machine, in the file the backend never read
    frontend_file.write_text(f"VITE_FIREBASE_API_KEY=web-key\nVITE_FIREBASE_AUTH_DOMAIN=d.example.com\n"
                             f"VITE_FIREBASE_PROJECT_ID={PROJECT}\nVITE_FIREBASE_APP_ID=1:2:web:3\n", encoding="utf-8")
    body = config(monkeypatch)
    assert body["missing"] == [] and body["web"] == {"apiKey": "web-key", "authDomain": "d.example.com",
                                                     "projectId": PROJECT, "appId": "1:2:web:3"}


def test_a_real_environment_variable_beats_the_file(monkeypatch, frontend_file):
    frontend_file.write_text("VITE_FIREBASE_API_KEY=from-file\nVITE_FIREBASE_APP_ID=from-file\n", encoding="utf-8")
    assert config(monkeypatch, RIHLA_FIREBASE_WEB_API_KEY="from-env")["web"]["apiKey"] == "from-env"


def test_only_the_public_web_values_are_taken_from_the_frontend_file(monkeypatch, frontend_file):
    frontend_file.write_text("VITE_FIREBASE_API_KEY=web-key\nVITE_FIREBASE_APP_ID=1:2:web:3\n"
                             "RIHLA_SESSION_SECRET=from-the-wrong-file\nOPENROUTER_API_KEY=from-the-wrong-file\n"
                             "RIHLA_FIREBASE_PROJECT_ID=from-the-wrong-file\n", encoding="utf-8")
    body = config(monkeypatch)
    assert body["web"]["projectId"] == PROJECT
    settings = auth_config.settings()
    assert settings.session_secret == SECRET and settings.firebase_project_id == PROJECT
    assert os.environ.get("OPENROUTER_API_KEY") != "from-the-wrong-file"


def test_a_change_to_the_file_is_seen_without_a_restart(monkeypatch, frontend_file):
    assert config(monkeypatch)["web"] is None
    frontend_file.write_text("VITE_FIREBASE_API_KEY=web-key\nVITE_FIREBASE_APP_ID=1:2:web:3\n", encoding="utf-8")
    assert config(monkeypatch)["web"]["apiKey"] == "web-key"


# ------------------------------------------------------------------ missing
def test_missing_settings_are_named_and_no_partial_config_is_given(monkeypatch):
    body = config(monkeypatch, RIHLA_FIREBASE_WEB_API_KEY="web-key")
    assert body["web"] is None and body["missing"] == ["RIHLA_FIREBASE_APP_ID"]
    monkeypatch.delenv("RIHLA_FIREBASE_WEB_API_KEY")
    monkeypatch.delenv("RIHLA_SESSION_SECRET")
    monkeypatch.delenv("RIHLA_FIREBASE_PROJECT_ID")
    body = config(monkeypatch)
    assert body["configured"] is False and body["web"] is None and body["project_id"] is None
    assert body["missing"] == ["RIHLA_FIREBASE_PROJECT_ID", "RIHLA_SESSION_SECRET (at least 32 characters)",
                               "RIHLA_FIREBASE_WEB_API_KEY", "RIHLA_FIREBASE_APP_ID"]


def test_the_exact_error_from_the_report_project_and_secret_set_web_settings_absent(monkeypatch):
    body = config(monkeypatch)
    assert body["configured"] is True and body["web"] is None
    assert body["missing"] == ["RIHLA_FIREBASE_WEB_API_KEY", "RIHLA_FIREBASE_APP_ID"]


# ------------------------------------------------------------------ incorrectly named
@pytest.mark.parametrize("wrong, right", [
    ("FIREBASE_API_KEY", "RIHLA_FIREBASE_WEB_API_KEY"),
    ("RIHLA_FIREBASE_API_KEY", "RIHLA_FIREBASE_WEB_API_KEY"),
    ("REACT_APP_FIREBASE_API_KEY", "RIHLA_FIREBASE_WEB_API_KEY"),
    ("NEXT_PUBLIC_FIREBASE_API_KEY", "RIHLA_FIREBASE_WEB_API_KEY"),
    ("VITE_FIREBASE_APIKEY", "RIHLA_FIREBASE_WEB_API_KEY"),
    ("FIREBASE_APP_ID", "RIHLA_FIREBASE_APP_ID"),
    ("RIHLA_FIREBASE_WEB_APP_ID", "RIHLA_FIREBASE_APP_ID"),
    ("VITE_FIREBASE_APPID", "RIHLA_FIREBASE_APP_ID"),
])
def test_a_wrongly_named_setting_is_not_used_and_the_right_name_is_given(monkeypatch, wrong, right):
    body = config(monkeypatch, **{wrong: "a-value-that-must-not-leak"})
    assert body["web"] is None and right in body["missing"]
    assert f"{wrong} is not a name Rihla reads: use {right}." in body["hints"]
    assert "a-value-that-must-not-leak" not in str(body)


def test_a_wrong_project_variable_is_pointed_out_and_never_trusted(monkeypatch):
    monkeypatch.delenv("RIHLA_FIREBASE_PROJECT_ID")
    body = config(monkeypatch, FIREBASE_PROJECT_ID="a-project", VITE_FIREBASE_PROJECT_ID="a-project")
    assert body["configured"] is False and "RIHLA_FIREBASE_PROJECT_ID" in body["missing"]
    assert "FIREBASE_PROJECT_ID is not a name Rihla reads: use RIHLA_FIREBASE_PROJECT_ID." in body["hints"]


def test_a_wrong_name_in_the_frontend_file_is_found_too(monkeypatch, frontend_file):
    frontend_file.write_text("VITE_FIREBASE_APIKEY=web-key\nVITE_FIREBASE_APP_ID=1:2:web:3\n", encoding="utf-8")
    body = config(monkeypatch)
    assert body["web"] is None and body["missing"] == ["RIHLA_FIREBASE_WEB_API_KEY"]
    assert body["hints"] == ["VITE_FIREBASE_APIKEY is not a name Rihla reads: use RIHLA_FIREBASE_WEB_API_KEY."]


def test_no_hint_once_the_real_setting_is_there(monkeypatch):
    body = config(monkeypatch, FIREBASE_API_KEY="old", RIHLA_FIREBASE_WEB_API_KEY="web-key",
                  RIHLA_FIREBASE_APP_ID="1:2:web:3")
    assert body["web"]["apiKey"] == "web-key" and body["hints"] == []


def test_a_frontend_file_for_another_project_is_not_borrowed_from(monkeypatch, frontend_file):
    frontend_file.write_text("VITE_FIREBASE_API_KEY=other-key\nVITE_FIREBASE_APP_ID=1:2:web:3\n"
                             "VITE_FIREBASE_PROJECT_ID=another-project\n", encoding="utf-8")
    body = config(monkeypatch)
    assert body["web"] is None and any("different Firebase project" in h for h in body["hints"])


# ------------------------------------------------------------------ production
@pytest.mark.parametrize("environment", ["production", "a-typo-counts-as-production"])
def test_production_never_reads_the_development_file(monkeypatch, frontend_file, environment):
    frontend_file.write_text("VITE_FIREBASE_API_KEY=web-key\nVITE_FIREBASE_APP_ID=1:2:web:3\n", encoding="utf-8")
    body = config(monkeypatch, RIHLA_ENV=environment)
    assert body["web"] is None and body["missing"] == ["RIHLA_FIREBASE_WEB_API_KEY", "RIHLA_FIREBASE_APP_ID"]


def test_production_takes_the_settings_from_the_environment(monkeypatch):
    body = config(monkeypatch, RIHLA_ENV="production", RIHLA_FIREBASE_WEB_API_KEY="web-key",
                  RIHLA_FIREBASE_APP_ID="1:2:web:3")
    assert body["web"]["apiKey"] == "web-key" and body["missing"] == []


# ------------------------------------------------------------------ which .env files
def test_every_env_file_up_to_the_repository_root_is_read_the_nearest_first(tmp_path, monkeypatch):
    # one .env used to hide the other: sign-in lines in the root .env vanished once kit/.env existed
    kit = tmp_path / "repo" / "Final" / "kit"
    kit.mkdir(parents=True)
    (tmp_path / "repo" / ".git").write_text("gitdir: elsewhere", encoding="utf-8")  # a worktree's .git is a file
    (kit / ".env").write_text("RIHLA_T_ONLY_KIT=kit\nRIHLA_T_BOTH=kit\n", encoding="utf-8")
    (tmp_path / "repo" / ".env").write_text("RIHLA_T_ONLY_ROOT=root\nRIHLA_T_BOTH=root\nRIHLA_T_REAL=root\n",
                                            encoding="utf-8")
    (tmp_path / ".env").write_text("RIHLA_T_OUTSIDE=outside\n", encoding="utf-8")  # above the repository: not read
    monkeypatch.setattr(auth_config, "KIT_DIR", kit)
    names = ("RIHLA_T_ONLY_KIT", "RIHLA_T_ONLY_ROOT", "RIHLA_T_BOTH", "RIHLA_T_OUTSIDE")
    os.environ["RIHLA_T_REAL"] = "real"
    try:
        auth_config._load_env_files()
        assert [os.environ.get(n) for n in names] == ["kit", "root", "kit", None]
        assert os.environ["RIHLA_T_REAL"] == "real"  # the real environment always wins
    finally:
        for name in (*names, "RIHLA_T_REAL"):
            os.environ.pop(name, None)


# ------------------------------------------------------------------ python -m app.auth.check
def test_the_check_names_what_is_missing_and_prints_no_value(monkeypatch, capsys):
    monkeypatch.setenv("RIHLA_FIREBASE_APIKEY", "a-value-that-must-not-leak")
    assert auth_check.main() == 1
    out = capsys.readouterr().out
    assert "Missing: RIHLA_FIREBASE_WEB_API_KEY" in out and "Missing: RIHLA_FIREBASE_APP_ID" in out
    assert "RIHLA_FIREBASE_APIKEY is not a name Rihla reads" in out and "Not ready" in out
    assert "a-value-that-must-not-leak" not in out and SECRET not in out


def test_the_check_passes_with_only_the_frontend_file_and_prints_no_value(monkeypatch, capsys, frontend_file):
    frontend_file.write_text(f"VITE_FIREBASE_API_KEY=a-value-that-must-not-leak\nVITE_FIREBASE_APP_ID=1:2:web:3\n"
                             f"VITE_FIREBASE_PROJECT_ID={PROJECT}\n", encoding="utf-8")
    assert auth_check.main() == 0
    out = capsys.readouterr().out
    assert "Ready" in out and "a-value-that-must-not-leak" not in out and "1:2:web:3" not in out


# ------------------------------------------------------------------ the templates each developer copies
KIT = auth_config.Path(__file__).resolve().parents[1]


def _names(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.split("=")[0] for line in lines if "=" in line and not line.startswith("#")}


def test_the_templates_list_every_name_and_hold_no_value():
    backend = KIT / ".env.example"
    frontend = KIT / "rihla-frontend" / ".env.local.example"
    assert {"RIHLA_FIREBASE_PROJECT_ID", "RIHLA_SESSION_SECRET", "RIHLA_FIREBASE_WEB_API_KEY", "RIHLA_FIREBASE_APP_ID",
            "RIHLA_FIREBASE_AUTH_DOMAIN", "RIHLA_ALLOWED_ORIGINS", "RIHLA_AUTH_REQUIRED"} <= _names(backend)
    assert {"VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN", "VITE_FIREBASE_PROJECT_ID",
            "VITE_FIREBASE_APP_ID"} <= _names(frontend)
    for line in backend.read_text(encoding="utf-8").splitlines() + frontend.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#") and ("FIREBASE" in line or "SESSION_SECRET" in line):
            assert line.split("=", 1)[1].strip() in ("", "your-project.firebaseapp.com", "your-project"), line
