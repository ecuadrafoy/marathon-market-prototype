"""Autoload every user-authored leaf in this package.

Files in this directory are written by the editor's "New Leaf" form and
register themselves via @bt_condition on import. We discover them with
pkgutil so adding a new file is a drop-in — no central list to update.

Files starting with `_` are skipped (reserved for non-leaf helpers if any).
"""
from __future__ import annotations
import importlib
import pkgutil

for _info in pkgutil.iter_modules(__path__):
    if _info.name.startswith("_"):
        continue
    importlib.import_module(f"{__name__}.{_info.name}")
