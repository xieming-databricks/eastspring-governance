"""Validation for the Eastspring governance YAML contract.

The contract is authored as YAML JSON Schema files (``schemas/config/v1/*.schema.yaml``,
Draft 2020-12) and enforced with the ``jsonschema`` library, plus a small
structural semantic layer for the cross-item rules JSON Schema cannot express.

``validate_dir(config_dir, reference=<reference yaml path | dict>) -> ValidationResult`` returns
normalized ``Issue``s.
"""
from .validator import (Check, Issue, ValidationResult, check_reference, load_reference, validate_dir,
                        validate_docs)

__all__ = ["Check", "Issue", "ValidationResult", "check_reference", "load_reference", "validate_dir",
           "validate_docs"]
