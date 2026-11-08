"""A CA bundle path that does not exist on this machine is replaced by certifi's; an existing one is kept."""
import os

import certifi

from app.ssl_env import ensure_ca_bundle


def test_a_missing_bundle_from_dotenv_becomes_certifi(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SSL_CERT_FILE=C:/Users/user/.rihla/ca-bundle.pem\nREQUESTS_CA_BUNDLE=C:/nowhere.pem\n")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    replaced = ensure_ca_bundle(env)
    assert set(replaced) == {"SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
    assert os.environ["SSL_CERT_FILE"] == certifi.where() == os.environ["REQUESTS_CA_BUNDLE"]


def test_an_existing_bundle_is_kept(tmp_path, monkeypatch):
    bundle = tmp_path / "corp.pem"
    bundle.write_text("x")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    assert ensure_ca_bundle(None) == {}
    assert os.environ["SSL_CERT_FILE"] == str(bundle)
