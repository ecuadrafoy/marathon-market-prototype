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
    _registry_groups,
    _runner_top_affinity,
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
        n = len(roster.runners)
        avg = (sum(r.extraction_attempts for r in roster.runners) / n) if n else 0.0
        events = (s.last_roster_events or {}).get(company.name)
        change_str = ""
        if events is not None:
            bits = []
            if events.signed:                 bits.append(f"+{len(events.signed)} signed")
            if events.orphaned_unaffordable:  bits.append(f"-{len(events.orphaned_unaffordable)} orphaned")
            if events.voluntarily_dropped:    bits.append(f"-{len(events.voluntarily_dropped)} dropped")
            if bits:
                change_str = "  [" + ", ".join(bits) + "]"
        # pending_valuation_delta is a SCORE (counter), not cr.
        # Project the cr it will turn into at the next quarterly report.
        from marathon_market import VALUATION_CR_PER_COUNTER
        score = company.pending_valuation_delta
        projected_cr = score * VALUATION_CR_PER_COUNTER
        pending_str = f" ({score:+.0f}→{projected_cr:+.0f}cr)" if score != 0 else ""
        print(f"  {company.name:<12} {n} runners  "
              f"avg {avg:.1f} extr, {roster.total_deaths} deaths  "
              f"budget {company.budget:>5.0f} cr  "
              f"val {company.valuation:>5.0f}{pending_str}{change_str}")

    if s.free_agents:
        idle = max((r.weeks_orphaned for r in s.free_agents), default=0)
        avg_up = sum(r.upkeep_cost or 0 for r in s.free_agents) / len(s.free_agents)
        print(f"\nFREE AGENTS  {len(s.free_agents)} idle  "
              f"avg upkeep {avg_up:.0f} cr  longest idle {idle}w")

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

    # News ticker — last few events, most recent first.
    if s.news_feed:
        print(f"\nNEWS")
        for item in reversed(s.news_feed[-5:]):
            print(f"  W{item.week:>2}  {item.text}")

    print(f"\n[B]uy  [S]ell  [A]ll in  s[K] shells  [R]oster  [N]ews  [H]old / advance week  [Q]uit")


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

    # Roster outcome — results phase shows ONLY what happened during deployment.
    if s.last_roster_events:
        print(f"\nROSTER OUTCOME")
        for company in s.companies:
            ev = s.last_roster_events.get(company.name)
            died = len(ev.died) if ev else 0
            if died:
                print(f"  {company.name:<12} -{died} lost in deployment")
            else:
                print(f"  {company.name:<12} roster held — no casualties")

    # Quarterly valuation report — fires every QUARTERLY_REPORT_WEEKS weeks.
    # Wrapped in ANSI orange-background escape codes so the report state is
    # visually distinct from a normal results week. Terminals that don't
    # support ANSI will show the literal escape sequences, which is ugly but
    # not breaking; the TUI is the primary surface for this state anyway.
    if s.last_quarterly_reports:
        # \033[48;5;208m = orange background; \033[30m = black foreground; \033[0m = reset
        ORANGE = "\033[48;5;208m\033[30m"
        RESET = "\033[0m"
        WIDTH = 60  # band width
        def _band(text: str) -> str:
            padded = text + " " * max(0, WIDTH - len(text))
            return f"{ORANGE}{padded}{RESET}"
        print()
        print(_band(" "))
        print(_band(f"  ⚑ QUARTERLY VALUATION REPORT  ·  Week {s.week - 1}"))
        print(_band(" "))
        for company in s.companies:
            entry = s.last_quarterly_reports.get(company.name)
            if entry is None:
                continue
            before, delta, after = entry
            sign = "+" if delta >= 0 else ""
            pct = (delta / before * 100) if before > 0 else 0.0
            line = (f"  {company.name:<12} {before:>6.0f} → {after:>6.0f} cr  "
                    f"({sign}{delta:.0f}, {sign}{pct:.1f}%)")
            print(_band(line))
        print(_band(" "))

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
        market = s.market
        prev = (market.price_history[-2] if len(market.price_history) >= 2
                else {sh.name: BASE_SHELL_PRICE for sh in SHELL_ROSTER})
        print(f"\nSHELL MARKET")
        for shell, price in sorted(market.prices.items(), key=lambda kv: -kv[1]):
            delta     = price - prev.get(shell, BASE_SHELL_PRICE)
            delta_str = f"{delta:+.1f}" if abs(delta) >= 0.05 else "  —  "
            print(f"  {shell:<10} {price:>6.1f} cr  ({delta_str})")

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


