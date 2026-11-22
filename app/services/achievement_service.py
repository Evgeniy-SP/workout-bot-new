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
from datetime import date, datetime, timedelta, timezone
from html import escape

from app.services.pr_service import (
    _unit_key, classify_duration_pr, classify_pr, split_weight,
)
from app.utils import format_duration, format_weight, to_msk

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
class SeasonalChallenge:
    code: str
    title: str
    start: date
    end: date  # первый день после окончания
    target: int
    unit: str
    goal: str
    by_pr_exercise: bool = False

    def progress(self, session_dates: list[date], all_sets: list,
                 today: date) -> tuple[int, list[tuple[str, str, str]]]:
        end = self.end if today >= self.end else today + timedelta(days=1)
        if end <= self.start:
            return 0, []
        if self.by_pr_exercise:
            exercises = pr_exercises(all_sets, self.start, end)
            return len(exercises), exercises
        return sum(self.start <= day < end for day in session_dates), []


SEASONAL_CHALLENGES = (
    SeasonalChallenge("monthly_12_2026_12", "Декабрь 2026",
                      date(2026, 12, 1), date(2027, 1, 1), 12,
                      "завершённых тренировок", "12 тренировок за месяц"),
    SeasonalChallenge("pr_exercises_3_2026_12", "Декабрь 2026",
                      date(2026, 12, 1), date(2027, 1, 1), 3,
                      "разных упр. с личным рекордом", "личный рекорд в 3 разных упражнениях",
                      by_pr_exercise=True),
)

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

    codes = await get_user_achievement_codes(db_session, user_id)
    all_sets = await get_all_completed_sets(db_session, user_id)
    session_dates = await get_finished_session_dates(db_session, user_id)
    today = today or to_msk(datetime.now(timezone.utc)).date()

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
    for challenge in SEASONAL_CHALLENGES:
        if challenge.code not in codes:
            progress, _ = challenge.progress(session_dates, all_sets, today)
            if progress >= challenge.target:
                new.append((challenge.code, ""))
    return [(code, detail) for code, detail in new
            if await add_achievement(db_session, user_id, code, skip_existing=True)]


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
    seasonal = {challenge.code: challenge for challenge in SEASONAL_CHALLENGES}
    lines = ["🏆 <b>Новые достижения!</b>", ""]
    for code, detail in sorted(new, key=lambda x: order.get(x[0], 99)):
        if code in seasonal:
            challenge = seasonal[code]
            lines.append(f"🎯 <b>{challenge.title}</b> — {challenge.goal}")
            continue
        a = ACHIEVEMENTS[code]
        suffix = f" — {detail}" if detail else ""
        lines.append(f"{a['icon']} <b>{a['title']}</b> — {a['desc']}{suffix}")
    return "\n".join(lines)


def has_seasonal_archive(today: date) -> bool:
    return any(challenge.end <= today for challenge in SEASONAL_CHALLENGES)


def render_seasonal_challenges(*, codes: set, session_dates: list[date], all_sets: list,
                               today: date, archive: bool = False) -> list[str]:
    title = "📦 <b>Архив сезонных челленджей</b>" if archive else "🎯 <b>Сезонные</b>"
    periods: dict[tuple[str, date, date], list[str]] = {}
    for challenge in SEASONAL_CHALLENGES:
        if (challenge.end <= today) != archive:
            continue
        period = (challenge.title, challenge.start, challenge.end)
        heading = f"<b>{challenge.title}</b>"
        if today < challenge.start:
            heading += f" · Старт {challenge.start:%d.%m.%Y}"
        lines = periods.setdefault(period, [heading])
        progress, exercises = challenge.progress(session_dates, all_sets, today)
        if challenge.code in codes:
            status = " · ✅ Цель выполнена"
        elif archive:
            status = " · Цель не выполнена"
        else:
            status = ""
        lines.append(f"<b>{progress}/{challenge.target}</b> {challenge.unit}{status}")
        if exercises:
            shown = exercises[:challenge.target]
            counts = Counter(name for name, _, _ in shown)
            lines.append("В зачёте:")
            for name, kind, result in shown:
                if counts[name] > 1:
                    name += " (кардио)" if kind == "cardio" else " (силовое)"
                lines.append(f"• {escape(name)} — <b>{escape(result)}</b>")
            if progress > len(shown):
                lines.append(f"Ещё {progress - len(shown)}")
    if not periods:
        return [title, "Архив пока пуст." if archive else "Сейчас нет активных челленджей."]

    result = [title]
    for lines in periods.values():
        if len(result) > 1:
            result.append("")
        result.extend(lines)
    return result


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
    return "\n".join(lines)
