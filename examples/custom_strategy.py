#!/usr/bin/env python3
"""Example: Create and test a custom strategy.

Shows how to subclass the Strategy base class to implement your own logic.

Usage:
    python examples/custom_strategy.py
"""

from datetime import datetime

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.api.models import Market, PricePoint
from polymarket_trader.engine.backtester import Backtester
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.base import Signal, Strategy


class VolumeWeightedDipBuyer(Strategy):
    """Custom strategy: buy dips weighted by how large the dip is.

    Buys more aggressively when price drops further from recent highs.
    Sells when price recovers to near recent highs.
    """

    def __init__(self, lookback: int = 10, dip_threshold: float = 0.10, position_size: float = 0.15):
        super().__init__(name=f"DipBuyer(lb={lookback}, dip={dip_threshold:.0%})")
        self.lookback = lookback
        self.dip_threshold = dip_threshold
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

        if not yes_token or len(price_history) < self.lookback:
            return signals

        current_price = market.yes_price
        recent_prices = [p.price for p in price_history[-self.lookback:]]
        recent_high = max(recent_prices)
        recent_low = min(recent_prices)

        has_position = yes_token in portfolio.positions

        # Calculate how much price has dipped from recent high
        if recent_high > 0:
            dip_pct = (recent_high - current_price) / recent_high
        else:
            return signals

        # Buy on dips - size proportional to dip magnitude
        if dip_pct > self.dip_threshold and not has_position:
            # Allocate more capital for bigger dips
            size_multiplier = min(3.0, dip_pct / self.dip_threshold)
            cash_to_use = portfolio.cash * self.position_size * size_multiplier
            shares = cash_to_use / current_price if current_price > 0 else 0

            if shares > 0:
                signals.append(Signal(
                    action="BUY",
                    token_id=yes_token,
                    market_condition_id=market.condition_id,
                    market_question=market.question,
                    outcome="Yes",
                    target_shares=shares,
                    confidence=min(1.0, dip_pct / 0.30),
                    reason=f"Dip buy: {dip_pct:.1%} off recent high {recent_high:.3f}",
                ))

        # Sell when price recovers near recent high
        elif has_position and current_price >= recent_high * 0.95:
            pos = portfolio.positions[yes_token]
            signals.append(Signal(
                action="SELL",
                token_id=yes_token,
                market_condition_id=market.condition_id,
                market_question=market.question,
                outcome="Yes",
                target_shares=pos.shares,
                reason=f"Recovery sell: price {current_price:.3f} near high {recent_high:.3f}",
            ))

        return signals


def main():
    client = PolymarketClient()

    # Find a market to test on
    print("Searching for markets...")
    markets = client.search_markets("president", limit=5)

    market = None
    for m in markets:
        if m.yes_token_id:
            market = m
            break

    if not market:
        print("No suitable market found.")
        return

    print(f"Testing on: {market.question}")

    # Fetch history
    history = client.get_price_history(market.yes_token_id, interval="max", fidelity=100)
    print(f"Got {len(history)} price points\n")

    if len(history) < 15:
        print("Not enough data for this strategy.")
        return

    # Run our custom strategy
    strategy = VolumeWeightedDipBuyer(lookback=10, dip_threshold=0.08, position_size=0.15)

    backtester = Backtester(starting_balance=1000.0, verbose=True)
    result = backtester.run_single_market(
        strategy=strategy,
        market=market,
        price_history=history,
        resolved_outcome=market.outcome,
    )
    print(result.summary())


if __name__ == "__main__":
    main()
