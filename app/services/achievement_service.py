"""Сервис достижений (C2.6, Блок 3).

Чистые функции без обращений к БД и Telegram (по образцу pr_service):
принимают готовые выборки из queries.py и возвращают решения/тексты.

Категории ачивок:
- workouts_10/50/100/250 — суммарное число завершённых тренировок;
- streak_7/30 — серия дней с тренировками (день по МСК, тренировка =
  любая завершённая сессия). Серия «актуальна» до конка завтрашнего
  дня: вечерняя тренировка не обнуляет стрик мгновенно после полуночи;
- exercises_10 — сколько разных упражнений (по name+kind) было выполнено;
- pr_first/pr_10/pr_50 — суммарное число PR за всю историю
  (силовые: вес/повторы, кардио: время — те же правила, что в pr_service);
- pr_3_session — 3 и более рекорда за одну тренировку;
- weight_plus_20 — рабочий вес вырос на 20% от стартовой планки
  упражнения. Планка = МАКСИМАЛЬНЫЙ вес за первые 30 дней истории
  упражнения (считая от первого подхода), по каждой единице измерения
  отдельно («12 блоков» сравнивается только с блоками — та же схема,
  что в pr_service). Планка фиксированная: ачивка показывает рост
  от старта и не «откатывается» при новых рекордах.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from app.services.pr_service import (
    _unit_key, classify_duration_pr, classify_pr, split_weight,
)
from app.utils import MSK, to_msk

# Пороги серийных ачивок
WORKOUT_MILESTONES = [10, 50, 100, 250]
STREAK_MILESTONES = [7, 30]
# (порог суммарного числа PR, код ачивки) — у первой ачивки код не числовой
PR_MILESTONES = [(1, "pr_first"), (10, "pr_10"), (50, "pr_50")]
# Градации «Разнообразия» — по числу уникальных упражнений (name+kind)
EXERCISES_MILESTONES = [10, 25, 50]

# Параметры ачивки «+20% к весу»
WEIGHT_GAIN_WINDOW_DAYS = 30   # базовая планка — максимум за первые 30 дней
WEIGHT_GAIN_FACTOR = 1.2       # цель: вес >= 1.2 × планки

ACHIEVEMENTS: dict[str, dict] = {
    "workouts_10": {
        "icon": "🎖", "title": "Первая десятка",
        "desc": "10 завершённых тренировок"},
    "workouts_50": {
        "icon": "🥇", "title": "Полсотни тренировок",
        "desc": "50 завершённых тренировок"},
    "workouts_100": {
        "icon": "💯", "title": "Сотня тренировок",
        "desc": "100 завершённых тренировок"},
    "workouts_250": {
        "icon": "🏅", "title": "Марафонец",
        "desc": "250 завершённых тренировок"},
    "streak_7": {
        "icon": "📅", "title": "Неделя без пропусков",
        "desc": "7 дней подряд с тренировками"},
    "streak_30": {
        "icon": "🗓", "title": "Месяц железной дисциплины",
        "desc": "30 дней подряд с тренировками"},
    "exercises_10": {
        "icon": "🧩", "title": "Экспериментатор",
        "desc": "10 разных упражнений в истории"},
    "exercises_25": {
        "icon": "🎯", "title": "Коллекционер движений",
        "desc": "25 разных упражнений в истории"},
    "exercises_50": {
        "icon": "🧠", "title": "Энциклопедия упражнений",
        "desc": "50 разных упражнений в истории"},
    "pr_first": {
        "icon": "🔥", "title": "Первый рекорд",
        "desc": "ты побил свой прошлый результат"},
    "pr_10": {
        "icon": "⚡️", "title": "10 рекордов",
        "desc": "10 PR за всю историю"},
    "pr_50": {
        "icon": "🌟", "title": "50 рекордов",
        "desc": "50 PR за всю историю"},
    "pr_3_session": {
        "icon": "💥", "title": "Взрывная тренировка",
        "desc": "3 и более рекорда за одну тренировку"},
    "weight_plus_20": {
        "icon": "📈", "title": "Вес +20%",
        "desc": "рост рабочего веса на 20% от стартовой планки"},
}

# Порядок вывода на экране «🏆 Ачивки» и в уведомлении
ACHIEVEMENT_ORDER = [
    "workouts_10", "workouts_50", "workouts_100", "workouts_250",
    "streak_7", "streak_30",
    "pr_first", "pr_10", "pr_50", "pr_3_session", "weight_plus_20",
    "exercises_10", "exercises_25", "exercises_50",
]

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _sort_key(s) -> tuple:
    """Хронологический ключ подхода: (старт сессии, номер подхода, id)."""
    started = getattr(getattr(s, "session", None), "started_at", None)
    return (started or _EPOCH, s.set_number or 0, s.id or 0)


def _day(s):
    """День подхода по МСК (date)."""
    started = getattr(getattr(s, "session", None), "started_at", None)
    return to_msk(started).date() if started else None


def _exercise_key(s) -> tuple:
    """Ключ упражнения — по name+kind, как в истории прогресса."""
    ex = getattr(s, "exercise", None)
    return (getattr(ex, "name", "?"), getattr(ex, "kind", "strength"))


# ─── Серия дней (streak) ──────────────────────────────────────────


def compute_streak(dates: set, today) -> int:
    """Текущая серия дней с тренировками.

    dates — множество дат (date) завершённых тренировок по МСК,
    today — сегодняшняя дата по МСК.

    Серия «жива», если последняя тренировка была сегодня или вчера
    (иначе пропуск уже состоялся — серия потухла, возвращаем 0).
    Дальше считаем назад, пока каждый предыдущий день есть в dates.
    """
    if not dates:
        return 0
    last = max(dates)
    if (today - last).days > 1:
        return 0
    streak, day = 0, last
    while day in dates:
        streak += 1
        day -= timedelta(days=1)
    return streak


# ─── Подсчёт PR за всю историю ────────────────────────────────────


def count_pr_events(all_sets: list) -> int:
    """Суммарное число PR за всю историю.

    Каждый подход сравнивается со ВСЕМИ предыдущими (хронологически) —
    по тем же правилам, что мгновенные рекорды в pr_service
    (силовые: вес/повторы; кардио: длительность; единицы веса
    не смешиваются). i-й подход — рекорд, если бьёт всё, что было до
    него, а не только прошлые тренировки.
    """
    total, prior = 0, []
    for s in sorted(all_sets, key=_sort_key):
        if getattr(s, "reps", None) and \
                split_weight(getattr(s, "weight_kg", None))[0] is not None:
            if classify_pr(s, prior):
                total += 1
        elif getattr(s, "duration_seconds", None):
            if classify_duration_pr(s, prior):
                total += 1
        prior.append(s)
    return total


# ─── Ачивка «Вес +20%» ────────────────────────────────────────────


def weight_gain_achievements(all_sets: list) -> dict:
    """{ключ упражнения: название} — где выполнено условие +20%.

    Базовая планка = максимальный вес за первые WEIGHT_GAIN_WINDOW_DAYS
    дней истории упражнения (отдельно по каждой единице измерения).
    Ачивка выдаётся, если ПОСЛЕ окончания окна появился подход
    с весом >= WEIGHT_GAIN_FACTOR × планки в той же единице.
    """
    groups: dict[tuple, list] = defaultdict(list)
    for s in all_sets:
        w, _ = split_weight(getattr(s, "weight_kg", None))
        if w is None or not getattr(s, "reps", None) or _day(s) is None:
            continue
        groups[_exercise_key(s)].append(s)

    unlocked: dict[tuple, str] = {}
    for key, sets in groups.items():
        sets.sort(key=_sort_key)
        window_end = _day(sets[0]) + timedelta(days=WEIGHT_GAIN_WINDOW_DAYS)

        baseline: dict[str, float] = {}
        for s in sets:
            if _day(s) > window_end:
                continue
            w, unit = split_weight(s.weight_kg)
            k = _unit_key(unit)
            baseline[k] = max(baseline.get(k, 0.0), w)
        if not baseline:
            continue

        for s in sets:
            if _day(s) <= window_end:
                continue
            w, unit = split_weight(s.weight_kg)
            base = baseline.get(_unit_key(unit))
            if base and w >= base * WEIGHT_GAIN_FACTOR:
                unlocked[key] = getattr(s.exercise, "name", "?")
                break
    return unlocked


# ─── Сводная проверка новых ачивок ────────────────────────────────


def check_new_achievements(*, codes: set, today, session_dates,
                           workouts_count: int, exercises_count: int,
                           pr_total: int, pr_in_session: int,
                           weight_gain_names: list) -> list[tuple]:
    """Определяет НОВЫЕ ачивки после завершения тренировки.

    Возвращает [(код, деталь)] только для ачивок, которых ещё нет
    в codes (деталь — например, название упражнения для weight_plus_20).
    """
    new: list[tuple] = []
    for n in WORKOUT_MILESTONES:
        code = f"workouts_{n}"
        if workouts_count >= n and code not in codes:
            new.append((code, ""))

    streak = compute_streak(set(session_dates), today)
    for n in STREAK_MILESTONES:
        code = f"streak_{n}"
        if streak >= n and code not in codes:
            new.append((code, ""))

    for n in EXERCISES_MILESTONES:
        code = f"exercises_{n}"
        if exercises_count >= n and code not in codes:
            new.append((code, ""))

    for threshold, code in PR_MILESTONES:
        if pr_total >= threshold and code not in codes:
            new.append((code, ""))

    if pr_in_session >= 3 and "pr_3_session" not in codes:
        new.append(("pr_3_session", ""))

    if weight_gain_names and "weight_plus_20" not in codes:
        new.append(("weight_plus_20", weight_gain_names[0]))
    return new


# ─── Проверка с записью в БД ──────────────────────────────────────


async def unlock_achievements(db_session, user_id: int,
                              pr_in_session: int = 0) -> list[tuple]:
    """Проверяет условия и записывает НОВЫЕ достижения в БД.

    Возвращает [(код, деталь)] только что разблокированных. Используется:
    - при завершении тренировки (pr_in_session = число лучших PR
      текущей сессии — для pr_3_session);
    - при открытии экрана «🏆 Ачивки» — ретро-выдача наград за историю
      ДО появления этой фичи (pr_in_session=0: за давно завершённые
      тренировки pr_3_session не восстанавливаем — только за новые).

    Вызывать только когда свежие данные уже в БД (finished_at
    зафиксирован), иначе тренировка не попадёт в счётчики.
    """
    from app.db.queries import (
        add_achievement, count_exercises_with_history, count_finished_sessions,
        get_all_completed_sets, get_finished_session_dates,
        get_user_achievement_codes,
    )

    codes = await get_user_achievement_codes(db_session, user_id)
    all_sets = await get_all_completed_sets(db_session, user_id)

    new = check_new_achievements(
        codes=codes,
        today=datetime.now(MSK).date(),
        session_dates=await get_finished_session_dates(db_session, user_id),
        workouts_count=await count_finished_sessions(db_session, user_id),
        exercises_count=await count_exercises_with_history(db_session,
                                                           user_id),
        pr_total=count_pr_events(all_sets),
        pr_in_session=pr_in_session,
        weight_gain_names=list(weight_gain_achievements(all_sets).values()),
    )
    for code, _ in new:
        await add_achievement(db_session, user_id, code)
    return new


# ─── Тексты ───────────────────────────────────────────────────────


def build_achievements_message(new: list[tuple]) -> str:
    """Текст мгновенного уведомления о новых ачивках.

    new — [(код, деталь)] из check_new_achievements. Ачивки идут
    в каноническом порядке ACHIEVEMENT_ORDER.
    """
    order = {c: i for i, c in enumerate(ACHIEVEMENT_ORDER)}
    lines = ["🏆 <b>Новые достижения!</b>", ""]
    for code, detail in sorted(new, key=lambda x: order.get(x[0], 99)):
        a = ACHIEVEMENTS[code]
        suffix = f" — {detail}" if detail else ""
        lines.append(f"{a['icon']} <b>{a['title']}</b> — {a['desc']}{suffix}")
    return "\n".join(lines)


def render_achievements_screen(*, codes: set, session_dates: list,
                               workouts_count: int, exercises_count: int,
                               pr_total: int, weight_gain_names: list,
                               today) -> str:
    """Экран «🏆 Ачивки»: разблокированные ✅ и замки 🔒 с прогрессом."""
    streak = compute_streak(set(session_dates), today)
    known = set(ACHIEVEMENT_ORDER)
    unlocked = len(set(codes) & known)

    def line(code, current=None, target=None, unit="", detail=""):
        a = ACHIEVEMENTS[code]
        if code in codes:
            extra = f" · {current}{unit}" if current is not None else ""
            if detail:
                extra += f" · {detail}"
            return f"✅ {a['icon']} {a['title']}{extra}"
        if current is not None and target is not None:
            cur = min(current, target)
            return f"🔒 {a['icon']} {a['title']} — {cur}/{target}{unit}"
        return f"🔒 {a['icon']} {a['title']}"

    lines = [
        f"🏆 <b>Достижения — {unlocked} из {len(ACHIEVEMENT_ORDER)}</b>",
        "<i>Разблокируются автоматически при завершении тренировки.</i>",
        "",
        "💪 <b>Регулярность</b>",
        line("workouts_10", workouts_count, 10),
        line("workouts_50", workouts_count, 50),
        line("workouts_100", workouts_count, 100),
        line("workouts_250", workouts_count, 250),
        line("streak_7", streak, 7, " дн."),
        line("streak_30", streak, 30, " дн."),
        "",
        "🏋️ <b>Рекорды</b>",
        line("pr_first", pr_total if pr_total else None, 1),
        line("pr_10", pr_total if pr_total else None, 10),
        line("pr_50", pr_total if pr_total else None, 50),
        line("pr_3_session"),
        line("weight_plus_20",
             detail=weight_gain_names[0] if weight_gain_names else ""),
        "",
        "🧩 <b>Разнообразие</b>",
    ]
    for n in EXERCISES_MILESTONES:
        lines.append(
            line(f"exercises_{n}",
                 exercises_count if exercises_count else None,
                 n, " упр."))
    return "\n".join(lines)
