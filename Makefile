.PHONY: audit audit-ovs test-agent test-api test-api-container test-web-container test-web-container-down observability-canary observability-db-report observability-canary-nonprod observability-maintenance-nonprod observability-cron-install iso-metadata-parity confidence-gate confidence-gate-run confidence-gate-json backfill-device-image-catalog backfill-manifest-compatible-devices catalog-manifest-drift-check catalog-maintenance install-gitleaks install-hooks scan-secrets

API_TEST ?= tests
WEB_TEST ?=
ISO ?=
JSON_OUT ?=
BASE ?= origin/main
CONFIDENCE_FILES ?=
CONFIDENCE_REPORT ?= reports/confidence-gate/latest.json
CONFIDENCE_RULES ?= scripts/confidence_gate_rules.json
CONFIDENCE_MIN_SCORE ?= 0
CATALOG_MANIFEST ?= /var/lib/archetype/images/manifest.json
CATALOG_APPLY ?= 0
CATALOG_DATABASE_URL ?= postgresql+psycopg://archetype:archetype@localhost:15432/archetype
MANIFEST_BACKFILL_APPLY ?= 0
CATALOG_DRIFT_FAIL ?= 0
CATALOG_DRIFT_JSON_OUT ?=

audit:
	python3 scripts/cleanup_audit.py

audit-ovs:
	python3 scripts/cleanup_audit.py --include-ovs

test-agent:
	pytest -q agent/tests

test-api:
	@if command -v python3.11 >/dev/null 2>&1; then \
		python3.11 -m pytest -q api/tests; \
	elif docker ps --format '{{.Names}}' | grep -q '^archetype-iac-api-1$$'; then \
		echo "python3.11 not found locally; running API tests in container archetype-iac-api-1"; \
		$(MAKE) test-api-container; \
	else \
		echo "python3.11 is not available locally and API container archetype-iac-api-1 is not running."; \
		echo "Start the stack (docker compose -f docker-compose.gui.yml up -d api) or run make test-api-container after the container is up."; \
		exit 1; \
	fi

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

iso-metadata-parity:
	@if [ -z "$(ISO)" ]; then \
		echo "Usage: make iso-metadata-parity ISO=/path/to/file.iso [JSON_OUT=reports/iso-parity.json]"; \
		exit 1; \
	fi
	@if [ -n "$(JSON_OUT)" ]; then \
		python3 scripts/iso_metadata_parity_report.py --iso "$(ISO)" --json-out "$(JSON_OUT)"; \
	else \
		python3 scripts/iso_metadata_parity_report.py --iso "$(ISO)"; \
	fi

confidence-gate:
	@if [ -n "$(CONFIDENCE_FILES)" ]; then \
		python3 scripts/confidence_gate.py --rules "$(CONFIDENCE_RULES)" --min-score "$(CONFIDENCE_MIN_SCORE)" --files $(CONFIDENCE_FILES) --report-path "$(CONFIDENCE_REPORT)"; \
	else \
		python3 scripts/confidence_gate.py --rules "$(CONFIDENCE_RULES)" --min-score "$(CONFIDENCE_MIN_SCORE)" --base "$(BASE)" --report-path "$(CONFIDENCE_REPORT)"; \
	fi

confidence-gate-run:
	@if [ -n "$(CONFIDENCE_FILES)" ]; then \
		python3 scripts/confidence_gate.py --run --rules "$(CONFIDENCE_RULES)" --min-score "$(CONFIDENCE_MIN_SCORE)" --files $(CONFIDENCE_FILES) --report-path "$(CONFIDENCE_REPORT)"; \
	else \
		python3 scripts/confidence_gate.py --run --rules "$(CONFIDENCE_RULES)" --min-score "$(CONFIDENCE_MIN_SCORE)" --base "$(BASE)" --report-path "$(CONFIDENCE_REPORT)"; \
	fi

confidence-gate-json:
	@if [ -n "$(CONFIDENCE_FILES)" ]; then \
		python3 scripts/confidence_gate.py --json --rules "$(CONFIDENCE_RULES)" --min-score "$(CONFIDENCE_MIN_SCORE)" --files $(CONFIDENCE_FILES); \
	else \
		python3 scripts/confidence_gate.py --json --rules "$(CONFIDENCE_RULES)" --min-score "$(CONFIDENCE_MIN_SCORE)" --base "$(BASE)"; \
	fi

