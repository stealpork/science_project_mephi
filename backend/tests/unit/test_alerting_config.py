import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def alerting(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "deploy/scripts"))
    return importlib.import_module("alerting")


def test_local_alerts_use_real_smtp_adapter_with_mailpit(alerting):
    config = alerting.configuration({})
    assert config["global"]["smtp_smarthost"] == "mailpit:1025"
    assert config["global"]["smtp_require_tls"] is False
    assert config["receivers"][0]["email_configs"][0]["send_resolved"] is True


@pytest.mark.parametrize(
    "values",
    [
        {"SC_ENVIRONMENT": "production"},
        {"SC_ENVIRONMENT": "production", "ALERT_EMAIL_TO": "ops@example.org"},
        {
            "SC_ENVIRONMENT": "production",
            "ALERT_EMAIL_TO": "ops@example.org",
            "SMTP_SECURITY": "tls",
        },
        {"SMTP_SECURITY": "tls"},
    ],
)
def test_invalid_production_delivery_configuration_fails_closed(alerting, values):
    with pytest.raises(SystemExit):
        alerting.configuration(values)


def test_production_delivery_uses_env_credentials_and_starttls(alerting):
    result = alerting.configuration(
        {
            "SC_ENVIRONMENT": "production",
            "ALERT_EMAIL_TO": "ops@example.org",
            "SMTP_HOST": "smtp.example.org",
            "SMTP_PORT": "587",
            "SMTP_SECURITY": "starttls",
            "SMTP_USERNAME": "operator",
            "SMTP_PASSWORD": "private-test-password",
        }
    )
    assert result["global"]["smtp_require_tls"]
    assert result["global"]["smtp_auth_password"] == "private-test-password"
    assert result["global"]["smtp_smarthost"] == "smtp.example.org:587"


def test_generated_secret_is_private_and_not_printed(alerting, tmp_path, capsys):
    settings = {"SECRET_DIRECTORY": str(tmp_path), "SMTP_PASSWORD": "do-not-print"}
    alerting.render(SimpleNamespace(config=settings))
    target = tmp_path / "alertmanager.yml"
    assert target.stat().st_mode & 0o777 == 0o600
    assert "do-not-print" in target.read_text()
    assert "do-not-print" not in capsys.readouterr().out
    assert not list(tmp_path.glob(".alert-config-*"))
