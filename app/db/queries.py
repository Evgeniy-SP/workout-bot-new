"""Запросы к БД, специфичные для тренировок.

Вынесены отдельно от handlers/, чтобы не засорять логику.
"""
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Exercise, MonthlyChallengeResult, SessionExerciseNote, SessionSet, SessionSkippedExercise,
    WorkoutSession, WorkoutType,
)
from app.utils import to_msk


async def get_active_session(session: AsyncSession,
                             user_id: int) -> WorkoutSession | None:
    """Сессия без finished_at = активная. Ищем только среди неархивных тренировок."""
    from app.db.models import WorkoutType

    result = await session.execute(
        select(WorkoutSession)
        .join(WorkoutType,
              WorkoutType.id == WorkoutSession.workout_type_id)  # <--- ДЖОИН
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_(None),
            WorkoutType.is_archived == False,  # <--- ПРОВЕРКА НА АРХИВ
        )
        .order_by(WorkoutSession.started_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_sets_grouped(session: AsyncSession, session_id: int) -> dict[int, list[SessionSet]]:
    """Все подходы сессии, сгруппированные по exercise_id."""
    result = await session.execute(
        select(SessionSet)
        .where(SessionSet.session_id == session_id)
        .order_by(SessionSet.exercise_id, SessionSet.set_number)
    )
    grouped: dict[int, list[SessionSet]] = defaultdict(list)
    for s in result.scalars():
        grouped[s.exercise_id].append(s)
    return dict(grouped)


async def get_last_completed_set(
        session: AsyncSession, user_id: int, exercise_id: int,
        current_session_id: int | None = None
) -> SessionSet | None:
    """Последний успешно выполненный подход (исключая текущую сессию)."""
    stmt = (
        select(SessionSet)
        .join(WorkoutSession, WorkoutSession.id == SessionSet.session_id)
        .where(
            WorkoutSession.user_id == user_id,
            SessionSet.exercise_id == exercise_id,
            SessionSet.completed == True,
        )
    )

    if current_session_id:
        stmt = stmt.where(SessionSet.session_id != current_session_id)

    result = await session.execute(
        stmt.order_by(SessionSet.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def get_last_note_for_exercise(
    session: AsyncSession, exercise_id: int, last_session_id: int
) -> str | None:
    """Возвращает заметку из конкретной (последней) тренировки."""
    result = await session.execute(
        select(SessionExerciseNote.note)
        .where(
            SessionExerciseNote.exercise_id == exercise_id,
            SessionExerciseNote.session_id == last_session_id
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_last_workout_note(
        session: AsyncSession, user_id: int, workout_type_id: int,
        current_session_id: int
) -> str | None:
    """Возвращает заметку из ПРЕДЫДУЩЕЙ тренировки этого типа.

    Если в предыдущей тренировке заметки не было, возвращает None.
    """
    result = await session.execute(
        select(WorkoutSession.notes)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.workout_type_id == workout_type_id,
            WorkoutSession.id != current_session_id,
        )
        .order_by(WorkoutSession.started_at.desc())
        .limit(1)  # Берем ровно одну (предыдущую) тренировку
    )
    note = result.scalar_one_or_none()

    # Если заметка есть и она не пустая - возвращаем, иначе None
    return note if note else None


async def get_user_sessions(
        session: AsyncSession, user_id: int, offset: int, limit: int,
        wt_id: int | None = None,
) -> list[WorkoutSession]:
    """Получает список завершенных тренировок с пагинацией.

    wt_id — опциональный фильтр по типу тренировки (фильтры истории).
    """
    from sqlalchemy.orm import selectinload

    stmt = (
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None)
        )
        .options(
            selectinload(WorkoutSession.sets),
            selectinload(WorkoutSession.workout_type)
        )
        .order_by(WorkoutSession.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if wt_id:
        stmt = stmt.where(WorkoutSession.workout_type_id == wt_id)

    result = await session.execute(stmt)
    return list(result.scalars())


async def get_user_sessions_count(session: AsyncSession, user_id: int,
                                  wt_id: int | None = None) -> int:
    """Считает общее количество завершенных тренировок для пагинации."""
    from sqlalchemy import func

    stmt = select(func.count(WorkoutSession.id)).where(
        WorkoutSession.user_id == user_id,
        WorkoutSession.finished_at.is_not(None)
    )
    if wt_id:
        stmt = stmt.where(WorkoutSession.workout_type_id == wt_id)

    result = await session.scalar(stmt)
    return result or 0


async def get_workout_types_with_sessions(
        session: AsyncSession, user_id: int
) -> list[tuple]:
    """Типы тренировок с числом завершённых сессий — для фильтра истории.

    Возвращает [(WorkoutType, количество завершённых тренировок), ...]
    в алфавитном порядке. Архивные типы тоже включаем: у них остаётся
    история, которую пользователь может захотеть посмотреть.
    """
    from sqlalchemy import func
    from app.db.models import WorkoutType

    result = await session.execute(
        select(WorkoutType, func.count(WorkoutSession.id))
        .join(WorkoutSession,
              WorkoutSession.workout_type_id == WorkoutType.id)
        .where(
            WorkoutType.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
        )
        .group_by(WorkoutType.id)
        .order_by(WorkoutType.name)
    )
    return list(result.all())


async def get_session_details(session: AsyncSession,
                              session_id: int) -> WorkoutSession | None:
    """Загружает сессию со всеми подходами и упражнениями для детального просмотра."""
    from sqlalchemy.orm import selectinload
    from app.db.models import Exercise

    result = await session.execute(
        select(WorkoutSession)
        .where(WorkoutSession.id == session_id)
        .options(
            selectinload(WorkoutSession.workout_type),
            selectinload(WorkoutSession.sets).selectinload(SessionSet.exercise),
            selectinload(WorkoutSession.exercise_notes)
            .selectinload(SessionExerciseNote.exercise),
        )
    )
    return result.scalar_one_or_none()


async def get_exercise_history(
    session: AsyncSession, user_id: int, exercise_name: str, exercise_kind: str
) -> list:
    """Возвращает историю подходов по ИМЕНИ упражнения (собирает со всех типов тренировок)."""
    from sqlalchemy.orm import selectinload
    from app.db.models import WorkoutSession, Exercise

    result = await session.execute(
        select(SessionSet)
        .join(WorkoutSession, SessionSet.session_id == WorkoutSession.id)
        .join(Exercise, SessionSet.exercise_id == Exercise.id)
        .where(
            WorkoutSession.user_id == user_id,
            Exercise.name == exercise_name,
            Exercise.kind == exercise_kind,
            SessionSet.completed == True
        )
        .options(
            # Жадно загружаем сессию, тип тренировки и само упражнение
            selectinload(SessionSet.session).selectinload(WorkoutSession.workout_type),
            selectinload(SessionSet.exercise)
        )
        .order_by(WorkoutSession.started_at.asc())
    )
    return list(result.scalars())


async def get_months_with_sessions(session: AsyncSession, user_id: int
                                   ) -> list[tuple[int, int]]:
    """Список (год, месяц), в которых у пользователя есть завершённые тренировки.

    Отсортирован по возрастанию. Навигация аналитики листает ТОЛЬКО по
    этим месяцам: пустые месяцы и большие перерывы между тренировками
    пропускаются, а не прощёлкиваются по одному.
    """
    result = await session.execute(
        select(WorkoutSession.started_at)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None)
        )
    )
    months = {(to_msk(sa).year, to_msk(sa).month)
              for sa in result.scalars() if sa}
    return sorted(months)


# ─── Архив заметок ────────────────────────────────────────────────


async def get_exercises_with_notes(session: AsyncSession,
                                   user_id: int) -> list[tuple]:
    """Упражнения пользователя, у которых есть заметки, с количеством.

    Возвращает [(Exercise, количество заметок), ...] по алфавиту.
    """
    result = await session.execute(
        select(Exercise, func.count(SessionExerciseNote.id))
        .join(SessionExerciseNote,
              SessionExerciseNote.exercise_id == Exercise.id)
        .join(WorkoutSession,
              WorkoutSession.id == SessionExerciseNote.session_id)
        .where(WorkoutSession.user_id == user_id)
        .group_by(Exercise.id)
        .order_by(Exercise.name)
    )
    return list(result.all())


async def get_exercise_notes(session: AsyncSession, user_id: int,
                             exercise_id: int, limit: int = 10,
                             offset: int = 0) -> list[tuple]:
    """Заметки к упражнению (от новых к старым) с датами сессий.

    Возвращает [(SessionExerciseNote, WorkoutSession), ...].
    """
    result = await session.execute(
        select(SessionExerciseNote, WorkoutSession)
        .join(WorkoutSession,
              WorkoutSession.id == SessionExerciseNote.session_id)
        .where(
            WorkoutSession.user_id == user_id,
            SessionExerciseNote.exercise_id == exercise_id,
        )
        .order_by(WorkoutSession.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.all())


async def get_session_exercise_note(session: AsyncSession, session_id: int,
                                    exercise_id: int) -> str | None:
    """Заметка к упражнению в КОНКРЕТНОЙ тренировке (None, если не писали)."""
    result = await session.execute(
        select(SessionExerciseNote.note)
        .where(SessionExerciseNote.session_id == session_id,
               SessionExerciseNote.exercise_id == exercise_id)
        .order_by(SessionExerciseNote.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def count_exercise_notes(session: AsyncSession, user_id: int,
                               exercise_id: int) -> int:
    """Сколько заметок у упражнения (для пагинации ленты)."""
    result = await session.scalar(
        select(func.count(SessionExerciseNote.id))
        .join(WorkoutSession,
              WorkoutSession.id == SessionExerciseNote.session_id)
        .where(
            WorkoutSession.user_id == user_id,
            SessionExerciseNote.exercise_id == exercise_id,
        )
    )
    return result or 0


async def get_sessions_with_notes(session: AsyncSession, user_id: int,
                                  limit: int = 8,
                                  offset: int = 0) -> list[WorkoutSession]:
    """Завершённые тренировки с непустыми заметками (от новых к старым)."""
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
            WorkoutSession.notes.is_not(None),
            WorkoutSession.notes != "",
        )
        .options(selectinload(WorkoutSession.workout_type))
        .order_by(WorkoutSession.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars())


async def count_sessions_with_notes(session: AsyncSession,
                                    user_id: int) -> int:
    """Сколько тренировок с заметками (для пагинации)."""
    result = await session.scalar(
        select(func.count(WorkoutSession.id))
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
            WorkoutSession.notes.is_not(None),
            WorkoutSession.notes != "",
        )
    )
    return result or 0


async def search_notes(session: AsyncSession, user_id: int, query: str,
                       limit: int = 20) -> list[tuple]:
    """Регистронезависимый поиск по всем заметкам.

    Возвращает [(дата сессии, вид, название, текст, id сессии), ...]
    от новых к старым. Вид: 'exercise' (название = имя упражнения) или
    'workout' (название = тип тренировки).

    Фильтрация в Python через casefold(): корректно работает с кириллицей
    на любой БД (SQLite lower() умеет только ASCII). Объёмы заметок в
    личном боте — сотни строк, фильтрация мгновенная.
    """
    needle = query.strip().casefold()

    ex_stmt = (
        select(
            WorkoutSession.started_at,
            Exercise.name,
            SessionExerciseNote.note,
            WorkoutSession.id,
        )
        .select_from(SessionExerciseNote)
        .join(WorkoutSession,
              WorkoutSession.id == SessionExerciseNote.session_id)
        .join(Exercise, Exercise.id == SessionExerciseNote.exercise_id)
        .where(WorkoutSession.user_id == user_id)
    )
    ws_stmt = (
        select(
            WorkoutSession.started_at,
            WorkoutType.name,
            WorkoutSession.notes,
            WorkoutSession.id,
        )
        .select_from(WorkoutSession)
        .join(WorkoutType,
              WorkoutType.id == WorkoutSession.workout_type_id)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
            WorkoutSession.notes.is_not(None),
            WorkoutSession.notes != "",
        )
    )

    ex_rows = list((await session.execute(ex_stmt)).all())
    ws_rows = list((await session.execute(ws_stmt)).all())

    hits = []
    for date_s, name, text, sess_id in ex_rows:
        if needle in text.casefold():
            hits.append((date_s, "exercise", name, text, sess_id))
    for date_s, name, text, sess_id in ws_rows:
        if needle in text.casefold():
            hits.append((date_s, "workout", name, text, sess_id))

    hits.sort(key=lambda r: r[0], reverse=True)
    return hits[:limit]


async def get_sessions_in_range(session: AsyncSession, user_id: int,
                                start, end) -> list[WorkoutSession]:
    """Завершённые сессии пользователя за период [start, end) с подходами."""
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
            WorkoutSession.started_at >= start,
            WorkoutSession.started_at < end,
        )
        .options(
            selectinload(WorkoutSession.sets).selectinload(SessionSet.exercise),
            selectinload(WorkoutSession.workout_type),
        )
        .order_by(WorkoutSession.started_at)
    )
    return list(result.scalars())


