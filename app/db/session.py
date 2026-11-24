"""Подключение к PostgreSQL."""
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import MonthlyChallengeResult, SessionSet

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def ensure_db_schema() -> None:
    """Проверить наличие схемы архива и отметок разминки."""
    async with engine.connect() as conn:
        has_archive = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).has_table(MonthlyChallengeResult.__tablename__),
        )
        if not has_archive:
            raise RuntimeError(
                "В базе бота нет таблицы monthly_challenge_results. "
                "Проверь адрес базы и примени миграции Alembic перед запуском."
            )
        set_columns = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_columns(SessionSet.__tablename__),
        )
    if not any(column["name"] == "is_warmup" for column in set_columns):
        raise RuntimeError(
            "В базе бота нет отметок разминки. Проверь адрес базы и примени миграцию 0004."
        )
