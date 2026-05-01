"""
Standalone CLI harness for the tick-based zone simulation.

Run:
    uv run python -m runner_sim.zone_sim.harness --seed 42

Uses Rich for all console output: colored zone headers, per-squad composition
tables, a styled match log, and a final standings table.
"""

from __future__ import annotations
import argparse
import random
import re

from rich.console import Console
from rich.rule import Rule  # noqa: F401 — imported for potential direct use
from rich.table import Table
from rich.text import Text  # noqa: F401

from ..encounters import _squad_breakdown, _squad_combat, form_squads
from ..harness import create_runner_pool
from .items import load_items
from .sim import SQUAD_NAMES, Squad, make_squad, run_zone
from .zones import ZONES


console = Console()

DEFAULT_POOL_SIZE = 27   # 9 squads of 3 → 3 squads per zone


# ---------------------------------------------------------------------------
# STYLE MAPS
# ---------------------------------------------------------------------------
DOCTRINE_STYLE: dict[str, str] = {
    "GREEDY":   "bold red",
    "CAUTIOUS": "bold cyan",
    "BALANCED": "white",
    "SUPPORT":  "bold blue",
}

TIER_STYLE: dict[str, str] = {
    "COMMON":   "dim white",
    "UNCOMMON": "green",
    "RARE":     "yellow",
    "EPIC":     "magenta",
}

SHELL_STYLE: dict[str, str] = {
    "Destroyer": "red",
    "Assassin":  "red",
    "Thief":     "cyan",
    "Recon":     "cyan",
    "Vandal":    "white",
    "Rook":      "white",
    "Triage":    "blue",
}

ZONE_STYLE: dict[str, str] = {
    "Sector 7":   "bright_green",
    "Deep Reach": "yellow",
    "The Shelf":  "bright_red",
}


# ---------------------------------------------------------------------------
# SQUAD DISTRIBUTION
# ---------------------------------------------------------------------------
def _distribute_squads_to_zones(squads: list[Squad], zone_count: int) -> list[list[Squad]]:
    """Random distribution of squads across N zones. Remainders favour earlier zones."""
    shuffled = list(squads)
    random.shuffle(shuffled)
    bins: list[list[Squad]] = [[] for _ in range(zone_count)]
    for idx, squad in enumerate(shuffled):
        bins[idx % zone_count].append(squad)
    return bins


# ---------------------------------------------------------------------------
# RICH RENDERERS
# ---------------------------------------------------------------------------
def _print_zone_header(zone, zone_squads: list[Squad]) -> None:
    zone_color = ZONE_STYLE.get(zone.name, "white")
    squad_labels = "  ".join(
        f"[{DOCTRINE_STYLE[s.doctrine.value.upper()]}]{s.name}[/]"
        for s in zone_squads
    )
    console.print()
    console.rule(
        f"[bold {zone_color}]{zone.name}[/]  "
        f"[dim]difficulty {zone.difficulty}  ·  pool {zone.pool_size} items[/]",
        style=zone_color,
    )
    console.print(f"  Squads: {squad_labels}")
    console.print()


def _print_squad_table(squad: Squad) -> None:
    breakdown = _squad_breakdown(squad.runners)
    squad_cbt = _squad_combat(breakdown)
    squad_ext = float(breakdown[:, 1].sum())
    doctrine  = squad.doctrine.value.upper()

    table = Table(
        title=f"[bold]{squad.name}[/bold]  [{DOCTRINE_STYLE[doctrine]}]{doctrine}[/]",
        title_justify="left",
        header_style="bold dim",
        border_style="dim",
        show_lines=False,
        min_width=58,
    )
    table.add_column("Runner",  min_width=10)
    table.add_column("Shell",   min_width=10)
    table.add_column("C",       justify="right", width=5, style="red")
    table.add_column("E",       justify="right", width=5, style="cyan")
    table.add_column("S",       justify="right", width=5, style="blue")
    table.add_column("eff_cbt", justify="right", width=8)
    table.add_column("eff_ext", justify="right", width=8)

    for runner, row in zip(squad.runners, breakdown):
        eff_cbt, eff_ext = float(row[0]), float(row[1])
        shell_style = SHELL_STYLE.get(runner.current_shell, "white")
        table.add_row(
            runner.name,
            f"[{shell_style}]{runner.current_shell}[/]",
            f"{runner.combat:.2f}",
            f"{runner.extraction:.2f}",
            f"{runner.support:.2f}",
            f"{eff_cbt:.3f}",
            f"{eff_ext:.3f}",
        )

    table.add_section()
    table.add_row(
        "[dim]SQUAD[/dim]", "",
        "", "", "",
        f"[bold]{squad_cbt:.3f}[/bold]",
        f"[bold]{squad_ext:.3f}[/bold]",
    )
    console.print(table)


