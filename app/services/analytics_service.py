"""Сервис месячной аналитики.

Чистые функции без обращений к БД и Telegram: принимают сессии/подходы,
возвращают числа и списки для дашборда. Легко тестируются.
"""
from collections import defaultdict
from re import compile as re_compile
from statistics import median

from app.services.progress_service import parse_weight
from app.utils import to_msk

# Интервалы длиннее 10 минут между подходами считаем не «отдыхом»,
# а перерывом (отвлекся, перешёл к другому делу) — в статистику не берём
MAX_REST_INTERVAL_SEC = 600

# Число «недельных» бакетов в месяце: 1–7, 8–14, 15–21, 22–28, 29–31
WEEK_BUCKETS = 5

# Строка-вес считается числовой, только если состоит целиком из числа
_NUMERIC_WEIGHT = re_compile(r"\d+(?:[.,]\d+)?")


def calculate_tonnage(sets: list) -> float:
    """Суммарный тоннаж (вес × повторы) по силовым подходам.

    Подходы с текстовым весом без числа («Своё тело») честно пропускаем.
    """
    total = 0.0
    for s in sets:
        if not (s.completed and s.weight_kg and s.reps):
            continue
        w = parse_weight(s.weight_kg)
        if w is not None:
            total += w * s.reps
    return total


def month_activity(sets: list) -> tuple[int, int]:
    """(выполненных подходов, уникальных выполненных упражнений) за месяц."""
    completed = [s for s in sets if s.completed and s.exercise]
    unique_exercises = {s.exercise_id for s in completed}
    return len(completed), len(unique_exercises)


def month_top_stats(sets: list, limit: int = 3
                    ) -> list[tuple[str, int, str | None]]:
    """Топ упражнений месяца по числу подходов + лучший результат каждого.

    Возвращает [(имя, подходов, результат), ...] по убыванию подходов.
    Результат: максимальный вес для силовых («60 кг»), текстовый вес
    для блоков/собственного веса («9 блоков», «Своё тело»), лучшее время
    для кардио («15 мин»). None, если результата нет.
    """
    groups: dict[str, list] = defaultdict(list)
    for s in sets:
        if s.completed and s.exercise:
            groups[s.exercise.name].append(s)

    rows = [(name, len(group), _best_result(group))
            for name, group in groups.items()]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:limit]


def _best_result(sets: list) -> str | None:
    """Лучший результат по подходам одного упражнения: вес или время.

    Числовой вес — строка, состоящая только из числа («52.5»).
    Всё остальное («9 блоков», «Своё тело») — текстовый вес: он не
    конвертируется в кг, показываем как есть.
    """
    weights: list[float] = []
    text_weights: set[str] = set()
    durations: list[int] = []
    for s in sets:
        if s.weight_kg:
            w_str = str(s.weight_kg).strip()
            if _NUMERIC_WEIGHT.fullmatch(w_str):
                weights.append(float(w_str.replace(",", ".")))
            else:
                text_weights.add(s.weight_kg)
        if s.duration_seconds:
            durations.append(s.duration_seconds)

    if weights:
        return f"{max(weights):g} кг"
    if text_weights:
        # Блоки и т.п.: берём вес с максимальным числом («9 блоков»)
        with_numbers = [(parse_weight(w), w) for w in text_weights]
        with_numbers = [(n, w) for n, w in with_numbers if n is not None]
        if with_numbers:
            return max(with_numbers, key=lambda t: t[0])[1]
        # Только текст без числа («Своё тело») — показываем как есть
        return sorted(text_weights)[0]
    if durations:
        return _format_minutes(max(durations))
    return None


def _format_minutes(total_seconds: int) -> str:
    minutes = round(total_seconds / 60)
    if minutes >= 60:
        return f"{minutes // 60} ч {minutes % 60} мин"
    return f"{minutes} мин"


def average_rest(sets: list) -> float | None:
    """Медианный отдых между подходами внутри упражнения (секунды).

    Считаем интервалы между последовательными подходами одного упражнения
    в рамках одной сессии. Интервалы > 10 минут и <= 0 отбрасываем.
    None, если данных нет.
    """
    groups: dict[tuple, list] = defaultdict(list)
    for s in sets:
        if s.completed and s.completed_at:
            groups[(s.session_id, s.exercise_id)].append(s.completed_at)

    intervals = []
    for times in groups.values():
        times.sort()
        for a, b in zip(times, times[1:]):
            delta = (b - a).total_seconds()
            if 0 < delta <= MAX_REST_INTERVAL_SEC:
                intervals.append(delta)

    return median(intervals) if intervals else None


def weekly_activity(sessions: list) -> tuple[list[int], list[int]]:
    """Число тренировок и уникальных выполненных упражнений по неделям месяца.

    Недели — фиксированные бакеты по числам: 1–7, 8–14, 15–21, 22–28, 29–31.
    Упражнение считается выполненным, если по нему есть хоть один
    завершённый подход; пропущенные (skipped) не считаются.
    Возвращает (тренировки, упражнения) — списки из 5 чисел.
    """
    trainings = [0] * WEEK_BUCKETS
    exercise_ids: list[set] = [set() for _ in range(WEEK_BUCKETS)]

    for ses in sessions:
        if not ses.started_at:
            continue
        bucket = (to_msk(ses.started_at).day - 1) // 7
        trainings[bucket] += 1
        for s in ses.sets:
            if s.completed:
                exercise_ids[bucket].add(s.exercise_id)

    exercises = [len(ids) for ids in exercise_ids]
    return trainings, exercises
