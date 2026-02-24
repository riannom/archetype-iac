#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = REPO_ROOT / "scripts" / "confidence_gate_rules.json"

CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".json",
    ".mjs",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}

DOC_EXTENSIONS = {".md", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg"}
CODE_FILENAMES = {"Makefile"}


@dataclass(frozen=True)
class CheckDefinition:
    check_id: str
    description: str
    command: list[str]
    required: bool = True


def _git_list_changed(base_ref: str) -> tuple[list[str], str]:
    primary = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if primary.returncode == 0:
        files = _normalize_file_list(primary.stdout.splitlines())
        return files, f"git diff --name-only {base_ref}...HEAD"

    staged = subprocess.run(
        ["git", "diff", "--name-only", "--cached"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    working = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    merged = staged.stdout.splitlines() + working.stdout.splitlines()
    files = _normalize_file_list(merged)
    return files, "git diff --name-only (--cached + working-tree)"


def _normalize_file_list(files: list[str]) -> list[str]:
    cleaned = []
    seen = set()
    for item in files:
        path = item.strip().replace("\\", "/")
        if not path or path in seen:
            continue
        seen.add(path)
        cleaned.append(path)
    return sorted(cleaned)


def _load_rules(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    checks = payload.get("checks", {})
    if not isinstance(checks, dict) or not checks:
        raise ValueError("rules file must define a non-empty checks object")

    rules = payload.get("rules", [])
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")

    check_ids = set(checks.keys())
    for rule in rules:
        for check in rule.get("checks", []):
            if check not in check_ids:
                raise ValueError(
                    f"rule {rule.get('name', '<unnamed>')} references unknown check {check}"
                )

    for check in payload.get("default_checks", []):
        if check not in check_ids:
            raise ValueError(f"default_checks references unknown check {check}")
    for check in payload.get("fallback_checks", []):
        if check not in check_ids:
            raise ValueError(f"fallback_checks references unknown check {check}")

    return payload


def _to_check_definitions(raw: dict[str, Any]) -> dict[str, CheckDefinition]:
    out = {}
    for check_id, spec in raw.items():
        command = spec.get("command")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) for part in command
        ):
            raise ValueError(f"check {check_id} has an invalid command")
        out[check_id] = CheckDefinition(
            check_id=check_id,
            description=str(spec.get("description", check_id)),
            command=command,
            required=bool(spec.get("required", True)),
        )
    return out


def _matches_any(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in globs)


def _is_doc_file(path: str, doc_globs: list[str]) -> bool:
    if _matches_any(path, doc_globs):
        return True
    extension = Path(path).suffix.lower()
    return extension in DOC_EXTENSIONS


def _is_code_like(path: str, doc_globs: list[str]) -> bool:
    if _is_doc_file(path, doc_globs):
        return False
    p = Path(path)
    if p.name in CODE_FILENAMES:
        return True
    if p.name.startswith("Dockerfile"):
        return True
    if p.name.endswith(".test.ts") or p.name.endswith(".test.tsx"):
        return True
    return p.suffix.lower() in CODE_EXTENSIONS


def build_plan(changed_files: list[str], rules_data: dict[str, Any]) -> dict[str, Any]:
    rules = rules_data.get("rules", [])
    doc_globs = list(rules_data.get("doc_globs", []))

    file_rule_matches: dict[str, list[str]] = {}
    file_check_matches: dict[str, list[str]] = {}
    impacted_areas: set[str] = set()
    selected_checks: set[str] = set(rules_data.get("default_checks", []))
    code_files: list[str] = []
    unknown_code_files: list[str] = []
    matched_code_files: set[str] = set()

    for path in changed_files:
        matched_rules = []
        matched_checks = set()
        is_code = _is_code_like(path, doc_globs)
        if is_code:
            code_files.append(path)

        for rule in rules:
            if _matches_any(path, rule.get("globs", [])):
                name = str(rule.get("name", "<unnamed-rule>"))
                matched_rules.append(name)
                for check in rule.get("checks", []):
                    matched_checks.add(check)
                    selected_checks.add(check)
                for area in rule.get("areas", []):
                    impacted_areas.add(area)

        if matched_rules:
            file_rule_matches[path] = sorted(set(matched_rules))
        if matched_checks:
            file_check_matches[path] = sorted(matched_checks)
        if is_code and matched_rules:
            matched_code_files.add(path)
        if is_code and not matched_rules:
            unknown_code_files.append(path)

    if unknown_code_files:
        selected_checks.update(rules_data.get("fallback_checks", []))

    return {
        "changed_files": changed_files,
        "code_files": sorted(code_files),
        "file_rule_matches": file_rule_matches,
        "file_check_matches": file_check_matches,
        "matched_code_files": sorted(matched_code_files),
        "unknown_code_files": sorted(unknown_code_files),
        "impacted_areas": sorted(impacted_areas),
        "selected_checks": sorted(selected_checks),
    }


def _tail_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


def run_checks(
    selected_checks: list[str],
    check_defs: dict[str, CheckDefinition],
    *,
    execute: bool,
    max_output_lines: int = 60,
) -> list[dict[str, Any]]:
    results = []
    for check_id in selected_checks:
        check = check_defs[check_id]
        result: dict[str, Any] = {
            "check_id": check.check_id,
            "description": check.description,
            "command": check.command,
            "required": check.required,
            "status": "planned",
            "exit_code": None,
            "duration_seconds": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
        }
        if not execute:
            results.append(result)
            continue

        start = time.perf_counter()
        completed = subprocess.run(
            check.command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        duration = time.perf_counter() - start
        result["duration_seconds"] = round(duration, 3)
        result["exit_code"] = completed.returncode
        result["stdout_tail"] = _tail_lines(completed.stdout, max_output_lines)
        result["stderr_tail"] = _tail_lines(completed.stderr, max_output_lines)
        result["status"] = "passed" if completed.returncode == 0 else "failed"
        results.append(result)

    return results


def score_confidence(plan: dict[str, Any], results: list[dict[str, Any]], execute: bool) -> dict[str, Any]:
    score = 100
    reasons = []

    unknown = len(plan["unknown_code_files"])
    if unknown:
        penalty = min(35, unknown * 10)
        score -= penalty
        reasons.append(
            f"{unknown} changed code/config file(s) are not mapped to a rule (-{penalty})."
        )

    total_code = len(plan["code_files"])
    matched_code = len(plan["matched_code_files"])
    if total_code > 0 and matched_code < total_code:
        gap = total_code - matched_code
        penalty = min(20, gap * 5)
        score -= penalty
        reasons.append(
            f"Rule coverage misses {gap} of {total_code} changed code/config file(s) (-{penalty})."
        )

    if total_code > 0 and not plan["selected_checks"]:
        score -= 40
        reasons.append("No checks selected for changed code/config files (-40).")

    if not execute and plan["selected_checks"]:
        score -= 10
        reasons.append("Dry-run only; selected checks were not executed (-10).")

    if execute:
        failed_required = [
            item
            for item in results
            if item["status"] == "failed" and bool(item.get("required"))
        ]
        failed_optional = [
            item
            for item in results
            if item["status"] == "failed" and not bool(item.get("required"))
        ]
        if failed_required:
            penalty = min(60, 30 * len(failed_required))
            score -= penalty
            reasons.append(
                f"{len(failed_required)} required check(s) failed (-{penalty})."
            )
        if failed_optional:
            penalty = min(15, 5 * len(failed_optional))
            score -= penalty
            reasons.append(
                f"{len(failed_optional)} optional check(s) failed (-{penalty})."
            )

    score = max(0, min(100, score))
    level = "high" if score >= 85 else "medium" if score >= 65 else "low"

    if not reasons:
        reasons.append("All changed code/config files were mapped and all checks passed.")

    return {"score": score, "level": level, "reasons": reasons}


def build_remediation(plan: dict[str, Any], results: list[dict[str, Any]], execute: bool) -> list[str]:
    steps = []
    if plan["unknown_code_files"]:
        steps.append(
            "Add rules for unmatched code/config paths in scripts/confidence_gate_rules.json."
        )
    if not execute and plan["selected_checks"]:
        steps.append("Run `make confidence-gate-run` to execute the selected checks.")
    failed = [item for item in results if item["status"] == "failed"]
    if failed:
        for item in failed:
            command = " ".join(item["command"])
            steps.append(f"Fix and rerun `{item['check_id']}` using `{command}`.")
    return steps


def assemble_output(
    *,
    base_ref: str,
    change_source: str,
    rules_path: Path,
    plan: dict[str, Any],
    check_defs: dict[str, CheckDefinition],
    results: list[dict[str, Any]],
    execute: bool,
) -> dict[str, Any]:
    confidence = score_confidence(plan, results, execute)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "run" if execute else "dry-run",
        "base_ref": base_ref,
        "change_source": change_source,
        "rules_path": str(rules_path.relative_to(REPO_ROOT)),
        "changed_files": plan["changed_files"],
        "code_files": plan["code_files"],
        "matched_code_files": plan["matched_code_files"],
        "unknown_code_files": plan["unknown_code_files"],
        "impacted_areas": plan["impacted_areas"],
        "file_rule_matches": plan["file_rule_matches"],
        "selected_checks": [
            {
                "check_id": check_id,
                "description": check_defs[check_id].description,
                "command": check_defs[check_id].command,
                "required": check_defs[check_id].required,
            }
            for check_id in plan["selected_checks"]
        ],
        "check_results": results,
        "confidence": confidence,
        "remediation": build_remediation(plan, results, execute),
    }
    return payload


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Confidence Gate",
        f"Mode: {report['mode']}",
        f"Change source: {report['change_source']}",
        f"Changed files: {len(report['changed_files'])} (code/config: {len(report['code_files'])})",
    ]

    if report["impacted_areas"]:
        lines.append(f"Impacted areas: {', '.join(report['impacted_areas'])}")
    else:
        lines.append("Impacted areas: none")

    lines.append("Selected checks:")
    if not report["selected_checks"]:
        lines.append("- none")
    for item in report["selected_checks"]:
        required = "required" if item["required"] else "optional"
        lines.append(
            f"- {item['check_id']} [{required}] -> {' '.join(item['command'])}"
        )

    if report["unknown_code_files"]:
        lines.append("Unmatched code/config files:")
        for path in report["unknown_code_files"]:
            lines.append(f"- {path}")

    if report["check_results"]:
        lines.append("Check results:")
        for item in report["check_results"]:
            suffix = ""
            if item["exit_code"] is not None:
                suffix = f" (exit={item['exit_code']}, {item['duration_seconds']}s)"
            lines.append(f"- {item['check_id']}: {item['status']}{suffix}")

    confidence = report["confidence"]
    lines.append(
        f"Confidence: {confidence['score']} ({confidence['level']})"
    )
    lines.append("Reasons:")
    for reason in confidence["reasons"]:
        lines.append(f"- {reason}")

    if report["remediation"]:
        lines.append("Remediation:")
        for step in report["remediation"]:
            lines.append(f"- {step}")

    return "\n".join(lines)


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Select and optionally execute the minimal relevant build/test checks "
            "for changed files."
        )
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="Git base ref used for change detection when --files is not supplied.",
    )
    parser.add_argument(
        "--files",
        nargs="+",
        help="Explicit changed file paths. Skips git diff detection.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute selected checks instead of only planning them.",
    )
    parser.add_argument(
        "--rules",
        default=str(DEFAULT_RULES_PATH),
        help="Path to confidence gate rule JSON.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output.",
    )
    parser.add_argument(
        "--report-path",
        help="Write the final JSON report to this path.",
    )
    parser.add_argument(
        "--max-output-lines",
        type=int,
        default=60,
        help="Maximum number of stdout/stderr lines to retain per executed check.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=0,
        help="Minimum confidence score required for a successful exit status.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    rules_path = Path(args.rules)
    if not rules_path.is_absolute():
        rules_path = REPO_ROOT / rules_path

    try:
        rules_data = _load_rules(rules_path)
        check_defs = _to_check_definitions(rules_data["checks"])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"confidence-gate error: {exc}", file=sys.stderr)
        return 2

    if args.files:
        changed_files = _normalize_file_list(args.files)
        change_source = "explicit --files input"
    else:
        changed_files, change_source = _git_list_changed(args.base)

    plan = build_plan(changed_files, rules_data)
    results = run_checks(
        plan["selected_checks"],
        check_defs,
        execute=args.run,
        max_output_lines=max(1, args.max_output_lines),
    )
    report = assemble_output(
        base_ref=args.base,
        change_source=change_source,
        rules_path=rules_path,
        plan=plan,
        check_defs=check_defs,
        results=results,
        execute=args.run,
    )

    if args.report_path:
        report_path = Path(args.report_path)
        if not report_path.is_absolute():
            report_path = REPO_ROOT / report_path
        _write_report(report_path, report)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
        if args.run:
            failed_required = [
                item
                for item in report["check_results"]
                if item["status"] == "failed" and item["required"]
            ]
            for item in failed_required:
                if item["stdout_tail"]:
                    print(
                        f"\n[{item['check_id']}] stdout (tail):\n{item['stdout_tail']}"
                    )
                if item["stderr_tail"]:
                    print(
                        f"\n[{item['check_id']}] stderr (tail):\n{item['stderr_tail']}"
                    )

    below_min_score = report["confidence"]["score"] < args.min_score
    has_required_failures = any(
        item["status"] == "failed" and item["required"]
        for item in report["check_results"]
    )

    if has_required_failures or below_min_score:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
