"""Тесты сервиса достижений (C2.6, Блок 3).

По образцу test_pr_service.py: SessionSet/WorkoutSession/Exercise
создаются как plain-объекты, без БД."""
from datetime import date, datetime, timedelta, timezone

from app.db.models import Exercise, SessionSet, WorkoutSession
from app.services import achievement_service as ach


def _sess(day, sess_id=1, month=11, year=2026):
    """Фейковая завершённая сессия с нужным днём (UTC, в МСК = +3ч)."""
    return WorkoutSession(
        id=sess_id, user_id=1, workout_type_id=1,
        started_at=datetime(year, month, day, 12, 0, tzinfo=timezone.utc),
        finished_at=datetime(year, month, day, 13, 0, tzinfo=timezone.utc),
    )


def _s(reps, weight, sess, set_number=1, set_id=None,
       name="Жим", kind="strength"):
    """Силовой подход с привязкой к упражнению (name+kind)."""
    ex = Exercise(id=set_id or 1, name=name, kind=kind)
    return SessionSet(reps=reps, weight_kg=weight, set_number=set_number,
                      session=sess, id=set_id, exercise_id=set_id or 1,
                      exercise=ex)


def _c(duration, sess, set_number=1, set_id=None, name="Бег"):
    """Кардио-подход с длительностью."""
    ex = Exercise(id=set_id or 1, name=name, kind="cardio")
    return SessionSet(duration_seconds=duration, set_number=set_number,
                      session=sess, id=set_id, exercise_id=set_id or 1,
                      exercise=ex)


# ─── compute_streak ───────────────────────────────────────────────

def test_streak_empty_history():
    assert ach.compute_streak(set(), date(2026, 11, 19)) == 0


def test_streak_counts_back_from_today():
    days = {date(2026, 11, 15) + timedelta(days=i) for i in range(5)}
    assert ach.compute_streak(days, date(2026, 11, 19)) == 5


def test_streak_survives_until_end_of_tomorrow():
    """Тренировка вчера — серия ещё жива (весь сегодняшний день)."""
    days = {date(2026, 11, 14) + timedelta(days=i) for i in range(5)}
    assert ach.compute_streak(days, date(2026, 11, 19)) == 5


def test_streak_dead_after_full_gap_day():
    days = {date(2026, 11, 14) + timedelta(days=i) for i in range(5)}
    assert ach.compute_streak(days, date(2026, 11, 20)) == 0


def test_streak_counts_only_tail():
    """Пропуск внутри истории: считается только «хвост» от последнего дня."""
    days = {date(2026, 11, 1), date(2026, 11, 2),
            date(2026, 11, 17), date(2026, 11, 18)}
    assert ach.compute_streak(days, date(2026, 11, 18)) == 2


def test_streak_single_session_today():
    assert ach.compute_streak({date(2026, 11, 19)}, date(2026, 11, 19)) == 1


# ─── count_pr_events ──────────────────────────────────────────────

def test_pr_events_first_set_is_record():
    sets = [_s(10, "50", _sess(1, sess_id=1), set_id=1)]
    assert ach.count_pr_events(sets) == 1


def test_pr_events_growth_each_session():
    """Каждая тренировка бьёт прошлый максимум — каждый подход = PR."""
    sets = []
    for i, w in enumerate([40, 50, 60]):
        sets.append(_s(8, str(w), _sess(1 + i, sess_id=i + 1), set_id=i + 1))
    assert ach.count_pr_events(sets) == 3


def test_pr_events_plateau_not_record():
    """Тот же вес и меньше повторов — не рекорд."""
    s1 = _s(10, "50", _sess(1, sess_id=1), set_id=1)
    s2 = _s(8, "50", _sess(2, sess_id=2), set_id=2)
    assert ach.count_pr_events([s1, s2]) == 1


def test_pr_events_same_weight_more_reps_is_record():
    s1 = _s(10, "50", _sess(1, sess_id=1), set_id=1)
    s2 = _s(12, "50", _sess(2, sess_id=2), set_id=2)
    assert ach.count_pr_events([s1, s2]) == 2


