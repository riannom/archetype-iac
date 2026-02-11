.PHONY: audit audit-ovs test-agent test-api observability-canary

audit:
	python3 scripts/cleanup_audit.py

audit-ovs:
	python3 scripts/cleanup_audit.py --include-ovs

test-agent:
	pytest -q agent/tests

test-api:
	pytest -q api/tests

observability-canary:
	python3 scripts/observability_canary.py
