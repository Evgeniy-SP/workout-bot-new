"""Сервис личных рекордов (PR) — C2.6.

Чистые функции без обращений к БД и Telegram: принимают подходы
(SessionSet), возвращают классификацию PR и готовые тексты сообщений.
Легко тестируются без моков (по образцу progress_service).

Логика:
- рекорд считается ОТНОСИТЕЛЬНО ПРОШЛЫХ ТРЕНИРОВОК (текущая сессия
  исключается), иначе пирамида набирая вес спамила бы рекордами;
- сравнение только ВНУТРИ одной единицы измерения: «12 блоков» бьёт
  «11 блоков», но не сравнивается с «50» (кг) — иначе смешение
  единиц (блоки/кг/резина) убивает корректность рекордов;
- голое число («50») и «кг» («50 кг») считаются одной единицей;
- первый замер в новой единице — рекорд (как первый взвешенный подход).

Силовые: рекорд по весу и по повторям (при максимальном весе).
Кардио: рекорд по времени (длительность выше всех прежних).
"""
from app.services.progress_service import WEIGHT_RE, parse_weight
from app.utils import fmt_msk_date, format_duration, format_weight

# Единицы, приводимые к «кг по умолчанию» (голое число = килограммы)
KG_ALIASES = {"", "кг"}


def split_weight(weight_str) -> tuple[float | None, str]:
    """Разделяет строку веса на число и единицу: («12 блоков») -> (12.0, «блоков»).

    Голое число -> единица "". Не числовые веса («Своё тело») -> (None, текст).
    """
    if not weight_str:
        return None, ""
    text = str(weight_str).replace(",", ".")
    m = WEIGHT_RE.search(text)
    if not m:
        return None, text.strip().casefold()
    num = float(m.group())
    unit = (text[:m.start()] + text[m.end():]).strip().casefold()
    return num, unit


def _unit_key(unit: str) -> str:
    """Ключ единицы для сравнения: кг-алиасы приводим к одному виду."""
    return "" if unit in KG_ALIASES else unit


def _split_prior(prior_sets: list) -> list[tuple[float, int, str, object]]:
    """Подходы прошлых тренировок: [(число, повторы, единица, подход)]."""
    out = []
    for s in prior_sets:
        w, unit = split_weight(getattr(s, "weight_kg", None))
        r = getattr(s, "reps", None)
        if w is not None and r:
            out.append((w, r, unit, s))
    return out


def classify_pr(new_set, prior_sets: list) -> str | None:
    """Определяет тип PR нового подхода относительно прошлых тренировок.

    Сравнение только внутри той же единицы измерения, что у new_set.
    Возвращает:
      "weight" — число выше всех прежних в этой единице
                 (или первый замер в ней);
      "reps"   — число равно максимуму, но повторов больше, чем было
                 когда-либо при нём;
      None     — рекорда нет.
    """
    w, unit = split_weight(getattr(new_set, "weight_kg", None))
    r = getattr(new_set, "reps", None)
    if w is None or not r:
        return None

    key = _unit_key(unit)
    same = [(pw, pr) for pw, pr, pu, _ in _split_prior(prior_sets)
            if _unit_key(pu) == key]
    if not same:
        return "weight"

    max_w = max(p[0] for p in same)
    if w > max_w:
        return "weight"
    if w == max_w:
        max_r = max(p[1] for p in same if p[0] == max_w)
        if r > max_r:
            return "reps"
    return None


def raw_session_pr_events(current_sets: list, prior_sets: list) -> list[tuple]:
    """ВСЕ подходы сессии, бьющие прошлые рекорды: [(подход, тип), ...].

    Без дедупа «пирамиды»: 60, 70 и 80 против прошлого максимума 50 —
    три события. Нужно для счётчиков в мгновенных сообщениях.
    """
    events = []
    for s in sorted(current_sets, key=lambda x: (x.set_number, x.id or 0)):
        kind = classify_pr(s, prior_sets)
        if kind:
            events.append((s, kind))
    return events


def session_pr_events(current_sets: list, prior_sets: list) -> list[tuple]:
    """PR-события текущей тренировки: [(подход, тип), ...] по порядку подходов.

    current_sets — завершённые подходы ТЕКУЩЕЙ сессии (силовые).
    prior_sets   — завершённые подходы ПРОШЛЫХ сессий того же упражнения.

    Защита от «пирамидного» спама: если в тренировке несколько подходов
    бьют прошлые рекорды (40→50→60 кг против прошлого максимума 30),
    рекордом считается только ЛУЧШИЙ из них (60). Рекорды разных типов
    (вес и повторы при максимальном весе) независимы — могут быть оба.
    """
    raw = raw_session_pr_events(current_sets, prior_sets)

    best: dict[str, tuple] = {}
    for s, kind in raw:
        cur = best.get(kind)
        if kind == "weight":
            better = cur is None or parse_weight(s.weight_kg) > \
                parse_weight(cur[0].weight_kg)
        else:  # reps: сравниваем количество повторов
            better = cur is None or s.reps > cur[0].reps
        if better:
            best[kind] = (s, kind)

    return [best[k] for k in ("weight", "reps") if k in best]


