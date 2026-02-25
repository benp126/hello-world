#!/usr/bin/env python3
"""Example: Scan for binary arbitrage on short-duration Bitcoin markets.

Binary arbitrage works because on Polymarket, every binary market resolves
with one side paying $1 and the other paying $0. If you can buy BOTH the
YES and NO tokens for a combined cost of less than $1.00, you're guaranteed
a profit equal to the difference when the market resolves.

For example:
  - YES ask = $0.52
  - NO ask  = $0.47
  - Total   = $0.99  (1 cent edge per share pair)
  - Buy 100 pairs = $99.00 cost
  - Market resolves = $100.00 payout (one side wins)
  - Profit = $1.00

This scanner targets Bitcoin 15-minute markets where prices can briefly
misprice, creating these arb windows.

Usage:
    python examples/bitcoin_arb_scanner.py

Or via CLI:
    python -m polymarket_trader.cli arb-scan --balance 5000 --min-edge 0.5
"""

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.engine.arb_scanner import (
    ArbScanner,
    is_bitcoin_market,
    is_short_duration,
)


def main():
    client = PolymarketClient()

    # First, let's see what Bitcoin markets are available right now
    print("Scanning for Bitcoin markets...\n")

    all_btc = []
    for query in ["Bitcoin", "BTC"]:
        try:
            markets = client.search_markets(query, limit=50)
            all_btc.extend(m for m in markets if is_bitcoin_market(m))
        except Exception as e:
            print(f"  Search '{query}' failed: {e}")

    # Deduplicate
    seen = set()
    btc_markets = []
    for m in all_btc:
        if m.condition_id not in seen:
            btc_markets.append(m)
            seen.add(m.condition_id)

    print(f"Found {len(btc_markets)} Bitcoin markets total\n")

    # Show active ones and their YES+NO pricing
    active = [m for m in btc_markets if m.active and not m.closed]
    short = [m for m in active if is_short_duration(m, max_minutes=15)]

    print(f"Active: {len(active)} | Short-duration (<=15min): {len(short)}\n")

    if active:
        print(f"{'YES':>6} {'NO':>6} {'SUM':>6} {'Edge':>6} {'Question'}")
        print("-" * 80)
        for m in active[:20]:
            combined = m.yes_price + m.no_price
            edge_cents = (1.0 - combined) * 100
            marker = " <<<" if edge_cents >= 0.5 else ""
            is_short = "*" if is_short_duration(m, 15) else " "
            q = m.question[:45] + "..." if len(m.question) > 45 else m.question
            print(
                f"{m.yes_price:>5.1%} {m.no_price:>5.1%} "
                f"{combined:>5.3f} {edge_cents:>+5.1f}c "
                f"{is_short}{q}{marker}"
            )
        print("\n  * = short-duration market | <<< = potential arb opportunity")
        print("  (Note: above uses midpoint prices. Real arb uses ask prices from order book)\n")

    # Now check order books for the short-duration markets
    if short:
        print("Checking order books for short-duration markets...\n")
        for m in short[:10]:
            if not m.yes_token_id or not m.no_token_id:
                continue
            try:
                yes_book = client.get_order_book(m.yes_token_id)
                no_book = client.get_order_book(m.no_token_id)
            except Exception as e:
                print(f"  Could not fetch order book: {e}")
                continue

            yes_ask = yes_book.best_ask
            no_ask = no_book.best_ask

            if yes_ask and no_ask:
                combined = yes_ask + no_ask
                edge = (1.0 - combined) * 100
                q = m.question[:50]
                status = "ARB!" if edge >= 0.5 else "no arb"
                print(f"  [{status:>6}] YES ask={yes_ask:.4f} + NO ask={no_ask:.4f} = {combined:.4f} (edge={edge:+.2f}c) | {q}")

    # Run the scanner for a few cycles
    print("\n" + "=" * 65)
    print("Starting arb scanner (5 cycles demo)...")
    print("=" * 65)

    scanner = ArbScanner(
        client=client,
        starting_balance=5000.0,
        min_edge_cents=0.5,    # at least 0.5 cents profit per pair
        position_size=200.0,    # $200 per arb opportunity
        max_concurrent_arbs=10,
        max_minutes_to_expiry=15,
        poll_interval=15,       # check every 15 seconds
    )

    scanner.run_loop(max_iterations=5)


if __name__ == "__main__":
    main()
