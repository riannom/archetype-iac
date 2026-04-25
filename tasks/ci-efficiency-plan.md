# CI/CD Efficiency Plan

**Created**: 2026-04-25
**Status**: PROPOSED
**Goal**: Cut PR CI wall-clock and total runner-minutes by skipping work that the diff doesn't justify, parallelizing the unavoidable work, and de-duplicating layers that already overlap.

## Why

`.github/workflows/test.yml` runs ~9 jobs unconditionally on every PR. A docs-only PR currently triggers ~3+ hours of compute. The `confidence-gate` job already implements doc-aware skipping but runs *alongside* the jobs it would replace, so it adds overhead instead of removing it. The biggest single inefficiency is the frontend job, which runs each `.test.tsx` file in its own `npm test` invocation (133 invocations) and then runs vitest **again** for coverage.

## Design Principles

1. **One source of truth for "what to run"** — either confidence-gate or path-filter, not both layered.
2. **Required status check is an aggregator**, not the union of every job. Skipped jobs must not block docs-only PRs.
3. **Cancel stale PR runs, never cancel main/release**.
4. **Coverage is computed once per area**, not in two passes.
5. **Test isolation must survive parallelization** — sqlite filenames, `WORKSPACE`, fixtures.
6. **Each batch ships independently** with rollback signal in the next 5–10 PRs.

## Constraints

- Branch protection currently pins individual job names as required checks. Phase 1 must coordinate with admin to swap the pin to `ci-required` before old job names disappear, or PRs will block forever.
- Another agent is concurrently building out test files. CI changes must not regress per-file test runnability — the existing `vitest run <file>` invocation pattern is currently load-bearing for that workflow until Phase 4 lands.
- `release.yml` is the tag-push path and must remain unconditional + unsharded.

## Loop / subagent execution model

Each batch below is **one PR**. Process them via this loop:

```
while batch in ready_batches(plan):
  spawn subagent with prompt = batch.prompt
  subagent: implement → push branch → open PR
  human: review + merge
  observe next 5 PRs for regression signal
  mark batch complete; unblock dependents
```

Batches are independent unless `Depends on:` is listed. Batches 1 and 1a are gating (must land first + together); after that batches 2–6 can run in parallel if reviewers have bandwidth.

A subagent picks up a batch by reading the **Prompt** block verbatim. Each prompt is self-contained: it includes context, files to touch, acceptance criteria, and what *not* to change. A subagent should not need to reread this whole document.

---

## Batches

### Batch 1 — `changes` job + `ci-required` aggregator

**Owner**: subagent or human
**Depends on**: none
**Risk**: Low (additive; existing jobs keep running)
**Estimated effort**: ~½ day

**Goal**: Add a `changes` detection job at the top of `test.yml` that emits per-area boolean outputs. Add a final `ci-required` aggregator job that summarizes pass/fail/skip across every job that PR branch protection cares about. Do **not** yet gate any existing jobs — this batch only adds infrastructure.

**Files to touch**:
- `.github/workflows/test.yml`
- `tasks/ci-efficiency-plan.md` (mark Batch 1 in-progress / done)

**Filter design** (mirror `scripts/confidence_gate_rules_ci.json`):

| Output | Triggers (paths) |
|--------|------------------|
| `api` | `api/**`, `api/requirements*.txt`, `Dockerfile.api` |
| `agent` | `agent/**`, `agent/requirements.txt`, `Dockerfile.agent` |
| `web` | `web/**`, `web/package-lock.json` |
| `scripts` | `scripts/**`, `tests/scripts/**`, `Makefile`, `install*.sh` |
| `observability` | `grafana/**`, `prometheus*.yml`, `promtail-config.yml`, `scripts/observability_*`, `scripts/support_bundle*` |
| `runtime` | `docker-compose*.yml`, `Dockerfile.*` |
| `infra` (fan-out) | `.github/workflows/**`, `scripts/confidence_gate*`, `pyproject.toml`, `pytest.ini`, `VERSION` |
| `docs_only` | true if **all** changed paths match `*.md`, `assets/**`, `reports/**`, `screenshots/**`, `tasks/**` |

