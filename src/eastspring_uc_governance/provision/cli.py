"""Provision account groups + env schemas via the databricks CLI (the CLI alternative to terraform/).

    ucgov-provision groups  --env esg --profile <workspace-profile>            # plan
    ucgov-provision groups  --env esg --profile <workspace-profile> --apply    # execute
    ucgov-provision schemas --env esg --profile <workspace-profile> [--apply]

(`ucgov-provision` is the installed entry point; `python -m eastspring_uc_governance.provision.cli`
is equivalent.) Both subcommands read <config-root>/reference/<env>.yaml — --config-root defaults
to ./config, i.e. run from the directory holding config/ — and use a WORKSPACE profile: no
account-level access is needed.

groups   the reference's `groups` ({name, external_id}). Account groups are reached through the
         workspace-level IAM v2 proxy API, which acts on the account that parents the workspace:
           external_id set  -> `workspace-iam-v2 resolve-group-proxy <external_id>` (AIM; provisions
                               the Entra group into the account). Needs AIM onboarding.
           external_id null -> `workspace-iam-v2 create-group-proxy` (a local account group).
         Needs WORKSPACE ADMIN on an identity-federated (Unity Catalog) workspace. The groups are
         still account-level (usable in UC grants), unlike `databricks groups create`, which makes
         workspace-local groups UC can't grant to. Run it before the env's grants.
schemas  governance_schemas + serving_schemas under target_catalog (the catalog itself is
         customer-owned and must already exist). Needs CREATE SCHEMA on the catalog.

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

from ..validation import check_reference
from . import cli_runner, planner

def _reference(args: argparse.Namespace) -> tuple[dict[str, Any] | None, str]:
    """Load <config-root>/reference/<env>.yaml (or --reference); returns (doc or None, display path).
    Config is per-deployment data, not part of the package, so it is resolved from the current
    directory — never from where the package is installed."""
    path = Path(args.reference) if args.reference else Path(args.config_root) / "reference" / f"{args.env}.yaml"
    resolved, cwd = path.resolve(), Path.cwd().resolve()
    shown = str(resolved.relative_to(cwd)) if resolved.is_relative_to(cwd) else str(resolved)
    if not path.exists():
        print(f"not found: {shown}")
        return None, shown
    doc = yaml.safe_load(path.read_text())
    return (doc if isinstance(doc, dict) else {}), shown


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
    reference, source = _reference(args)
    if reference is None:
        return 1
    for w in check_reference({}, reference):           # malformed GUID / duplicate / unnamed group
        print(f"WARNING {w}")
    try:
        live = cli_runner.list_account_groups(args.profile)
    except cli_runner.CliError as exc:
        print(f"could not list account groups: {exc}")
        return 1
    results = cli_runner.execute(planner.plan_groups(reference, live),
                                 profile=args.profile, dry_run=not args.apply)
    return _report(source, results, args.apply)


def _schemas(args: argparse.Namespace) -> int:
    reference, source = _reference(args)
    if reference is None:
        return 1
    catalog = reference.get("target_catalog")
    if not catalog:
        print(f"no target_catalog in {source}")
        return 1
    try:
        live = cli_runner.list_schemas(catalog, args.profile)
    except cli_runner.CliError as exc:
        print(f"could not list schemas in {catalog!r} (it must already exist): {exc}")
        return 1
    results = cli_runner.execute(planner.plan_schemas(reference, live, source=source),
                                 profile=args.profile, dry_run=not args.apply)
    return _report(source, results, args.apply)


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", default="esg", help="selects <config-root>/reference/<env>.yaml (default: esg)")
    common.add_argument("--config-root", default="config",
                        help="directory holding reference/ (default: ./config, relative to the current dir)")
    common.add_argument("--reference", help="explicit path to the reference YAML (overrides --env/--config-root)")
    common.add_argument("--profile", help="WORKSPACE-level CLI profile (default: CLI default)")
    common.add_argument("--apply", action="store_true", help="execute (default: plan only)")

    parser = argparse.ArgumentParser(prog="provision", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("groups", parents=[common],
                   help="account groups from the reference's `groups` (needs workspace admin)"
                   ).set_defaults(func=_groups)
    sub.add_parser("schemas", parents=[common],
                   help="governance + serving schemas from the reference"
                   ).set_defaults(func=_schemas)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
