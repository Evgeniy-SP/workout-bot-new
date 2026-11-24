"""Хэндлеры для завершения тренировки и управления упражнениями на ходу."""
import asyncio
import random
import re
from datetime import datetime, timezone
from html import escape, unescape
from textwrap import wrap

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, \
    InlineKeyboardMarkup
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Exercise, SessionSet, WorkoutSession
from app.db.queries import (
    get_exercise_history, get_previous_finished_session, get_session_skipped_exercise_ids,
    get_sets_grouped,
)
from app.keyboards.workout_kb import (
    after_finish_kb, choose_kind_kb, confirm_training_delete_kb,
    finish_confirm_kb, training_remove_menu_kb,
)
from app.services import achievement_service as ach
from app.services.analytics_service import (
    average_rest, average_rest_between_exercises,
)
from app.services.training_service import (
    load_workout, render_approach_screen, render_list,
)
from app.services.workout_comparison import compare_exercise, format_set_count
from app.states import WorkoutCreate
from app.utils import fmt_msk_datetime, format_hm, to_msk

router = Router()

TELEGRAM_TEXT_LIMIT = 4096


def _visible_length(text: str) -> int:
    return len(unescape(re.sub(r"<[^>]+>", "", text)))


def _comparison_pages(
    heading: str, comparisons: list[tuple[str, str, str]],
) -> list[str]:
    header = f"<blockquote>{heading}\n\n"
    capacity = TELEGRAM_TEXT_LIMIT - _visible_length(header)
    pages = []
    rows = []
    remaining = capacity

    for icon, name, description in comparisons:
        prefix = f"{icon} <b>{escape(name)}</b> · "
        width = capacity - _visible_length(prefix)
        parts = [description]
        if len(description) > width:
            parts = wrap(description, width=width, replace_whitespace=False)
        for part in parts:
            row = prefix + escape(part)
            size = _visible_length(row)
            separator = 1 if rows else 0
            if size + separator > remaining:
                pages.append(header + "\n".join(rows) + "</blockquote>")
                rows = []
                remaining = capacity
                separator = 0
            rows.append(row)
            remaining -= size + separator

    if rows:
        pages.append(header + "\n".join(rows) + "</blockquote>")
    return pages


# ═══════════════════════════════════════════════════════════
#  Обновление времени отдыха
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("tr_refresh:"))
async def cb_refresh_approach(callback: CallbackQuery, session: AsyncSession):
    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    result = await render_approach_screen(session, session_id, ex_id)
    if result:
        text, kb = result
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    await callback.answer("Время обновлено")


