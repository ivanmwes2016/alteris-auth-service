.PHONY: install dev lint format typecheck check test migrate deploy-dev deploy-prod

install:
	pip install -r requirements.txt
	pre-commit install

dev:
	uvicorn app.main:app --reload --port 8001

# ── Linting ───────────────────────────────────────────────────────────────────
lint:
	ruff check .

lint-fix:
	ruff check . --fix

format:
	ruff format .

format-check:
	ruff format . --check

typecheck:
	mypy app/

# Run all checks (used in CI)
check: format-check lint typecheck

# ── Testing ───────────────────────────────────────────────────────────────────
test:
	pytest tests/ -v

# ── Database / Alembic ────────────────────────────────────────────────────────
.PHONY: migrate
migrate:
	alembic upgrade head

migrate-down:
	alembic downgrade -1

.PHONY: migration
migration:
	@echo "Applying existing migrations..."
	$(MAKE) migrate

	@echo "Creating new migration: $(name)"
	alembic revision --autogenerate -m "$(name)"

	@echo "Applying new migration..."
	$(MAKE) migrate

migrate-history:
	alembic history --verbose

# ── Lambda packaging ──────────────────────────────────────────────────────────
package:
	pip install -r requirements.txt -t package/
	cp -r app package/
	cd package && zip -r ../lambda.zip . && cd ..
	@echo "✅  lambda.zip ready"

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