def test_pr_events_pyramid_every_step_is_record():
    """Пирамида 40→50→60 против прошлого максимума 30: каждый шаг бьёт
    всё предыдущее — каждый подход считается PR (как нарастающий итог)."""
    base = _s(10, "30", _sess(1, sess_id=1), set_id=1)
    s2 = _s(10, "40", _sess(2, sess_id=2), set_id=2, set_number=1)
    s3 = _s(10, "50", _sess(2, sess_id=2), set_id=3, set_number=2)
    s4 = _s(10, "60", _sess(2, sess_id=2), set_id=4, set_number=3)
    # 40 не бьёт 30? бьёт. Все три бьют 30 -> 3 + 1 базовый
    assert ach.count_pr_events([base, s2, s3, s4]) == 4


def test_pr_events_cardio_duration():
    c1 = _c(300, _sess(1, sess_id=1), set_id=1)
    c2 = _c(400, _sess(2, sess_id=2), set_id=2)
    c3 = _c(350, _sess(3, sess_id=3), set_id=3)
    assert ach.count_pr_events([c1, c2, c3]) == 2


def test_pr_events_units_not_mixed():
    """«12 блоков» не бьёт «11 блоков»-планку через кг и наоборот."""
    s1 = _s(10, "50", _sess(1, sess_id=1), set_id=1, name="Тяга")
    s2 = _s(10, "12 блоков", _sess(2, sess_id=2), set_id=2, name="Тяга")
    # 12 блоков — первый замер в своей единице = рекорд; 50 кг тоже рекорд
    assert ach.count_pr_events([s1, s2]) == 2


# ─── weight_gain_achievements ─────────────────────────────────────

def _gain_sets(weights_by_day):
    """[(день, вес), ...] -> подходы одного упражнения."""
    sets, sid = [], 0
    for day, w in weights_by_day:
        sid += 1
        sets.append(_s(8, str(w) if isinstance(w, int) else w,
                       _sess(day, sess_id=sid), set_id=sid))
    return sets


def test_weight_gain_unlocks_after_window():
    # Окно: 1 ноября + 30 дней = 1 декабря. Планка за окно = 50.
    # 10 декабря: 60 кг >= 50 * 1.2 -> ачивка.
    sets = _gain_sets([(1, 50), (10, 45)])
    sets.append(_s(8, "60", _sess(10, sess_id=3, month=12), set_id=3))
    result = ach.weight_gain_achievements(sets)
    assert len(result) == 1
    assert "Жим" in result.values()


def test_weight_gain_not_enough_growth():
    sets = _gain_sets([(1, 50)])
    sets.append(_s(8, "59", _sess(10, sess_id=2, month=12), set_id=2))
    assert ach.weight_gain_achievements(sets) == {}


def test_weight_gain_20_percent_boundary():
    """Ровно 1.2× планки засчитывается (>=)."""
    sets = _gain_sets([(1, 50)])
    sets.append(_s(8, "60", _sess(2, sess_id=2, month=12), set_id=2))
    assert len(ach.weight_gain_achievements(sets)) == 1


def test_weight_gain_inside_window_does_not_unlock():
    """Рост внутри первых 30 дней поднимает планку, ачивки нет."""
    sets = _gain_sets([(1, 40), (15, 60), (20, 50)])
    assert ach.weight_gain_achievements(sets) == {}


def test_weight_gain_different_units_separate():
    """Блоки и кг не смешиваются: планка по блокам своя."""
    sets = [_s(8, "10 блоков", _sess(1, sess_id=1), set_id=1, name="Тяга"),
            _s(8, "12 блоков", _sess(10, sess_id=2, month=12), set_id=2,
                name="Тяга")]
    assert len(ach.weight_gain_achievements(sets)) == 1

    # То же по кг, но кандидат в блоках — не засчитывается
    sets2 = [_s(8, "50", _sess(1, sess_id=1), set_id=1, name="Жим"),
             _s(8, "12 блоков", _sess(10, sess_id=2, month=12), set_id=2,
                name="Жим")]
    assert ach.weight_gain_achievements(sets2) == {}


def test_weight_gain_multiple_exercises():
    sets = _gain_sets([(1, 50)])
    sets.append(_s(8, "65", _sess(10, sess_id=2, month=12), set_id=2))  # Жим
    sets += [_s(8, "30", _sess(1, sess_id=3), set_id=3, name="Становая"),
             _s(8, "36", _sess(10, sess_id=4, month=12), set_id=4,
                name="Становая")]
    result = ach.weight_gain_achievements(sets)
    assert len(result) == 2


