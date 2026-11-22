"""Тесты запросов архива заметок (SQLite в памяти)."""
import pytest
from datetime import datetime, timezone, timedelta

from app.db.models import (
    Exercise, SessionExerciseNote, User, WorkoutSession, WorkoutType,
)
from app.db.queries import (
    count_exercise_notes, count_sessions_with_notes,
    get_exercise_notes, get_exercises_with_notes, get_sessions_with_notes,
    get_session_exercise_note, search_notes,
)


async def _make_env(session, tg_id=1):
    """Пользователь + тип тренировки + упражнение. Возвращает (user, wt, ex)."""
    user = User(tg_id=tg_id, full_name="Test")
    wt = WorkoutType(user=user, name="Верх")
    ex = Exercise(workout_type=wt, name="Жим лёжа", kind="strength")
    session.add_all([user, wt, ex])
    await session.commit()
    return user, wt, ex


async def _add_session(session, user_id, wt_id, days_ago, notes=None):
    now = datetime.now(timezone.utc)
    ws = WorkoutSession(
        user_id=user_id, workout_type_id=wt_id,
        started_at=now - timedelta(days=days_ago),
        finished_at=now - timedelta(days=days_ago) + timedelta(hours=1),
        notes=notes)
    session.add(ws)
    await session.commit()
    return ws


# ─── get_exercises_with_notes ────────────────────────────────────
@pytest.mark.asyncio
async def test_exercises_with_notes_counts_and_filters_user(session):
    user, wt, ex = await _make_env(session)
    other = User(tg_id=2, full_name="Other")
    wt2 = WorkoutType(user=other, name="Чужая")
    ex2 = Exercise(workout_type=wt2, name="Чужое", kind="strength")
    session.add_all([other, wt2, ex2])
    await session.commit()

    ws1 = await _add_session(session, user.id, wt.id, 1)
    ws2 = await _add_session(session, user.id, wt.id, 3)
    ws3 = await _add_session(session, user.id, wt.id, 5)
    await _add_session(session, other.id, wt2.id, 1)

    session.add_all([
        SessionExerciseNote(session_id=ws1.id, exercise_id=ex.id, note="a"),
        SessionExerciseNote(session_id=ws2.id, exercise_id=ex.id, note="b"),
        SessionExerciseNote(session_id=ws3.id, exercise_id=ex.id, note="c"),
    ])
    # Заметка чужого пользователя — не должна попасть
    foreign_ws = await _add_session(session, other.id, wt2.id, 2)
    session.add(SessionExerciseNote(session_id=foreign_ws.id,
                                    exercise_id=ex2.id, note="x"))
    await session.commit()

    rows = await get_exercises_with_notes(session, user.id)
    assert [(e.name, cnt) for e, cnt in rows] == [("Жим лёжа", 3)]


# ─── get_exercise_notes / count ──────────────────────────────────
@pytest.mark.asyncio
async def test_exercise_notes_order_and_pagination(session):
    user, wt, ex = await _make_env(session)
    ws_old = await _add_session(session, user.id, wt.id, 10)
    ws_new = await _add_session(session, user.id, wt.id, 2)
    ws_mid = await _add_session(session, user.id, wt.id, 5)

    for ws, text in [(ws_old, "старая"), (ws_new, "новая"),
                     (ws_mid, "средняя")]:
        session.add(SessionExerciseNote(session_id=ws.id,
                                        exercise_id=ex.id, note=text))
    await session.commit()

    rows = await get_exercise_notes(session, user.id, ex.id)
    assert [note.note for note, _ in rows] == ["новая", "средняя", "старая"]
    assert rows[0][1].id == ws_new.id  # сессия подтянута

    page1 = await get_exercise_notes(session, user.id, ex.id,
                                     limit=2, offset=0)
    assert len(page1) == 2
    assert await count_exercise_notes(session, user.id, ex.id) == 3


# ─── get_sessions_with_notes / count ─────────────────────────────
@pytest.mark.asyncio
async def test_sessions_with_notes_skips_empty(session):
    user, wt, ex = await _make_env(session)
    await _add_session(session, user.id, wt.id, 1, notes="болит плечо")
    await _add_session(session, user.id, wt.id, 3, notes="")
    await _add_session(session, user.id, wt.id, 5, notes=None)
    await _add_session(session, user.id, wt.id, 7, notes="всё ок")
    # Незавершённая с заметкой — не должна попасть
    now = datetime.now(timezone.utc)
    session.add(WorkoutSession(user_id=user.id, workout_type_id=wt.id,
                               started_at=now - timedelta(days=9),
                               finished_at=None, notes="черновик"))
    await session.commit()

    rows = await get_sessions_with_notes(session, user.id)
    assert len(rows) == 2
    assert [ws.notes for ws in rows] == ["болит плечо", "всё ок"]
    assert await count_sessions_with_notes(session, user.id) == 2


# ─── search_notes ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_search_notes_finds_both_kinds_case_insensitive(session):
    user, wt, ex = await _make_env(session)
    ws = await _add_session(session, user.id, wt.id, 2, notes="тянущая БОЛЬ в плече")
    ws2 = await _add_session(session, user.id, wt.id, 5)
    session.add(SessionExerciseNote(session_id=ws2.id, exercise_id=ex.id,
                                    note="лёгкая боль после жима"))
    await session.commit()

    rows = await search_notes(session, user.id, "Боль")
    assert len(rows) == 2
    kinds = sorted(r[1] for r in rows)
    assert kinds == ["exercise", "workout"]
    # Новые сверху: сессия 2 дня назад раньше, чем 5 дней назад
    assert rows[0][0] > rows[1][0]
    assert rows[0][2] == "Верх"  # workout-заметка: название = тип тренировки


@pytest.mark.asyncio
async def test_search_notes_no_foreign_hits(session):
    user, wt, ex = await _make_env(session)
    other, wt2, ex2 = await _make_env(session, tg_id=2)
    ws = await _add_session(session, other.id, wt2.id, 1, notes="секрет")
    session.add(SessionExerciseNote(session_id=ws.id, exercise_id=ex2.id,
                                    note="секрет"))
    await session.commit()

    assert await search_notes(session, user.id, "секрет") == []


@pytest.mark.asyncio
async def test_get_session_exercise_note(session):
    """Заметка ищется точно по паре (тренировка, упражнение)."""
    user, wt, ex = await _make_env(session)
    ws_a = await _add_session(session, user.id, wt.id, 2)
    ws_b = await _add_session(session, user.id, wt.id, 1)
    session.add_all([
        SessionExerciseNote(session_id=ws_a.id, exercise_id=ex.id,
                            note="из тренировки A"),
        SessionExerciseNote(session_id=ws_b.id, exercise_id=ex.id,
                            note="из тренировки B"),
    ])
    await session.commit()

    assert await get_session_exercise_note(session, ws_a.id, ex.id) == \
        "из тренировки A"
    assert await get_session_exercise_note(session, ws_b.id, ex.id) == \
        "из тренировки B"
    # У другой тренировки такой заметки нет
    ws_c = await _add_session(session, user.id, wt.id, 0)
    assert await get_session_exercise_note(session, ws_c.id, ex.id) is None
