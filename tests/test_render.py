"""Тесты рендера деталей тренировки: заметки к упражнениям.

Регрессия: заметка к упражнению обязана быть видна на экране тренировки,
даже если у упражнения нет выполненных подходов (кардио-разминка).
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.render import render_history_details


def _ws(sets=(), exercise_notes=(), notes=None):
    return SimpleNamespace(
        workout_type=SimpleNamespace(name="Тест"),
        started_at=datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 11, 6, 17, 45, tzinfo=timezone.utc),
        sets=list(sets),
        notes=notes,
        exercise_notes=list(exercise_notes),
    )


def test_details_shows_note_for_exercise_without_completed_sets():
    """Заметка к кардио-разминке видна, даже когда подходов 0."""
    note = SimpleNamespace(
        exercise_id=3, note="тестик",
        exercise=SimpleNamespace(id=3, name="Элипс (разминка)", kind="cardio"),
    )
    text = render_history_details(_ws(exercise_notes=[note]))
    assert "Нет выполненных подходов" in text
    assert "Элипс (разминка)" in text
    assert "тестик" in text


def test_details_shows_note_under_completed_exercise():
    """Заметка выводится под блоком подходов выполненного упражнения."""
    ex = SimpleNamespace(id=1, name="Жим", kind="strength")
    s = SimpleNamespace(
        completed=True, exercise=ex, set_number=1, reps=9, weight_kg="60",
        duration_seconds=None,
        completed_at=datetime(2026, 11, 6, 17, 30, tzinfo=timezone.utc),
    )
    note = SimpleNamespace(exercise_id=1, note="отдых 50 сек", exercise=ex)
    text = render_history_details(_ws(sets=[s], exercise_notes=[note]))
    assert "Жим" in text
    assert "отдых 50 сек" in text


def test_details_without_exercise_notes_still_works():
    """Старые сессии без заметок рендерятся как раньше."""
    ex = SimpleNamespace(id=1, name="Жим", kind="strength")
    s = SimpleNamespace(
        completed=True, exercise=ex, set_number=1, reps=9, weight_kg="60",
        duration_seconds=None,
        completed_at=datetime(2026, 11, 6, 17, 30, tzinfo=timezone.utc),
    )
    text = render_history_details(_ws(sets=[s]))
    assert "Жим" in text
    assert "📝" not in text
