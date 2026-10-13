"""Pure-Python compile step: validated config dicts -> normalized target state.

No Spark here. ``compile_config(docs) -> TargetState`` expands grant rules into
native-privilege rows, normalizes tag assignments and ABAC policies, and collects
the objects/functions the config references (for the preflight existence check).
"""
from .model import RefObject, TargetGrant, TargetPolicy, TargetState, TargetTag
from .compiler import compile_config

__all__ = [
    "RefObject", "TargetGrant", "TargetPolicy", "TargetState", "TargetTag",
    "compile_config",
]
