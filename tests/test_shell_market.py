"""
Tests for runner_sim/market/shell_market.py

Key invariants:
  - update_prices: fair adoption → base price; monopoly → expensive winner, cheap others
  - choose_affordable_shell: budget=0 → cheapest available; big budget → capability-optimal
  - prices are always positive (the formula can produce negatives at extreme k values — verify
    our current k=4.0 doesn't do this at realistic adoption levels)
"""

import pytest

from runner_sim.market.shell_market import (
    BASE_SHELL_PRICE,
    SHELL_PRICE_SENSITIVITY,
    ShellMarket,
    choose_affordable_shell,
    make_initial_market,
    update_prices,
)
from runner_sim.runners import Runner
from runner_sim.shells import SHELL_ROSTER, SHELL_BY_NAME


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_runner(shell_name: str, combat: float = 0.5, extraction: float = 0.3,
                 support: float = 0.2) -> Runner:
    """Minimal runner fixture — only fields used by shell_market functions."""
    return Runner(
        id=0, name="Test", company_name="Co",
        combat=combat, extraction=extraction, support=support,
        current_shell=shell_name,
    )


def _make_roster_wearing(shell_name: str, count: int) -> list[Runner]:
    """Build a list of runners all wearing the same shell."""
    return [_make_runner(shell_name) for _ in range(count)]


# ---------------------------------------------------------------------------
# update_prices
# ---------------------------------------------------------------------------
class TestUpdatePrices:
    def test_fair_adoption_equals_base_price(self):
        """If every shell is worn by exactly 1/N runners, all prices == BASE_SHELL_PRICE."""
        market = make_initial_market()
        n = len(SHELL_ROSTER)
        runners = []
        for shell in SHELL_ROSTER:
            runners.extend(_make_roster_wearing(shell.name, n))   # n runners each → 1/N share
        update_prices(market, runners)
        for shell in SHELL_ROSTER:
            assert market.prices[shell.name] == pytest.approx(BASE_SHELL_PRICE, rel=1e-6)

    def test_monopoly_shell_is_most_expensive(self):
        """A shell worn by 100% of runners should be priced highest."""
        market = make_initial_market()
        runners = _make_roster_wearing("Destroyer", 36)
        update_prices(market, runners)
        destroyer_price = market.prices["Destroyer"]
        for shell in SHELL_ROSTER:
            if shell.name != "Destroyer":
                assert destroyer_price > market.prices[shell.name]

    def test_zero_adoption_shell_is_cheapest(self):
        """A shell worn by nobody should cost less than the base price."""
        market = make_initial_market()
        # All 36 runners on Destroyer — every other shell has 0% adoption
        runners = _make_roster_wearing("Destroyer", 36)
        update_prices(market, runners)
        for shell in SHELL_ROSTER:
            if shell.name != "Destroyer":
                assert market.prices[shell.name] < BASE_SHELL_PRICE

    def test_prices_always_non_negative(self):
        """Prices must stay positive even at 0% adoption with k=4.0."""
        market = make_initial_market()
        runners = _make_roster_wearing("Destroyer", 36)
        update_prices(market, runners)
        for price in market.prices.values():
            assert price > 0

    def test_history_appended_each_call(self):
        """Each update_prices call adds one snapshot to both history lists."""
        market = make_initial_market()
        runners = _make_roster_wearing("Destroyer", 10)
        update_prices(market, runners)
        update_prices(market, runners)
        assert len(market.adoption_history) == 2
        assert len(market.price_history) == 2

    def test_empty_runner_list_does_not_crash(self):
        """Empty roster is a valid edge case — prices should not update."""
        market = make_initial_market()
        before = dict(market.prices)
        update_prices(market, [])
        assert market.prices == before


# ---------------------------------------------------------------------------
# choose_affordable_shell
# ---------------------------------------------------------------------------
class TestChooseAffordableShell:
    def test_zero_budget_returns_cheapest_shell(self):
        """With no money, the runner takes whatever costs least."""
        market = make_initial_market()   # uniform BASE_SHELL_PRICE — all equal
        runner = _make_runner("Destroyer")
        shell = choose_affordable_shell(runner, market.prices, budget=0.0)
        cheapest_price = min(market.prices.values())
        assert market.prices[shell.name] == pytest.approx(cheapest_price)

    def test_large_budget_returns_a_shell(self):
        """With unlimited budget, must always return something."""
        market = make_initial_market()
        runner = _make_runner("Destroyer", combat=0.8, extraction=0.1, support=0.1)
        shell = choose_affordable_shell(runner, market.prices, budget=99_999.0)
        assert shell is not None
        assert shell.name in {s.name for s in SHELL_ROSTER}

    def test_result_is_within_budget(self):
        """Shell price must not exceed the given budget."""
        market = make_initial_market()
        # Make Destroyer expensive, so a modest budget is forced to middle shells
        market.prices["Destroyer"] = 999.0
        market.prices["Thief"]     = 999.0
        market.prices["Triage"]    = 999.0
        runner = _make_runner("Vandal", combat=0.5, extraction=0.3, support=0.2)
        budget = 250.0
        shell = choose_affordable_shell(runner, market.prices, budget=budget)
        assert market.prices[shell.name] <= budget