# ─── check_new_achievements ───────────────────────────────────────

def _base_kwargs(**over):
    kw = dict(
        codes=set(), today=date(2026, 11, 19),
        session_dates=[date(2026, 11, 19)], workouts_count=1,
        exercises_count=1, pr_total=0, pr_in_session=0,
        weight_gain_names=[],
    )
    kw.update(over)
    return kw


def test_check_workouts_milestones():
    codes = [c for c, _ in ach.check_new_achievements(
        **_base_kwargs(workouts_count=100))]
    assert codes == ["workouts_10", "workouts_50", "workouts_100"]


def test_check_not_returned_twice():
    codes = {c for c, _ in ach.check_new_achievements(
        **_base_kwargs(codes={"workouts_10"}, workouts_count=10))}
    assert "workouts_10" not in codes


def test_check_streak():
    week = [date(2026, 11, 13) + timedelta(days=i) for i in range(7)]
    codes = [c for c, _ in ach.check_new_achievements(
        **_base_kwargs(session_dates=week))]
    assert "streak_7" in codes and "streak_30" not in codes


def test_check_pr_milestones_and_session():
    codes = [c for c, _ in ach.check_new_achievements(
        **_base_kwargs(pr_total=50, pr_in_session=3))]
    assert codes == ["pr_first", "pr_10", "pr_50", "pr_3_session"]


def test_check_weight_gain_detail():
    new = ach.check_new_achievements(
        **_base_kwargs(weight_gain_names=["Приседания"]))
    assert new == [("weight_plus_20", "Приседания")]


def test_check_exercises_milestone():
    codes = [c for c, _ in ach.check_new_achievements(
        **_base_kwargs(exercises_count=10))]
    assert codes == ["exercises_10"]


def test_check_exercises_gradations():
    """Градации разнообразия: 30 упр. закрывает 10 и 25, но не 50."""
    codes = [c for c, _ in ach.check_new_achievements(
        **_base_kwargs(exercises_count=30))]
    assert codes == ["exercises_10", "exercises_25"]


def test_screen_shows_exercises_gradations():
    text = ach.render_achievements_screen(
        codes=set(), session_dates=[], workouts_count=0,
        exercises_count=12, pr_total=0, weight_gain_names=[],
        today=date(2026, 11, 19))
    assert "🔒 🧩 Экспериментатор — 10/10 упр." in text
    assert "🔒 🎯 Коллекционер движений — 12/25 упр." in text
    assert "🔒 🧠 Энциклопедия упражнений — 12/50 упр." in text


def test_check_nothing_new():
    assert ach.check_new_achievements(**_base_kwargs()) == []


# ─── Тексты ───────────────────────────────────────────────────────

def test_achievements_message_contains_titles():
    text = ach.build_achievements_message(
        [("pr_first", ""), ("weight_plus_20", "Жим")])
    assert "Новые достижения" in text
    assert "Первый рекорд" in text
    assert "Вес +20%" in text and "Жим" in text
    # Канонический порядок: pr_first идёт раньше weight_plus_20
    assert text.index("Первый рекорд") < text.index("Вес +20%")


def test_screen_shows_progress_and_unlocked():
    week = [date(2026, 11, 13) + timedelta(days=i) for i in range(7)]
    text = ach.render_achievements_screen(
        codes={"workouts_10", "streak_7"},
        session_dates=week, workouts_count=14,
        exercises_count=4, pr_total=0, weight_gain_names=[],
        today=date(2026, 11, 19))
    assert "2 из 14" in text
    assert "✅ 🎖 Первая десятка · 14" in text
    assert "✅ 📅 Неделя без пропусков · 7 дн." in text
    assert "🔒 🥇 Полсотни тренировок — 14/50" in text
    assert "🔒 🔥 Первый рекорд" in text
    assert "🔒 🧩 Экспериментатор — 4/10 упр." in text


def test_screen_weight_gain_unlocked_with_name():
    text = ach.render_achievements_screen(
        codes={"weight_plus_20"}, session_dates=[], workouts_count=0,
        exercises_count=0, pr_total=0, weight_gain_names=["Приседания"],
        today=date(2026, 11, 19))
    assert "✅ 📈 Вес +20% · Приседания" in text
