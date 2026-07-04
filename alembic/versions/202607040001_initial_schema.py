"""initial schema

Revision ID: 202607040001
Revises:
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202607040001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("latitude_encrypted", sa.Text(), nullable=True),
        sa.Column("longitude_encrypted", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("subscribed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("threshold", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("lead_time_minutes", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("pending_input", sa.String(length=32), nullable=True),
        sa.Column("last_notified_for_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "forecast_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("forecast_date", sa.Date(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sunset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("weather_data", postgresql.JSONB(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_forecast_cache_user_date",
        "forecast_cache",
        ["user_id", "forecast_date"],
    )
    op.create_index("ix_forecast_cache_fetched_at", "forecast_cache", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_forecast_cache_fetched_at", table_name="forecast_cache")
    op.drop_constraint("uq_forecast_cache_user_date", "forecast_cache", type_="unique")
    op.drop_table("forecast_cache")
    op.drop_table("user_settings")
    op.drop_table("users")
