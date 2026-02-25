#!/usr/bin/env python3
"""Example: Run paper trading with live Polymarket data.

Usage:
    python examples/paper_trade_live.py
"""

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.engine.paper_trader import PaperTrader
from polymarket_trader.strategies.threshold import ThresholdStrategy


def main():
    client = PolymarketClient()

    # 1. Find markets to trade
    print("Finding active markets to trade...\n")
    markets = client.get_markets(limit=10, active=True)

    for m in markets[:5]:
        print(f"  {m.yes_price:>5.1%} | {m.question[:60]}")
    print()

    # 2. Set up paper trader
    strategy = ThresholdStrategy(
        buy_below=0.35,
        sell_above=0.70,
        position_size=0.10,
    )

    trader = PaperTrader(
        client=client,
        strategy=strategy,
        starting_balance=1000.0,
        poll_interval=120,  # check every 2 minutes
        save_path="paper_portfolio.json",
    )

    # Add top markets to watchlist
    for m in markets:
        trader.add_market(m.condition_id)

    # 3. Run (will poll until interrupted with Ctrl+C)
    # Use max_iterations for a limited run
    trader.run_loop(max_iterations=5)  # 5 cycles for demo; remove for continuous


if __name__ == "__main__":
    main()
