"""Тесты клавиатур: структура inline-кнопок.

Регрессия: history_list_kb обязана ВОЗВРАЩАТЬ разметку (баг — return
утерян при правке файла, клавиатура молча не отправлялась).
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.keyboards.workout_kb import (
    history_filter_kb, history_list_kb, notes_exercise_kb,
    notes_search_results_kb, notes_workouts_kb,
)


def _ws(ws_id: int, name: str = "Верх"):
    sets = [SimpleNamespace(exercise_id=1, completed=True)]
    return SimpleNamespace(
        id=ws_id,
        started_at=datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 11, 17, 11, 0, tzinfo=timezone.utc),
        workout_type=SimpleNamespace(name=name),
        sets=sets,
    )


def test_history_list_kb_returns_markup_with_rows():
    markup = history_list_kb([_ws(1), _ws(2)], page=0, has_next=True)
    assert markup is not None
    rows = markup.inline_keyboard
    # 2 кнопки тренировок + Вперед + Фильтр + В меню
    assert len(rows) == 5
    assert rows[0][0].callback_data == "hist_view:1:0:0"
    assert rows[1][0].callback_data == "hist_view:2:0:0"
    assert rows[2][0].callback_data == "hist_page:1:0"
    assert rows[3][0].callback_data == "hist_filter"
    assert rows[4][0].callback_data == "wt_list"


def test_history_list_kb_keeps_filter_in_callbacks():
    markup = history_list_kb([_ws(1)], page=2, has_next=False, wt_id=7)
    rows = markup.inline_keyboard
    # wt_id=7 сохраняется в callback кнопок (пагинация не сбрасывает фильтр)
    assert rows[0][0].callback_data == "hist_view:1:2:7"
    assert rows[1][0].callback_data == "hist_page:1:7"
    # Активный фильтр -> кнопка сброса вместо выбора фильтра
    assert rows[2][0].callback_data == "hist_page:0:0"


def test_history_filter_kb_lists_types():
    wt = SimpleNamespace(name="Верх", id=3)
    markup = history_filter_kb([(wt, 4)])
    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "hist_setfilter:3"
    assert "Верх (4)" in rows[0][0].text
    assert rows[1][0].callback_data == "hist_page:0:0"


def test_notes_exercise_kb_items_open_workout():
    """Каждая заметка в ленте — кнопка, открывающая её тренировку,
    с «адресом возврата» обратно в ленту."""
    items = [(42, "17.11.2026", "Проверка заметок")]
    markup = notes_exercise_kb(7, 1, False, items)
    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "hist_view:42:0:0:notes_ex:7:1"
    assert "📅 17.11.2026" in rows[0][0].text
    assert "Проверка заметок" in rows[0][0].text
    assert rows[1][0].callback_data == "notes_ex:7:0"  # пагинация
    assert rows[2][0].callback_data == "notes_main:0"  # в общий архив


def test_notes_workouts_kb_shows_note_preview():
    """Кнопка тренировки в «Заметках к тренировкам» несёт сниппет заметки,
    чтобы одинаковые «Тест | 11.11» различались по первым словам."""
    from datetime import datetime, timezone
    wt = SimpleNamespace(name="Тест")
    ws = SimpleNamespace(id=5, workout_type=wt,
                         started_at=datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
                         notes="Плечи болели, снизил вес")
    markup = notes_workouts_kb([ws], 0, False)
    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "notes_ws_view:5:0"
    assert "Тест | 11.11" in rows[0][0].text
    assert "Плечи болели" in rows[0][0].text


def test_notes_workouts_kb_truncates_long_note():
    """Длинная заметка ужимается: подпись кнопки не превышает лимит Telegram."""
    from datetime import datetime, timezone
    wt = SimpleNamespace(name="Тест")
    ws = SimpleNamespace(id=5, workout_type=wt,
                         started_at=datetime(2026, 11, 11, 12, tzinfo=timezone.utc),
                         notes="Очень длинная заметка " * 10)
    markup = notes_workouts_kb([ws], 0, False)
    label = markup.inline_keyboard[0][0].text
    assert len(label) <= 64
    assert label.endswith("…")


def test_notes_exercise_kb_origin_back_to_workout():
    """Если ленту открыли с экрана прогресса — «Назад» ведёт в тренировку,
    пагинация и сами заметки сохраняют этот адрес (цепочка не рвётся)."""
    items = [(42, "17.11.2026", "текст")]
    markup = notes_exercise_kb(7, 0, True, items, origin="hist_view:5:3")
    rows = markup.inline_keyboard
    # Заметка ведёт в тренировку, а обратно — в эту ленту с тем же origin
    assert rows[0][0].callback_data == \
        "hist_view:42:0:0:notes_ex:7:0:hist_view:5:3"
    assert rows[1][0].callback_data == "notes_ex:7:1:hist_view:5:3"
    assert rows[2][0].callback_data == "hist_view:5:3"
    assert "Назад к тренировке" in rows[2][0].text


def test_notes_search_and_workout_view_return_to_notes():
    """Поиск и «Открыть тренировку» ведут в детали с возвратом в заметки."""
    from app.keyboards.workout_kb import notes_workout_view_kb

    search = notes_search_results_kb([(1, "🤸", "Жим", "17.11.2026", "текст")])
    assert search.inline_keyboard[0][0].callback_data == \
        "hist_view:1:0:0:notes_main:0"

    view = notes_workout_view_kb(42, 2)
    assert view.inline_keyboard[0][0].callback_data == \
        "hist_view:42:0:0:notes_ws:2"
    assert view.inline_keyboard[1][0].callback_data == "notes_ws:2"


def test_exercise_progress_kb_custom_back():
    """Кнопка «Назад» на графике может вести куда угодно (проброс возврата)."""
    from app.keyboards.workout_kb import exercise_progress_kb

    kb = exercise_progress_kb(5, 3, 9,
                              back_cb="hist_view:5:3:0:notes_ex:9:0")
    assert kb.inline_keyboard[0][0].callback_data == \
        "notes_ex:9:0:ex_progress:9:5:3"  # возврат из ленты — на экран прогресса
    assert kb.inline_keyboard[1][0].callback_data == \
        "exp_ex:9"  # экспорт истории упражнения (C2.6 Блок 4)
    assert kb.inline_keyboard[2][0].callback_data == \
        "hist_view:5:3:0:notes_ex:9:0"
    assert "Назад к тренировке" in kb.inline_keyboard[2][0].text
    # Без back_cb — как раньше, в детали тренировки
    kb2 = exercise_progress_kb(5, 3, 9)
    assert kb2.inline_keyboard[2][0].callback_data == "hist_view:5:3"


def test_exercise_progress_kb_notes_return_with_origin():
    """При заходе из архива заметок origin пробрасывается и в ленту."""
    from app.keyboards.workout_kb import exercise_progress_kb

    kb = exercise_progress_kb(5, 3, 9, show_notes_btn=True,
                              origin="notes_main:0")
    assert kb.inline_keyboard[0][0].callback_data == \
        "notes_ex:9:0:ex_progress:9:5:3:notes_main:0"


def test_notes_exercise_kb_back_to_progress():
    """В ленте заметок, открытой с экрана прогресса, кнопка ведёт
    в прогресс и подписана «Назад к прогрессу»."""
    from app.keyboards.workout_kb import notes_exercise_kb

    kb = notes_exercise_kb(9, 0, False, [(1, "18.11.2026", "заметка")],
                           origin="ex_progress:9:5:3")
    assert kb.inline_keyboard[-1][0].callback_data == "ex_progress:9:5:3"
    assert "Назад к прогрессу" in kb.inline_keyboard[-1][0].text


def test_exercise_progress_kb_hides_notes_button_in_notes_flow():
    """При заходе из архива заметок кнопка «Заметки по упражнению» не нужна —
    иначе она замыкает петлю обратно на тот же экран."""
    from app.keyboards.workout_kb import exercise_progress_kb

    kb = exercise_progress_kb(5, 3, 9, show_notes_btn=False)
    # Экспорт остаётся (он не замыкает петлю), заметок нет
    assert len(kb.inline_keyboard) == 2
    assert kb.inline_keyboard[0][0].callback_data == "exp_ex:9"
    assert kb.inline_keyboard[1][0].callback_data == "hist_view:5:3"


def test_main_menu_layout():
    """«Главная» одна в ряду, остальные — тремя в ряд (визуальный стиль)."""
    from app.keyboards.workout_kb import main_reply_kb

    kb = main_reply_kb()
    rows = kb.keyboard
    assert [b.text for b in rows[0]] == ["🏠 Главная"]
    assert [b.text for b in rows[1]] == \
        ["📒 Заметки", "📊 История", "📅 Аналитика"]


def test_notes_search_results_kb_fits_button_limit():
    """Подписи кнопок результатов поиска не превышают лимит Telegram (64)."""
    items = [(1, "🤸", "Жим лёжа с длинным названием", "17.11.2026",
              "очень длинный текст заметки " * 10)]
    markup = notes_search_results_kb(items)
    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "hist_view:1:0:0:notes_main:0"
    assert len(rows[0][0].text) <= 64
    assert rows[-1][0].callback_data == "notes_main:0"


def test_after_finish_kb_export_button():
    """На экране итогов — кнопка экспорта тренировки (C2.6 Блок 4)."""
    from app.keyboards.workout_kb import after_finish_kb

    kb = after_finish_kb(session_id=42)
    assert kb.inline_keyboard[0][0].callback_data == "exp_sess:42"
    assert kb.inline_keyboard[1][0].callback_data == "wt_list"
    # Без session_id — как раньше, только «В меню тренировок»
    kb2 = after_finish_kb()
    assert len(kb2.inline_keyboard) == 1
    assert kb2.inline_keyboard[0][0].callback_data == "wt_list"
