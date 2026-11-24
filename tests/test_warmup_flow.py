from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.db.models import Exercise, SessionSet, User, WorkoutSession, WorkoutType
from app.db.queries import get_session_details, get_sets_grouped
from app.handlers.history import cb_history_warmups
from app.handlers.training_finish import cb_finish_confirm
from app.handlers.training_sets import (
    cb_custom_start, cb_numeric_input, cb_save_planned, cb_set_kind, cb_weight_input,
    fsm_enter_reps, fsm_enter_weight,
)
from app.render import render_history_details
from app.services.training_service import render_approach_screen


@pytest.mark.asyncio
async def test_warmup_entry_working_suggestion_and_remarking(session, monkeypatch):
    workout = WorkoutType(user=User(tg_id=1, full_name="Test"), name="Test")
    ex = Exercise(workout_type=workout, name="Жим", kind="strength", sets=5, reps=5, weight_kg="65")
    session.add(ex)
    await session.flush()
    now = datetime.now(timezone.utc)
    current = WorkoutSession(user_id=workout.user_id, workout_type_id=workout.id, started_at=now)
    prior = WorkoutSession(user_id=workout.user_id, workout_type_id=workout.id,
                           started_at=now - timedelta(days=2), finished_at=now - timedelta(days=2, hours=-1))
    session.add_all([prior, current])
    await session.flush()
    session.add(SessionSet(session_id=prior.id, exercise_id=ex.id, set_number=1,
                           reps=5, weight_kg="70", completed=True))
    await session.commit()

    async def groups_with_utc(*args):
        groups = await get_sets_grouped(*args)
        for sets in groups.values():
            for item in sets:
                if item.completed_at and item.completed_at.tzinfo is None:
                    item.completed_at = item.completed_at.replace(tzinfo=timezone.utc)
        return groups

    # PostgreSQL returns timezone-aware timestamps. SQLite drops the offset.
    monkeypatch.setattr("app.services.training_service.get_sets_grouped", groups_with_utc)
    monkeypatch.setattr("app.handlers.training_finish.get_sets_grouped", groups_with_utc)
    monkeypatch.setattr("app.handlers.training_finish.ach.unlock_achievements", AsyncMock(return_value=[]))
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1))
    message = SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), text="20")
    callback = SimpleNamespace(message=message, from_user=SimpleNamespace(id=1), answer=AsyncMock())
    warmup_action = f"tr_warmup:{current.id}:{ex.id}"

    callback.data = warmup_action
    await cb_custom_start(callback, session, state)
    await fsm_enter_reps(message, state, session)
    message.text = "Пустой гриф"
    await fsm_enter_weight(message, state, session)
    callback.data = warmup_action
    await cb_custom_start(callback, session, state)
    await state.update_data(temp_num="10")
    callback.data = "num:done"
    await cb_numeric_input(callback, state, session)
    await state.update_data(temp_weight="40")
    callback.data = "w_num:done"
    await cb_weight_input(callback, state, session)

    screen, keyboard = await render_approach_screen(session, current.id, ex.id)
    assert "Рабочих по плану: <b>0</b> из <b>5</b>" in screen
    assert screen.count("Рабочих по плану") == 1 and "Выполнено" not in screen
    assert "🔥 Разминка · 20" in screen
    quick_save = next(b for row in keyboard.inline_keyboard for b in row if b.callback_data.startswith("tr_save_planned:"))
    assert "5 × 70 кг" in quick_save.text
    callback.data = quick_save.callback_data
    await cb_save_planned(callback, session, state)
    rows = (await groups_with_utc(session, current.id))[ex.id]
    assert [(s.is_warmup, s.reps, s.weight_kg) for s in rows] == [
        (True, 20, "Пустой гриф"), (True, 10, "40"), (False, 5, "70"),
    ]

    first = rows[0]
    saved_values = first.reps, first.weight_kg, first.completed_at
    callback.data = f"tr_set_kind:{current.id}:{first.id}:0"
    await cb_set_kind(callback, session, state)
    assert not first.is_warmup
    callback.data = f"tr_set_kind:{current.id}:{first.id}:1"
    await cb_set_kind(callback, session, state)
    assert first.is_warmup
    assert (first.reps, first.weight_kg, first.completed_at) == saved_values

    callback.data = f"tr_finish_yes:{current.id}"
    await cb_finish_confirm(callback, session, state)
    summary = message.edit_text.await_args.args[0]
    assert "1/5" in summary and "без изменений" in summary

    callback.data = f"hw:0:{first.id}.0:{current.id}:0:0"
    await cb_history_warmups(callback, session)
    assert not first.is_warmup
    callback.data = f"hw:0:{first.id}.1:{current.id}:0:0"
    await cb_history_warmups(callback, session)
    assert (first.reps, first.weight_kg, first.completed_at) == saved_values
    history = render_history_details(await get_session_details(session, current.id))
    assert history.count("🔥 Разминка") == 2
