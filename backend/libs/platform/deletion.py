from fastapi import Depends, Request
from sqlalchemy.dialects.postgresql import insert

from libs.platform.api import Command
from libs.platform.database import session
from libs.platform.errors import require
from libs.platform.http import internal, internal_call


class DeleteInput(Command):
    deletion_id: str


def install(app, handler):
    @app.post(
        "/internal/v1/accounts/{user_id}/delete", dependencies=[Depends(internal("identity"))]
    )
    async def delete_user(
        user_id: str, body: DeleteInput, request: Request, db=Depends(session, scope="function")
    ):
        account = await internal_call(request, "identity", "GET", f"/internal/v1/users/{user_id}")
        require(
            account["status"] in {"deleting", "deleted"},
            "INVALID_STATE",
            "Аккаунт не находится в процессе удаления",
        )
        table = request.app.state.db.metadata.tables["processed_events"]
        inserted = await db.scalar(
            insert(table)
            .values(event_id=body.deletion_id, consumer="account-deletion")
            .on_conflict_do_nothing()
            .returning(table.c.event_id)
        )
        if inserted:
            await handler(request, db, user_id)
        return {"status": "completed", "deletion_id": body.deletion_id}
