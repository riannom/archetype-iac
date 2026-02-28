# Support Bundle Triage Runbook

Use this runbook to triage customer incidents from a support bundle without needing live access first.

## 1. Validate Bundle Integrity

Check these files first:

- `manifest.json`: confirms included files, hashes, and size/truncation.
- `errors.json` (if present): lists artifacts that failed to collect.
- `incident/user-report.json`: user-reported summary, repro, expected/actual behavior.

If required artifacts are missing, stop and request a regenerated bundle.

## 2. Establish Scope and Timeline

Build the incident timeline from:

- `api/action-logs.json`: recent jobs and audit events.
- `labs/<lab_id>/bundle.json`: lab state transitions, node/link errors, per-lab job excerpts.
- `system/controller.json`: controller settings impacting behavior.

Determine:

- impacted lab IDs and agent IDs,
- first failing timestamp (UTC),
- whether failure is control-plane-wide or isolated to one lab/host.

## 3. Check Control Plane and Queue Health

Use:

- `system/queue-status.json`: queue depth vs active/running jobs.
- `system/circuit-breaker.json`: enforcement/circuit breaker state.
- `observability/prometheus.json`: sampled platform metrics (targets, queue, DB, failures).
- `observability/prometheus-targets.json`: scrape health by target.
- `observability/prometheus-alerts.json`: active alerts at capture time.

Fast isolation:

- `targets_up_* < 1` or target health `down`: service availability/scrape issue.
- queue growth with low completions: worker/scheduler saturation or stuck jobs.
- DB idle-in-transaction spikes: transaction leak/long transaction path.

## 4. Review Service Logs by Component

Primary logs:

- `observability/loki-service-logs.json` (api/worker/scheduler/agent)
- `observability/loki-api-logs.json` (legacy compatibility path)

Correlate log timestamps with failing jobs from `api/action-logs.json`.

## 5. Drill into Agents and Runtime State

For each impacted agent:

- `agents/<agent_id>/snapshot.json`: online/offline, lock/overlay/OVS/images/live errors.
- `labs/<lab_id>/boot-logs-<agent_id>.json` (if present): boot/config delivery evidence.

If agent is offline in snapshot, prioritize connectivity/host health before deeper lab debugging.

## 6. Classify the Failure

Classify into one of these buckets to route quickly:

- preflight/connectivity/image validation
- missing images / capacity / host assignment
- link/VXLAN/OVS setup and reservation drift
- DB/session/connection/ORM-staleness errors
- partial host failures vs full control-plane failure

Use the class to choose the owning subsystem (API, scheduler, worker, agent, networking, DB).

## 7. Decide Next Action

Escalate with a minimal packet:

- incident summary and exact UTC failure window,
- failing job IDs and dominant failure class,
- impacted lab IDs + agent IDs,
- top 3 supporting log/metric snippets and file paths.

If bundle evidence is insufficient, request a fresh bundle including the same impacted labs/agents and a wider `time_window_hours`.
