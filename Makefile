.PHONY: audit audit-ovs test-agent test-api observability-canary observability-db-report observability-canary-nonprod observability-maintenance-nonprod observability-cron-install

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
