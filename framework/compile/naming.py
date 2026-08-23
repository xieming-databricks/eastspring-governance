"""Deterministic ABAC policy naming.

The framework owns the names it CREATEs, so target names match what SHOW POLICIES
returns as long as generation is deterministic. The name is derived from
(kind, scope, match_signature) but NOT the function — so changing only the function
keeps the name stable and surfaces as a CHANGE rather than REMOVE+ADD.
"""
from __future__ import annotations

import hashlib
import re

_PREFIX = "acm_policy_"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def policy_name(kind: str, scope: str, match_signature: str, descriptor: str = "") -> str:
    """kind is 'mask' or 'rls'. Returns e.g. acm_policy_mask_full_name__h1a2b3c4d5e."""
    h = hashlib.sha256(f"{kind}|{scope}|{match_signature}".encode()).hexdigest()[:10]
    desc = _slug(descriptor)
    middle = f"{kind}_{desc}" if desc else kind
    return f"{_PREFIX}{middle}__h{h}"
