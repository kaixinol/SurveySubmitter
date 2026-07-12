from __future__ import annotations

from CI.python_checks.common import (
    configure_console_encoding,
    ensure_target_dirs,
    print_issues,
    print_scan_targets,
    run_ty_check,
)


def main() -> int:
    configure_console_encoding()
    target_dirs = ensure_target_dirs()

    print_scan_targets(target_dirs)
    issues, error = run_ty_check(target_dirs)
    if error:
        print(f"[ERROR] {error}")
        return 2

    print(f"[INFO] ty diagnostics: {len(issues)}")
    if not issues:
        print("[PASS] ty checks passed.")
        return 0

    print(f"[FAIL] Found {len(issues)} ty diagnostic(s):")
    print_issues("[ty diagnostics]", issues)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
