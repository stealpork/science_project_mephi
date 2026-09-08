import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from libs.platform.config import get_settings
from libs.platform.database import metadata_for

settings = get_settings()
metadata = metadata_for(settings.service_name)


def run(connection):
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    async with engine.connect() as connection:
        await connection.run_sync(run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
