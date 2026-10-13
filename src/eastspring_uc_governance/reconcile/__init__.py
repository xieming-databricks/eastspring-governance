"""Reconcile target state against live Unity Catalog state.

Pure-Python pieces (``diff``, ``preflight``) import cleanly with no Spark. The Spark
I/O layer (``live``, ``frames``) is imported lazily by callers that run on Databricks,
so importing this package never requires pyspark.
"""
from . import diff, preflight, scope

__all__ = ["diff", "preflight", "scope"]
