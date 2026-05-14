"""
Tests for runner_sim/market/company_strategy.py

Covers each public function in the v1 company-AI module:
  - compute_upkeep        — earned-value formula, three orthogonal axes
  - settle_payroll        — cheapest-first ordering, orphan semantics, affinity preserved
  - company_health        — 3-week MA boundaries
  - decide_voluntary_drops— struggling drops > 2× median; thriving keeps everyone
  - decide_acquisitions   — health-driven preference & bid amount
  - resolve_bidding       — sequential draft with budget contention
  - tick_free_agent_pool  — retirement at 8 weeks; rookie spawn floor
  - release_to_free_agents— preserves career stats and affinities
"""

from __future__ import annotations
import random
from dataclasses import dataclass

import pytest

from runner_sim.runners import Runner
from runner_sim.market.roster import CompanyRoster, create_roster
from runner_sim.market.shell_market import make_initial_market
from runner_sim.market.company_strategy import (
    BASE_UPKEEP,
    CREDIT_SHARE_TO_COMPANY,
    INITIAL_FREE_AGENT_BENCH,
    MIN_GLOBAL_POOL,
    ORPHAN_RETIRE_AFTER_WEEKS,
    RunnerIdCounter,
    collect_company_income,
    company_health,
    compute_upkeep,
    decide_acquisitions,
    decide_voluntary_drops,
    refresh_upkeep,
    release_to_free_agents,
    resolve_bidding,
    settle_payroll,
    tick_free_agent_pool,
)


# ---------------------------------------------------------------------------
# TEST HELPERS
# ---------------------------------------------------------------------------
@dataclass
class FakeCompany:
    """Minimal stand-in for marathon_market.Company to avoid import cycles."""
    name: str
    price: float = 100.0
    budget: float = 0.0


def _make_runner(
    runner_id: int = 1,
    name: str = "Test",
    company_name: str = "TestCo",
    shell: str = "Recon",
    net_loot: float = 0.0,
    eliminations: int = 0,
    deployments_survived: int = 0,
    affinities: dict | None = None,
) -> Runner:
    r = Runner(
        id=runner_id,
        name=name,
        company_name=company_name,
        combat=0.33, extraction=0.34, support=0.33,
        current_shell=shell,
    )
    r.net_loot = net_loot
    r.eliminations = eliminations
    r.deployments_survived = deployments_survived
    if affinities is not None:
        for shell_name, value in affinities.items():
            r.shell_affinities[shell_name] = value
    return r


def _populate_roster(runners: list[Runner]) -> CompanyRoster:
    roster = CompanyRoster(company_name="TestCo")
    roster.runners = runners
    return roster


# ---------------------------------------------------------------------------
# compute_upkeep — three orthogonal axes of earned value
# ---------------------------------------------------------------------------
class TestComputeUpkeep:
    def test_rookie_costs_base(self):
        """A brand-new runner with zero career stats costs exactly BASE_UPKEEP."""
        r = _make_runner()
        assert compute_upkeep(r) == pytest.approx(BASE_UPKEEP)

    def test_monotonic_in_net_loot(self):
        a = _make_runner(net_loot=0.0)
        b = _make_runner(net_loot=2000.0)
        assert compute_upkeep(b) > compute_upkeep(a)

    def test_monotonic_in_eliminations(self):
        a = _make_runner(eliminations=0)
        b = _make_runner(eliminations=10)
        assert compute_upkeep(b) > compute_upkeep(a)

    def test_monotonic_in_deployments_survived(self):
        """Longevity axis — orthogonal to combat/extraction."""
        a = _make_runner(deployments_survived=0)
        b = _make_runner(deployments_survived=20)
        assert compute_upkeep(b) > compute_upkeep(a)

    def test_long_serving_support_is_valuable_without_combat(self):
        """The motivating case for adding deployments_survived to the formula:
        a Triage who's never gotten a kill or extracted much can still command
        meaningful upkeep just by *not dying*."""
        veteran_support = _make_runner(
            shell="Triage",
            net_loot=500.0,        # barely any extraction credit
            eliminations=1,         # almost no kills
            deployments_survived=35,
        )
        assert compute_upkeep(veteran_support) > 100.0

    def test_elite_costs_substantially_more_than_rookie(self):
        elite = _make_runner(
            shell="Destroyer",
            net_loot=8000.0,
            eliminations=30,
            deployments_survived=40,
        )
        rookie = _make_runner()
        assert compute_upkeep(elite) > 250.0
        assert compute_upkeep(elite) > 10 * compute_upkeep(rookie)

    def test_shell_does_not_affect_upkeep(self):
        """Earned-value formula: a Destroyer and a Recon with identical stats
        cost the same. No shell-tier multiplier in v1."""
        destroyer = _make_runner(shell="Destroyer", net_loot=2000, eliminations=8)
        recon     = _make_runner(shell="Recon",     net_loot=2000, eliminations=8)
        assert compute_upkeep(destroyer) == pytest.approx(compute_upkeep(recon))


