"""
Test harness — orchestrates a multi-week standalone run of the runner ecosystem.

Validation goal: after 20–30 weeks against a fixed persistent pool, veterans
should visibly outperform novices on the leaderboard and runners should
specialize toward the affinity profile of the shell they wear most. No
runaway dominance, no homogeneous collapse.

Run: uv run python -m runner_sim --weeks 25 --pool 30 --seed 42
"""

from __future__ import annotations
import argparse
import random
import sys

from .encounters import EncounterReport, WeeklyOutcome, resolve_week
from .runners import (
    Runner,
    choose_best_shell,
    drift_attributes,
    extraction_success_rate,
    gain_affinity,
    switch_shell,
)
from .shells import SHELL_BY_NAME, SHELL_ROSTER

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
POOL_SIZE  = 30
TEST_WEEKS = 25

COMPANY_NAMES: tuple[str, ...] = ("Aegis", "Helix", "Vector")

# Flavor names for runner identities — purely cosmetic
NAME_POOL: tuple[str, ...] = (
    "Vega", "Orion", "Lyra", "Sable", "Crow", "Echo", "Ash", "Wren",
    "Pike", "Onyx", "Juno", "Cipher", "Nova", "Ridge", "Hex", "Kite",
    "Mara", "Tully", "Quinn", "Shrike", "Vesper", "Pax", "Cinder", "Halo",
    "Glass", "Kestrel", "Thorne", "Reno", "Slate", "Brand", "Lark", "Mire",
    "Drift", "Polar", "Rook", "Soren", "Tessa", "Volk", "Wynn", "Yara",
)


# ---------------------------------------------------------------------------
# POOL CREATION & OUTCOME APPLICATION
# ---------------------------------------------------------------------------
def _random_simplex_triple() -> tuple[float, float, float]:
    """Uniform random point on the 2-simplex — (a, b, c) >= 0 with a+b+c=1."""
    a, b = sorted((random.random(), random.random()))
    return a, b - a, 1.0 - b


def create_runner_pool(size: int) -> list[Runner]:
    """Generate the persistent runner pool. Career stats start at zero;
    each runner gets a uniformly random (combat, extraction, support) triple."""
    names = list(NAME_POOL)
    random.shuffle(names)
    runners: list[Runner] = []
    shell_names = [s.name for s in SHELL_ROSTER]
    for i in range(size):
        name = names[i] if i < len(names) else f"Runner-{i:03d}"
        c, e, s = _random_simplex_triple()
        runners.append(Runner(
            id=i,
            name=name,
            company_name=random.choice(COMPANY_NAMES),
            combat=c,
            extraction=e,
            support=s,
            current_shell=random.choice(shell_names),
        ))
    return runners


def apply_outcome(runner: Runner, outcome: WeeklyOutcome) -> None:
    """Update career stats and drift attributes from a weekly outcome.

    Death is a stat counter — the runner respawns next week in whatever shell
    the AI picks for them. No state transition, no time off.
    """
    if not outcome.participated:
        return

    runner.extraction_attempts += 1
    if outcome.extracted:
        runner.extraction_successes += 1
        runner.net_loot += outcome.yield_received
    runner.eliminations += outcome.eliminations_scored

    if outcome.survived:
        gain_affinity(runner, runner.current_shell)
        drift_attributes(runner, SHELL_BY_NAME[runner.current_shell])
    else:
        runner.death_count += 1


# ---------------------------------------------------------------------------
# WEEKLY LOOP
# ---------------------------------------------------------------------------
def _dominant_axis(runner: Runner) -> str:
    """Return 'C', 'E', or 'S' — whichever attribute is currently largest."""
    pairs = (("C", runner.combat), ("E", runner.extraction), ("S", runner.support))
    return max(pairs, key=lambda p: p[1])[0]


def _print_squad(squad: list[Runner], label: str) -> None:
    parts = [f"{r.name}({r.company_name[0]}/{r.current_shell[:3]}/{_dominant_axis(r)})" for r in squad]
    print(f"  {label}: {', '.join(parts)}")