If `infra: true`, every other area output is forced to `true`. If `docs_only: true`, no area outputs are forced.

**Aggregator pattern**:

```yaml
ci-required:
  needs: [changes, api-tests, agent-tests, frontend-tests, lint, ...]
  if: always()
  runs-on: ubuntu-latest
  steps:
    - name: Reduce results
      run: |
        for r in "${{ needs.api-tests.result }}" "${{ needs.agent-tests.result }}" \
                 "${{ needs.frontend-tests.result }}" "${{ needs.lint.result }}"; do
          case "$r" in
            success|skipped) ;;
            *) echo "Required job failed: $r"; exit 1 ;;
          esac
        done
```

**Prompt** (hand to a subagent verbatim):

> You are adding a `changes` detection job and a `ci-required` aggregator job to `.github/workflows/test.yml`. Use `dorny/paths-filter@v3` and pin to a SHA. Add the filter table from `tasks/ci-efficiency-plan.md` Batch 1 verbatim. Do **not** add `if:` guards to any existing job in this PR — the aggregator should currently observe every existing job's `result` and accept `success` or `skipped` (in case a future batch starts gating). The aggregator must fail on `failure`, `cancelled`, or any other non-success/skipped result. Add `concurrency: { group: ci-${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: ${{ github.event_name == 'pull_request' }} }` at the workflow level. Keep `release.yml` and `observability-maintenance.yml` untouched. Open the PR with title `ci: add changes detection and ci-required aggregator` and reference `tasks/ci-efficiency-plan.md`.

**Acceptance**:
- [ ] `changes` job runs on every PR and emits all 8 outputs.
- [ ] `ci-required` runs after every other job and prints a summary table (job → result) before exiting.
- [ ] Concurrency group cancels the prior PR run on a force-push.
- [ ] CI minutes per PR unchanged (within ±5%) — this batch should be neutral, not faster or slower.
- [ ] Validation: `gh run list --branch <pr-branch>` shows `ci-required` reporting `success` on a noop edit.

**Validation script** (run before opening PR):

```bash
# Replay last 20 PRs through the new filter and confirm classification matches expectation
gh pr list --base main --state merged --limit 20 --json number,files |
  jq -r '.[] | "\(.number) \(.files | map(.path) | join(","))"' |
  while read num files; do
    classify "$files"  # local helper that mirrors the filter logic
  done
```

The author of this PR includes the classification table in the PR description.

---

### Batch 1a — Branch protection swap (admin)

**Owner**: human admin (cannot be subagent — requires repo-admin scope)
**Depends on**: Batch 1 merged to `main`
**Risk**: Med — if old required checks are removed before `ci-required` is recognized, every PR blocks.
**Estimated effort**: 15 min

**Steps**:
1. Confirm `ci-required` has run successfully on at least one PR after Batch 1 merge.
2. In branch protection for `main`: add `ci-required` as a required status check.
3. Wait for one more PR to confirm `ci-required` reports correctly.
4. Remove the old per-job required checks (`API Tests`, `Agent Tests`, `Frontend Tests`, `Lint`, `Confidence Gate`, etc.) — keep only `ci-required` and `Secret Scan`.
5. Document the new required check list in `.github/CLAUDE.md`.

**Acceptance**:
- [ ] Test docs-only PR (only edits a `.md` file) merges green via `ci-required`.
- [ ] Test failing-API PR (intentional) is blocked by `ci-required`.

**Why this is its own batch**: It's not code. Doing it in the same PR as Batch 1 risks the protection swap taking effect before the aggregator job is observed working, and the rollback is a manual admin action.

---

### Batch 2 — Gate jobs on `changes` outputs

**Depends on**: Batch 1a complete (branch protection now points at `ci-required`)
**Risk**: Med — wrong filter on a critical job ships a broken test signal.
**Estimated effort**: ~½ day

**Goal**: Add `if:` conditions to existing jobs so they skip when irrelevant. After this batch, a docs-only PR runs only `changes`, `secrets-scan`, and `ci-required`.