# ---------------------------------------------------------------------------
# settle_payroll — cheapest first, orphans preserve affinity
# ---------------------------------------------------------------------------
class TestSettlePayroll:
    def test_all_kept_when_budget_covers_everyone(self):
        company = FakeCompany("TestCo", budget=300.0)
        r1 = _make_runner(runner_id=1, net_loot=0)             # ~15
        r2 = _make_runner(runner_id=2, net_loot=2000)          # ~45
        r3 = _make_runner(runner_id=3, net_loot=5000)          # ~90
        roster = _populate_roster([r1, r2, r3])

        kept, orphaned = settle_payroll(company, roster)
        assert orphaned == []
        assert {r.id for r in kept} == {1, 2, 3}
        assert company.budget < 300.0
        assert company.budget > 0   # didn't spend everything

    def test_orphans_the_unaffordable_when_budget_tight(self):
        """Only the cheapest fit; the expensive runner is orphaned."""
        company = FakeCompany("TestCo", budget=80.0)
        cheap1 = _make_runner(runner_id=1, net_loot=0)         # ~15
        cheap2 = _make_runner(runner_id=2, net_loot=2000)      # ~45
        expensive = _make_runner(runner_id=3, eliminations=50, deployments_survived=50)  # ~215
        roster = _populate_roster([cheap1, cheap2, expensive])

        kept, orphaned = settle_payroll(company, roster)
        assert {r.id for r in kept} == {1, 2}
        assert [r.id for r in orphaned] == [3]
        assert roster.runners == kept   # roster mutated to match kept list

    def test_orphaned_runner_keeps_shell_affinities(self):
        """The whole point: affinity is preserved on orphaning. A rival can
        sign this runner and immediately benefit from their specialization."""
        company = FakeCompany("TestCo", budget=20.0)  # only covers 1 runner
        v = _make_runner(runner_id=1, net_loot=8000, eliminations=30, deployments_survived=40,
                         affinities={"Destroyer": 0.8, "Assassin": 0.3})
        cheap = _make_runner(runner_id=2)
        roster = _populate_roster([v, cheap])

        _kept, orphaned = settle_payroll(company, roster)
        assert orphaned[0].id == 1
        assert orphaned[0].shell_affinities["Destroyer"] == pytest.approx(0.8)
        assert orphaned[0].shell_affinities["Assassin"] == pytest.approx(0.3)
        assert orphaned[0].net_loot == 8000  # career stats also intact

    def test_cheapest_first_ordering(self):
        """One elite cannot bankrupt the entire roster. If we paid expensive-
        first, a single high-upkeep runner could eat the whole budget."""
        company = FakeCompany("TestCo", budget=50.0)
        elite = _make_runner(runner_id=1, eliminations=50, deployments_survived=50)  # ~215
        cheap1 = _make_runner(runner_id=2)
        cheap2 = _make_runner(runner_id=3)
        roster = _populate_roster([elite, cheap1, cheap2])

        kept, orphaned = settle_payroll(company, roster)
        # Cheapest-first: both cheaps stay, elite is orphaned.
        assert {r.id for r in kept} == {2, 3}
        assert [r.id for r in orphaned] == [1]


