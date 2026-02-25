"""Multi-market diversified strategy: spread bets across multiple markets."""

from datetime import datetime

from polymarket_trader.api.models import Market, PricePoint
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Signal, Strategy


class DiversifiedValueStrategy(Strategy):
    """Buy underpriced YES tokens across multiple markets to build a diversified portfolio.

    Identifies markets where YES tokens are priced below a target and allocates
    a fixed amount to each, up to a max number of concurrent positions.

    Good for spreading risk across many binary outcome markets.

    Args:
        max_price: Only buy YES tokens priced below this (e.g., 0.40 = 40 cents).
        min_price: Avoid tokens priced below this (likely near-zero for a reason).
        max_positions: Maximum number of open positions at once.
        per_position_size: Fixed dollar amount per position.
    """

    def __init__(
        self,
        max_price: float = 0.40,
        min_price: float = 0.05,
        max_positions: int = 20,
        per_position_size: float = 50.0,
        name: str = "",
    ):
        super().__init__(name=name or f"DiversifiedValue(max={max_price:.0%})")
        self.max_price = max_price
        self.min_price = min_price
        self.max_positions = max_positions
        self.per_position_size = per_position_size

    def evaluate(
        self,
        market: Market,
        price_history: list[PricePoint],
        portfolio: Portfolio,
        current_time: datetime,
    ) -> list[Signal]:
        signals = []
        yes_token = market.yes_token_id
        yes_price = market.yes_price

        if not yes_token or yes_price <= 0:
            return signals

        num_positions = len(portfolio.positions)
        has_position = yes_token in portfolio.positions

        # Buy condition: price in range, we have room, and enough cash
        if (
            not has_position
            and self.min_price <= yes_price <= self.max_price
            and num_positions < self.max_positions
            and portfolio.cash >= self.per_position_size
        ):
            shares = self.per_position_size / yes_price
            signals.append(Signal(
                action="BUY",
                token_id=yes_token,
                market_condition_id=market.condition_id,
                market_question=market.question,
                outcome="Yes",
                target_shares=shares,
                confidence=(self.max_price - yes_price) / self.max_price,
                reason=f"Value buy: YES at {yes_price:.3f} (range {self.min_price}-{self.max_price})",
            ))

        # Sell if price has risen significantly (take profit)
        elif has_position and yes_price > self.max_price + 0.20:
            pos = portfolio.positions[yes_token]
            signals.append(Signal(
                action="SELL",
                token_id=yes_token,
                market_condition_id=market.condition_id,
                market_question=market.question,
                outcome="Yes",
                target_shares=pos.shares,
                reason=f"Take profit: price {yes_price:.3f} above threshold {self.max_price + 0.20:.3f}",
            ))

        return signals
