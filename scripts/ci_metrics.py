#!/usr/bin/env python3
"""Compute CI wall-clock metrics for the Tests workflow over the last 30 days.

Pulls successful runs of `.github/workflows/test.yml` via the `gh` CLI, classifies
each run by which area(s) of the repo it touched (mirroring the path filters in
the workflow definition), and prints a markdown table of count / median / p95 /
min / max wall-clock per bucket.

Buckets: docs-only, web-only, api-only, agent-only, cross-cutting, other.

Usage:
    python3 scripts/ci_metrics.py            # last 30 days, last 200 runs
    python3 scripts/ci_metrics.py --limit 50 # tighter window

Requires: `gh` CLI authenticated for the current repo. Stdlib only otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

WORKFLOW = "test.yml"
DEFAULT_LIMIT = 200
DEFAULT_WINDOW_DAYS = 30

# Mirror of dorny/paths-filter rules in .github/workflows/test.yml. Each entry
# is (bucket_label, list_of_path_predicates). Predicates are simple prefix or
# glob-ish checks evaluated in order.
PATH_RULES: list[tuple[str, list[str]]] = [
    ("api", [
        "api/",
        "Dockerfile.api",
    ]),
    ("agent", [
        "agent/",
        "Dockerfile.agent",
    ]),
    ("web", [
        "web/",
        "Dockerfile.web",
    ]),
    ("scripts", [
        "scripts/",
        "tests/scripts/",
        "Makefile",
        "install-controller.sh",
        # also matches install*.sh at repo root
    ]),
    ("observability", [
        "grafana/",
        "prometheus",       # matches prometheus*.yml
        "promtail-config.yml",
        "scripts/observability_",
        "scripts/support_bundle",
    ]),
    ("runtime", [
        "docker-compose",
        "Dockerfile.",
    ]),
    ("infra", [
        ".github/workflows/",
        "scripts/confidence_gate",
        "pyproject.toml",
        "pytest.ini",
        "VERSION",
    ]),
]

DOCS_ONLY_EXCLUDE = re.compile(r"(\.md$|^assets/|^reports/|^screenshots/|^tasks/)")


@dataclass
class Run:
    db_id: int
    head_sha: str
    head_branch: str
    event: str
    created_at: datetime
    updated_at: datetime
    files: list[str] = field(default_factory=list)

    @property
    def wall_seconds(self) -> float:
        return (self.updated_at - self.created_at).total_seconds()


def _run(cmd: list[str], *, check: bool = True) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        sys.stderr.write(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}\n"
        )
        raise SystemExit(proc.returncode)
    return proc.stdout


def _parse_iso(value: str) -> datetime:
    # gh returns 2026-04-27T05:00:53Z
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_runs(limit: int) -> list[Run]:
    raw = _run(
        [
            "gh", "run", "list",
            f"--workflow={WORKFLOW}",
            f"--limit={limit}",
            "--status=success",
            "--json", "databaseId,headBranch,conclusion,createdAt,updatedAt,headSha,event",
        ]
    )
    data = json.loads(raw)
    runs: list[Run] = []
    for item in data:
        runs.append(Run(
            db_id=int(item["databaseId"]),
            head_sha=item["headSha"],
            head_branch=item.get("headBranch") or "",
            event=item.get("event") or "",
            created_at=_parse_iso(item["createdAt"]),
            updated_at=_parse_iso(item["updatedAt"]),
        ))
    return runs


def _repo_slug() -> str:
    out = _run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()
    return out


def _files_for_sha_local(sha: str) -> list[str] | None:
    """Diff a single commit (sha^!) against its parent locally. Returns None if sha unknown."""
    exists = subprocess.run(
        ["git", "cat-file", "-e", sha], capture_output=True, text=True
    )
    if exists.returncode != 0:
        return None
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{sha}^!"], capture_output=True, text=True
    )
    if out.returncode != 0:
        # e.g., sha is a root commit; fall back to listing all files via ls-tree
        ls = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", sha], capture_output=True, text=True
        )
        if ls.returncode != 0:
            return None
        return [line for line in ls.stdout.splitlines() if line]
    return [line for line in out.stdout.splitlines() if line]


def _files_for_sha_remote(slug: str, sha: str) -> list[str]:
    raw = _run(
        ["gh", "api", f"repos/{slug}/commits/{sha}", "--jq", ".files[].filename"],
        check=False,
    )
    return [line for line in raw.splitlines() if line]


def populate_files(runs: Iterable[Run], slug: str) -> None:
    for run in runs:
        local = _files_for_sha_local(run.head_sha)
        if local is not None:
            run.files = local
        else:
            run.files = _files_for_sha_remote(slug, run.head_sha)


def _matches_rule(path: str, predicates: list[str]) -> bool:
    for pred in predicates:
        if pred.endswith("/"):
            if path.startswith(pred):
                return True
        elif "/" in pred:
            if path.startswith(pred):
                return True
        else:
            # bare token: prefix match against basename or any path segment start.
            # Cheap approximation: substring match. Sufficient for the
            # well-defined tokens in PATH_RULES (Dockerfile., prometheus, etc.).
            if pred in path:
                return True
    return False


def _is_docs_only(files: list[str]) -> bool:
    if not files:
        return False
    for f in files:
        if not DOCS_ONLY_EXCLUDE.search(f):
            return False
    return True


def classify(files: list[str]) -> str:
    """Map the file list to one of the report buckets."""
    if not files:
        # No files known (e.g., remote fetch failed and no local commit).
        return "other"

    if _is_docs_only(files):
        return "docs-only"

    areas: set[str] = set()
    for path in files:
        for label, preds in PATH_RULES:
            if _matches_rule(path, preds):
                areas.add(label)
                break  # one bucket per file

    # Roll up: api/agent/web get their own dedicated buckets only if exclusive.
    backend_areas = {"api", "agent", "web"}
    backend_hit = areas & backend_areas
    other_hit = areas - backend_areas

    if len(backend_hit) == 1 and not other_hit:
        sole = next(iter(backend_hit))
        return f"{sole}-only"

    if not backend_hit and not other_hit:
        # Files matched no rule (e.g., only top-level README touched but isn't
        # docs-only because of some non-doc sibling). Bucket as "other".
        return "other"

    if not backend_hit and other_hit:
        # Touches scripts/observability/runtime/infra but no api/agent/web.
        # These trigger the confidence-gate / scripts paths but skip core test
        # shards, so call them "other" rather than "cross-cutting".
        return "other"

    # Multiple backends touched, or backend + infra/scripts/etc. -> cross-cutting.
    return "cross-cutting"


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 0:
        seconds = 0
    if seconds < 3600:
        return f"{seconds // 60}:{seconds % 60:02d}"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    # Nearest-rank percentile, 1-indexed.
    rank = max(1, int(round(pct / 100.0 * len(ordered))))
    rank = min(rank, len(ordered))
    return ordered[rank - 1]


BUCKET_ORDER = ["docs-only", "web-only", "api-only", "agent-only", "cross-cutting", "other"]


def render_table(runs: list[Run]) -> str:
    by_bucket: dict[str, list[float]] = {b: [] for b in BUCKET_ORDER}
    for r in runs:
        bucket = classify(r.files)
        by_bucket.setdefault(bucket, []).append(r.wall_seconds)

    lines: list[str] = []
    lines.append(f"## CI metrics — last {DEFAULT_WINDOW_DAYS} days ({len(runs)}-run window)")
    lines.append("")
    lines.append("| Bucket          | Runs | Median  | P95     | Min     | Max     |")
    lines.append("|-----------------|------|---------|---------|---------|---------|")
    for bucket in BUCKET_ORDER:
        vals = by_bucket.get(bucket, [])
        if not vals:
            lines.append(f"| {bucket:<15} | {0:>4} | {'-':>7} | {'-':>7} | {'-':>7} | {'-':>7} |")
            continue
        med = statistics.median(vals)
        p95 = _percentile(vals, 95.0)
        lines.append(
            f"| {bucket:<15} | {len(vals):>4} | "
            f"{_fmt_duration(med):>7} | {_fmt_duration(p95):>7} | "
            f"{_fmt_duration(min(vals)):>7} | {_fmt_duration(max(vals)):>7} |"
        )

    # Surface any unrecognised bucket labels (defensive — classify only emits
    # values from BUCKET_ORDER today, but make regressions visible).
    extra = [b for b in by_bucket if b not in BUCKET_ORDER and by_bucket[b]]
    if extra:
        lines.append("")
        lines.append("Unexpected buckets observed: " + ", ".join(sorted(extra)))

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"max runs to fetch (default: {DEFAULT_LIMIT})")
    parser.add_argument("--days", type=int, default=DEFAULT_WINDOW_DAYS,
                        help=f"window in days (default: {DEFAULT_WINDOW_DAYS})")
    args = parser.parse_args(argv)

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    runs = fetch_runs(args.limit)
    runs = [r for r in runs if r.created_at >= cutoff]
    if not runs:
        print(f"No successful runs found in the last {args.days} days.")
        return 0

    slug = _repo_slug()
    populate_files(runs, slug)

    print(render_table(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
