"""
Marathon Market Simulator — Python Prototype
Console-only. Run: uv run python marathon_market.py
Debug mode (shows all zones + shell market): uv run python marathon_market.py --debug

This is the player-facing entry point. All week-simulation mechanics
(rosters, squads, zone runs, shell market, price math) live in
runner_sim.market — this file just orchestrates the loop and renders
the UI.
"""

from __future__ import annotations
import random
import sys
import time
from dataclasses import dataclass, field

from runner_sim.market.calibration import (
    DEFAULT_COMPANY_NAMES,
    bootstrap_default_state,
)
from runner_sim.market.pricing import CompanyWeekResult
from runner_sim.market.roster import all_runners as roster_all_runners
from runner_sim.market.week import simulate_week
from runner_sim.market.deployment import assign_squads
from runner_sim.market.shell_market import BASE_SHELL_PRICE, N_SHELLS
from runner_sim.shells import SHELL_ROSTER
from runner_sim.zone_sim.items import load_items
from runner_sim.zone_sim.zones import ZONES, Zone
from runner_sim.shells import SHELL_BY_NAME

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS (player-layer only)
# ---------------------------------------------------------------------------
STARTING_CREDITS = 10_000.0
SIM_PAUSE_SECS   = 0.8


# ---------------------------------------------------------------------------
# DATA STRUCTURES (player layer — Company prices and Portfolio)
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


# ---------------------------------------------------------------------------
# FORMATTING HELPERS
# ---------------------------------------------------------------------------
DIVIDER = "─" * 52


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


# ---------------------------------------------------------------------------
# PLANNING PHASE UI
# ---------------------------------------------------------------------------
def print_header(week: int) -> None:
    print(f"\n{DIVIDER}")
    print(f"  MARATHON MARKET SIMULATOR — WEEK {week}")
    print(DIVIDER)


def _build_sector7_previews(rosters, monitored_zone: Zone) -> dict[str, list[str]]:
    """Peek at each company's likely Sector 7 squad for the planning phase.

    NOTE: assign_squads uses random shuffling, so the actual zone assignment
    won't be locked in until simulate_week runs. This preview is a
    'representative' draw — it shows three of the company's runners
    formatted as 'Name/Shl', but the player can't know which 3 of 9 will
    actually go to Sector 7. We use a stable seed-of-the-week so the same
    preview shows during repeated planning prints.
    """
    previews: dict[str, list[str]] = {}
    for co_name, roster in rosters.items():
        # Just take the first 3 runners by id as a stable display preview.
        # (assign_squads will randomly assign one squad to S7 at simulate time.)
        sample = sorted(roster.runners, key=lambda r: r.id)[:3]
        previews[co_name] = [f"{r.name}/{r.current_shell[:3]}" for r in sample]
    return previews


def print_planning_phase(
    week: int,
    companies: list[Company],
    rosters,
    monitored_zone: Zone,
    portfolio: Portfolio,
    last_results: list[CompanyWeekResult] | None,
) -> None:
    print_header(week)

    # Portfolio summary
    prices = _prices_dict(companies)
    total = portfolio.total_value(prices)
    print(f"\nPORTFOLIO")
    print(f"  Credits:     {_fmt_cr(portfolio.credits)}")
    for name, shares in portfolio.holdings.items():
        price = prices[name]
        print(f"  {name:<12} {shares} shares  (@ {price:.0f} cr = {_fmt_cr(shares * price)})")
    print(f"  Total value: {_fmt_cr(total)}")

    # Roster summaries — visible across all companies
    print(f"\nROSTERS")
    for company in companies:
        roster = rosters[company.name]
        avg_attempts = sum(r.extraction_attempts for r in roster.runners) / len(roster.runners)
        deaths_total = sum(r.death_count for r in roster.runners)
        print(f"  {company.name:<12} {len(roster.runners)} runners  "
              f"avg {avg_attempts:.1f} extractions, {deaths_total} career deaths")

    # Zone intel — preview of who could go to Sector 7
    print(f"\nZONE INTEL — {monitored_zone.name}  ({_difficulty_label(monitored_zone.difficulty)})")
    print(f"  (squad lineup randomized at deploy; preview shows roster sample)")
    previews = _build_sector7_previews(rosters, monitored_zone)
    for company in companies:
        members = ", ".join(previews[company.name])
        print(f"  {company.name:<12} [{members}]")

    # Market prices with last week's change
    print(f"\nMARKET PRICES")
    for company in companies:
        line = f"  {company.name:<12} {company.price:>7.1f} cr"
        if last_results:
            for r in last_results:
                if r.company_name == company.name:
                    line += f"  ({_fmt_pct(r.price_change_pct)} last week)"
        print(line)

    print(f"\n[B]uy  [S]ell  [A]ll in  s[K] shells  [H]old / advance week  [Q]uit")


