"""Provision the containers, identities + governed tags the framework needs, via the ``databricks`` CLI.

Everything is driven by the env's reference YAML:

- **groups** (the reference's ``groups``): ``external_id`` set -> resolved from Entra ID via AIM;
  ``null`` -> a local Databricks account group. Done through the workspace-level IAM v2 proxy API,
  so it needs a workspace admin, not account-level access.
- **schemas**: the governance + serving schemas under the pre-existing ``target_catalog``.
- **tags**: the reference's ``governed_tags`` as governed tags (tag policies) with their allowed
  values — what the ABAC column-mask policies match on.

All read the reference YAML given by ``--reference`` (required).

``planner`` (pure Python) diffs the YAML against live state into ``Action``s; ``cli_runner``
reads live state and executes actions through the ``databricks`` CLI. Additive only — nothing
is ever deleted.
"""
from . import planner

__all__ = ["planner"]
