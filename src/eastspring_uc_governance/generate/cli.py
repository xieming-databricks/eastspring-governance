"""Generate framework config YAMLs from an Access-Matrix workbook.

    ucgov-generate <workbook.xlsx> <out_dir> --reference reference/esg-dev.yaml [--check | --verify]
                   [--verbose] [--skip-warning]

Reads the workbook, converts it, validates the result (schema + the reference allow-list) and
writes the config directory. --reference is required — never guessed — so the business sees an
off-list group, schema, data product or tag value while generating, not one step later. The
workbook's Data Products tab is checked too, including entries no table or grant rule uses (those
never reach the config, so ucgov-validate can't see them).

Warnings fail by default, like ucgov-validate, and then nothing is written; --skip-warning lets
them through (still printed). --verbose lists every check. --check writes nothing; --verify writes
nothing either, but fails unless out_dir already holds exactly what the workbook generates (a
committed config out of date with its workbook). Exit code 0 on success, 1 on any failure.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from ..validation import load_reference, validate_docs
from ..validation.cli import print_result
from ..validation.validator import Check, Issue
from .generator import (GeneratorError, REQUIRED_SHEETS, T_DATA_PRODUCTS, generate_config,
                        workbook_data_products, write_config)


def _differences(docs, out_dir: str | Path) -> list[str]:
    """``changed`` / ``missing`` / ``extra`` lines for out_dir vs. what ``docs`` would write.
    Text is compared with universal newlines, so CRLF checkouts (Windows) don't count."""
    out = Path(out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        fresh = write_config(docs, tmp)
        diffs = []
        for f in sorted(fresh.glob("*.yaml")):
            have = out / f.name
            if not have.is_file():
                diffs.append(f"missing: {f.name}")
            elif have.read_text(encoding="utf-8") != f.read_text(encoding="utf-8"):
                diffs.append(f"changed: {f.name}")
        generated = {f.name for f in fresh.glob("*.yaml")}
    diffs += [f"extra:   {f.name} (not generated from the workbook)"
              for f in sorted(out.glob("*.yaml")) if f.name not in generated] if out.is_dir() else []
    return diffs
from .workbook import read_workbook


def _data_products_tab(wb, docs, reference) -> tuple[list[Issue], Check]:
    """Data Products tab entries that are not in the reference and that no table or grant rule uses.
    (A used one reaches the config and is reported by the regular reference check, with its place
    in the config.)"""
    tab = workbook_data_products(wb)
    allowed = reference.get("data_products")
    if allowed is None:
        return [], Check(f"{T_DATA_PRODUCTS} tab", f"{len(tab)} value(s); reference curates no data_products",
                         "skipped")
    used = {e.get("labels", {}).get("data_product") for e in docs["labels"]["labels"]}
    for short in ("base_grants", "rbac_view_grants"):
        for rule in (docs.get(short) or {}).get("grant_rules", []):
            used.add(((rule.get("match") or {}).get("labels") or {}).get("data_product"))
    issues = [Issue(f"{T_DATA_PRODUCTS} tab", "", f"data_product {dp!r} is not in the reference allow-list "
                                                  f"(listed in the workbook, used by no table or grant rule)",
                    "reference", "warning")
              for dp in tab if dp not in set(allowed) and dp not in used]
    return issues, Check(f"{T_DATA_PRODUCTS} tab", f"{len(tab)} value(s) vs the reference's data_products",
                         "warnings" if issues else "ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate framework config YAMLs from an Access-Matrix workbook.")
    parser.add_argument("workbook", help="path to the Access-Matrix .xlsx")
    parser.add_argument("out_dir", help="output config directory (e.g. config/esg)")
    parser.add_argument("--reference", required=True, metavar="PATH",
                        help="the reference allow-list YAML to check against, e.g. reference/esg-dev.yaml")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="list every check performed, not just issues")
    parser.add_argument("--skip-warning", action="store_true",
                        help="go on when there are only warnings (they are still printed); "
                             "by default warnings fail like errors and nothing is written")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="validate and print a summary without writing files")
    mode.add_argument("--verify", action="store_true",
                      help="write nothing; fail unless out_dir already holds exactly what the "
                           "workbook generates")
    args = parser.parse_args(argv)

    reference, reason = load_reference(args.reference)
    if reason:
        print(f"FAIL {reason}")
        return 1
    try:
        wb = read_workbook(args.workbook, sheets=REQUIRED_SHEETS)
        docs = generate_config(wb)
    except GeneratorError as exc:
        print(f"FAIL {args.workbook}\n{exc}")
        return 1

    # the generated config against the schemas + the reference, and the Data Products tab
    result = validate_docs(docs, reference=reference)
    result.config_dir = str(args.workbook)
    tab_issues, tab_check = _data_products_tab(wb, docs, reference)
    result.issues.extend(tab_issues)
    result.checks.append(tab_check)
    if args.verbose or result.issues:
        print_result(result, verbose=args.verbose)
    writes = not (args.check or args.verify)
    if not result.ok:
        print(f"FAIL: {len(result.errors)} error(s)" + (" — nothing written" if writes else ""))
        return 1
    if result.warnings and not args.skip_warning:
        print(f"FAIL: {len(result.warnings)} warning(s) — fix the workbook or the reference, or pass "
              f"--skip-warning to ignore" + (" (nothing written)" if writes else ""))
        return 1

    n_labels = len(docs["labels"]["labels"])
    n_base = len(docs["base_grants"]["grant_rules"])
    n_rbac = len(docs["rbac_view_grants"]["grant_rules"])
    n_exc = sum(len(r.get("include", [])) + len(r.get("exclude", []))
                for r in docs["base_grants"]["grant_rules"])
    summary = (f"{n_labels} labels, {n_base} base grants, {n_rbac} rbac grants, "
               f"{n_exc} exception object(s)")

    if args.check:
        print(f"OK {args.workbook} — {summary} (not written)")
        return 0
    if args.verify:
        diffs = _differences(docs, args.out_dir)
        if diffs:
            print(f"FAIL {args.out_dir} is out of date with {args.workbook}:")
            for d in diffs:
                print(f"  {d}")
            print(f"regenerate: ucgov-generate {args.workbook} {args.out_dir} --reference {args.reference}")
            return 1
        print(f"OK {args.out_dir} is up to date with {args.workbook} — {summary}")
        return 0
    write_config(docs, args.out_dir)
    print(f"OK {args.workbook} -> {args.out_dir} — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
