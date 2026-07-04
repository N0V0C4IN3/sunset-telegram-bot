"""encrypt location columns

Revision ID: 202607040002
Revises: 202607040001
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "202607040002"
down_revision: str | None = "202607040001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "latitude_encrypted" not in columns:
        op.add_column("users", sa.Column("latitude_encrypted", sa.Text(), nullable=True))
    if "longitude_encrypted" not in columns:
        op.add_column("users", sa.Column("longitude_encrypted", sa.Text(), nullable=True))
    if "latitude" in columns:
        op.drop_column("users", "latitude")
    if "longitude" in columns:
        op.drop_column("users", "longitude")


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "latitude" not in columns:
        op.add_column("users", sa.Column("latitude", sa.Numeric(9, 6), nullable=True))
    if "longitude" not in columns:
        op.add_column("users", sa.Column("longitude", sa.Numeric(9, 6), nullable=True))
    if "longitude_encrypted" in columns:
        op.drop_column("users", "longitude_encrypted")
    if "latitude_encrypted" in columns:
        op.drop_column("users", "latitude_encrypted")
