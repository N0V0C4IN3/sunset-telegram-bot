# Repository Guidelines

## Project Structure & Module Organization

This repository contains a self-hosted Python Telegram bot for sunset forecasts. Runtime code lives under `app/`: `app/main.py` wires the bot, scheduler, database session factory, and weather client; `app/bot/` contains aiogram handlers, keyboards, and message text; `app/db/` contains SQLAlchemy models, sessions, and repository code; `app/services/` contains forecast, scoring, weather, timezone, scheduler, and location encryption logic. Database migrations are in `alembic/versions/`. Deployment files are at the repository root: `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `.env.example`, and `alembic.ini`.

## Build, Test, and Development Commands

- `python -m venv .venv` then `.venv\Scripts\Activate.ps1`: create and activate a local virtual environment on Windows.
- `pip install -r requirements.txt`: install Python dependencies.
- `python -m app.main`: run the bot locally, assuming `.env` provides `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, and `LOCATION_ENCRYPTION_KEY`.
- `alembic upgrade head`: apply all database migrations.
- `docker compose up -d --build`: build and run Postgres plus the bot; startup automatically runs migrations.
- `docker compose logs -f bot`: follow bot logs during local or Raspberry Pi deployment.

## Coding Style & Naming Conventions

Use Python 3.12-compatible code with 4-space indentation and type hints for public functions and async workflows. Keep modules focused by layer: Telegram UI logic in `app/bot`, persistence in `app/db`, and external/domain services in `app/services`. Prefer descriptive snake_case names for functions, variables, and modules; use PascalCase for classes. Keep logs privacy-conscious and avoid recording Telegram user data or coordinates.

## Testing Guidelines

There is currently no committed test suite. When adding tests, place them under `tests/` and use `pytest` naming conventions such as `test_scoring.py` and `test_returns_higher_score_for_clear_sky()`. Favor unit tests for scoring, timezone, encryption, and repository behavior, with integration coverage for database migrations where practical.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries, for example `Add V2 sunset scoring with air quality` and `Fix subscription state in info menus`. Keep commit subjects concise and action-oriented. Pull requests should describe the behavior change, list migration or configuration impacts, mention testing performed, and include screenshots only when Telegram message or menu output changes.

## Security & Configuration Tips

Do not commit `.env` or real Telegram tokens. Keep `LOCATION_ENCRYPTION_KEY` stable after deployment; changing it makes stored locations undecryptable. Use `.env.example` as the template for required settings.
