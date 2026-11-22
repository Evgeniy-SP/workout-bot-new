"""Точка входа. Запускает бота: python bot.py"""
import asyncio
import logging
from contextlib import suppress
from datetime import datetime, time, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select

from app.config import settings
from app.db.models import User
from app.db.session import async_session_maker, ensure_db_schema
from app.handlers import (
    analytics, exercise_create, exercise_edit, export, history, notes, start,
    training_finish, training_menu, training_notes, training_sets, training_timers,
    workout_archive, workout_create, workout_crud, workout_view,
)
from app.middlewares.db import DbSessionMiddleware
from app.services.achievement_service import close_monthly_challenges, has_seasonal_archive
from app.utils import MSK

logging.basicConfig(level=logging.INFO)


async def archive_closed_months() -> None:
    today = datetime.now(MSK).date()
    if not has_seasonal_archive(today):
        return

    async with async_session_maker() as session:
        user_ids = list(await session.scalars(select(User.id)))
    for user_id in user_ids:
        async with async_session_maker() as session:
            await close_monthly_challenges(session, user_id, today)


async def maintain_challenge_archive() -> None:
    while True:
        now = datetime.now(MSK)
        midnight = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=MSK)
        await asyncio.sleep((midnight - now).total_seconds())
        while True:
            try:
                await archive_closed_months()
                break
            except Exception:
                logging.exception("Не удалось закрыть месячные челленджи")
                await asyncio.sleep(60)


async def main() -> None:
    await ensure_db_schema()
    await archive_closed_months()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.update.middleware(DbSessionMiddleware())
    # Порядок роутеров важен: общие команды → навигация → FSM-редактирование
    dp.include_routers(
        start.router,
        workout_view.router,
        history.router,
        analytics.router,
        export.router,
        notes.router,
        exercise_edit.router,
        training_menu.router,
        training_sets.router,
        training_timers.router,
        training_notes.router,
        training_finish.router,
        workout_crud.router,
        workout_archive.router,
        workout_create.router,
        exercise_create.router,
    )
    logging.info("Бот запущен")
    archive_task = asyncio.create_task(maintain_challenge_archive())
    try:
        await dp.start_polling(bot)
    finally:
        archive_task.cancel()
        with suppress(asyncio.CancelledError):
            await archive_task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