def _show_runners(s: GameState) -> None:
    """Runner registry — every runner in the world, grouped by contract.

    Each row shows: name, current shell, weekly upkeep cost, deployments
    survived, career kills, career net loot, top shell affinity, and (for
    free agents) how long they've been idle. Columns are sized so a typical
    80-column terminal fits cleanly.
    """
    print(f"\n{DIVIDER}")
    print(f"  RUNNER REGISTRY  ·  Week {s.week}")
    print(DIVIDER)

    # Column widths: Name 10 · Shell 10 · Upkeep 8 · Surv 4 · Kills 5 · Loot 7 · Affinity 12 · Idle 4
    header = (
        f"    {'Name':<10}  {'Shell':<10}  {'Upkeep':>8}  "
        f"{'Surv':>4}  {'Kills':>5}  {'Loot':>7}  {'Affinity':<12}  {'Idle':>4}"
    )
    rule = "    " + "─" * (len(header) - 4)

    groups = _registry_groups(s)
    for group_name, payroll, runners in groups:
        is_free_agents = (group_name == "FREE AGENTS")
        n = len(runners)
        if is_free_agents:
            longest = max((r.weeks_orphaned for r in runners), default=0)
            section_header = f"FREE AGENTS  ({n} idle · longest idle {longest}w)"
        else:
            section_header = f"{group_name}  ({n} runners · payroll {payroll} cr/wk)"

        print(f"\n  [ {section_header} ]")
        print(header)
        print(rule)

        for r in runners:
            shell_label = r.current_shell if r.current_shell else "—"
            aff_shell, aff_value = _runner_top_affinity(r)
            aff_str = f"{aff_shell[:3]} {aff_value:.2f}" if aff_value > 0 else "—"
            premium = aff_shell in PREMIUM_SHELLS and aff_value >= 0.3
            aff_cell = f"{aff_str}{' ★' if premium else ''}"
            idle_str = f"{r.weeks_orphaned}w" if is_free_agents else " —"
            print(
                f"    {r.name[:10]:<10}  {shell_label[:10]:<10}  "
                f"{r.upkeep_cost:>6.0f}cr  {r.deployments_survived:>4d}  "
                f"{r.eliminations:>5d}  {r.net_loot:>7.0f}  "
                f"{aff_cell:<12}  {idle_str:>4}"
            )

        if not runners:
            print("    (none)")

    total_runners = sum(len(rs) for _, _, rs in groups)
    print(f"\n  Total runners in world: {total_runners}")
    print("  ★ marks veterans with ≥ 0.30 affinity to a premium shell (Destroyer/Thief/Triage)")
    input("\nPress ENTER to return...")


def _show_news_history(s: GameState) -> None:
    """Full-screen news view — every event still in the rolling feed,
    most recent at top, grouped by week with a thin separator.
    """
    print(f"\n{DIVIDER}")
    print(f"  NEWS HISTORY  ·  Week {s.week}  ·  {len(s.news_feed)} events buffered")
    print(DIVIDER)

    if not s.news_feed:
        print("\n  (no events yet — advance the week to populate the feed)")
        input("\nPress ENTER to return...")
        return

    prev_week = None
    for item in reversed(s.news_feed):
        # Visual separator between distinct week blocks
        if prev_week is not None and item.week != prev_week:
            print("  " + "─" * 50)
        prev_week = item.week
        company_tag = f"[{item.company_name}]" if item.company_name else "[—]"
        print(f"  W{item.week:>3}  {company_tag:<14} {item.text}")
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
            elif action == "R":
                _show_runners(s)
                _print_planning(s)
            elif action == "N":
                _show_news_history(s)
                _print_planning(s)
            else:
                print("  Enter B, S, A, K, R, N, H, or Q.")

        print(f"\n  Simulating week {s.week}...")
        engine.advance_week()
        _print_results(s, value_before)

    _print_session_end(s, s.week - 1)
