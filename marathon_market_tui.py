"""
Marathon Market Simulator — Textual TUI
Launched by marathon_market.py when run as __main__.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import plotext as _plt
from rich.text import Text as RichText

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, Static

from marathon_market import (
    GameEngine,
    GameState,
    PREMIUM_SHELLS,
    STARTING_CREDITS,
    _build_sector7_previews,
    _difficulty_label,
    _fmt_cr,
    _fmt_pct,
    _prices_dict,
    _sparkline,
    _trend_arrow,
    _expectation_label,
    roster_all_runners,
)
from runner_sim.market.shell_market import BASE_SHELL_PRICE, N_SHELLS
from runner_sim.shells import SHELL_ROSTER


# ---------------------------------------------------------------------------
# MESSAGES
# ---------------------------------------------------------------------------
class WeekComplete(Message):
    pass


# ---------------------------------------------------------------------------
# CHART CONSTANTS
# ---------------------------------------------------------------------------
# plotext color per company — RGB tuples avoid name-lookup ambiguity across versions
_CHART_COLOR: dict[str, Any] = {
    "CyberAcme": (0, 210, 80),      # green
    "Sekiguchi":  (0, 200, 220),     # cyan
    "Traxus":     (255, 140, 0),     # orange
    "NuCaloric":  (220, 50, 50),     # red
}
_EXP_DOT_COLOR = {"beat": "green+", "met": "yellow", "missed": "red+"}


# ---------------------------------------------------------------------------
# WIDGETS
# ---------------------------------------------------------------------------
class CompanyPanel(Widget):
    """Displays one company: current price, plotext line chart, compact shell row."""

    def __init__(self, company_name: str) -> None:
        super().__init__(id=company_name.lower())
        self.company_name = company_name
        self.border_title = company_name
        self._state: GameState | None = None

    def refresh_content(self, state: GameState) -> None:
        self._state = state
        self.refresh()

    def render(self) -> RichText:
        if self._state is None or self.size.height < 4:
            return RichText("")

        state = self._state
        company = next(c for c in state.companies if c.name == self.company_name)
        # subtract 2 for border, 2 for padding (CompanyPanel has padding: 0 1)
        content_width = max(12, self.size.width - 4)
        # pass available height; _render_chart caps internally to avoid stretching
        chart_height = max(3, self.size.height - 2 - 3)

        text = RichText()

        # ── Price + last-week change ─────────────────────────────────────────
        text.append(f"{company.price:.1f} cr", style="bold")
        if state.last_results:
            for r in state.last_results:
                if r.company_name == self.company_name:
                    pct = r.price_change_pct
                    text.append(
                        f"  {'+' if pct >= 0 else ''}{pct:.1f}%",
                        style="green" if pct >= 0 else "red",
                    )
        text.append("\n")

        # ── Plotext line chart ───────────────────────────────────────────────
        text.append_text(self._render_chart(state, content_width, chart_height))

        # ── Shell composition (one compact line) ─────────────────────────────
        text.append("─" * content_width + "\n", style="dim")
        self._render_shells_compact(text, state)

        return text

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _render_chart(self, state: GameState, width: int, height: int) -> RichText:
        prices = state.price_history.get(self.company_name, [])

        if len(prices) < 2:
            # No weeks simulated yet — blank padding so layout is stable
            return RichText("\n" * height)

        # Show up to 7 points: the initial price (week 0) plus 6 weekly closes
        window = prices[-7:]
        start_week = len(prices) - len(window)
        x = list(range(start_week, start_week + len(window)))
        exp_map = {w: lbl for w, cn, lbl in state.expectation_history
                   if cn == self.company_name}

        brand_color = _CHART_COLOR.get(self.company_name, "white")

        # Cap chart size so it doesn't stretch awkwardly when panels are large.
        # Width fills the panel (needed for readable x-ticks); height is capped
        # at 9 rows so the chart looks proportional rather than filling all space.
        chart_w = width
        chart_h = min(height, 9)

        _plt.clf()
        _plt.plot_size(chart_w, chart_h)
        # Transparent backgrounds so the chart blends with Textual's dark theme
        _plt.canvas_color("default")
        _plt.axes_color("default")
        _plt.ticks_color("white")
        _plt.plot(x, window, color=brand_color, marker="braille")

        # Overlay a colored dot at each post-week close, colored by expectation
        for xi, price in zip(x, window):
            if xi > 0 and xi in exp_map:
                dot = _EXP_DOT_COLOR.get(exp_map[xi].split()[0], "white")
                _plt.scatter([xi], [price], color=dot, marker="●")

        _plt.xticks(x, [str(xi) for xi in x])
        _plt.yfrequency(3)

        chart_str = _plt.build()
        _plt.clf()

        return RichText.from_ansi(chart_str)

    def _render_shells_compact(self, text: RichText, state: GameState) -> None:
        roster = state.rosters.get(self.company_name)
        if not roster:
            text.append("\n")
            return
        counts = Counter(r.current_shell for r in roster.runners)
        for shell_name, count in counts.most_common():
            is_premium = shell_name in PREMIUM_SHELLS
            abbrev = shell_name[:3]
            text.append(f"{abbrev}×{count}", style="bold" if is_premium else "white")
            if is_premium:
                text.append("★", style="yellow")
            text.append("  ", style="dim")
        text.append("\n")


class PortfolioPanel(Static):
    """Shows credits, holdings, total value, and week-over-week change."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.border_title = "PORTFOLIO"
        self._value_before: float = STARTING_CREDITS

    def refresh_content(self, state: GameState, phase: str) -> None:
        prices = _prices_dict(state.companies)
        total = state.portfolio.total_value(prices)

        lines = [f"Credits:  [bold]{_fmt_cr(state.portfolio.credits)}[/bold]"]
        for name, shares in state.portfolio.holdings.items():
            price = prices[name]
            lines.append(f"  {name:<12} {shares} sh  @ {price:.0f} = {_fmt_cr(shares * price)}")

        if phase == "results" and state.last_results:
            gain = total - self._value_before
            color = "green" if gain >= 0 else "red"
            sign = "+" if gain >= 0 else ""
            gain_pct = (gain / self._value_before * 100) if self._value_before > 0 else 0.0
            lines.append(
                f"Total:    [bold]{_fmt_cr(total)}[/bold]  "
                f"[{color}]({sign}{_fmt_cr(gain)}, {_fmt_pct(gain_pct)})[/{color}]"
            )
        else:
            lines.append(f"Total:    [bold]{_fmt_cr(total)}[/bold]")
            self._value_before = total

        self.update("\n".join(lines))


