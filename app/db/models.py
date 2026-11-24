"""Модели БД — описание всех таблиц."""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer,
    JSON, Numeric, String, Text,
    UniqueConstraint, false, func, true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ExerciseKind(str, Enum):
    WARMUP = "warmup"       # разминка: только длительность
    STRENGTH = "strength"   # силовое: подходы × повторения + вес
    CARDIO = "cardio"       # заминка: длительность, необязательное


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workout_types: Mapped[list["WorkoutType"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class WorkoutType(Base):
    __tablename__ = "workout_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_in_rotation: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="workout_types")
    exercises: Mapped[list["Exercise"]] = relationship(
        back_populates="workout_type",
        cascade="all, delete-orphan",
        order_by="Exercise.sort_order",
    )


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_type_id: Mapped[int] = mapped_column(
        ForeignKey("workout_types.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default=ExerciseKind.STRENGTH.value)

    sets: Mapped[int | None] = mapped_column(Integer)
    reps: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[str | None] = mapped_column(String(32))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    is_optional: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    workout_type: Mapped["WorkoutType"] = relationship(back_populates="exercises")


# Заложено на будущее — учёт фактического выполнения
class WorkoutSession(Base):
    __tablename__ = "workout_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workout_type_id: Mapped[int] = mapped_column(
        ForeignKey("workout_types.id", ondelete="RESTRICT")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    workout_type: Mapped["WorkoutType"] = relationship()

    sets: Mapped[list["SessionSet"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    exercise_notes: Mapped[list["SessionExerciseNote"]] = relationship(
        cascade="all, delete-orphan"
    )


class SessionSet(Base):
    __tablename__ = "session_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True
    )
    exercise_id: Mapped[int] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT")
    )
    set_number: Mapped[int] = mapped_column(Integer)
    reps: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[str | None] = mapped_column(String(32))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_warmup: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped["WorkoutSession"] = relationship(back_populates="sets")
    exercise: Mapped["Exercise"] = relationship()


class SessionExerciseNote(Base):
    """Заметка к конкретному упражнению в рамках одной тренировки."""
    __tablename__ = "session_exercise_notes"
    __table_args__ = (
        UniqueConstraint("session_id", "exercise_id", name="session_exercise_notes_session_id_exercise_id_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id", ondelete="CASCADE"), index=True)
    note: Mapped[str] = mapped_column(Text)
    exercise: Mapped["Exercise"] = relationship()


class SessionSkippedExercise(Base):
    """Упражнения, которые пользователь намеренно завершил досрочно."""
    __tablename__ = "session_skipped_exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id", ondelete="CASCADE"), index=True)


class Achievement(Base):
    """Полученные достижения пользователя (C2.6).

    Коды: workouts_10/50/100/250, streak_7/30, exercises_10,
    pr_first, pr_10, pr_50, pr_3_session, weight_plus_20.
    Одна строка = одна ачивка (защита от дублей — UNIQUE).
    """
    __tablename__ = "achievements"
    __table_args__ = (
        UniqueConstraint("user_id", "code", name="uq_achievement_user_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(32))
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MonthlyChallengeResult(Base):
    """Сохранённый итог одной цели за месяц со значениями на момент закрытия."""

    __tablename__ = "monthly_challenge_results"
    __table_args__ = (
        CheckConstraint("actual_value >= 0", name="ck_monthly_results_actual_value"),
        CheckConstraint("target_value > 0", name="ck_monthly_results_target_value"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True,
    )
    # Первый день месяца по МСК.
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    goal_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal_title: Mapped[str] = mapped_column(String(128))
    metric_unit: Mapped[str] = mapped_column(String(32))
    target_value: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    actual_value: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    goal_met: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict[str, Any]] = mapped_column(JSON)
    closed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