# ---------------------------------------------------------------------------
# TRADE INPUT HANDLERS
# ---------------------------------------------------------------------------
PREMIUM_SHELLS = {"Destroyer", "Thief", "Triage"}


def _trend_arrow(delta: float) -> str:
    """Single-character indicator of week-over-week price direction."""
    if delta > 1.0:
        return "▲"
    if delta < -1.0:
        return "▼"
    return "·"


def _sparkline(prices: list[float]) -> str:
    """Render a price series as a 6-character ASCII sparkline.

    Maps each value to one of '▁▂▃▄▅▆▇█' relative to the series range.
    Use up to the last 6 weeks; if fewer weeks of history exist, pad blank.
    """
    if not prices:
        return "      "
    glyphs = "▁▂▃▄▅▆▇█"
    series = prices[-6:]
    lo, hi = min(series), max(series)
    span = hi - lo
    if span < 0.01:
        return "·" * 6 + " " * max(0, 6 - len(series))
    out = ""
    for p in series:
        idx = min(len(glyphs) - 1, int((p - lo) / span * len(glyphs)))
        out += glyphs[idx]
    return out + "·" * max(0, 6 - len(series))


def show_shell_market_view(market, rosters) -> None:
    """Render the shell market state — prices, week-over-week change, adoption.

    The display answers two questions for the player:
      1. Where are shell prices right now and which way are they moving?
      2. Is the market actually using all 7 shells, or just clustering on
         Destroyer/Thief/Triage?
    """
    print(f"\n{DIVIDER}")
    print(f"  SHELL MARKET")
    print(DIVIDER)

    # Current adoption counts
    from collections import Counter
    runners = roster_all_runners(rosters)
    total_runners = len(runners)
    counts = Counter(r.current_shell for r in runners)

    # Previous week's prices (for week-over-week delta)
    if len(market.price_history) >= 2:
        prev_prices = market.price_history[-2]
    else:
        prev_prices = {s.name: BASE_SHELL_PRICE for s in SHELL_ROSTER}

    # Header
    print(f"\n  {'Shell':<10}  {'Price':>8}  {'Δ wk':>8}  {'':1}  {'Trend':>7}  "
          f"{'Adoption':>13}")
    print(f"  {'─'*10}  {'─'*8}  {'─'*8}  {'─'*1}  {'─'*7}  {'─'*13}")

    # Sort by current price descending
    sorted_shells = sorted(SHELL_ROSTER, key=lambda s: -market.prices[s.name])

    for shell in sorted_shells:
        price = market.prices[shell.name]
        prev = prev_prices.get(shell.name, BASE_SHELL_PRICE)
        delta = price - prev
        delta_str = f"{delta:+.1f}" if abs(delta) >= 0.05 else "  —  "
        arrow = _trend_arrow(delta)

        # Sparkline from price_history (last 6 weeks)
        history_for_shell = [snap.get(shell.name, BASE_SHELL_PRICE)
                              for snap in market.price_history]
        spark = _sparkline(history_for_shell)

        # Adoption
        count = counts.get(shell.name, 0)
        pct = 100 * count / total_runners if total_runners else 0
        is_premium = shell.name in PREMIUM_SHELLS
        tag = " ★" if is_premium else "  "

        print(f"  {shell.name:<10}  {price:>7.1f}cr  {delta_str:>8}  {arrow:1}  "
              f"{spark:>7}  {count:>2} ({pct:4.1f}%){tag}")

    # Footer: premium vs middle adoption split
    premium_count = sum(counts.get(s, 0) for s in PREMIUM_SHELLS)
    middle_count = total_runners - premium_count
    fair_share_pct = 100 / N_SHELLS  # 14.3%

    print(f"  {'─'*10}  {'─'*8}  {'─'*8}  {'─'*1}  {'─'*7}  {'─'*13}")
    print(f"\n  ★ Premium archetypes (Destroyer/Thief/Triage):  "
          f"{premium_count}/{total_runners} ({100*premium_count/total_runners:.1f}%)")
    print(f"    Middle shells (Vandal/Assassin/Recon/Rook):     "
          f"{middle_count}/{total_runners} ({100*middle_count/total_runners:.1f}%)")
    print(f"    Fair share (uniform adoption): {fair_share_pct:.1f}% per shell")

    weeks_recorded = len(market.price_history)
    if weeks_recorded > 1:
        print(f"\n  Showing latest prices · sparkline = last {min(6, weeks_recorded)} weeks")
    else:
        print(f"\n  Sparklines build up over time — come back after a few weeks.")

    input("\nPress ENTER to return...")