def _print_week(week: int, report: EncounterReport, outcomes: dict[int, WeeklyOutcome]) -> None:
    print(f"\n=== Week {week:02d} ===")
    print(f"Squads formed: {len(report.squads)}   Sit-outs: {len(report.sit_outs)}")

    for idx, (squad_a, squad_b, winner) in enumerate(report.contested_pairs, start=1):
        print(f"\n[Encounter {idx}]")
        _print_squad(squad_a, "Squad A")
        _print_squad(squad_b, "Squad B")
        winner_label = "A" if winner is squad_a else "B"
        loser = squad_b if winner is squad_a else squad_a
        winner_yield = sum(outcomes[r.id].yield_received for r in winner)
        kills = sum(outcomes[r.id].eliminations_scored for r in winner)
        print(f"  -> Squad {winner_label} wins. Extraction yield: {winner_yield:.1f}  Kills: {kills}  Losses: {len(loser)}")

    for idx, squad in enumerate(report.uncontested, start=1):
        print(f"\n[Uncontested {idx}]")
        _print_squad(squad, "Squad")
        squad_yield = sum(outcomes[r.id].yield_received for r in squad)
        print(f"  -> Extraction yield: {squad_yield:.1f}")

    if report.sit_outs:
        names = ", ".join(r.name for r in report.sit_outs)
        print(f"\n[Sat out] {names}")


def _print_shell_switches(switches: list[tuple[Runner, str, str]]) -> None:
    if not switches:
        return
    parts = [f"{r.name}: {old} -> {new}" for r, old, new in switches]
    print(f"\n[Shell switches] {' | '.join(parts)}")


