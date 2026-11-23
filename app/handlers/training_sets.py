"""Хэндлеры для сохранения подходов (кнопки «Как планировал» и «Ввести фактически»)."""
import asyncio
from datetime import datetime, timezone
from html import escape

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, \
    InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Exercise, SessionSet, WorkoutSession
from app.db.queries import get_exercise_history, get_last_completed_set, \
    get_sets_grouped
from app.keyboards.workout_kb import (
    cancel_kb,
    numeric_keypad_kb,
    weight_keypad_kb,
    text_weight_kb,
)
from app.services.training_service import (
    next_set_num, render_approach_screen, render_list,
)
from app.states import TrainingFSM
from app.utils import format_duration, format_weight, parse_minutes_to_seconds

router = Router()


def _weight_entry_text(ex: Exercise, reps: int, data: dict) -> str:
    weight = data.get("suggested_weight", ex.weight_kg)
    source = data.get("weight_hint_source", "План")
    return (
        f"🏋️ <b>{ex.name}</b>\n\n"
        f"Повторов: <b>{reps}</b>.\n"
        "Теперь введи вес (снаряд).\n"
        f"<i>{source}:</i> <code>{escape(format_weight(weight))}</code>\n\n"
        "<i>(Нажми на значение, чтобы скопировать)</i>"
    )


@router.callback_query(F.data.startswith("tr_save_planned:"))
async def cb_save_planned(callback: CallbackQuery, session: AsyncSession,
                          state: FSMContext):
    await state.clear()

    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    ws = await session.get(WorkoutSession, session_id)
    ex = await session.get(Exercise, ex_id)
    if not ws or not ex:
        return

    # Определяем плановые значения
    last_set = await get_last_completed_set(session, ws.user_id, ex_id)
    planned_weight = last_set.weight_kg if last_set and last_set.weight_kg else ex.weight_kg
    planned_reps = last_set.reps if last_set and last_set.reps else ex.reps

    if ex.kind != "strength":
        planned_duration = last_set.duration_seconds if last_set and last_set.duration_seconds else ex.duration_seconds
    else:
        planned_duration = None

    # Красивое уведомление ДО сохранения
    from app.utils import format_weight
    if ex.kind == "strength":
        alert_text = f"Как в предыдущем подходе: {planned_reps} × {format_weight(planned_weight)}"
    else:
        from app.utils import format_duration
        alert_text = f"✅ Сохранено: {format_duration(planned_duration)}"
    await callback.answer(alert_text)

    # Удаляем метку досрочного завершения, если она была
    from app.db.models import SessionSkippedExercise
    from sqlalchemy import delete
    await session.execute(
        delete(SessionSkippedExercise).where(
            SessionSkippedExercise.session_id == session_id,
            SessionSkippedExercise.exercise_id == ex_id
        )
    )
    await session.commit()

    sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    set_num = next_set_num(sets)

    new_set = SessionSet(
        session_id=session_id,
        exercise_id=ex_id,
        set_number=set_num,
        reps=planned_reps if ex.kind == "strength" else None,
        weight_kg=planned_weight if ex.kind == "strength" else None,
        duration_seconds=planned_duration,
        completed=True,
        completed_at=datetime.now(timezone.utc)
    )
    session.add(new_set)
    await session.commit()

    # C2.6: мгновенное уведомление о рекорде (для новых силовых подходов)
    await _check_and_notify_pr(callback.message, session, session_id,
                               ex_id, new_set)

    result_screen = await render_approach_screen(session, session_id, ex.id)
    if result_screen:
        text, kb = result_screen
        await callback.message.edit_text(text, reply_markup=kb)
    else:
        text, kb = await render_list(session, session_id, ws.user_id)
        await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("tr_custom:"))
