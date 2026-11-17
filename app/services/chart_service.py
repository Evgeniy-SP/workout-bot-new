"""Сервис для генерации графиков прогресса с помощью matplotlib."""
import io
from datetime import datetime, timedelta

import matplotlib

# ВАЖНО: Используем Agg бэкенд, чтобы рисовать без окна (для сервера)
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def generate_progress_chart(dates: list[datetime], values: list[float],
                            title: str, y_label: str) -> io.BytesIO:
    """Рисует график по датам и возвращает картинку в виде байтов."""
    plt.style.use('dark_background')

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)

    if len(dates) == 1:
        ax.plot(dates, values, marker='o', linestyle='', color='#00c9ff',
                markersize=12)
    else:
        ax.plot(dates, values, marker='o', linestyle='-', color='#00c9ff',
                linewidth=2, markersize=6)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))

    # === УМНОЕ МАСШТАБИРОВАНИЕ ОСИ X ===
    if len(dates) == 1:
        # Если 1 точка: показываем окно в 2 дня (день до и день после)
        ax.set_xlim(dates[0] - timedelta(days=1), dates[0] + timedelta(days=1))
    else:
        # Если несколько точек: показываем от первой до последней + небольшой отступ по краям
        min_date = dates[0] - timedelta(days=1)
        max_date = dates[-1] + timedelta(days=1)
        ax.set_xlim(min_date, max_date)

        # Если точек мало (меньше 5), принудительно ставим метки на каждый день
        if len(dates) <= 5:
            ax.set_xticks(dates)
            ax.set_xticklabels([d.strftime('%d.%m') for d in dates])

    fig.autofmt_xdate()
    # ==================================

    ax.set_title(title, fontsize=14, color='white', pad=15)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_xlabel('Дата', fontsize=12)

    ax.grid(True, linestyle='--', alpha=0.3)

    for x, y in zip(dates, values):
        ax.annotate(f'{y:g}', (x, y), textcoords="offset points",
                    xytext=(0, 10), ha='center', fontsize=9)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', transparent=False, facecolor='#17212b')
    buf.seek(0)
    plt.close(fig)

    return buf


def generate_progress_chart_two(suptitle: str,
                                upper: tuple, lower: tuple) -> io.BytesIO:
    """Две панели друг под другом с общей осью X (например, вес + повторы).

    upper/lower — кортежи (даты, значения, заголовок панели, подпись оси Y,
    цвет). Заголовок всей картинки — suptitle.
    """
    plt.style.use('dark_background')

    fig, axes = plt.subplots(2, 1, figsize=(8, 6.5), dpi=150, sharex=True)
    fig.suptitle(suptitle, fontsize=14, color='white')

    for ax, (dates, values, title, y_label, color) in zip(axes, (upper, lower)):
        if len(dates) == 1:
            ax.plot(dates, values, marker='o', linestyle='',
                    color=color, markersize=12)
        else:
            ax.plot(dates, values, marker='o', linestyle='-', color=color,
                    linewidth=2, markersize=6)
        ax.set_title(title, fontsize=12, color='white', pad=8)
        ax.set_ylabel(y_label, fontsize=11)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.margins(y=0.15)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        for x, y in zip(dates, values):
            ax.annotate(f'{y:g}', (x, y), textcoords="offset points",
                        xytext=(0, 8), ha='center', fontsize=8)

    # Подписи по оси X — ВСЕ фактические даты из обеих панелей (общая ось).
    # Иначе matplotlib сам выбирает шаг ("через день"), и даты на графике
    # не совпадают с реальными датами тренировок.
    all_dates = sorted(set(upper[0]) | set(lower[0]))
    if len(all_dates) <= 15:  # при небольшом числе точек подписываем каждую
        axes[1].set_xticks(all_dates)
        axes[1].set_xticklabels(
            [d.strftime('%d.%m') for d in all_dates],
            rotation=45, ha='right', fontsize=9)

    axes[1].set_xlabel('Дата', fontsize=11)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', transparent=False, facecolor='#17212b')
    buf.seek(0)
    plt.close(fig)

    return buf


def generate_grouped_bar_chart(labels: list[str],
                               series: list[tuple[str, list, str]],
                               title: str, y_label: str) -> io.BytesIO:
    """Сгруппированная столбчатая диаграмма: на каждую метку — столбик серии.

    series — [(название, значения, цвет), ...]; длина значений = len(labels).
    Нулевые значения не подписываем, чтобы не мусорить на пустых неделях.
    """
    plt.style.use('dark_background')

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)

    n = len(labels)
    count = len(series)
    width = 0.8 / count
    x = range(n)

    for i, (name, values, color) in enumerate(series):
        offset = (i - (count - 1) / 2) * width
        bars = ax.bar([xi + offset for xi in x], values, width=width,
                      label=name, color=color)
        for bar, v in zip(bars, values):
            if v > 0:
                ax.annotate(f'{v:g}',
                            (bar.get_x() + bar.get_width() / 2, v),
                            textcoords="offset points", xytext=(0, 4),
                            ha='center', fontsize=8)

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_title(title, fontsize=14, color='white', pad=15)
    ax.set_ylabel(y_label, fontsize=12)
    ax.set_xlabel('Неделя месяца (числа)', fontsize=12)
    ax.grid(True, linestyle='--', alpha=0.3, axis='y')
    ax.margins(y=0.15)  # запас сверху, чтобы легенда не перекрывала столбики
    ax.legend(fontsize=10)

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', transparent=False, facecolor='#17212b')
    buf.seek(0)
    plt.close(fig)

    return buf