async def get_finished_sessions_with_sets(
    session: AsyncSession, user_id: int,
) -> list[WorkoutSession]:
    """История завершённых тренировок с подходами для доски рекордов."""
    from sqlalchemy.orm import selectinload

    result = await session.scalars(
        select(WorkoutSession)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
        )
        .options(selectinload(WorkoutSession.sets))
    )
    return list(result)


# ─── PR и ачивки (C2.6) ───────────────────────────────────────────


async def get_all_completed_sets(session: AsyncSession,
                                 user_id: int) -> list[SessionSet]:
    """Все завершённые подходы пользователя по хронологии.

    Нужен для подсчёта суммарного числа PR и ачивок «+20% к весу»:
    объёмы в личном боте — сотни подходов, считается мгновенно.
    """
    from sqlalchemy.orm import selectinload

    result = await session.execute(
        select(SessionSet)
        .join(WorkoutSession, SessionSet.session_id == WorkoutSession.id)
        .where(
            WorkoutSession.user_id == user_id,
            SessionSet.completed == True,
        )
        .options(
            selectinload(SessionSet.session),
            selectinload(SessionSet.exercise),
        )
        .order_by(WorkoutSession.started_at)
    )
    return list(result.scalars())


async def count_finished_sessions(session: AsyncSession,
                                  user_id: int) -> int:
    """Сколько тренировок завершено (для ачивок-серий)."""
    result = await session.scalar(
        select(func.count(WorkoutSession.id)).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
        )
    )
    return result or 0


