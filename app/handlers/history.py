"""Хэндлеры для раздела «История тренировок»."""
import asyncio

from aiogram import Router, F
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WorkoutType
from app.db.queries import (
    get_session_details, get_user_sessions, get_user_sessions_count,
    get_workout_types_with_sessions,
)
from app.keyboards.workout_kb import (
    exercise_progress_kb, history_details_kb, history_filter_kb,
    history_list_kb,
)
from app.render import render_history_details
from app.services.chart_service import (
    generate_progress_chart, generate_progress_chart_two,
)
from app.services.analytics_service import average_rest
from app.services.progress_service import (
    collect_workout_stats, daily_max_durations, daily_max_reps,
    daily_max_weights, to_chart_points,
)
from app.services.training_service import calculate_personal_records, get_user
from app.utils import fmt_msk_date, format_weight
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()


async def _render_history_page(
    session: AsyncSession, user_id: int, page: int, message: Message,
    is_edit: bool = True, wt_id: int = 0,
):
    """Вспомогательная функция: собирает данные и отрисовывает страницу истории.

    wt_id — фильтр по типу тренировки (0 = все).
    """
    limit = 5
    offset = page * limit

    filter_id = wt_id or None
    sessions = await get_user_sessions(session, user_id, offset, limit,
                                       wt_id=filter_id)
    total_count = await get_user_sessions_count(session, user_id,
                                                wt_id=filter_id)

    if not sessions and page > 0:
        return False  # Если пусто, сообщаем вызвавшему, что страницы нет

    has_next = (offset + limit) < total_count

    if not sessions:
        text = "📊 <b>История тренировок</b>\n\nУ тебя пока нет завершенных тренировок."
        kb = history_list_kb([], 0, False)
    else:
        total_pages = (total_count - 1) // limit + 1
        if wt_id:
            wt = await session.get(WorkoutType, wt_id)
            wt_name = wt.name if wt else ""
            header = f"📊 <b>История: {wt_name}</b>"
        else:
            header = "📊 <b>История твоих тренировок</b>"
        text = (
            f"{header}\n\n"
            f"Страница {page + 1} из {total_pages}\n\n"
            f"👇 Выбери тренировку для просмотра деталей:"
        )
        kb = history_list_kb(sessions, page, has_next, wt_id=wt_id)

    if is_edit:
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    else:
        await message.answer(text, reply_markup=kb)

    return True


@router.callback_query(F.data.startswith("hist_page:"))
async def cb_history_page(callback: CallbackQuery, session: AsyncSession):
    # Форматы: "hist_page:{page}" (старые кнопки) и "hist_page:{page}:{wt_id}"
    parts = callback.data.split(":")
    page = int(parts[1])
    wt_id = int(parts[2]) if len(parts) > 2 else 0

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    success = await _render_history_page(session, user.id, page,
                                         callback.message, is_edit=True,
                                         wt_id=wt_id)
    if not success:
        await callback.answer("Это последняя страница", show_alert=True)
    else:
        await callback.answer()


@router.callback_query(F.data == "hist_filter")
async def cb_history_filter(callback: CallbackQuery, session: AsyncSession):
    """Показывает список типов тренировок для фильтрации истории."""
    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    types_with_counts = await get_workout_types_with_sessions(session, user.id)
    if not types_with_counts:
        return await callback.answer("Пока нет завершённых тренировок",
                                     show_alert=True)

    await callback.message.edit_text(
        "🔍 <b>Фильтр по типу тренировки</b>\n\n"
        "Выбери тип — покажу только такие тренировки:",
        reply_markup=history_filter_kb(types_with_counts))
    await callback.answer()


