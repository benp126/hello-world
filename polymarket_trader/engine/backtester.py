"""Backtesting engine: replay historical price data through a strategy."""

from datetime import datetime, timezone
from typing import Optional

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.api.models import Market, PricePoint, Token
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Strategy
from polymarket_trader.utils.metrics import calculate_metrics, format_metrics


class BacktestResult:
    """Container for backtest results."""

    def __init__(self, strategy: Strategy, portfolio: Portfolio, markets_tested: list[str]):
        self.strategy = strategy
        self.portfolio = portfolio
        self.markets_tested = markets_tested
        self.metrics = calculate_metrics(portfolio)

    def summary(self) -> str:
        lines = [
            f"\nBacktest Results: {self.strategy.name}",
            f"Markets tested: {len(self.markets_tested)}",
            "",
            format_metrics(self.metrics),
        ]
        return "\n".join(lines)

    def trade_log(self) -> str:
        lines = [f"{'ID':<12} {'Time':<20} {'Side':<6} {'Outcome':<4} {'Shares':>10} {'Price':>8} {'Cost':>10} {'Market'}"]
        lines.append("-" * 110)
        for t in self.portfolio.trade_history:
            lines.append(
                f"{t.trade_id:<12} {t.timestamp.strftime('%Y-%m-%d %H:%M'):<20} "
                f"{t.side:<6} {t.outcome:<4} {t.shares:>10.2f} {t.price:>8.4f} "
                f"${t.cost:>9.2f} {t.market_question[:40]}"
            )
        return "\n".join(lines)


