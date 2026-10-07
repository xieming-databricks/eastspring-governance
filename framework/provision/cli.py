"""Provision account groups + env schemas via the databricks CLI (the CLI alternative to terraform/).

    python -m framework.provision.cli groups  --profile <account-profile>            # plan
    python -m framework.provision.cli groups  --profile <account-profile> --apply    # execute
    python -m framework.provision.cli schemas --env esg --profile <workspace-profile> [--apply]

groups   (account level, once per account) reads config/account/groups.yaml:
           external_id set  -> `account iam-v2 resolve-group <external_id>` (AIM; provisions the
                               Entra group into the account). Needs AIM onboarding.
           external_id null -> `account groups create` (a local Databricks group).
         Needs an ACCOUNT profile with account admin. Run it before any env's grants.
schemas  (per env) reads config/reference/<env>.yaml: governance_schemas + serving_schemas under
         target_catalog (the catalog itself is customer-owned and must already exist). Needs a
         WORKSPACE profile with CREATE SCHEMA on the catalog.

Without --apply it only reads (it never resolves: resolving provisions). It is additive only —
nothing is ever deleted. --profile defaults to the CLI's own default profile. Exit code 0 on
success, 1 if anything FAILED or is a CONFLICT.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from ..validation import check_account_groups
from . import cli_runner, planner

REPO = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        print(f"not found: {path}")
        return None
    doc = yaml.safe_load(path.read_text())
    return doc if isinstance(doc, dict) else {}


def _display(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(REPO)) if resolved.is_relative_to(REPO) else str(resolved)


def _report(source: str, results: list[dict[str, Any]], apply: bool) -> int:
    print(f"provision {source} — {'APPLY' if apply else 'PLAN (read-only)'}")
    for r in results:
        line = f"  [{r['kind']}s] {r['target']:<34} {r['status']:<9}"
        if r["command"] and r["status"] in ("PLANNED", "SUCCEEDED", "FAILED"):
            line += f" -> {r['command']}"
        print(line)
        if r["error"]:
            print(f"  {'':<10}{'':<34} ! {r['error']}")
    print(f"{len(results)} item(s): {dict(Counter(r['status'] for r in results))}")
    return 1 if cli_runner.failed(results) else 0


def _groups(args: argparse.Namespace) -> int:
    path = Path(args.account_file)
    account = _load(path)
    if account is None:
        return 1
    for w in check_account_groups(account):            # malformed GUID / duplicate / unnamed
        print(f"WARNING {w}")
    try:
        live = cli_runner.list_account_groups(args.profile)
    except cli_runner.CliError as exc:
        print(f"could not list account groups: {exc}")
        return 1
    results = cli_runner.execute(planner.plan_groups(account, live),
                                 profile=args.profile, dry_run=not args.apply)
    return _report(_display(path), results, args.apply)


def _schemas(args: argparse.Namespace) -> int:
    path = Path(args.reference) if args.reference else REPO / "config" / "reference" / f"{args.env}.yaml"
    reference = _load(path)
    if reference is None:
        return 1
    catalog = reference.get("target_catalog")
    if not catalog:
        print(f"no target_catalog in {_display(path)}")
        return 1
    try:
        live = cli_runner.list_schemas(catalog, args.profile)
    except cli_runner.CliError as exc:
        print(f"could not list schemas in {catalog!r} (it must already exist): {exc}")
        return 1
    source = _display(path)
    results = cli_runner.execute(planner.plan_schemas(reference, live, source=source),
                                 profile=args.profile, dry_run=not args.apply)
    return _report(source, results, args.apply)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="provision", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("groups", help="account groups from config/account/groups.yaml")
    g.add_argument("--account-file", default=str(REPO / "config" / "account" / "groups.yaml"),
                   help="account group inventory (default: config/account/groups.yaml)")
    g.add_argument("--profile", help="ACCOUNT-level CLI profile (default: CLI default)")
    g.add_argument("--apply", action="store_true", help="execute (default: plan only)")
    g.set_defaults(func=_groups)

    s = sub.add_parser("schemas", help="governance + serving schemas from config/reference/<env>.yaml")
    s.add_argument("--env", default="esg", help="selects config/reference/<env>.yaml (default: esg)")
    s.add_argument("--reference", help="explicit path to the reference YAML (overrides --env)")
    s.add_argument("--profile", help="WORKSPACE-level CLI profile (default: CLI default)")
    s.add_argument("--apply", action="store_true", help="execute (default: plan only)")
    s.set_defaults(func=_schemas)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
