"""Keep the server defaults and note uniqueness used on the legacy VPS."""

import sqlalchemy as sa
from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("exercises", "is_archived", server_default=sa.false())
    op.alter_column("workout_types", "is_archived", server_default=sa.false())
    op.alter_column("workout_types", "is_in_rotation", server_default=sa.true())
    # The legacy VPS already has this constraint. PostgreSQL also supports this
    # check when running the generated offline migration SQL.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'session_exercise_notes'::regclass
                  AND conname = 'session_exercise_notes_session_id_exercise_id_key'
            ) THEN
                ALTER TABLE session_exercise_notes
                    ADD CONSTRAINT session_exercise_notes_session_id_exercise_id_key
                    UNIQUE (session_id, exercise_id);
            END IF;
        END;
        $$;
    """)


def downgrade() -> None:
    op.drop_constraint(
        "session_exercise_notes_session_id_exercise_id_key", "session_exercise_notes", type_="unique"
    )
    op.alter_column("workout_types", "is_in_rotation", server_default=None)
    op.alter_column("workout_types", "is_archived", server_default=None)
    op.alter_column("exercises", "is_archived", server_default=None)
