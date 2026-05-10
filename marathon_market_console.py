"""
Marathon Market — console fallback mode.
Useful for debugging game logic without the TUI overhead.

Usage:
    uv run python marathon_market.py --console
    uv run python marathon_market.py --console --debug
"""

from __future__ import annotations

import time
from collections import Counter

from marathon_market import (
    GameEngine,
    GameState,
    DIVIDER,
    PREMIUM_SHELLS,
    SIM_PAUSE_SECS,
    STARTING_CREDITS,
    _build_sector7_previews,
    _company_shortcuts,
    _difficulty_label,
    _expectation_label,
    _find_company,
    _fmt_cr,
    _fmt_pct,
    _prices_dict,
    _sparkline,
    _trend_arrow,
    roster_all_runners,
)
from runner_sim.market.shell_market import BASE_SHELL_PRICE, N_SHELLS
from runner_sim.shells import SHELL_ROSTER
from runner_sim.zone_sim.zones import ZONES


# ---------------------------------------------------------------------------
# DISPLAY
# ---------------------------------------------------------------------------
def _print_planning(s: GameState) -> None:
    print(f"\n{DIVIDER}")
    print(f"  MARATHON MARKET SIMULATOR — WEEK {s.week}")
    print(DIVIDER)

    prices = _prices_dict(s.companies)
    total = s.portfolio.total_value(prices)
    print(f"\nPORTFOLIO")
    print(f"  Credits:     {_fmt_cr(s.portfolio.credits)}")
    for name, shares in s.portfolio.holdings.items():
        price = prices[name]
        print(f"  {name:<12} {shares} shares  (@ {price:.0f} cr = {_fmt_cr(shares * price)})")
    print(f"  Total value: {_fmt_cr(total)}")

    print(f"\nROSTERS")
    for company in s.companies:
        roster = s.rosters[company.name]
        avg = sum(r.extraction_attempts for r in roster.runners) / len(roster.runners)
        print(f"  {company.name:<12} {len(roster.runners)} runners  "
              f"avg {avg:.1f} extractions, {roster.total_deaths} career deaths")

    print(f"\nZONE INTEL — {s.monitored_zone.name}  ({_difficulty_label(s.monitored_zone.difficulty)})")
    print(f"  (squad lineup randomized at deploy; preview shows roster sample)")
    previews = _build_sector7_previews(s.rosters, s.monitored_zone)
    for company in s.companies:
        members = ", ".join(previews[company.name])
        print(f"  {company.name:<12} [{members}]")

    print(f"\nMARKET PRICES")
    for company in s.companies:
        line = f"  {company.name:<12} {company.price:>7.1f} cr"
        if s.last_results:
            for r in s.last_results:
                if r.company_name == company.name:
                    line += f"  ({_fmt_pct(r.price_change_pct)} last week)"
        print(line)

    print(f"\n[B]uy  [S]ell  [A]ll in  s[K] shells  [H]old / advance week  [Q]uit")


