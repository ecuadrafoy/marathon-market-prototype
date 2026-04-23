"""
Marathon Market Simulator — Python Prototype
Console-only. Run: uv run python marathon_market.py
Debug mode (shows all zones): uv run python marathon_market.py --debug
"""

from __future__ import annotations
import io
import random
import sys
import time
from dataclasses import dataclass, field

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS — adjust here if market behavior needs calibration
# ---------------------------------------------------------------------------
TOTAL_RUNNERS       = 30      # fixed global runner pool split across all zones per week
MIN_ZONE_RUNNERS    = 6       # minimum runners assigned to any one zone
RUNNER_SKILL_MEAN   = 0.5
RUNNER_SKILL_SD     = 0.15
STARTING_CREDITS    = 10_000.0
PRICE_FLOOR         = 1.0
HEADCOUNT_SCALE     = 0.2     # how much extra runners inflate baseline expectation
MIN_RUNNERS_PER_CO  = 3       # 1 per zone × 3 zones
MAX_RUNNERS_PER_CO  = 15      # ~50% cap per zone × 3 zones
BASE_EXPECTATION    = 34.4    # empirical average performance score across zones (1000-week simulation)
MAX_PERF_SCORE      = 150.0   # normalization ceiling for price-change formula
DELTA_MULTIPLIER    = 10.0    # per spec
NOISE_RANGE         = 2.0     # uniform ±2%
SIM_PAUSE_SECS      = 0.8     # cosmetic delay during simulation


# ---------------------------------------------------------------------------
# DATA STRUCTURES
# ---------------------------------------------------------------------------
@dataclass
class Zone:
    name: str
    difficulty: float  # 0–1 scale, same as skill; subtracted directly from success probability
    monitored: bool    # True = player sees runner counts here


ZONES: list[Zone] = [
    Zone("Sector 7",   difficulty=0.1, monitored=True),
    Zone("Deep Reach", difficulty=0.3, monitored=False),
    Zone("The Shelf",  difficulty=0.5, monitored=False),
]


@dataclass
class Runner:
    zone_name: str
    skill: float          # [0.0, 1.0]
    company_name: str = ""
    success: bool = False
    yield_value: float = 0.0


@dataclass
class Company:
    name: str
    price: float


@dataclass
class CompanyWeekResult:
    company_name: str
    runner_count: int            # total across ALL zones
    successes: int
    success_rate: float
    average_yield: float         # 0.0 if no successes
    performance_score: float
    baseline: float
    delta: float
    price_change_pct: float
    price_before: float
    price_after: float
    monitored_runner_count: int  # player's zone only — for display
    monitored_successes: int
    monitored_average_yield: float


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
# STEP 1 — RUNNER & ZONE GENERATION
# ---------------------------------------------------------------------------
def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _distribute_zone_sizes(total: int, zones: list[Zone]) -> dict[str, int]:
    """Split total runners across zones, each zone gets at least MIN_ZONE_RUNNERS."""
    counts = {z.name: MIN_ZONE_RUNNERS for z in zones}
    surplus = total - sum(counts.values())
    names = [z.name for z in zones]
    for _ in range(surplus):
        counts[random.choice(names)] += 1
    return counts


def _allocate_to_companies(zone_runner_count: int, company_names: list[str]) -> dict[str, int]:
    """Distribute runners within a zone to companies: min 1 each, no company > 50%."""
    allocation = {name: 1 for name in company_names}
    surplus = zone_runner_count - len(company_names)
    cap = zone_runner_count // 2
    for _ in range(surplus):
        eligible = [n for n in company_names if allocation[n] < cap]
        allocation[random.choice(eligible)] += 1
    return allocation


def assign_runners_standard(total: int, zones: list[Zone], company_names: list[str]) -> list[Runner]:
    """Distribute runners randomly across zones, then assign to companies within each zone."""
    zone_sizes = _distribute_zone_sizes(total, zones)
    runners: list[Runner] = []
    for zone in zones:
        count = zone_sizes[zone.name]
        allocation = _allocate_to_companies(count, company_names)
        for company, n in allocation.items():
            for _ in range(n):
                skill = _clamp(random.gauss(RUNNER_SKILL_MEAN, RUNNER_SKILL_SD), 0.0, 1.0)
                runners.append(Runner(
                    zone_name=zone.name,
                    skill=skill,
                    company_name=company,
                ))
    return runners


