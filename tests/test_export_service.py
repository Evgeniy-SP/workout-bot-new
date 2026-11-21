"""Тесты сервиса CSV-экспорта (C2.6 Блок 4)."""

from datetime import date, datetime

import pytest

from app.services.export_service import (
    DELIMITER,
    _slug,
    _weight_cell,
    build_exercise_csv,
    build_month_csv,
)


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig")


def test_slug_cleans_special_chars():
    assert _slug("Жим лёжа (штанга)") == "Жим_лёжа_штанга"
    assert _slug("!!!") == "export"


def test_weight_cell_excel_russian_locale():
    # Числовые кг → запятая (ru-локаль Excel), текстовые — как есть
    assert _weight_cell("50") == "50"
    assert _weight_cell("52.5") == "52,5"
    assert _weight_cell("Своё тело") == "Своё тело"
    assert _weight_cell("12 блоков") == "12 блоков"
    assert _weight_cell(None) == ""


def test_csv_uses_bom_and_semicolon():
    filename, data = build_exercise_csv(
        "Жим", "strength",
        [{"date": date(2026, 11, 20), "workout": "Грудь", "set_number": 1,
          "weight": "50", "reps": 10, "duration": None}],
        formed_on=date(2026, 11, 20),
    )
    assert data.startswith(b"\xef\xbb\xbf")  # BOM
    assert DELIMITER == ";"
    text = _decode(data)
    assert (text.splitlines()[0]
            == "Дата;Тренировка;Подход;Вес;Повторы;Интервал")
    assert "2026-11-20;Грудь;1;50;10;" in text  # один подход → интервал пуст
    assert filename == "history_Жим_2026-11-20.csv"


def test_strength_rows_have_no_duration_column():
    _, data = build_exercise_csv("Присед", "strength", [
        {"date": date(2026, 11, 1), "set_number": 1,
         "weight": "60", "reps": 8, "duration": None, "rest_after": 90},
        {"date": date(2026, 11, 1), "set_number": 2,
         "weight": None, "reps": None, "duration": None, "rest_after": None},
    ])
    lines = _decode(data).splitlines()
    assert len(lines) == 3  # шапка + 2 строки
    assert lines[1].endswith("1м 30с")  # интервал 90 сек, формат экрана
    assert lines[2].endswith(";")  # последний подход — без интервала


def test_exercise_csv_rest_none_is_blank():
    """Без интервала (первый/последний подход, давний переход) — пусто."""
    _, data = build_exercise_csv("Жим", "strength", [
        {"date": date(2026, 11, 1), "set_number": 1,
         "weight": "60", "reps": 8, "duration": None, "rest_after": None},
    ])
    assert _decode(data).splitlines()[1].endswith(";")


def test_cardio_has_duration_column():
    _, data = build_exercise_csv("Бег", "cardio", [
        {"date": date(2026, 11, 2), "workout": "Кардио", "set_number": 1,
         "weight": None, "reps": None, "duration": 600},
        {"date": date(2026, 11, 3), "workout": "Кардио", "set_number": 1,
         "weight": None, "reps": None, "duration": 3720},
    ])
    text = _decode(data)
    assert (text.splitlines()[0]
            == "Дата;Тренировка;Подход;Вес;Повторы;Длительность;Интервал")
    # Формат мин/сек, как на экранах бота
    assert "10 мин" in text
    assert "62 мин" in text


def test_datetime_dates_are_truncated():
    _, data = build_exercise_csv("Жим", "strength", [
        {"date": datetime(2026, 11, 20, 18, 30), "workout": "Грудь",
         "set_number": 1, "weight": "40", "reps": 12, "duration": None},
    ])
    assert "2026-11-20;Грудь;1;40;12;" in _decode(data)


