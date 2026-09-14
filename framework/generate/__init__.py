"""Generate framework config YAMLs from a signed-off Access-Matrix workbook.

``read_workbook`` (openpyxl I/O) loads the tabs; ``generate_config`` (pure) transforms them
into the docs-shaped dict the validator/compiler consume — running ``validate_docs`` on its
own output; ``write_config`` serializes that to a config directory. Asset-level scope: labels
+ grants are populated, tags/policies are valid empty stubs.
"""
from .generator import GeneratorError, REQUIRED_SHEETS, generate_config, write_config
from .workbook import read_workbook

__all__ = ["read_workbook", "generate_config", "write_config", "GeneratorError",
           "REQUIRED_SHEETS"]
