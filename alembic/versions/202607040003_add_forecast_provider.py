"""add forecast provider

Revision ID: 202607040003
Revises: 202607040002
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "202607040003"
down_revision: str | None = "202607040002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("forecast_cache")}
    if "provider" not in columns:
        op.add_column(
            "forecast_cache",
            sa.Column("provider", sa.String(32), nullable=False, server_default="open_meteo"),
        )
        op.execute(
            "UPDATE forecast_cache SET provider = weather_data->>'provider' "
            "WHERE weather_data->>'provider' IS NOT NULL"
        )


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("forecast_cache")}
    if "provider" in columns:
        op.drop_column("forecast_cache", "provider")
