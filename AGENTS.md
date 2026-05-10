# Repository Guidelines

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions).
- If something goes sideways, STOP and re-plan immediately — don't keep pushing.
- Use plan mode for verification steps, not just building.
- Write detailed specs upfront to reduce ambiguity.

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean.
- Offload research, exploration, and parallel analysis to subagents.
- For complex problems, throw more compute at it via subagents.
- One task per subagent for focused execution.

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern.
- Write rules for yourself that prevent the same mistake.
- Ruthlessly iterate on these lessons until mistake rate drops.
- Review lessons at session start for relevant project.

### 4. Completion Standard
- Never mark a task complete without proving it works.
- Use the smallest verification that convincingly covers the change: tests, type checks, logs, screenshots, or behavior diffs against main.
- If verification exposes a problem, stop, re-plan, fix the root cause, and verify again.
- Ask yourself: "Would a staff engineer approve this?"

### 5. Simplification And Elegance Gate
- Before presenting work as finished, re-read the diff and look for unnecessary complexity, duplication, oversized functions, brittle conditionals, and missed reuse of existing helpers.
- Simplify the code when it improves correctness, clarity, maintainability, or testability without broadening scope.
- For non-trivial changes, pause and ask: "Is there a more elegant way to design this if the requirement had existed from the start?"
- If a fix feels hacky, replace it with the cleaner design before calling it done.
- Skip elaborate redesign for simple, obvious fixes.

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding.
- Point at logs, errors, failing tests — then resolve them.
- Zero context switching required from the user.
- Go fix failing CI tests without being told how.

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items.
2. **Verify Plan**: Check in before starting implementation.
3. **Track Progress**: Mark items complete as you go.
4. **Explain Changes**: High-level summary at each step.
5. **Document Results**: Add review section to `tasks/todo.md`.
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections.

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
- **Destructive Commands Require One-Off Permission**: Never request, persist, or rely on broad approval prefixes for destructive cleanup commands such as `rm -rf`. Use an exact one-off approval for each destructive command, or avoid the cleanup by writing generated artifacts to ignored temp paths.

## Grade-Oriented Coding Defaults

- Keep new and edited functions below `CCN <= 15`; split branching render logic, parsing, validation, and async workflows into named helpers before they grow.
- Treat production files over `400` lines as a design warning and over `500` lines as a refactor trigger unless the file is generated or deliberately classified.
- For complex UI, default to small view components plus a controller hook/helper layer; keep data shaping, state transitions, and rendering decisions separate.
- Add behavioral tests with the code, including null/empty/error/boundary cases; preserve coverage with meaningful tests, not synthetic defensive branches.
- Use existing schema validation, error handling, logging, timeout, and retry utilities at system boundaries instead of ad hoc logic.
- Before final review, run focused tests and touched-file lizard checks when the change affects non-trivial logic.

## Final Review And Response

Before presenting results to the user:

1. **Quality review** — Re-read all changed code and docs for correctness, reuse opportunities, simplification opportunities, performance or efficiency issues, and unintended scope creep. Fix anything it flags.
2. **Edge case audit** — Explicitly verify:
   - Null/undefined inputs and empty arrays/objects.
   - Off-by-one errors in loops and slicing.
   - Missing error handling at system boundaries (API calls, DB queries, user input).
   - Race conditions in async code.
   - Boundary values (0, negative numbers, MAX_SAFE_INTEGER, empty strings).
3. **Verification review** — Confirm the task has adequate tests or checks, and state what was run.
4. **Self-review** — Re-read the diff and confirm the changes match the user's intent with no unintended side effects.
5. **Final response** — Summarize what changed and what was verified first. After that summary, provide next steps for the user; "None required" is a valid answer when no follow-up action is needed.

Use depth proportional to the request. For non-trivial work, make assumptions explicit, evaluate tradeoffs and failure modes, and flag second-order consequences. For simple work, stay concise without skipping verification.

