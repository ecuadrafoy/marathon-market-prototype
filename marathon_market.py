"""
Marathon Market Simulator — Python Prototype
Textual TUI entry point. Run: uv run python marathon_market.py
Debug mode: uv run python marathon_market.py --debug
"""

from __future__ import annotations
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

from runner_sim.market.calibration import bootstrap_default_state
from runner_sim.market.company_strategy import CompanyRosterEvents
from runner_sim.market.pricing import CompanyWeekResult
from runner_sim.market.roster import all_runners as roster_all_runners
from runner_sim.market.week import simulate_week
from runner_sim.market.shell_market import BASE_SHELL_PRICE, N_SHELLS, ShellMarket
from runner_sim.runners import Runner
from runner_sim.shells import SHELL_ROSTER
from runner_sim.zone_sim.items import load_items
from runner_sim.zone_sim.zones import ZONES, Zone


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
STARTING_CREDITS         = 10_000.0
SIM_PAUSE_SECS           = 0.8
STARTING_COMPANY_BUDGET  = 600.0    # initial cash each company has for upkeep + acquisitions

# Share of every player share-purchase that flows into the company's operating
# budget. 1.0 = full capital injection (player is effectively an investor
# funding ops). Lower it later if "secondary market" semantics are wanted.
PLAYER_BUY_TO_BUDGET_RATIO = 1.0

# Share of every player share-sale that is clawed back FROM the company's
# budget when the player liquidates. 0.0 = no direct clawback — selling
# instead damages VALUATION (see below), reflecting "loss of confidence"
# rather than literal cash withdrawal.
PLAYER_SELL_CLAWBACK_RATIO = 0.0

# ── VALUATION (third axis: enterprise value, separate from price + budget) ──
# Valuation represents the company's total worth as an enterprise — accumulated
# brand strength, roster pedigree, market position. Unlike price (twitches
# weekly on noise) and budget (flows in/out continuously), valuation is
# REPORTED quarterly. Between reports, events accumulate in
# Company.pending_valuation_delta and are released at week boundaries that
# are multiples of QUARTERLY_REPORT_WEEKS. STARTING_VALUATION and
# VALUATION_CR_PER_COUNTER are now owned by runner_sim.market.pricing — the
# pricing module needs them for the valuation-anchored price formula and
# pricing.py is imported BY this module, so we re-import them here to keep
# the public surface stable.
from runner_sim.market.pricing import STARTING_VALUATION, VALUATION_CR_PER_COUNTER
QUARTERLY_REPORT_WEEKS   = 12


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class Company:
    name: str
    price: float
    budget: float = 0.0                            # weekly spending pot for upkeep + bids
    valuation: float = STARTING_VALUATION          # enterprise value, updated only on quarterly reports
    pending_valuation_delta: float = 0.0           # accumulates between quarters; reset on report


@dataclass
class Portfolio:
    credits: float = STARTING_CREDITS
    holdings: dict[str, int] = field(default_factory=dict)

    def total_value(self, prices: dict[str, float]) -> float:
        share_value = sum(
            self.holdings.get(name, 0) * price
            for name, price in prices.items()
        )
        return self.credits + share_value

    def buy(self, company_name: str, shares: int, price: float) -> str | None:
        """Returns error string on failure, None on success."""
        if shares <= 0:
            return "Share count must be a positive integer."
        cost = shares * price
        if cost > self.credits:
            affordable = int(self.credits / price)
            return f"Insufficient credits. You can afford up to {affordable} share(s) at {price:.0f} cr."
        self.credits -= cost
        self.holdings[company_name] = self.holdings.get(company_name, 0) + shares
        return None

    def sell(self, company_name: str, shares: int, price: float) -> str | None:
        """Returns error string on failure, None on success."""
        if shares <= 0:
            return "Share count must be a positive integer."
        held = self.holdings.get(company_name, 0)
        if shares > held:
            return f"You only hold {held} share(s) of {company_name}."
        self.holdings[company_name] = held - shares
        if self.holdings[company_name] == 0:
            del self.holdings[company_name]
        self.credits += shares * price
        return None