async def get_finished_session_dates(session: AsyncSession,
                                     user_id: int) -> list:
    """Даты завершённых тренировок (дней по МСК) — для серий без пропусков."""
    result = await session.execute(
        select(WorkoutSession.started_at).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_not(None),
        )
    )
    return [to_msk(sa).date() for sa in result.scalars() if sa]


async def count_exercises_with_history(session: AsyncSession,
                                       user_id: int) -> int:
    """Сколько разных упражнений (по name+kind) пользователь выполнял."""
    result = await session.scalar(
        select(func.count())
        .select_from(
            select(Exercise.name, Exercise.kind)
            .join(SessionSet, SessionSet.exercise_id == Exercise.id)
            .join(WorkoutSession,
                  WorkoutSession.id == SessionSet.session_id)
            .where(
                WorkoutSession.user_id == user_id,
                SessionSet.completed == True,
            )
            .group_by(Exercise.name, Exercise.kind)
            .subquery()
        )
    )
    return result or 0


async def get_user_achievement_codes(session: AsyncSession,
                                     user_id: int) -> set[str]:
    """Коды уже полученных ачивок — чтобы не выдавать дважды."""
    from app.db.models import Achievement

    result = await session.execute(
        select(Achievement.code).where(Achievement.user_id == user_id)
    )
    return set(result.scalars())