async def cb_custom_start(callback: CallbackQuery, session: AsyncSession,
                          state: FSMContext):
    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    ex = await session.get(Exercise, ex_id)
    if not ex:
        return await callback.answer("Не найдено", show_alert=True)

    existing_id = None
    if ex.kind != "strength":
        sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
        completed = [s for s in sets if s.completed]
        if completed:
            existing_id = completed[0].id

    ws = await session.get(WorkoutSession, session_id)

    # Ищем подсказку для повторов
    sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    completed = [s for s in sets if s.completed]

    if completed:
        # Берем из последнего подхода в ТЕКУЩЕЙ тренировке
        suggested_weight = completed[-1].weight_kg
        weight_hint_source = "В предыдущем подходе"
        history = f"<i>В предыдущем подходе:</i> <code>{completed[-1].reps}</code> повторений"
    else:
        # Если сегодня еще не делали, берем из истории
        last_set = await get_last_completed_set(session, ws.user_id, ex_id, current_session_id=session_id)
        if last_set:
            suggested_weight = last_set.weight_kg
            weight_hint_source = "В прошлый раз"
            history = f"<i>В прошлый раз:</i> <code>{last_set.reps}</code> повторений"
        else:
            suggested_weight = ex.weight_kg
            weight_hint_source = "План"
            history = f"<i>План:</i> <code>{ex.reps}</code> повторений"

    await state.update_data(
        session_id=session_id,
        ex_id=ex_id,
        editing_set_id=existing_id,
        return_to="tr_ex_card",
        temp_num="",
        temp_weight="",
        suggested_weight=suggested_weight,
        weight_hint_source=weight_hint_source,
    )

    if ex.kind == "strength":
        set_num = len(completed) + 1
        await state.set_state(TrainingFSM.entering_reps)

        await callback.message.edit_text(
            f"🏋️ <b>{ex.name}</b> — подход {set_num}\n\n"
            f"{history}\n\n"
            f"Сколько повторов сделал? Введи число:",
            reply_markup=numeric_keypad_kb()
        )
    else:
        await state.set_state(TrainingFSM.entering_duration)
        # ... (код для кардио оставляем без изменений) ...
        if existing_id:
            current_set = await session.get(SessionSet, existing_id)
            current_text = (format_duration(
                current_set.duration_seconds) if current_set and current_set.duration_seconds else "не задано")
            await callback.message.edit_text(
                f"<b>{ex.name}</b>\n\nСейчас: <b>{current_text}</b>\n\n"
                "Введи новое время в минутах (можно 1.5 = 1 мин 30 сек):",
                reply_markup=weight_keypad_kb(prefix="t_num"),
            )
        else:
            plan_text = format_duration(
                ex.duration_seconds) if ex.duration_seconds else "по факту"
            await callback.message.edit_text(
                f"<b>{ex.name}</b>\n\nПлан: <b>{plan_text}</b>\n\n"
                "Сколько минут заняло? Введи число (можно 1.5 = 1 мин 30 сек):",
                reply_markup=weight_keypad_kb(prefix="t_num"),
            )

    await callback.answer()


@router.message(TrainingFSM.entering_reps)
async def fsm_enter_reps(message: Message, state: FSMContext,
                         session: AsyncSession):
    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        return await message.answer("Нужно целое число > 0. Попробуй ещё:",
                                    reply_markup=cancel_kb())

    data = await state.get_data()
    reps = int(text)
    await state.update_data(custom_reps=reps, temp_weight="")

    ex = await session.get(Exercise, data["ex_id"])

    await state.set_state(TrainingFSM.entering_weight)

    await message.answer(
        _weight_entry_text(ex, reps, data),
        reply_markup=weight_keypad_kb()
    )


@router.message(TrainingFSM.entering_weight)
async def fsm_enter_weight(message: Message, state: FSMContext,
                           session: AsyncSession):
    text = message.text.strip()
    if text in {"-", "0", "0.0", "нет"}:
        weight = None
    else:
        weight = text.replace(",", ".")

    data = await state.get_data()
    await _save_custom_set(message, state, session, data,
                           data.get("custom_reps"), weight)


@router.message(TrainingFSM.entering_duration)
async def fsm_enter_duration(message: Message, state: FSMContext,
                             session: AsyncSession):
    text = message.text.strip()
    if text in {"-", "0"}:
        seconds = None
    else:
        seconds = parse_minutes_to_seconds(text)
        if seconds is None:
            return await message.answer(
                "Нужно положительное число или «-». Попробуй ещё:",
                reply_markup=cancel_kb())

    data = await state.get_data()
    await _save_custom_set(message, state, session, data, None, None,
                           duration=seconds)


