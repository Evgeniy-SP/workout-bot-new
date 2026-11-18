"""Тесты сервиса PR (C2.6). По образцу test_records.py: SessionSet
создаются как plain-объекты, без БД."""
from datetime import datetime, timezone

from app.db.models import SessionSet, WorkoutSession
from app.services.pr_service import (
    build_pr_message,
    build_pr_summary,
    classify_duration_pr,
    classify_pr,
    raw_session_pr_events,
    session_pr_events,
)


def _s(reps, weight, set_number=1, sess=None, set_id=None):
    return SessionSet(reps=reps, weight_kg=weight, set_number=set_number,
                      session=sess, id=set_id, exercise_id=1)


def _c(duration, set_number=1, sess=None, set_id=None):
    """Кардио-подход с длительностью."""
    return SessionSet(duration_seconds=duration, set_number=set_number,
                      session=sess, id=set_id, exercise_id=1)


def _sess(day):
    """Фейковая завершённая сессия с нужным днём (для дат в сообщениях)."""
    return WorkoutSession(
        id=999, user_id=1, workout_type_id=1,
        started_at=datetime(2026, 11, day, 12, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 11, day, 13, 0, tzinfo=timezone.utc),
    )


# ─── classify_pr ──────────────────────────────────────────────────

def test_first_weighted_set_is_weight_pr():
    """Первый взвешенный подход = рекорд по весу."""
    assert classify_pr(_s(10, "50"), []) == "weight"


def test_text_weight_is_not_pr():
    """«Своё тело» — не рекорд."""
    assert classify_pr(_s(10, "Своё тело"), [_s(8, "40")]) is None


def test_higher_weight_is_pr():
    prior = [_s(10, "50"), _s(5, "60")]
    assert classify_pr(_s(8, "62.5"), prior) == "weight"


def test_same_weight_more_reps_is_reps_pr():
    prior = [_s(5, "60"), _s(8, "60")]
    assert classify_pr(_s(10, "60"), prior) == "reps"


def test_same_weight_same_reps_is_not_pr():
    prior = [_s(8, "60")]
    assert classify_pr(_s(8, "60"), prior) is None


def test_lower_weight_more_reps_is_not_pr():
    """Много повторов на меньшем весе — не рекорд (строгое правило)."""
    prior = [_s(5, "80")]
    assert classify_pr(_s(30, "20"), prior) is None


def test_weight_below_max_but_reps_at_max_compared_to_max_weight():
    """Повторы считаются только при весе == максимуму."""
    prior = [_s(3, "80"), _s(20, "40")]
    assert classify_pr(_s(25, "40"), prior) is None
    assert classify_pr(_s(4, "80"), prior) == "reps"


# ─── session_pr_events ────────────────────────────────────────────

def test_session_events_ignore_within_session_progression():
    """Подходы внутри одной тренировки не сравниваются между собой:
    пирамида 40→50→60 кг даст ОДИН рекорд (относительно прошлых тренировок)."""
    prior = [_s(10, "30")]
    current = [_s(10, "40", set_number=1, set_id=1),
               _s(8, "50", set_number=2, set_id=2),
               _s(6, "60", set_number=3, set_id=3)]
    events = session_pr_events(current, prior)
    assert len(events) == 1
    assert events[0][1] == "weight"
    assert events[0][0].weight_kg == "60"


def test_session_events_empty_when_no_prs():
    prior = [_s(10, "100")]
    current = [_s(5, "80", set_number=1)]
    assert session_pr_events(current, prior) == []


# ─── build_pr_message ─────────────────────────────────────────────

def test_message_weight_pr_with_history():
    prior = [_s(5, "60", sess=_sess(10))]
    msg = build_pr_message("Жим лёжа", _s(5, "62.5"), "weight", prior, 2, 3)
    assert "Новый рекорд по весу" in msg
    assert "62" in msg and "кг" in msg          # 62,5 кг
    assert "60" in msg and "10.11" in msg       # было 60 кг от 10.11.2026
    assert "2-й рекорд в «Жим лёжа»" in msg
    assert "3-й за тренировку" in msg


def test_message_first_ever_weight():
    msg = build_pr_message("Приседания", _s(10, "50"), "weight", [], 1, 1)
    assert "первый замер с этим типом веса" in msg
    assert "1-й рекорд в «Приседания»" in msg
    assert "1-й за тренировку" in msg


def test_message_reps_pr():
    prior = [_s(8, "60", sess=_sess(3))]
    msg = build_pr_message("Жим", _s(10, "60"), "reps", prior, 2, 5)
    assert "10 повт." in msg
    assert "было 8 повт." in msg and "03.11" in msg
    assert "2-й рекорд в «Жим»" in msg
    assert "5-й за тренировку" in msg


# ─── build_pr_summary ─────────────────────────────────────────────

def test_summary_format():
    current = [_s(6, "100", set_number=1, set_id=1),
               _s(8, "60", set_number=1, set_id=2, )]
    current[1].exercise_id = 2
    events = [(current[0], "weight"), (current[1], "reps")]
    names = {1: "Приседания", 2: "Жим лёжа"}
    summary = build_pr_summary(events, names)
    assert "Рекорды за тренировку (2)" in summary
    assert "▪️ Приседания — вес 100" in summary
    assert "▪️ Жим лёжа — 8 повт. при 60 кг" in summary