async def add_achievement(session: AsyncSession,
                          user_id: int, code: str, *,
                          skip_existing: bool = False) -> bool:
    """Записывает полученную ачивку.

    UNIQUE(user_id, code) страхует от дублей при повторных проверках.
    """
    from app.db.models import Achievement

    if skip_existing:
        try:
            async with session.begin_nested():
                session.add(Achievement(user_id=user_id, code=code))
        except IntegrityError:
            if code not in await get_user_achievement_codes(session, user_id):
                raise
            return False
    else:
        session.add(Achievement(user_id=user_id, code=code))
    await session.commit()
    return True


async def get_challenge_archive_months(session: AsyncSession, user_id: int) -> list[date]:
    result = await session.scalars(
        select(MonthlyChallengeResult.period_start)
        .where(MonthlyChallengeResult.user_id == user_id)
        .distinct().order_by(MonthlyChallengeResult.period_start),
    )
    return list(result)


async def get_challenge_archive_results(
    session: AsyncSession, user_id: int, period_start: date,
) -> list[MonthlyChallengeResult]:
    result = await session.scalars(
        select(MonthlyChallengeResult).where(
            MonthlyChallengeResult.user_id == user_id,
            MonthlyChallengeResult.period_start == period_start,
        ).order_by(MonthlyChallengeResult.goal_key),
    )
    return list(result)


# ─── CSV-экспорт (C2.6 Блок 4) ────────────────────────────────────


async def get_user_exercise(session: AsyncSession, user_id: int,
                            exercise_id: int) -> Exercise | None:
    """Упражнение пользователя по id (проверка владельца через workout_type)."""
    result = await session.execute(
        select(Exercise)
        .join(WorkoutType, Exercise.workout_type_id == WorkoutType.id)
        .where(Exercise.id == exercise_id, WorkoutType.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_session_skipped_exercise_names(session: AsyncSession,
                                             session_id: int) -> list[str]:
    """Названия упражнений, завершённых досрочно (пропущенных) в сессии.

    Нужны для CSV-экспорта тренировки: «выполнено X из Y» считает и их.
    """
    result = await session.execute(
        select(Exercise.name)
        .join(SessionSkippedExercise,
              SessionSkippedExercise.exercise_id == Exercise.id)
        .where(SessionSkippedExercise.session_id == session_id)
        .order_by(Exercise.sort_order)
    )
    return list(result.scalars())
