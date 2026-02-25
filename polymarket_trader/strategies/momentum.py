"""Momentum strategy: follow the trend direction."""

from datetime import datetime

from polymarket_trader.api.models import Market, PricePoint
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Signal, Strategy


class MomentumStrategy(Strategy):
    """Buy when price is trending up, sell when trending down.

    Uses a simple moving average crossover: compares the short-term
    average to the long-term average to determine trend direction.

    Args:
        short_window: Number of recent price points for short MA.
        long_window: Number of recent price points for long MA.
        position_size: Fraction of available cash to use per trade (0-1).
        min_trend_strength: Minimum difference between short/long MA to trigger (0-1).
    """

    def __init__(
        self,
        short_window: int = 5,
        long_window: int = 20,
        position_size: float = 0.10,
        min_trend_strength: float = 0.02,
        name: str = "",
    ):
        super().__init__(name=name or f"Momentum({short_window}/{long_window})")
        self.short_window = short_window
        self.long_window = long_window
        self.position_size = position_size
        self.min_trend_strength = min_trend_strength

    def evaluate(
        self,
        market: Market,
        price_history: list[PricePoint],
        portfolio: Portfolio,
        current_time: datetime,
    ) -> list[Signal]:
        signals = []
        yes_token = market.yes_token_id

        if not yes_token or len(price_history) < self.long_window:
            return signals

        prices = [p.price for p in price_history[-self.long_window:]]
        short_ma = sum(prices[-self.short_window:]) / self.short_window
        long_ma = sum(prices) / self.long_window
        trend = short_ma - long_ma

        has_position = yes_token in portfolio.positions

        # Bullish crossover - buy
        if trend > self.min_trend_strength and not has_position:
            yes_price = market.yes_price
            if yes_price > 0:
                cash_to_use = portfolio.cash * self.position_size
                shares = cash_to_use / yes_price
                if shares > 0:
                    signals.append(Signal(
                        action="BUY",
                        token_id=yes_token,
                        market_condition_id=market.condition_id,
                        market_question=market.question,
                        outcome="Yes",
                        target_shares=shares,
                        confidence=min(1.0, abs(trend) / 0.10),
                        reason=f"Bullish momentum: short MA {short_ma:.3f} > long MA {long_ma:.3f} (diff={trend:+.4f})",
                    ))

        # Bearish crossover - sell
        elif trend < -self.min_trend_strength and has_position:
            pos = portfolio.positions[yes_token]
            signals.append(Signal(
                action="SELL",
                token_id=yes_token,
                market_condition_id=market.condition_id,
                market_question=market.question,
                outcome="Yes",
                target_shares=pos.shares,
                confidence=min(1.0, abs(trend) / 0.10),
                reason=f"Bearish momentum: short MA {short_ma:.3f} < long MA {long_ma:.3f} (diff={trend:+.4f})",
            ))

        return signals
