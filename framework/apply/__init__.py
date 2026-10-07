"""Apply the reconcile delta to Unity Catalog.

``statement`` (pure Python, no Spark) turns delta rows into GRANT/REVOKE/ALTER…TAGS
strings and is imported freely; the Spark ``executor`` is imported lazily by callers
running on Databricks, so importing this package never requires pyspark.
"""
from . import statement

__all__ = ["statement"]
