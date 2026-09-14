import importlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Request
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from libs.platform.config import Settings
from libs.platform.errors import APIError


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return str(uuid4())


class Record:
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    version: Mapped[int] = mapped_column(Integer, default=1)


def install_tables(metadata: MetaData) -> None:
    Table(
        "outbox_events",
        metadata,
        Column("id", Uuid(as_uuid=False), primary_key=True, default=uid),
        Column("event_type", String(100), nullable=False),
        Column("aggregate_id", String(100), nullable=False),
        Column("payload", JSONB, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False, default=now),
        Column("published_at", DateTime(timezone=True)),
        Column("attempts", Integer, nullable=False, default=0),
    )
    Table(
        "processed_events",
        metadata,
        Column("event_id", Uuid(as_uuid=False), primary_key=True),
        Column("consumer", String(100), primary_key=True),
        Column("processed_at", DateTime(timezone=True), nullable=False, default=now),
    )
    Table(
        "idempotency_keys",
        metadata,
        Column("scope", String(500), primary_key=True),
        Column("request_hash", String(64), nullable=False),
        Column("response", JSONB),
        Column("status", Integer, nullable=False, default=200),
        Column("created_at", DateTime(timezone=True), nullable=False, default=now),
    )


def metadata_for(service: str) -> MetaData:
    return importlib.import_module(f"services.{service}.models").Base.metadata


class Database:
    def __init__(self, settings: Settings):
        self.metadata = metadata_for(settings.service_name)
        self.engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=5,
            connect_args={
                "timeout": 5,
                "server_settings": {
                    "statement_timeout": str(settings.db_statement_timeout_ms),
                    "lock_timeout": str(settings.db_lock_timeout_ms),
                    "idle_in_transaction_session_timeout": "120000",
                },
            },
            hide_parameters=True,
        )
        self.sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, info={"metadata": self.metadata}
        )


async def session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessions() as db:
        db.info["metadata"] = request.app.state.db.metadata
        db.info["trace_id"] = request.state.request_id
        try:
            yield db
            await db.commit()
        except OSError:
            await db.rollback()
            raise APIError(503, "DATABASE_UNAVAILABLE", "База данных временно недоступна") from None
        except BaseException:
            await db.rollback()
            raise


async def append_event(db: AsyncSession, event_type: str, aggregate_id: str, payload: dict) -> str:
    table = db.info["metadata"].tables["outbox_events"]
    event_id = uid()
    await db.execute(
        table.insert().values(
            id=event_id,
            event_type=event_type,
            aggregate_id=aggregate_id,
            payload={"__trace_id": db.info.get("trace_id", event_id), **payload},
        )
    )
    return event_id