@dataclass
class GameState:
    companies: list[Company]
    rosters: dict
    market: ShellMarket
    item_catalog: list
    monitored_zone: Zone
    portfolio: Portfolio
    week: int = 1
    last_results: list[CompanyWeekResult] | None = None
    last_zone_results: dict | None = None  # populated only when debug=True
    # (week_num, company_name, label) where label starts with "beat"/"met"/"missed"
    expectation_history: list[tuple[int, str, str]] = field(default_factory=list)
    # per-company price history for sparklines
    price_history: dict[str, list[float]] = field(default_factory=dict)
    # closed-pool free-agent roster — orphaned + dead runners awaiting rehire
    free_agents: list[Runner] = field(default_factory=list)
    # most recent week's roster events per company (signings, drops, deaths) —
    # surfaced by the UI in both planning and results phases so the player
    # can see WHEN runner purchases and losses happen in the cycle.
    last_roster_events: dict[str, CompanyRosterEvents] = field(default_factory=dict)
    # set on quarterly-report weeks (every 12); maps company_name → (before, delta, after).
    # None on non-report weeks so the UI can detect when a report just fired.
    last_quarterly_reports: dict[str, tuple[float, float, float]] | None = None
    debug: bool = False


# ---------------------------------------------------------------------------
# FORMATTING HELPERS  (used by TUI widgets)
# ---------------------------------------------------------------------------
DIVIDER = "─" * 52
PREMIUM_SHELLS = {"Destroyer", "Thief", "Triage"}


def _fmt_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _fmt_cr(amount: float) -> str:
    return f"{amount:,.0f} cr"


def _difficulty_label(difficulty: float) -> str:
    if difficulty <= 0.15:
        return "Easy"
    if difficulty <= 0.4:
        return "Medium"
    return "Hard"


def _expectation_label(delta: float) -> str:
    if delta > 50.0:
        return "beat expectations"
    if delta < -50.0:
        return "missed expectations"
    return "met expectations"


def _prices_dict(companies: list[Company]) -> dict[str, float]:
    return {c.name: c.price for c in companies}


def _find_company(companies: list[Company], name_input: str) -> Company | None:
    key = name_input.strip().lower().replace(" ", "")
    for c in companies:
        if c.name.lower().replace(" ", "").startswith(key):
            return c
    return None


def _company_shortcuts(companies: list[Company]) -> str:
    return "  ".join(f"[{c.name[0]}]{c.name[1:]}" for c in companies)


def _trend_arrow(delta: float) -> str:
    if delta > 1.0:
        return "▲"
    if delta < -1.0:
        return "▼"
    return "·"


def _sparkline(prices: list[float]) -> str:
    """Render a price series as a 6-character ASCII sparkline."""
    if not prices:
        return "      "
    glyphs = "▁▂▃▄▅▆▇█"
    series = prices[-6:]
    lo, hi = min(series), max(series)
    span = hi - lo
    if span < 0.01:
        return "·" * len(series) + " " * max(0, 6 - len(series))
    out = ""
    for p in series:
        idx = min(len(glyphs) - 1, int((p - lo) / span * len(glyphs)))
        out += glyphs[idx]
    return out + "·" * max(0, 6 - len(series))


def _build_sector7_previews(rosters, monitored_zone: Zone) -> dict[str, list[str]]:
    """Stable preview of each company's likely Perimeter squad."""
    previews: dict[str, list[str]] = {}
    for co_name, roster in rosters.items():
        sample = sorted(roster.runners, key=lambda r: r.id)[:3]
        previews[co_name] = [f"{r.name}/{r.current_shell[:3]}" for r in sample]
    return previews


# ---------------------------------------------------------------------------
# VALUATION ACCRUAL — the "third axis" of company state
# ---------------------------------------------------------------------------
def accrue_valuation(company: Company, score: float, reason: str = "") -> None:
    """Add a counter score to this company's pending valuation tally.

    `score` is a small signed integer (e.g. +1, -3) representing how much
    this event nudges the company's reputation. Scores accumulate over the
    quarter; the multiplication into actual valuation cr happens at the
    quarterly report (see GameEngine.advance_week). `reason` is for the
    player-facing breakdown — short label like 'squad_returned'.
    """
    company.pending_valuation_delta += score


