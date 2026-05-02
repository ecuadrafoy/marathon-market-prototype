"""
The week orchestrator. Replaces marathon_market._apply_yield with a
zone-sim-driven flow:

  1. Build per-zone deployment lists from each company's assigned squads.
  2. Run zone_sim.run_zone() for each zone.
  3. Aggregate per-company outcomes into CompanyWeekResult.
  4. Update runner career state (drift, affinity, kill/death stats, credit_balance).
  5. Recruit replacements for runners whose squads were eliminated.
  6. Update shell market prices based on the new roster composition.

The split between simulate_week (orchestration) and apply_zone_outcome
(per-runner state update) keeps the per-runner math testable in isolation.
"""

from __future__ import annotations
from dataclasses import dataclass

import numpy as np

from runner_sim.runners import (
    Runner,
    SHELL_BY_NAME,
    drift_attributes,
    gain_affinity,
)
from runner_sim.encounters import (
    _distribute_extraction,
    _distribute_eliminations,
    _squad_breakdown,
)
from runner_sim.market.deployment import assign_squads
from runner_sim.market.pricing import (
    CompanyWeekResult,
    compute_baseline,
    compute_price_change_pct,
)
from runner_sim.market.roster import (
    CompanyRoster,
    all_runners,
    collect_used_names,
    replace_dead_runners,
)
from runner_sim.market.shell_market import ShellMarket, update_prices
from runner_sim.zone_sim.items import Item
from runner_sim.zone_sim.sim import (
    CombatEvent,
    Squad,
    ZoneRunResult,
    run_zone,
)
from runner_sim.zone_sim.zones import Zone


# ---------------------------------------------------------------------------
# RESULT BUNDLE
# ---------------------------------------------------------------------------
@dataclass
class WeekSimulationResult:
    """Bundle of everything produced by one call to simulate_week.

    company_results is the player-facing summary (asymmetric — only monitored
    zone is named). zone_results is the full engine state including hidden
    zones; intended for debug display, charts, and analysis.
    """
    company_results: list["CompanyWeekResult"]
    zone_results: dict[str, ZoneRunResult]


# ---------------------------------------------------------------------------
# PER-RUNNER STATE UPDATES
# ---------------------------------------------------------------------------
def apply_zone_outcome(
    runner: Runner,
    squad_extracted: bool,
    squad_eliminated: bool,
    credits_received: float,
    kills_attributed: int,
) -> None:
    """Update a single runner's career state from one week's zone outcome.

    Three cases:
      - squad_eliminated → runner dies. Mark _died_this_week sentinel for
        replace_dead_runners. No drift, no affinity, no credit credit.
      - squad_extracted → runner survived & extracted. Full update path:
        career stats + credit_balance + affinity + drift.
      - neither (shouldn't happen given run_zone forces end-of-run extraction):
        treated as "participated, survived, no loot" — drift but no credits.
    """
    runner.extraction_attempts += 1

    if squad_eliminated:
        runner.death_count += 1
        runner._died_this_week = True   # sentinel for replace_dead_runners
        # Per design: dead runners do NOT drift, do NOT gain affinity, do NOT
        # receive credit. Their record is appended to shell_history once so
        # the timeline is consistent with surviving runners.
        runner.shell_history.append(runner.current_shell)
        return

    if squad_extracted:
        runner.extraction_successes += 1
        runner.net_loot += credits_received        # lifetime career stat
        runner.credit_balance += credits_received  # spendable budget
        runner.eliminations += kills_attributed
        gain_affinity(runner, runner.current_shell)
        drift_attributes(runner, SHELL_BY_NAME[runner.current_shell])
    else:
        # Defensive fallback: squad didn't die but didn't extract.
        # run_zone forces extraction at end-of-run for active squads, so
        # this branch shouldn't fire today. If it ever does, treat as a
        # participation-only week (drift but no credit).
        gain_affinity(runner, runner.current_shell)
        drift_attributes(runner, SHELL_BY_NAME[runner.current_shell])

    runner.shell_history.append(runner.current_shell)


def _update_runners_for_squad(
    squad: Squad,
    combat_events: list[CombatEvent],
) -> None:
    """Apply per-runner updates for one squad after a zone run."""
    breakdown = _squad_breakdown(squad.runners)

    # Per-runner credit share. Distribute squad.loot.total_credits() across
    # runners proportional to eff_extraction. Mirrors _distribute_extraction
    # but reuses the actual extracted credit total (not the formula's BASE+...).
    # Eliminated squads forfeit all loot regardless of items in squad.loot.
    if squad.extracted:
        total_credits = float(squad.loot.total_credits())
        eff_extraction = breakdown[:, 1]
        sum_extraction = float(eff_extraction.sum())
        if sum_extraction > 0:
            credit_shares = total_credits * eff_extraction / sum_extraction
        else:
            # Fallback: equal split (extreme corner — squad has no extraction stat at all)
            credit_shares = np.full(len(squad.runners), total_credits / len(squad.runners))
    else:
        credit_shares = np.zeros(len(squad.runners))

    # Per-runner kill attribution. Each combat event where this squad was
    # the winner contributes loser_runner_count kills, distributed by combat.
    kill_shares = np.zeros(len(squad.runners), dtype=int)
    for event in combat_events:
        if event.winner_squad == squad.name:
            event_kills = _distribute_eliminations(event.loser_runner_count, breakdown)
            kill_shares += event_kills

    for idx, runner in enumerate(squad.runners):
        apply_zone_outcome(
            runner,
            squad_extracted=squad.extracted,
            squad_eliminated=squad.eliminated,
            credits_received=float(credit_shares[idx]),
            kills_attributed=int(kill_shares[idx]),
        )


