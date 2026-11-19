"""Тесты таблицы achievements и запросов к ней (C2.6, Блок 3).

Используют реальную SQLite в памяти через фикстуру session из conftest."""
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
    from datetime import datetime, timezone

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
