"""Добавить таблицу фиксированных итогов месячных челленджей."""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monthly_challenge_results",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("goal_key", sa.String(64), nullable=False),
        sa.Column("goal_title", sa.String(128), nullable=False),
        sa.Column("metric_unit", sa.String(32), nullable=False),
        sa.Column("target_value", sa.Numeric(18, 3), nullable=False),
        sa.Column("actual_value", sa.Numeric(18, 3), nullable=False),
        sa.Column("goal_met", sa.Boolean(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("actual_value >= 0", name="ck_monthly_results_actual_value"),
        sa.CheckConstraint("target_value > 0", name="ck_monthly_results_target_value"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "period_start", "goal_key"),
    )


def downgrade() -> None:
    op.drop_table("monthly_challenge_results")
