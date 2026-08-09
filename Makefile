.PHONY: install dev lint lint-fix format format-check typecheck check test \
        migrate migrate-down migration migrate-history package sam-build \
        deploy-dev deploy-prod stripe-listen

install:
	uv python install 3.12
	uv sync --frozen
	uv run pre-commit install

run:
	uv run uvicorn app.main:app --reload --port 8001

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