"""Context container passed to every leaf during tree evaluation.

A Context is a thin wrapper around a dict that supports both attribute and
item access. This lets leaves read state with idiomatic Python (`ctx.loot`,
`ctx.perception.had_encounter_this_run`) while keeping the shape flexible —
extraction trees and encounter trees populate different fields, and future
strategy trees will populate yet others.

The engine doesn't validate context shape; that's the publish gate's job
(via the `requires` field on each NodeSpec).
"""

from __future__ import annotations
from typing import Any


class Context:
    """Attribute-and-item-access namespace for leaf evaluation.

    Construct with keyword arguments:
        ctx = Context(doctrine=Doctrine.GREEDY, loot=loot, perception=p)

    Read either way:
        ctx.loot               # attribute access
        ctx["loot"]            # item access
        ctx.has("perception")  # membership test
    """

    def __init__(self, **kwargs: Any) -> None:
        # Store on a sentinel attribute so __getattr__ doesn't recurse on _data.
        object.__setattr__(self, "_data", dict(kwargs))

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "_data")
        if name in data:
            return data[name]
        raise AttributeError(
            f"Context has no attribute {name!r}. "
            f"Available: {sorted(data.keys())}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        # Allow mutation via attribute access — useful for actions that write back state.
        self._data[name] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def has(self, key: str) -> bool:
        return key in self._data

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def __repr__(self) -> str:
        return f"Context({self._data!r})"