async def _save_custom_set(message: Message, state: FSMContext,
                           session: AsyncSession,
                           data: dict, reps: int | None, weight: str | None,
                           duration: int | None = None):
    """Создаёт новый или обновляет существующий подход."""
    from datetime import datetime, timezone
    from app.db.models import SessionSet, WorkoutSession, Exercise
    from app.db.queries import get_sets_grouped
    from app.services.training_service import render_approach_screen, \
        render_list

    session_id = data["session_id"]
    ex_id = data["ex_id"]
    editing_set_id = data.get("editing_set_id")

    ex = await session.get(Exercise, ex_id)

    if editing_set_id:
        target = await session.get(SessionSet, editing_set_id)
        if not target:
            await state.clear()
            return await message.answer("Подход не найден.")

        old_reps = target.reps
        old_weight = target.weight_kg
        old_duration = target.duration_seconds

        target.reps = reps
        target.weight_kg = weight
        target.duration_seconds = duration
        target.completed_at = datetime.now(timezone.utc)

        from app.db.models import SessionSkippedExercise
        from sqlalchemy import delete
        await session.execute(
            delete(SessionSkippedExercise).where(
                SessionSkippedExercise.session_id == session_id,
                SessionSkippedExercise.exercise_id == ex_id
            )
        )

        await session.commit()

        parts = []
        if ex.kind == "strength":
            if old_reps != reps:
                parts.append(f"повторы: <b>{old_reps} → {reps}</b>")
            if old_weight != weight:
                from app.utils import format_weight
                parts.append(
                    f"вес: <b>{format_weight(old_weight)} → {format_weight(weight)}</b>")
        else:
            if old_duration != duration:
                from app.utils import format_duration
                parts.append(
                    f"время: <b>{format_duration(old_duration)} → {format_duration(duration)}</b>")

        if parts:
            change_text = f"✅ <b>Подход {target.set_number} обновлен.</b>\n" + "\n".join(
                parts)
        else:
            change_text = f"✅ <b>Подход {target.set_number}: сохранено (без изменений).</b>"

        await state.clear()

        # Используем единую функцию для отрисовки
        result_screen = await render_approach_screen(session, session_id,
                                                     ex_id)
        if result_screen:
            text, kb = result_screen
            await message.answer(change_text + "\n\n" + text, reply_markup=kb)
        else:
            ws = await session.get(WorkoutSession, session_id)
            text, kb = await render_list(session, session_id, ws.user_id)
            await message.answer(change_text + "\n\n" + text, reply_markup=kb)
        return

    # Создание нового подхода
    sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    from app.services.training_service import next_set_num
    set_num = next_set_num(sets)

    new_set = SessionSet(
        session_id=session_id,
        exercise_id=ex_id,
        set_number=set_num,
        reps=reps,
        weight_kg=weight,
        duration_seconds=duration,
        completed=True,
        completed_at=datetime.now(timezone.utc)
    )
    session.add(new_set)

    from app.db.models import SessionSkippedExercise
    from sqlalchemy import delete
    await session.execute(
        delete(SessionSkippedExercise).where(
            SessionSkippedExercise.session_id == session_id,
            SessionSkippedExercise.exercise_id == ex_id
        )
    )

    await session.commit()

    # C2.6: определяем рекорд СЕЙЧАС, но шлём ПОСЛЕ отрисовки экрана,
    # чтобы сообщение о рекорде было последним в чате
    pr_text = await _build_pr_notification(session, session_id, ex_id, new_set)

    all_sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    done = sum(1 for s in all_sets if s.completed)
    plan = (ex.sets or 1) if ex.kind == "strength" else 1

    await state.clear()

    ws = await session.get(WorkoutSession, session_id)

    if done >= plan:
        result_screen = await render_approach_screen(session, session_id,
                                                     ex.id)
        if result_screen:
            text, kb = result_screen
            await message.answer(f"✅ {ex.name} готово\n\n" + text,
                                 reply_markup=kb)
        else:
            text, kb = await render_list(session, session_id, ws.user_id)
            await message.answer(text, reply_markup=kb)
    else:
        # ВАЖНО: Вместо ручного кода, вызываем нашу единую умную функцию!
        result_screen = await render_approach_screen(session, session_id, ex_id)
        if result_screen:
            text, kb = result_screen
            await message.answer(text, reply_markup=kb)
        else:
            text, kb = await render_list(session, session_id, ws.user_id)
            await message.answer(text, reply_markup=kb)

    if pr_text:
        await _send_pr_notification(message, pr_text)