def valuation_delta_for_event(event_kind: str, **context) -> float:
    """Return the COUNTER SCORE (signed small int) to accrue for one event.

    The score is a unitless tally entry — it does NOT directly equal cr.
    At the next quarterly report, the company's total accumulated score is
    multiplied by VALUATION_CR_PER_COUNTER to produce the actual valuation
    cr movement. This split makes both halves independently tunable:
      - change the per-event weights here to rebalance event importance
      - change VALUATION_CR_PER_COUNTER to scale the whole system

    event_kind values currently emitted by the sim:
      "player_buy"          context: shares: int, price: float
      "player_sell"         context: shares: int, price: float
      "squad_returned"      context: credits: float (avg per squad)
      "squad_eliminated"    context: (none)
      "runner_orphaned"     context: (none)
      "runner_signed"       context: (none)
    """
    if event_kind == "player_buy":
        # Each share purchased posts +1 to the reputation ledger — buying is a
        # confidence vote, scaled to ownership weight.
        return +1 * context.get("shares", 1)
    if event_kind == "player_sell":
        # Symmetric counterpart — every share sold is a tick against the company.
        return -1 * context.get("shares", 1)
    if event_kind == "squad_returned":
        # Small positive for each successful extraction — these are routine wins.
        return +1
    if event_kind == "squad_eliminated":
        # Asymmetric: wipes hurt harder than wins help. Reputation is fragile.
        return -3
    if event_kind == "runner_orphaned":
        # Public signal of financial distress — labor market notices.
        return -2
    if event_kind == "runner_signed":
        # Modest positive — winning a free-agent draft signals confidence.
        return +1
    return 0


# ---------------------------------------------------------------------------
# RUNNER REGISTRY DATA  (shared by console and TUI runner screens)
# ---------------------------------------------------------------------------
def _runner_top_affinity(runner: Runner) -> tuple[str, float]:
    """Return (shell_name, value) for the runner's highest affinity. Empty
    runner_affinities (defensive) yields ('—', 0.0)."""
    if not runner.shell_affinities:
        return "—", 0.0
    shell, value = max(runner.shell_affinities.items(), key=lambda kv: kv[1])
    return shell, value


def _registry_groups(state: GameState) -> list[tuple[str, int, list[Runner]]]:
    """Group runners for the registry screen.

    Returns: list of (header, payroll_total, sorted_runners) tuples. Companies
    come first in `state.companies` order, then a single 'FREE AGENTS' group.
    Upkeep is refreshed on every runner before grouping so the displayed
    numbers match what payroll would actually charge this week.
    """
    from runner_sim.market.company_strategy import refresh_upkeep

    groups: list[tuple[str, int, list[Runner]]] = []

    for company in state.companies:
        roster = state.rosters[company.name]
        for r in roster.runners:
            refresh_upkeep(r)
        # Sort by upkeep descending — most expensive (usually most valuable) first.
        ordered = sorted(roster.runners, key=lambda r: -r.upkeep_cost)
        payroll = int(sum(r.upkeep_cost for r in roster.runners))
        groups.append((company.name, payroll, ordered))

    if state.free_agents:
        for r in state.free_agents:
            refresh_upkeep(r)
        # Free agents: sort by upkeep desc (premium veterans first), so the
        # most-contested signings stand out.
        ordered = sorted(state.free_agents, key=lambda r: -r.upkeep_cost)
        groups.append(("FREE AGENTS", 0, ordered))

    return groups


