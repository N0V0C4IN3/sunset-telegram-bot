#!/usr/bin/env sh
set -eu

alembic upgrade head
python -m app.main
