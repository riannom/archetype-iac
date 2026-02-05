#!/usr/bin/env python3
import json
import os
import re


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPORTS_DIR = os.path.join(ROOT, "reports")

TARGETS = [
    ("api", "api/app", "api/tests", {".py"}),
    ("agent", "agent", "agent/tests", {".py"}),
    ("web", "web/src", "web/src", {".ts", ".tsx"}),
]

IMPORT_RE = re.compile(r"^(?:from|import)\s+([a-zA-Z0-9_\.]+)")
REQ_RE = re.compile(r"require\(['\"]([^'\"]+)['\"]\)")
ESM_RE = re.compile(r"from\s+['\"]([^'\"]+)['\"]")


def list_files(base, exts):
    files = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [
            d
            for d in dirnames
            if d
            not in {
                "node_modules",
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".venv",
                "dist",
                "build",
            }
        ]
        for filename in filenames:
            if exts is None or os.path.splitext(filename)[1] in exts:
                files.append(os.path.join(dirpath, filename))
    return files


def rel(path):
    return os.path.relpath(path, ROOT)


def is_test_file(path):
    name = os.path.basename(path)
    return "test" in name or "spec" in name


def scan_imports_py(path):
    imports = set()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                match = IMPORT_RE.match(line.strip())
                if match:
                    imports.add(match.group(1))
    except OSError:
        pass
    return imports


def scan_imports_ts(path):
    imports = set()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
        for match in REQ_RE.finditer(text):
            imports.add(match.group(1))
        for match in ESM_RE.finditer(text):
            imports.add(match.group(1))
    except OSError:
        pass
    return imports


def module_to_path_py(mod, base_dir):
    rel_path = mod.replace(".", "/")
    candidates = [
        os.path.join(base_dir, rel_path + ".py"),
        os.path.join(base_dir, rel_path, "__init__.py"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def module_to_path_ts(mod, base_dir):
    if not mod.startswith("."):
        return None
    base = os.path.normpath(os.path.join(base_dir, mod))
    candidates = [
        base + ".ts",
        base + ".tsx",
        os.path.join(base, "index.ts"),
        os.path.join(base, "index.tsx"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def build_report():
    report = {}
    for name, src_base, tests_base, exts in TARGETS:
        src_files = [
            path
            for path in list_files(os.path.join(ROOT, src_base), exts)
            if not is_test_file(path)
        ]
        test_files = [
            path
            for path in list_files(os.path.join(ROOT, tests_base), exts)
            if is_test_file(path)
        ]

        covered = set()
        test_map = {}

        for test_file in test_files:
            targets = set()
            if name in {"api", "agent"}:
                imports = scan_imports_py(test_file)
                for mod in imports:
                    if mod.startswith("app.") and name == "api":
                        path = module_to_path_py(
                            mod.replace("app.", ""), os.path.join(ROOT, src_base)
                        )
                    elif mod.startswith("agent.") and name == "agent":
                        path = module_to_path_py(
                            mod.replace("agent.", ""), os.path.join(ROOT, src_base)
                        )
                    elif mod.startswith(src_base.replace("/", ".")):
                        path = module_to_path_py(
                            mod.replace(src_base.replace("/", ".") + ".", ""),
                            os.path.join(ROOT, src_base),
                        )
                    else:
                        path = None
                    if path:
                        targets.add(rel(path))
            else:
                imports = scan_imports_ts(test_file)
                for mod in imports:
                    if mod.startswith("."):
                        base_dir = os.path.dirname(test_file)
                        path = module_to_path_ts(mod, base_dir)
                    elif mod.startswith("@/"):
                        rel_mod = mod.replace("@/", "")
                        path = None
                        for ext in [".ts", ".tsx"]:
                            candidate = os.path.join(ROOT, src_base, rel_mod + ext)
                            if os.path.exists(candidate):
                                path = candidate
                                break
                        if not path:
                            for idx in ["index.ts", "index.tsx"]:
                                candidate = os.path.join(ROOT, src_base, rel_mod, idx)
                                if os.path.exists(candidate):
                                    path = candidate
                                    break
                    else:
                        path = None
                    if path:
                        targets.add(rel(path))

            if targets:
                test_map[rel(test_file)] = sorted(targets)
                covered.update(targets)

        src_rel = set(rel(path) for path in src_files)
        covered_rel = set(rel(path) for path in covered)
        uncovered = sorted(src_rel - covered_rel)

        report[name] = {
            "source_files": sorted(src_rel),
            "test_files": sorted(rel(path) for path in test_files),
            "test_map": test_map,
            "covered_sources": sorted(covered_rel),
            "uncovered_sources": uncovered,
            "counts": {
                "source": len(src_rel),
                "covered": len(covered_rel),
                "uncovered": len(uncovered),
            },
        }

    return report


def write_reports(report):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    json_path = os.path.join(REPORTS_DIR, "test-coverage-map.json")
    md_path = os.path.join(REPORTS_DIR, "test-coverage-gaps.md")

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    lines = [
        "# Test Coverage Gap Report",
        "",
        "This report maps tests to directly imported source files and lists uncovered files.",
        "",
    ]

    for name in ["api", "agent", "web"]:
        data = report[name]
        lines.append(f"## {name}")
        lines.append("")
        lines.append(
            f"Source files: {data['counts']['source']}. Covered: {data['counts']['covered']}. Uncovered: {data['counts']['uncovered']}."
        )
        lines.append("")
        lines.append("Uncovered files:")
        lines.extend([f"- `{path}`" for path in data["uncovered_sources"]])
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    return json_path, md_path


def main():
    report = build_report()
    json_path, md_path = write_reports(report)
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")


if __name__ == "__main__":
    main()
