"""Одноразовая миграция данных из старой БД workout-bot в новую.

Отличия схем (старая → новая):
- weight_kg: double precision → String(32) (конвертируем число в строку)
- session_sets.completed_at: могло отсутствовать → заполняем started_at сессии
- workout_sessions.notes, workout_types.is_in_rotation: могли отсутствовать
- таблицы session_exercise_notes и session_skipped_exercises: могли отсутствовать

Скрипт сам смотрит, какие колонки реально есть в источнике, и читает только их.
Идемпотентность: сущности ищутся по «естественным ключам»
(user → tg_id, тип тренировки → имя, упражнение → имя+kind,
сессия → user + тип + started_at), повторный запуск не дублирует.

Запуск (из корня проекта):
    python scripts/migrate_old_db.py \
        --source postgresql+asyncpg://bot_user:secret@localhost:5434/workout_old \
        --target postgresql+asyncpg://bot_user:secret@localhost:5434/workout_bot
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    Exercise, SessionExerciseNote, SessionSet, SessionSkippedExercise, User,
    WorkoutSession, WorkoutType,
)


def weight_to_str(w) -> str | None:
    """Старое числовое значение веса → строка новой схемы."""
    if w is None:
        return None
    if isinstance(w, float) and w.is_integer():
        return str(int(w))
    return str(w)


async def migrate(source_url: str, target_url: str) -> None:
    src_engine = create_async_engine(source_url)
    tgt_engine = create_async_engine(target_url)

    async with src_engine.connect() as conn:
        insp = await conn.run_sync(sa_inspect)
        tables = set(await conn.run_sync(
            lambda c: sa_inspect(c).get_table_names()))
        columns = {tbl: set(await conn.run_sync(
            lambda c, t=tbl: sa_inspect(c).get_columns(t) and
            [col["name"] for col in sa_inspect(c).get_columns(t)]))
            for tbl in tables}

    if "users" not in tables:
        raise SystemExit("В источнике нет таблицы users — проверь --source")

    def cols(table, wanted):
        """Из списка колонок оставляем только существующие в источнике."""
        model_table = {
            "users": User.__table__,
            "workout_types": WorkoutType.__table__,
            "exercises": Exercise.__table__,
            "workout_sessions": WorkoutSession.__table__,
            "session_sets": SessionSet.__table__,
            "session_exercise_notes": SessionExerciseNote.__table__,
            "session_skipped_exercises": SessionSkippedExercise.__table__,
        }[table]
        available = columns.get(table, set())
        return [model_table.c[name] for name in wanted if name in available]

    src = async_sessionmaker(src_engine, expire_on_commit=False)
    tgt = async_sessionmaker(tgt_engine, expire_on_commit=False)

    stats = {"users": 0, "workout_types": 0, "exercises": 0,
             "sessions": 0, "sets": 0, "notes": 0, "skipped": 0}

    async with src() as s, tgt() as t:
        # ── users (естественный ключ: tg_id) ──
        u_cols = cols("users", ["id", "tg_id", "username", "full_name"])
        user_rows = (await s.execute(select(*u_cols))).mappings().all()
        tg_to_user: dict[int, User] = {}
        for row in user_rows:
            existing = (await t.execute(select(User).where(
                User.tg_id == row["tg_id"]))).scalar_one_or_none()
            if existing:
                tg_to_user[row["tg_id"]] = existing
                continue
            u = User(tg_id=row["tg_id"], username=row["username"],
                     full_name=row["full_name"])
            t.add(u)
            await t.flush()
            tg_to_user[row["tg_id"]] = u
            stats["users"] += 1

        # карта старый id пользователя → tg_id (для ссылок)
        old_uid_to_tg = {row["id"]: row["tg_id"] for row in user_rows}

        # ── workout_types (ключ: user + имя) ──
        wt_cols = cols("workout_types", ["id", "user_id", "name",
                                         "description", "sort_order",
                                         "is_archived"])
        wt_rows = (await s.execute(select(*wt_cols))).mappings().all()
        old_to_wt: dict[int, WorkoutType] = {}
        for row in wt_rows:
            new_user = tg_to_user.get(old_uid_to_tg.get(row["user_id"]))
            if not new_user:
                continue
            wt = (await t.execute(select(WorkoutType).where(
                WorkoutType.user_id == new_user.id,
                WorkoutType.name == row["name"]))).scalar_one_or_none()
            if not wt:
                wt = WorkoutType(
                    user_id=new_user.id, name=row["name"],
                    description=row.get("description"),
                    sort_order=row.get("sort_order") or 0,
                    is_archived=row.get("is_archived") or False,
                    is_in_rotation=True,
                )
                t.add(wt)
                await t.flush()
                stats["workout_types"] += 1
            old_to_wt[row["id"]] = wt

        # ── exercises (ключ: тип тренировки + имя + kind) ──
        ex_cols = cols("exercises", ["id", "workout_type_id", "name", "kind",
                                     "sets", "reps", "weight_kg",
                                     "duration_seconds", "is_optional",
                                     "notes", "sort_order", "is_archived"])
        ex_rows = (await s.execute(select(*ex_cols))).mappings().all()
        old_to_ex: dict[int, Exercise] = {}
        for row in ex_rows:
            wt = old_to_wt.get(row["workout_type_id"])
            if not wt:
                continue
            ex = (await t.execute(select(Exercise).where(
                Exercise.workout_type_id == wt.id,
                Exercise.name == row["name"],
                Exercise.kind == row["kind"]))).scalar_one_or_none()
            if not ex:
                ex = Exercise(
                    workout_type_id=wt.id, name=row["name"], kind=row["kind"],
                    sets=row.get("sets"), reps=row.get("reps"),
                    weight_kg=weight_to_str(row.get("weight_kg")),
                    duration_seconds=row.get("duration_seconds"),
                    is_optional=row.get("is_optional") or False,
                    notes=row.get("notes"),
                    sort_order=row.get("sort_order") or 0,
                    is_archived=row.get("is_archived") or False,
                )
                t.add(ex)
                await t.flush()
                stats["exercises"] += 1
            old_to_ex[row["id"]] = ex

        # ── sessions (ключ: user + тип + started_at) ──
        ses_cols = cols("workout_sessions", ["id", "user_id",
                                             "workout_type_id", "started_at",
                                             "finished_at", "notes"])
        ses_rows = (await s.execute(select(*ses_cols))).mappings().all()
        old_to_ses: dict[int, WorkoutSession] = {}
        for row in ses_rows:
            wt = old_to_wt.get(row["workout_type_id"])
            if not wt:
                continue
            ses = (await t.execute(select(WorkoutSession).where(
                WorkoutSession.user_id == wt.user_id,
                WorkoutSession.workout_type_id == wt.id,
                WorkoutSession.started_at == row["started_at"],
            ))).scalar_one_or_none()
            if not ses:
                ses = WorkoutSession(
                    user_id=wt.user_id, workout_type_id=wt.id,
                    started_at=row["started_at"],
                    finished_at=row.get("finished_at"),
                    notes=row.get("notes"),
                )
                t.add(ses)
                await t.flush()
                stats["sessions"] += 1
            old_to_ses[row["id"]] = ses

        # ── sets (дедупликация: session + exercise + set_number) ──
        set_cols = cols("session_sets", ["id", "session_id", "exercise_id",
                                         "set_number", "reps", "weight_kg",
                                         "duration_seconds", "completed",
                                         "completed_at"])
        set_rows = (await s.execute(select(*set_cols))).mappings().all()
        for row in set_rows:
            ses = old_to_ses.get(row["session_id"])
            ex = old_to_ex.get(row["exercise_id"])
            if not ses or not ex:
                continue
            dup = (await t.execute(select(SessionSet).where(
                SessionSet.session_id == ses.id,
                SessionSet.exercise_id == ex.id,
                SessionSet.set_number == row["set_number"],
            ))).scalar_one_or_none()
            if dup:
                continue
            completed_at = row.get("completed_at") or ses.started_at
            t.add(SessionSet(
                session_id=ses.id, exercise_id=ex.id,
                set_number=row["set_number"], reps=row.get("reps"),
                weight_kg=weight_to_str(row.get("weight_kg")),
                duration_seconds=row.get("duration_seconds"),
                completed=row.get("completed") or False,
                completed_at=completed_at,
            ))
            stats["sets"] += 1

        # ── заметки к упражнениям (таблица могла отсутствовать) ──
        if "session_exercise_notes" in tables:
            n_cols = cols("session_exercise_notes",
                          ["session_id", "exercise_id", "note"])
            rows = (await s.execute(select(*n_cols))).mappings().all()
            for r in rows:
                ses = old_to_ses.get(r["session_id"])
                ex = old_to_ex.get(r["exercise_id"])
                if not ses or not ex:
                    continue
                dup = (await t.execute(select(SessionExerciseNote).where(
                    SessionExerciseNote.session_id == ses.id,
                    SessionExerciseNote.exercise_id == ex.id,
                ))).scalar_one_or_none()
                if dup:
                    continue
                t.add(SessionExerciseNote(
                    session_id=ses.id, exercise_id=ex.id, note=r["note"]))
                stats["notes"] += 1

        # ── досрочно завершённые (таблица могла отсутствовать) ──
        if "session_skipped_exercises" in tables:
            rows = (await s.execute(
                select(SessionSkippedExercise.__table__))).all()
            for r in rows:
                ses = old_to_ses.get(r.session_id)
                ex = old_to_ex.get(r.exercise_id)
                if not ses or not ex:
                    continue
                t.add(SessionSkippedExercise(
                    session_id=ses.id, exercise_id=ex.id))
                stats["skipped"] += 1

        await t.commit()

    print("Миграция завершена:", stats)
    await src_engine.dispose()
    await tgt_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="URL старой БД")
    parser.add_argument("--target", required=True, help="URL новой БД")
    args = parser.parse_args()
    asyncio.run(migrate(args.source, args.target))
