.PHONY: audit audit-ovs test-agent test-api test-api-container test-web-container observability-canary observability-db-report observability-canary-nonprod observability-maintenance-nonprod observability-cron-install

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
	docker run --rm \
		-v "$(PWD)/web:/app" \
		-w /app \
		node:20-alpine \
		/bin/sh -lc 'npm ci && if [ -n "$(WEB_TEST)" ]; then NODE_OPTIONS=--max-old-space-size=3072 npx vitest run --no-isolate --pool=threads --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=1 "$(WEB_TEST)"; else NODE_OPTIONS=--max-old-space-size=3072 npx vitest run --no-isolate --pool=threads --poolOptions.threads.minThreads=1 --poolOptions.threads.maxThreads=1; fi'

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