def test_summary_none_when_empty():
    assert build_pr_summary([], {}) is None


# ─── Кардио: рекорд по времени ────────────────────────────────────

def test_duration_first_ever_is_pr():
    assert classify_duration_pr(_c(600), []) == "duration"


def test_duration_longer_is_pr():
    prior = [_c(600), _c(900)]
    assert classify_duration_pr(_c(1200), prior) == "duration"


def test_duration_shorter_is_not_pr():
    prior = [_c(900)]
    assert classify_duration_pr(_c(600), prior) is None


def test_duration_equal_is_not_pr():
    prior = [_c(900)]
    assert classify_duration_pr(_c(900), prior) is None


def test_message_duration_pr():
    prior = [_c(900, sess=_sess(5))]
    msg = build_pr_message("Бег", _c(1200), "duration", prior, 1, 2)
    assert "Новый рекорд по времени" in msg
    assert "1-й рекорд в «Бег»" in msg
    assert "2-й за тренировку" in msg


# ─── Единицы измерения: блоки ≠ кг ────────────────────────────────

def test_blocks_beat_blocks_despite_kg_in_history():
    """Главный багфикс: история «50», «51» (кг) и «11 блоков».
    «12 блоков» — рекорд, потому что бьёт именно 11 блоков, а не 51 кг."""
    prior = [_s(10, "50"), _s(10, "51"), _s(10, "11 блоков")]
    assert classify_pr(_s(8, "12 блоков"), prior) == "weight"


def test_kg_is_not_compared_to_blocks():
    """«13» (кг) против истории из «15 блоков» — это первый замер в кг,
    значит рекорд (а не «13 < 15, рекорда нет»)."""
    prior = [_s(10, "15 блоков")]
    assert classify_pr(_s(10, "13"), prior) == "weight"


def test_kg_below_kg_max_is_not_pr_even_with_blocks():
    """«13» кг против «50», «51» кг — рекорда нет, даже если в истории
    есть блоки (они не должны влиять на кг-сравнение)."""
    prior = [_s(10, "50"), _s(10, "51"), _s(10, "100 блоков")]
    assert classify_pr(_s(10, "13"), prior) is None


def test_same_weight_different_units_both_are_records():
    """50 кг и 50 блоков — разные единицы, каждый первый в своей = рекорд."""
    prior = [_s(10, "50")]
    assert classify_pr(_s(10, "50 блоков"), prior) == "weight"


def test_bodyweight_still_not_pr():
    """«Своё тело» по-прежнему не рекорд."""
    prior = [_s(10, "15 блоков"), _s(10, "50")]
    assert classify_pr(_s(10, "Своё тело"), prior) is None


def test_message_blocks_prior_in_same_unit():
    """В сообщении «было ...» показывается прошлый максимум в блоках,
    а не в кг из той же истории."""
    prior = [_s(10, "50", sess=_sess(10)), _s(10, "11 блоков", sess=_sess(12))]
    msg = build_pr_message("Тяга блока", _s(8, "12 блоков"), "weight",
                           prior, 1, 1)
    assert "11 блоков" in msg
    assert "12.11" in msg
    assert "50" not in msg.split("было")[1].split("\n")[0]  # «было» — не про кг


# ─── raw_session_pr_events ────────────────────────────────────────

def test_raw_events_counts_pyramid_steps():
    """«Сырые» события: каждый шаг пирамиды — отдельное событие
    (нужно для счётчика «N-й рекорд» в мгновенных сообщениях)."""
    prior = [_s(10, "50")]
    current = [_s(8, "60", set_number=1, set_id=1),
               _s(5, "70", set_number=2, set_id=2),
               _s(3, "80", set_number=3, set_id=3)]
    raw = raw_session_pr_events(current, prior)
    assert len(raw) == 3


def test_dedup_keeps_only_best_of_pyramid():
    """Итоги тренировки показывают только лучший подход пирамиды."""
    prior = [_s(10, "50")]
    s60 = _s(8, "60", set_number=1, set_id=1)
    s70 = _s(5, "70", set_number=2, set_id=2)
    s80 = _s(3, "80", set_number=3, set_id=3)
    current = [s60, s70, s80]

    events = session_pr_events(current, prior)
    assert len(events) == 1           # в итогах — только 80
    assert events[0][0].weight_kg == "80"


def test_dedup_keeps_weight_and_reps_independently():
    """Рекорды разных типов независимы: вес и повторы — оба в итогах."""
    prior = [_s(8, "60")]               # прошлый максимум: 60 кг × 8
    s_weight = _s(5, "62.5", set_number=1, set_id=1)
    s_reps = _s(10, "60", set_number=2, set_id=2)
    events = session_pr_events([s_weight, s_reps], prior)

    kinds = {k for _, k in events}
    assert kinds == {"weight", "reps"}
