"""store the Sunsethue run a forecast comes from

Revision ID: 202611210001
Revises: 202610080001
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202611210001"
down_revision: str | None = "202610080001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows stay NULL: the cache verdict judges those by the schedule.
    op.add_column("forecast_cache", sa.Column("run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("forecast_cache", sa.Column("next_update_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("forecast_cache", "next_update_at")
    op.drop_column("forecast_cache", "run_at")