# ---------------------------------------------------------------------------
# GAME ENGINE  (headless — no I/O; all state mutations go through here)
# ---------------------------------------------------------------------------
class GameEngine:
    def __init__(self, debug: bool = False) -> None:
        companies: list[Company] = [
            Company("CyberAcme",  450.0, budget=STARTING_COMPANY_BUDGET),
            Company("Sekiguchi",  380.0, budget=STARTING_COMPANY_BUDGET),
            Company("Traxus",     300.0, budget=STARTING_COMPANY_BUDGET),
            Company("NuCaloric",  200.0, budget=STARTING_COMPANY_BUDGET),
        ]
        rosters, market, free_agents, id_supplier = bootstrap_default_state(
            company_names=tuple(c.name for c in companies),
        )
        self.state = GameState(
            companies=companies,
            rosters=rosters,
            market=market,
            item_catalog=load_items(),
            monitored_zone=next(z for z in ZONES if z.monitored),
            portfolio=Portfolio(),
            price_history={c.name: [c.price] for c in companies},
            free_agents=free_agents,
            debug=debug,
        )
        # Stored on the engine (not GameState) since it's a closure-like helper
        # rather than serialisable state.
        self._id_supplier = id_supplier

    def do_buy(self, company_name: str, shares: int) -> str | None:
        """Returns error string on failure, None on success.

        Two side-effects on success: the cost flows into the company's
        operating budget (PLAYER_BUY_TO_BUDGET_RATIO), AND a positive
        valuation delta accrues for next quarter's report.
        """
        c = _find_company(self.state.companies, company_name)
        if c is None:
            return f"Unknown company '{company_name}'."
        cost = shares * c.price
        err = self.state.portfolio.buy(c.name, shares, c.price)
        if err is None:
            c.budget += cost * PLAYER_BUY_TO_BUDGET_RATIO
            delta = valuation_delta_for_event("player_buy", shares=shares, price=c.price)
            accrue_valuation(c, delta, reason="player_buy")
        return err

    def do_sell(self, company_name: str, shares: int) -> str | None:
        """Returns error string on failure, None on success.

        Selling damages VALUATION (loss of investor confidence) rather than
        directly draining budget. PLAYER_SELL_CLAWBACK_RATIO is the legacy
        cash-drain knob; default 0.0 means selling has no immediate budget
        effect — only the quarterly valuation report registers it.
        """
        c = _find_company(self.state.companies, company_name)
        if c is None:
            return f"Unknown company '{company_name}'."
        proceeds = shares * c.price
        err = self.state.portfolio.sell(c.name, shares, c.price)
        if err is None:
            if PLAYER_SELL_CLAWBACK_RATIO > 0:
                c.budget = max(0.0, c.budget - proceeds * PLAYER_SELL_CLAWBACK_RATIO)
            delta = valuation_delta_for_event("player_sell", shares=shares, price=c.price)
            accrue_valuation(c, delta, reason="player_sell")
        return err

    def do_all_in(self) -> str:
        """Spread available credits equally across all companies."""
        s = self.state
        if s.portfolio.credits <= 0:
            return "No credits to invest."
        alloc = s.portfolio.credits / len(s.companies)
        bought: list[str] = []
        for company in s.companies:
            shares = int(alloc / company.price)
            if shares > 0:
                cost = shares * company.price
                err = s.portfolio.buy(company.name, shares, company.price)
                if err is None:
                    company.budget += cost * PLAYER_BUY_TO_BUDGET_RATIO
                    delta = valuation_delta_for_event(
                        "player_buy", shares=shares, price=company.price
                    )
                    accrue_valuation(company, delta, reason="player_buy")
                    bought.append(f"{company.name} ×{shares}")
        if bought:
            return "Bought: " + "  |  ".join(bought)
        return "Not enough credits to buy even one share."

    def advance_week(self) -> None:
        """Run one week of simulation. Blocking — call from a worker thread."""
        s = self.state
        time.sleep(SIM_PAUSE_SECS)

        # Build anchor_inputs so the valuation-anchored price formula in
        # pricing.py can pull each company's weekly move toward its
        # fair-value (= starting price scaled by projected_valuation /
        # STARTING_VALUATION). pending_valuation_delta at this point reflects
        # events through last week — the intended one-week lag.
        anchor_inputs = {
            c.name: (c.valuation, c.pending_valuation_delta, s.price_history[c.name][0])
            for c in s.companies
        }

        sim_result = simulate_week(
            s.rosters, s.market, ZONES, s.item_catalog,
            company_prices=_prices_dict(s.companies),
            companies=s.companies,
            free_agents=s.free_agents,
            id_supplier=self._id_supplier,
            price_histories=s.price_history,
            anchor_inputs=anchor_inputs,
        )

        for r in sim_result.company_results:
            for c in s.companies:
                if c.name == r.company_name:
                    c.price = r.price_after
                    s.price_history.setdefault(c.name, []).append(r.price_after)

        s.last_results = sim_result.company_results
        s.last_roster_events = sim_result.roster_events

        if s.debug:
            s.last_zone_results = {
                company_name: {
                    zone_name: {
                        "extracted": squad.extracted,
                        "eliminated": squad.eliminated,
                        "credits": float(squad.loot.total_credits()) if squad.extracted else 0.0,
                        "runners": [f"{r.name}/{r.current_shell[:3]}" for r in squad.runners],
                    }
                    for zone_name, squad in zone_squads.items()
                }
                for company_name, zone_squads in sim_result.co_squads_by_company.items()
            }

        for r in sim_result.company_results:
            label = _expectation_label(r.delta)
            s.expectation_history.append((s.week, r.company_name, label))

        # Prune expectation history to last 6 per company
        for co in s.companies:
            entries = [(w, cn, l) for w, cn, l in s.expectation_history if cn == co.name]
            if len(entries) > 6:
                for e in entries[:-6]:
                    s.expectation_history.remove(e)

        # ── Valuation accrual: bridge sim events → pending valuation delta ──
        # Each operational event of the week contributes a delta, sized by
        # valuation_delta_for_event. Accumulates in company.pending_valuation_delta
        # until the quarterly report (below) releases it into company.valuation.
        for r in sim_result.company_results:
            company = next(c for c in s.companies if c.name == r.company_name)
            # Squad returns — one event per successful squad, sized by its credits
            per_squad_credits = (
                r.total_credits_extracted / r.squads_returned if r.squads_returned else 0.0
            )
            for _ in range(r.squads_returned):
                delta = valuation_delta_for_event("squad_returned", credits=per_squad_credits)
                accrue_valuation(company, delta, reason="squad_returned")
            # Squad wipes — flat
            for _ in range(r.squads_eliminated):
                delta = valuation_delta_for_event("squad_eliminated")
                accrue_valuation(company, delta, reason="squad_eliminated")
            # Roster events (orphans, drops, signings)
            ev = sim_result.roster_events.get(company.name)
            if ev is not None:
                for _ in ev.orphaned_unaffordable:
                    delta = valuation_delta_for_event("runner_orphaned")
                    accrue_valuation(company, delta, reason="runner_orphaned")
                for _ in ev.signed:
                    delta = valuation_delta_for_event("runner_signed")
                    accrue_valuation(company, delta, reason="runner_signed")

        # ── Quarterly report: convert accumulated counter score into cr ──
        # Fires on weeks 12, 24, 36, ... The pending counter score is
        # multiplied by VALUATION_CR_PER_COUNTER to produce the actual cr
        # movement. Valuation cannot go below 0 (a "bankrupt-reputation" floor).
        s.last_quarterly_reports = None
        if s.week % QUARTERLY_REPORT_WEEKS == 0:
            reports: dict[str, tuple[float, float, float]] = {}
            for company in s.companies:
                before = company.valuation
                score = company.pending_valuation_delta
                delta_cr = score * VALUATION_CR_PER_COUNTER
                company.valuation = max(0.0, before + delta_cr)
                company.pending_valuation_delta = 0.0
                reports[company.name] = (before, delta_cr, company.valuation)
            s.last_quarterly_reports = reports

        s.week += 1


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Ensure UTF-8 output on Windows consoles
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if "--console" in sys.argv:
        from marathon_market_console import run_console
        run_console()
    else:
        from marathon_market_tui import MarathonMarketApp
        MarathonMarketApp("--debug" in sys.argv).run()
