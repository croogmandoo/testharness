"""Database access layer.

Re-exports ``Database`` so external callers keep importing from
``harness.db`` exactly as before. The class itself lives in
``harness.db._base`` and gradually picks up its methods from a set of
per-domain mixins in this package (``_audit``, ``_users``, etc).
"""
from ._base import Database

__all__ = ["Database"]
