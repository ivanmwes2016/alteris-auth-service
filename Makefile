.PHONY: install dev lint lint-fix format format-check typecheck check test \
        migrate migrate-down migration migrate-history package sam-build \
        deploy-dev deploy-prod stripe-listen db-local run-local migrate-local db-local-reset

install:
	uv python install 3.12
	uv sync --frozen
	uv run pre-commit install

run:
	uv run uvicorn app.main:app --reload --port 8001

# API spec + Postman collection in docs/ (Postman: Import → the .postman_collection.json)
openapi:
	uv run python -m scripts.export_openapi
	uv run python -m scripts.postman_collection

# ── Linting ───────────────────────────────────────────────────────────────────
lint:
	uv run ruff check .

lint-fix:
	uv run ruff check . --fix

format:
	uv run ruff format .

format-check:
	uv run ruff format . --check

typecheck:
	uv run mypy app/

# Run all checks (used in CI)
check: format-check lint typecheck

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	uv run pytest tests/ -v

# ── Database / Alembic ────────────────────────────────────────────────────────
migrate:
	uv run alembic upgrade head

migrate-down:
	uv run alembic downgrade -1

migration:
	@echo "Applying existing migrations..."
	$(MAKE) migrate

	@echo "Creating new migration: $(name)"
	uv run alembic revision --autogenerate -m "$(name)"

	@echo "Applying new migration..."
	$(MAKE) migrate

migrate-history:
	uv run alembic history --verbose

# ── Local database (Docker) ───────────────────────────────────────────────────
# A Postgres on this machine instead of the live one. Sign-in still uses Supabase.
# Note: plain `make migrate` uses .env, i.e. the live database; use migrate-local here.
LOCAL_DB = postgres:postgres@localhost:54330/skolarly
LOCAL_ENV = DATABASE_URL=postgresql+asyncpg://$(LOCAL_DB) DATABASE_URL_SYNC=postgresql://$(LOCAL_DB)

db-local:
	@docker start skolarly-db >/dev/null 2>&1 || docker run -d --name skolarly-db \
		-e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=skolarly -p 54330:5432 \
		-v skolarly-db:/var/lib/postgresql/data postgres:16 >/dev/null
	@until docker exec skolarly-db pg_isready -U postgres -d skolarly >/dev/null 2>&1; do sleep 1; done
	$(LOCAL_ENV) uv run python -m scripts.local_db

# Rate limits count in memory, so local requests never touch the live Redis.
run-local: db-local
	$(LOCAL_ENV) UPSTASH_REDIS_REST_URL= uv run uvicorn app.main:app --reload --port 8001

migrate-local:
	$(LOCAL_ENV) uv run alembic upgrade head

db-local-reset:
	docker rm -f skolarly-db && docker volume rm skolarly-db

# ── Lambda packaging ──────────────────────────────────────────────────────────
package:
	rm -rf package lambda.zip
	mkdir -p package
	uv export --frozen --no-dev --no-emit-project --format requirements-txt > package/requirements.txt
	uv pip install \
		--python-platform x86_64-manylinux2014 \
		--target package/ \
		-r package/requirements.txt
	rm package/requirements.txt
	cp -r app package/
	cd package && zip -r ../lambda.zip . && cd ..
	@echo "✅ lambda.zip ready"

# ── SAM deploy ────────────────────────────────────────────────────────────────
sam-build:
	sam build

deploy-dev: check sam-build
	sam deploy --config-env dev --parameter-overrides AppEnv=development

deploy-prod: check sam-build migrate
	sam deploy --config-env prod --parameter-overrides AppEnv=production

# ── Stripe CLI (local webhook testing) ───────────────────────────────────────
stripe-listen:
	stripe listen --forward-to localhost:8000/api/v1/billing/webhook