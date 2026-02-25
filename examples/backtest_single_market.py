#!/usr/bin/env python3
"""Example: Backtest a strategy on a single Polymarket market.

Usage:
    python examples/backtest_single_market.py
"""

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.engine.backtester import Backtester
from polymarket_trader.strategies.threshold import ThresholdStrategy
from polymarket_trader.strategies.momentum import MomentumStrategy
from polymarket_trader.strategies.mean_reversion import MeanReversionStrategy


def main():
    client = PolymarketClient()

    # 1. Search for an interesting market
    print("Searching for markets...")
    markets = client.search_markets("election", limit=5)

    if not markets:
        print("No markets found. Try a different search term.")
        return

    # Pick the first market with a YES token
    market = None
    for m in markets:
        if m.yes_token_id and not m.closed:
            market = m
            break

    if not market:
        # Fallback to first result
        market = markets[0]

    print(f"\nSelected: {market.question}")
    print(f"  YES: {market.yes_price:.1%}  |  Volume: ${market.volume:,.0f}")
    print(f"  Condition ID: {market.condition_id}")

    # 2. Fetch price history
    print("\nFetching price history...")
    history = client.get_price_history(
        market.yes_token_id,
        interval="max",
        fidelity=100,
    )
    print(f"  Got {len(history)} data points")

    if len(history) < 10:
        print("Not enough price history for backtesting.")
        return

    # 3. Run backtests with different strategies
    backtester = Backtester(
        client=client,
        starting_balance=1000.0,
        verbose=True,
    )

    strategies = [
        ThresholdStrategy(buy_below=0.35, sell_above=0.65, position_size=0.20),
        MomentumStrategy(short_window=5, long_window=15, position_size=0.15),
        MeanReversionStrategy(window=15, entry_z=-1.5, exit_z=0.5, position_size=0.15),
    ]

    print("\n" + "=" * 70)
    for strategy in strategies:
        print(f"\nRunning: {strategy.name}")
        print("-" * 40)
        result = backtester.run_single_market(
            strategy=strategy,
            market=market,
            price_history=history,
            resolved_outcome=market.outcome,
        )
        print(result.summary())


if __name__ == "__main__":
    main()
