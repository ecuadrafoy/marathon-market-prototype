"""Marathon runner ecosystem — standalone test harness, decoupled from the market layer."""

from .shells import Shell, SHELL_ROSTER, SHELL_BY_NAME
from .runners import Runner, drift_attributes, effective_capability
from .encounters import WeeklyOutcome, resolve_week
from .harness import run_simulation, print_leaderboard

__all__ = [
    "Shell",
    "SHELL_ROSTER",
    "SHELL_BY_NAME",
    "Runner",
    "drift_attributes",
    "effective_capability",
    "WeeklyOutcome",
    "resolve_week",
    "run_simulation",
    "print_leaderboard",
]
