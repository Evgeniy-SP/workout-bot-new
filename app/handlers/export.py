"""Хэндлеры селективного CSV-экспорта (C2.6 Блок 4).

Три сценария:
- exp_ex:{exercise_id} — вся история подходов одного упражнения;
- exp_month:{year}:{month} — все тренировки календарного месяца;
- exp_sess:{session_id} — одна конкретная тренировка (доработка).
Файл уходит документом (BufferedInputFile), UTF-8 с BOM + «;» —
Excel на Windows открывает по двойному клику.
"""
from datetime import datetime

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.queries import (
    get_exercise_history, get_session_details,
    get_session_skipped_exercise_names, get_sessions_in_range,
    get_user_exercise,
)
from app.services.analytics_service import strict_tonnage
from app.services.export_service import (
    build_exercise_csv, build_month_csv, build_session_csv,
)
from app.services.training_service import get_user
from app.utils import MSK, fmt_msk_date, format_hm, to_msk

router = Router()


@router.callback_query(F.data.startswith("exp_ex:"))
async def cb_export_exercise(callback: CallbackQuery,
                             session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    _, ex_id = callback.data.split(":")
    exercise = await get_user_exercise(session, user.id, int(ex_id))
    if exercise is None:
        return await callback.answer("Упражнение не найдено", show_alert=True)

    sets_ = await get_exercise_history(session, user.id,
                                       exercise.name, exercise.kind)
    # Интервал до следующего подхода этого же упражнения (секунды);
    # > 10 минут — переход между тренировками, не отдых
    MAX_REST_SEC = 600
    rows = []
    for i, s in enumerate(sets_):
        rest_after = None
        nxt = sets_[i + 1] if i + 1 < len(sets_) else None
        if nxt and s.completed_at and nxt.completed_at:
            gap = int((nxt.completed_at - s.completed_at).total_seconds())
            if 0 < gap <= MAX_REST_SEC:
                rest_after = gap
        rows.append({
            "date": to_msk(s.session.started_at),
            "workout": (s.session.workout_type.name
                        if s.session and s.session.workout_type else ""),
            "set_number": s.set_number,
            "weight": s.weight_kg,
            "reps": s.reps,
            "duration": s.duration_seconds,
            "rest_after": rest_after,
        })
    filename, data = build_exercise_csv(exercise.name, exercise.kind, rows)
    await callback.message.answer_document(
        BufferedInputFile(data, filename=filename),
        caption=f"📤 История: {exercise.name} ({len(rows)} подходов)",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exp_month:"))
async def cb_export_month(callback: CallbackQuery,
                          session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    _, y, m = callback.data.split(":")
    year, month = int(y), int(m)

    start = datetime(year, month, 1, tzinfo=MSK)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    end = datetime(next_y, next_m, 1, tzinfo=MSK)

    sessions = await get_sessions_in_range(session, user.id, start, end)
    if not sessions:
        return await callback.answer("Нет данных за этот месяц",
                                     show_alert=True)

    export_rows = []
    for s in sessions:
        completed = [st for st in s.sets if st.completed]
        export_rows.append({
            "date": to_msk(s.started_at),
            "workout_name": s.workout_type.name,
            "exercises": sorted({st.exercise.name for st in completed
                                 if st.exercise}),
            "sets_count": len(completed),
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            # Строгий тоннаж (только чистые кг) — совпадает с экраном
            # деталей тренировки, в отличие от дашборда «на карандаше»
            "tonnage": round(strict_tonnage(list(s.sets))),
        })
    filename, data = build_month_csv(year, month, export_rows)
    await callback.message.answer_document(
        BufferedInputFile(data, filename=filename),
        caption=f"📤 Тренировки за {month:02d}.{year} "
                f"({len(export_rows)} шт.)",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exp_sess:"))
async def cb_export_session(callback: CallbackQuery,
                            session: AsyncSession) -> None:
    """Экспорт одной тренировки — детально, как на экране деталей:
    упражнения по порядку выполнения, подходы с весом/повторами/
    длительностью и отдыхом после каждого подхода."""
    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    _, sess_id = callback.data.split(":")
    ws = await get_session_details(session, int(sess_id))
    if ws is None or ws.user_id != user.id:
        return await callback.answer("Тренировка не найдена", show_alert=True)

    completed = sorted((st for st in ws.sets if st.completed),
                       key=lambda st: st.id)
    # Порядок упражнений — по первому выполненному подходу
    order: dict[int, str] = {}
    for s in completed:
        if s.exercise and s.exercise.id not in order:
            order[s.exercise.id] = s.exercise.name
    # Завершённые досрочно (пропущенные) — в конец списка
    for name in await get_session_skipped_exercise_names(session, ws.id):
        if name not in order.values():
            order[-(len(order) + 1)] = name

    groups = []
    for ex_id, ex_name in order.items():
        sets_rows = []
        ex_sets = sorted((st for st in completed if st.exercise_id == ex_id),
                         key=lambda st: st.set_number)
        for i, s in enumerate(ex_sets):
            rest_after = None
            if i < len(ex_sets) - 1 and s.completed_at \
                    and ex_sets[i + 1].completed_at:
                rest_after = int(
                    (ex_sets[i + 1].completed_at - s.completed_at)
                    .total_seconds())
            sets_rows.append({
                "set_number": s.set_number,
                "weight": s.weight_kg,
                "reps": s.reps,
                "duration": s.duration_seconds,
                "rest_after": rest_after,
            })
        groups.append({"exercise": ex_name, "sets": sets_rows})

    filename, data = build_session_csv(ws.workout_type.name,
                                       to_msk(ws.started_at), groups)
    # Длительность и тоннаж — в подписи к файлу (в CSV только подходы)
    caption = (f"📤 Тренировка: {ws.workout_type.name} "
               f"({fmt_msk_date(ws.started_at)})")
    if ws.finished_at and ws.started_at:
        minutes = round((ws.finished_at - ws.started_at).total_seconds() / 60)
        caption += f" · ~{format_hm(minutes)}"
    tonnage = round(strict_tonnage(list(ws.sets)))
    if tonnage > 0:
        caption += f" · тоннаж {tonnage} кг"
    await callback.message.answer_document(
        BufferedInputFile(data, filename=filename), caption=caption)
    await callback.answer()
