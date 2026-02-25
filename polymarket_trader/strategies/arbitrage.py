"""Binary arbitrage strategy: buy both YES and NO when combined cost < $1.00.

On Polymarket, binary markets always resolve with one side paying $1 and
the other paying $0. If you can buy YES + NO for less than $1.00 total,
you lock in a guaranteed profit equal to the difference.

This strategy focuses on short-duration Bitcoin markets (15 min or less)
where momentary pricing inefficiencies can create arb opportunities.
"""

from datetime import datetime
from typing import Optional

from polymarket_trader.api.models import Market, OrderBook, PricePoint
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Signal, Strategy


class BinaryArbitrageStrategy(Strategy):
    """Find and exploit binary arbitrage: YES_ask + NO_ask < $1.00.

    For each market, checks whether buying both outcomes costs less than
    the guaranteed $1.00 payout. When the spread exceeds the minimum
    threshold, buys equal shares of both sides.

    Args:
        min_edge_cents: Minimum profit in cents per share pair (default 0.5).
            e.g., 0.5 means YES + NO must cost <= $0.995 to trigger.
        position_size: Dollar amount to allocate per arb opportunity.
        max_concurrent_arbs: Max number of arb positions open at once.
        use_order_book: If True, use best ask prices. If False, use
            midpoint prices (faster but less accurate for execution).
    """

    def __init__(
        self,
        min_edge_cents: float = 0.5,
        position_size: float = 100.0,
        max_concurrent_arbs: int = 20,
        use_order_book: bool = True,
        name: str = "",
    ):
        super().__init__(name=name or f"BinaryArb(edge>={min_edge_cents:.1f}c)")
        self.min_edge = min_edge_cents / 100.0  # convert cents to dollars
        self.position_size = position_size
        self.max_concurrent_arbs = max_concurrent_arbs
        self.use_order_book = use_order_book
        # Track which markets we've already arbed (by condition_id)
        self._arbed_markets: set[str] = set()

    def evaluate(
        self,
        market: Market,
        price_history: list[PricePoint],
        portfolio: Portfolio,
        current_time: datetime,
        yes_ask: Optional[float] = None,
        no_ask: Optional[float] = None,
    ) -> list[Signal]:
        """Evaluate a single market for binary arbitrage.

        Args:
            market: Market with current token prices.
            price_history: Not used for arb (included for interface compat).
            portfolio: Current portfolio.
            current_time: Current time.
            yes_ask: Override YES ask price (from order book).
            no_ask: Override NO ask price (from order book).
        """
        signals = []
        yes_token = market.yes_token_id
        no_token = market.no_token_id

        if not yes_token or not no_token:
            return signals

        # Skip if we already have an arb on this market
        if market.condition_id in self._arbed_markets:
            return signals

        # Skip if at max concurrent arbs
        active_arbs = len(self._arbed_markets)
        if active_arbs >= self.max_concurrent_arbs:
            return signals

        # Get prices to check - prefer explicit ask prices, fall back to market prices
        yes_price = yes_ask if yes_ask is not None else market.yes_price
        no_price = no_ask if no_ask is not None else market.no_price

        if yes_price <= 0 or no_price <= 0:
            return signals

        # Core arb check: can we buy both sides for less than $1?
        combined_cost = yes_price + no_price
        edge = 1.0 - combined_cost  # profit per share pair

        if edge < self.min_edge:
            return signals

        # How many share pairs can we buy?
        # Each pair costs (yes_price + no_price) and pays out $1.00
        max_pairs_by_cash = portfolio.cash / combined_cost if combined_cost > 0 else 0
        target_pairs = self.position_size / combined_cost if combined_cost > 0 else 0
        pairs = min(target_pairs, max_pairs_by_cash)

        if pairs < 1:
            return signals

        profit_per_pair = edge
        total_profit = pairs * profit_per_pair

        reason = (
            f"ARB: YES@{yes_price:.4f} + NO@{no_price:.4f} = {combined_cost:.4f} "
            f"(edge={edge * 100:.2f}c, pairs={pairs:.0f}, est_profit=${total_profit:.2f})"
        )

        # Emit two BUY signals - one for YES, one for NO
        signals.append(Signal(
            action="BUY",
            token_id=yes_token,
            market_condition_id=market.condition_id,
            market_question=market.question,
            outcome="Yes",
            target_shares=pairs,
            confidence=min(1.0, edge / 0.05),  # 5 cents edge = full confidence
            reason=reason,
        ))
        signals.append(Signal(
            action="BUY",
            token_id=no_token,
            market_condition_id=market.condition_id,
            market_question=market.question,
            outcome="No",
            target_shares=pairs,
            confidence=min(1.0, edge / 0.05),
            reason=reason,
        ))

        self._arbed_markets.add(market.condition_id)
        return signals

    def on_settled(self, condition_id: str):
        """Call when a market settles to free up the arb slot."""
        self._arbed_markets.discard(condition_id)
