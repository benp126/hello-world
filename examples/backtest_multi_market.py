#!/usr/bin/env python3
"""Example: Backtest a strategy across multiple Polymarket markets.

Usage:
    python examples/backtest_multi_market.py
"""

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.engine.backtester import Backtester
from polymarket_trader.strategies.multi_market import DiversifiedValueStrategy
from polymarket_trader.strategies.threshold import ThresholdStrategy


def main():
    client = PolymarketClient()

    # 1. Fetch top active markets by volume
    print("Fetching top active markets...")
    markets = client.get_markets(limit=15, active=True)
    print(f"Found {len(markets)} markets\n")

    for i, m in enumerate(markets[:10], 1):
        q = m.question[:50] + "..." if len(m.question) > 50 else m.question
        print(f"  {i:>2}. {m.yes_price:>5.1%} YES  |  ${m.volume:>10,.0f} vol  |  {q}")

    # 2. Run diversified value strategy
    strategy = DiversifiedValueStrategy(
        max_price=0.45,
        min_price=0.05,
        max_positions=10,
        per_position_size=100.0,
    )

    print(f"\nBacktesting: {strategy.name}")
    print(f"Starting balance: $5,000")
    print("-" * 50)

    backtester = Backtester(
        client=client,
        starting_balance=5000.0,
        verbose=True,
    )
    result = backtester.run_multi_market(
        strategy=strategy,
        markets=markets,
        interval="max",
        fidelity=80,
    )
    print(result.summary())

    # 3. Compare with threshold strategy
    strategy2 = ThresholdStrategy(
        buy_below=0.40,
        sell_above=0.65,
        position_size=0.08,
    )

    print(f"\nBacktesting: {strategy2.name}")
    print("-" * 50)

    result2 = backtester.run_multi_market(
        strategy=strategy2,
        markets=markets,
        interval="max",
        fidelity=80,
    )
    print(result2.summary())


if __name__ == "__main__":
    main()
