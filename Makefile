.PHONY: install format format-check lint test api worker worker-once logs-worker migrate revision downgrade generate-encryption-key

install:
	uv sync --dev

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

test:
	uv run pytest

api:
	uv run uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload

worker:
	uv run python -m app.jobs.worker

worker-once:
	uv run python -m app.jobs.worker --once

logs-worker:
	docker compose logs --follow worker

migrate:
	uv run alembic upgrade head

revision:
	@test -n "$(MESSAGE)" || (echo "Usage: make revision MESSAGE='description'" && exit 2)
	uv run alembic revision --autogenerate -m "$(MESSAGE)"

downgrade:
	uv run alembic downgrade -1

generate-encryption-key:
	uv run python scripts/generate_encryption_key.py
