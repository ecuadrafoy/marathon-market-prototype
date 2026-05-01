"""
Helpers for slicing monitored-zone information out of the per-week
ZoneRunResult and Squad data, for player-facing display.

Asymmetric info is core to the design: only Sector 7 (monitored=True)
should expose runner names, squad outcomes, and per-runner intel. The
other zones contribute to the price signal but stay hidden.

Stub — implementation lands alongside step 7 of the migration.
"""

from __future__ import annotations