def _render_log_line(line: str) -> str | None:
    """Convert a plain log line to Rich markup. Returns None to suppress the line."""
    # Zone header — handled by _print_zone_header
    if line.startswith("==="):
        return None

    # Squad entry summary — shown in zone header already
    if re.search(r"\[T0\] \d+ squads enter:", line):
        return None

    tick_m = re.match(r"(\[T\d+\])(.*)", line)
    if not tick_m:
        return line

    tick = f"[dim]{tick_m.group(1)}[/dim]"
    rest = tick_m.group(2)

    # Pool spawn
    if "Pool spawned:" in rest:
        return f"{tick}[dim]{rest}[/dim]"

    # Item found — highlight the tier name in-line
    if "found" in rest and "cr)" in rest:
        colored = rest
        for tier, style in TIER_STYLE.items():
            colored = colored.replace(f"({tier},", f"([{style}]{tier}[/{style}],")
        return f"{tick}[green]{colored}[/green]"

    # Both engage (precedes a combat block)
    if "cross paths — both engage" in rest:
        return f"{tick}[bold yellow]{rest}[/bold yellow]"

    # Disengage
    if "cross paths —" in rest:
        return f"{tick}[dim]{rest}[/dim]"

    # Combat header
    if re.match(r" Combat: .+ vs .+", rest):
        return f"{tick}[bold red]{rest}[/bold red]"

    # Per-squad breakdown rows (indented, contain "base:")
    if rest.startswith("  ") and "base:" in rest:
        return f"{tick}[dim red]{rest}[/dim red]"

    # Combat winner line
    if "→" in rest and "wins." in rest:
        return f"{tick}[bold red]{rest}[/bold red]"

    # Extraction (voluntary or end-of-run forced)
    if "extracts" in rest:
        return f"{tick}[bold cyan]{rest}[/bold cyan]"

    return f"{tick}{rest}"


def _print_match_log(log: list[str]) -> None:
    for line in log:
        rendered = _render_log_line(line)
        if rendered is not None:
            console.print(rendered, highlight=False)
    console.print()


def _print_final_summary(all_squads: list[Squad], zone_assignments: dict[str, str]) -> None:
    console.print()
    console.rule("[bold white]FINAL STANDINGS[/bold white]")
    console.print()

    table = Table(
        header_style="bold dim",
        border_style="dim",
        show_lines=False,
        min_width=72,
    )
    table.add_column("#",        width=3,  justify="right")
    table.add_column("Squad",    min_width=10)
    table.add_column("Zone",     min_width=12)
    table.add_column("Doctrine", min_width=10)
    table.add_column("Status",   min_width=10)
    table.add_column("Items",    width=6,  justify="right")
    table.add_column("Credits",  width=10, justify="right")

    sorted_squads = sorted(all_squads, key=lambda s: -s.loot.total_credits())
    for rank, squad in enumerate(sorted_squads, start=1):
        doctrine   = squad.doctrine.value.upper()
        zone_name  = zone_assignments.get(squad.name, "?")
        zone_color = ZONE_STYLE.get(zone_name, "white")
        credits    = squad.loot.total_credits()

        if squad.eliminated:
            status     = "[red]ELIMINATED[/red]"
            rank_style = "dim"
            cr_markup  = f"[dim]{credits:,}[/dim]"
        elif squad.extracted:
            status     = "[green]extracted[/green]"
            rank_style = "bold" if rank <= 3 else ""
            cr_markup  = f"[bold]{credits:,}[/bold]"
        else:
            status     = "[yellow]stranded[/yellow]"
            rank_style = ""
            cr_markup  = f"{credits:,}"

        rank_text = f"[{rank_style}]{rank}[/]" if rank_style else str(rank)
        name_text = f"[{rank_style}]{squad.name}[/]" if rank_style else squad.name

        table.add_row(
            rank_text,
            name_text,
            f"[{zone_color}]{zone_name}[/]",
            f"[{DOCTRINE_STYLE[doctrine]}]{doctrine}[/]",
            status,
            str(len(squad.loot.items)),
            cr_markup,
        )

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Tick-based zone simulation harness")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument(
        "--pool-size", type=int, default=DEFAULT_POOL_SIZE,
        help=f"Number of runners to create (default {DEFAULT_POOL_SIZE})",
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    # 1. Load item catalog
    items = load_items()
    console.print(f"\nLoaded [bold]{len(items)}[/bold] items from [dim]data/items.csv[/dim]\n")

    # 2. Build runner pool and form squads of 3
    runners = create_runner_pool(args.pool_size)
    raw_squads, sit_outs = form_squads(runners)
    if sit_outs:
        console.print(
            f"[dim](Note: {len(sit_outs)} runner(s) sat out — "
            f"pool not divisible by squad size.)[/dim]\n"
        )

    # 3. Wrap raw squads in zone_sim Squad objects with names + doctrines
    squads = [
        make_squad(SQUAD_NAMES[i % len(SQUAD_NAMES)], runners=members)
        for i, members in enumerate(raw_squads)
    ]

    # 4. Distribute across zones
    zone_bins = _distribute_squads_to_zones(squads, len(ZONES))
    zone_assignments: dict[str, str] = {}
    for zone, zone_squads in zip(ZONES, zone_bins):
        for squad in zone_squads:
            zone_assignments[squad.name] = zone.name

    # 5. Run each zone — print composition tables, then the match log
    all_squads: list[Squad] = []
    for zone, zone_squads in zip(ZONES, zone_bins):
        if not zone_squads:
            console.print(f"[dim]=== {zone.name} === (no squads assigned, skipping)[/dim]\n")
            continue

        _print_zone_header(zone, zone_squads)
        for squad in zone_squads:
            _print_squad_table(squad)
        console.print()

        result = run_zone(zone, zone_squads, items)
        _print_match_log(result.match_log)
        all_squads.extend(result.squads)

    # 6. Final standings
    _print_final_summary(all_squads, zone_assignments)


if __name__ == "__main__":
    main()