def _date_of(s) -> str:
    sess = getattr(s, "session", None)
    started = getattr(sess, "started_at", None)
    return fmt_msk_date(started) if started else "?"


def _prior_record_str(kind: str, new_set, prior_sets: list) -> str:
    """Строка «было ...» для сообщения о рекорде (в той же единице)."""
    _, unit = split_weight(getattr(new_set, "weight_kg", None))
    key = _unit_key(unit)
    prior = [(pw, pr, s) for pw, pr, pu, s in _split_prior(prior_sets)
             if _unit_key(pu) == key]
    if not prior:
        return "первый замер с этим типом веса 💪"

    if kind == "weight":
        best = max(prior, key=lambda p: p[0])
        date_str = _date_of(best[2])
        return f"было {format_weight(best[2].weight_kg)} от {date_str}"

    # kind == "reps": максимум повторов при максимальном весе
    max_w = max(p[0] for p in prior)
    best_r = max(p[1] for p in prior if p[0] == max_w)
    date_str = _date_of(next(p[2] for p in prior
                             if p[0] == max_w and p[1] == best_r))
    return f"было {best_r} повт. от {date_str}"


# ─── Кардио: рекорд по времени ────────────────────────────────────


def classify_duration_pr(new_set, prior_sets: list) -> str | None:
    """Рекорд по времени для кардио: длительность выше всех прежних.

    Возвращает "duration" или None.
    """
    d = getattr(new_set, "duration_seconds", None)
    if not d:
        return None
    prior = [s.duration_seconds for s in prior_sets
             if getattr(s, "duration_seconds", None)]
    if not prior:
        return "duration"
    return "duration" if d > max(prior) else None


def _prior_duration_record_str(prior_sets: list) -> str:
    """Строка «было ...» для рекорда по времени."""
    prior = [(s.duration_seconds, s) for s in prior_sets
             if getattr(s, "duration_seconds", None)]
    if not prior:
        return "первый замер времени 💪"
    best = max(prior, key=lambda p: p[0])
    return f"было {format_duration(best[0])} от {_date_of(best[1])}"


def build_pr_message(ex_name: str, new_set, kind: str,
                     prior_sets: list,
                     ex_ordinal: int, session_ordinal: int) -> str:
    """Текст мгновенного сообщения о рекорде.

    ex_ordinal — номер рекорда в ЭТОМ упражнении (1, 2, 3...);
    session_ordinal — сквозной номер по ВСЕЙ тренировке.
    """
    if kind == "weight":
        w_str = format_weight(new_set.weight_kg)
        head = (f"🎉 <b>Новый рекорд по весу!</b>\n"
                f"{ex_name}: <b>{w_str}</b>")
        prior_str = _prior_record_str(kind, new_set, prior_sets)
    elif kind == "reps":
        w_str = format_weight(new_set.weight_kg)
        head = (f"🎉 <b>Новый рекорд по повторам!</b>\n"
                f"{ex_name}: <b>{new_set.reps} повт.</b> при {w_str}")
        prior_str = _prior_record_str(kind, new_set, prior_sets)
    else:  # duration — кардио
        head = (f"🎉 <b>Новый рекорд по времени!</b>\n"
                f"{ex_name}: <b>{format_duration(new_set.duration_seconds)}</b>")
        prior_str = _prior_duration_record_str(prior_sets)

    return (f"{head}\n"
            f"<i>{prior_str}</i>\n"
            f"🔥 {ex_ordinal}-й рекорд в «{ex_name}» · "
            f"{session_ordinal}-й за тренировку")


def build_pr_summary(events: list[tuple], exercise_names: dict[int, str]) -> str | None:
    """Блок «Рекорды за тренировку» для экрана итогов.

    events — результат session_pr_events. exercise_names — {ex_id: имя}.
    """
    if not events:
        return None

    lines = [f"🏆 <b>Рекорды за тренировку ({len(events)}):</b>"]
    for s, kind in events:
        name = exercise_names.get(s.exercise_id, "Упражнение")
        if kind == "weight":
            lines.append(f"▪️ {name} — вес {format_weight(s.weight_kg)}")
        elif kind == "reps":
            lines.append(
                f"▪️ {name} — {s.reps} повт. при {format_weight(s.weight_kg)}")
        else:  # duration
            lines.append(
                f"▪️ {name} — время {format_duration(s.duration_seconds)}")
    return "\n".join(lines)