def handle_buy(companies: list[Company], portfolio: Portfolio) -> None:
    raw = input(f"Buy which company?  {_company_shortcuts(companies)}\n> ").strip()
    company = _find_company(companies, raw)
    if company is None:
        print(f"  Unknown company '{raw}'. Try again.")
        return
    affordable = int(portfolio.credits / company.price)
    raw_shares = input(f"How many shares? (you can afford up to {affordable})\n> ").strip()
    try:
        shares = int(raw_shares)
    except ValueError:
        print("  Enter a whole number.")
        return
    err = portfolio.buy(company.name, shares, company.price)
    if err:
        print(f"  Error: {err}")
    else:
        print(f"  Bought {shares} share(s) of {company.name} @ {company.price:.0f} cr. "
              f"Credits remaining: {_fmt_cr(portfolio.credits)}")


def handle_all_in(companies: list[Company], portfolio: Portfolio) -> None:
    """Split available credits equally across all companies and buy as many shares as possible."""
    if portfolio.credits <= 0:
        print("  No credits to invest.")
        return
    alloc_per_company = portfolio.credits / len(companies)
    bought: list[str] = []
    for company in companies:
        shares = int(alloc_per_company / company.price)
        if shares > 0:
            portfolio.buy(company.name, shares, company.price)
            bought.append(f"{company.name} ×{shares}")
    if bought:
        print("  Bought: " + "  |  ".join(bought))
        print(f"  Credits remaining: {_fmt_cr(portfolio.credits)}")
    else:
        print("  Not enough credits to buy even one share in any company.")


def handle_sell(companies: list[Company], portfolio: Portfolio) -> None:
    if not portfolio.holdings:
        print("  You have no shares to sell.")
        return
    raw = input(f"Sell which company?  {_company_shortcuts(companies)}\n> ").strip()
    company = _find_company(companies, raw)
    if company is None:
        print(f"  Unknown company '{raw}'. Try again.")
        return
    held = portfolio.holdings.get(company.name, 0)
    if held == 0:
        print(f"  You hold no shares of {company.name}.")
        return
    raw_shares = input(f"How many shares? (you hold {held})\n> ").strip()
    try:
        shares = int(raw_shares)
    except ValueError:
        print("  Enter a whole number.")
        return
    err = portfolio.sell(company.name, shares, company.price)
    if err:
        print(f"  Error: {err}")
    else:
        print(f"  Sold {shares} share(s) of {company.name} @ {company.price:.0f} cr. "
              f"Credits remaining: {_fmt_cr(portfolio.credits)}")