class ZoneIntelPanel(Static):
    """Shows monitored zone intel or post-week results."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.border_title = "ZONE INTEL"

    def refresh_content(self, state: GameState, phase: str) -> None:
        zone = state.monitored_zone
        header = f"[bold]{zone.name}[/bold]  [{_difficulty_label(zone.difficulty)}]"

        if phase in ("planning", "simulating"):
            previews = _build_sector7_previews(state.rosters, zone)
            lines = [header, ""]
            for company in state.companies:
                members = "  ".join(previews[company.name])
                lines.append(f"[dim]{company.name:<12}[/dim]")
                lines.append(f"  {members}")
        else:
            lines = [header, ""]
            if state.last_results:
                for r in state.last_results:
                    names = ", ".join(r.monitored_runner_names) if r.monitored_runner_names else "—"
                    if r.monitored_squad_returned:
                        status = "[green]RETURNED[/green]"
                        detail = f"{r.monitored_credits:.0f} cr  ·  {r.monitored_eliminations} kills"
                    else:
                        status = "[red]LOST[/red]    "
                        detail = "no extraction"
                    lines.append(f"[dim]{r.company_name:<12}[/dim] {status}")
                    lines.append(f"  [{names}]  {detail}")

        self.update("\n".join(lines))


class ShellTickerPanel(Static):
    """Live shell market ticker — price, weekly delta, trend arrow, sparkline."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.border_title = "SHELL MARKET"

    def refresh_content(self, state: GameState) -> None:
        market = state.market
        prev_prices = market.price_history[-2] if len(market.price_history) >= 2 else {}

        lines = []
        for shell in sorted(SHELL_ROSTER, key=lambda s: -market.prices[s.name]):
            price   = market.prices[shell.name]
            prev    = prev_prices.get(shell.name, price)
            delta   = price - prev
            arrow   = _trend_arrow(delta)
            spark   = _sparkline([snap.get(shell.name, BASE_SHELL_PRICE)
                                   for snap in market.price_history])
            marker  = "[yellow]★[/yellow]" if shell.name in PREMIUM_SHELLS else " "
            d_color = "green" if delta > 0.5 else "red" if delta < -0.5 else "dim"
            d_str   = f"{delta:+.1f}" if abs(delta) >= 0.05 else "  —"
            lines.append(
                f"{shell.name:<10} {price:>6.1f}cr "
                f"[{d_color}]{d_str:>5}[/{d_color}] {arrow} [dim]{spark}[/dim] {marker}"
            )

        self.update("\n".join(lines))