def print_initial_pool(pool: list[Runner]) -> None:
    """Dump the starting roster — random profiles before any week is played.

    'Match' is the dot product between the runner's (combat, extraction, support)
    attributes and their starting shell's affinity vector. Both are simplex points
    so match is in [0, 1]; 0.33 is the random expectation, higher = better fit.
    """
    header = (
        f"{'ID':>3}  {'Name':<10} {'Co':<3} {'Shell':<10} "
        f"{'Cmb':>4} {'Ext':>4} {'Sup':>4}  {'Match':>5}"
    )
    print("\n" + "=" * len(header))
    print("INITIAL POOL (week 0 — random profiles, no career history)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for runner in sorted(pool, key=lambda r: r.id):
        shell = SHELL_BY_NAME[runner.current_shell]
        match = (
            runner.combat * shell.combat_affinity
            + runner.extraction * shell.extraction_affinity
            + runner.support * shell.support_affinity
        )
        print(
            f"{runner.id:>3}  {runner.name:<10} {runner.company_name[:3]:<3} {runner.current_shell:<10} "
            f"{runner.combat:>4.2f} {runner.extraction:>4.2f} {runner.support:>4.2f}  {match:>5.2f}"
        )
    print("=" * len(header))


def run_simulation(weeks: int, pool_size: int, seed: int | None, quiet: bool, print_pool: bool = False) -> list[Runner]:
    if seed is not None:
        random.seed(seed)

    pool = create_runner_pool(pool_size)

    if not quiet:
        print(f"=== Runner Ecosystem Test Harness ===")
        print(f"Pool: {pool_size} runners   Weeks: {weeks}   Seed: {seed}")
        print(f"Companies: {', '.join(COMPANY_NAMES)}")
        print(f"Shells: {', '.join(s.name for s in SHELL_ROSTER)}")

    if print_pool:
        print_initial_pool(pool)

    for week in range(1, weeks + 1):
        # All runners participate every week — death is a stat counter, not a
        # state. Shell selection: each runner picks the shell that maximizes
        # their attribute-weighted effective capability. Skipped on week 1 so
        # the random starting shell is the one actually played — gives the
        # timeline an observable "natural starting state" before the AI kicks in.
        switches: list[tuple[Runner, str, str]] = []
        if week > 1:
            for runner in pool:
                previous = runner.current_shell
                best = choose_best_shell(runner, SHELL_ROSTER)
                if switch_shell(runner, best.name):
                    switches.append((runner, previous, best.name))

        # Record shell history for the week, after shell selection so the entry
        # reflects the shell used in this week's encounters.
        for runner in pool:
            runner.shell_history.append(runner.current_shell)

        outcomes, report = resolve_week(pool)

        for runner in pool:
            apply_outcome(runner, outcomes[runner.id])

        if not quiet:
            _print_week(week, report, outcomes)
            _print_shell_switches(switches)

    return pool


# ---------------------------------------------------------------------------
# LEADERBOARD
# ---------------------------------------------------------------------------
def print_leaderboard(pool: list[Runner]) -> None:
    ranked = sorted(pool, key=lambda r: r.net_loot, reverse=True)

    header = (
        f"{'Rank':>4}  {'Name':<10} {'Co':<3} {'Shell':<10} "
        f"{'Cmb':>4} {'Ext':>4} {'Sup':>4}  "
        f"{'Loot':>8}  {'Kills':>5}  {'Deaths':>6}  {'Succ%':>5}"
    )
    print("\n" + "=" * len(header))
    print("FINAL LEADERBOARD (sorted by net loot)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for rank, runner in enumerate(ranked, start=1):
        succ_pct = extraction_success_rate(runner) * 100.0
        print(
            f"{rank:>4}  {runner.name:<10} {runner.company_name[:3]:<3} {runner.current_shell:<10} "
            f"{runner.combat:>4.2f} {runner.extraction:>4.2f} {runner.support:>4.2f}  "
            f"{runner.net_loot:>8.1f}  {runner.eliminations:>5d}  {runner.death_count:>6d}  "
            f"{succ_pct:>4.0f}%"
        )
    print("=" * len(header))


def _shell_history_string(runner: Runner) -> str:
    """Render the runner's per-week shell record as a single-letter string,
    one character per week using each shell's `code`.
    """
    parts: list[str] = []
    for entry in runner.shell_history:
        shell = SHELL_BY_NAME.get(entry)
        parts.append(shell.code if shell else "?")
    return "".join(parts)


def _shell_switch_count(runner: Runner) -> int:
    """Count how many times the runner switched shells across the recorded weeks."""
    last: str | None = None
    switches = 0
    for entry in runner.shell_history:
        if last is not None and entry != last:
            switches += 1
        last = entry
    return switches


def print_shell_histories(pool: list[Runner]) -> None:
    """One-line-per-runner per-week shell timeline.

    Each character is a shell code (D/A/V/T/R/G/K). Sorted by leaderboard rank
    (net loot descending) for readability.
    """
    ranked = sorted(pool, key=lambda r: r.net_loot, reverse=True)
    legend = "  ".join(f"{s.code}={s.name}" for s in SHELL_ROSTER)

    header = f"{'Rank':>4}  {'Name':<10}  {'Sw':>3}  Timeline"
    print("\n" + "=" * 80)
    print("PER-WEEK SHELL HISTORY")
    print(f"Legend: {legend}")
    print("=" * 80)
    print(header)
    print("-" * 80)
    for rank, runner in enumerate(ranked, start=1):
        sw = _shell_switch_count(runner)
        timeline = _shell_history_string(runner)
        print(f"{rank:>4}  {runner.name:<10}  {sw:>3}  {timeline}")
    print("=" * 80)


def print_summary_stats(pool: list[Runner]) -> None:
    """Print high-level differentiation stats for validation."""
    ranked = sorted(pool, key=lambda r: r.net_loot, reverse=True)
    n = len(ranked)
    top_n = max(1, n // 6)   # top ~5 of 30
    top = ranked[:top_n]
    bottom = ranked[-top_n:]

    def avg(rs: list[Runner], attr: str) -> float:
        return sum(getattr(r, attr) for r in rs) / len(rs) if rs else 0.0

    total_loot = sum(r.net_loot for r in pool) or 1.0
    max_share = max(r.net_loot for r in pool) / total_loot

    print("\n--- Differentiation summary ---")
    print(f"Top {top_n} avg net_loot: {avg(top, 'net_loot'):.1f}    Bottom {top_n} avg: {avg(bottom, 'net_loot'):.1f}")
    print(f"Top {top_n} avg eliminations: {avg(top, 'eliminations'):.2f}    Bottom {top_n} avg: {avg(bottom, 'eliminations'):.2f}")
    print(f"Top {top_n} avg deaths: {avg(top, 'death_count'):.2f}    Bottom {top_n} avg: {avg(bottom, 'death_count'):.2f}")
    print(f"Largest single share of total loot: {max_share*100:.1f}%   (collapse warning if >25%)")
    zero_participation = sum(1 for r in pool if r.extraction_attempts == 0)
    print(f"Runners with zero participations: {zero_participation}")


# ---------------------------------------------------------------------------
# CLI ENTRY
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner_sim", description="Marathon runner ecosystem test harness")
    parser.add_argument("--weeks", type=int, default=TEST_WEEKS, help="number of weeks to simulate")
    parser.add_argument("--pool", type=int, default=POOL_SIZE, help="runner pool size")
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducibility")
    parser.add_argument("--quiet", action="store_true", help="suppress weekly logs; show only the leaderboard")
    parser.add_argument("--print-pool", action="store_true", help="print the initial random pool before week 1")
    parser.add_argument("--print-history", action="store_true", help="print each runner's per-week shell timeline after the leaderboard")
    args = parser.parse_args(argv)

    pool = run_simulation(args.weeks, args.pool, args.seed, args.quiet, args.print_pool)
    print_leaderboard(pool)
    print_summary_stats(pool)
    if args.print_history:
        print_shell_histories(pool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
