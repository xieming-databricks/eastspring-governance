"""Provision the containers + identities the framework needs, via the ``databricks`` CLI.

The CLI alternative to ``terraform/``, with the same split and the same YAML contract:

- **groups** (the reference's ``groups``): ``external_id`` set -> resolved from Entra ID via AIM;
  ``null`` -> a local Databricks account group. Done through the workspace-level IAM v2 proxy API,
  so it needs a workspace admin, not account-level access.
- **schemas**: the governance + serving schemas under the pre-existing ``target_catalog``.

Both read the reference YAML given by ``--reference`` (required).

``planner`` (pure Python) diffs the YAML against live state into ``Action``s; ``cli_runner``
reads live state and executes actions through the ``databricks`` CLI. Additive only — nothing
is ever deleted.
"""
from . import planner

__all__ = ["planner"]
