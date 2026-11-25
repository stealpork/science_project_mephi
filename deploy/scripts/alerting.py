import argparse
import json
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from email_validator import validate_email
from ops import ROOT, Ops


def configuration(values):
    production = values.get("SC_ENVIRONMENT") == "production"
    receiver = values.get("ALERT_EMAIL_TO") or (None if production else "operator@example.test")
    if not receiver:
        raise SystemExit("Set ALERT_EMAIL_TO for production alert delivery")
    validate_email(receiver, check_deliverability=False, test_environment=not production)
    host = values.get("ALERT_SMTP_HOST") or values.get("SMTP_HOST", "mailpit")
    port = values.get("ALERT_SMTP_PORT") or values.get("SMTP_PORT", "1025")
    security = values.get("ALERT_SMTP_SECURITY") or values.get("SMTP_SECURITY", "none")
    if production and (security != "starttls" or host == "mailpit"):
        raise SystemExit(
            "Production alerts require real SMTP with STARTTLS; set ALERT_SMTP_* explicitly"
        )
    if security not in {"none", "starttls"}:
        raise SystemExit(
            "Alertmanager SMTP uses none/starttls; set ALERT_SMTP_SECURITY=starttls and a STARTTLS port"
        )
    global_config = {
        "smtp_smarthost": f"{host}:{int(port)}",
        "smtp_from": values.get("ALERT_MAIL_FROM")
        or values.get("MAIL_FROM", "monitor@example.test"),
        "smtp_require_tls": security == "starttls",
    }
    username = values.get("ALERT_SMTP_USERNAME") or values.get("SMTP_USERNAME")
    password = values.get("ALERT_SMTP_PASSWORD") or values.get("SMTP_PASSWORD")
    if username:
        global_config["smtp_auth_username"] = username
    if password:
        global_config["smtp_auth_password"] = password
    return {
        "global": global_config,
        "route": {
            "receiver": "operator",
            "group_by": ["alertname", "service"],
            "group_wait": "5s",
            "group_interval": "30s",
            "repeat_interval": "4h",
        },
        "receivers": [
            {"name": "operator", "email_configs": [{"to": receiver, "send_resolved": True}]}
        ],
    }


def render(ops):
    secret = Path(ops.config["SECRET_DIRECTORY"])
    if not secret.is_absolute():
        secret = ROOT / "deploy" / secret
    secret.mkdir(parents=True, exist_ok=True)
    target = secret / "alertmanager.yml"

    data = yaml.safe_dump(configuration(ops.config), allow_unicode=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=secret, prefix=".alert-config-", delete=False
    ) as output:
        temporary = Path(output.name)
        output.write(data)
    try:
        os.chmod(temporary, 0o600)
        if os.getuid() == 0:
            os.chown(
                temporary,
                int(ops.config.get("APP_UID", "10001")),
                int(ops.config.get("APP_GID", "10001")),
            )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print("Private Alertmanager configuration rendered; credentials were not printed")


def drill(ops):
    if ops.config.get("SC_ENVIRONMENT") == "production":
        raise SystemExit(
            "Synthetic mail drill is local-only; production drill requires an agreed destination"
        )
    identifier = "ScienceDeliveryDrill" + uuid4().hex
    base = f"http://127.0.0.1:{ops.config.get('ALERTMANAGER_PORT', '19093')}"
    mail = f"http://127.0.0.1:{ops.config.get('MAIL_PORT', '18025')}"
    issued = datetime.now(UTC)
    alert = {
        "labels": {"alertname": identifier, "severity": "warning", "service": "delivery-drill"},
        "annotations": {"summary": "Local synthetic delivery drill; no service was stopped"},
        "startsAt": issued.isoformat(),
        "endsAt": (issued + timedelta(minutes=2)).isoformat(),
    }
    httpx.post(base + "/api/v2/alerts", json=[alert], timeout=10).raise_for_status()
    try:
        for _ in range(45):
            response = httpx.get(mail + "/api/v1/search", params={"query": identifier}, timeout=10)
            response.raise_for_status()
            if response.json().get("messages"):
                destination = ROOT / "test-results/alert-delivery.json"
                destination.parent.mkdir(exist_ok=True)
                destination.write_text(
                    json.dumps(
                        {
                            "success": True,
                            "transport": "Alertmanager -> SMTP -> Mailpit",
                            "verified_at": datetime.now(UTC).isoformat(),
                        }
                    )
                )
                print("Alertmanager -> SMTP -> Mailpit delivery verified")
                return
            time.sleep(1)
        raise SystemExit("No matching drill notification arrived in Mailpit")
    finally:
        alert["endsAt"] = datetime.now(UTC).isoformat()
        httpx.post(base + "/api/v2/alerts", json=[alert], timeout=10).raise_for_status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["configure", "drill"])
    parser.add_argument("--env-file", default=ROOT / ".env")
    args = parser.parse_args()
    ops = Ops(args.env_file)
    render(ops) if args.action == "configure" else drill(ops)


if __name__ == "__main__":
    main()