class Backtester:
    """Run a strategy against historical Polymarket data.

    Modes:
    1. Single-market backtest: test on one market's price history.
    2. Multi-market scan: test on many markets, cycling through them.
    """

    def __init__(
        self,
        client: Optional[PolymarketClient] = None,
        starting_balance: float = 1000.0,
        fee_rate: float = 0.0,
        verbose: bool = False,
    ):
        self.client = client or PolymarketClient()
        self.starting_balance = starting_balance
        self.fee_rate = fee_rate
        self.verbose = verbose

    def run_single_market(
        self,
        strategy: Strategy,
        market: Market,
        price_history: list[PricePoint],
        resolved_outcome: Optional[str] = None,
    ) -> BacktestResult:
        """Backtest a strategy on a single market's price history.

        Walks through price_history step by step, feeding the strategy
        an expanding window of prices and executing any signals.

        Args:
            strategy: The strategy to test.
            market: The market to test on.
            price_history: Historical price points (sorted by time).
            resolved_outcome: If the market resolved, "Yes" or "No".
        """
        portfolio = Portfolio(
            starting_balance=self.starting_balance,
            fee_rate=self.fee_rate,
        )
        strategy.on_start(portfolio)

        for i in range(1, len(price_history)):
            current_time = price_history[i].timestamp
            current_price = price_history[i].price
            history_so_far = price_history[:i + 1]

            # Update market's current price for the strategy
            sim_market = self._market_at_price(market, current_price)

            # Update portfolio positions with current price
            if market.yes_token_id:
                portfolio.update_prices({market.yes_token_id: current_price})
            if market.no_token_id:
                portfolio.update_prices({market.no_token_id: 1.0 - current_price})

            # Get signals from strategy
            signals = strategy.evaluate(sim_market, history_so_far, portfolio, current_time)

            # Execute signals
            for signal in signals:
                self._execute_signal(signal, portfolio, current_time)

            # Take snapshot
            portfolio.take_snapshot(timestamp=current_time)

        # Settle if market resolved
        if resolved_outcome and market.yes_token_id:
            yes_won = resolved_outcome.lower() == "yes"
            portfolio.settle(
                market.yes_token_id,
                winning=yes_won,
                timestamp=price_history[-1].timestamp if price_history else None,
            )
            if market.no_token_id and market.no_token_id in portfolio.positions:
                portfolio.settle(
                    market.no_token_id,
                    winning=not yes_won,
                    timestamp=price_history[-1].timestamp if price_history else None,
                )

        strategy.on_end(portfolio)
        return BacktestResult(strategy, portfolio, [market.condition_id])

    def run_multi_market(
        self,
        strategy: Strategy,
        markets: list[Market],
        interval: str = "all",
        fidelity: int = 100,
        settle_resolved: bool = True,
    ) -> BacktestResult:
        """Backtest a strategy across multiple markets using live API data.

        Fetches price history for each market and runs the strategy on each
        sequentially, using a shared portfolio.

        Args:
            strategy: The strategy to test.
            markets: List of markets to test on.
            interval: Price history interval ("1d", "1w", "1m", "all", etc.).
            fidelity: Number of price points to fetch per market.
            settle_resolved: Whether to settle positions on resolved markets.
        """
        portfolio = Portfolio(
            starting_balance=self.starting_balance,
            fee_rate=self.fee_rate,
        )
        strategy.on_start(portfolio)
        tested_markets = []

        for market in markets:
            if not market.yes_token_id:
                continue

            if self.verbose:
                print(f"  Testing: {market.question[:60]}...")

            try:
                price_history = self.client.get_price_history(
                    market.yes_token_id,
                    interval=interval,
                    fidelity=fidelity,
                )
            except Exception as e:
                if self.verbose:
                    print(f"    Skipped (API error): {e}")
                continue

            if len(price_history) < 5:
                if self.verbose:
                    print(f"    Skipped (insufficient data: {len(price_history)} points)")
                continue

            tested_markets.append(market.condition_id)

            # Walk through the price history
            for i in range(1, len(price_history)):
                current_time = price_history[i].timestamp
                current_price = price_history[i].price
                history_so_far = price_history[:i + 1]

                sim_market = self._market_at_price(market, current_price)
                portfolio.update_prices({market.yes_token_id: current_price})

                signals = strategy.evaluate(sim_market, history_so_far, portfolio, current_time)
                for signal in signals:
                    self._execute_signal(signal, portfolio, current_time)

                portfolio.take_snapshot(timestamp=current_time)

            # Settle resolved markets
            if settle_resolved and market.closed and market.outcome:
                yes_won = market.outcome.lower() == "yes"
                if market.yes_token_id in portfolio.positions:
                    portfolio.settle(
                        market.yes_token_id,
                        winning=yes_won,
                        timestamp=price_history[-1].timestamp if price_history else None,
                    )

        strategy.on_end(portfolio)
        return BacktestResult(strategy, portfolio, tested_markets)

    def _market_at_price(self, market: Market, yes_price: float) -> Market:
        """Create a copy of the market with updated YES/NO prices."""
        tokens = []
        for t in market.tokens:
            if t.outcome.lower() == "yes":
                tokens.append(Token(token_id=t.token_id, outcome=t.outcome, price=yes_price))
            elif t.outcome.lower() == "no":
                tokens.append(Token(token_id=t.token_id, outcome=t.outcome, price=1.0 - yes_price))
            else:
                tokens.append(t)
        return Market(
            condition_id=market.condition_id,
            question=market.question,
            slug=market.slug,
            tokens=tokens,
            active=market.active,
            closed=market.closed,
            end_date=market.end_date,
            description=market.description,
            category=market.category,
            volume=market.volume,
            liquidity=market.liquidity,
            outcome=market.outcome,
        )

    def _execute_signal(self, signal, portfolio: Portfolio, timestamp: datetime):
        """Execute a trading signal against the portfolio."""
        if signal.action == "BUY" and signal.target_shares:
            # Find the current price for this token
            price = 0.0
            if signal.token_id in portfolio.positions:
                price = portfolio.positions[signal.token_id].current_price
            if price <= 0:
                # Use a fallback - we need the price from the signal context
                # The strategy should ensure this is available via market data
                return

            trade = portfolio.buy(
                token_id=signal.token_id,
                shares=signal.target_shares,
                price=price,
                market_condition_id=signal.market_condition_id,
                market_question=signal.market_question,
                outcome=signal.outcome,
                timestamp=timestamp,
            )
            if trade and self.verbose:
                print(f"    BUY  {signal.outcome} x{trade.shares:.1f} @ ${trade.price:.4f} - {signal.reason}")

        elif signal.action == "SELL" and signal.target_shares:
            if signal.token_id not in portfolio.positions:
                return
            price = portfolio.positions[signal.token_id].current_price
            trade = portfolio.sell(
                token_id=signal.token_id,
                shares=signal.target_shares,
                price=price,
                timestamp=timestamp,
            )
            if trade and self.verbose:
                print(f"    SELL {signal.outcome} x{trade.shares:.1f} @ ${trade.price:.4f} - {signal.reason}")