def assign_runners_skill_matched(total: int, zones: list[Zone], company_names: list[str]) -> list[Runner]:
    """
    Generate all runners first, then assign each to a zone with probability
    proportional to zone.difficulty × runner.skill — skilled runners favour harder zones.
    Zone capacity is pre-determined so every zone meets its minimum.
    """
    zone_sizes = _distribute_zone_sizes(total, zones)
    remaining_capacity = dict(zone_sizes)

    # Generate all skills upfront, sort descending so elite runners pick first
    all_skills = sorted(
        [_clamp(random.gauss(RUNNER_SKILL_MEAN, RUNNER_SKILL_SD), 0.0, 1.0) for _ in range(total)],
        reverse=True,
    )

    zone_skill_buckets: dict[str, list[float]] = {z.name: [] for z in zones}
    for skill in all_skills:
        eligible = [z for z in zones if remaining_capacity[z.name] > 0]
        # Weight: harder zones attract more skilled runners; +0.1 avoids zero weights
        weights = [z.difficulty * skill + 0.1 for z in eligible]
        chosen = random.choices(eligible, weights=weights, k=1)[0]
        zone_skill_buckets[chosen.name].append(skill)
        remaining_capacity[chosen.name] -= 1

    runners: list[Runner] = []
    zone_map = {z.name: z for z in zones}
    for zone_name, skills in zone_skill_buckets.items():
        zone = zone_map[zone_name]
        allocation = _allocate_to_companies(len(skills), company_names)
        # Shuffle skills before assigning to companies so company↔skill pairing is random
        shuffled = list(skills)
        random.shuffle(shuffled)
        idx = 0
        for company, n in allocation.items():
            for _ in range(n):
                runners.append(Runner(
                    zone_name=zone.name,
                    skill=shuffled[idx],
                    company_name=company,
                ))
                idx += 1
    return runners


