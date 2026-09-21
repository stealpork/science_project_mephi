from email.message import EmailMessage
from urllib.parse import urlencode

import aiosmtplib
from sqlalchemy import select

from libs.platform.database import append_event
from libs.platform.http import internal_call
from services.identity.models import AccountDeletion, User

BINDINGS = ["identity.mail_requested", "identity.account_deletion_requested"]


async def handle(request, db, event):
    settings = request.app.state.settings
    data = event["payload"]
    if event["event_type"] == "identity.mail_requested":
        message = EmailMessage()
        message["From"], message["To"] = settings.mail_from, data["email"]
        reset = data["kind"] == "reset_password"
        message["Subject"] = "Восстановление пароля" if reset else "Подтверждение email"
        url = (
            settings.public_url
            + ("/reset-password?" if reset else "/verify-email?")
            + urlencode({"token": data["token"]})
        )
        message.set_content(
            f"Stepork Science\n\nПерейдите по ссылке:\n{url}\n\nСсылка действует 30 минут. Если вы не запрашивали письмо, проигнорируйте его."
        )
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username or None,
            password=settings.smtp_password or None,
            use_tls=settings.smtp_security == "tls",
            start_tls=settings.smtp_security == "starttls",
            timeout=20,
        )
    else:
        row = await db.scalar(
            select(AccountDeletion)
            .where(AccountDeletion.id == data["deletion_id"])
            .with_for_update()
        )
        if not row or row.status == "completed":
            return
        row.status = "processing"
        for service in ("projects", "publications", "people", "media", "activity"):
            await internal_call(
                request,
                service,
                "POST",
                f"/internal/v1/accounts/{row.user_id}/delete",
                json={"deletion_id": row.id},
            )
            row.completed_services = (
                [*row.completed_services, service]
                if service not in row.completed_services
                else row.completed_services
            )
        user = await db.get(User, row.user_id)
        user.email, user.password_hash = f"{user.id}@deleted.invalid", "!deleted"
        user.mfa_secret, user.mfa_pending_secret, user.recovery_hashes = None, None, []
        user.status, user.roles, row.status = "deleted", [], "completed"


async def reconcile(request, db):
    rows = (
        await db.scalars(
            select(AccountDeletion).where(AccountDeletion.status != "completed").limit(10)
        )
    ).all()
    for row in rows:
        await append_event(
            db,
            "identity.account_deletion_requested",
            row.user_id,
            {"user_id": row.user_id, "deletion_id": row.id},
        )
