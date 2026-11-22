"""Тесты таблицы achievements и запросов к ней (C2.6, Блок 3).

Используют реальную SQLite в памяти через фикстуру session из conftest."""
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Exercise, SessionSet, User, WorkoutSession, WorkoutType,
)
from app.db.queries import add_achievement, get_user_achievement_codes
from app.services.achievement_service import unlock_achievements


async def _make_user(session) -> User:
    user = User(tg_id=999001, full_name="Тест")
    session.add(user)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_add_and_get_codes(session):
    user = await _make_user(session)
    assert await get_user_achievement_codes(session, user.id) == set()

    await add_achievement(session, user.id, "workouts_10")
    await add_achievement(session, user.id, "pr_first")

    codes = await get_user_achievement_codes(session, user.id)
    assert codes == {"workouts_10", "pr_first"}


@pytest.mark.asyncio
async def test_duplicate_code_rejected(session):
    """UNIQUE(user_id, code) страхует от дублей на уровне БД."""
    user = await _make_user(session)
    await add_achievement(session, user.id, "workouts_10")

    with pytest.raises(IntegrityError):
        await add_achievement(session, user.id, "workouts_10")


@pytest.mark.asyncio
async def test_codes_isolated_between_users(session):
    u1 = await _make_user(session)
    u2 = User(tg_id=999002, full_name="Тест 2")
    session.add(u2)
    await session.commit()

    await add_achievement(session, u1.id, "streak_7")
    assert await get_user_achievement_codes(session, u2.id) == set()
    assert await get_user_achievement_codes(session, u1.id) == {"streak_7"}


# ─── unlock_achievements: сквозная проверка с реальной БД ─────────


@pytest.mark.asyncio
async def test_unlock_achievements_first_workout(session):
    """Первая завершённая тренировка с подходом -> ровно pr_first."""
    user = await _make_user(session)
    wt = WorkoutType(user_id=user.id, name="Тест", sort_order=0)
    session.add(wt)
    await session.commit()
    ex = Exercise(workout_type_id=wt.id, name="Жим", kind="strength",
                  sets=3, reps=10, sort_order=0)
    session.add(ex)
    await session.commit()
    ws = WorkoutSession(
        user_id=user.id, workout_type_id=wt.id,
        started_at=datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 11, 19, 11, 0, tzinfo=timezone.utc),
    )
    session.add(ws)
    await session.commit()
    session.add(SessionSet(session_id=ws.id, exercise_id=ex.id,
                           set_number=1, reps=10, weight_kg="50",
                           completed=True))
    await session.commit()

    new = await unlock_achievements(session, user.id)
    assert [c for c, _ in new] == ["pr_first"]
    # повторный вызов ничего не выдаёт (все условия уже записаны)
    assert await unlock_achievements(session, user.id) == []
    assert await get_user_achievement_codes(session, user.id) == {"pr_first"}


@pytest.mark.asyncio
async def test_december_challenge_counts_finished_sessions_in_msk_and_awards_once(session):
    user = await _make_user(session)
    workout_type = WorkoutType(user_id=user.id, name="Тест", sort_order=0)
    session.add(workout_type)
    await session.flush()

    start = datetime(2026, 11, 30, 21, tzinfo=timezone.utc)
    end = datetime(2026, 12, 31, 21, tzinfo=timezone.utc)
    session.add_all([
        WorkoutSession(user_id=user.id, workout_type_id=workout_type.id,
                       started_at=start, finished_at=start)
        for _ in range(11)
    ])
    session.add_all([
        WorkoutSession(user_id=user.id, workout_type_id=workout_type.id,
                       started_at=end, finished_at=end),
        WorkoutSession(user_id=user.id, workout_type_id=workout_type.id,
                       started_at=start),
    ])
    await session.commit()

    code = "monthly_12_2026_12"
    today = date(2027, 1, 1)
    assert (code, "") not in await unlock_achievements(session, user.id, today=today)

    session.add(WorkoutSession(user_id=user.id, workout_type_id=workout_type.id,
                               started_at=start, finished_at=start))
    await session.commit()
    assert (code, "") in await unlock_achievements(session, user.id, today=today)
    assert await unlock_achievements(session, user.id, today=today) == []
    assert code in await get_user_achievement_codes(session, user.id)
    assert not await add_achievement(session, user.id, code, skip_existing=True)


@pytest.mark.asyncio
async def test_december_pr_challenge_awards_at_third_distinct_exercise(session):
    user = await _make_user(session)
    workout_type = WorkoutType(user_id=user.id, name="Тест", sort_order=0)
    session.add(workout_type)
    await session.flush()
    start = datetime(2026, 12, 1, 10, tzinfo=timezone.utc)
    workout = WorkoutSession(user_id=user.id, workout_type_id=workout_type.id,
                             started_at=start, finished_at=start)
    session.add(workout)
    await session.flush()

    async def add_record(name, weight):
        exercise = Exercise(workout_type_id=workout_type.id, name=name,
                            kind="strength", sets=1, reps=8, sort_order=0)
        session.add(exercise)
        await session.flush()
        session.add(SessionSet(session_id=workout.id, exercise_id=exercise.id,
                               set_number=1, reps=8, weight_kg=weight, completed=True))
        await session.commit()

    await add_record("Жим", "60")
    await add_record("Присед", "80")
    today = date(2026, 12, 1)
    reward = ("pr_exercises_3_2026_12", "")
    assert reward not in await unlock_achievements(session, user.id, today=today)

    await add_record("Тяга", "100")
    assert reward in await unlock_achievements(session, user.id, today=today)
    assert "pr_exercises_3_2026_12" in await get_user_achievement_codes(session, user.id)
    assert await unlock_achievements(session, user.id, today=today) == []
