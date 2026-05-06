"""Opt-in tracing of behaviour-tree decisions during simulation.

When `Tracer.enable()` has been called, the extraction and encounter
dispatchers each emit one line per decision. The output is meant for
humans reading a sim run, not for parsing — fields are spaced for
visual scanning, not for stable column boundaries.

Disabled by default. The simulator turns it on when `--trace-ai` is
passed on the command line. Tests turn it on inside a fixture and
read the captured stdout.

Why a global toggle and not, say, a logger? Two reasons:
- The output is intentionally a side-effect of the dispatcher: zero
  overhead when disabled (one boolean check), no logger/handler
  configuration to remember.
- The trace runs *inside* the existing sim's print-driven UI, so the
  lines need to interleave naturally with the sim's own log. Mixing
  in logging output via a separate handler would race with stdout.
"""

from __future__ import annotations
from typing import Any


class Tracer:
    """Module-level singleton state. Use `Tracer.enable()` / `Tracer.disable()`.

    The class is stateful at the *class level* on purpose — every dispatcher
    needs cheap access to the toggle, and threading a context through every
    call site for an opt-in debug feature would add API churn for zero
    runtime benefit.
    """
    enabled: bool = False

    @classmethod
    def enable(cls) -> None:
        cls.enabled = True

    @classmethod
    def disable(cls) -> None:
        cls.enabled = False

    @classmethod
    def emit(cls, line: str) -> None:
        """Print `line` if tracing is on. Flush so output interleaves
        with surrounding sim prints."""
        if cls.enabled:
            print(line, flush=True)


# ---------------------------------------------------------------------------
# Convenience formatters used by extraction_ai / encounter_ai.
#
# Kept in this module so the dispatchers stay readable — they call one
# function each, the formatting choices live here.
# ---------------------------------------------------------------------------
def format_extract(doctrine_value: str, result: bool, loot: Any,
                   perception: Any) -> str:
    """Build the trace line for one should_extract call."""
    best = loot.best_tier()
    best_str = best.name if best is not None else "—"
    return (
        f"[bt] T{perception.tick:>2}/{perception.max_ticks}  "
        f"extract_{doctrine_value:<8} → {_yes_no(result)}  "
        f"loot={len(loot.items):>2}({best_str:<8}) "
        f"dry={_truthy(perception.zone_feels_dry())} "
        f"enc={_truthy(perception.had_encounter_this_run)} "
        f"dmg={_truthy(perception.took_damage_this_run)}"
    )


def format_engage(doctrine_value: str, result: bool, own_combat: float,
                  opponent_combat_estimate: float, loot: Any) -> str:
    """Build the trace line for one should_engage call."""
    ratio = own_combat / max(0.001, opponent_combat_estimate)
    best = loot.best_tier()
    best_str = best.name if best is not None else "—"
    return (
        f"[bt]        "                             # spacing aligns with extract lines
        f"engage_{doctrine_value:<8}  → {_yes_no(result)}  "
        f"own={own_combat:>4.1f} opp={opponent_combat_estimate:>4.1f} "
        f"ratio={ratio:>4.2f}  loot={best_str}"
    )


def _yes_no(result: bool) -> str:
    return "YES" if result else "NO "


def _truthy(value: bool) -> str:
    return "Y" if value else "."
