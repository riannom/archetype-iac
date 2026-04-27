# CI Efficiency — Follow-up Batches Plan

**Created**: 2026-04-27
**Status**: PROPOSED
**Companion to**: `tasks/ci-efficiency-plan.md` (Batches 1–7 already shipped)

## Why this plan

Batches 1, 2, 3, 5, 7, 8 shipped. Batch 4 in flight (#142). Aggregate impact: **docs-only PRs ~28 min → ~30 sec; web-only ~28 min → ~10 min; api-only ~28 min → ~16 min** (post-Batch-8 expected ~10 min once durations cache primes). Batch 4 will drop frontend further.

This doc covers the next 11 batches across four risk tiers. Each batch is self-contained, sized as one PR, and has a subagent-ready prompt block.

## Dependency graph

```
Tier 1 (parallel-safe):
  Batch 9  ─── Batch 10  ─── Batch 11 (investigation, no PR)
                                │
Tier 2:                         ▼
  Batch 14 (metrics) ───┬─── Batch 12 (drop coverage)
                        └─── Batch 16 (xdist re-attempt, in Tier 3)
  Batch 13 (profile) — independent
Tier 3:
  Batch 15 (retire confidence-gate) — needs decision
  Batch 16 (xdist re-attempt) — blocked on 11 + 14
Tier 4:
  Batch 17, 18 — independent
```

---

## Tier 1 — small, defensive

### Batch 9 — Mirror 0-test sentinel to API shards

**Why**: Batch 4 v2 (#142) added a sentinel that fails the step if vitest reports `Test Files no tests`. The same class of false-positive lurks in API: if `pytest-split` ever fails to collect (broken `.test_durations`, syntax error in a test file picked up early, etc.), pytest exits 0 with no tests run. Defensive 5-line addition.

**Approach**: In each API shard's pytest invocation, tee output and grep for `passed` or `[N] tests collected` line. Fail step if 0 tests ran.

**Risk**: Very low. Pure additive defensive check.

**Acceptance**: Shard fails loudly if pytest collects 0 tests (manually verified via temporary --ignore-glob on PR).

**Subagent prompt**:

> In `.github/workflows/test.yml`, add a 0-test sentinel to the `Run API tests (shard ${{ matrix.shard }}/4)` step, mirroring the pattern in `Run frontend tests (shard ${{ matrix.shard }}/4)`. Tee pytest output to `/tmp/pytest-shard.log`, then grep for the pytest summary line (e.g., `=====.*passed.*=====` or `collected 0 items` for the failure case). Fail the step with `::error::` if no tests were collected. Don't change anything else. Branch `ci/batch-9-api-sentinel`. Don't push. Report SHA and diff.

---

### Batch 10 — Enforce frontend coverage thresholds on merged data

**Why**: Batch 4 disables thresholds per shard (`--coverage.thresholds.*=0`) so per-shard slices don't fail. The merge job doesn't re-check thresholds, so coverage drops silently on PRs. Restore the threshold safety net Batch 4 removed.

**Approach**: In `frontend-coverage-merge` job, after `nyc merge` produces `combined-coverage.json`, run `npx nyc check-coverage --lines 50 --statements 50 --functions 40 --branches 40 --temp-dir=...`. May require pointing nyc at a directory rather than a single file — verify with `nyc check-coverage --help`.

**Risk**: Low. If the threshold check fails on first run, current main coverage may legitimately be below threshold (test growth without commensurate test additions). Be ready to either lower thresholds or fix coverage gaps.

**Acceptance**: A PR that drops a file's coverage below 50% lines fails `frontend-coverage-merge`. `web/vitest.config.ts` thresholds become advisory only (per-shard now disabled, merge-time enforcement is the new authority).

**Subagent prompt**:

> In `.github/workflows/test.yml`, find the `frontend-coverage-merge` job. After the existing `nyc merge` step, add a `Check merged coverage thresholds` step that runs `npx nyc check-coverage --lines 50 --statements 50 --functions 40 --branches 40` against the merged output. Investigate the correct flags by running `npx nyc check-coverage --help` mentally — `--temp-dir` likely points to where nyc loads data. If `nyc check-coverage` doesn't accept a single merged JSON, fall back to manual threshold check via inline Python parsing `combined-coverage.json`. Branch `ci/batch-10-frontend-threshold`. Don't push. Report what flag combination worked.

---

### Batch 11 — Verify Batch 8 durations cache delivered

**Why**: Batch 8 (#141) shipped `.test_durations` cache. First api-touching PR after merge wrote the cache; second should read it and produce balanced shards. Without verification, we don't know if the merge logic worked.

**Approach**: NOT a PR. Investigation task: pull last 2 api-touching PRs from `gh run list`, check API shard durations. If slowest shard is still ~16 min on the 2nd run after merge, something's broken (cache write, key mismatch, merge logic). If slowest shard is ~10-12 min, batch shipped.

**Risk**: None (read-only).

**Acceptance**: Document slowest shard duration over 3 runs in a comment on a tracking issue or in `tasks/todo.md`. If broken, file a follow-up issue with hypothesis.

**Subagent prompt** (or do directly, no subagent needed):

> Investigation task. Run `gh run list --workflow=test.yml --limit 20 --json databaseId,headBranch,conclusion,createdAt`. Filter for runs after commit `8f282cbb` (Batch 8 merge) on api-touching branches. For each, query `gh run view <id> --json jobs` and extract durations of `API Tests (shard 1/4)` through `(shard 4/4)`. Report: median, min, max slowest-shard duration over those runs. Compare to pre-Batch-8 baseline of ~16 min. If slowest shard didn't drop, recommend a code investigation.

---

## Tier 2 — meaningful new wins

### Batch 12 — Drop coverage from PR runs; nightly main produces the trend

**Why**: Coverage instrumentation costs ~20-30% of test wall-clock. Removing it from PR-path runs cuts API tests from ~10 min (post-Batch-8) toward ~7 min, frontend by ~2 min, agent by ~1 min. Combined: **~5+ minutes off the longest pole**. Trade-off: PR authors lose immediate "did my change tank coverage" feedback; gain it back via nightly trend on main.

**Approach**:
- Remove `--coverage` from the per-shard pytest and per-shard vitest invocations on PR/push:main triggers.
- Remove the `api-coverage-merge` and `frontend-coverage-merge` jobs from the PR path (or condition them on `github.event_name == 'schedule'`).
- Add a new workflow `.github/workflows/nightly-coverage.yml` that runs daily at e.g. 06:00 UTC, runs api+agent+frontend tests with coverage, uploads to e.g. Codecov or stores artifacts with retention.
- `ci-required` aggregator no longer needs the coverage-merge jobs in `needs:`.

**Risk**: Med. Trade-off is a policy change. If you discover coverage regressions only nightly, you might land a PR that drops coverage — but the next-day trend alert catches it. Mitigation: add a coverage comment bot to PRs that compares against main's last nightly snapshot (cheap to implement later).

**Acceptance**:
- [ ] PR runs no longer execute coverage instrumentation; visible duration drop.
- [ ] Nightly workflow runs successfully on schedule; coverage artifact lands somewhere queryable.
- [ ] First PR after merge shows total wall-clock under [target] min.

**Subagent prompt**:

> Implement Batch 12 of `tasks/ci-efficiency-followups.md`. Three changes: (1) remove `--cov=app`, `--cov-report=`, and the `COVERAGE_FILE` env from the API shards in `.github/workflows/test.yml`; (2) remove `--coverage` from the frontend shards; (3) drop `api-coverage-merge` and `frontend-coverage-merge` jobs from the PR path entirely (or gate them on `github.event_name == 'schedule'`). Add a new workflow file `.github/workflows/nightly-coverage.yml` that runs daily, executes the same shard matrix WITH coverage, runs the merge jobs, and uploads merged coverage as artifacts with 90-day retention. Update `ci-required.needs` to drop the coverage-merge jobs. Update `tasks/todo.md` (append a Batch 12 section). Branch `ci/batch-12-no-pr-coverage`. Don't push. Report what threshold enforcement looks like in the nightly path.

---

### Batch 13 — Profile API tests with `pytest --durations=20`

**Why**: We split 28 min over 4 shards but never asked *why* 28 min in the first place. Pareto-likely: 5-10 slow tests dominate. Surfacing them opens targeted optimizations independent of CI structure.

**Approach**: One CI run with `--durations=20` added to the API pytest invocation. Capture artifact. Post results as PR comment for human triage.

**Risk**: None. Pure observability change, no behavior change.

**Acceptance**: Run completes; artifact contains the top 20 slow tests; followup issues filed for tests > 5 sec.

**Subagent prompt**:

> Add `--durations=20` to the API shard pytest invocation in `.github/workflows/test.yml`. Tee the output (or use `--durations-min=1.0`) and capture the durations summary as an artifact (e.g., `api-test-durations-summary-shard-N.txt`) by parsing the pytest output between the `slowest durations` header and the next blank line. Don't try to consolidate across shards — just upload per-shard. After CI runs, manually inspect the artifacts and open follow-up issues for any test taking > 5 seconds. Branch `ci/batch-13-pytest-durations`. Don't push.

---

### Batch 14 — CI metrics dashboard (`scripts/ci_metrics.py`)

**Why**: Original plan called for this in Batch 1 but it never landed. Without it, "did our work make CI faster" is anecdotal. With it, we can report exact numbers and detect regressions automatically.

**Approach**: New script `scripts/ci_metrics.py` that:
1. Calls `gh run list --workflow=test.yml --limit 200 --json databaseId,conclusion,headBranch,createdAt,updatedAt`.
2. For successful runs in last 30 days, computes wall-clock = `updatedAt - createdAt`.
3. Bucket by classification: docs-only / web-only / api-only / cross-cutting (read changes-job outputs from `gh run view` for that).
4. Print median + p95 per bucket.
5. Add to `Makefile`: `make ci-metrics`.

**Risk**: Low. New script, no CI workflow changes.

**Acceptance**: `make ci-metrics` outputs a table like:

```
Bucket          | Runs | Median | P95
----------------|------|--------|------
docs-only       |   3  | 0:35   | 0:48
web-only        |  18  | 9:12   | 11:45
api-only        |   7  | 14:30  | 16:22
cross-cutting   |   6  | 17:45  | 22:10
```

**Subagent prompt**:

> Implement Batch 14. Write `scripts/ci_metrics.py` per the spec in `tasks/ci-efficiency-followups.md`. Use `subprocess.run(["gh", "run", "list", ...])` to fetch JSON. Use `gh run view <id> --json jobs` to read the `Detect changed paths` job's outputs to classify the run (you may need to fetch logs for the outputs since they're not in the JSON view directly — alternative: use the PR's file list via `gh pr list --json files` and apply the same path filters in Python). Add a `Makefile` target `ci-metrics`. No workflow changes. Test by running it on the current repo. Branch `ci/batch-14-metrics-dashboard`. Don't push.

---

## Tier 3 — bigger structural changes

### Batch 15 — Retire `Confidence Gate` from PR path

**Why**: After Batch 2's gating, `Confidence Gate` and the dedicated test jobs run on the same triggers. They overlap. The dedicated jobs are faster, parallelizable, cacheable; Confidence Gate is a meta-runner that re-runs subsets of them. Currently runs ~3.7 min per non-docs PR.

**Approach**:
- Remove `confidence-gate` and `confidence-gate-optional` jobs from the PR/push triggers in `test.yml`.
- Move them to a new `.github/workflows/confidence-gate-nightly.yml` with `schedule: cron 0 6 * * *` and `workflow_dispatch:`.
- Update `ci-required.needs` to drop `confidence-gate`.
- The `make confidence-gate` local target stays available for developers.

**Decision required from human**: confirm Confidence Gate's value is fully covered by dedicated jobs, or counter-propose. This is the recommendation but it's not a slam-dunk.

**Risk**: Med. If Confidence Gate catches anything the dedicated jobs miss (it has its own rule logic), removing it weakens defense.

**Acceptance**:
- [ ] PR runs no longer show `Confidence Gate` checks.
- [ ] Nightly workflow produces the same `confidence-gate-report-required.json` artifact.
- [ ] Across 2 weeks, no incident traceable to "would have been caught by Confidence Gate but wasn't by dedicated jobs."

**Subagent prompt** (only after human decision):

> Per the decision in `tasks/ci-efficiency-followups.md` Batch 15, remove `confidence-gate` and `confidence-gate-optional` jobs from `.github/workflows/test.yml`'s PR/push triggers. Move them to a new workflow file `.github/workflows/confidence-gate-nightly.yml` with `schedule: cron 0 6 * * *` and `workflow_dispatch`. Update `ci-required.needs` to drop both confidence-gate jobs. Branch `ci/batch-15-retire-confidence-gate`. Don't push.

---

### Batch 16 — Re-attempt `pytest-xdist -n auto` on API shards

**Why**: Batch 5 shipped `pytest-xdist` installed but `-n auto` was descoped after runner-shutdown failures. Now that:
- Sharding spreads load across 4 runners (less per-runner pressure)
- `.test_durations` cache exists (better task scheduling)
- Coverage is gone (Batch 12) — no instrumentation overhead

…each shard runs ~25% of the suite and might safely use `-n 2` (one xdist worker per CPU on a 2-core shard). Could drop slowest shard from ~10 min toward ~5-6 min.

**Approach**:
- Add `-n 2` to the per-shard pytest invocation.
- Verify per-worker DB isolation still holds (the `api/tests/conftest.py` fix from Batch 5 should already handle this).
- Watch for the runner-shutdown signal; if it returns, immediately revert.

**Risk**: Med-high. The original failure was opaque. Need Batch 14 (metrics) live first to detect regression.

**Acceptance**:
- [ ] All 4 shards complete in <8 min (from ~10 min baseline).
- [ ] No "shutdown signal" errors over 5 consecutive runs.

**Subagent prompt**:

> Implement Batch 16. In `.github/workflows/test.yml`, add `-n 2` to each API shard's pytest invocation. Verify `api/tests/conftest.py` still has the `PYTEST_XDIST_WORKER`-keyed DATABASE_URL/WORKSPACE override (added in Batch 5). Open the PR with title `ci: re-enable xdist within API shards`. Watch first run carefully; if any shard hits "runner has received a shutdown signal," DO NOT push fixes — open a comment with the log link, revert the change, and stop. Branch `ci/batch-16-shard-xdist`. Don't push.

---

## Tier 4 — quality-of-life

### Batch 17 — Add `workflow_dispatch:` to `test.yml`

**Why**: Today, the only way to run CI is to push a commit. Sometimes you want to re-validate main without a code change (post-revert, dependency-bump verification, dependency-update probe). One-line addition.

**Approach**: Add `workflow_dispatch:` to the `on:` block.

**Risk**: None.

**Acceptance**: `gh workflow run test.yml --ref main` triggers a full unconditional run.

**Subagent prompt**: trivial, no subagent needed — single line in `.github/workflows/test.yml` `on:` block.

---

### Batch 18 — Larger runners for API tests

**Why**: GitHub paid runners (4-/8-core) drop CPU-bound jobs by 30-50%. If you're on a paid plan with allowance, this is essentially free.

**Approach**: Change `runs-on: ubuntu-latest` to `runs-on: ubuntu-latest-8-cores` (or similar) on the api-tests matrix job. Combine with Batch 16's `-n 2` (or scale to `-n 4`) for compounded speedup.

**Risk**: Low (no semantic change). Cost depends on billing.

**Acceptance**: Slowest API shard drops by ≥30% with no other changes.

**Subagent prompt**:

> Verify cost impact first: confirm the org has paid GitHub Actions runner allowance. If yes, change `runs-on` for `api-tests` (and optionally `agent-tests`, `frontend-tests`) to a larger runner SKU. Recommend `ubuntu-latest-4-cores`. Don't change anything else. Branch `ci/batch-18-larger-runners`. Don't push until billing is confirmed.

---

## Recommended sequence

1. **Wait for #142 to land + observe one cycle.**
2. **Batch 11** (verify durations cache) — read-only, can do anytime.
3. **Batch 9 + 10** parallel — both are small defensive PRs.
4. **Batch 14** (metrics dashboard) — gives us measurement before bigger changes.
5. **Batch 13** (profile durations) — surfaces optimization targets.
6. **Decide on Batch 12** (drop coverage from PR runs) — biggest remaining single win, but a policy call.
7. **Decide on Batch 15** (retire Confidence Gate) — overlaps with Batch 12 in motivation.
8. **Batch 16** (xdist re-attempt) — only after 14 is live.
9. **Batches 17, 18** — fold into any of the above PRs as small adjacent changes.

## What I'd skip

- Pre-built test container image — adds image-management overhead for marginal speedup.
- Aggressive `--changed`-only test runs — risky regression exposure.
