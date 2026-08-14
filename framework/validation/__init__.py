"""Validation for the Eastspring governance YAML contract.

Code-first Pydantic v2 models (``pydantic_validator``) mirror the contract:
``extra="forbid"`` (closed records), ``Literal`` enums, and ``@model_validator``
for cross-field / cross-item rules. YAML -> ``model_validate`` -> typed objects.

``validate_dir(config_dir) -> ValidationResult`` returns the normalized
``Issue`` shape.
"""
from .pydantic_validator import Issue, ValidationResult, validate_dir

__all__ = ["Issue", "ValidationResult", "validate_dir"]
