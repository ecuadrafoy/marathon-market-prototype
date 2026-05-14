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
import random
from dataclasses import dataclass, field

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
    _hire_one,
    all_runners,
    collect_used_names,
    cull_dead_runners,
)
from runner_sim.market.company_strategy import (
    CompanyRosterEvents,
    RunnerIdCounter,
    collect_company_income,
    decide_acquisitions,
    decide_voluntary_drops,
    company_health,
    release_to_free_agents,
    resolve_bidding,
    settle_payroll,
    tick_free_agent_pool,
)
from runner_sim.market.shell_market import ShellMarket, update_prices, reequip_survivors
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
    co_squads_by_company maps company_name → zone_name → Squad; used to
    extract per-company, per-zone outcomes for debug display.
    roster_events maps company_name → CompanyRosterEvents for this week —
    deaths, signings, drops, orphans — so the UI can communicate timing.
    """
    company_results: list["CompanyWeekResult"]
    zone_results: dict[str, ZoneRunResult]
    co_squads_by_company: dict[str, dict[str, Squad]]
    roster_events: dict[str, CompanyRosterEvents] = field(default_factory=dict)


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

    # Survived the week — counts toward longevity-based upkeep value.
    runner.deployments_survived += 1

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
    credits_by_runner_id: dict[int, float] | None = None,
) -> None:
    """Apply per-runner updates for one squad after a zone run.

    If credits_by_runner_id is provided, also records each runner's per-week
    extraction credit share (used by company_strategy.collect_company_income).
    """
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
        credit = float(credit_shares[idx])
        apply_zone_outcome(
            runner,
            squad_extracted=squad.extracted,
            squad_eliminated=squad.eliminated,
            credits_received=credit,
            kills_attributed=int(kill_shares[idx]),
        )
        if credits_by_runner_id is not None and credit > 0.0:
            credits_by_runner_id[runner.id] = credits_by_runner_id.get(runner.id, 0.0) + credit


# ---------------------------------------------------------------------------
# COMPANY-LEVEL AGGREGATION
# ---------------------------------------------------------------------------
def _kills_from_events(
    events: list[CombatEvent],
    winner_squad_names: set[str],
) -> int:
    """Sum loser_runner_count for every event won by one of the named squads."""
    return sum(ev.loser_runner_count for ev in events if ev.winner_squad in winner_squad_names)


def _build_company_result(
    company_name: str,
    price_before: float,
    co_squads: dict[str, Squad],
    monitored_zone_name: str,
    zone_results: dict[str, ZoneRunResult],
    expected_squad_count: int | None = None,
) -> CompanyWeekResult:
    """Aggregate one company's per-zone squad outcomes into a CompanyWeekResult.

    Critical guard: only credit-counted from extracted squads. Eliminated
    squads' loot is forfeit (they died with it; survivors plundered Uncommon+
    via kill-loot, which is already merged into the winner's squad.loot).

    Kill counts come from CombatEvent.loser_runner_count — the authoritative
    per-week source — rather than runner.eliminations, which is a lifetime
    career total and would show cumulative kills across all past weeks.
    """
    squads_returned = sum(1 for sq in co_squads.values() if sq.extracted)
    squads_eliminated = sum(1 for sq in co_squads.values() if sq.eliminated)

    total_credits = sum(
        float(sq.loot.total_credits()) for sq in co_squads.values() if sq.extracted
    )

    # Weekly kills: count runners eliminated by this company's squads across all zones.
    company_squad_names = {sq.name for sq in co_squads.values()}
    total_eliminations = sum(
        _kills_from_events(zone_results[z].combat_events, company_squad_names)
        for z in zone_results
    )

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

    # Weekly kills for the monitored squad specifically.
    if monitored_squad is not None and monitored_zone_name in zone_results:
        monitored_kills = _kills_from_events(
            zone_results[monitored_zone_name].combat_events,
            {monitored_squad.name},
        )
    else:
        monitored_kills = 0

    # Baseline reflects market expectation: the company "should have" fielded
    # expected_squad_count squads. Under-deploying due to roster shortfall is
    # visibly punished here — the market doesn't care why you fell short.
    expected = expected_squad_count if expected_squad_count is not None else len(co_squads)
    baseline = compute_baseline(squads_deployed=expected)
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
        monitored_eliminations=monitored_kills,
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
    companies: list | None = None,
    free_agents: list | None = None,
    id_supplier: RunnerIdCounter | None = None,
    price_histories: dict[str, list[float]] | None = None,
    rng: random.Random | None = None,
) -> WeekSimulationResult:
    """Run one full week of the integrated simulation.

    Args:
        rosters:         company name → CompanyRoster (mutated: payroll, signings)
        market:          shell market (mutated: prices updated end-of-week)
        zones:           list of Zones to run (typically all 3)
        item_catalog:    loaded item list
        company_prices:  optional {company_name: current_stock_price}; seeds
                         CompanyWeekResult.price_before. If None, calibration
                         mode — price_before = 0.0 for everyone and the
                         company-AI loop is skipped.
        companies:       list[Company] — required when running the AI loop.
                         Used for budget mutation and health classification.
        free_agents:     mutable list[Runner] — closed-pool reserve, mutated
                         each week (orphaning + bidding + retirement).
        id_supplier:     RunnerIdCounter — shared across rosters + free agents
                         so newly spawned rookies get globally unique ids.
        price_histories: per-company price history (last 4+ entries needed
                         for the struggling/thriving signal).
        rng:             random.Random used for bidding-draft order. Threaded
                         for determinism under seeded tests.

    Returns:
        WeekSimulationResult containing the player-facing company_results
        and the engine-internal zone_results (full ZoneRunResult per zone).
    """
    ai_enabled = companies is not None and free_agents is not None and id_supplier is not None
    rng = rng or random.Random()
    used_names = collect_used_names(rosters)
    if free_agents is not None:
        used_names.update(r.name for r in free_agents)

    # Per-company event log — populated through the week's phases. Even in
    # calibration mode we initialise the dict so callers can access it safely.
    roster_events: dict[str, CompanyRosterEvents] = {
        name: CompanyRosterEvents() for name in rosters
    }

    # --- 1. Build per-zone deployment lists from the CURRENT rosters ---
    # Rosters of <6 sit out the week — the company is too broke to field anyone.
    # This is the "deploy what you have" phase: AI doesn't make headcount
    # decisions until AFTER it sees this week's outcome (post-deployment).
    from runner_sim.market.deployment import MIN_ROSTER_FOR_DEPLOYMENT
    deployments: dict[str, list[tuple[str, Squad]]] = {z.name: [] for z in zones}
    co_squads_by_company: dict[str, dict[str, Squad]] = {}
    for company_name, roster in rosters.items():
        if len(roster.runners) < MIN_ROSTER_FOR_DEPLOYMENT:
            co_squads_by_company[company_name] = {}
            continue
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
    credits_by_runner_id: dict[int, float] = {}
    for zone in zones:
        events = zone_results[zone.name].combat_events
        for (_, squad) in deployments[zone.name]:
            _update_runners_for_squad(squad, events, credits_by_runner_id)

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
            zone_results=zone_results,
            expected_squad_count=len(zones),
        ))

    # --- 5. Route dead runners. In AI mode: → free-agent pool (closed-pool model,
    # bodies destroyed, consciousness preserved). In calibration mode: replace with
    # fresh hires to keep rosters at full size — preserves the steady-state
    # assumptions baked into pricing.py's calibrated constants.
    if ai_enabled:
        for company_name, roster in rosters.items():
            dead = cull_dead_runners(roster)
            for r in dead:
                roster_events[company_name].died.append(r.name)
                release_to_free_agents(r, free_agents)
    else:
        used_names_calib = collect_used_names(rosters)
        for roster in rosters.values():
            dead = cull_dead_runners(roster)
            for _ in dead:
                new_id = roster.next_runner_id
                roster.next_runner_id += 1
                roster.runners.append(
                    _hire_one(roster.company_name, new_id, market, used_names_calib)
                )
            used_names_calib = collect_used_names(rosters)

    # --- 6. Company AI cycle — runs AFTER deployment so decisions see outcomes.
    # Order matches the natural employment cycle: earn → pay → adjust headcount.
    if ai_enabled:
        # 6a. Income — credits from this week's extraction fund this week's payroll.
        for company in companies:
            collect_company_income(company, rosters[company.name], credits_by_runner_id)

        # 6b. Settle payroll — runners we can't afford go to the free-agent pool.
        for company in companies:
            roster = rosters[company.name]
            _kept, orphaned = settle_payroll(company, roster)
            for r in orphaned:
                roster_events[company.name].orphaned_unaffordable.append(r.name)
                release_to_free_agents(r, free_agents)

        # 6c. Voluntary drops — struggling companies dump expensive runners
        # to make room for cheaper rookies next week.
        for company in companies:
            history = (price_histories or {}).get(company.name, [])
            health = company_health(history)
            drops = decide_voluntary_drops(company, rosters[company.name], health)
            for r in drops:
                roster_events[company.name].voluntarily_dropped.append(r.name)
                release_to_free_agents(r, free_agents)

        # 6d. Age the free-agent pool — retire idle, spawn rookies if needed.
        # Happens after orphan/drop so this week's releases get weeks_orphaned=0
        # rather than 1 immediately.
        total_employed = sum(len(r.runners) for r in rosters.values())
        tick_free_agent_pool(
            free_agents=free_agents,
            total_employed=total_employed,
            market=market,
            used_names=used_names,
            id_supplier=id_supplier,
        )

        # 6e. Bidding draft — each company signs from the FA pool to refill.
        bids_by_company: dict[str, list[tuple]] = {}
        for company in companies:
            history = (price_histories or {}).get(company.name, [])
            health = company_health(history)
            roster = rosters[company.name]
            slots_needed = 9 - len(roster.runners)
            bids_by_company[company.name] = decide_acquisitions(
                company, roster, free_agents, health, slots_needed
            )
        signed = resolve_bidding(
            companies=companies,
            rosters=rosters,
            free_agents=free_agents,
            bids_by_company=bids_by_company,
            rng=rng,
            target_roster_size=9,
        )
        for co_name, runners_signed in signed.items():
            for r in runners_signed:
                roster_events[co_name].signed.append(r.name)

        # 6f. Re-equip any roster runner without a shell (signed free agents or
        # rookies arriving with current_shell=""). Reuses the existing shell market.
        from runner_sim.market.shell_market import choose_affordable_shell
        for roster in rosters.values():
            for r in roster.runners:
                if not r.current_shell:
                    shell = choose_affordable_shell(r, market.prices, r.credit_balance)
                    r.current_shell = shell.name
                    r.credit_balance = max(r.credit_balance - market.prices[shell.name], 0.0)

    # --- 7. Weekly re-equip: surviving runners upgrade to best affordable shell ---
    reequip_survivors(all_runners(rosters), market)

    # --- 8. Update shell market based on new (post-recruitment) adoption ---
    update_prices(market, all_runners(rosters))

    return WeekSimulationResult(
        company_results=results,
        zone_results=zone_results,
        co_squads_by_company=co_squads_by_company,
        roster_events=roster_events,
    )
