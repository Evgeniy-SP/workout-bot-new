"""Сервис селективного CSV-экспорта (C2.6 Блок 4).

Чистые функции: на вход — уже готовые данные (из db/queries),
на выход — (имя файла, bytes) для BufferedInputFile.

Формат: UTF-8 с BOM + разделитель «;» — Excel на Windows
открывает файл по двойному клику без мастера импорта.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date

from app.services.pr_service import split_weight
from app.utils import format_duration

DELIMITER = ";"
ENCODING = "utf-8-sig"  # UTF-8 с BOM: Excel на Windows определяет кириллицу автоматически
KG_ALIASES = {"", "кг"}


def _slug(text: str) -> str:
    """Безопасный кусок имени файла из произвольной строки."""
    return re.sub(r"[^0-9A-Za-zА-Яа-яёЁ]+", "_", text).strip("_")[:40] or "export"


def _to_csv(header: list[str], rows: list[list[object]]) -> bytes:
    """Собирает CSV в bytes (BOM + «;»)."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=DELIMITER)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode(ENCODING)


def _fmt_date(value) -> str:
    """date/datetime → 'YYYY-MM-DD'."""
    return value.date().isoformat() if hasattr(value, "date") else value.isoformat()


def _weight_cell(weight_str) -> str:
    """Ячейка «Вес» для Excel: числовые кг — с запятой (ru-локаль),
    всё остальное («Своё тело», «12 блоков») — как есть."""
    if not weight_str:
        return ""
    num, unit = split_weight(weight_str)
    if num is None or unit not in KG_ALIASES:
        return str(weight_str)
    text = f"{num:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def build_exercise_csv(
    exercise_name: str,
    kind: str,
    rows: list[dict],
    formed_on: date | None = None,
) -> tuple[str, bytes]:
    """CSV истории одного упражнения.

    rows: [{"date", "set_number", "weight" (сырая строка weight_kg|None),
            "reps" (int|None), "duration" (int сек|None)}]
    Для cardio добавляется колонка «Длительность (сек)».
    Возвращает (имя файла, содержимое).
    """
    formed = (formed_on or date.today()).isoformat()
    cardio = kind == "cardio"
    header = (["Дата", "Тренировка", "Подход", "Вес", "Повторы"]
              + (["Длительность"] if cardio else [])
              + ["Интервал"])
    lines = []
    for r in rows:
        line: list[object] = [
            _fmt_date(r["date"]),
            r.get("workout") or "",
            r["set_number"],
            _weight_cell(r["weight"]),
            r["reps"] or "",
        ]
        if cardio:
            line.append(format_duration(r["duration"])
                        if r["duration"] else "")
        # Интервал до следующего подхода; > 10 минут — уже не «отдых»,
        # а переход между тренировками (тот же порог, что в аналитике)
        line.append(_fmt_rest(r.get("rest_after")))
        lines.append(line)
    filename = f"history_{_slug(exercise_name)}_{formed}.csv"
    return filename, _to_csv(header, lines)


def _fmt_duration(started_at, finished_at) -> str:
    """Длительность тренировки «11 ч 32 мин» / «28 мин»; '' если данных нет."""
    if not started_at or not finished_at:
        return ""
    minutes = max(0, round((finished_at - started_at).total_seconds() / 60))
    hours, rem = divmod(minutes, 60)
    return f"{hours} ч {rem} мин" if hours else f"{rem} мин"


def build_month_csv(
    year: int,
    month: int,
    sessions: list[dict],
    formed_on: date | None = None,
) -> tuple[str, bytes]:
    """CSV тренировок за календарный месяц.

    sessions: [{"date", "workout_name", "exercises" (list[str]),
                "sets_count", "started_at", "finished_at", "tonnage"}]
    Возвращает (имя файла, содержимое).
    """
    formed = (formed_on or date.today()).isoformat()
    header = ["Дата", "Тренировка", "Упражнения", "Подходы", "Время", "Тоннаж"]
    lines = [
        [
            _fmt_date(s["date"]),
            s["workout_name"],
            ", ".join(s["exercises"]),
            s["sets_count"],
            _fmt_duration(s["started_at"], s["finished_at"]),
            s["tonnage"],
        ]
        for s in sessions
    ]
    filename = f"workouts_{year}-{month:02d}_{formed}.csv"
    return filename, _to_csv(header, lines)


def _fmt_rest(seconds) -> str:
    """Отдых после подхода, как на экране деталей: '8с', '25м 0с'."""
    if seconds is None:
        return ""
    m, sec = divmod(int(seconds), 60)
    return f"{m}м {sec}с" if m > 0 else f"{sec}с"


def build_session_csv(
    workout_name: str,
    session_date,
    groups: list[dict],
    formed_on: date | None = None,
) -> tuple[str, bytes]:
    """CSV одной конкретной тренировки — детальный (C2.6 Блок 4).

    groups — упражнения по порядку выполнения:
    [{"exercise": название,
      "sets": [{"set_number", "weight", "reps", "duration",
                "rest_after"}]}]
    Строка на каждый подход; rest_after — отдых до следующего подхода
    этого же упражнения (секунды), у последнего подхода пусто.
    Возвращает (имя файла, содержимое).
    """
    formed = (formed_on or date.today()).isoformat()
    header = ["Дата", "Тренировка", "Упражнение", "Подход", "Вес",
              "Повторы", "Длительность", "Отдых после подхода"]
    lines = []
    for g in groups:
        for s in g["sets"]:
            lines.append([
                _fmt_date(session_date),
                workout_name,
                g["exercise"],
                s["set_number"],
                _weight_cell(s["weight"]),
                s["reps"] or "",
                format_duration(s["duration"]) if s["duration"] else "",
                _fmt_rest(s["rest_after"]),
            ])
    filename = f"workout_{_slug(workout_name)}_{formed}.csv"
    return filename, _to_csv(header, lines)
