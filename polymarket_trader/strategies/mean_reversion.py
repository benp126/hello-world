"""Mean reversion strategy: bet on prices reverting to their average."""

import math
from datetime import datetime

from polymarket_trader.api.models import Market, PricePoint
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Signal, Strategy


class MeanReversionStrategy(Strategy):
    """Buy when price deviates significantly below its moving average,
    sell when it deviates above.

    Uses z-score (number of standard deviations from the mean) to
    determine when price has moved too far from its average.

    Args:
        window: Lookback window for calculating mean and std deviation.
        entry_z: Z-score threshold to enter a position (e.g., -1.5 = buy).
        exit_z: Z-score threshold to exit a position (e.g., 0.5 = sell).
        position_size: Fraction of available cash per trade (0-1).
    """

    def __init__(
        self,
        window: int = 20,
        entry_z: float = -1.5,
        exit_z: float = 0.5,
        position_size: float = 0.10,
        name: str = "",
    ):
        super().__init__(name=name or f"MeanReversion(w={window},z={entry_z}/{exit_z})")
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.position_size = position_size

    def evaluate(
        self,
        market: Market,
        price_history: list[PricePoint],
        portfolio: Portfolio,
        current_time: datetime,
    ) -> list[Signal]:
        signals = []
        yes_token = market.yes_token_id

        if not yes_token or len(price_history) < self.window:
            return signals

        prices = [p.price for p in price_history[-self.window:]]
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        std = math.sqrt(variance) if variance > 0 else 0

        if std < 0.001:
            return signals  # Not enough variance to trade

        current_price = market.yes_price
        z_score = (current_price - mean) / std
        has_position = yes_token in portfolio.positions

        # Price is significantly below mean - buy
        if z_score < self.entry_z and not has_position:
            if current_price > 0:
                cash_to_use = portfolio.cash * self.position_size
                shares = cash_to_use / current_price
                if shares > 0:
                    signals.append(Signal(
                        action="BUY",
                        token_id=yes_token,
                        market_condition_id=market.condition_id,
                        market_question=market.question,
                        outcome="Yes",
                        target_shares=shares,
                        confidence=min(1.0, abs(z_score) / 3.0),
                        reason=f"Mean reversion buy: z={z_score:.2f} (mean={mean:.3f}, std={std:.3f}, price={current_price:.3f})",
                    ))

        # Price reverted to/above mean - sell
        elif z_score > self.exit_z and has_position:
            pos = portfolio.positions[yes_token]
            signals.append(Signal(
                action="SELL",
                token_id=yes_token,
                market_condition_id=market.condition_id,
                market_question=market.question,
                outcome="Yes",
                target_shares=pos.shares,
                confidence=min(1.0, abs(z_score) / 3.0),
                reason=f"Mean reversion sell: z={z_score:.2f} (mean={mean:.3f}, std={std:.3f}, price={current_price:.3f})",
            ))

        return signals