def planning_loop(
    week: int,
    companies: list[Company],
    rosters,
    monitored_zone: Zone,
    portfolio: Portfolio,
    last_results: list[CompanyWeekResult] | None,
    market = None,
) -> bool:
    """Returns False if the player chose to quit."""
    print_planning_phase(week, companies, rosters, monitored_zone, portfolio, last_results)
    while True:
        action = input("> ").strip().upper()
        if action in ("", "H", "HOLD"):
            return True
        if action == "Q":
            return False
        if action == "B":
            handle_buy(companies, portfolio)
            print_planning_phase(week, companies, rosters, monitored_zone, portfolio, last_results)
        elif action == "S":
            handle_sell(companies, portfolio)
            print_planning_phase(week, companies, rosters, monitored_zone, portfolio, last_results)
        elif action == "A":
            handle_all_in(companies, portfolio)
            print_planning_phase(week, companies, rosters, monitored_zone, portfolio, last_results)
        elif action == "K":
            if market is not None:
                show_shell_market_view(market, rosters)
            else:
                print("  Shell market not available.")
            print_planning_phase(week, companies, rosters, monitored_zone, portfolio, last_results)
        else:
            print("  Enter B, S, A, K, H, or Q.")


# ---------------------------------------------------------------------------
# RESULTS UI
# ---------------------------------------------------------------------------
def _print_all_zones_breakdown(zone_results, monitored_zone: Zone) -> None:
    """Debug view: per-squad outcomes for every zone, including hidden ones.

    Each zone shows pool start→end and a row per squad with company prefix,
    doctrine, status, items extracted, credits, and per-week kill count
    (computed from the zone's combat_events).
    """
    print(f"\nALL ZONES BREAKDOWN  [debug]")
    for zone_name, zr in zone_results.items():
        is_monitored = zone_name == monitored_zone.name
        tag = " ★ monitored" if is_monitored else " · hidden"
        # Difficulty label not on ZoneRunResult, look it up
        zone_obj = next(z for z in ZONES if z.name == zone_name)
        print(f"\n▸ {zone_name}  ({_difficulty_label(zone_obj.difficulty)}){tag}  "
              f"pool {zr.pool_size_at_start} → {zr.pool_size_at_end}")
        for sq in zr.squads:
            # Per-week kills: sum loser_runner_count from combat_events where this squad won
            kills = sum(
                ev.loser_runner_count
                for ev in zr.combat_events
                if ev.winner_squad == sq.name
            )
            if sq.extracted:
                status = "extracted "
                detail = f"{len(sq.loot.items):>2} items, {sq.loot.total_credits():>5}cr, {kills} kills"
            elif sq.eliminated:
                status = "ELIMINATED"
                detail = "— squad wiped —"
            else:
                status = "stranded  "
                detail = f"{len(sq.loot.items):>2} items (unrecovered)"
            print(f"  {sq.name:<16} {sq.doctrine.value.upper():<8} {status}   {detail}")


def print_results(
    results: list[CompanyWeekResult],
    monitored_zone: Zone,
    portfolio: Portfolio,
    portfolio_value_before: float,
    companies: list[Company],
    rosters,
    market,
    zone_results = None,
    debug: bool = False,
) -> None:
    print(f"\n{DIVIDER}")
    print("  RESULTS")
    print(DIVIDER)

    # Monitored zone outcomes — what the player had signal on
    print(f"\nYOUR ZONE — {monitored_zone.name}  ({_difficulty_label(monitored_zone.difficulty)})")
    for r in results:
        names = ", ".join(r.monitored_runner_names) if r.monitored_runner_names else "—"
        if r.monitored_squad_returned:
            outcome = "Squad RETURNED"
            detail = f"  {r.monitored_credits:>5.0f} cr  ·  {r.monitored_eliminations} kills"
        else:
            outcome = "Squad LOST    "
            detail = "  — no extraction —"
        print(f"  {r.company_name:<12} {outcome}   [{names}]")
        print(f"               {detail}")

    # Market response — driven by all zones combined
    print(f"\nMARKET RESPONSE  (all zones)")
    for r in results:
        label = _expectation_label(r.delta)
        print(f"  {r.company_name:<12} {r.price_before:>7.1f} → {r.price_after:>7.1f} cr  "
              f"({_fmt_pct(r.price_change_pct):>7})  [{label}]")

    # Debug-only sections
    if debug:
        if zone_results is not None:
            _print_all_zones_breakdown(zone_results, monitored_zone)

        print(f"\nSHELL MARKET  [debug]")
        for shell, price in sorted(market.prices.items(), key=lambda kv: -kv[1]):
            print(f"  {shell:<10} {price:>6.1f} cr")
        print(f"\nROSTER STATE  [debug]")
        for company in companies:
            roster = rosters[company.name]
            from collections import Counter
            shells = Counter(r.current_shell for r in roster.runners)
            print(f"  {company.name:<12} shells: {dict(shells)}")

    prices = _prices_dict(companies)
    value_after = portfolio.total_value(prices)
    week_gain = value_after - portfolio_value_before
    week_pct = (week_gain / portfolio_value_before * 100) if portfolio_value_before > 0 else 0.0
    sign = "+" if week_gain >= 0 else ""

    print("\nPORTFOLIO UPDATE")
    print(f"  Credits:     {_fmt_cr(portfolio.credits)}")
    for name, shares in portfolio.holdings.items():
        price = prices[name]
        print(f"  {name:<12} {shares} shares  (@ {price:.0f} cr = {_fmt_cr(shares * price)})")
    print(f"  Total value: {_fmt_cr(value_after)}  "
          f"({sign}{_fmt_cr(week_gain)}, {_fmt_pct(week_pct)} this week)")

    input("\nPress ENTER to continue...")


