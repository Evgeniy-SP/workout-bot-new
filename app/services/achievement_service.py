"""Сервис достижений (C2.6, Блок 3).

Чистые функции без обращений к БД и Telegram (по образцу pr_service):
принимают готовые выборки из queries.py и возвращают решения/тексты.

Категории ачивок:
- workouts_10/50/100/250 — суммарное число завершённых тренировок;
- monthly_12/20 — регулярность: максимум завершённых тренировок за
  ОДИН календарный месяц (по МСК) — 12 и 20 соответственно. Честнее
  серии-дней для сплита «через день»: пропуск между тренировками
  не рвёт прогресс, важен итог месяца;
- exercises_10/25/50 — сколько разных упражнений (по name+kind)
  было выполнено;
- pr_first/pr_10/pr_50 — суммарное число PR за всю историю
  (силовые: вес/повторы, кардио: время — те же правила, что в pr_service);
- pr_3_session — 3 и более рекорда за одну тренировку;
- weight_plus_20 — рабочий вес вырос на 20% от стартовой планки
  упражнения. Планка = МАКСИМАЛЬНЫЙ вес за первые 30 дней истории
  упражнения (считая от первого подхода), по каждой единице измерения
  отдельно («12 блоков» сравнивается только с блоками — та же схема,
  что в pr_service). Планка фиксированная: ачивка показывает рост
  от старта и не «откатывается» при новых рекордах. Ачивка
  одноразовая, но на экране показываем ВСЕ упражнения, выполнившие
  условие (а не только первое).
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape
from typing import Callable

from app.services.pr_service import (
    _unit_key, classify_duration_pr, classify_pr, split_weight,
)
from app.utils import MSK, format_duration, format_weight, to_msk

# Пороги серийных ачивок
WORKOUT_MILESTONES = [10, 50, 100, 250]
# (порог «максимум тренировок за один календарный месяц», код ачивки)
MONTHLY_MILESTONES = [(12, "monthly_12"), (20, "monthly_20")]
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
    "monthly_12": {
        "icon": "🗓", "title": "Месяц в ритме",
        "desc": "12 тренировок за один календарный месяц"},
    "monthly_20": {
        "icon": "⚙️", "title": "Железный конвейер",
        "desc": "20 тренировок за один календарный месяц"},
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
        "desc": "1 личный рекорд"},
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
    "monthly_12", "monthly_20",
    "pr_first", "pr_10", "pr_50", "pr_3_session", "weight_plus_20",
    "exercises_10", "exercises_25", "exercises_50",
]


@dataclass(frozen=True)
class MonthlyGoal:
    key: str
    target: int
    unit: str
    title: str
    measure: Callable[[list[date], list, date, date], tuple[int, list[tuple[str, str, str]]]]


@dataclass(frozen=True)
class SeasonalChallenge:
    start: date
    goal: MonthlyGoal

    @property
    def code(self) -> str:
        return f"{self.goal.key}_{self.goal.target}_{self.start:%Y_%m}"

    def progress(self, session_dates: list[date], all_sets: list,
                 today: date) -> tuple[int, list[tuple[str, str, str]]]:
        if today < self.start:
            return 0, []
        end = min(next_month(self.start), today + timedelta(days=1))
        return self.goal.measure(session_dates, all_sets, self.start, end)


FIRST_CHALLENGE_MONTH = date(2026, 12, 1)
MONTH_TITLES = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)


def next_month(start: date) -> date:
    if start.month == 12:
        return date(start.year + 1, 1, 1)
    return date(start.year, start.month + 1, 1)


def month_title(start: date) -> str:
    return f"{MONTH_TITLES[start.month - 1]} {start.year}"


def _workout_progress(dates: list[date], _sets: list, start: date, end: date):
    return sum(start <= day < end for day in dates), []


def _pr_progress(_dates: list[date], sets: list, start: date, end: date):
    exercises = pr_exercises(sets, start, end)
    return len(exercises), exercises


DEFAULT_MONTHLY_GOALS = (
    MonthlyGoal("monthly", 12, "завершённых тренировок",
                "{target} тренировок за месяц", _workout_progress),
    MonthlyGoal("pr_exercises", 3, "разных упр. с личным рекордом",
                "личный рекорд в {target} разных упражнениях", _pr_progress),
)
# Для месяца с другими целями указывается полный набор, действующий с его начала.
MONTHLY_GOAL_OVERRIDES: dict[date, tuple[MonthlyGoal, ...]] = {}


def monthly_challenges(start: date) -> list[SeasonalChallenge]:
    if start < FIRST_CHALLENGE_MONTH:
        return []
    return [SeasonalChallenge(start, goal)
            for goal in MONTHLY_GOAL_OVERRIDES.get(start, DEFAULT_MONTHLY_GOALS)]


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


# ─── Регулярность по месяцам ──────────────────────────────────────


def best_monthly_count(dates: set) -> int:
    """Максимум завершённых тренировок за один календарный месяц.

    dates — множество дат (date) завершённых тренировок по МСК.
    Для ачивок регулярности важен лучший месяц за всю историю:
    закрытый месяц остаётся «в активе» навсегда.
    """
    by_month: dict = defaultdict(int)
    for d in dates:
        by_month[(d.year, d.month)] += 1
    return max(by_month.values()) if by_month else 0


# ─── Подсчёт PR за всю историю ────────────────────────────────────


def _pr_kind(workout_set, prior: list) -> str | None:
    weight, _ = split_weight(getattr(workout_set, "weight_kg", None))
    if getattr(workout_set, "reps", None) and weight is not None:
        return classify_pr(workout_set, prior)
    if getattr(workout_set, "duration_seconds", None):
        return classify_duration_pr(workout_set, prior)
    return None


def pr_exercises(all_sets: list, start: date, end: date) -> list[tuple[str, str, str]]:
    """Последний PR каждого упражнения, в порядке первого зачёта за период."""
    prior_by_exercise: dict[tuple, list] = defaultdict(list)
    records: dict[tuple[str, str], str] = {}
    for workout_set in sorted(all_sets, key=_sort_key):
        session = workout_set.session
        if not session or session.finished_at is None or not workout_set.completed:
            continue
        day = _day(workout_set)
        if day is None:
            continue
        if day >= end:
            break
        key = _exercise_key(workout_set)
        prior = prior_by_exercise[key]
        record_kind = _pr_kind(workout_set, prior) if day >= start else None
        if record_kind == "duration":
            records[key] = format_duration(workout_set.duration_seconds)
        elif record_kind:
            weight = format_weight(workout_set.weight_kg)
            records[key] = f"{weight} × {workout_set.reps}"
        prior.append(workout_set)
    return [(name, kind, result) for (name, kind), result in records.items()]


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
        if _pr_kind(s, prior):
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


def check_new_achievements(*, codes: set, session_dates,
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

    best_month = best_monthly_count(set(session_dates))
    for threshold, code in MONTHLY_MILESTONES:
        if best_month >= threshold and code not in codes:
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


async def close_monthly_challenges(db_session, user_id: int, today: date) -> list[tuple[str, str]]:
    """Фиксирует прошедшие месяцы без незавершённых тренировок."""
    if today < next_month(FIRST_CHALLENGE_MONTH):
        return []

    from sqlalchemy import select

    from app.db.models import Achievement, MonthlyChallengeResult, User, WorkoutSession
    from app.db.queries import (
        get_all_completed_sets, get_challenge_archive_months, get_finished_session_dates,
        get_user_achievement_codes,
    )

    # Два одновременных закрытия одного пользователя читают и записывают по очереди.
    created_at = await db_session.scalar(
        select(User.created_at).where(User.id == user_id).with_for_update(),
    )
    if created_at is None:
        return []
    current = today.replace(day=1)
    start = max(FIRST_CHALLENGE_MONTH, to_msk(created_at).date().replace(day=1))
    closed = set(await get_challenge_archive_months(db_session, user_id))
    open_dates = await db_session.scalars(
        select(WorkoutSession.started_at).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.finished_at.is_(None),
            WorkoutSession.started_at >= datetime.combine(start, time.min, tzinfo=MSK),
            WorkoutSession.started_at < datetime.combine(current, time.min, tzinfo=MSK),
        ),
    )
    pending = {to_msk(started_at).date().replace(day=1) for started_at in open_dates}
    to_close = []
    while start < current:
        if start not in closed and start not in pending:
            to_close.extend(monthly_challenges(start))
        start = next_month(start)
    if not to_close:
        await db_session.commit()
        return []

    dates = await get_finished_session_dates(db_session, user_id)
    sets = await get_all_completed_sets(db_session, user_id)
    codes = await get_user_achievement_codes(db_session, user_id)
    awarded = []
    for challenge in to_close:
        goal = challenge.goal
        value, records = challenge.progress(dates, sets, today)
        reached = value >= goal.target or challenge.code in codes
        db_session.add(MonthlyChallengeResult(
            user_id=user_id,
            period_start=challenge.start,
            goal_key=goal.key,
            goal_title=goal.title.format(target=goal.target),
            metric_unit=goal.unit,
            target_value=goal.target,
            actual_value=value,
            goal_met=reached,
            details={"exercises": [
                {"name": name, "kind": kind, "result": result}
                for name, kind, result in records
            ]},
        ))
        if reached and challenge.code not in codes:
            db_session.add(Achievement(user_id=user_id, code=challenge.code))
            awarded.append((challenge.code, ""))
    await db_session.commit()
    return awarded


async def unlock_achievements(db_session, user_id: int,
                              pr_in_session: int = 0,
                              *, today: date | None = None) -> list[tuple]:
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

    today = today or to_msk(datetime.now(timezone.utc)).date()
    monthly_awards = await close_monthly_challenges(db_session, user_id, today)
    codes = await get_user_achievement_codes(db_session, user_id)
    all_sets = await get_all_completed_sets(db_session, user_id)
    session_dates = await get_finished_session_dates(db_session, user_id)

    new = check_new_achievements(
        codes=codes,
        session_dates=session_dates,
        workouts_count=await count_finished_sessions(db_session, user_id),
        exercises_count=await count_exercises_with_history(db_session,
                                                           user_id),
        pr_total=count_pr_events(all_sets),
        pr_in_session=pr_in_session,
        weight_gain_names=list(weight_gain_achievements(all_sets).values()),
    )
    for challenge in monthly_challenges(today.replace(day=1)):
        if challenge.code not in codes:
            progress, _ = challenge.progress(session_dates, all_sets, today)
            if progress >= challenge.goal.target:
                new.append((challenge.code, ""))
    return monthly_awards + [
        (code, detail) for code, detail in new
        if await add_achievement(db_session, user_id, code, skip_existing=True)
    ]


# ─── Тексты ───────────────────────────────────────────────────────


def _join_names(names: list) -> str:
    """«Приседания, Жим, Тяга…» — не более трёх названий в строке."""
    if not names:
        return ""
    shown = ", ".join(names[:3])
    return shown + ("…" if len(names) > 3 else "")


def build_achievements_message(new: list[tuple]) -> str:
    """Текст мгновенного уведомления о новых ачивках.

    new — [(код, деталь)] из check_new_achievements. Ачивки идут
    в каноническом порядке ACHIEVEMENT_ORDER.
    """
    order = {c: i for i, c in enumerate(ACHIEVEMENT_ORDER)}
    lines = ["🏆 <b>Новые достижения!</b>", ""]
    for code, detail in sorted(new, key=lambda x: order.get(x[0], 99)):
        if code not in ACHIEVEMENTS:
            _, year, month = code.rsplit("_", 2)
            period = date(int(year), int(month), 1)
            challenge = next(ch for ch in monthly_challenges(period) if ch.code == code)
            goal_title = escape(challenge.goal.title.format(target=challenge.goal.target))
            lines.append(f"🎯 <b>{month_title(period)}</b> — {goal_title}")
            continue
        a = ACHIEVEMENTS[code]
        suffix = f" — {detail}" if detail else ""
        lines.append(f"{a['icon']} <b>{a['title']}</b> — {a['desc']}{suffix}")
    return "\n".join(lines)


def has_seasonal_archive(today: date) -> bool:
    return today >= next_month(FIRST_CHALLENGE_MONTH)


def yearly_achievement_code(year: int) -> str:
    return f"year_review_{year}"


def _render_count(value, target, unit: str, status: str,
                  exercises: list[tuple[str, str, str]]) -> list[str]:
    progress = format(Decimal(value).normalize(), "f")
    goal = format(Decimal(target).normalize(), "f")
    lines = [f"<b>{progress}/{goal}</b> {escape(unit)}{status}"]
    if exercises:
        shown = exercises[:min(3, int(target))]
        counts = Counter(name for name, _, _ in shown)
        lines.append("В зачёте:")
        for name, kind, result in shown:
            if counts[name] > 1:
                name += " (кардио)" if kind == "cardio" else " (силовое)"
            lines.append(f"• {escape(name)} — <b>{escape(result)}</b>")
        if len(exercises) > len(shown):
            lines.append(f"Ещё {len(exercises) - len(shown)}")
    return lines


def render_seasonal_challenges(*, codes: set, session_dates: list[date], all_sets: list,
                               today: date) -> list[str]:
    start = max(today.replace(day=1), FIRST_CHALLENGE_MONTH)
    heading = f"<b>{month_title(start)}</b>"
    if today < start:
        heading += f" · Старт {start:%d.%m.%Y}"
    challenges = monthly_challenges(start)
    if not challenges:
        return ["🎯 <b>Сезонные</b>", "Сейчас нет активных челленджей."]
    lines = ["🎯 <b>Сезонные</b>", heading]
    for challenge in challenges:
        progress, exercises = challenge.progress(session_dates, all_sets, today)
        status = " · ✅ Цель выполнена" if challenge.code in codes else ""
        lines.extend(_render_count(progress, challenge.goal.target, challenge.goal.unit,
                                   status, exercises))
    return lines


def render_challenge_archive(results: list) -> str:
    lines = ["📦 <b>Архив челленджей</b>", ""]
    if not results:
        lines.append("Архив пока пуст. Итог сохраняется после закрытия месяца "
                     "и завершения или отмены оставшихся тренировок.")
    else:
        lines.append(f"<b>{month_title(results[0].period_start)}</b>")
        for goal in results:
            status = " · ✅ Цель выполнена" if goal.goal_met else " · Цель не выполнена"
            exercises = [(row["name"], row["kind"], row["result"])
                         for row in goal.details.get("exercises", [])]
            lines.extend(_render_count(goal.actual_value, goal.target_value, goal.metric_unit,
                                       status, exercises))
    return "\n".join(lines)


def render_achievements_screen(*, codes: set, session_dates: list,
                               workouts_count: int, exercises_count: int,
                               pr_total: int, weight_gain_names: list,
                               today, all_sets: list) -> str:
    """Экран «🏆 Ачивки»: разблокированные ✅ и замки 🔒 с прогрессом."""
    best_month = best_monthly_count(set(session_dates))
    known = set(ACHIEVEMENT_ORDER)
    unlocked = len(set(codes) & known)

    def line(code, current=None, target=None, unit="", detail=""):
        a = ACHIEVEMENTS[code]
        if code in codes:
            extra = f" · {escape(detail)}" if detail else ""
            return f"✅ {a['icon']} <b>{a['title']}</b> — {a['desc']}{extra}"
        if current is not None and target is not None:
            cur = min(current, target)
            return f"🔒 {a['icon']} {a['title']} — {cur}/{target}{unit}"
        return f"🔒 {a['icon']} {a['title']} — {a['desc']}"

    lines = [
        f"🏆 <b>Достижения — {unlocked} из {len(ACHIEVEMENT_ORDER)}</b>",
        "<i>Разблокируются автоматически при завершении тренировки.</i>",
        "",
        *render_seasonal_challenges(
            codes=codes, session_dates=session_dates, all_sets=all_sets, today=today,
        ),
        "",
        "💪 <b>Регулярность</b>",
        line("workouts_10", workouts_count, 10),
        line("workouts_50", workouts_count, 50),
        line("workouts_100", workouts_count, 100),
        line("workouts_250", workouts_count, 250),
        line("monthly_12", best_month, 12, " тренировок за месяц"),
        line("monthly_20", best_month, 20, " тренировок за месяц"),
        "",
        "🏋️ <b>Рекорды</b>",
        line("pr_first", pr_total if pr_total else None, 1),
        line("pr_10", pr_total if pr_total else None, 10),
        line("pr_50", pr_total if pr_total else None, 50),
        line("pr_3_session"),
        line("weight_plus_20", detail=_join_names(weight_gain_names)),
        "",
        "🧩 <b>Разнообразие</b>",
    ]
    for n in EXERCISES_MILESTONES:
        lines.append(
            line(f"exercises_{n}",
                 exercises_count if exercises_count else None,
                 n, " упр."))
    yearly_codes = sorted((code for code in codes if code.startswith("year_review_")),
                          reverse=True)
    if yearly_codes:
        lines.extend(["", "🎖 <b>Памятные</b>"])
        for code in yearly_codes:
            year = escape(code.removeprefix("year_review_"))
            lines.append(f"✅ <b>Тренировочный год {year}</b>")
    return "\n".join(lines)
