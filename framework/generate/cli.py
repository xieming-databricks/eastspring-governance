"""Generate framework config YAMLs from an Access-Matrix workbook.

    python -m framework.generate.cli <workbook.xlsx> <out_dir> [--check]

Reads the workbook, converts it (asset-level: labels + grants, with valid empty tag/policy
stubs), self-validates, and writes the config directory. ``--check`` validates and prints a
summary without writing. Exit code 0 on success, 1 on any generation/validation failure.
"""
from __future__ import annotations

import argparse
import sys

from .generator import GeneratorError, REQUIRED_SHEETS, generate_config, write_config
from .workbook import read_workbook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate framework config YAMLs from an Access-Matrix workbook.")
    parser.add_argument("workbook", help="path to the Access-Matrix .xlsx")
    parser.add_argument("out_dir", help="output config directory (e.g. config/esg)")
    parser.add_argument("--check", action="store_true",
                        help="validate and print a summary without writing files")
    args = parser.parse_args(argv)

    try:
        wb = read_workbook(args.workbook, sheets=REQUIRED_SHEETS)
        docs = generate_config(wb)
    except GeneratorError as exc:
        print(f"FAIL {args.workbook}\n{exc}")
        return 1

    n_labels = len(docs["labels"]["labels"])
    n_base = len(docs["base_grants"]["grant_rules"])
    n_rbac = len(docs["rbac_grants"]["grant_rules"])
    n_exc = sum(len(r.get("include", [])) + len(r.get("exclude", []))
                for r in docs["base_grants"]["grant_rules"])
    summary = (f"{n_labels} labels, {n_base} base grants, {n_rbac} rbac grants, "
               f"{n_exc} exception object(s)")

    if args.check:
        print(f"OK {args.workbook} — {summary} (not written)")
        return 0
    write_config(docs, args.out_dir)
    print(f"OK {args.workbook} -> {args.out_dir} — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
