"""Хэндлеры раздела «📅 Аналитика» (C2.5)."""
import asyncio
from datetime import datetime

from aiogram import Router, F
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.queries import get_months_with_sessions, get_sessions_in_range
from app.keyboards.workout_kb import MONTH_NAMES, analytics_kb
from app.services.analytics_service import (
    average_rest, calculate_tonnage, month_activity, month_top_stats,
    weekly_activity,
)
from app.services.chart_service import generate_grouped_bar_chart
from app.services.training_service import get_user
from app.utils import MSK

router = Router()

WEEK_LABELS = ["1–7", "8–14", "15–21", "22–28", "29–31"]
MEDALS = ["🥇", "🥈", "🥉"]


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """[Первый день месяца, первый день следующего) в МСК."""
    start = datetime(year, month, 1, tzinfo=MSK)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    end = datetime(next_y, next_m, 1, tzinfo=MSK)
    return start, end


def _nav_targets(year: int, month: int,
                 months: list[tuple[int, int]]
                 ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Ближайшие месяца С ДАННЫМИ до/после текущего — цели кнопок листания.

    Пустые месяцы (и перерывы любой длины) пропускаются.
    """
    cur = (year, month)
    prev = next_ = None
    for ym in months:  # months отсортирован по возрастанию
        if ym < cur:
            prev = ym
        elif ym > cur:
            next_ = ym
            break
    return prev, next_


def _format_hours(total_seconds: int) -> str:
    """13011 минут -> '216 ч 51 мин'; если меньше часа — просто '35 мин'."""
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _format_rest(seconds: float) -> str:
    """Отдых в минутах (секунды не показываем): '2 мин', '1 ч 5 мин'."""
    minutes = round(seconds / 60)
    if minutes >= 60:
        return f"{minutes // 60} ч {minutes % 60} мин"
    return f"{minutes} мин"


def _set_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "подход"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "подхода"
    return "подходов"


async def _render_dashboard(session: AsyncSession, user_id: int,
                            year: int, month: int, message: Message,
                            is_edit: bool) -> None:
    start, end = _month_bounds(year, month)
    sessions = await get_sessions_in_range(session, user_id, start, end)

    month_title = f"{MONTH_NAMES[month - 1]} {year}"
    months = await get_months_with_sessions(session, user_id)
    prev_ym, next_ym = _nav_targets(year, month, months)
    kb = analytics_kb(year, month, prev_ym, next_ym)

    if not sessions:
        text = (f"📅 <b>Аналитика: {month_title}</b>\n\n"
                f"За этот месяц нет завершённых тренировок.")
        if is_edit:
            await message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        return

    all_sets = [s for ses in sessions for s in ses.sets]

    # Собираем метрики через сервис
    tonnage = calculate_tonnage(all_sets)
    total_sets, total_exercises = month_activity(all_sets)
    favorites = month_top_stats(all_sets)
    rest = average_rest(all_sets)

    total_seconds = sum(
        int((ses.finished_at - ses.started_at).total_seconds())
        for ses in sessions if ses.finished_at and ses.started_at
    )

    lines = [
        f"📅 <b>Аналитика: {month_title}</b>",
        "",
        f"🏋️ Тренировок: <b>{len(sessions)}</b>",
        f"💪 Подходов: <b>{total_sets}</b>",
        f"🤸 Упражнений выполнено: <b>{total_exercises}</b>",
        f"⏱ Общее время: <b>{_format_hours(total_seconds)}</b>",
        f"🚚 Тоннаж: <b>{tonnage:,.0f} кг</b>".replace(",", " "),
    ]

    if favorites:
        lines.append("")
        lines.append("🏆 <b>Топ упражнений по подходам:</b>")
        for medal, (name, n, best) in zip(MEDALS, favorites):
            result = f" · макс: {best}" if best else ""
            lines.append(f"{medal} {name} — {n} {_set_word(n)}{result}")

    if rest is not None:
        lines.append("")
        lines.append(f"😴 Средний отдых между подходами: "
                     f"<b>{_format_rest(rest)}</b>")

    lines.append("")
    lines.append("👇 Листай месяцы или смотри график:")

    text = "\n".join(lines)
    if is_edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(F.text == "📅 Аналитика")
async def cmd_analytics(message: Message, session: AsyncSession):
    user = await get_user(session, message.from_user.id)
    if not user:
        return await message.answer("Сначала нажми /start")

    now = datetime.now(MSK)
    await _render_dashboard(session, user.id, now.year, now.month,
                            message, is_edit=False)


@router.callback_query(F.data.startswith("an_month:"))
async def cb_analytics_month(callback: CallbackQuery, session: AsyncSession):
    _, y, m = callback.data.split(":")
    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    await _render_dashboard(session, user.id, int(y), int(m),
                            callback.message, is_edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("an_chart:"))
async def cb_analytics_chart(callback: CallbackQuery, session: AsyncSession):
    _, y, m = callback.data.split(":")
    year, month = int(y), int(m)

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    start, end = _month_bounds(year, month)
    sessions = await get_sessions_in_range(session, user.id, start, end)
    if not sessions:
        return await callback.answer("Нет данных за этот месяц",
                                     show_alert=True)

    trainings, exercises = weekly_activity(sessions)
    if not any(trainings):
        return await callback.answer("Нет данных за этот месяц",
                                     show_alert=True)

    title = f"Активность по неделям: {MONTH_NAMES[month - 1]} {year}"
    series = [("Тренировки", trainings, "#00c9ff"),
              ("Упражнения", exercises, "#ff9f43")]
    chart_buf = await asyncio.to_thread(generate_grouped_bar_chart,
                                        WEEK_LABELS, series, title,
                                        "Количество")
    photo = BufferedInputFile(chart_buf.read(),
                              filename=f"analytics_{year}_{month}.png")

    await callback.message.answer_photo(photo=photo)
    await callback.answer()
