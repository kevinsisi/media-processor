.PHONY: help up down logs ps test lint fmt typecheck dev-api dev-web migrate

help:
	@echo "Targets: up down logs ps test lint fmt typecheck dev-api dev-web migrate"

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

test:
	pytest -v

lint:
	ruff check src tests

fmt:
	ruff format src tests

typecheck:
	mypy src

dev-api:
	uvicorn media_processor.api.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd web && npm run dev

migrate:
	alembic upgrade head
