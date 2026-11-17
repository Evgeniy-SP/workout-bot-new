"""Тесты для progress_service — чистые функции, БД не нужна.

Подходы имитируем лёгкими фейковыми объектами (SimpleNamespace),
так как сервис работает только с атрибутами.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.progress_service import (
    collect_workout_stats, daily_max_durations, daily_max_reps,
    daily_max_weights, parse_weight, to_chart_points,
)


def make_set(weight=None, duration=None, reps=None, session_id=1,
             started_at=None, wt_name="Тренировка А"):
    """Фабрика фейкового подхода с минимально нужными атрибутами."""
    wt = SimpleNamespace(name=wt_name)
    sess = SimpleNamespace(id=session_id, started_at=started_at, workout_type=wt)
    return SimpleNamespace(
        session=sess, weight_kg=weight, duration_seconds=duration, reps=reps,
    )


# ─── parse_weight ────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("10", 10.0),
    ("52.5", 52.5),
    ("10,5", 10.5),          # запятая → точка
    ("Свое тело 5", 5.0),    # число внутри текста
    ("Своё тело", None),     # числа нет
    ("", None),
    (None, None),
])
def test_parse_weight(raw, expected):
    assert parse_weight(raw) == expected


# ─── collect_workout_stats ───────────────────────────────────────
def test_collect_workout_stats_counts_unique_sessions():
    sets = [
        make_set(session_id=1, wt_name="Верх"),
        make_set(session_id=1, wt_name="Верх"),  # второй подход той же сессии
        make_set(session_id=2, wt_name="Верх"),
        make_set(session_id=3, wt_name="Низ"),
    ]
    stats = collect_workout_stats(sets)
    assert stats == {"Верх": 2, "Низ": 1}


def test_collect_workout_stats_skips_empty_session():
    s = make_set()
    s.session = None
    assert collect_workout_stats([s]) == {}


# ─── daily_max_weights ───────────────────────────────────────────
def test_daily_max_weights_takes_max_per_day():
    day1 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    sets = [
        make_set(weight="40", started_at=day1),
        make_set(weight="50", started_at=day1),   # максимум дня
        make_set(weight="45", started_at=day2),
        make_set(weight="Своё тело", started_at=day2),  # без числа — пропуск
    ]
    daily = daily_max_weights(sets)
    assert len(daily) == 2
    assert list(daily.values()) == [50.0, 45.0]


def test_daily_max_weights_empty():
    assert daily_max_weights([]) == {}


# ─── daily_max_durations ─────────────────────────────────────────
def test_daily_max_durations():
    day = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    sets = [
        make_set(duration=120, started_at=day),
        make_set(duration=180, started_at=day),
        make_set(duration=None, started_at=day),
    ]
    daily = daily_max_durations(sets)
    assert list(daily.values()) == [180]


# ─── daily_max_reps ──────────────────────────────────────────────
def test_daily_max_reps_takes_best_set_of_day():
    day1 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    sets = [
        make_set(reps=8, started_at=day1),
        make_set(reps=12, started_at=day1),   # лучший подход дня
        make_set(reps=5, started_at=day1),
        make_set(reps=20, started_at=day2),
        make_set(reps=None, started_at=day2),  # без повторов — пропуск
    ]
    daily = daily_max_reps(sets)
    assert list(daily.values()) == [12, 20]


def test_daily_max_reps_empty():
    assert daily_max_reps([]) == {}


# ─── to_chart_points ─────────────────────────────────────────────
def test_to_chart_points_sorts_and_builds():
    from datetime import date
    daily = {date(2026, 11, 12): 45.0, date(2026, 11, 10): 50.0}
    dates, values = to_chart_points(daily)
    assert values == [50.0, 45.0]                    # отсортировано по дате
    assert dates[0] < dates[1]
    assert len(dates) == len(values)


def test_to_chart_points_single_day():
    from datetime import date
    dates, values = to_chart_points({date(2026, 11, 10): 30.0})
    assert len(dates) == 1 and values == [30.0]
