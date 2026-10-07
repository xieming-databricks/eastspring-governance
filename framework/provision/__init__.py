"""Provision the containers + identities the framework needs, via the ``databricks`` CLI.

The CLI alternative to ``terraform/``, with the same split and the same YAML contract:

- **groups** (account level, ``config/account/groups.yaml``): ``external_id`` set -> resolved from
  Entra ID via AIM; ``null`` -> a local Databricks account group.
- **schemas** (per env, ``config/reference/<env>.yaml``): the governance + serving schemas under
  the pre-existing ``target_catalog``.

``planner`` (pure Python) diffs the YAML against live state into ``Action``s; ``cli_runner``
reads live state and executes actions through the ``databricks`` CLI. Additive only — nothing
is ever deleted.
"""
from . import planner

__all__ = ["planner"]
