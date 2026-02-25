"""Threshold-based strategy: buy when price is below a threshold, sell when above."""

from datetime import datetime

from polymarket_trader.api.models import Market, PricePoint
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Signal, Strategy


class ThresholdStrategy(Strategy):
    """Buy YES tokens when price drops below a low threshold, sell above a high threshold.

    This is a simple contrarian strategy that assumes prices will mean-revert.

    Args:
        buy_below: Buy YES when price is below this value (e.g., 0.30).
        sell_above: Sell YES when price is above this value (e.g., 0.70).
        position_size: Fraction of available cash to use per trade (0-1).
    """

    def __init__(
        self,
        buy_below: float = 0.30,
        sell_above: float = 0.70,
        position_size: float = 0.10,
        name: str = "",
    ):
        super().__init__(name=name or f"Threshold({buy_below:.0%}-{sell_above:.0%})")
        self.buy_below = buy_below
        self.sell_above = sell_above
        self.position_size = position_size

    def evaluate(
        self,
        market: Market,
        price_history: list[PricePoint],
        portfolio: Portfolio,
        current_time: datetime,
    ) -> list[Signal]:
        signals = []
        yes_price = market.yes_price
        yes_token = market.yes_token_id

        if not yes_token or yes_price <= 0:
            return signals

        # Check if we already have a position
        has_position = yes_token in portfolio.positions

        if yes_price < self.buy_below and not has_position:
            cash_to_use = portfolio.cash * self.position_size
            shares = cash_to_use / yes_price if yes_price > 0 else 0
            if shares > 0:
                signals.append(Signal(
                    action="BUY",
                    token_id=yes_token,
                    market_condition_id=market.condition_id,
                    market_question=market.question,
                    outcome="Yes",
                    target_shares=shares,
                    confidence=min(1.0, (self.buy_below - yes_price) / self.buy_below),
                    reason=f"Price {yes_price:.3f} below buy threshold {self.buy_below:.3f}",
                ))

        elif yes_price > self.sell_above and has_position:
            pos = portfolio.positions[yes_token]
            signals.append(Signal(
                action="SELL",
                token_id=yes_token,
                market_condition_id=market.condition_id,
                market_question=market.question,
                outcome="Yes",
                target_shares=pos.shares,
                confidence=min(1.0, (yes_price - self.sell_above) / (1 - self.sell_above)),
                reason=f"Price {yes_price:.3f} above sell threshold {self.sell_above:.3f}",
            ))

        return signals
