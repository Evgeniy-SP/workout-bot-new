import pytest

from app.db.models import SessionSet
from app.services.workout_comparison import compare_exercise, format_set_count


def _sets(*entries):
    return [
        SessionSet(set_number=number, reps=reps, weight_kg=weight, completed=True)
        for number, (reps, weight) in enumerate(entries, 1)
    ]


def test_compares_by_number_even_when_the_heaviest_set_changes():
    previous = _sets((10, "75"), (12, "50"))
    current = _sets((8, "80,5 кг"), (15, "50"), (12, "52"), (1, "100"))
    current[-1].completed = False

    assert compare_exercise(current, previous) == (
        "🔄",
        "1-й подход: было 10 × 75 кг, стало 8 × 80,5 кг (+5,5 кг, −2 повтора)\n"
        "   2-й подход: было 12 × 50 кг, стало 15 × 50 кг (+3 повтора)\n"
        "   2 → 3 подхода",
    )


def test_groups_identical_changes():
    previous = _sets((15, "152"), (15, "152"), (15, "152"))
    current = _sets((15, "152"), (10, "100"), (10, "100"))

    assert compare_exercise(current, previous) == (
        "🔴", "2-й и 3-й подходы: было 15 × 152 кг, стало 10 × 100 кг (−52 кг, −5 повторов)",
    )


def test_one_changed_set_can_be_displayed_without_a_number():
    assert compare_exercise(_sets((10, "100")), _sets((8, "95"))) == (
        "✅", "было 8 × 95 кг, стало 10 × 100 кг (+5 кг, +2 повтора) 🔥",
    )


@pytest.mark.parametrize(
    "current_weight, previous_weight, expected",
    [
        ("Своё тело", "своё тело", (
            "✅", "1-й подход: было 8 повторов, стало 10 повторов (+2 повтора)\n   1 → 2 подхода 🔥",
        )),
        ("12 блоков", "9 блока", (
            "✅", "1-й подход: было 8 × 9 бл., стало 10 × 12 бл. (+3 бл., +2 повтора)\n   1 → 2 подхода 🔥",
        )),
        ("12 блоков", "10 кг", ("✅", "1 → 2 подхода")),
        ("Резинка", "Своё тело", ("✅", "1 → 2 подхода")),
        (None, None, (
            "✅", "1-й подход: было 8 повторов, стало 10 повторов (+2 повтора)\n   1 → 2 подхода 🔥",
        )),
    ],
)
def test_weight_formats(current_weight, previous_weight, expected):
    current = _sets((10, current_weight), (4, current_weight))
    previous = _sets((8, previous_weight))

    assert compare_exercise(current, previous) == expected


def test_shows_unchanged_but_omits_missing_or_incomparable_exercises():
    sets = _sets((10, "75"))
    assert compare_exercise(sets, _sets((10, "75 кг"))) == ("🟡", "без изменений")
    assert compare_exercise(sets, []) is None
    assert compare_exercise([], sets) is None
    assert compare_exercise(sets, _sets((10, "10 блоков"))) is None


@pytest.mark.parametrize("weight", ["0", "0.0", "0,0 кг"])
def test_zero_weight_matches_a_set_without_weight(weight):
    assert compare_exercise(_sets((10, weight)), _sets((8, None))) == (
        "✅", "было 8 повторов, стало 10 повторов (+2 повтора) 🔥",
    )
    assert compare_exercise(_sets((8, "0 блоков")), _sets((8, None))) is None


def test_mixed_weights_compare_only_matching_units_at_the_same_set_number():
    previous = _sets((10, "75"), (15, "10 блоков"))
    current = _sets((8, "80"), (15, "40 кг"))

    assert compare_exercise(current, previous) == (
        "🔄", "1-й подход: было 10 × 75 кг, стало 8 × 80 кг (+5 кг, −2 повтора)",
    )


def test_reports_fewer_sets_even_when_paired_sets_are_unchanged():
    previous = _sets((8, "70"), (8, "70"), (8, "70"))
    current = _sets((8, "70"), (8, "70"))

    assert compare_exercise(current, previous) == ("🔴", "3 → 2 подхода")
    assert format_set_count(1) == "1 подход"


def test_warmups_do_not_shift_working_set_numbers():
    previous = _sets((20, "Пустой гриф"), (5, "70"), (5, "70"))
    current = _sets((20, "Пустой гриф"), (10, "50"), (5, "70"), (6, "70"))
    previous[0].is_warmup = True
    current[0].is_warmup = current[1].is_warmup = True
    assert compare_exercise(current, previous) == (
        "✅", "2-й подход: было 5 × 70 кг, стало 6 × 70 кг (+1 повтор) 🔥",
    )
    assert compare_exercise(current[:2], previous) is None