@router.callback_query(F.data.startswith("tr_edit_set:"))
async def cb_edit_set(callback: CallbackQuery, session: AsyncSession,
                      state: FSMContext):
    _, sess_str, set_str = callback.data.split(":")
    session_id = int(sess_str)
    set_id = int(set_str)

    target = await session.get(SessionSet, set_id)
    if not target:
        return await callback.answer("Подход не найден", show_alert=True)

    ex = await session.get(Exercise, target.exercise_id)
    if not ex:
        return await callback.answer("Упражнение не найдено", show_alert=True)

    # Ищем подсказку для повторов (исключая текущий редактируемый подход)
    ws = await session.get(WorkoutSession, session_id)
    sets = (await get_sets_grouped(session, session_id)).get(ex.id, [])
    completed = [s for s in sets if s.completed and s.id != set_id]

    if completed:
        suggested_reps = completed[-1].reps
        history_text = f"<i>В предыдущем подходе:</i> <code>{suggested_reps}</code> повторений"
    else:
        last_set = await get_last_completed_set(session, ws.user_id, ex.id, current_session_id=session_id)
        if last_set:
            suggested_reps = last_set.reps
            history_text = f"<i>В прошлый раз:</i> <code>{suggested_reps}</code> повторений"
        else:
            suggested_reps = ex.reps
            history_text = f"<i>План:</i> <code>{suggested_reps}</code> повторений"

    await state.update_data(
        session_id=session_id,
        ex_id=ex.id,
        editing_set_id=set_id,
        return_to="tr_strength_summary",
        temp_num="",
        suggested_weight=target.weight_kg,
        weight_hint_source="Вес этого подхода",
    )
    await state.set_state(TrainingFSM.entering_reps)

    # ЕДИНЫЙ СТИЛЬ, БЕЗ "ТЕКУЩЕГО ЗНАЧЕНИЯ"
    await callback.message.edit_text(
        f"🏋️ <b>{ex.name}</b> — подход {target.set_number}\n\n"
        f"{history_text}\n\n"
        f"Сколько повторов сделал? Введи число:",
        reply_markup=numeric_keypad_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tr_add_set:"))
async def cb_add_set(callback: CallbackQuery, session: AsyncSession,
                     state: FSMContext):
    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    ex = await session.get(Exercise, ex_id)
    if not ex:
        return await callback.answer("Не найдено", show_alert=True)

    sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    completed = [s for s in sets if s.completed]
    set_num = len(completed) + 1

    if completed:
        reference = completed[-1]
        source = "В предыдущем подходе"
    else:
        ws = await session.get(WorkoutSession, session_id)
        if not ws:
            return await callback.answer("Тренировка не найдена", show_alert=True)
        reference = await get_last_completed_set(
            session, ws.user_id, ex_id, current_session_id=session_id,
        )
        source = "В прошлый раз" if reference else "План"

    reps = reference.reps if reference else ex.reps
    weight = reference.weight_kg if reference else ex.weight_kg
    reps_text = str(reps) if reps is not None else "—"
    history = f"🕰 {source}: <b>{reps_text} × {escape(format_weight(weight))}</b>"

    await state.update_data(
        session_id=session_id,
        ex_id=ex_id,
        editing_set_id=None,
        return_to="tr_strength_summary",
        temp_num="",
        temp_weight="",
        suggested_weight=weight,
        weight_hint_source=source,
    )
    await state.set_state(TrainingFSM.entering_reps)

    await callback.message.edit_text(
        f"🏋️ <b>{ex.name}</b> — подход {set_num}\n\n"
        f"{history}\n\n"
        f"Сколько повторов сделал?",
        reply_markup=numeric_keypad_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tr_delete_set:"))
async def cb_delete_set(callback: CallbackQuery, session: AsyncSession,
                        state: FSMContext):
    await state.clear()
    _, sess_str, set_str = callback.data.split(":")
    session_id = int(sess_str)
    set_id = int(set_str)

    target = await session.get(SessionSet, set_id)
    if not target:
        return await callback.answer("Подход уже удален", show_alert=True)

    ex_id = target.exercise_id
    await session.delete(target)
    await session.commit()

    # Перенумеровываем оставшиеся подходы, чтобы они шли 1, 2, 3... (без дырок)
    sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    for idx, s in enumerate(sets, start=1):
        s.set_number = idx
    await session.commit()

    # Возвращаемся на экран подхода/итогов
    result = await render_approach_screen(session, session_id, ex_id)
    if result:
        text, kb = result
        await callback.message.edit_text("🗑 Подход удален.\n\n" + text,
                                         reply_markup=kb)
    else:
        ws = await session.get(WorkoutSession, session_id)
        text, kb = await render_list(session, session_id, ws.user_id)
        await callback.message.edit_text("🗑 Подход удален.\n\n" + text,
                                         reply_markup=kb)

    await callback.answer("Подход удален")


@router.callback_query(F.data.startswith("tr_edit_menu:"))
async def cb_edit_menu(callback: CallbackQuery, session: AsyncSession):
    """Меню выбора подхода для редактирования."""
    _, sess_str, ex_str = callback.data.split(":")
    session_id = int(sess_str)
    ex_id = int(ex_str)

    sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
    completed = [s for s in sets if s.completed]

    if not completed:
        return await callback.answer("Нет выполненных подходов",
                                     show_alert=True)

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    sizes = []

    # Выводим все выполненные подходы в виде кнопок
    for s in completed:
        weight_text = format_weight(s.weight_kg)
        kb.button(
            text=f"✏️ Подход {s.set_number}: {s.reps}×{weight_text}",
            callback_data=f"tr_edit_set:{session_id}:{s.id}"
        )

    # Логика группировки по 2 в ряд
    num_sets = len(completed)
    while num_sets > 0:
        row_size = min(num_sets, 2)
        sizes.append(row_size)
        num_sets -= row_size

    # Кнопка возврата (всегда одна в ряду)
    kb.button(text="⬅️ Назад к упражнению",
              callback_data=f"tr_ex:{session_id}:{ex_id}")
    sizes.append(1)

    kb.adjust(*sizes)
    await callback.message.edit_text(
        "📝 <b>Выбери подход для редактирования:</b>",
        reply_markup=kb.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("num:"))
async def cb_numeric_input(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    if "session_id" not in data:
        return await callback.answer("Устаревшая кнопка. Открой упражнение заново.", show_alert=True)
    action = callback.data.split(":")[1]
    data = await state.get_data()
    current_val = data.get("temp_num", "")

    if action == "add":
        digit = callback.data.split(":")[2]
        if len(current_val) < 3:
            current_val += digit
            await state.update_data(temp_num=current_val)

    elif action == "del":
        current_val = current_val[:-1]
        await state.update_data(temp_num=current_val)

    elif action == "done":
        if not current_val:
            return await callback.answer("Введи хотя бы одну цифру!",
                                         show_alert=True)

        reps = int(current_val)
        await state.update_data(custom_reps=reps, temp_weight="")
        await state.set_state(TrainingFSM.entering_weight)

        ex = await session.get(Exercise, data["ex_id"])

        await callback.message.edit_text(
            _weight_entry_text(ex, reps, data),
            reply_markup=weight_keypad_kb()
        )
        await callback.answer()
        return

    # Выводим с названием упражнения
    ex = await session.get(Exercise, data["ex_id"])
    ex_name = ex.name if ex else "Упражнение"

    await callback.message.edit_text(
        f"🏋️ <b>{ex_name}</b>\n\n"
        f"Ввод повторов: <code>{current_val or '0'}</code>\n\n"
        f"<i>Выбери цифры на панели ниже или нажми «Готово»</i>",
        reply_markup=numeric_keypad_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("w_num:"))
async def cb_weight_input(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    if "session_id" not in data:
        return await callback.answer("Устаревшая кнопка. Открой упражнение заново.", show_alert=True)
    action = callback.data.split(":")[1]
    data = await state.get_data()
    current_val = data.get("temp_weight", "")
    reps = data.get("custom_reps")

    if action == "add":
        digit = callback.data.split(":")[2]
        if len(current_val) < 5:
            current_val += digit
            await state.update_data(temp_weight=current_val)

    elif action == "del":
        current_val = current_val[:-1]
        await state.update_data(temp_weight=current_val)

    elif action == "text":
        await callback.message.edit_text(
            "📝 Напиши вес текстом (например «Своё тело», «Резинка») или отправь число:",
            reply_markup=text_weight_kb()
        )
        await callback.answer()
        return

    elif action == "back":
        await state.set_state(TrainingFSM.entering_reps)
        await callback.message.edit_text(
            f"<b>Ввод повторов</b>\n\nТекущее значение: <b>{reps}</b>",
            reply_markup=numeric_keypad_kb()
        )
        await callback.answer()
        return

    elif action == "done":
        if not current_val:
            return await callback.answer(
                "Введи вес или нажми 'Ввести текстом'", show_alert=True)

        weight_str = current_val.replace(",", ".")

        from datetime import datetime, timezone
        from app.db.models import SessionSet

        session_id = data["session_id"]
        ex_id = data["ex_id"]
        editing_set_id = data.get("editing_set_id")

        if editing_set_id:
            target = await session.get(SessionSet, editing_set_id)
            target.reps = reps
            target.weight_kg = weight_str
            target.completed_at = datetime.now(timezone.utc)
            await session.commit()
        else:
            sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
            new_set = SessionSet(
                session_id=session_id, exercise_id=ex_id,
                set_number=next_set_num(sets),
                reps=reps, weight_kg=weight_str, completed=True,
                completed_at=datetime.now(timezone.utc)
            )
            session.add(new_set)
            await session.commit()

            # C2.6: мгновенное уведомление о рекорде
            await _check_and_notify_pr(callback.message, session, session_id,
                                       ex_id, new_set)

        await state.clear()

        result = await render_approach_screen(session, session_id, ex_id)
        if result:
            text, kb = result
            await callback.message.edit_text(text, reply_markup=kb)
        else:
            ws = await session.get(WorkoutSession, session_id)
            text, kb = await render_list(session, session_id, ws.user_id)
            await callback.message.edit_text(text, reply_markup=kb)

        await callback.answer()
        return

    # Выводим с названием упражнения
    ex = await session.get(Exercise, data["ex_id"])
    ex_name = ex.name if ex else "Упражнение"

    await callback.message.edit_text(
        f"🏋️ <b>{ex_name}</b>\n\n"
        f"Повторов: <b>{reps}</b>.\n"
        f"Ввод веса: <code>{current_val or '0'}</code>\n\n"
        f"<i>Выбери цифры на панели ниже или нажми «Готово»</i>",
        reply_markup=weight_keypad_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "w_num:back_from_text")
async def cb_back_from_text(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    if "session_id" not in data:
        return await callback.answer("Устаревшая кнопка.", show_alert=True)
    data = await state.get_data()
    current_val = data.get("temp_weight", "")
    reps = data.get("custom_reps")
    ex_id = data.get("ex_id")

    ex = await session.get(Exercise, ex_id)
    ex_name = ex.name if ex else "Упражнение"

    await state.set_state(TrainingFSM.entering_weight)

    await callback.message.edit_text(
        f"🏋️ <b>{ex_name}</b>\n\n"
        f"Повторов: <b>{reps}</b>.\n"
        f"Ввод веса: <code>{current_val or '0'}</code>\n\n"
        f"<i>Выбери цифры на панели ниже или нажми «Готово»</i>",
        reply_markup=weight_keypad_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t_num:"))
async def cb_time_input(callback: CallbackQuery, state: FSMContext,
                        session: AsyncSession):
    """Ввод времени кардио через цифровую клавиатуру."""
    action = callback.data.split(":")[1]
    data = await state.get_data()

    if "session_id" not in data:
        return await callback.answer(
            "Устаревшая кнопка. Открой упражнение заново.", show_alert=True)

    current_val = data.get("temp_num",
                           "")  # используем тот же буфер, что и для повторов

    if action == "add":
        digit = callback.data.split(":")[2]
        if len(current_val) < 5:
            current_val += digit
            await state.update_data(temp_num=current_val)
    elif action == "del":
        current_val = current_val[:-1]
        await state.update_data(temp_num=current_val)
    elif action == "text":
        await callback.message.edit_text(
            "📝 Напиши время текстом (например «30» или «1.5»):",
            reply_markup=cancel_kb()
        )
        await callback.answer()
        return
    elif action == "done":
        if not current_val:
            return await callback.answer("Введи время!", show_alert=True)

        from app.utils import parse_minutes_to_seconds
        seconds = parse_minutes_to_seconds(current_val)
        if seconds is None:
            return await callback.answer("Некорректное время", show_alert=True)

        session_id = data["session_id"]
        ex_id = data["ex_id"]
        editing_set_id = data.get("editing_set_id")

        from datetime import datetime, timezone
        from app.db.models import SessionSet
        from app.db.queries import get_sets_grouped
        from app.services.training_service import next_set_num, \
            render_approach_screen, render_list

        if editing_set_id:
            target = await session.get(SessionSet, editing_set_id)
            target.duration_seconds = seconds
            target.completed_at = datetime.now(timezone.utc)
            await session.commit()
        else:
            sets = (await get_sets_grouped(session, session_id)).get(ex_id, [])
            new_set = SessionSet(
                session_id=session_id, exercise_id=ex_id,
                set_number=next_set_num(sets),
                duration_seconds=seconds, completed=True,
                completed_at=datetime.now(timezone.utc)
            )
            session.add(new_set)
            await session.commit()

            # C2.6: мгновенное уведомление о рекорде по времени (кардио)
            await _check_and_notify_pr(callback.message, session, session_id,
                                       ex_id, new_set)

        await state.clear()

        result = await render_approach_screen(session, session_id, ex_id)
        if result:
            text, kb = result
            await callback.message.edit_text(text, reply_markup=kb)
        else:
            ws = await session.get(WorkoutSession, session_id)
            text, kb = await render_list(session, session_id, ws.user_id)
            await callback.message.edit_text(text, reply_markup=kb)

        await callback.answer()
        return

    ex = await session.get(Exercise, data["ex_id"])
    ex_name = ex.name if ex else "Кардио"

    await callback.message.edit_text(
        f"🏃 <b>{ex_name}</b>\n\n"
        f"Ввод времени: <code>{current_val or '0'}</code>\n\n"
        f"<i>Выбери цифры на панели ниже или нажми «Готово»</i>",
        reply_markup=weight_keypad_kb(prefix="t_num")
    )
    await callback.answer()


# ─── C2.6: мгновенные уведомления о рекордах ──────────────────────


async def _auto_delete_pr_message(msg, delay: int = 15):
    """Удаляет сообщение о рекорде через delay секунд (как у таймеров)."""
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass  # сообщение уже удалено пользователем или протухло


@router.callback_query(F.data == "pr_dismiss")
async def cb_pr_dismiss(callback: CallbackQuery):
    """Кнопка «✅ Понятно» — удалить сообщение о рекорде сразу."""
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


async def _build_pr_notification(session: AsyncSession,
                                 session_id: int, ex_id: int,
                                 new_set: SessionSet) -> str | None:
    """Определяет, рекорд ли новый подход, и возвращает текст сообщения.

    НЕ отправляет — отправка отдельным шагом (_send_pr_notification),
    чтобы контролировать порядок сообщений в чате (экран первым,
    рекорд — последним).

    Работает для силовых (вес / повторы) и кардио (время).
    """
    from app.services.pr_service import (
        build_pr_message, classify_duration_pr, raw_session_pr_events,
        session_pr_events,
    )

    ex = await session.get(Exercise, ex_id)
    ws = await session.get(WorkoutSession, session_id)
    if not ex or not ws:
        return None

    all_groups = await get_sets_grouped(session, session_id)

    # История того же упражнения (по name+kind) ИСКЛЮЧАЯ текущую сессию:
    # рекорд — только относительно прошлых тренировок
    history = await get_exercise_history(session, ws.user_id, ex.name, ex.kind)
    prior = [s for s in history
             if not s.session or s.session.id != session_id]

    if ex.kind == "strength":
        current = [s for s in all_groups.get(ex_id, [])
                   if s.completed and s.weight_kg and s.reps]
        # Сообщение шлём, только если подход — лучший в СВОЁМ упражнении
        # (дедуп пирамиды: из 60/70/80 сообщения идут на каждом шаге,
        # но в итогах тренировки останется только 80)
        kind = next((k for s, k in session_pr_events(current, prior)
                     if s.id == new_set.id), None)
        ex_events = raw_session_pr_events(current, prior)
    else:
        # Кардио: один подход = одно упражнение; рекорд по времени
        current = [s for s in all_groups.get(ex_id, [])
                   if s.completed and s.duration_seconds]
        kind = classify_duration_pr(new_set, prior)
        if kind and any(s.id != new_set.id and
                        s.duration_seconds >= new_set.duration_seconds
                        for s in current):
            kind = None  # в сессии уже был такой или лучший — сообщение уходило
        ex_events = [(s, "duration") for s in current
                     if classify_duration_pr(s, prior)]

    if kind is None:
        return None

    # Сквозной номер рекорда по тренировке: собираем «сырые» PR всех
    # упражнений сессии (силовые + кардио) и сортируем по времени
    session_events = []
    for eid, sets in all_groups.items():
        e = await session.get(Exercise, eid)
        if not e:
            continue
        hist = await get_exercise_history(session, ws.user_id,
                                          e.name, e.kind)
        pr = [s for s in hist
              if not s.session or s.session.id != session_id]
        if e.kind == "strength":
            str_sets = [s for s in sets
                        if s.completed and s.weight_kg and s.reps]
            session_events.extend(raw_session_pr_events(str_sets, pr))
        else:
            for s in sets:
                if s.completed and s.duration_seconds and \
                        classify_duration_pr(s, pr):
                    session_events.append((s, "duration"))

    session_events.sort(
        key=lambda x: (x[0].completed_at.timestamp()
                       if x[0].completed_at else 0,
                       x[0].id or 0))

    ex_ordinal = next(
        (i for i, (s, _) in enumerate(ex_events, start=1)
         if s.id == new_set.id), None)
    session_ordinal = next(
        (i for i, (s, _) in enumerate(session_events, start=1)
         if s.id == new_set.id), None)
    if ex_ordinal is None or session_ordinal is None:
        return None

    return build_pr_message(ex.name, new_set, kind, prior,
                            ex_ordinal, session_ordinal)


async def _send_pr_notification(target, text: str):
    """Шлёт сообщение о рекорде с кнопкой «✅ Понятно» и автоудалением."""
    msg = await target.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Понятно",
                                 callback_data="pr_dismiss")
        ]]),
    )
    asyncio.create_task(_auto_delete_pr_message(msg))


async def _check_and_notify_pr(target, session: AsyncSession,
                               session_id: int, ex_id: int,
                               new_set: SessionSet):
    """Определить рекорд и сразу отправить (для callback-сценариев,
    где экран обновляется через edit_text — новое сообщение и так
    окажется ниже)."""
    text = await _build_pr_notification(session, session_id, ex_id, new_set)
    if text:
        await _send_pr_notification(target, text)
