"""Сравнение выполненных силовых подходов двух тренировок."""

from typing import NamedTuple

from app.db.models import SessionSet
from app.services.pr_service import KG_ALIASES, split_weight


class _Load(NamedTuple):
    weight: float | None
    unit: str
    reps: int | None

    @property
    def weight_type(self) -> tuple[bool, str]:
        return self.weight is not None, self.unit


def compare_exercise(
    current: list[SessionSet], previous: list[SessionSet],
) -> tuple[str, str] | None:
    """Возвращает значок и описание изменений при наличии двух результатов."""
    now = [(s.set_number, _load(s)) for s in current if s.completed]
    before = [(s.set_number, _load(s)) for s in previous if s.completed]
    if not now or not before:
        return None

    previous_by_number = dict(before)
    changed_sets: dict[str, list[int]] = {}
    deltas: list[float] = []
    for number, current_load in now:
        previous_load = previous_by_number.get(number)
        if previous_load is None:
            continue
        change = _set_change(previous_load, current_load)
        if change:
            description, differences = change
            changed_sets.setdefault(description, []).append(number)
            deltas.extend(differences)

    details = []
    for description, numbers in changed_sets.items():
        if len(before) == len(now) == 1:
            details.append(description)
        else:
            details.append(f"{_set_numbers(numbers)}: {description}")

    count_delta = len(now) - len(before)
    if count_delta:
        details.append(f"{len(before)} → {format_set_count(len(now))}")
        deltas.append(count_delta)

    if not details:
        return ("🟡", "без изменений") if dict(now) == previous_by_number else None

    description = "\n   ".join(details)
    if all(delta > 0 for delta in deltas):
        return "✅", description + (" 🔥" if changed_sets else "")
    if all(delta < 0 for delta in deltas):
        return "🔴", description
    return "🔄", description


def format_set_count(count: int) -> str:
    return _counted(count, ("подход", "подхода", "подходов"))


def _counted(count: int, forms: tuple[str, str, str]) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} {forms[0]}"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} {forms[1]}"
    return f"{count} {forms[2]}"


def _set_numbers(numbers: list[int]) -> str:
    ordinals = [f"{number}-й" for number in numbers]
    if len(ordinals) == 1:
        return f"{ordinals[0]} подход"
    return ", ".join(ordinals[:-1]) + f" и {ordinals[-1]} подходы"


def _load(s: SessionSet) -> _Load:
    weight, unit = split_weight(s.weight_kg)
    if weight is not None:
        if unit in KG_ALIASES:
            if weight == 0:
                return _Load(None, "", s.reps)
            unit = "кг"
        elif unit in {"блок", "блока", "блоков", "бл."}:
            unit = "бл."
    return _Load(weight, unit, s.reps)


def _set_change(before: _Load, now: _Load) -> tuple[str, list[float]] | None:
    if before.weight_type != now.weight_type:
        return None

    deltas = []
    labels = []
    if before.weight is not None and now.weight is not None:
        weight_delta = now.weight - before.weight
        if weight_delta:
            deltas.append(weight_delta)
            labels.append(f"{_signed(weight_delta)}{_number(abs(weight_delta))} {now.unit}")
    if before.reps is not None and now.reps is not None:
        reps_delta = now.reps - before.reps
        if reps_delta:
            deltas.append(reps_delta)
            repeats = _counted(abs(reps_delta), ("повтор", "повтора", "повторов"))
            labels.append(f"{_signed(reps_delta)}{repeats}")
    if not deltas:
        return None
    text = f"было {_format_load(before)}, стало {_format_load(now)}"
    return f"{text} ({', '.join(labels)})", deltas


def _signed(value: float) -> str:
    return "+" if value > 0 else "−"


def _number(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _format_load(load: _Load) -> str:
    if load.weight is None:
        return _counted(load.reps, ("повтор", "повтора", "повторов"))
    reps = f"{load.reps} × " if load.reps is not None else ""
    return f"{reps}{_number(load.weight)} {load.unit}"
