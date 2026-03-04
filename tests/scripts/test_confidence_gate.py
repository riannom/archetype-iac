from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "confidence_gate.py"
    spec = importlib.util.spec_from_file_location("confidence_gate", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_rules(module):
    rules_path = Path(__file__).resolve().parents[2] / "scripts" / "confidence_gate_rules.json"
    return module._load_rules(rules_path)


def _load_ci_rules(module):
    rules_path = Path(__file__).resolve().parents[2] / "scripts" / "confidence_gate_rules_ci.json"
    return module._load_rules(rules_path)


def test_build_plan_selects_expected_checks():
    module = _load_module()
    rules = _load_rules(module)

    plan = module.build_plan(
        ["api/app/main.py", "web/src/main.tsx", "README.md"],
        rules,
    )

    assert "test-api" in plan["selected_checks"]
    assert "test-web-container" in plan["selected_checks"]
    assert plan["unknown_code_files"] == []
    assert "api" in plan["impacted_areas"]
    assert "web" in plan["impacted_areas"]


def test_unknown_file_triggers_fallback_check():
    module = _load_module()
    rules = _load_rules(module)

    plan = module.build_plan(["custom/new_feature.go"], rules)

    assert plan["unknown_code_files"] == ["custom/new_feature.go"]
    assert "audit" in plan["selected_checks"]


def test_dry_run_confidence_penalizes_unmapped_changes():
    module = _load_module()
    rules = _load_rules(module)
    plan = module.build_plan(["custom/new_feature.go"], rules)
    check_defs = module._to_check_definitions(rules["checks"])
    results = module.run_checks(
        plan["selected_checks"],
        check_defs,
        execute=False,
    )

    confidence = module.score_confidence(plan, results, execute=False)

    assert confidence["score"] < 85
    assert confidence["level"] in {"medium", "low"}
    assert any("not mapped" in reason for reason in confidence["reasons"])


def test_run_checks_plans_without_execution():
    module = _load_module()
    rules = _load_rules(module)
    check_defs = module._to_check_definitions(rules["checks"])

    results = module.run_checks(["audit"], check_defs, execute=False)

    assert len(results) == 1
    assert results[0]["status"] == "planned"
    assert results[0]["exit_code"] is None


def test_filter_selected_checks_required_only():
    module = _load_module()
    rules = _load_ci_rules(module)
    check_defs = module._to_check_definitions(rules["checks"])
    plan = module.build_plan(
        ["api/app/main.py", "web/src/main.tsx", "agent/main.py"],
        rules,
    )

    scoped = module._filter_selected_checks(
        plan["selected_checks"],
        check_defs,
        check_scope="required-only",
    )

    assert scoped == ["test-agent-smoke", "test-api-smoke", "test-web-container"]


def test_filter_selected_checks_optional_only():
    module = _load_module()
    rules = _load_ci_rules(module)
    check_defs = module._to_check_definitions(rules["checks"])
    plan = module.build_plan(
        ["api/app/main.py", "web/src/main.tsx", "agent/main.py"],
        rules,
    )

    scoped = module._filter_selected_checks(
        plan["selected_checks"],
        check_defs,
        check_scope="optional-only",
    )

    assert scoped == ["test-agent", "test-api"]


def test_scope_filtered_report_keeps_all_selected_checks():
    module = _load_module()
    rules = _load_ci_rules(module)
    check_defs = module._to_check_definitions(rules["checks"])
    plan = module.build_plan(
        ["api/app/main.py", "web/src/main.tsx", "agent/main.py"],
        rules,
    )
    scoped = module._filter_selected_checks(
        plan["selected_checks"],
        check_defs,
        check_scope="required-only",
    )
    results = module.run_checks(scoped, check_defs, execute=False)
    report = module.assemble_output(
        base_ref="origin/main",
        change_source="explicit --files input",
        rules_path=Path(__file__).resolve().parents[2] / "scripts" / "confidence_gate_rules_ci.json",
        plan=plan,
        check_defs=check_defs,
        check_scope="required-only",
        scoped_check_ids=scoped,
        results=results,
        execute=False,
    )

    assert report["check_scope"] == "required-only"
    assert [item["check_id"] for item in report["selected_checks"]] == [
        "test-agent-smoke",
        "test-api-smoke",
        "test-web-container",
    ]
    assert [item["check_id"] for item in report["selected_checks_all"]] == [
        "test-agent",
        "test-agent-smoke",
        "test-api",
        "test-api-smoke",
        "test-web-container",
    ]


def test_ci_catalog_rule_selects_catalog_regression_check():
    module = _load_module()
    rules = _load_ci_rules(module)

    plan = module.build_plan(
        ["api/app/services/catalog_query.py"],
        rules,
    )

    assert "test-api-catalog-regression" in plan["selected_checks"]


def test_local_catalog_rule_selects_catalog_regression_check():
    module = _load_module()
    rules = _load_rules(module)

    plan = module.build_plan(
        ["api/app/image_store/manifest.py"],
        rules,
    )

    assert "test-api-catalog-regression" in plan["selected_checks"]


def test_min_score_exit_code_for_dry_run():
    module = _load_module()

    code = module.main(
        [
            "--files",
            "api/app/main.py",
            "--min-score",
            "95",
        ]
    )

    assert code == 1
