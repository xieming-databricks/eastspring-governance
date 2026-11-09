"""CLI for the Eastspring config validator.

    python -m eastspring_uc_governance.validation.cli config/esg --reference reference/esg.yaml --sql sql/esg [--verbose]

--reference (the allow-list of groups / schemas / data products / tags) and --sql (the deployment's
SQL folder: mask UDFs + the RLS allow-list MV) are required: neither is guessed from the config
path, and a missing or unreadable one is an error. The SQL is checked against both: every file
renders, each acm_mask_<x>.sql defines acm_mask_<x> and uses ${unmasked_condition}, every Masking
Rules function has its file, and the reference's rls_entitlement_table is created by one of them.

Every issue is reported in one run (nothing stops at the first one). Exit code 1 if there is any
error — a schema / semantic violation, anything outside the reference allow-list, a broken SQL
folder — and 0 otherwise. Warnings are advisory only (e.g. an RLS table the SQL folder doesn't
create): printed, never failing.
"""
from __future__ import annotations

import argparse
import sys

from .validator import ValidationResult, validate_dir


def print_result(result: ValidationResult, verbose: bool = False) -> None:
    """The checks (with --verbose), the summary line and every issue — shared by ucgov-validate and
    ucgov-generate, so both report the same way."""
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
    parser.add_argument("--sql", required=True, metavar="PATH",
                        help="the deployment's SQL folder (mask UDFs, RLS allow-list MV), e.g. sql/esg")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every check performed (files, schemas, semantic checks, reference), not just issues")
    args = parser.parse_args(argv)

    result = validate_dir(args.config_dir, reference=args.reference, sql_dir=args.sql)
    print_result(result, verbose=args.verbose)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