# ---------------------------------------------------------------------------
# COMPANY-LEVEL AGGREGATION
# ---------------------------------------------------------------------------
def _build_company_result(
    company_name: str,
    price_before: float,
    co_squads: dict[str, Squad],
    monitored_zone_name: str,
) -> CompanyWeekResult:
    """Aggregate one company's per-zone squad outcomes into a CompanyWeekResult.

    Critical guard: only credit-counted from extracted squads. Eliminated
    squads' loot is forfeit (they died with it; survivors plundered Uncommon+
    via kill-loot, which is already merged into the winner's squad.loot).
    """
    squads_returned = sum(1 for sq in co_squads.values() if sq.extracted)
    squads_eliminated = sum(1 for sq in co_squads.values() if sq.eliminated)

    total_credits = sum(
        float(sq.loot.total_credits()) for sq in co_squads.values() if sq.extracted
    )
    total_eliminations = sum(
        sum(r.eliminations for r in sq.runners) for sq in co_squads.values()
    )
    # Note: r.eliminations was incremented inside apply_zone_outcome; it
    # accumulates lifetime career kills. For the *weekly* total we'd want a
    # delta, but tracking lifetime here is fine for display since we only
    # use total_eliminations for player-facing flavor (not the price signal).

    monitored_squad = co_squads.get(monitored_zone_name)
    monitored_credits = (
        float(monitored_squad.loot.total_credits())
        if monitored_squad is not None and monitored_squad.extracted
        else 0.0
    )
    monitored_runner_names = (
        [f"{r.name}/{r.current_shell[:3]}" for r in monitored_squad.runners]
        if monitored_squad is not None
        else []
    )

    baseline = compute_baseline(squads_deployed=len(co_squads))
    delta = total_credits - baseline
    pct = compute_price_change_pct(total_credits, baseline)
    price_after = max(price_before * (1.0 + pct / 100.0), 1.0)  # PRICE_FLOOR=1.0

    return CompanyWeekResult(
        company_name=company_name,
        squads_deployed=len(co_squads),
        squads_returned=squads_returned,
        squads_eliminated=squads_eliminated,
        total_credits_extracted=total_credits,
        total_eliminations=total_eliminations,
        baseline=baseline,
        delta=delta,
        price_change_pct=pct,
        price_before=price_before,
        price_after=price_after,
        monitored_squad_returned=(
            monitored_squad is not None and monitored_squad.extracted
        ),
        monitored_credits=monitored_credits,
        monitored_eliminations=(
            sum(r.eliminations for r in monitored_squad.runners)
            if monitored_squad is not None
            else 0
        ),
        monitored_runner_names=monitored_runner_names,
    )


# ---------------------------------------------------------------------------
# MAIN ORCHESTRATOR
# ---------------------------------------------------------------------------
def simulate_week(
    rosters: dict[str, CompanyRoster],
    market: ShellMarket,
    zones: list[Zone],
    item_catalog: list[Item],
    company_prices: dict[str, float] | None = None,
) -> WeekSimulationResult:
    """Run one full week of the integrated simulation.

    Args:
        rosters:        company name → CompanyRoster (mutated: deaths, recruits)
        market:         shell market (mutated: prices updated end-of-week)
        zones:          list of Zones to run (typically all 3)
        item_catalog:   loaded item list
        company_prices: optional {company_name: current_stock_price}; used to
                        seed CompanyWeekResult.price_before. If None, every
                        company's price_before is set to 0.0 (calibration mode).

    Returns:
        WeekSimulationResult containing the player-facing company_results
        and the engine-internal zone_results (full ZoneRunResult per zone,
        including match_log and combat_events).
    """
    # --- 1. Build per-zone deployment lists ---
    deployments: dict[str, list[tuple[str, Squad]]] = {z.name: [] for z in zones}
    co_squads_by_company: dict[str, dict[str, Squad]] = {}
    for company_name, roster in rosters.items():
        zone_to_squad = assign_squads(roster, zones)
        co_squads_by_company[company_name] = zone_to_squad
        for zone_name, squad in zone_to_squad.items():
            deployments[zone_name].append((company_name, squad))

    # --- 2. Run each zone ---
    zone_results: dict[str, ZoneRunResult] = {}
    for zone in zones:
        squads_in_zone = [sq for (_, sq) in deployments[zone.name]]
        zone_results[zone.name] = run_zone(zone, squads_in_zone, item_catalog)

    # --- 3. Per-runner state updates (drift, affinity, kill/death, credit) ---
    # Iterate per zone so the right combat_events are passed for kill attribution.
    for zone in zones:
        events = zone_results[zone.name].combat_events
        for (_, squad) in deployments[zone.name]:
            _update_runners_for_squad(squad, events)

    # --- 4. Aggregate per-company results ---
    monitored_zone_names = [z.name for z in zones if z.monitored]
    monitored = monitored_zone_names[0] if monitored_zone_names else zones[0].name

    results: list[CompanyWeekResult] = []
    for company_name, roster in rosters.items():
        price_before = (
            company_prices[company_name] if company_prices is not None else 0.0
        )
        results.append(_build_company_result(
            company_name=company_name,
            price_before=price_before,
            co_squads=co_squads_by_company[company_name],
            monitored_zone_name=monitored,
        ))

    # --- 5. Replace dead runners ---
    used_names = collect_used_names(rosters)
    for roster in rosters.values():
        replace_dead_runners(roster, market, used_names)
        # Refresh used_names — a death-replacement pass may have added new names.
        used_names = collect_used_names(rosters)

    # --- 6. Update shell market based on new (post-recruitment) adoption ---
    update_prices(market, all_runners(rosters))

    return WeekSimulationResult(company_results=results, zone_results=zone_results)
