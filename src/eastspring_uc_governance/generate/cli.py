"""Generate framework config YAMLs from an Access-Matrix workbook.

    python -m eastspring_uc_governance.generate.cli <workbook.xlsx> <out_dir> [--check | --verify]

Reads the workbook, converts it (asset-level: labels + grants, with valid empty tag/policy
stubs), self-validates, and writes the config directory. ``--check`` validates and prints a
summary without writing. ``--verify`` writes nothing either, but fails unless ``out_dir`` already
holds exactly what the workbook generates (a committed config that is out of date with its
workbook). Exit code 0 on success, 1 on any generation/validation failure or difference.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from .generator import GeneratorError, REQUIRED_SHEETS, generate_config, write_config


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
            elif have.read_text() != f.read_text():
                diffs.append(f"changed: {f.name}")
        generated = {f.name for f in fresh.glob("*.yaml")}
    diffs += [f"extra:   {f.name} (not generated from the workbook)"
              for f in sorted(out.glob("*.yaml")) if f.name not in generated] if out.is_dir() else []
    return diffs
from .workbook import read_workbook


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate framework config YAMLs from an Access-Matrix workbook.")
    parser.add_argument("workbook", help="path to the Access-Matrix .xlsx")
    parser.add_argument("out_dir", help="output config directory (e.g. config/esg)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="validate and print a summary without writing files")
    mode.add_argument("--verify", action="store_true",
                      help="write nothing; fail unless out_dir already holds exactly what the "
                           "workbook generates")
    args = parser.parse_args(argv)

    try:
        wb = read_workbook(args.workbook, sheets=REQUIRED_SHEETS)
        docs = generate_config(wb)
    except GeneratorError as exc:
        print(f"FAIL {args.workbook}\n{exc}")
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
            print(f"regenerate: ucgov-generate {args.workbook} {args.out_dir}")
            return 1
        print(f"OK {args.out_dir} is up to date with {args.workbook} — {summary}")
        return 0
    write_config(docs, args.out_dir)
    print(f"OK {args.workbook} -> {args.out_dir} — {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