def test_month_csv_structure_correct():
    sessions = [
        {
            "date": date(2026, 11, 5),
            "workout_name": "Грудь",
            "exercises": ["Жим лёжа", "Отжимания"],
            "sets_count": 8,
            "started_at": datetime(2026, 11, 5, 18, 0),
            "finished_at": datetime(2026, 11, 5, 19, 15),
            "tonnage": 3200,
        },
        {
            "date": date(2026, 11, 7),
            "workout_name": "Спина",
            "exercises": ["Тяга"],
            "sets_count": 6,
            "started_at": None,
            "finished_at": None,
            "tonnage": 1800,
        },
    ]
    filename, data = build_month_csv(2026, 11, sessions,
                                     formed_on=date(2026, 11, 20))
    text = _decode(data)
    assert text.splitlines()[0] == (
        "Дата;Тренировка;Упражнения;Подходы;Время;Тоннаж")
    assert "2026-11-05;Грудь;Жим лёжа, Отжимания;8;1 ч 15 мин;3200" in text
    assert "2026-11-07;Спина;Тяга;6;;1800" in text  # нет времени → пустая ячейка
    assert filename == "workouts_2026-11_2026-11-20.csv"


def test_month_csv_empty_sessions():
    filename, data = build_month_csv(2026, 11, [],
                                     formed_on=date(2026, 11, 20))
    assert _decode(data).splitlines() == [
        "Дата;Тренировка;Упражнения;Подходы;Время;Тоннаж"]
    assert filename == "workouts_2026-11_2026-11-20.csv"


@pytest.mark.parametrize("kind", ["strength", "cardio"])
def test_exercise_csv_parametrized_kinds(kind):
    row = {"date": date(2026, 11, 1), "workout": "Тест", "set_number": 1,
           "weight": "10", "reps": 5,
           "duration": 300 if kind == "cardio" else None, "rest_after": 45}
    _, data = build_exercise_csv("Упр", kind, [row])
    text = _decode(data)
    assert text.count("\n") == 2  # шапка + 1 строка
    assert text.splitlines()[1].endswith("45с")  # интервал на месте


# ── Доработка Блока 4 (по репорту обкатки) ────────────────────────


def _set(completed=True, weight_kg="50", reps=10, session_id=1,
         exercise_id=1):
    from types import SimpleNamespace
    return SimpleNamespace(completed=completed, weight_kg=weight_kg,
                           reps=reps, session_id=session_id,
                           exercise_id=exercise_id)


def test_strict_tonnage_only_plain_kg():
    """Строгий тоннаж: «14 блоков» и «Своё тело» не считаем (как в деталях)."""
    from app.services.analytics_service import strict_tonnage

    sets = [_set(weight_kg="300", reps=10),        # 3000
            _set(weight_kg="14 блоков", reps=9),   # пропуск
            _set(weight_kg="Своё тело", reps=15),  # пропуск
            _set(weight_kg="14", reps=9),          # 126
            _set(completed=False, weight_kg="50", reps=10)]  # не завершён
    assert strict_tonnage(sets) == 3126


def test_strict_tonnage_empty_and_zero():
    from app.services.analytics_service import strict_tonnage
    assert strict_tonnage([]) == 0
    assert strict_tonnage([_set(weight_kg=None, reps=10)]) == 0


def test_session_csv_detailed():
    """Детальный экспорт тренировки: строка на подход, с отдыхом."""
    from app.services.export_service import build_session_csv

    groups = [
        {"exercise": "Жим", "sets": [
            {"set_number": 1, "weight": "203", "reps": 11,
             "duration": None, "rest_after": 8},
            {"set_number": 2, "weight": "203", "reps": 12,
             "duration": None, "rest_after": 1500},
            {"set_number": 3, "weight": "203", "reps": 12,
             "duration": None, "rest_after": None},
        ]},
        {"exercise": "Элипс (разминка)", "sets": [
            {"set_number": 1, "weight": None, "reps": None,
             "duration": 3720, "rest_after": None},
        ]},
    ]
    filename, data = build_session_csv("Тест", date(2026, 11, 18), groups,
                                       formed_on=date(2026, 11, 21))
    lines = _decode(data).splitlines()
    assert lines[0] == ("Дата;Тренировка;Упражнение;Подход;Вес;"
                        "Повторы;Длительность;Отдых после подхода")
    assert lines[1] == ("2026-11-18;Тест;Жим;1;203;11;;8с")
    assert lines[2].endswith("25м 0с")   # 1500 секунд
    assert lines[3].endswith(";")        # последний подход — без отдыха
    assert "2026-11-18;Тест;Элипс (разминка);1;;;62 мин;" in lines[4]
    assert filename == "workout_Тест_2026-11-21.csv"