# ---------------------------------------------------------------------------
# company_health — 3-week MA boundary
# ---------------------------------------------------------------------------
class TestCompanyHealth:
    def test_thriving_when_above_threshold(self):
        history = [100.0, 100.0, 100.0, 110.0]
        assert company_health(history) == "thriving"

    def test_struggling_when_below_threshold(self):
        history = [100.0, 100.0, 100.0, 90.0]
        assert company_health(history) == "struggling"

    def test_neutral_inside_band(self):
        history = [100.0, 100.0, 100.0, 101.0]
        assert company_health(history) == "neutral"

    def test_neutral_with_insufficient_history(self):
        assert company_health([100.0]) == "neutral"
        assert company_health([100.0, 100.0]) == "neutral"

    def test_neutral_when_ma_is_zero(self):
        """Defensive: a zero-price MA shouldn't crash (rare PRICE_FLOOR edge)."""
        assert company_health([0.0, 0.0, 0.0, 1.0]) == "neutral"


# ---------------------------------------------------------------------------
# decide_voluntary_drops — struggling dumps > 2× median upkeep
# ---------------------------------------------------------------------------
class TestDecideVoluntaryDrops:
    def test_thriving_drops_no_one(self):
        company = FakeCompany("TestCo")
        runners = [_make_runner(runner_id=i, net_loot=i*1000) for i in range(1, 6)]
        for r in runners:
            refresh_upkeep(r)
        roster = _populate_roster(runners)
        assert decide_voluntary_drops(company, roster, "thriving") == []

    def test_neutral_drops_no_one(self):
        company = FakeCompany("TestCo")
        runners = [_make_runner(runner_id=i, net_loot=i*1000) for i in range(1, 6)]
        for r in runners:
            refresh_upkeep(r)
        roster = _populate_roster(runners)
        assert decide_voluntary_drops(company, roster, "neutral") == []

    def test_struggling_drops_above_2x_median(self):
        company = FakeCompany("TestCo")
        cheap     = _make_runner(runner_id=1, net_loot=0)                   # ~15
        median    = _make_runner(runner_id=2, net_loot=2000)                # ~45
        expensive = _make_runner(runner_id=3, eliminations=50,
                                 deployments_survived=50)                   # ~215
        for r in (cheap, median, expensive):
            refresh_upkeep(r)
        roster = _populate_roster([cheap, median, expensive])

        drops = decide_voluntary_drops(company, roster, "struggling")
        assert [r.id for r in drops] == [3]
        assert expensive not in roster.runners


# ---------------------------------------------------------------------------
# decide_acquisitions — health-driven preference & bid
# ---------------------------------------------------------------------------
class TestDecideAcquisitions:
    def test_struggling_picks_cheap_and_safe(self):
        company = FakeCompany("TestCo", budget=500.0)
        # roster has one mid-cost runner so the median is meaningful
        anchor = _make_runner(runner_id=99, net_loot=2000)
        refresh_upkeep(anchor)
        roster = _populate_roster([anchor])

        cheap_safe  = _make_runner(runner_id=1, affinities={"Recon": 0.7})
        cheap_combat = _make_runner(runner_id=2, affinities={"Destroyer": 0.7})
        expensive   = _make_runner(runner_id=3, eliminations=50, deployments_survived=50,
                                   affinities={"Recon": 0.9})  # > 1.1× median

        bids = decide_acquisitions(
            company, roster, [cheap_safe, cheap_combat, expensive],
            "struggling", max_slots=2,
        )
        bid_ids = [r.id for (r, _) in bids]
        # Expensive runner exceeds the cap → never appears.
        assert 3 not in bid_ids
        # Safe affinity preferred over aggressive when both are cheap.
        assert bid_ids[0] == 1

    def test_thriving_picks_high_affinity_and_bids_higher(self):
        company = FakeCompany("TestCo", budget=2000.0)
        roster = _populate_roster([])

        rookie = _make_runner(runner_id=1)
        veteran = _make_runner(runner_id=2, deployments_survived=20,
                               affinities={"Destroyer": 0.7})

        bids = decide_acquisitions(
            company, roster, [rookie, veteran], "thriving", max_slots=2,
        )
        # Thriving company prioritizes the high-affinity veteran.
        assert bids[0][0].id == 2
        # Bid amount is 1.5× upkeep (outbids struggling/neutral rivals).
        assert bids[0][1] == pytest.approx(1.5 * veteran.upkeep_cost)

    def test_neutral_picks_cheapest(self):
        company = FakeCompany("TestCo", budget=500.0)
        roster = _populate_roster([])
        a = _make_runner(runner_id=1, net_loot=5000)   # expensive
        b = _make_runner(runner_id=2)                  # cheap
        c = _make_runner(runner_id=3, net_loot=2000)   # mid
        bids = decide_acquisitions(company, roster, [a, b, c], "neutral", max_slots=2)
        assert [r.id for (r, _) in bids] == [2, 3]

    def test_no_slots_no_bids(self):
        company = FakeCompany("TestCo", budget=500.0)
        roster = _populate_roster([])
        assert decide_acquisitions(company, roster, [_make_runner()], "neutral", 0) == []


