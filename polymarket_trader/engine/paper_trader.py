"""Live paper trading engine: run strategies against real-time Polymarket data."""

import time
from datetime import datetime, timezone
from typing import Optional

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.api.models import Market
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Strategy
from polymarket_trader.utils.metrics import calculate_metrics, format_metrics


class PaperTrader:
    """Run a strategy in paper-trading mode using live Polymarket prices.

    Periodically polls the API for current prices and lets the strategy
    make decisions, executing paper trades against a simulated portfolio.

    Args:
        client: Polymarket API client.
        strategy: The strategy to run.
        starting_balance: Initial paper money balance.
        fee_rate: Simulated fee rate per trade.
        poll_interval: Seconds between price checks.
        history_interval: Interval for fetching price history ("1d", "1w", etc.).
        save_path: If set, save portfolio state to this file periodically.
    """

    def __init__(
        self,
        client: Optional[PolymarketClient] = None,
        strategy: Optional[Strategy] = None,
        starting_balance: float = 1000.0,
        fee_rate: float = 0.0,
        poll_interval: int = 60,
        history_interval: str = "1w",
        save_path: Optional[str] = None,
    ):
        self.client = client or PolymarketClient()
        self.strategy = strategy
        self.starting_balance = starting_balance
        self.fee_rate = fee_rate
        self.poll_interval = poll_interval
        self.history_interval = history_interval
        self.save_path = save_path
        self.portfolio = Portfolio(starting_balance=starting_balance, fee_rate=fee_rate)
        self.watchlist: list[str] = []  # condition_ids to monitor
        self._running = False

    def add_market(self, condition_id: str):
        """Add a market to the watchlist by condition ID."""
        if condition_id not in self.watchlist:
            self.watchlist.append(condition_id)

    def remove_market(self, condition_id: str):
        """Remove a market from the watchlist."""
        self.watchlist = [m for m in self.watchlist if m != condition_id]

    def run_once(self) -> list[str]:
        """Run one cycle: fetch data, evaluate strategy, execute signals.

        Returns a list of action descriptions for logging.
        """
        if not self.strategy:
            return ["No strategy set"]

        actions = []
        now = datetime.now(tz=timezone.utc)

        for condition_id in self.watchlist:
            try:
                market = self.client.get_market(condition_id)
            except Exception as e:
                actions.append(f"Error fetching {condition_id}: {e}")
                continue

            if not market.yes_token_id:
                continue

            # Fetch recent price history for the strategy
            try:
                price_history = self.client.get_price_history(
                    market.yes_token_id,
                    interval=self.history_interval,
                    fidelity=50,
                )
            except Exception:
                price_history = []

            # Update portfolio prices
            self.portfolio.update_prices({market.yes_token_id: market.yes_price})
            if market.no_token_id:
                self.portfolio.update_prices({market.no_token_id: market.no_price})

            # Evaluate strategy
            signals = self.strategy.evaluate(market, price_history, self.portfolio, now)

            for signal in signals:
                result = self._execute_signal(signal, now)
                if result:
                    actions.append(result)

        # Take snapshot
        self.portfolio.take_snapshot(timestamp=now)

        # Save state if configured
        if self.save_path:
            try:
                self.portfolio.save(self.save_path)
            except Exception:
                pass

        return actions

    def run_loop(self, max_iterations: Optional[int] = None):
        """Run the paper trader in a continuous loop.

        Args:
            max_iterations: Stop after this many iterations (None = run forever).
        """
        if not self.strategy:
            print("Error: No strategy set. Use paper_trader.strategy = YourStrategy()")
            return

        print(f"Starting paper trader: {self.strategy.name}")
        print(f"  Balance: ${self.starting_balance:,.2f}")
        print(f"  Watching: {len(self.watchlist)} markets")
        print(f"  Poll interval: {self.poll_interval}s")
        print()

        self.strategy.on_start(self.portfolio)
        self._running = True
        iteration = 0

        try:
            while self._running:
                if max_iterations is not None and iteration >= max_iterations:
                    break

                iteration += 1
                now = datetime.now(tz=timezone.utc)
                print(f"[{now.strftime('%H:%M:%S')}] Cycle {iteration}...")

                actions = self.run_once()
                for action in actions:
                    print(f"  {action}")

                pv = self.portfolio.total_value
                pnl = self.portfolio.total_pnl
                print(f"  Portfolio: ${pv:,.2f} (P&L: ${pnl:+,.2f})")
                print()

                if max_iterations is None or iteration < max_iterations:
                    time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\nStopping paper trader...")
        finally:
            self._running = False
            self.strategy.on_end(self.portfolio)
            print(self.summary())

    def stop(self):
        """Signal the loop to stop."""
        self._running = False

    def summary(self) -> str:
        """Get a performance summary."""
        metrics = calculate_metrics(self.portfolio)
        return format_metrics(metrics)

    def _execute_signal(self, signal, timestamp: datetime) -> Optional[str]:
        """Execute a signal and return a description."""
        if signal.action == "BUY" and signal.target_shares:
            # Get price from market data
            if signal.token_id in self.portfolio.positions:
                price = self.portfolio.positions[signal.token_id].current_price
            else:
                try:
                    price = self.client.get_price(signal.token_id)
                except Exception:
                    return None

            if price <= 0:
                return None

            # Adjust shares based on actual price
            max_shares = self.portfolio.cash / price
            shares = min(signal.target_shares, max_shares)

            trade = self.portfolio.buy(
                token_id=signal.token_id,
                shares=shares,
                price=price,
                market_condition_id=signal.market_condition_id,
                market_question=signal.market_question,
                outcome=signal.outcome,
                timestamp=timestamp,
            )
            if trade:
                return f"BUY {signal.outcome} x{trade.shares:.1f} @ ${trade.price:.4f} | {signal.reason}"

        elif signal.action == "SELL" and signal.target_shares:
            if signal.token_id not in self.portfolio.positions:
                return None
            price = self.portfolio.positions[signal.token_id].current_price
            trade = self.portfolio.sell(
                token_id=signal.token_id,
                shares=signal.target_shares,
                price=price,
                timestamp=timestamp,
            )
            if trade:
                return f"SELL {signal.outcome} x{trade.shares:.1f} @ ${trade.price:.4f} | {signal.reason}"

        return None
