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
from runner_sim.market.pricing import CompanyWeekResult
from runner_sim.market.roster import all_runners as roster_all_runners
from runner_sim.market.week import simulate_week
from runner_sim.market.shell_market import BASE_SHELL_PRICE, N_SHELLS, ShellMarket
from runner_sim.shells import SHELL_ROSTER
from runner_sim.zone_sim.items import load_items
from runner_sim.zone_sim.zones import ZONES, Zone


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS
# ---------------------------------------------------------------------------
STARTING_CREDITS = 10_000.0
SIM_PAUSE_SECS   = 0.8


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class Company:
    name: str
    price: float


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
# GAME ENGINE  (headless — no I/O; all state mutations go through here)
# ---------------------------------------------------------------------------
class GameEngine:
    def __init__(self, debug: bool = False) -> None:
        companies: list[Company] = [
            Company("CyberAcme", 450.0),
            Company("Sekiguchi",  380.0),
            Company("Traxus",     300.0),
            Company("NuCaloric",  200.0),
        ]
        rosters, market = bootstrap_default_state(
            company_names=tuple(c.name for c in companies)
        )
        self.state = GameState(
            companies=companies,
            rosters=rosters,
            market=market,
            item_catalog=load_items(),
            monitored_zone=next(z for z in ZONES if z.monitored),
            portfolio=Portfolio(),
            price_history={c.name: [c.price] for c in companies},
            debug=debug,
        )

    def do_buy(self, company_name: str, shares: int) -> str | None:
        """Returns error string on failure, None on success."""
        c = _find_company(self.state.companies, company_name)
        if c is None:
            return f"Unknown company '{company_name}'."
        return self.state.portfolio.buy(c.name, shares, c.price)

    def do_sell(self, company_name: str, shares: int) -> str | None:
        """Returns error string on failure, None on success."""
        c = _find_company(self.state.companies, company_name)
        if c is None:
            return f"Unknown company '{company_name}'."
        return self.state.portfolio.sell(c.name, shares, c.price)

    def do_all_in(self) -> str:
        """Spread available credits equally across all companies. Returns summary."""
        s = self.state
        if s.portfolio.credits <= 0:
            return "No credits to invest."
        alloc = s.portfolio.credits / len(s.companies)
        bought: list[str] = []
        for company in s.companies:
            shares = int(alloc / company.price)
            if shares > 0:
                s.portfolio.buy(company.name, shares, company.price)
                bought.append(f"{company.name} ×{shares}")
        if bought:
            return "Bought: " + "  |  ".join(bought)
        return "Not enough credits to buy even one share."

    def advance_week(self) -> None:
        """Run one week of simulation. Blocking — call from a worker thread."""
        s = self.state
        time.sleep(SIM_PAUSE_SECS)

        sim_result = simulate_week(
            s.rosters, s.market, ZONES, s.item_catalog,
            company_prices=_prices_dict(s.companies),
        )

        for r in sim_result.company_results:
            for c in s.companies:
                if c.name == r.company_name:
                    c.price = r.price_after
                    s.price_history.setdefault(c.name, []).append(r.price_after)

        s.last_results = sim_result.company_results

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
