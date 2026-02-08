#!/usr/bin/env python3
"""Check for sync-blocking patterns inside async functions.

This script detects common patterns that block the event loop when used
inside async functions:
- socket.gethostbyname (use asyncio.getaddrinfo instead)
- subprocess.run / subprocess.Popen / subprocess.call (use asyncio.create_subprocess_exec)
- time.sleep (use asyncio.sleep)
- requests.get/post/put/delete/patch (use httpx.AsyncClient)
- SessionLocal() direct usage (use get_db() or get_session())

Usage:
    python scripts/check_async_safety.py [directory]
    python scripts/check_async_safety.py api/app
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


# Patterns to detect inside async functions
BLOCKING_PATTERNS = {
    # (module_or_attr, function_name, suggested_replacement)
    "socket.gethostbyname": "asyncio.getaddrinfo()",
    "subprocess.run": "asyncio.create_subprocess_exec()",
    "subprocess.Popen": "asyncio.create_subprocess_exec()",
    "subprocess.call": "asyncio.create_subprocess_exec()",
    "time.sleep": "asyncio.sleep()",
    "requests.get": "httpx.AsyncClient",
    "requests.post": "httpx.AsyncClient",
    "requests.put": "httpx.AsyncClient",
    "requests.delete": "httpx.AsyncClient",
    "requests.patch": "httpx.AsyncClient",
}

# Direct name patterns (no module qualifier)
DIRECT_NAME_PATTERNS = {
    "SessionLocal": "get_db() or get_session()",
}


class AsyncSafetyChecker(ast.NodeVisitor):
    """AST visitor that detects blocking calls inside async functions."""

    def __init__(self, filename: str):
        self.filename = filename
        self.violations: list[tuple[int, str, str]] = []
        self._in_async = False

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        old = self._in_async
        self._in_async = True
        self.generic_visit(node)
        self._in_async = old

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old = self._in_async
        self._in_async = False
        self.generic_visit(node)
        self._in_async = old

    def visit_Call(self, node: ast.Call):
        if self._in_async:
            call_name = self._get_call_name(node)
            if call_name:
                # Check qualified patterns (module.function)
                for pattern, replacement in BLOCKING_PATTERNS.items():
                    if call_name.endswith(pattern):
                        self.violations.append((
                            node.lineno,
                            call_name,
                            replacement,
                        ))
                        break

                # Check direct name patterns
                for pattern, replacement in DIRECT_NAME_PATTERNS.items():
                    if call_name == pattern or call_name.endswith(f".{pattern}"):
                        self.violations.append((
                            node.lineno,
                            call_name,
                            replacement,
                        ))
                        break

        self.generic_visit(node)

    def _get_call_name(self, node: ast.Call) -> str | None:
        """Extract the dotted name from a call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return None


def check_file(filepath: Path) -> list[tuple[str, int, str, str]]:
    """Check a single Python file for async safety violations."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return []

    checker = AsyncSafetyChecker(str(filepath))
    checker.visit(tree)

    return [
        (str(filepath), line, call, replacement)
        for line, call, replacement in checker.violations
    ]


def main():
    search_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("api/app")

    if not search_dir.exists():
        print(f"Error: directory {search_dir} not found", file=sys.stderr)
        sys.exit(1)

    all_violations = []
    for py_file in sorted(search_dir.rglob("*.py")):
        # Skip test files
        if "/tests/" in str(py_file) or py_file.name.startswith("test_"):
            continue
        all_violations.extend(check_file(py_file))

    if all_violations:
        print(f"\n{'='*70}")
        print(f"ASYNC SAFETY VIOLATIONS: {len(all_violations)} found")
        print(f"{'='*70}\n")
        for filepath, line, call, replacement in all_violations:
            print(f"  {filepath}:{line}")
            print(f"    Blocking call: {call}")
            print(f"    Use instead:   {replacement}")
            print()
        sys.exit(1)
    else:
        print("No async safety violations found.")
        sys.exit(0)


if __name__ == "__main__":
    main()