## Project Structure & Module Organization
- Current repository contents are minimal; no source tree is present yet.
- When adding code, prefer a clear split such as `src/` for application code, `tests/` for automated tests, and `assets/` for static files.
- Keep top-level clutter low; group related modules under feature folders (for example, `src/auth/`, `src/api/`).

## Build, Test, and Development Commands
- Developer commands are defined in the `Makefile`:
  - `make audit` — dry-run cleanup audit (no deletions).
  - `make audit-ovs` — dry-run cleanup audit including OVS/VXLAN checks.
  - `make cleanup-legacy-runtime-artifacts` — dry-run audit for pre-metadata OVS/libvirt runtime artifacts (`python3 scripts/cleanup_legacy_runtime_artifacts.py --apply` to remove them explicitly).
  - `make test-agent` — run agent test suite.
  - `make test-api` — run API test suite (uses local `python3.14` when available; otherwise falls back to `make test-api-container` if `archetype-iac-api-1` is running).
  - `make test-api-container` — run API tests inside the running API container (`API_TEST=<path-or-kexpr>` optional, default `tests`).
  - `make test-api-catalog-regression` — run targeted catalog/manifest non-regression API tests.
  - `make test-web-container` — run web/vitest in a persistent Docker Compose `web-test` service with cached `node_modules` (`WEB_TEST=<vitest-file-or-pattern>` optional).
  - `make test-web-container-down` — remove the `web-test` container when you are done.
  - `make observability-canary` — run observability canary checks (read-only by default).
  - `make observability-db-report` — print DB-backed duration/failure diagnostics (30-day default).
  - `make observability-canary-nonprod` — run controlled non-prod canary traffic and coverage checks.
  - `make observability-maintenance-nonprod` — run canary + support-bundle triage drill + DB report bundle and write timestamped logs under `reports/observability/`.
  - `make observability-cron-install` — print suggested non-prod cron entries (use `./scripts/install_observability_cron_nonprod.sh --apply` to install).
  - `make iso-metadata-parity ISO=<path-to-iso> [JSON_OUT=reports/iso-parity.json]` — show parsed→stored→runtime metadata parity per ISO node definition.
  - `make confidence-gate [BASE=origin/main] [CONFIDENCE_FILES="<paths>"] [CONFIDENCE_RULES=scripts/confidence_gate_rules.json] [CONFIDENCE_MIN_SCORE=0]` — plan minimal relevant build/test checks and write `reports/confidence-gate/latest.json`.
  - `make confidence-gate-run [BASE=origin/main] [CONFIDENCE_FILES="<paths>"] [CONFIDENCE_RULES=scripts/confidence_gate_rules.json] [CONFIDENCE_MIN_SCORE=0]` — execute selected checks and fail on required check failures or low confidence.
  - `make confidence-gate-json [BASE=origin/main] [CONFIDENCE_FILES="<paths>"] [CONFIDENCE_RULES=scripts/confidence_gate_rules.json] [CONFIDENCE_MIN_SCORE=0]` — emit machine-readable confidence-gate output to stdout.
  - `make backfill-device-image-catalog [CATALOG_MANIFEST=/var/lib/archetype/images/manifest.json] [CATALOG_APPLY=1] [CATALOG_DATABASE_URL=postgresql+psycopg://archetype:archetype@localhost:15432/archetype]` — seed canonical catalog tables from vendor/custom/manifest sources (dry-run unless `CATALOG_APPLY=1`).
  - `make backfill-manifest-compatible-devices [CATALOG_MANIFEST=/var/lib/archetype/images/manifest.json] [MANIFEST_BACKFILL_APPLY=1]` — normalize `compatible_devices` for shared qcow2 artifacts (dry-run unless apply flag set).
  - `make catalog-manifest-drift-check [CATALOG_MANIFEST=/var/lib/archetype/images/manifest.json] [CATALOG_DRIFT_JSON_OUT=<optional-report-path>] [CATALOG_DRIFT_FAIL=1]` — compare manifest vs DB catalog and optionally fail on drift.
  - `make catalog-maintenance` — run DB catalog backfill, manifest compatibility backfill, and drift check as one bundle.
  - `python3 scripts/coverage_map.py` — generate test-to-source coverage mapping and gaps report (outputs under `reports/`).
  - `python3 scripts/confidence_gate.py --base origin/main --run --report-path reports/confidence-gate/latest.json` — direct CLI entrypoint for rule-driven check selection and confidence scoring.
  - `python3 scripts/backfill_manifest_compatible_devices.py --manifest /var/lib/archetype/images/manifest.json` — dry-run manifest compatibility backfill for shared qcow2 images (`--apply` to write changes).
  - `python3 scripts/backfill_device_image_catalog_db.py --manifest /var/lib/archetype/images/manifest.json` — dry-run DB catalog backfill (`--apply` to persist) after Alembic revisions `055` and `056`.
  - `python3 scripts/catalog_manifest_drift_check.py --manifest /var/lib/archetype/images/manifest.json` — compare manifest and catalog projection (`--json-out <path>` and `--fail-on-drift` supported).
  - `python3 scripts/cleanup_legacy_runtime_artifacts.py [--apply] [--skip-ovs] [--skip-libvirt]` — audit or explicitly remove pre-metadata OVS ports and libvirt domains that lack deterministic Archetype identity metadata.
  - `python3 scripts/observability_canary.py --apply --lab-id <lab_id> --sync-node-id <node_id> --run-up-down` — generate controlled traffic (status/sync/up/down) and validate metric coverage.
  - `./scripts/run_support_bundle_triage_nonprod.sh` — seed deterministic failure signals, generate a support bundle via API, and validate triage artifact completeness.
  - `./scripts/observability_db_report.sh [days]` — query Postgres for long-running tasks, failure rates, and failure classes.
  - `./scripts/run_observability_canary_nonprod.sh` — wrapper for scheduled non-prod canary runs (`CANARY_LAB_ID` and `CANARY_SYNC_NODE_ID` required).
  - `./scripts/run_observability_maintenance_nonprod.sh` — runs canary + DB report and stores logs in `reports/observability/`.
  - `./scripts/install_observability_cron_nonprod.sh [--apply]` — print or install recurring non-prod cron jobs.