def build_monitored_allocation(runners: list[Runner], monitored_zone_name: str) -> dict[str, int]:
    """Return per-company headcount for the player's monitored zone."""
    counts: dict[str, int] = {}
    for r in runners:
        if r.zone_name == monitored_zone_name:
            counts[r.company_name] = counts.get(r.company_name, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# STEP 2 — WEEK RESOLUTION
# ---------------------------------------------------------------------------
def resolve_runner(runner: Runner, zone_map: dict[str, Zone]) -> Runner:
    """Mutates runner in-place. Looks up difficulty from zone_map via zone_name."""
    difficulty = zone_map[runner.zone_name].difficulty
    p = _clamp(runner.skill - difficulty, 0.0, 1.0)
    runner.success = random.random() < p
    if runner.success:
        runner.yield_value = (50.0 + runner.skill * 100.0) * (1.0 + difficulty ** 2 * 8)
    return runner


def compute_company_result(
    company: Company,
    all_runners: list[Runner],
    monitored_zone_name: str,
    zone_map: dict[str, Zone],
) -> CompanyWeekResult:
    company_runners = [r for r in all_runners if r.company_name == company.name]
    for r in company_runners:
        resolve_runner(r, zone_map)

    # Aggregate across all zones — drives price
    total = len(company_runners)
    successes = sum(1 for r in company_runners if r.success)
    success_rate = successes / total if total > 0 else 0.0
    successful_yields = [r.yield_value for r in company_runners if r.success]
    average_yield = sum(successful_yields) / len(successful_yields) if successful_yields else 0.0
    performance_score = success_rate * average_yield
    baseline = _compute_baseline(total)
    delta = performance_score - baseline
    price_change_pct = _compute_price_change_pct(performance_score, baseline)
    price_before = company.price
    price_after = max(price_before * (1.0 + price_change_pct / 100.0), PRICE_FLOOR)

    # Monitored zone only — display intel for the player
    mon = [r for r in company_runners if r.zone_name == monitored_zone_name]
    mon_successes = sum(1 for r in mon if r.success)
    mon_yields = [r.yield_value for r in mon if r.success]
    mon_avg_yield = sum(mon_yields) / len(mon_yields) if mon_yields else 0.0

    return CompanyWeekResult(
        company_name=company.name,
        runner_count=total,
        successes=successes,
        success_rate=success_rate,
        average_yield=average_yield,
        performance_score=performance_score,
        baseline=baseline,
        delta=delta,
        price_change_pct=price_change_pct,
        price_before=price_before,
        price_after=price_after,
        monitored_runner_count=len(mon),
        monitored_successes=mon_successes,
        monitored_average_yield=mon_avg_yield,
    )


# ---------------------------------------------------------------------------
# STEP 3 — MARKET RESPONSE
# ---------------------------------------------------------------------------
def _compute_baseline(runner_count: int) -> float:
    """Market expectation scales slightly with total runners across all zones."""
    factor = 1.0 + (
        (runner_count - MIN_RUNNERS_PER_CO) /
        (MAX_RUNNERS_PER_CO - MIN_RUNNERS_PER_CO)
    ) * HEADCOUNT_SCALE
    return BASE_EXPECTATION * factor


def _compute_price_change_pct(score: float, baseline: float) -> float:
    """
    Normalize delta against the theoretical max so the ×10 multiplier
    produces ≈2–8% weekly swings rather than hundreds of percent.
    """
    delta = score - baseline
    normalized = delta / MAX_PERF_SCORE
    noise = random.uniform(-NOISE_RANGE, NOISE_RANGE)
    return (normalized * DELTA_MULTIPLIER) + noise


# ---------------------------------------------------------------------------
# STEP 4 — PLAYER LAYER
# ---------------------------------------------------------------------------
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
# STEP 5 — CONSOLE UI & GAME LOOP
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
    if delta > 2.0:
        return "beat expectations"
    if delta < -2.0:
        return "missed expectations"
    return "met expectations"


def print_header(week: int) -> None:
    print(f"\n{DIVIDER}")
    print(f"  MARATHON MARKET SIMULATOR — WEEK {week}")
    print(DIVIDER)


def select_runner_mode() -> bool:
    """Returns True if skill-matched mode was selected."""
    print(f"\n{DIVIDER}")
    print("  RUNNER ASSIGNMENT MODE")
    print(DIVIDER)
    print("  [S]tandard  — runners distributed randomly across zones")
    print("  [M]atched   — skilled runners favour harder zones")
    while True:
        choice = input("\n> ").strip().upper()
        if choice in ("S", "STANDARD", ""):
            print("  Standard mode selected.")
            return False
        if choice in ("M", "MATCHED"):
            print("  Skill-matched mode selected.")
            return True
        print("  Enter S or M.")


def print_planning_phase(
    week: int,
    companies: list[Company],
    monitored_allocation: dict[str, int],
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

    # Zone intel — monitored zone only
    print(f"\nZONE INTEL — {monitored_zone.name}  ({_difficulty_label(monitored_zone.difficulty)})")
    for company in companies:
        count = monitored_allocation.get(company.name, 0)
        print(f"  {company.name:<12} {count} runner{'s' if count != 1 else ''}")

    # Market prices with last week's change
    print(f"\nMARKET PRICES")
    for company in companies:
        line = f"  {company.name:<12} {company.price:>6.0f} cr"
        if last_results:
            for r in last_results:
                if r.company_name == company.name:
                    line += f"  ({_fmt_pct(r.price_change_pct)} last week)"
        print(line)

    print(f"\n[B]uy  [S]ell  [A]ll in  [H]old / advance week  [Q]uit")


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
    monitored_allocation: dict[str, int],
    monitored_zone: Zone,
    portfolio: Portfolio,
    last_results: list[CompanyWeekResult] | None,
) -> bool:
    """Returns False if the player chose to quit."""
    print_planning_phase(week, companies, monitored_allocation, monitored_zone, portfolio, last_results)
    while True:
        action = input("> ").strip().upper()
        if action in ("", "H", "HOLD"):
            return True
        if action == "Q":
            return False
        if action == "B":
            handle_buy(companies, portfolio)
            print_planning_phase(week, companies, monitored_allocation, monitored_zone, portfolio, last_results)
        elif action == "S":
            handle_sell(companies, portfolio)
            print_planning_phase(week, companies, monitored_allocation, monitored_zone, portfolio, last_results)
        elif action == "A":
            handle_all_in(companies, portfolio)
            print_planning_phase(week, companies, monitored_allocation, monitored_zone, portfolio, last_results)
        else:
            print("  Enter B, S, A, H, or Q.")


def _print_zone_breakdown(all_runners: list[Runner], zones: list[Zone], companies: list[Company]) -> None:
    """Debug view: runner outcomes broken down by every zone."""
    print(f"\nALL ZONES BREAKDOWN  [debug]")
    for zone in zones:
        tag = " ★ monitored" if zone.monitored else " · hidden"
        zone_runners = [r for r in all_runners if r.zone_name == zone.name]
        avg_skill = sum(r.skill for r in zone_runners) / len(zone_runners) if zone_runners else 0.0
        print(f"  {zone.name} ({_difficulty_label(zone.difficulty)}){tag}  avg skill {avg_skill:.2f}")
        for company in companies:
            co_runners = [r for r in zone_runners if r.company_name == company.name]
            if not co_runners:
                continue
            total = len(co_runners)
            successes = sum(1 for r in co_runners if r.success)
            yields = [r.yield_value for r in co_runners if r.success]
            avg_yield = sum(yields) / len(yields) if yields else 0.0
            yield_str = f"avg yield {avg_yield:>5.0f} cr" if successes else "---"
            print(f"    {company.name:<12} ({total})  {successes}/{total} success   {yield_str}")


def print_results(
    results: list[CompanyWeekResult],
    monitored_zone: Zone,
    portfolio: Portfolio,
    portfolio_value_before: float,
    companies: list[Company],
    all_runners: list[Runner] | None = None,
    debug: bool = False,
) -> None:
    print(f"\n{DIVIDER}")
    print("  RESULTS")
    print(DIVIDER)

    if debug and all_runners is not None:
        _print_zone_breakdown(all_runners, ZONES, companies)

    # Monitored zone outcomes — what the player had signal on
    print(f"\nYOUR ZONE — {monitored_zone.name}  ({_difficulty_label(monitored_zone.difficulty)})")
    for r in results:
        count = r.monitored_runner_count
        if r.monitored_successes > 0:
            yield_str = f"avg yield {r.monitored_average_yield:>5.0f} cr"
        else:
            yield_str = "---"
        print(f"  {r.company_name:<12} ({count})  "
              f"{r.monitored_successes}/{count} success   {yield_str}")

    # Market response — driven by all zones combined
    print(f"\nMARKET RESPONSE  (all zones)")
    for r in results:
        label = _expectation_label(r.delta)
        print(f"  {r.company_name:<12} {r.price_before:>6.0f} → {r.price_after:>6.0f} cr  "
              f"({_fmt_pct(r.price_change_pct):>6})  [{label}]")

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


def print_session_end(week: int, portfolio: Portfolio) -> None:
    final = portfolio.credits + sum(portfolio.holdings.values())
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


def run_game() -> None:
    debug = "--debug" in sys.argv

    print("\n" + "=" * 52)
    print("   MARATHON MARKET SIMULATOR")
    print("   Tau Ceti IV — Financial Intelligence Division")
    print("=" * 52)
    if debug:
        print("   [DEBUG MODE — all zones visible]")
    print(f"\nStarting capital: {_fmt_cr(STARTING_CREDITS)}")
    print(f"Zones: {len(ZONES)} total  |  Monitoring: 1  |  Press Q to quit")

    skill_matched = select_runner_mode()

    input("\nPress ENTER to begin...\n")

    companies: list[Company] = [
        Company("CyberAcme", 450.0),
        Company("Sekiguchi",  380.0),
        Company("Traxus",     300.0),
        Company("NuCaloric",  200.0),
    ]
    company_names = [c.name for c in companies]
    monitored_zone = next(z for z in ZONES if z.monitored)
    zone_map = {z.name: z for z in ZONES}

    portfolio = Portfolio()
    last_results: list[CompanyWeekResult] | None = None
    week = 1

    while True:
        # Generate all runners for this week across all zones
        if skill_matched:
            all_runners = assign_runners_skill_matched(TOTAL_RUNNERS, ZONES, company_names)
        else:
            all_runners = assign_runners_standard(TOTAL_RUNNERS, ZONES, company_names)

        monitored_allocation = build_monitored_allocation(all_runners, monitored_zone.name)
        value_before = portfolio.total_value(_prices_dict(companies))

        # Planning phase
        still_playing = planning_loop(
            week, companies, monitored_allocation, monitored_zone, portfolio, last_results
        )
        if not still_playing:
            break

        # Simulation pause
        print(f"\n  Simulating week {week}...")
        time.sleep(SIM_PAUSE_SECS)

        # Resolve all runners and compute results
        results: list[CompanyWeekResult] = []
        for company in companies:
            result = compute_company_result(company, all_runners, monitored_zone.name, zone_map)
            company.price = result.price_after
            results.append(result)

        print_results(results, monitored_zone, portfolio, value_before, companies, all_runners, debug)

        last_results = results
        week += 1

    print_session_end(week, portfolio)


if __name__ == "__main__":
    run_game()