# ---------------------------------------------------------------------------
# resolve_bidding — sequential draft with contention
# ---------------------------------------------------------------------------
class TestResolveBidding:
    def test_higher_bid_wins_contention(self):
        """Two companies want the same runner. Whichever fires first under the
        shuffled order pays their bid. Subsequent companies fall to runner-up."""
        co_a = FakeCompany("A", budget=1000.0)
        co_b = FakeCompany("B", budget=1000.0)
        rosters = {"A": _populate_roster([]), "B": _populate_roster([])}
        target = _make_runner(runner_id=1, net_loot=3000)
        spare  = _make_runner(runner_id=2)
        for r in (target, spare):
            refresh_upkeep(r)
        free_agents = [target, spare]

        bids = {
            "A": [(target, target.upkeep_cost), (spare, spare.upkeep_cost)],
            "B": [(target, target.upkeep_cost * 1.5), (spare, spare.upkeep_cost)],
        }
        # Force deterministic shuffle order
        rng = random.Random(0)
        signed = resolve_bidding(
            [co_a, co_b], rosters, free_agents, bids, rng, target_roster_size=9,
        )

        all_signed_ids = {r.id for signs in signed.values() for r in signs}
        assert all_signed_ids == {1, 2}
        # Exactly one company got the target; both signed someone.
        assert free_agents == []

    def test_runner_company_name_updated_on_signing(self):
        co = FakeCompany("Alpha", budget=200.0)
        rosters = {"Alpha": _populate_roster([])}
        r = _make_runner(runner_id=1, company_name="(orphan)")
        refresh_upkeep(r)
        bids = {"Alpha": [(r, r.upkeep_cost)]}
        free_agents = [r]
        resolve_bidding([co], rosters, free_agents, bids, random.Random(0), 9)
        assert r.company_name == "Alpha"
        assert r in rosters["Alpha"].runners

    def test_unaffordable_company_skips_all_picks(self):
        co = FakeCompany("Poor", budget=5.0)
        rosters = {"Poor": _populate_roster([])}
        target = _make_runner(runner_id=1, net_loot=5000)
        refresh_upkeep(target)
        free_agents = [target]
        bids = {"Poor": [(target, target.upkeep_cost)]}
        resolve_bidding([co], rosters, free_agents, bids, random.Random(0), 9)
        assert target in free_agents  # nobody could afford them
        assert rosters["Poor"].runners == []