@router.callback_query(F.data.startswith("tr_refresh_list:"))
async def cb_refresh_list(callback: CallbackQuery, session: AsyncSession):
    session_id = int(callback.data.split(":")[1])
    ws = await session.get(WorkoutSession, session_id)
    if not ws:
        return await callback.answer("Не найдено", show_alert=True)

    text, kb = await render_list(session, session_id, ws.user_id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer("Время обновлено")


# ═══════════════════════════════════════════════════════════
#  Добавление упражнения во время тренировки
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("tr_ex_add:"))
async def cb_training_add_ex(callback: CallbackQuery, session: AsyncSession,
                             state: FSMContext):
    session_id = int(callback.data.split(":")[1])
    ws = await session.get(WorkoutSession, session_id)
    if not ws or ws.finished_at:
        return await callback.answer("Тренировка не активна", show_alert=True)

    await state.update_data(
        wt_id=ws.workout_type_id,
        return_to_session=session_id,
        return_to="tr_list",
    )
    await state.set_state(WorkoutCreate.adding_exercise_kind)
    await callback.message.edit_text(
        "Какой тип упражнения?",
        # «Назад» вернёт в активную тренировку, а не в меню упражнений
        reply_markup=choose_kind_kb(ws.workout_type_id,
                                    back_cb=f"tr_list:{session_id}")
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  Удаление упражнения из шаблона (на ходу)
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("tr_remove:"))
async def cb_training_remove_ask(callback: CallbackQuery,
                                 session: AsyncSession):
    parts = callback.data.split(":")
    session_id = int(parts[1])
    ex_id = int(parts[2])
    from_menu = len(parts) > 3 and parts[3] == "menu"
    context = "menu" if from_menu else "card"

    ws = await session.get(WorkoutSession, session_id)
    ex = await session.get(Exercise, ex_id)
    if not ws or not ex or ws.finished_at:
        return await callback.answer("Недоступно", show_alert=True)

    current_sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    if current_sets:
        return await callback.answer(
            "По упражнению уже есть подходы в этой тренировке. Используйте «⏭ Пропустить».",
            show_alert=True,
        )

    history_count = await session.scalar(
        select(sa_func.count()).select_from(SessionSet).where(
            SessionSet.exercise_id == ex_id)
    ) or 0

    if history_count > 0:
        text = (
            f"Убрать упражнение <b>{ex.name}</b> из шаблона?\n\n"
            f"Упражнение выполнялось в <b>{history_count}</b> "
            f"тренировк{'е' if history_count == 1 else 'ах'} — история сохранится.\n\n"
            "📦 Упражнение скроется из шаблона."
        )
    else:
        text = (
            f"Удалить упражнение <b>{ex.name}</b>?\n\n"
            "Истории нет — удалится полностью."
        )
    await callback.message.edit_text(
        text,
        reply_markup=confirm_training_delete_kb(session_id, ex_id, context,
                                                history_count > 0)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tr_remove_yes:"))
async def cb_training_remove_confirm(callback: CallbackQuery,
                                     session: AsyncSession):
    parts = callback.data.split(":")
    session_id = int(parts[1])
    ex_id = int(parts[2])
    from_menu = len(parts) > 3 and parts[3] == "menu"

    ws = await session.get(WorkoutSession, session_id)
    ex = await session.get(Exercise, ex_id)
    if not ws or not ex or ws.finished_at:
        return await callback.answer("Недоступно", show_alert=True)

    name = ex.name
    history_count = await session.scalar(
        select(sa_func.count()).select_from(SessionSet).where(
            SessionSet.exercise_id == ex_id)
    ) or 0

    if history_count > 0:
        ex.is_archived = True
        await session.commit()
        action = "📦 Убрано в архив (история сохранена)"
    else:
        await session.delete(ex)
        await session.commit()
        action = "🗑 Удалено"

    if from_menu:
        wt = await load_workout(session, ws.workout_type_id)
        sets_by_ex = await get_sets_grouped(session, session_id)
        removable = [e for e in wt.exercises if not sets_by_ex.get(e.id)]

        if removable:
            await callback.message.edit_text(
                f"{action}: <b>{name}</b>\n\nМожно убрать ещё:",
                reply_markup=training_remove_menu_kb(session_id, removable),
            )
        else:
            text, kb = await render_list(session, session_id, ws.user_id)
            await callback.message.edit_text(
                f"{action}: <b>{name}</b>\n\n" + text, reply_markup=kb
            )
        await callback.answer("Готово")
        return

    text, kb = await render_list(session, session_id, ws.user_id)
    await callback.message.edit_text(
        f"{action}: <b>{name}</b>\n\n" + text, reply_markup=kb
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("tr_remove_menu:"))
async def cb_training_remove_menu(callback: CallbackQuery,
                                  session: AsyncSession):
    session_id = int(callback.data.split(":")[1])
    ws = await session.get(WorkoutSession, session_id)
    if not ws or ws.finished_at:
        return await callback.answer("Тренировка не активна", show_alert=True)

    wt = await load_workout(session, ws.workout_type_id)
    sets_by_ex = await get_sets_grouped(session, session_id)

    removable = [ex for ex in wt.exercises if not sets_by_ex.get(ex.id)]

    if not removable:
        text, kb = await render_list(session, session_id, ws.user_id)
        reason = (
            "Все упражнения уже начаты в этой тренировке."
            if wt.exercises
            else "В тренировке нет упражнений."
        )
        await callback.message.edit_text(f"{reason}\n\n" + text,
                                         reply_markup=kb)
        return await callback.answer()

    await callback.message.edit_text(
        "🗑 <b>Убрать упражнение из шаблона</b>\n\n"
        "Оно пропадёт из этой и будущих тренировок. "
        "Если есть история выполнения — она сохранится.\n\n"
        "Если просто хочешь пропустить сейчас — вернись и нажми на упражнение → «⏭ Пропустить».",
        reply_markup=training_remove_menu_kb(session_id, removable),
    )
    await callback.answer()


# ═══════════════════════════════════════════════════════════
#  Завершение тренировки
# ═══════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("tr_finish:"))
async def cb_finish_ask(callback: CallbackQuery, session: AsyncSession):
    session_id = int(callback.data.split(":")[1])
    ws = await session.get(WorkoutSession, session_id)
    if not ws:
        return await callback.answer("Не найдено", show_alert=True)

    wt = await load_workout(session, ws.workout_type_id)
    sets_by_ex = await get_sets_grouped(session, session_id)
    skipped_ex_ids = await get_session_skipped_exercise_ids(session, session_id)

    total_ex = 0
    partial = []
    not_started = []
    for ex in wt.exercises:
        if ex.id in skipped_ex_ids:
            continue
        sets = sets_by_ex.get(ex.id, [])
        done = sum(1 for s in sets if s.completed and not s.is_warmup)
        plan = (ex.sets or 0) if ex.kind == "strength" else 1
        if ex.is_optional and done == 0:
            continue
        total_ex += 1
        if done >= plan:
            continue

        name = escape(ex.name)
        if done:
            remaining = format_set_count(plan - done)
            verb = "остался" if remaining.endswith(" подход") else "осталось"
            partial.append(f"🟡 <b>{name}</b> · {verb} <b>{remaining}</b>")
        else:
            not_started.append(f"▪️ {name} (0/{plan})")

    not_done = len(partial) + len(not_started)
    if not_done:
        exercises_text = "\n\n".join("\n".join(group) for group in (partial, not_started) if group)

        text = (
            f"🏁 <b>Завершить тренировку?</b>\n\n"
            f"⚠️ Не выполнено полностью: <b>{not_done} из {total_ex}</b> упражнений:\n\n"
            f"{exercises_text}\n\n"
            "Тренировка сохранится в истории. Те подходы, которые ты успел сделать, будут записаны. "
            "Недоделанные подходы не сохранятся.\n\n"
            "Точно завершить?"
        )
    elif skipped_ex_ids:
        text = (
            "🏁 <b>Завершить тренировку?</b>\n\n"
            "✅ Все упражнения закрыты. Тренировка сохранится в истории."
        )
    else:
        text = (
            "🏁 <b>Завершить тренировку?</b>\n\n"
            "✅ Все упражнения выполнены. Отличная работа!"
        )

    await callback.message.edit_text(text, reply_markup=finish_confirm_kb(
        session_id))
    await callback.answer()


@router.callback_query(F.data.startswith("tr_finish_yes:"))
async def cb_finish_confirm(callback: CallbackQuery, session: AsyncSession,
                            state: FSMContext):
    await state.clear()
    session_id = int(callback.data.split(":")[1])
    ws = await session.get(WorkoutSession, session_id)
    if not ws:
        return await callback.answer("Не найдено", show_alert=True)

    ws.finished_at = datetime.now(timezone.utc)
    await session.commit()

    wt = await load_workout(session, ws.workout_type_id)
    sets_by_ex = await get_sets_grouped(session, session_id)
    skipped_ex_ids = await get_session_skipped_exercise_ids(session, session_id)

    # === C2.6: сводка рекордов за тренировку (ОДИН блок на все упражнения) ===
    from app.services.pr_service import build_pr_summary, session_pr_events

    all_pr_events = []
    exercise_names = {}
    for ex_id, sets in sets_by_ex.items():
        strength_done = [s for s in sets
                         if s.completed and not s.is_warmup and s.weight_kg and s.reps]
        if not strength_done:
            continue
        ex = await session.get(Exercise, ex_id)
        if not ex:
            continue
        exercise_names[ex_id] = ex.name
        history = await get_exercise_history(session, ws.user_id,
                                             ex.name, ex.kind)
        prior = [s for s in history
                 if not s.is_warmup and (not s.session or s.session.id != session_id)]
        all_pr_events.extend(session_pr_events(strength_done, prior))

    pr_summary_block = build_pr_summary(all_pr_events, exercise_names)

    # === C2.6 Блок 3: проверка достижений (после фиксации finished_at) ===
    new_achievements = await ach.unlock_achievements(
        session, ws.user_id, pr_in_session=len(all_pr_events))

    total_ex = 0
    done_ex = 0
    early_ex = 0
    exercise_lines = []
    for ex in wt.exercises:
        sets = sets_by_ex.get(ex.id, [])
        done = sum(1 for s in sets if s.completed and not s.is_warmup)
        plan = (ex.sets or 0) if ex.kind == "strength" else 1
        early = done < plan and ex.id in skipped_ex_ids
        if ex.is_optional and done == 0 and not early:
            continue

        total_ex += 1
        name = escape(ex.name)
        opt_mark = " <i>(опц.)</i>" if ex.is_optional else ""
        if done >= plan:
            done_ex += 1
            exercise_lines.append(f"✅ {name}{opt_mark}")
        elif early:
            early_ex += 1
            exercise_lines.append(
                f"🏁 <b>{name}</b>{opt_mark} · завершено досрочно ({done}/{plan})"
            )
        elif done > 0:
            remaining = format_set_count(plan - done)
            exercise_lines.append(f"🟡 {name}{opt_mark}: {done}/{plan} · до плана {remaining}")
        else:
            exercise_lines.append(f"⚪ {name}")

    ratio = done_ex / total_ex if total_ex else 0

    duration_sec = int((ws.finished_at - ws.started_at).total_seconds())
    minutes = duration_sec // 60

    # Средний отдых — те же метрики, что в месячной аналитике
    all_sets = [s for group in sets_by_ex.values() for s in group]
    rest = average_rest(all_sets)
    rest_ex = average_rest_between_exercises(all_sets)

    finished_time_str = fmt_msk_datetime(ws.finished_at)
    progress = f"📊 По плану: {done_ex} из {total_ex}"
    if early_ex:
        progress += f" · досрочно: {early_ex}"

    lines = [
        f"🏁 <b>{wt.name}</b> — завершена!",
        f"<i>Завершена: {finished_time_str} (МСК)</i>", # <-- Добавили время
        "", # <-- Добавили отступ
        f"⏱ Длительность: ~{format_hm(minutes)}",
        progress,
    ]
    if rest is not None:
        lines.append(f"😴 Средний отдых между подходами: "
                     f"{round(rest / 60)} мин")
    if rest_ex is not None:
        lines.append(f"🚶 Средний отдых между упражнениями: "
                     f"{round(rest_ex / 60)} мин")
    lines.append("")

    comparison_position = len(lines)
    previous = await get_previous_finished_session(session, ws)
    comparison_pages = []
    if previous:
        previous_sets = await get_sets_grouped(session, previous.id)
        comparisons = []
        for ex in wt.exercises:
            if ex.kind != "strength":
                continue
            change = compare_exercise(
                sets_by_ex.get(ex.id, []), previous_sets.get(ex.id, []),
            )
            if change:
                icon, description = change
                comparisons.append((icon, ex.name, description))
        if comparisons:
            comparison_date = to_msk(previous.started_at)
            date_format = "%d.%m, %H:%M"
            if comparison_date.year != to_msk(ws.started_at).year:
                date_format = "%d.%m.%Y, %H:%M"
            heading = f"🆚 <b>Сравнение с прошлой тренировкой ({comparison_date:{date_format}})</b>"
            comparison_pages = _comparison_pages(heading, comparisons)

    lines.extend(exercise_lines)

    # C2.6: блок рекордов (перед мотивационной фразой)
    if pr_summary_block:
        lines.append("")
        lines.append(pr_summary_block)

    if ratio >= 1.0:
        phrases = [
            "🏆 Идеальная тренировка! Все упражнения выполнены.",
            "🔥 Ты на пике формы! 100% выполнения.",
            "💎 Безупречно! Так держать.",
        ]
    elif ratio >= 0.7:
        phrases = [
            "💪 Отличная работа! Большая часть программы сделана.",
            "🔥 Хороший результат — прогресс налицо.",
            "👊 Ты выложился! Продолжай в том же духе.",
        ]
    elif ratio >= 0.4:
        phrases = [
            "👍 Хорошо поработал! Каждый подход важен.",
            "📈 Ты движешься вперёд. Не останавливайся.",
            "💡 Половина пути пройдена — продолжай!",
        ]
    else:
        phrases = [
            "🌱 Главное — не сдаваться. Каждый шаг важен.",
            "🚶 Лучше что-то, чем ничего. Ты начал!",
            "⏳ Сегодня так, завтра — больше. Главное — регулярность.",
        ]

    lines.append("")
    lines.append(f"<i>{random.choice(phrases)}</i>")

    if ws.notes:
        lines.append("")
        lines.append(f"📝 <b>Заметка к тренировке:</b>\n<i>{ws.notes}</i>")

    summary = "\n".join(lines)
    if len(comparison_pages) == 1:
        inline_lines = lines[:comparison_position] + [comparison_pages[0], ""] + lines[comparison_position:]
        inline_summary = "\n".join(inline_lines)
        if _visible_length(inline_summary) <= TELEGRAM_TEXT_LIMIT:
            summary = inline_summary
            comparison_pages = []

    await callback.message.edit_text(summary, reply_markup=after_finish_kb(session_id))
    await callback.answer("Тренировка завершена")
    for page in comparison_pages:
        await callback.message.answer(page)

    # Мгновенное сообщение о разблокированных ачивках — ПОСЛЕ экрана
    # итогов, с автоудалением через 15 сек (как у PR-уведомлений)
    if new_achievements:
        await _send_achievements_notification(callback.message,
                                              new_achievements)


@router.callback_query(F.data.startswith("tr_finish_ex:"))
async def cb_finish_ex_early(callback: CallbackQuery, session: AsyncSession):
    from app.db.models import SessionSkippedExercise
    from app.services.training_service import render_approach_screen

    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    # Ставим метку в БД
    skipped = SessionSkippedExercise(session_id=session_id, exercise_id=ex_id)
    session.add(skipped)
    await session.commit()

    # Перерисовываем экран (render_approach_screen сам поймет, что is_skipped=True и покажет итоги)
    result = await render_approach_screen(session, session_id, ex_id)
    if result:
        text, kb = result
        await callback.message.edit_text(
            "🏁 Упражнение завершено досрочно.\n\n" + text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("tr_resume_ex:"))
async def cb_resume_ex(callback: CallbackQuery, session: AsyncSession):
    from app.db.models import SessionSkippedExercise
    from app.services.training_service import render_approach_screen
    from sqlalchemy import delete

    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    # Удаляем метку
    await session.execute(
        delete(SessionSkippedExercise).where(
            SessionSkippedExercise.session_id == session_id,
            SessionSkippedExercise.exercise_id == ex_id
        )
    )
    await session.commit()

    # Перерисовываем (теперь is_skipped=False, покажется обычный экран подхода)
    result = await render_approach_screen(session, session_id, ex_id)
    if result:
        text, kb = result
        await callback.message.edit_text(
            "🔄 Упражнение возобновлено.\n\n" + text, reply_markup=kb)
    await callback.answer("Возобновлено")


@router.callback_query(F.data.startswith("tr_make_plan:"))
async def cb_make_plan(callback: CallbackQuery, session: AsyncSession):
    """Обновляет шаблон упражнения (план) фактическими данными из текущей тренировки."""
    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    from app.db.models import Exercise
    from app.db.queries import get_sets_grouped
    from app.services.training_service import render_approach_screen

    ex = await session.get(Exercise, ex_id)
    if not ex:
        return await callback.answer("Упражнение не найдено", show_alert=True)

    # Получаем все выполненные подходы в этой сессии
    sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    completed = [s for s in sets if s.completed and not s.is_warmup]

    if not completed:
        return await callback.answer("Нет выполненных подходов",
                                     show_alert=True)

    # Берем данные из ПОСЛЕДНЕГО выполненного подхода (как самый актуальный уровень)
    last_set = completed[-1]

    # Обновляем шаблон (работает и на увеличение, и на уменьшение)
    ex.sets = len(completed)  # <-- Берем фактическое количество
    if ex.kind == "strength":
        ex.reps = last_set.reps
        ex.weight_kg = last_set.weight_kg
    else:
        ex.duration_seconds = last_set.duration_seconds

    await session.commit()

    # Перерисовываем экран, чтобы пользователь увидел результат
    result = await render_approach_screen(session, session_id, ex_id)
    if result:
        text, kb = result

        # Формируем красивое сообщение об успехе
        if ex.kind == "strength":
            update_text = f"✅ Шаблон обновлен: <b>{ex.sets} подх. × {ex.reps} повт.</b>, вес <b>{ex.weight_kg or 'без веса'}</b>.\n\n"
        else:
            from app.utils import format_duration
            update_text = f"✅ Шаблон обновлен: <b>{ex.sets} подх.</b> по <b>{format_duration(ex.duration_seconds)}</b>.\n\n"

        await callback.message.edit_text(update_text + text, reply_markup=kb)
    await callback.answer("План обновлен")


# ─── C2.6 Блок 3: достижения ──────────────────────────────────────


async def _auto_delete_message(msg, delay: int = 15):
    """Удаляет сообщение через delay секунд (как у PR-уведомлений)."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass  # сообщение уже удалено пользователем или протухло


async def _send_achievements_notification(target,
                                          new: list[tuple]):
    """Шлёт сообщение о новых ачивках: кнопка «✅ Понятно» (тот же
    callback pr_dismiss, что и у рекордов) + автоудаление 15 сек."""
    msg = await target.answer(
        ach.build_achievements_message(new),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Понятно",
                                 callback_data="pr_dismiss")
        ]]),
    )
    asyncio.create_task(_auto_delete_message(msg))
