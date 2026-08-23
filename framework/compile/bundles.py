"""Permission-bundle expansion into native Unity Catalog privileges.

Encodes the design doc's Permission Bundles table. ``expand_bundle`` returns
``(privilege_type, securable_type, securable_fqn)`` triples including the traversal
grants (USE CATALOG / USE SCHEMA) needed to reach a nested securable.

Privilege strings use the underscore form stored in
``information_schema.privilege_type`` (verified against the live workspace:
USE_CATALOG, USE_SCHEMA, READ_VOLUME, EXECUTE, SELECT, ...) so target rows diff
directly against live rows. A view is granted like a table (privileges land in
table_privileges).
"""
from __future__ import annotations

Priv = tuple[str, str, str]   # (privilege_type, securable_type, securable_fqn)


class BundleError(ValueError):
    """Raised for an unsupported (bundle, object_type) combination."""


def _ancestors(object_type: str, fqn: str) -> list[Priv]:
    """Traversal grants on the ancestor securables of ``fqn``."""
    parts = fqn.split(".")
    if object_type == "catalog":
        return []
    out: list[Priv] = [("USE_CATALOG", "catalog", parts[0])]
    if object_type == "schema":
        return out
    out.append(("USE_SCHEMA", "schema", f"{parts[0]}.{parts[1]}"))
    return out


def _read_leaf(object_type: str, fqn: str) -> list[Priv]:
    if object_type == "catalog":
        # USE SCHEMA + SELECT granted ON CATALOG inherit to all descendant schemas/tables.
        return [("USE_CATALOG", "catalog", fqn),
                ("USE_SCHEMA", "catalog", fqn),
                ("SELECT", "catalog", fqn)]
    if object_type == "schema":
        return [("USE_SCHEMA", "schema", fqn), ("SELECT", "schema", fqn)]
    if object_type in ("table", "view"):
        return [("SELECT", "table", fqn)]
    if object_type == "function":
        return [("EXECUTE", "function", fqn)]
    if object_type == "volume":
        return [("READ_VOLUME", "volume", fqn)]
    raise BundleError(f"READ not supported for object type {object_type!r}")


def expand_bundle(bundle: str, object_type: str, fqn: str) -> list[Priv]:
    """Expand one bundle grant on one object into native-privilege triples."""
    if bundle == "READ":
        return _ancestors(object_type, fqn) + _read_leaf(object_type, fqn)

    if bundle == "WRITE":
        if object_type == "catalog":
            return _read_leaf("catalog", fqn) + [("MODIFY", "catalog", fqn)]
        if object_type == "schema":
            return _ancestors("schema", fqn) + _read_leaf("schema", fqn) + [("MODIFY", "schema", fqn)]
        if object_type in ("table", "view"):
            return _ancestors(object_type, fqn) + [("SELECT", "table", fqn), ("MODIFY", "table", fqn)]
        raise BundleError(f"WRITE not supported for object type {object_type!r}")

    if bundle == "OWNER":
        # Schema create privileges used by the restricted deployment/owner workflow.
        if object_type == "schema":
            return _ancestors("schema", fqn) + [
                ("USE_SCHEMA", "schema", fqn),
                ("CREATE_TABLE", "schema", fqn),
                ("CREATE_FUNCTION", "schema", fqn),
                ("CREATE_VOLUME", "schema", fqn),
            ]
        raise BundleError(f"OWNER only supported at schema grain, not {object_type!r}")

    if bundle == "ADMIN":
        st = "table" if object_type == "view" else object_type
        return _ancestors(object_type, fqn) + [("MANAGE", st, fqn)]

    raise BundleError(f"unknown bundle {bundle!r}")
