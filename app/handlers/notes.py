"""Хэндлеры раздела «📒 Заметки» — архив заметок к упражнениям и тренировкам.

Заметки пишутся во время тренировки, а смотреть их раньше можно было
только выходя на конкретную тренировку в истории. Здесь единая точка:
список упражнений с заметками, лента по каждому, заметки к тренировкам
и регистронезависимый поиск по тексту.
"""
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Exercise, WorkoutSession
from app.db.queries import (
    count_exercise_notes, count_sessions_with_notes,
    get_exercise_notes, get_exercises_with_notes, get_sessions_with_notes,
    search_notes,
)
from app.keyboards.workout_kb import (
    NOTES_PER_EX_PAGE, NOTES_PER_PAGE, NOTES_WS_PER_PAGE,
    notes_exercise_kb, notes_main_kb, notes_search_kb, notes_search_results_kb,
    notes_workouts_kb, notes_workout_view_kb,
)
from app.services.training_service import get_user
from app.states import NotesSearch
from app.utils import fmt_msk_date

router = Router()


def _escape(text: str) -> str:
    """Экранирует служебные HTML-символы aiogram."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


async def _render_notes_main(session: AsyncSession, user_id: int, page: int,
                             message: Message, is_edit: bool) -> None:
    """Главный экран архива: упражнения с заметками (страница page)."""
    limit = NOTES_PER_PAGE
    offset = page * limit

    exercises = await get_exercises_with_notes(session, user_id)

    if not exercises:
        text = ("📒 <b>Заметки</b>\n\n"
                "Заметок пока нет. Добавить можно во время тренировки: "
                "к упражнению или ко всей тренировке целиком.")
        kb = notes_search_kb()
        if is_edit:
            await message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        return

    total = len(exercises)
    has_next = (offset + limit) < total
    page_items = exercises[offset:offset + limit]
    total_pages = (total - 1) // limit + 1

    text = (f"📒 <b>Заметки</b> — упражнения ({total})\n\n"
            f"Страница {page + 1} из {total_pages}\n"
            f"👇 Выбери упражнение, чтобы посмотреть его заметки:")

    kb = notes_main_kb(page_items, page, has_next)
    if is_edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(F.text == "📒 Заметки")
async def cmd_notes(message: Message, session: AsyncSession,
                    state: FSMContext):
    await state.clear()  # на случай, если нажали кнопку посреди поиска

    user = await get_user(session, message.from_user.id)
    if not user:
        return await message.answer("Сначала нажми /start")

    await _render_notes_main(session, user.id, 0, message, is_edit=False)


@router.callback_query(F.data.startswith("notes_main:"))
async def cb_notes_main(callback: CallbackQuery, session: AsyncSession):
    page = int(callback.data.split(":")[1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    await _render_notes_main(session, user.id, page, callback.message,
                             is_edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("notes_ex:"))
async def cb_notes_exercise(callback: CallbackQuery, session: AsyncSession):
    """Лента заметок по упражнению (страница page)."""
    parts = callback.data.split(":")
    exercise_id, page = int(parts[1]), int(parts[2])
    # «Адрес возврата»: если ленту открыли с экрана прогресса упражнения,
    # «Назад» ведёт в детали тренировки, а не в общий архив
    origin = ":".join(parts[3:]) if len(parts) > 3 else ""

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    ex = await session.get(Exercise, exercise_id)
    if not ex:
        return await callback.answer("Упражнение не найдено", show_alert=True)

    limit = NOTES_PER_EX_PAGE
    offset = page * limit

    total = await count_exercise_notes(session, user.id, exercise_id)
    if not total:
        return await callback.answer("Заметок по этому упражнению нет",
                                     show_alert=True)

    rows = await get_exercise_notes(session, user.id, exercise_id,
                                    limit=limit, offset=offset)
    has_next = (offset + limit) < total
    total_pages = (total - 1) // limit + 1

    lines = [f"📒 <b>Заметки: {ex.name}</b> ({total})\n",
             f"Страница {page + 1} из {total_pages}\n"
             "👇 Выбери заметку — открою тренировку, где она написана:"]
    items = [(ws.id,
              fmt_msk_date(ws.started_at) if ws.started_at else "—",
              note.note)
             for note, ws in rows]

    # Кнопка «📒 Заметки по упражнению» живёт на фото графика прогресса,
    # а у фото нет текста — edit_text на нём падает. Поэтому пробуем
    # отредактировать, а если сообщение не текстовое — шлём новым.
    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=notes_exercise_kb(exercise_id, page, has_next,
                                           items, origin=origin))
    except TelegramBadRequest:
        await callback.message.answer(
            "\n".join(lines),
            reply_markup=notes_exercise_kb(exercise_id, page, has_next,
                                           items, origin=origin))
    await callback.answer()


@router.callback_query(F.data.startswith("notes_ws:"))
async def cb_notes_workouts(callback: CallbackQuery, session: AsyncSession):
    """Список тренировок с заметками."""
    page = int(callback.data.split(":")[1])

    user = await get_user(session, callback.from_user.id)
    if not user:
        return await callback.answer("Сначала /start", show_alert=True)

    limit = NOTES_WS_PER_PAGE
    offset = page * limit

    total = await count_sessions_with_notes(session, user.id)
    if not total:
        return await callback.answer("Заметок к тренировкам нет",
                                     show_alert=True)

    sessions = await get_sessions_with_notes(session, user.id, limit=limit,
                                             offset=offset)
    has_next = (offset + limit) < total
    total_pages = (total - 1) // limit + 1

    text = (f"📋 <b>Заметки к тренировкам</b> ({total})\n\n"
            f"Страница {page + 1} из {total_pages}\n"
            f"👇 Выбери тренировку:")

    await callback.message.edit_text(
        text,
        reply_markup=notes_workouts_kb(sessions, page, has_next))
    await callback.answer()


@router.callback_query(F.data.startswith("notes_ws_view:"))
async def cb_notes_workout_view(callback: CallbackQuery,
                                session: AsyncSession):
    """Текст заметки к конкретной тренировке."""
    _, sess_str, page_str = callback.data.split(":")
    session_id, page = int(sess_str), int(page_str)

    # Eager-загрузка: без selectinload доступ к ws.workout_type уходил бы
    # в ленивую подгрузку и падал с MissingGreenlet в асинхронной сессии.
    result = await session.execute(
        select(WorkoutSession)
        .where(WorkoutSession.id == session_id)
        .options(selectinload(WorkoutSession.workout_type))
    )
    ws = result.scalar_one_or_none()

    if not ws:
        return await callback.answer("Тренировка не найдена", show_alert=True)

    user = await get_user(session, callback.from_user.id)
    if not user or ws.user_id != user.id:
        return await callback.answer("Сначала /start", show_alert=True)

    wt_name = ws.workout_type.name if ws.workout_type else "Тренировка"
    date_str = fmt_msk_date(ws.started_at) if ws.started_at else "—"

    text = (f"📋 <b>{wt_name}</b>\n"
            f"📅 {date_str}\n\n"
            f"{_escape(ws.notes or '—')}")

    await callback.message.edit_text(
        text, reply_markup=notes_workout_view_kb(session_id, page))
    await callback.answer()


@router.callback_query(F.data == "notes_search")
async def cb_notes_search(callback: CallbackQuery, state: FSMContext):
    """Старт поиска: переходим в FSM-состояние ожидания текста."""
    await state.clear()
    await state.set_state(NotesSearch.entering_query)
    await callback.message.edit_text(
        "🔍 <b>Поиск по заметкам</b>\n\n"
        "Пришли слово или фразу — найду заметки к упражнениям "
        "и тренировкам, где она встречается.\n\n"
        "Отмена: /cancel")
    await callback.answer()


@router.message(NotesSearch.entering_query, F.text)
async def msg_notes_search(message: Message, session: AsyncSession,
                           state: FSMContext):
    """Выполняет поиск по присланному тексту."""
    query = message.text.strip()

    if query == "/cancel":
        await state.clear()
        user = await get_user(session, message.from_user.id)
        if user:
            await _render_notes_main(session, user.id, 0, message,
                                     is_edit=False)
        return

    if len(query) < 2:
        return await message.answer(
            "Слишком короткий запрос — минимум 2 символа. "
            "Попробуй ещё раз или /cancel")

    user = await get_user(session, message.from_user.id)
    if not user:
        await state.clear()
        return await message.answer("Сначала нажми /start")

    rows = await search_notes(session, user.id, query)
    if not rows:
        text = (f"🔍 По запросу «{_escape(query)}» ничего не найдено.\n\n"
                f"Пришли другой запрос или /cancel")
        return await message.answer(text, reply_markup=notes_search_kb())

    await state.clear()

    items = [(sess_id, "🤸" if kind == "exercise" else "📋",
              title,
              fmt_msk_date(date_s) if date_s else "—",
              note_text)
             for date_s, kind, title, note_text, sess_id in rows]

    text = (f"🔍 <b>Результаты по запросу «{_escape(query)}»</b> ({len(rows)})\n\n"
            "👇 Выбери заметку — открою тренировку, где она написана:")

    await message.answer(text, reply_markup=notes_search_results_kb(items))
