"""Сервис месячной аналитики.

Чистые функции без обращений к БД и Telegram: принимают сессии/подходы,
возвращают числа и списки для дашборда. Легко тестируются.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
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


def strict_tonnage(sets: list) -> float:
    """Тоннаж строго в кг — как на экране деталей тренировки.

    Считаем только подходы, где weight_kg — чистое число («14 блоков»,
    «Своё тело» честно пропускаем). Отличается от calculate_tonnage,
    которая для дашборда парсит число из любой строки (она «на карандаше»).
    Используем в CSV-экспорте, чтобы цифры сходились с экраном тренировки.
    """
    total = 0.0
    for s in sets:
        if s.completed and s.weight_kg:
            try:
                total += (s.reps or 0) * float(s.weight_kg)
            except ValueError:
                pass
    return total


def month_activity(sets: list) -> tuple[int, int]:
    """(выполненных подходов, уникальных выполненных упражнений) за месяц."""
    completed = [s for s in sets if s.completed and s.exercise]
    unique_exercises = {s.exercise_id for s in completed}
    return len(completed), len(unique_exercises)


def total_training_time(sessions: list) -> int:
    return sum(
        int((session.finished_at - session.started_at).total_seconds())
        for session in sessions if session.finished_at and session.started_at
    )


def best_training_month(dates: list[date]) -> tuple[date, int] | None:
    months = Counter(day.replace(day=1) for day in dates)
    return min(months.items(), key=lambda item: (-item[1], item[0]), default=None)


def available_retrospective_years(dates: list[date], today: date) -> list[int]:
    """Закрытые годы с тренировками и текущий год начиная с декабря."""
    years = {day.year for day in dates if day.year < today.year}
    if today.month == 12:
        years.add(today.year)
    return sorted(years)


def season_start(day: date) -> date:
    """Первый день календарного сезона, включая декабрь предыдущего года."""
    if day.month <= 2:
        return date(day.year - 1, 12, 1)
    return date(day.year, day.month // 3 * 3, 1)


def season_end(start: date) -> date:
    """Исключительная граница сезона."""
    if start.month == 12:
        return date(start.year + 1, 3, 1)
    return date(start.year, start.month + 3, 1)


def season_title(start: date) -> str:
    if start.month == 12:
        return f"Зима {start.year}/{(start.year + 1) % 100:02d}"
    names = {3: "Весна", 6: "Лето", 9: "Осень"}
    return f"{names[start.month]} {start.year}"


def available_retrospective_seasons(dates: list[date], today: date) -> list[date]:
    """Сезоны, в которых уже есть хотя бы одна завершённая тренировка."""
    return sorted({season_start(day) for day in dates if day <= today})


def all_time_bests(sessions: list) -> tuple[
    tuple[date, int], tuple[date, int], tuple[date, float, int] | None
] | None:
    """Рекорды по завершённым сессиям: месяц, неделя и (дата, тоннаж, ID)."""
    months: Counter[date] = Counter()
    weeks: Counter[date] = Counter()
    best_tonnage = None
    for session in sessions:
        if not session.started_at:
            continue
        started = to_msk(session.started_at)
        day = started.date()
        months[day.replace(day=1)] += 1
        weeks[day - timedelta(days=day.weekday())] += 1

        tonnage = strict_tonnage(session.sets)
        if tonnage > 0:
            candidate = (-tonnage, started, session.id)
            if best_tonnage is None or candidate < best_tonnage:
                best_tonnage = candidate

    if not months:
        return None
    best_month = min(months.items(), key=lambda item: (-item[1], item[0]))
    best_week = min(weeks.items(), key=lambda item: (-item[1], item[0]))
    tonnage_record = None
    if best_tonnage is not None:
        negative_tonnage, started_at, session_id = best_tonnage
        tonnage_record = (started_at.date(), -negative_tonnage, session_id)
    return best_month, best_week, tonnage_record


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


# Переход между упражнениями длиннее 20 минут считаем уже не «отдыхом»,
# а перерывом — в статистику не берём
MAX_EXERCISE_GAP_SEC = 1200


def average_rest_between_exercises(sets: list) -> float | None:
    """Медианный отдых между упражнениями внутри тренировки (секунды).

    Берём все завершённые подходы сессии по хронологии; интервал считается
    между последним подходом упражнения N и первым подходом следующего
    упражнения M (exercise_id меняется). Интервалы > 20 минут и <= 0
    отбрасываем. None, если данных нет.
    """
    by_session: dict[int, list] = defaultdict(list)
    for s in sets:
        if s.completed and s.completed_at:
            by_session[s.session_id].append(s)

    intervals = []
    for sess_sets in by_session.values():
        sess_sets.sort(key=lambda s: s.completed_at)
        for prev, cur in zip(sess_sets, sess_sets[1:]):
            if prev.exercise_id == cur.exercise_id:
                continue  # это внутриупражнениевый интервал, не наш случай
            delta = (cur.completed_at - prev.completed_at).total_seconds()
            if 0 < delta <= MAX_EXERCISE_GAP_SEC:
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