**Job → condition mapping**:

| Job | Condition |
|-----|-----------|
| `confidence-gate` | `needs.changes.outputs.api == 'true' \|\| .agent == 'true' \|\| .web == 'true' \|\| .scripts == 'true' \|\| .observability == 'true' \|\| .infra == 'true'` (anything-but-docs) |
| `confidence-gate-optional` | same |
| `api-tests` | `.api == 'true' \|\| .infra == 'true'` |
| `agent-tests` | `.agent == 'true' \|\| .infra == 'true'` |
| `catalog-regression-tests` | `.api == 'true' \|\| .infra == 'true'` |
| `observability-guardrails` | `.observability == 'true' \|\| .api == 'true' \|\| .runtime == 'true' \|\| .infra == 'true'` (api included because observability stack imports api code) |
| `frontend-tests` | `.web == 'true' \|\| .infra == 'true'` |
| `lint` | always (cheap, broad coverage) |
| `script-tests` | `.scripts == 'true' \|\| .infra == 'true'` |
| `secrets-scan` | always |

**Prompt**:

> Add `if:` conditions to `.github/workflows/test.yml` jobs per the table in `tasks/ci-efficiency-plan.md` Batch 2. All jobs except `changes`, `lint`, `secrets-scan`, and `ci-required` get gated. Verify the `ci-required` aggregator still treats `skipped` as acceptable. Do not change job content otherwise. Open PR titled `ci: gate test jobs on changed paths`. In the PR description include a table of how a docs-only PR vs a web-only PR vs an api-only PR would be classified.

**Acceptance**:
- [ ] Manual test PR with only `*.md` edits → `ci-required: success`, all heavy jobs `skipped`.
- [ ] Manual test PR with only `web/src/...` edits → only `frontend-tests`, `lint`, `secrets-scan`, `confidence-gate*` run.
- [ ] Manual test PR editing `.github/workflows/test.yml` → all jobs run (because `infra` fan-out).

---

### Batch 3 — Concurrency + caching polish

**Depends on**: Batch 1 (concurrency block already added there); this batch is the cache layer
**Risk**: Low
**Estimated effort**: ~¼ day

**Goal**: Cache vitest's incremental cache and pip's installed wheels across runs. Drop `fetch-depth: 0` where unused.

**Files**:
- `.github/workflows/test.yml`

**Changes**:
1. Add `actions/cache@v4` for `web/node_modules/.vite` and `web/.vitest-cache` keyed on `package-lock.json` hash + `git rev-parse HEAD~1` for the parent commit (incremental cache).
2. Audit `fetch-depth: 0`. Required only in `secrets-scan` (gitleaks needs full history) and `changes` (paths-filter base diff). Remove elsewhere.
3. Confirm `actions/setup-python` `cache: pip` keys include both `api/requirements.txt` and `agent/requirements.txt` where relevant.

**Prompt**:

> Add an `actions/cache@v4` step to the `frontend-tests` job in `.github/workflows/test.yml` that caches `web/node_modules/.vite` and `web/.vitest-cache` keyed on `${{ runner.os }}-vitest-${{ hashFiles('web/package-lock.json') }}`. Restore-keys should fall back to `${{ runner.os }}-vitest-`. Audit every `actions/checkout@v4` step in the workflow and remove `fetch-depth: 0` unless the job runs git history operations (currently only `secrets-scan` needs full history). Title PR `ci: cache vitest incremental + shallow checkouts`.

**Acceptance**:
- [ ] Cache hit observed on second run of an unchanged web/ branch.
- [ ] Backend job clone time drops by ~5–15s.

---

### Batch 4 — Frontend test sharding

**Depends on**: Batch 1 (so `ci-required` can adapt to a matrix), Batch 2 (so frontend-tests is skippable)
**Coordination**: Confirm with the test-builder agent that the per-file invocation pattern in `.github/workflows/test.yml:556-575` isn't load-bearing for their workflow. If it is, they switch to `npx vitest run <file>` locally (which already works) and the workflow loop goes away.
**Risk**: Med — memory ceiling and flake exposure
**Estimated effort**: ~½ day

