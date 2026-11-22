"""Тесты для analytics_service — чистые функции, БД не нужна."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.services.analytics_service import (
    all_time_bests, available_retrospective_seasons, available_retrospective_years, average_rest,
    average_rest_between_exercises, best_training_month, calculate_tonnage,
    month_activity, month_top_stats, season_end, season_start, season_title, weekly_activity,
)

UTC = timezone.utc


def test_retrospective_years_open_in_december_and_keep_years_with_history():
    dates = [date(2024, 10, 1), date(2026, 11, 1)]
    assert available_retrospective_years(dates, date(2026, 11, 30)) == [2024]
    assert available_retrospective_years(dates, date(2026, 12, 1)) == [2024, 2026]
    assert available_retrospective_years(dates, date(2027, 1, 1)) == [2024, 2026]


def test_retrospective_best_month_counts_sessions_and_breaks_ties_by_date():
    assert best_training_month([]) is None
    dates = [date(2026, 12, 1)] * 2 + [date(2026, 1, 1)] * 2
    assert best_training_month(dates) == (date(2026, 1, 1), 2)


def test_seasonal_retrospective_includes_autumn_and_groups_winter_across_years():
    autumn = date(2026, 9, 1)
    winter = date(2026, 12, 1)
    spring = date(2027, 3, 1)
    dates = [date(2026, 10, 2), winter, date(2027, 1, 1), date(2027, 2, 28), spring]

    assert available_retrospective_seasons(dates, date(2026, 11, 30)) == [autumn]
    assert available_retrospective_seasons(dates, date(2027, 2, 28)) == [autumn, winter]
    assert available_retrospective_seasons(dates, spring) == [autumn, winter, spring]
    assert season_end(autumn) == winter
    assert season_end(winter) == spring
    assert season_title(winter) == "Зима 2026/27"
    assert season_start(date(2028, 2, 29)) == date(2027, 12, 1)
    assert [season_start(date(2027, month, 30)).month for month in (4, 7, 10)] == [3, 6, 9]


def make_set(weight=None, reps=None, duration=None, session_id=1,
             ex_id=1, ex_name="Жим лёжа", completed=True, completed_at=None,
             started_at=None):
    ex = SimpleNamespace(name=ex_name)
    sess = SimpleNamespace(id=session_id, started_at=started_at)
    return SimpleNamespace(
        session=sess, exercise=ex, session_id=session_id, exercise_id=ex_id,
        weight_kg=weight, reps=reps, duration_seconds=duration,
        completed=completed, completed_at=completed_at,
    )


def make_session(day, sets, session_id=1):
    """Завершённая сессия (по МСК) с заданным числом месяца и подходами."""
    started_at = datetime(2026, 11, day, 10, tzinfo=UTC)
    return SimpleNamespace(id=session_id, started_at=started_at, sets=sets)


def test_all_time_bests_uses_msk_and_calendar_weeks_across_months():
    sessions = [
        make_session(2, [
            make_set(weight="50", reps=10),
            make_set(weight="99 блоков", reps=10),
            make_set(weight="120", reps=10, completed=False),
        ]),
        make_session(1, [make_set(weight="50", reps=10)]),
        SimpleNamespace(id=3, started_at=datetime(2026, 10, 30, 22, tzinfo=UTC),
                        sets=[make_set(weight="50", reps=10)]),
        SimpleNamespace(started_at=datetime(2026, 10, 31, 12, tzinfo=UTC), sets=[]),
    ]

    assert all_time_bests(sessions) == (
        (date(2026, 10, 1), 2),
        (date(2026, 10, 26), 3),
        (date(2026, 10, 31), 500.0, 3),
    )


def test_all_time_bests_handles_empty_history_and_missing_kg():
    assert all_time_bests([]) is None
    assert all_time_bests([make_session(1, [make_set(weight="12 блоков", reps=10)])]) == (
        (date(2026, 11, 1), 1), (date(2026, 10, 26), 1), None,
    )


# ─── calculate_tonnage ───────────────────────────────────────────
def test_tonnage_sums_numeric_weights():
    sets = [
        make_set(weight="50", reps=10),   # 500
        make_set(weight="50", reps=8),    # 400
        make_set(weight="25.5", reps=12),  # 306
    ]
    assert calculate_tonnage(sets) == 1206.0


def test_tonnage_skips_text_weight_and_incomplete():
    sets = [
        make_set(weight="Своё тело", reps=15),  # без числа — пропуск
        make_set(weight="50", reps=10, completed=False),  # не завершён
        make_set(weight=None, reps=10),
        make_set(weight="10", reps=None),
    ]
    assert calculate_tonnage(sets) == 0.0


# ─── month_activity ─────────────────────────────────────────────
def test_month_activity_counts_sets_and_unique_exercises():
    sets = [
        make_set(ex_id=1), make_set(ex_id=1), make_set(ex_id=2),
        make_set(ex_id=3, completed=False),  # не завершён — не считается
    ]
    total_sets, total_ex = month_activity(sets)
    assert total_sets == 3
    assert total_ex == 2


def test_month_activity_empty():
    assert month_activity([]) == (0, 0)


# ─── month_top_stats ─────────────────────────────────────────────
def test_top_stats_max_weight():
    sets = [
        make_set(weight="50", reps=10),
        make_set(weight="60", reps=5),
        make_set(weight="60", reps=8),
        make_set(weight="Своё тело", reps=15),  # текстовый вес не ломает max
    ]
    top = month_top_stats(sets)
    assert top[0] == ("Жим лёжа", 4, "60 кг")


def test_top_stats_ranks_by_set_count():
    sets = [
        make_set(ex_name="Жим лёжа"), make_set(ex_name="Жим лёжа"),
        make_set(ex_name="Присед"), make_set(ex_name="Тяга"),
        make_set(ex_name="Тяга"), make_set(ex_name="Тяга"),
        make_set(ex_name="Жим лёжа", completed=False),  # не считается
    ]
    top = month_top_stats(sets)
    assert top[0][:2] == ("Тяга", 3)
    assert top[1][:2] == ("Жим лёжа", 2)
    assert len(top) == 3


def test_top_stats_text_weight():
    sets = [make_set(weight="9 блоков", reps=10),
            make_set(weight="9 блоков", reps=12)]
    assert month_top_stats(sets)[0][2] == "9 блоков"


def test_top_stats_max_among_text_weights():
    # Разные блоки за месяц — выводим максимальное количество
    sets = [make_set(weight="6 блоков", reps=10),
            make_set(weight="8 блоков", reps=10),
            make_set(weight="9 блоков", reps=10)]
    assert month_top_stats(sets)[0][2] == "9 блоков"


def test_top_stats_bodyweight_without_number():
    sets = [make_set(weight="Своё тело", reps=15),
            make_set(weight="Своё тело", reps=12)]
    assert month_top_stats(sets)[0][2] == "Своё тело"


def test_top_stats_cardio_best_duration():
    sets = [make_set(duration=600), make_set(duration=900)]
    top = month_top_stats(sets)
    assert top[0][1] == 2
    assert top[0][2] == "15 мин"


def test_top_stats_empty():
    assert month_top_stats([]) == []


# ─── average_rest ────────────────────────────────────────────────
def _t(day, h, m=0, s=0):
    return datetime(2026, 11, day, h, m, s, tzinfo=UTC)


def test_average_rest_median_within_exercise():
    # Подходы одного упражнения: интервалы 90 и 150 сек → медиана 120
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 0)),
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 1, 30)),
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 4, 0)),
    ]
    assert average_rest(sets) == 120.0


def test_average_rest_ignores_long_breaks():
    # Интервал 40 мин — перерыв, не отдых: данных не остаётся
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 0)),
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 40)),
    ]
    assert average_rest(sets) is None


def test_average_rest_separates_exercises():
    # Разные упражнения в одной сессии не перемешиваются
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10)),
        make_set(session_id=1, ex_id=2, completed_at=_t(10, 12)),
    ]
    assert average_rest(sets) is None


def test_average_rest_no_data():
    assert average_rest([]) is None


# ─── average_rest_between_exercises ──────────────────────────────
def test_rest_between_exercises_median():
    # Присед: 2 подхода, жим: 1 подход → один переход, интервал 5 мин
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 0)),
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 12)),
        make_set(session_id=1, ex_id=2, completed_at=_t(10, 10, 17)),
    ]
    assert average_rest_between_exercises(sets) == 300.0


def test_rest_between_exercises_ignores_same_exercise_intervals():
    # Интервалы внутри одного упражнения не считаются «между упражнениями»
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 0)),
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 12, 0)),
    ]
    assert average_rest_between_exercises(sets) is None


def test_rest_between_exercises_ignores_long_transition():
    # Переход 30 мин — уже перерыв, а не отдых между упражнениями
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 0)),
        make_set(session_id=1, ex_id=2, completed_at=_t(10, 10, 40)),
    ]
    assert average_rest_between_exercises(sets) is None


def test_rest_between_exercises_separates_sessions():
    # Переход между тренировками не считается
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 0)),
        make_set(session_id=2, ex_id=2, completed_at=_t(10, 15, 0)),
    ]
    assert average_rest_between_exercises(sets) is None


def test_rest_between_exercises_skips_uncompleted():
    sets = [
        make_set(session_id=1, ex_id=1, completed_at=_t(10, 10, 0)),
        make_set(session_id=1, ex_id=2, completed=False,
                 completed_at=_t(10, 15, 0)),
    ]
    assert average_rest_between_exercises(sets) is None


# ─── weekly_activity ─────────────────────────────────────────────
def test_weekly_activity_buckets_sessions_and_exercises():
    s1 = make_session(3, [make_set(session_id=1, ex_id=1)])   # бакет 0
    s2 = make_session(5, [make_set(session_id=2, ex_id=2),
                          make_set(session_id=2, ex_id=3)])  # бакет 0
    # бакет 1: два подхода одного упражнения = 1 уникальное упражнение
    s3 = make_session(12, [make_set(session_id=3, ex_id=1),
                           make_set(session_id=3, ex_id=1)])
    # бакет 3: подход не завершён — тренировка есть, упражнение не считается
    s4 = make_session(25, [make_set(session_id=4, ex_id=4,
                                    completed=False)])

    trainings, exercises = weekly_activity([s1, s2, s3, s4])
    assert trainings == [2, 1, 0, 1, 0]
    assert exercises == [3, 1, 0, 0, 0]


def test_weekly_activity_msk_boundary():
    # 31 октября 23:30 UTC = 1 ноября 02:30 МСК → бакет 0 (неделя 1–7)
    started_at = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)
    s = SimpleNamespace(id=1, started_at=started_at,
                        sets=[make_set(session_id=1, ex_id=1)])
    trainings, exercises = weekly_activity([s])
    assert trainings == [1, 0, 0, 0, 0]
    assert exercises == [1, 0, 0, 0, 0]


def test_weekly_activity_empty():
    empty = ([0, 0, 0, 0, 0], [0, 0, 0, 0, 0])
    assert weekly_activity([]) == empty