def _print_results(s: GameState, value_before: float) -> None:
    print(f"\n{DIVIDER}")
    print("  RESULTS")
    print(DIVIDER)

    print(f"\nYOUR ZONE — {s.monitored_zone.name}  ({_difficulty_label(s.monitored_zone.difficulty)})")
    for r in s.last_results:
        names = ", ".join(r.monitored_runner_names) if r.monitored_runner_names else "—"
        if r.monitored_squad_returned:
            outcome = "Squad RETURNED"
            detail  = f"  {r.monitored_credits:>5.0f} cr  ·  {r.monitored_eliminations} kills"
        else:
            outcome = "Squad LOST    "
            detail  = "  — no extraction —"
        print(f"  {r.company_name:<12} {outcome}   [{names}]")
        print(f"               {detail}")

    print(f"\nMARKET RESPONSE  (all zones)")
    for r in s.last_results:
        label = _expectation_label(r.delta)
        print(f"  {r.company_name:<12} {r.price_before:>7.1f} → {r.price_after:>7.1f} cr  "
              f"({_fmt_pct(r.price_change_pct):>7})  [{label}]")

    if s.last_zone_results:
        hidden_zones = [z for z in ZONES if not z.monitored]
        for z in hidden_zones:
            print(f"\nHIDDEN ZONE — {z.name}  ({_difficulty_label(z.difficulty)})")
            for company in s.companies:
                squad_data = s.last_zone_results.get(company.name, {}).get(z.name)
                if squad_data is None:
                    print(f"  {company.name:<12} —")
                    continue
                if squad_data["extracted"]:
                    outcome = "Squad RETURNED"
                    detail  = f"  {squad_data['credits']:>5.0f} cr"
                else:
                    outcome = "Squad LOST    "
                    detail  = "  — no extraction —"
                names = ", ".join(squad_data["runners"])
                print(f"  {company.name:<12} {outcome}   [{names}]")
                print(f"               {detail}")

    if s.debug:
        print(f"\nSHELL MARKET")
        for shell, price in sorted(s.market.prices.items(), key=lambda kv: -kv[1]):
            print(f"  {shell:<10} {price:>6.1f} cr")

        print(f"\nSHELL COMPOSITION")
        for company in s.companies:
            roster = s.rosters[company.name]
            shells = Counter(r.current_shell for r in roster.runners)
            shell_str = "  ".join(f"{name[:3]}×{count}" for name, count in shells.most_common())
            print(f"  {company.name:<12} {shell_str}")

    prices = _prices_dict(s.companies)
    value_after = s.portfolio.total_value(prices)
    week_gain   = value_after - value_before
    week_pct    = (week_gain / value_before * 100) if value_before > 0 else 0.0
    sign = "+" if week_gain >= 0 else ""

    print("\nPORTFOLIO UPDATE")
    print(f"  Credits:     {_fmt_cr(s.portfolio.credits)}")
    for name, shares in s.portfolio.holdings.items():
        price = prices[name]
        print(f"  {name:<12} {shares} shares  (@ {price:.0f} cr = {_fmt_cr(shares * price)})")
    print(f"  Total value: {_fmt_cr(value_after)}  "
          f"({sign}{_fmt_cr(week_gain)}, {_fmt_pct(week_pct)} this week)")

    input("\nPress ENTER to continue...")


def _print_session_end(s: GameState, weeks_played: int) -> None:
    prices = _prices_dict(s.companies)
    final   = s.portfolio.total_value(prices)
    net_pct = (final - STARTING_CREDITS) / STARTING_CREDITS * 100
    sign    = "+" if net_pct >= 0 else ""
    print(f"\n{DIVIDER}")
    print("  SESSION COMPLETE")
    print(DIVIDER)
    print(f"  Weeks played:  {weeks_played}")
    print(f"  Final value:   {_fmt_cr(final)}")
    print(f"  Starting:      {_fmt_cr(STARTING_CREDITS)}")
    print(f"  Net return:    {sign}{net_pct:.1f}%")
    print(DIVIDER)


def _show_shells(s: GameState) -> None:
    market = s.market
    runners = roster_all_runners(s.rosters)
    total   = len(runners)
    counts  = Counter(r.current_shell for r in runners)

    prev_prices = market.price_history[-2] if len(market.price_history) >= 2 else {
        sh.name: BASE_SHELL_PRICE for sh in SHELL_ROSTER
    }

    print(f"\n{DIVIDER}")
    print(f"  SHELL MARKET")
    print(DIVIDER)
    print(f"\n  {'Shell':<10}  {'Price':>8}  {'Δ wk':>8}  {'':1}  {'Trend':>7}  {'Adoption':>13}")
    print(f"  {'─'*10}  {'─'*8}  {'─'*8}  {'─'*1}  {'─'*7}  {'─'*13}")

    for shell in sorted(SHELL_ROSTER, key=lambda s: -market.prices[s.name]):
        price     = market.prices[shell.name]
        prev      = prev_prices.get(shell.name, BASE_SHELL_PRICE)
        delta     = price - prev
        delta_str = f"{delta:+.1f}" if abs(delta) >= 0.05 else "  —  "
        arrow     = _trend_arrow(delta)
        spark     = _sparkline([snap.get(shell.name, BASE_SHELL_PRICE)
                                for snap in market.price_history])
        count     = counts.get(shell.name, 0)
        pct       = 100 * count / total if total else 0
        tag       = " ★" if shell.name in PREMIUM_SHELLS else "  "
        print(f"  {shell.name:<10}  {price:>7.1f}cr  {delta_str:>8}  {arrow:1}  "
              f"{spark:>7}  {count:>2} ({pct:4.1f}%){tag}")

    premium_count = sum(counts.get(s, 0) for s in PREMIUM_SHELLS)
    middle_count  = total - premium_count
    print(f"\n  ★ Premium (Destroyer/Thief/Triage):  "
          f"{premium_count}/{total} ({100*premium_count/total:.1f}%)" if total else "")
    print(f"    Middle shells:  "
          f"{middle_count}/{total} ({100*middle_count/total:.1f}%)" if total else "")
    print(f"    Fair share (uniform): {100/N_SHELLS:.1f}% per shell")
    input("\nPress ENTER to return...")


