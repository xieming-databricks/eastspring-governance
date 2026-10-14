"""CLI for the Eastspring config validator.

    python -m eastspring_uc_governance.validation.cli configs/esg --reference configs/reference/esg.yaml [--skip-warning]

--reference (the allow-list of groups / schemas / data products / tags) is required: it is never
guessed from the config path, and a missing or unreadable file is an error.

Exit code 0 when the config reports no issues, 1 otherwise — matching the
design doc's "report through stdout/stderr and exit codes". Warnings (e.g. a
group not in the reference allow-list) fail too, unless --skip-warning is given:
then only errors fail, and warnings are still printed.
"""
from __future__ import annotations

import argparse
import sys

from .validator import ValidationResult, validate_dir


def _print(result: ValidationResult, verbose: bool = False) -> None:
    if verbose:
        print(f"checked {len(result.checks)} item(s):")
        for check in result.checks:
            print(f"  {check}")
    print(result.summary())
    for issue in result.sorted_issues():
        print(f"  {issue}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate", description=__doc__)
    parser.add_argument("config_dir",
                        help="directory holding base_grants/labels/tags/policies.yaml "
                             "(+ optional rbac_view_grants.yaml, rbac_views.yaml)")
    parser.add_argument("--reference", required=True, metavar="PATH",
                        help="the reference allow-list YAML to check the config against, e.g. reference/esg.yaml")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every check performed (files, schemas, semantic checks, reference), not just issues")
    parser.add_argument("--skip-warning", action="store_true",
                        help="exit 0 when there are only warnings (they are still printed); "
                             "by default warnings fail like errors")
    args = parser.parse_args(argv)

    result = validate_dir(args.config_dir, reference=args.reference)
    _print(result, verbose=args.verbose)
    if not result.ok:
        return 1
    if result.warnings and not args.skip_warning:
        print(f"FAIL: {len(result.warnings)} warning(s) — fix them, or pass --skip-warning to ignore")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