backfill-device-image-catalog:
	@if docker ps --format '{{.Names}}' | grep -q '^archetype-iac-api-1$$'; then \
		if [ "$(CATALOG_APPLY)" = "1" ]; then \
			docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/backfill_device_image_catalog_db.py --manifest "$(CATALOG_MANIFEST)" --apply'; \
		else \
			docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/backfill_device_image_catalog_db.py --manifest "$(CATALOG_MANIFEST)"'; \
		fi; \
	else \
		if [ "$(CATALOG_APPLY)" = "1" ]; then \
			DATABASE_URL="$(CATALOG_DATABASE_URL)" python3 scripts/backfill_device_image_catalog_db.py --manifest "$(CATALOG_MANIFEST)" --apply; \
		else \
			DATABASE_URL="$(CATALOG_DATABASE_URL)" python3 scripts/backfill_device_image_catalog_db.py --manifest "$(CATALOG_MANIFEST)"; \
		fi; \
	fi

backfill-manifest-compatible-devices:
	@if docker ps --format '{{.Names}}' | grep -q '^archetype-iac-api-1$$'; then \
		if [ "$(MANIFEST_BACKFILL_APPLY)" = "1" ]; then \
			docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/backfill_manifest_compatible_devices.py --manifest "$(CATALOG_MANIFEST)" --apply'; \
		else \
			docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/backfill_manifest_compatible_devices.py --manifest "$(CATALOG_MANIFEST)"'; \
		fi; \
	else \
		if [ "$(MANIFEST_BACKFILL_APPLY)" = "1" ]; then \
			python3 scripts/backfill_manifest_compatible_devices.py --manifest "$(CATALOG_MANIFEST)" --apply; \
		else \
			python3 scripts/backfill_manifest_compatible_devices.py --manifest "$(CATALOG_MANIFEST)"; \
		fi; \
	fi

catalog-manifest-drift-check:
	@if docker ps --format '{{.Names}}' | grep -q '^archetype-iac-api-1$$'; then \
		if [ "$(CATALOG_DRIFT_FAIL)" = "1" ]; then \
			if [ -n "$(CATALOG_DRIFT_JSON_OUT)" ]; then \
				docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)" --json-out "$(CATALOG_DRIFT_JSON_OUT)" --fail-on-drift'; \
			else \
				docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)" --fail-on-drift'; \
			fi; \
		else \
			if [ -n "$(CATALOG_DRIFT_JSON_OUT)" ]; then \
				docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)" --json-out "$(CATALOG_DRIFT_JSON_OUT)"'; \
			else \
				docker exec archetype-iac-api-1 /bin/sh -lc 'if [ -d /app/project ]; then cd /app/project; else cd /app; fi; python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)"'; \
			fi; \
		fi; \
	else \
		if [ "$(CATALOG_DRIFT_FAIL)" = "1" ]; then \
			if [ -n "$(CATALOG_DRIFT_JSON_OUT)" ]; then \
				DATABASE_URL="$(CATALOG_DATABASE_URL)" python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)" --json-out "$(CATALOG_DRIFT_JSON_OUT)" --fail-on-drift; \
			else \
				DATABASE_URL="$(CATALOG_DATABASE_URL)" python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)" --fail-on-drift; \
			fi; \
		else \
			if [ -n "$(CATALOG_DRIFT_JSON_OUT)" ]; then \
				DATABASE_URL="$(CATALOG_DATABASE_URL)" python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)" --json-out "$(CATALOG_DRIFT_JSON_OUT)"; \
			else \
				DATABASE_URL="$(CATALOG_DATABASE_URL)" python3 scripts/catalog_manifest_drift_check.py --manifest "$(CATALOG_MANIFEST)"; \
			fi; \
		fi; \
	fi

catalog-maintenance:
	$(MAKE) backfill-device-image-catalog
	$(MAKE) backfill-manifest-compatible-devices
	$(MAKE) catalog-manifest-drift-check

install-gitleaks:
	bash scripts/install-gitleaks.sh

install-hooks: install-gitleaks
	bash scripts/install-hooks.sh

scan-secrets:
	gitleaks detect --config .gitleaks.toml --verbose