## Coding Style & Naming Conventions
- Use consistent indentation (2 or 4 spaces) and enforce it with a formatter once a language is chosen.
- Name files and folders with lowercase and dashes (`kebab-case`) unless the chosen language prefers another convention.
- Add a formatter and linter early (for example, `prettier`, `eslint`, `ruff`, `gofmt`) and document the exact commands.

## Testing Guidelines
- No testing framework is configured yet.
- When tests are added, keep them colocated in `tests/` or alongside modules (for example, `src/foo.test.ts`).
- Name tests with a clear suffix (`*.test.*` or `*_test.*`) and ensure tests are runnable via a single command.
- If you spot a bug, write a non-regression test first, then fix it.

## Commit & Pull Request Guidelines
- No Git history is available to infer conventions. Use Conventional Commits by default (for example, `feat: add user login`).
- Pull requests should include a concise description, linked issues (if any), and screenshots for UI changes.
- Keep PRs focused and small; prefer multiple targeted PRs over a single large one.

## Agent-Specific Instructions
- If you add scripts or automation, keep them deterministic and document prerequisites.
- Update this guide whenever new tooling or structure is introduced.
- When UI or API changes affect running containers, rebuild and restart the Docker Compose stack using `docker compose -f docker-compose.gui.yml up -d --build`.

## Architecture Guidelines
- Build with extensibility in mind: prefer adapter/strategy patterns over hard-coded providers or vendors.
- Keep provider-specific logic in dedicated modules and expose a stable interface to the rest of the app.
- Avoid tight coupling between UI and backend implementations; use API contracts and feature flags instead.