# ---------------------------------------------------------------------------
# MODAL SCREENS
# ---------------------------------------------------------------------------
class BuyModal(ModalScreen[tuple[str, int] | None]):
    BINDINGS = [Binding("escape", "dismiss_cancel", "Cancel")]

    DEFAULT_CSS = """
    BuyModal { align: center middle; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-container"):
            yield Label("BUY SHARES", classes="modal-title")
            yield Label("Company name:")
            yield Input(placeholder="e.g. CyberAcme", id="buy-company")
            yield Label("", id="buy-company-error", classes="modal-error")
            yield Label("Number of shares:")
            yield Input(placeholder="e.g. 5", id="buy-shares")
            yield Label("", id="buy-shares-error", classes="modal-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Buy", variant="success", id="buy-confirm")
                yield Button("Cancel", variant="error", id="buy-cancel")

    def on_mount(self) -> None:
        self.query_one("#buy-company", Input).focus()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "buy-cancel":
            self.dismiss(None)
            return
        self._confirm()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "buy-company":
            self.query_one("#buy-shares", Input).focus()
        else:
            self._confirm()

    def _confirm(self) -> None:
        company_raw = self.query_one("#buy-company", Input).value.strip()
        shares_raw = self.query_one("#buy-shares", Input).value.strip()
        if not company_raw:
            self.query_one("#buy-company-error", Label).update("Enter a company name.")
            return
        try:
            shares = int(shares_raw)
        except ValueError:
            self.query_one("#buy-shares-error", Label).update("Enter a whole number.")
            return
        self.dismiss((company_raw, shares))


class SellModal(ModalScreen[tuple[str, int] | None]):
    BINDINGS = [Binding("escape", "dismiss_cancel", "Cancel")]

    DEFAULT_CSS = """
    SellModal { align: center middle; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-container"):
            yield Label("SELL SHARES", classes="modal-title")
            yield Label("Company name:")
            yield Input(placeholder="e.g. CyberAcme", id="sell-company")
            yield Label("", id="sell-company-error", classes="modal-error")
            yield Label("Number of shares:")
            yield Input(placeholder="e.g. 5", id="sell-shares")
            yield Label("", id="sell-shares-error", classes="modal-error")
            with Horizontal(classes="modal-buttons"):
                yield Button("Sell", variant="success", id="sell-confirm")
                yield Button("Cancel", variant="error", id="sell-cancel")

    def on_mount(self) -> None:
        self.query_one("#sell-company", Input).focus()

    def action_dismiss_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sell-cancel":
            self.dismiss(None)
            return
        self._confirm()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "sell-company":
            self.query_one("#sell-shares", Input).focus()
        else:
            self._confirm()

    def _confirm(self) -> None:
        company_raw = self.query_one("#sell-company", Input).value.strip()
        shares_raw = self.query_one("#sell-shares", Input).value.strip()
        if not company_raw:
            self.query_one("#sell-company-error", Label).update("Enter a company name.")
            return
        try:
            shares = int(shares_raw)
        except ValueError:
            self.query_one("#sell-shares-error", Label).update("Enter a whole number.")
            return
        self.dismiss((company_raw, shares))


class ShellMarketScreen(ModalScreen):
    """Full-screen overlay showing the shell market — prices, trends, adoption."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("enter", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    ShellMarketScreen { align: center middle; }
    """

    def __init__(self, state: GameState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        with Vertical(id="shell-market-container"):
            yield Static(self._build_content(), id="shell-market-body")
            yield Label("[dim]Escape or Enter to close[/dim]", id="shell-market-footer")

    def _build_content(self) -> str:
        market = self._state.market
        rosters = self._state.rosters

        runners = roster_all_runners(rosters)
        total = len(runners)
        counts = Counter(r.current_shell for r in runners)

        prev_prices = market.price_history[-2] if len(market.price_history) >= 2 else {
            s.name: BASE_SHELL_PRICE for s in SHELL_ROSTER
        }

        lines = [
            "[bold]SHELL MARKET[/bold]",
            "",
            f"  {'Shell':<10}  {'Price':>8}  {'Δ wk':>7}  {'':1}  {'Trend':>6}  {'Adoption':>12}",
            f"  {'─'*10}  {'─'*8}  {'─'*7}  {'─'*1}  {'─'*6}  {'─'*12}",
        ]

        for shell in sorted(SHELL_ROSTER, key=lambda s: -market.prices[s.name]):
            price = market.prices[shell.name]
            prev = prev_prices.get(shell.name, BASE_SHELL_PRICE)
            delta = price - prev
            delta_str = f"{delta:+.1f}" if abs(delta) >= 0.05 else "  —  "
            arrow = _trend_arrow(delta)

            history_for_shell = [snap.get(shell.name, BASE_SHELL_PRICE) for snap in market.price_history]
            spark = _sparkline(history_for_shell)

            count = counts.get(shell.name, 0)
            pct = 100 * count / total if total else 0
            tag = " [yellow]★[/yellow]" if shell.name in PREMIUM_SHELLS else "  "

            lines.append(
                f"  {shell.name:<10}  {price:>7.1f}cr  {delta_str:>7}  {arrow:1}  "
                f"{spark:>6}  {count:>2} ({pct:4.1f}%){tag}"
            )

        premium_count = sum(counts.get(s, 0) for s in PREMIUM_SHELLS)
        middle_count = total - premium_count
        fair_pct = 100 / N_SHELLS

        lines += [
            f"  {'─'*10}  {'─'*8}  {'─'*7}  {'─'*1}  {'─'*6}  {'─'*12}",
            "",
            (f"  [yellow]★[/yellow] Premium (Destroyer/Thief/Triage):  "
             f"{premium_count}/{total} ({100*premium_count/total:.1f}%)") if total else "",
            (f"    Middle shells:  {middle_count}/{total} ({100*middle_count/total:.1f}%)") if total else "",
            f"    Fair share (uniform): {fair_pct:.1f}% per shell",
        ]

        weeks = len(market.price_history)
        if weeks > 1:
            lines.append(f"\n  Sparkline = last {min(6, weeks)} weeks of price history")
        else:
            lines.append("\n  Sparklines build up over time — check back in a few weeks.")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# MAIN APP
# ---------------------------------------------------------------------------
class MarathonMarketApp(App):
    CSS_PATH = "marathon_market_tui.tcss"
    TITLE = "Marathon Market Simulator"

    BINDINGS = [
        Binding("b", "buy", "Buy"),
        Binding("s", "sell", "Sell"),
        Binding("a", "all_in", "All-in"),
        Binding("k", "shells", "Shells"),
        Binding("h", "advance_week", "Hold/Advance"),
        Binding("enter", "advance_week", "Advance", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, debug: bool = False) -> None:
        super().__init__()
        self.engine = GameEngine(debug=debug)
        self.phase = "planning"

    # ── Layout ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Label("", id="status-bar")
        with Horizontal(id="companies-row"):
            for c in self.engine.state.companies:
                yield CompanyPanel(c.name)
        with Horizontal(id="bottom-row"):
            yield PortfolioPanel(id="portfolio-panel")
            yield ZoneIntelPanel(id="zone-intel-panel")
            yield ShellTickerPanel(id="shell-ticker-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_all("planning")

    # ── Key bindings → actions ───────────────────────────────────────────────

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if self.phase == "simulating" and action != "quit":
            return False
        if self.phase == "results" and action in ("buy", "sell", "all_in", "shells"):
            return False
        return True

    def action_buy(self) -> None:
        self.push_screen(BuyModal(), self._on_buy_result)

    def action_sell(self) -> None:
        self.push_screen(SellModal(), self._on_sell_result)

    def action_all_in(self) -> None:
        msg = self.engine.do_all_in()
        self._refresh_all("planning", status_msg=msg)

    def action_shells(self) -> None:
        self.push_screen(ShellMarketScreen(self.engine.state))

    def action_advance_week(self) -> None:
        if self.phase == "planning":
            self._refresh_all("simulating")
            self._run_week()
        elif self.phase == "results":
            self._refresh_all("planning")

    def _on_buy_result(self, result: tuple[str, int] | None) -> None:
        if result is None:
            self._refresh_all("planning")
            return
        company_name, shares = result
        err = self.engine.do_buy(company_name, shares)
        msg = err if err else f"Bought {shares} share(s) of {company_name}."
        self._refresh_all("planning", status_msg=msg)

    def _on_sell_result(self, result: tuple[str, int] | None) -> None:
        if result is None:
            self._refresh_all("planning")
            return
        company_name, shares = result
        err = self.engine.do_sell(company_name, shares)
        msg = err if err else f"Sold {shares} share(s) of {company_name}."
        self._refresh_all("planning", status_msg=msg)

    # ── Week simulation worker ───────────────────────────────────────────────

    @work(thread=True)
    def _run_week(self) -> None:
        self.engine.advance_week()
        self.post_message(WeekComplete())

    def on_week_complete(self, _: WeekComplete) -> None:
        self._refresh_all("results")

    # ── Refresh ──────────────────────────────────────────────────────────────

    def _refresh_all(self, phase: str, status_msg: str = "") -> None:
        self.phase = phase
        self.refresh_bindings()
        s = self.engine.state

        status = f"MARATHON MARKET  ·  Week {s.week}  ·  {phase.upper()}"
        if status_msg:
            status += f"   {status_msg}"
        self.query_one("#status-bar", Label).update(status)

        for panel in self.query(CompanyPanel):
            panel.refresh_content(s)

        self.query_one(PortfolioPanel).refresh_content(s, phase)
        self.query_one(ZoneIntelPanel).refresh_content(s, phase)
        self.query_one(ShellTickerPanel).refresh_content(s)