# ---------------------------------------------------------------------------
# INPUT HANDLERS
# ---------------------------------------------------------------------------
def _handle_buy(s: GameState) -> None:
    raw = input(f"Buy which company?  {_company_shortcuts(s.companies)}\n> ").strip()
    company = _find_company(s.companies, raw)
    if company is None:
        print(f"  Unknown company '{raw}'.")
        return
    affordable = int(s.portfolio.credits / company.price)
    raw_shares = input(f"How many shares? (you can afford up to {affordable})\n> ").strip()
    try:
        shares = int(raw_shares)
    except ValueError:
        print("  Enter a whole number.")
        return
    err = s.portfolio.buy(company.name, shares, company.price)
    if err:
        print(f"  Error: {err}")
    else:
        print(f"  Bought {shares} share(s) of {company.name} @ {company.price:.0f} cr.  "
              f"Credits remaining: {_fmt_cr(s.portfolio.credits)}")


def _handle_sell(s: GameState) -> None:
    if not s.portfolio.holdings:
        print("  You have no shares to sell.")
        return
    raw = input(f"Sell which company?  {_company_shortcuts(s.companies)}\n> ").strip()
    company = _find_company(s.companies, raw)
    if company is None:
        print(f"  Unknown company '{raw}'.")
        return
    held = s.portfolio.holdings.get(company.name, 0)
    if held == 0:
        print(f"  You hold no shares of {company.name}.")
        return
    raw_shares = input(f"How many shares? (you hold {held})\n> ").strip()
    try:
        shares = int(raw_shares)
    except ValueError:
        print("  Enter a whole number.")
        return
    err = s.portfolio.sell(company.name, shares, company.price)
    if err:
        print(f"  Error: {err}")
    else:
        print(f"  Sold {shares} share(s) of {company.name} @ {company.price:.0f} cr.  "
              f"Credits remaining: {_fmt_cr(s.portfolio.credits)}")


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
def run_console() -> None:
    import sys
    debug    = True
    trace_ai = "--trace-ai" in sys.argv

    if trace_ai:
        from ai_tree.trace import Tracer
        Tracer.enable()

    print("\n" + "=" * 52)
    print("   MARATHON MARKET SIMULATOR  [CONSOLE MODE]")
    print("   Tau Ceti IV — Financial Intelligence Division")
    print("=" * 52)
    if debug:
        print("   [DEBUG MODE]")
    if trace_ai:
        print("   [TRACE-AI]")
    print(f"\nStarting capital: {_fmt_cr(STARTING_CREDITS)}")
    input("\nPress ENTER to begin...\n")

    engine = GameEngine(debug=debug)
    s      = engine.state

    while True:
        value_before = s.portfolio.total_value(_prices_dict(s.companies))

        _print_planning(s)
        while True:
            action = input("> ").strip().upper()
            if action in ("", "H", "HOLD"):
                break
            if action == "Q":
                _print_session_end(s, s.week - 1)
                return
            elif action == "B":
                _handle_buy(s)
                _print_planning(s)
            elif action == "S":
                _handle_sell(s)
                _print_planning(s)
            elif action == "A":
                msg = engine.do_all_in()
                print(f"  {msg}")
                _print_planning(s)
            elif action == "K":
                _show_shells(s)
                _print_planning(s)
            else:
                print("  Enter B, S, A, K, H, or Q.")

        print(f"\n  Simulating week {s.week}...")
        engine.advance_week()
        _print_results(s, value_before)

    _print_session_end(s, s.week - 1)
