"""Сервис расчёта прогресса упражнений.

Чистые функции без обращений к БД и Telegram:
принимают список подходов (SessionSet), возвращают агрегаты
для графиков и статистики. Легко тестируются без моков.
"""
import re
from collections import defaultdict
from datetime import datetime, time

from app.utils import to_msk

# Вес хранится строкой («10», «52.5», «Своё тело») — извлекаем число, если оно есть
WEIGHT_RE = re.compile(r'\d+(?:[.,]\d+)?')


def parse_weight(weight_str: str | None) -> float | None:
    """Извлекает числовое значение веса из строки. None, если числа нет."""
    if not weight_str:
        return None
    match = WEIGHT_RE.search(weight_str.replace(',', '.'))
    return float(match.group()) if match else None


def collect_workout_stats(history_sets: list) -> dict[str, int]:
    """Считает, в скольких тренировках каждого типа выполнялось упражнение.

    Возвращает {название типа тренировки: количество сессий}.
    """
    workout_stats: dict[str, set] = defaultdict(set)
    for s in history_sets:
        if s.session and s.session.workout_type:
            workout_stats[s.session.workout_type.name].add(s.session.id)
    return {name: len(ids) for name, ids in workout_stats.items()}


def daily_max_weights(history_sets: list) -> dict:
    """Максимальный вес за каждый день (дни по МСК).

    Возвращает {дата: вес в кг}, только для подходов с числовым весом.
    """
    daily: dict = defaultdict(float)
    for s in history_sets:
        if not (s.session and s.session.started_at and s.weight_kg):
            continue
        w = parse_weight(s.weight_kg)
        if w is None:
            continue
        day = to_msk(s.session.started_at).date()
        if w > daily[day]:
            daily[day] = w
    return dict(daily)


def daily_max_durations(history_sets: list) -> dict:
    """Максимальная длительность за каждый день (дни по МСК).

    Возвращает {дата: секунды}.
    """
    daily: dict = defaultdict(int)
    for s in history_sets:
        if not (s.session and s.session.started_at and s.duration_seconds):
            continue
        day = to_msk(s.session.started_at).date()
        if s.duration_seconds > daily[day]:
            daily[day] = s.duration_seconds
    return dict(daily)


def to_chart_points(daily: dict) -> tuple[list[datetime], list[float]]:
    """Превращает {дата: значение} в отсортированные списки для графика."""
    sorted_days = sorted(daily.keys())
    dates = [datetime.combine(d, time(12, 0)) for d in sorted_days]
    values = [daily[d] for d in sorted_days]
    return dates, values
