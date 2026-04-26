"""
Shell module — bio-synthetic bodies that runners inhabit.

Shells are fixed templates. Their attributes never change. What evolves is the
runner's affinity with a given shell type (tracked on the runner, not here).

Combat, extraction, and support affinities sum to 1.0 per shell.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Shell:
    name: str
    combat_affinity: float
    extraction_affinity: float
    support_affinity: float
    code: str   # single-letter identifier for compact history display


SHELL_ROSTER: list[Shell] = [
    Shell("Destroyer", 0.7, 0.2, 0.1, code="D"),
    Shell("Assassin",  0.6, 0.3, 0.1, code="A"),
    Shell("Vandal",    0.5, 0.4, 0.1, code="V"),
    Shell("Thief",     0.2, 0.7, 0.1, code="T"),
    Shell("Recon",     0.2, 0.3, 0.5, code="R"),
    Shell("Triage",    0.1, 0.1, 0.8, code="G"),   # G for triaGe (T already used by Thief)
    Shell("Rook",      0.3, 0.5, 0.2, code="K"),   # K for rooK (R already used by Recon)
]

SHELL_BY_NAME: dict[str, Shell] = {s.name: s for s in SHELL_ROSTER}


for _shell in SHELL_ROSTER:
    _total = _shell.combat_affinity + _shell.extraction_affinity + _shell.support_affinity
    assert abs(_total - 1.0) < 1e-9, f"Shell {_shell.name} affinities sum to {_total}, not 1.0"
del _shell, _total
