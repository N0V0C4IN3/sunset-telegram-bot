"""store a forecast per solar event

Revision ID: 202610080001
Revises: 202607040003
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202610080001"
down_revision: str | None = "202607040003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every row cached so far is a sunset, which is what the default says.
    op.add_column(
        "forecast_cache",
        sa.Column("event", sa.String(16), nullable=False, server_default="sunset"),
    )
    op.alter_column("forecast_cache", "sunset_at", new_column_name="event_at")
    op.drop_constraint("uq_forecast_cache_user_date", "forecast_cache", type_="unique")
    op.create_unique_constraint(
        "uq_forecast_cache_user_date_event", "forecast_cache", ["user_id", "forecast_date", "event"]
    )


def downgrade() -> None:
    # Sunrise rows have nowhere to go in the old shape.
    op.execute("DELETE FROM forecast_cache WHERE event <> 'sunset'")
    op.drop_constraint("uq_forecast_cache_user_date_event", "forecast_cache", type_="unique")
    op.create_unique_constraint("uq_forecast_cache_user_date", "forecast_cache", ["user_id", "forecast_date"])
    op.alter_column("forecast_cache", "event_at", new_column_name="sunset_at")
    op.drop_column("forecast_cache", "event")
