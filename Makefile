.PHONY: audit audit-ovs test-agent test-api test-api-container test-web-container test-web-container-down observability-canary observability-db-report observability-canary-nonprod observability-maintenance-nonprod observability-cron-install

API_TEST ?= tests
WEB_TEST ?=

audit:
	python3 scripts/cleanup_audit.py

audit-ovs:
	python3 scripts/cleanup_audit.py --include-ovs

test-agent:
	pytest -q agent/tests

test-api:
	@command -v python3.11 >/dev/null 2>&1 || { \
		echo "python3.11 is required for API tests. Install Python 3.11 and retry."; \
		exit 1; \
	}
	python3.11 -m pytest -q api/tests

test-api-container:
	docker exec archetype-iac-api-1 /bin/sh -lc 'cd /app && pytest -q $(API_TEST)'

test-web-container:
	docker compose -f docker-compose.gui.yml --profile test up -d web-test
	docker compose -f docker-compose.gui.yml --profile test exec -T web-test /bin/sh -lc '\
		HASH=$$(sha256sum package-lock.json | awk "{print \$$1}"); \
		CUR=$$(cat node_modules/.package-lock.hash 2>/dev/null || true); \
		if [ "$$HASH" != "$$CUR" ] || [ ! -x node_modules/.bin/vitest ]; then \
			npm ci && echo "$$HASH" > node_modules/.package-lock.hash; \
		fi; \
		if [ -n "$(WEB_TEST)" ]; then \
			npx vitest run --no-isolate --pool=threads --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=1 "$(WEB_TEST)"; \
		else \
			npx vitest run --no-isolate --pool=threads --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=1; \
		fi'

test-web-container-down:
	docker compose -f docker-compose.gui.yml --profile test rm -sf web-test || true

observability-canary:
	python3 scripts/observability_canary.py

observability-db-report:
	./scripts/observability_db_report.sh 30

observability-canary-nonprod:
	./scripts/run_observability_canary_nonprod.sh

observability-maintenance-nonprod:
	./scripts/run_observability_maintenance_nonprod.sh

observability-cron-install:
	./scripts/install_observability_cron_nonprod.sh
