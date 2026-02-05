# Repository Guidelines

## Project Structure & Module Organization
- Current repository contents are minimal; no source tree is present yet.
- When adding code, prefer a clear split such as `src/` for application code, `tests/` for automated tests, and `assets/` for static files.
- Keep top-level clutter low; group related modules under feature folders (for example, `src/auth/`, `src/api/`).

## Build, Test, and Development Commands
- No build or test commands are defined in this repository yet.
- Add a single source of truth for developer commands (for example, a `Makefile`, `package.json` scripts, or `scripts/` directory) and document it here.
- Example pattern once tooling exists:
  - `make build` — produce production artifacts.
  - `make test` — run the full automated test suite.
  - `make dev` — start a local development server.
  - `python3 scripts/coverage_map.py` — generate test-to-source coverage mapping and gaps report (outputs under `reports/`).

## Coding Style & Naming Conventions
- Use consistent indentation (2 or 4 spaces) and enforce it with a formatter once a language is chosen.
- Name files and folders with lowercase and dashes (`kebab-case`) unless the chosen language prefers another convention.
- Add a formatter and linter early (for example, `prettier`, `eslint`, `ruff`, `gofmt`) and document the exact commands.

## Testing Guidelines
- No testing framework is configured yet.
- When tests are added, keep them colocated in `tests/` or alongside modules (for example, `src/foo.test.ts`).
- Name tests with a clear suffix (`*.test.*` or `*_test.*`) and ensure tests are runnable via a single command.

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