def print_session_end(week: int, portfolio: Portfolio, prices: dict[str, float]) -> None:
    final = portfolio.total_value(prices)
    net_pct = (final - STARTING_CREDITS) / STARTING_CREDITS * 100
    sign = "+" if net_pct >= 0 else ""
    print(f"\n{DIVIDER}")
    print("  SESSION COMPLETE")
    print(DIVIDER)
    print(f"  Weeks played:  {week - 1}")
    print(f"  Final value:   {_fmt_cr(final)}")
    print(f"  Starting:      {_fmt_cr(STARTING_CREDITS)}")
    print(f"  Net return:    {sign}{net_pct:.1f}%")
    print(DIVIDER)


# ---------------------------------------------------------------------------
# MAIN GAME LOOP
# ---------------------------------------------------------------------------
def run_game() -> None:
    debug = "--debug" in sys.argv

    print("\n" + "=" * 52)
    print("   MARATHON MARKET SIMULATOR")
    print("   Tau Ceti IV — Financial Intelligence Division")
    print("=" * 52)
    if debug:
        print("   [DEBUG MODE — shell market and full roster visible]")
    print(f"\nStarting capital: {_fmt_cr(STARTING_CREDITS)}")
    print(f"Zones: {len(ZONES)} total  |  Monitoring: 1  |  Press Q to quit")

    input("\nPress ENTER to begin...\n")

    # --- bootstrap: 4 companies × 9 runners + initial shell market ---
    companies: list[Company] = [
        Company("CyberAcme", 450.0),
        Company("Sekiguchi",  380.0),
        Company("Traxus",     300.0),
        Company("NuCaloric",  200.0),
    ]
    rosters, market = bootstrap_default_state(
        company_names=tuple(c.name for c in companies)
    )
    item_catalog = load_items()
    monitored_zone = next(z for z in ZONES if z.monitored)

    portfolio = Portfolio()
    last_results: list[CompanyWeekResult] | None = None
    week = 1

    while True:
        value_before = portfolio.total_value(_prices_dict(companies))

        # Planning phase
        still_playing = planning_loop(
            week, companies, rosters, monitored_zone, portfolio, last_results, market=market
        )
        if not still_playing:
            break

        # Simulation pause
        print(f"\n  Simulating week {week}...")
        time.sleep(SIM_PAUSE_SECS)

        # Run one week through the integrated stack
        company_prices = _prices_dict(companies)
        sim_result = simulate_week(rosters, market, ZONES, item_catalog, company_prices=company_prices)

        # Push price changes back into Company objects
        for r in sim_result.company_results:
            for c in companies:
                if c.name == r.company_name:
                    c.price = r.price_after

        print_results(
            sim_result.company_results, monitored_zone, portfolio, value_before, companies,
            rosters, market, sim_result.zone_results, debug,
        )

        last_results = sim_result.company_results
        week += 1

    print_session_end(week, portfolio, _prices_dict(companies))


if __name__ == "__main__":
    run_game()
