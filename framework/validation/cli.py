"""CLI for the Eastspring config validator.

    python -m framework.validation.cli config/uat

Exit code 0 when the config reports no issues, 1 otherwise — matching the
design doc's "report through stdout/stderr and exit codes".
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
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every check performed (files, schemas, semantic checks, reference), not just issues")
    args = parser.parse_args(argv)

    result = validate_dir(args.config_dir)
    _print(result, verbose=args.verbose)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