# ---------------------------------------------------------------------------
# tick_free_agent_pool — retirement + rookie spawn floor
# ---------------------------------------------------------------------------
class TestTickFreeAgentPool:
    def test_retires_runners_past_threshold(self):
        market = make_initial_market()
        used = set()
        id_supplier = RunnerIdCounter(next_id=100)
        # Age one runner just over the threshold; another well under.
        old = _make_runner(runner_id=1)
        old.weeks_orphaned = ORPHAN_RETIRE_AFTER_WEEKS  # next tick pushes it over
        young = _make_runner(runner_id=2)
        free_agents = [old, young]

        retired, _spawned = tick_free_agent_pool(
            free_agents, total_employed=MIN_GLOBAL_POOL,  # already above floor
            market=market, used_names=used, id_supplier=id_supplier,
        )
        assert [r.id for r in retired] == [1]
        assert young in free_agents
        assert old not in free_agents

    def test_spawns_rookies_to_meet_minimum_pool(self):
        """If total_employed + len(free_agents) < MIN_GLOBAL_POOL, spawn rookies
        into the pool until the floor is met."""
        market = make_initial_market()
        used = set()
        id_supplier = RunnerIdCounter(next_id=500)
        free_agents = []

        retired, spawned = tick_free_agent_pool(
            free_agents, total_employed=20,  # 20 + 0 < MIN_GLOBAL_POOL=42
            market=market, used_names=used, id_supplier=id_supplier,
        )
        assert retired == []
        assert len(spawned) == MIN_GLOBAL_POOL - 20
        assert len(free_agents) == len(spawned)
        # Each rookie is a real Runner with a fresh id from the supplier.
        for r in spawned:
            assert isinstance(r, Runner)
            assert r.id >= 500

    def test_no_spawn_when_pool_at_floor(self):
        market = make_initial_market()
        used = set()
        id_supplier = RunnerIdCounter(next_id=500)
        free_agents = [_make_runner(runner_id=i) for i in range(10)]

        _retired, spawned = tick_free_agent_pool(
            free_agents,
            total_employed=MIN_GLOBAL_POOL - len(free_agents),  # exactly at floor
            market=market, used_names=used, id_supplier=id_supplier,
        )
        assert spawned == []


# ---------------------------------------------------------------------------
# release_to_free_agents — preserves career stats, resets transient state
# ---------------------------------------------------------------------------
class TestReleaseToFreeAgents:
    def test_preserves_career_stats(self):
        pool = []
        r = _make_runner(runner_id=1, net_loot=2000, eliminations=8,
                         deployments_survived=10,
                         affinities={"Destroyer": 0.6})
        release_to_free_agents(r, pool)
        assert r in pool
        assert r.net_loot == 2000
        assert r.eliminations == 8
        assert r.deployments_survived == 10
        assert r.shell_affinities["Destroyer"] == pytest.approx(0.6)

    def test_resets_transient_state(self):
        pool = []
        r = _make_runner(runner_id=1)
        r.current_shell = "Destroyer"
        r._died_this_week = True
        release_to_free_agents(r, pool)
        assert r.current_shell == ""
        assert not hasattr(r, "_died_this_week")
        assert r.weeks_orphaned == 0


# ---------------------------------------------------------------------------
# collect_company_income — credit-share routing
# ---------------------------------------------------------------------------
class TestCollectCompanyIncome:
    def test_credits_company_share_of_runner_earnings(self):
        co = FakeCompany("X", budget=0.0)
        r1 = _make_runner(runner_id=1)
        r2 = _make_runner(runner_id=2)
        roster = _populate_roster([r1, r2])
        credits_by_id = {1: 100.0, 2: 200.0}
        share = collect_company_income(co, roster, credits_by_id)
        assert share == pytest.approx(300.0 * CREDIT_SHARE_TO_COMPANY)
        assert co.budget == pytest.approx(300.0 * CREDIT_SHARE_TO_COMPANY)

    def test_ignores_runners_not_on_roster(self):
        """A runner who earned credits before being orphaned mid-week shouldn't
        contribute to a former employer's income."""
        co = FakeCompany("X", budget=0.0)
        r1 = _make_runner(runner_id=1)
        roster = _populate_roster([r1])
        credits_by_id = {1: 100.0, 999: 5000.0}   # 999 isn't on this roster
        collect_company_income(co, roster, credits_by_id)
        assert co.budget == pytest.approx(100.0 * CREDIT_SHARE_TO_COMPANY)