@router.callback_query(F.data.startswith("hist_setfilter:"))
async def cb_history_set_filter(callback: CallbackQuery,
                                session: AsyncSession):
    """Применяет выбранный фильтр: первая страница с этим типом."""
    wt_id = int(callback.data.split(":")[1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    await _render_history_page(session, user.id, 0, callback.message,
                               is_edit=True, wt_id=wt_id)
    await callback.answer()


@router.message(F.text == "📊 История")
async def cmd_history(message: Message, session: AsyncSession):
    """Хэндлер для нижней кнопки 'История'."""
    user = await get_user(session, message.from_user.id)
    if not user:
        return await message.answer("Сначала нажми /start")

    await _render_history_page(session, user.id, 0, message, is_edit=False)


@router.callback_query(F.data.startswith("hist_view:"))
async def cb_history_view(callback: CallbackQuery, session: AsyncSession):
    """Просмотр деталей конкретной тренировки."""
    parts = callback.data.split(":")
    session_id = int(parts[1])
    page = int(parts[2])
    wt_id = int(parts[3]) if len(parts) > 3 else 0  # старые кнопки — без wt
    # «Адрес возврата» для входа из архива заметок или доски рекордов.
    origin = ":".join(parts[4:]) if len(parts) > 4 else ""

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    ws = await get_session_details(session, session_id)
    if not ws or ws.user_id != user.id:
        return await callback.answer("Тренировка не найдена", show_alert=True)

    text = render_history_details(ws)

    # Собираем клавиатуру с упражнениями
    kb = InlineKeyboardBuilder()

    unique_exercises = {}
    for s in ws.sets:
        if s.completed and s.exercise:
            unique_exercises[s.exercise.id] = s.exercise.name

    for ex_id, ex_name in unique_exercises.items():
        kb.button(text=f"📈 {ex_name}",
                  callback_data=f"ex_progress:{ex_id}:{session_id}:{page}"
                               + (f":{origin}" if origin else ""))

    if ws.finished_at and any(s.completed and s.exercise and s.exercise.kind == "strength" for s in ws.sets):
        kb.button(text="🔥 Отметки разминки",
                  callback_data=f"hw:0:-:{callback.data.split(':', 1)[1]}")

    # CSV-экспорт этой тренировки (C2.6 Блок 4)
    kb.button(text="📤 Экспорт тренировки (CSV)",
              callback_data=f"exp_sess:{session_id}")

    if origin.startswith("an_best:"):
        kb.button(text="⬅️ К лучшему", callback_data=origin)
    elif origin:
        kb.button(text="⬅️ Назад к заметкам", callback_data=origin)
    else:
        kb.button(text="⬅️ Назад к истории",
                  callback_data=f"hist_page:{page}:{wt_id}")
    kb.adjust(1)

    # УДАЛЯЕМ СТАРОЕ СООБЩЕНИЕ (с фото) И ОТПРАВЛЯЕМ НОВОЕ
    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.answer(text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("hw:"))
async def cb_history_warmups(callback: CallbackQuery, session: AsyncSession):
    _, offset_text, selection, back = callback.data.split(":", 3)
    ws = await get_session_details(session, int(back.split(":", 1)[0]))
    user = await get_user(session, callback.from_user.id)
    if not user or not ws or ws.user_id != user.id or not ws.finished_at:
        return await callback.answer("Тренировка не найдена", show_alert=True)

    sets = sorted(
        (s for s in ws.sets if s.completed and s.exercise and s.exercise.kind == "strength"),
        key=lambda s: (s.exercise.sort_order, s.exercise_id, s.set_number, s.id),
    )
    offset = int(offset_text)
    shown = sets[offset:offset + 10]
    if selection != "-":
        set_id, warmup = selection.split(".")
        target = next((s for s in shown if s.id == int(set_id)), None)
        if not target:
            return await callback.answer("Подход не найден", show_alert=True)
        if target.is_warmup == (warmup == "1"):
            return await callback.answer("Отметка уже сохранена")
        target.is_warmup = warmup == "1"
        await session.commit()

    kb = InlineKeyboardBuilder()
    for s in shown:
        marker = "🔥" if s.is_warmup else "✅"
        kb.button(
            text=f"{marker} {s.exercise.name} · {s.reps} × {format_weight(s.weight_kg)}",
            callback_data=f"hw:{offset}:{s.id}.{int(not s.is_warmup)}:{back}",
        )
    if offset:
        kb.button(text="⬅️ Предыдущие", callback_data=f"hw:{offset - 10}:-:{back}")
    if offset + 10 < len(sets):
        kb.button(text="Следующие ➡️", callback_data=f"hw:{offset + 10}:-:{back}")
    kb.button(text="⬅️ К тренировке", callback_data=f"hist_view:{back}")
    kb.adjust(1)
    await callback.message.edit_text(
        "🔥 <b>Отметки разминки</b>\n\nНажми на подход, чтобы сменить отметку.\n"
        "🔥 Разминка · ✅ Рабочий",
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


def _plural_runs(count: int) -> str:
    """Склоняет слово 'раз' для русского языка."""
    if count % 10 == 1 and count % 100 != 11:
        return "раз"
    if count % 10 in [2, 3, 4] and count % 100 not in [12, 13, 14]:
        return "раза"
    return "раз"


def _format_time(sec: int | None) -> str:
    if not sec:
        return "—"
    m, s = divmod(sec, 60)
    return f"{m} мин {s} сек" if m > 0 else f"{s} сек"


@router.callback_query(F.data.startswith("ex_progress:"))
async def cb_exercise_progress(callback: CallbackQuery, session: AsyncSession):
    """Показывает график прогресса и личные рекорды."""
    import re

    from app.db.models import Exercise
    from app.db.queries import (
        get_exercise_history, get_exercise_notes, get_session_exercise_note,
    )

    parts = callback.data.split(":")
    ex_id = int(parts[1])
    session_id = int(parts[2])
    page = int(parts[3])
    # «Адрес возврата» пробрасываем через график обратно в детали тренировки
    origin = ":".join(parts[4:]) if len(parts) > 4 else ""
    back_cb = (f"hist_view:{session_id}:{page}:0:{origin}"
               if origin else None)

    ex = await session.get(Exercise, ex_id)
    if not ex:
        return await callback.answer("Упражнение не найдено", show_alert=True)

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Пользователь не найден", show_alert=True)

    # Заметка к упражнению: в первую очередь — из ТОЙ тренировки, откуда
    # зашли (контекст), если там пусто — последняя известная (как раньше)
    sess_note = await get_session_exercise_note(session, session_id, ex_id)
    note_line = ""
    if sess_note:
        note_line = f"📝 <i>Заметка: {sess_note}</i>"
    else:
        note_rows = await get_exercise_notes(session, user.id, ex_id,
                                             limit=1, offset=0)
        if note_rows:
            last_note, last_ws = note_rows[0]
            note_date = (fmt_msk_date(last_ws.started_at)
                         if last_ws.started_at else "")
            note_line = (f"📝 <i>Последняя заметка: {last_note.note} "
                         f"({note_date})</i>")

    # ИЩЕМ ИСТОРИЮ ПО ИМЕНИ И ТИПУ (см. PROJECT_STATE.md: осознанное решение)
    history_sets = await get_exercise_history(session, user.id, ex.name, ex.kind)
    if not history_sets:
        return await callback.answer("Нет данных для графика", show_alert=True)

    # Средний отдых между подходами — та же метрика, что в аналитике
    # и на экране итогов (C2.6 Блок 4)
    rest = average_rest(history_sets)
    rest_line = (f"\n😴 Средний отдых между подходами: "
                 f"<b>{round(rest / 60)} мин</b>") if rest is not None else ""

    # Статистика: в скольких тренировках каждого типа выполнялось упражнение
    workout_stats = collect_workout_stats(history_sets)
    stats_lines = [
        f"• «{wt_name}» — {count} {_plural_runs(count)}"
        for wt_name, count in workout_stats.items()
    ]
    info_text = "🔄 <b>Выполнялось:</b>\n" + "\n".join(stats_lines)

    two_panel_chart = None  # заполним для силовых с данными о повторах

    # --- ЛОГИКА ДЛЯ СИЛОВЫХ ---
    if ex.kind == "strength":
        prs = calculate_personal_records(history_sets)

        if prs['max_weight_str']:
            weight_display = prs['max_weight_str'] if re.search(
                r'[а-яА-Яa-zA-Z]',
                prs['max_weight_str']) else f"{prs['max_weight_str']} кг"
        else:
            weight_display = "Свой вес"

        best_set_text = "—"
        volume_unit = "кг"
        if prs['best_set']:
            best_set_text = f"{prs['best_set'].reps} × {prs['best_set'].weight_kg}"
            if re.search(r'[а-яА-Яa-zA-Z]', prs['best_set'].weight_kg):
                volume_unit = re.sub(r'[\d.,]', '',
                                     prs['best_set'].weight_kg).strip()

        text = (
            f"📈 <b>Прогресс: {ex.name}</b>\n"
            f"{info_text}\n\n"
            f"🏆 <b>Личные рекорды:</b>\n"
            f"⚖️ Максимальный вес: <b>{weight_display}</b>\n"
            f"💪 Лучший подход по тоннажу: <b>{best_set_text}</b>\n"
            f"📊 Тоннаж: <b>{prs['max_volume']:g} {volume_unit}</b>"
            f"{rest_line}\n"
            f"{note_line}\n\n"
            f"📉 График: максимальный вес (сверху) и максимум повторов"
            f" за подход (снизу) по каждому дню."
        )

        daily_max = daily_max_weights(history_sets)

        if not daily_max:
            await callback.message.answer(
                text, reply_markup=exercise_progress_kb(session_id, page, ex.id, back_cb=back_cb,
                                     show_notes_btn=not origin,
                                     origin=origin))
            await callback.answer()
            return

        dates_w, values_w = to_chart_points(daily_max)

        # Второй график: максимум повторов за подход в день.
        # Если повторов нет (странные данные) — остаёмся на одном графике.
        daily_reps = daily_max_reps(history_sets)
        if daily_reps:
            dates_r, values_r = to_chart_points(daily_reps)
            two_panel_chart = (
                (dates_w, values_w, "Максимальный вес", "Вес (кг)", "#00c9ff"),
                (dates_r, values_r, "Максимум повторов за подход",
                 "Повторы", "#ff9f43"),
            )

        chart_title = f"Прогресс: {ex.name}"
        y_label = "Вес (кг)"

    # --- ЛОГИКА ДЛЯ КАРДИО ---
    else:
        max_duration = 0
        total_duration = 0
        for s in history_sets:
            if s.duration_seconds:
                total_duration += s.duration_seconds
                if s.duration_seconds > max_duration:
                    max_duration = s.duration_seconds

        text = (
            f"📈 <b>Прогресс: {ex.name}</b>\n"
            f"{info_text}\n\n"
            f"🏆 <b>Личные рекорды:</b>\n"
            f"⏱ Максимальное время за раз: <b>{_format_time(max_duration)}</b>\n"
            f"⏳ Общее время за всё время: <b>{_format_time(total_duration)}</b>"
            f"{rest_line}\n"
            f"{note_line}\n\n"
            f"📉 График: максимальное время за каждый день."
        )

        daily_max = daily_max_durations(history_sets)

        if not daily_max:
            await callback.message.answer(
                text, reply_markup=exercise_progress_kb(session_id, page, ex.id, back_cb=back_cb,
                                     show_notes_btn=not origin,
                                     origin=origin))
            await callback.answer()
            return

        dates, values = to_chart_points(daily_max)
        values = [v / 60 for v in values]  # переводим секунды в минуты
        chart_title = f"Прогресс: {ex.name}"
        y_label = "Время (мин)"

    # --- ГЕНЕРАЦИЯ И ОТПРАВКА ГРАФИКА ---
    if two_panel_chart:
        chart_buf = await asyncio.to_thread(
            generate_progress_chart_two, chart_title, *two_panel_chart)
    else:
        chart_buf = await asyncio.to_thread(generate_progress_chart, dates,
                                            values, chart_title, y_label)
    photo = BufferedInputFile(chart_buf.read(),
                              filename=f"progress_{ex_id}.png")

    await callback.message.answer_photo(
        photo=photo,
        caption=text,
        reply_markup=exercise_progress_kb(session_id, page, ex.id, back_cb=back_cb,
                                     show_notes_btn=not origin,
                                     origin=origin)
    )
    await callback.answer()
