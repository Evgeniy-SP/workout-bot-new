import pytest
from datetime import datetime, timezone, timedelta
from app.db.models import Exercise, SessionSet, User, WorkoutType, WorkoutSession
from app.db.queries import (
    get_last_completed_set, get_previous_finished_session, get_user_sessions, get_user_sessions_count,
    get_workout_types_with_sessions,
)


@pytest.mark.asyncio
async def test_warmup_does_not_replace_last_working_suggestion(session):
    user = User(tg_id=1, full_name="Test")
    workout = WorkoutType(user=user, name="Верх")
    exercise = Exercise(workout_type=workout, name="Жим лёжа")
    session.add(exercise)
    await session.flush()
    workout_session = WorkoutSession(user_id=user.id, workout_type_id=workout.id)
    session.add(workout_session)
    await session.flush()
    working = SessionSet(session_id=workout_session.id, exercise_id=exercise.id,
                         set_number=1, reps=5, weight_kg="70", completed=True)
    warmup = SessionSet(session_id=workout_session.id, exercise_id=exercise.id,
                        set_number=2, reps=20, weight_kg="Пустой гриф",
                        completed=True, is_warmup=True)
    session.add_all([working, warmup])
    await session.flush()

    assert await get_last_completed_set(session, user.id, exercise.id) is working
    assert await get_last_completed_set(session, user.id, exercise.id, is_warmup=True) is warmup


@pytest.mark.asyncio
async def test_previous_finished_session_uses_same_workout_and_history_order(session):
    user = User(tg_id=1, full_name="Test")
    other_user = User(tg_id=2, full_name="Other")
    workout = WorkoutType(user=user, name="Верх")
    other_workout = WorkoutType(user=user, name="Низ")
    session.add_all([workout, other_workout, other_user])
    await session.flush()

    start = datetime(2026, 11, 20, tzinfo=timezone.utc)
    earlier = start - timedelta(days=1)
    finished = start + timedelta(hours=1)
    oldest = WorkoutSession(user_id=user.id, workout_type_id=workout.id,
                            started_at=earlier, finished_at=finished)
    previous = WorkoutSession(user_id=user.id, workout_type_id=workout.id,
                              started_at=start, finished_at=finished)
    current = WorkoutSession(user_id=user.id, workout_type_id=workout.id,
                             started_at=start, finished_at=finished)
    session.add_all([
        oldest,
        WorkoutSession(user_id=user.id, workout_type_id=workout.id,
                       started_at=start),
        WorkoutSession(user_id=user.id, workout_type_id=other_workout.id,
                       started_at=start, finished_at=finished),
        WorkoutSession(user_id=other_user.id, workout_type_id=workout.id,
                       started_at=start, finished_at=finished),
        previous,
        current,
        WorkoutSession(user_id=user.id, workout_type_id=workout.id,
                       started_at=finished, finished_at=finished + timedelta(hours=1)),
    ])
    await session.flush()

    assert await get_previous_finished_session(session, current) is previous
    assert await get_previous_finished_session(session, previous) is oldest
    assert await get_previous_finished_session(session, oldest) is None


@pytest.mark.asyncio
async def test_get_user_sessions_empty(session):
    """Проверяем, что пустая история возвращает пустой список."""
    user = User(tg_id=1, full_name="Test")
    session.add(user)
    await session.commit()

    sessions = await get_user_sessions(session, user.id, 0, 5)
    count = await get_user_sessions_count(session, user.id)

    assert len(sessions) == 0
    assert count == 0


@pytest.mark.asyncio
async def test_get_user_sessions_pagination(session):
    """Проверяем, что пагинация работает корректно."""
    user = User(tg_id=1, full_name="Test")
    wt = WorkoutType(user=user, name="Test Workout")
    session.add_all([user, wt])
    await session.commit()

    # Создаем 7 завершенных тренировок
    now = datetime.now(timezone.utc)
    for i in range(7):
        ws = WorkoutSession(
            user_id=user.id,
            workout_type_id=wt.id,
            started_at=now - timedelta(days=i),
            finished_at=now - timedelta(days=i) + timedelta(hours=1)
        )
        session.add(ws)
    await session.commit()

    # Тест 1: Первая страница (должно быть 5)
    page1 = await get_user_sessions(session, user.id, 0, 5)
    assert len(page1) == 5
    # Самая свежая должна быть первой (i=0)
    assert page1[0].started_at.date() == now.date()

    # Тест 2: Вторая страница (должно быть 2)
    page2 = await get_user_sessions(session, user.id, 5, 5)
    assert len(page2) == 2

    # Тест 3: Общее количество
    count = await get_user_sessions_count(session, user.id)
    assert count == 7


@pytest.mark.asyncio
async def test_get_user_sessions_filter_by_workout_type(session):
    """Фильтр истории по типу тренировки: только сессии этого типа."""
    user = User(tg_id=1, full_name="Test")
    wt_a = WorkoutType(user=user, name="Верх")
    wt_b = WorkoutType(user=user, name="Низ")
    session.add_all([user, wt_a, wt_b])
    await session.commit()

    now = datetime.now(timezone.utc)
    for i in range(3):
        session.add(WorkoutSession(
            user_id=user.id, workout_type_id=wt_a.id,
            started_at=now - timedelta(days=i),
            finished_at=now - timedelta(days=i) + timedelta(hours=1)))
    session.add(WorkoutSession(
        user_id=user.id, workout_type_id=wt_b.id,
        started_at=now - timedelta(days=5),
        finished_at=now - timedelta(days=5) + timedelta(hours=1)))
    # Незавершённая сессия — в историю не должна попасть
    session.add(WorkoutSession(
        user_id=user.id, workout_type_id=wt_a.id,
        started_at=now - timedelta(days=2), finished_at=None))
    await session.commit()

    sessions_a = await get_user_sessions(session, user.id, 0, 5,
                                         wt_id=wt_a.id)
    assert len(sessions_a) == 3
    assert all(ws.workout_type_id == wt_a.id for ws in sessions_a)

    count_a = await get_user_sessions_count(session, user.id, wt_id=wt_a.id)
    assert count_a == 3

    # Без фильтра — все завершённые
    all_sessions = await get_user_sessions(session, user.id, 0, 10)
    assert len(all_sessions) == 4


@pytest.mark.asyncio
async def test_get_workout_types_with_sessions(session):
    """Только типы с завершёнными сессиями, с правильными счётчиками."""
    user = User(tg_id=1, full_name="Test")
    wt_a = WorkoutType(user=user, name="Верх")
    wt_b = WorkoutType(user=user, name="Низ")
    wt_c = WorkoutType(user=user, name="Пустой")  # сессий не будет
    session.add_all([user, wt_a, wt_b, wt_c])
    await session.commit()

    now = datetime.now(timezone.utc)
    for i in range(2):
        session.add(WorkoutSession(
            user_id=user.id, workout_type_id=wt_a.id,
            started_at=now - timedelta(days=i),
            finished_at=now - timedelta(days=i) + timedelta(hours=1)))
    session.add(WorkoutSession(
        user_id=user.id, workout_type_id=wt_b.id,
        started_at=now - timedelta(days=3),
        finished_at=now - timedelta(days=3) + timedelta(hours=1)))
    await session.commit()

    rows = await get_workout_types_with_sessions(session, user.id)
    assert [(wt.name, cnt) for wt, cnt in rows] == [("Верх", 2), ("Низ", 1)]