**Goal**: Replace the per-file shell loop with a vitest shard matrix. Run coverage in the same pass; merge artifacts in an aggregator job.

**Approach**:
```yaml
frontend-tests:
  if: needs.changes.outputs.web == 'true' || needs.changes.outputs.infra == 'true'
  strategy:
    fail-fast: false
    matrix: { shard: [1, 2, 3, 4] }
  steps:
    - run: cd web && NODE_OPTIONS="--max-old-space-size=6144" \
        npx vitest run --coverage --shard=${{ matrix.shard }}/4 \
        --pool=forks --poolOptions.forks.maxForks=2 --dangerouslyIgnoreUnhandledErrors
    - uses: actions/upload-artifact@v4
      with: { name: web-coverage-shard-${{ matrix.shard }}, path: web/coverage/coverage-final.json }

frontend-coverage-merge:
  needs: frontend-tests
  if: always() && needs.frontend-tests.result != 'skipped'
  steps:
    - uses: actions/download-artifact@v4
    - run: npx istanbul-merge --out web/coverage/merged.json web-coverage-shard-*/coverage-final.json
    - uses: actions/upload-artifact@v4
      with: { name: frontend-coverage, path: web/coverage/merged.json }
```

**Prompt**:

> Replace the current per-file loop in the `frontend-tests` job in `.github/workflows/test.yml:556-575` with a 4-shard matrix using `vitest --shard=N/4 --coverage`. Match the matrix pattern in `tasks/ci-efficiency-plan.md` Batch 4 verbatim. Add a `frontend-coverage-merge` job that downloads each shard's coverage artifact and produces a single `frontend-coverage` artifact via `npx istanbul-merge`. Update `ci-required.needs` to include `frontend-coverage-merge`. Before opening the PR: run the matrix locally with `npx vitest run --shard=1/4` etc. to confirm no shard OOMs or hangs. If shard 1 OOMs at `--maxForks=2`, drop to `--maxForks=1` for that shard and document why. The `--dangerouslyIgnoreUnhandledErrors` flag must be present (matches `release.yml` behavior). Open PR titled `ci: shard frontend tests across 4 runners`.

**Acceptance**:
- [ ] All 2174+ frontend tests pass across the 4 shards on first run.
- [ ] Wall-clock for `frontend-tests` drops to ≤ ⅓ of pre-batch baseline.
- [ ] `frontend-coverage` artifact contains merged data covering ≥99% of files seen pre-batch.
- [ ] Run the new layout 5× on a noop PR; no flake-induced shard failures.

**Rollback signal**: If 2+ flakes appear in the next 10 PRs, revert and investigate test ordering deps.

---

### Batch 5 — Backend test parallelization (xdist)

**Depends on**: Batch 1 (concurrency block exists)
**Risk**: Med — sqlite/`WORKSPACE` sharing under xdist
**Estimated effort**: ~½ day (mostly test isolation audit)

**Goal**: Add `pytest-xdist` to api + agent dependencies; run with `-n auto`. Pre-work: audit `api/tests/conftest.py` and `agent/tests/conftest.py` for shared filesystem / DB state.

**Pre-work checklist** (subagent runs first, posts findings as a comment before writing code):
1. Grep for literal `sqlite:///test.db` and `/tmp/archetype-test` in test fixtures. Replace with `tmp_path_factory` or `worker_id`-suffixed paths.
2. Grep for module-level state in fixtures (`@pytest.fixture(scope="module")` / `scope="session"`) that mutates shared resources.
3. Grep for `os.environ` mutations not inside `monkeypatch` — these leak across xdist workers.

**Prompt**:

> Step 1: Audit `api/tests/conftest.py` and `agent/tests/conftest.py` for shared-state hazards under `pytest-xdist`. Look for literal `sqlite:///test.db` references, shared `/tmp` paths, session-scoped mutating fixtures, and unguarded `os.environ` mutations. Post a list of findings as the first PR comment before changing test code.
>
> Step 2: Fix the hazards by switching to `tmp_path_factory` and `${worker_id}`-suffixed sqlite filenames. Add `pytest-xdist` to `api/requirements*.txt` and `agent/requirements.txt`.
>
> Step 3: Update `.github/workflows/test.yml` jobs `api-tests` and `agent-tests` to add `-n auto` to the pytest command and `coverage combine` before `--cov-report`. Update `scripts/confidence_gate_rules_ci.json` `test-api` and `test-agent` commands similarly.
>
> Step 4: Run the suite locally with `pytest -n auto -x` 3 times; report any test that passes serially but fails in parallel. Do not merge until 3 consecutive parallel runs pass.
>
> Open PR titled `ci: parallelize api+agent pytest with xdist`.

**Acceptance**:
- [ ] `pytest -n auto` passes 3× consecutively for both api and agent.
- [ ] Coverage threshold (`--cov-fail-under=55`/`50`) still enforced via `coverage combine`.
- [ ] `api-tests` wall-clock drops by ≥40%.

---

### Batch 6 — Retire confidence-gate from PR path (policy decision)

**Depends on**: Batches 1, 2, 5 complete and stable for at least 1 week
**Risk**: Med — losing a layer of defense
**Estimated effort**: ~¼ day

**Goal**: After Batch 2, `confidence-gate` and the dedicated test jobs both run on the same triggers. They overlap. Pick one source of truth.

**Recommendation**: Keep dedicated jobs (faster, cacheable, parallelizable). Remove `confidence-gate` and `confidence-gate-optional` from `test.yml`'s PR triggers; preserve them as `workflow_dispatch` + nightly `schedule` for drift detection. The `make confidence-gate` target stays available locally.

**Decision required from human**: confirm the recommendation above, or counter-propose. If confidence-gate stays, the dedicated jobs should be removed instead.

**Prompt** (only after human decision):

> Per decision in `tasks/ci-efficiency-plan.md` Batch 6: remove `confidence-gate` and `confidence-gate-optional` jobs from `.github/workflows/test.yml`'s `pull_request` and `push: main` triggers. Move them to a new `.github/workflows/confidence-gate-nightly.yml` with `schedule: cron 0 6 * * *` and `workflow_dispatch`. Update `ci-required.needs` to drop those jobs. Open PR titled `ci: move confidence-gate to nightly`.

**Acceptance**:
- [ ] Single PR run no longer shows `confidence-gate` jobs.
- [ ] Nightly run produces the same artifact (`reports/confidence-gate/latest.json`) as before.

---

## Cross-cutting validation

After **each** batch merges:

1. Watch the next 5 PRs. Note any:
   - Flake (test pass/fail oscillating with no code change)
   - Skip-when-shouldn't-have (e.g., a docs PR that touched `VERSION` and got past tests)
   - Cache miss when one was expected
2. Update this doc with `**Status**: <BATCH N COMPLETE — observations: ...>`
3. If signal is bad, open the rollback PR before starting the next batch.

## Measurement

Drop a small script in `scripts/ci_metrics.py` (Batch 1 deliverable) that runs `gh run list --workflow=test.yml --limit 50 --json` and prints:

| Metric | Pre-batch median | Post-batch median |
|--------|------------------|-------------------|
| PR wall-clock (min) | | |
| Total runner-minutes/PR | | |
| Docs-only PR wall-clock | | |
| Frontend test wall-clock | | |

Run before Batch 1 (baseline) and after each merged batch.

## Open questions

- **Q1** (RESOLVED 2026-04-25): All workflows use `ubuntu-latest`; no self-hosted runners. Sharding stays GitHub-hosted.
- **Q2** (RESOLVED 2026-04-25): No `Dockerfile.*` does `COPY *.md`. README/docs are not baked into images. `*.md` safe to classify as docs-only.
- **Q3**: Is the test-builder agent OK with the per-file loop going away in Batch 4? Coordinate before that batch starts.

Resolve Q3 before Batch 4.
