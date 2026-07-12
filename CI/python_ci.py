from __future__ import annotations

import argparse
import time
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from CI.python_checks.common import (
    configure_console_encoding,
    ensure_target_dirs,
    iter_compile_targets,
    iter_module_names,
    iter_python_files,
    print_issues,
    print_scan_targets,
    run_compile_checks,
    run_module_import_checks,
    run_ty_check,
    run_ruff_check,
    run_type_ignore_check,
    run_unicode_escape_check,
    run_unit_tests,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check syntax, static imports, and startup-chain issues in survey_submitter."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Enable module import checks. The default is quick mode.",
    )
    ty_group = parser.add_mutually_exclusive_group()
    ty_group.add_argument(
        "--ty",
        dest="ty_enabled",
        action="store_true",
        help="Enable ty diagnostics (enabled by default).",
    )
    ty_group.add_argument(
        "--no-ty",
        dest="ty_enabled",
        action="store_false",
        help="Disable ty diagnostics and keep compile and Ruff checks only.",
    )
    parser.set_defaults(ty_enabled=True)
    return parser.parse_args()


def main() -> int:
    configure_console_encoding()
    args = parse_args()
    start_time = time.perf_counter()

    target_dirs = ensure_target_dirs()
    python_files = iter_python_files()
    compile_targets = iter_compile_targets()
    modules = iter_module_names(python_files)
    quick_mode = not args.full
    ty_mode = args.ty_enabled

    print_scan_targets(target_dirs)
    print(f"[INFO] Check mode: {'quick' if quick_mode else 'full'}")
    print(f"[INFO] Python files: {len(python_files)}")
    print(f"[INFO] Compile targets: {len(compile_targets)}")
    if ty_mode:
        print("[INFO] ty diagnostics: enabled")
    else:
        print("[INFO] ty diagnostics: disabled (--no-ty)")
    print("[INFO] Unit tests: enabled")
    if quick_mode:
        print("[INFO] Module import checks: skipped (use --full to enable)")
    else:
        print(f"[INFO] Module import checks: {len(modules)}")

    compile_issues = run_compile_checks(compile_targets)
    ruff_issues, ruff_error = run_ruff_check(target_dirs)
    type_ignore_issues = run_type_ignore_check(target_dirs)
    unicode_escape_issues = run_unicode_escape_check([ROOT_DIR / "CI"])
    ty_issues, ty_error = run_ty_check(target_dirs) if ty_mode else ([], None)
    unit_test_issue, coverage_summary = run_unit_tests()
    import_issues = run_module_import_checks(modules) if args.full else []

    if ruff_error:
        print(f"[ERROR] {ruff_error}")
        return 2
    if ty_error:
        print(f"[ERROR] {ty_error}")
        return 2

    total_issues = (
        len(compile_issues)
        + len(ruff_issues)
        + len(type_ignore_issues)
        + len(unicode_escape_issues)
        + len(ty_issues)
        + (1 if unit_test_issue else 0)
        + len(import_issues)
    )
    elapsed = time.perf_counter() - start_time

    print(f"[INFO] Compile issues: {len(compile_issues)}")
    print(f"[INFO] Ruff diagnostics: {len(ruff_issues)}")
    print(f"[INFO] Type ignore diagnostics: {len(type_ignore_issues)}")
    print(f"[INFO] Unicode escape diagnostics: {len(unicode_escape_issues)}")
    if ty_mode:
        print(f"[INFO] ty diagnostics: {len(ty_issues)}")
    print(f"[INFO] Unit test failures: {1 if unit_test_issue else 0}")
    if coverage_summary:
        print("[INFO] Coverage summary:")
        print(coverage_summary)
    print(f"[INFO] Module import failures: {len(import_issues)}")
    print(f"[INFO] Elapsed time: {elapsed:.2f}s")

    if total_issues == 0:
        if quick_mode:
            print(
                "[PASS] Quick checks passed: compile, Ruff, ty, and unit tests all succeeded."
            )
            print("[INFO] For module import checks, run: python CI/python_ci.py --full")
        else:
            print(
                "[PASS] Full checks passed: compile, Ruff, ty, unit tests, and module import all succeeded."
            )
        return 0

    print(f"[FAIL] Found {total_issues} issue(s):")
    print_issues("[Compile failures]", compile_issues)
    print_issues("[Ruff diagnostics]", ruff_issues)
    print_issues("[Type ignore diagnostics]", type_ignore_issues)
    print_issues("[Unicode escape diagnostics]", unicode_escape_issues)
    if ty_mode:
        print_issues("[ty diagnostics]", ty_issues)
    if unit_test_issue:
        print_issues("[Unit test failures]", [unit_test_issue])
    print_issues("[Module import failures]", import_issues)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
