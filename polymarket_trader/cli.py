"""CLI entry point for the Polymarket strategy backtesting & paper trading system."""

import argparse
import sys

from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.engine.backtester import Backtester
from polymarket_trader.engine.paper_trader import PaperTrader
from polymarket_trader.strategies.mean_reversion import MeanReversionStrategy
from polymarket_trader.strategies.momentum import MomentumStrategy
from polymarket_trader.strategies.multi_market import DiversifiedValueStrategy
from polymarket_trader.strategies.threshold import ThresholdStrategy


STRATEGIES = {
    "threshold": ThresholdStrategy,
    "momentum": MomentumStrategy,
    "mean_reversion": MeanReversionStrategy,
    "diversified": DiversifiedValueStrategy,
}


def cmd_search(args):
    """Search for markets."""
    client = PolymarketClient()
    markets = client.search_markets(args.query, limit=args.limit)

    if not markets:
        print("No markets found.")
        return

    print(f"\nFound {len(markets)} markets:\n")
    print(f"{'#':<4} {'YES':>6} {'NO':>6} {'Volume':>12} {'Question'}")
    print("-" * 90)
    for i, m in enumerate(markets, 1):
        q = m.question[:55] + "..." if len(m.question) > 55 else m.question
        status = "CLOSED" if m.closed else "ACTIVE"
        print(f"{i:<4} {m.yes_price:>5.1%} {m.no_price:>5.1%} ${m.volume:>11,.0f} {q} [{status}]")
        if args.verbose:
            print(f"     ID: {m.condition_id}")
            if m.yes_token_id:
                print(f"     YES token: {m.yes_token_id}")


def cmd_market(args):
    """Show details for a specific market."""
    client = PolymarketClient()
    market = client.get_market(args.condition_id)

    print(f"\n{'=' * 70}")
    print(f"  {market.question}")
    print(f"{'=' * 70}")
    print(f"  Condition ID: {market.condition_id}")
    print(f"  Status:       {'CLOSED' if market.closed else 'ACTIVE'}")
    print(f"  Volume:       ${market.volume:,.2f}")
    print(f"  Liquidity:    ${market.liquidity:,.2f}")
    if market.end_date:
        print(f"  End Date:     {market.end_date}")
    if market.outcome:
        print(f"  Outcome:      {market.outcome}")
    print()

    for token in market.tokens:
        print(f"  {token.outcome}: {token.price:.1%} (token: {token.token_id[:20]}...)")

    if market.yes_token_id:
        print(f"\n  Order Book:")
        try:
            book = client.get_order_book(market.yes_token_id)
            if book.best_bid is not None:
                print(f"    Best Bid: {book.best_bid:.4f}  ({len(book.bids)} levels)")
            if book.best_ask is not None:
                print(f"    Best Ask: {book.best_ask:.4f}  ({len(book.asks)} levels)")
            if book.spread is not None:
                print(f"    Spread:   {book.spread:.4f}")
        except Exception as e:
            print(f"    Could not fetch: {e}")

    print()


def cmd_history(args):
    """Show price history for a market."""
    client = PolymarketClient()
    market = client.get_market(args.condition_id)

    if not market.yes_token_id:
        print("Market has no YES token ID.")
        return

    history = client.get_price_history(
        market.yes_token_id,
        interval=args.interval,
        fidelity=args.points,
    )

    if not history:
        print("No price history available.")
        return

    print(f"\nPrice history for: {market.question}")
    print(f"Interval: {args.interval}, Points: {len(history)}\n")
    print(f"{'Date':<22} {'Price':>8} {'Bar'}")
    print("-" * 60)

    for pt in history:
        bar_len = int(pt.price * 40)
        bar = "#" * bar_len
        print(f"{pt.timestamp.strftime('%Y-%m-%d %H:%M'):<22} {pt.price:>7.1%} |{bar}")


def cmd_backtest(args):
    """Run a backtest."""
    client = PolymarketClient()
    strategy = _build_strategy(args)

    if args.condition_id:
        # Single market backtest
        print(f"Backtesting {strategy.name} on single market...")
        market = client.get_market(args.condition_id)

        if not market.yes_token_id:
            print("Error: Market has no YES token ID.")
            return

        history = client.get_price_history(
            market.yes_token_id,
            interval=args.interval,
            fidelity=args.points,
        )

        if len(history) < 5:
            print(f"Error: Insufficient price data ({len(history)} points). Try a longer interval.")
            return

        backtester = Backtester(
            client=client,
            starting_balance=args.balance,
            verbose=args.verbose,
        )
        result = backtester.run_single_market(
            strategy=strategy,
            market=market,
            price_history=history,
            resolved_outcome=market.outcome,
        )
    else:
        # Multi-market backtest
        print(f"Backtesting {strategy.name} across top markets...")
        markets = client.get_markets(
            limit=args.num_markets,
            active=None if args.include_closed else True,
        )
        print(f"Fetched {len(markets)} markets.\n")

        backtester = Backtester(
            client=client,
            starting_balance=args.balance,
            verbose=args.verbose,
        )
        result = backtester.run_multi_market(
            strategy=strategy,
            markets=markets,
            interval=args.interval,
            fidelity=args.points,
        )

    print(result.summary())

    if args.verbose:
        print(f"\nTrade Log:")
        print(result.trade_log())


def cmd_paper_trade(args):
    """Start paper trading."""
    client = PolymarketClient()
    strategy = _build_strategy(args)

    trader = PaperTrader(
        client=client,
        strategy=strategy,
        starting_balance=args.balance,
        poll_interval=args.poll_interval,
        history_interval="1w",
        save_path=args.save_file,
    )

    # Add markets to watchlist
    if args.condition_ids:
        for cid in args.condition_ids:
            trader.add_market(cid)
    else:
        # Default: watch top active markets
        print(f"No markets specified. Fetching top {args.num_markets} active markets...")
        markets = client.get_markets(limit=args.num_markets, active=True)
        for m in markets:
            trader.add_market(m.condition_id)
        print(f"Watching {len(markets)} markets.\n")

    trader.run_loop(max_iterations=args.max_cycles)


def cmd_list_strategies(args):
    """List available strategies."""
    print("\nAvailable strategies:\n")
    print(f"  {'Name':<20} {'Description'}")
    print(f"  {'-'*18}  {'-'*50}")
    print(f"  {'threshold':<20} Buy below / sell above fixed price thresholds")
    print(f"  {'momentum':<20} Follow trend using moving average crossover")
    print(f"  {'mean_reversion':<20} Bet on prices reverting to their mean (z-score)")
    print(f"  {'diversified':<20} Spread small bets across many underpriced markets")
    print()
    print("Use --strategy <name> with backtest or paper-trade commands.")
    print("Each strategy has tunable parameters via --param key=value.\n")
    print("Example parameters:")
    print("  threshold:       buy_below=0.3 sell_above=0.7 position_size=0.1")
    print("  momentum:        short_window=5 long_window=20 min_trend_strength=0.02")
    print("  mean_reversion:  window=20 entry_z=-1.5 exit_z=0.5")
    print("  diversified:     max_price=0.4 min_price=0.05 max_positions=20 per_position_size=50")


def _build_strategy(args):
    """Build a strategy instance from CLI args."""
    strategy_name = getattr(args, "strategy", "threshold")
    if strategy_name not in STRATEGIES:
        print(f"Unknown strategy: {strategy_name}")
        print(f"Available: {', '.join(STRATEGIES.keys())}")
        sys.exit(1)

    # Parse extra params
    params = {}
    for p in getattr(args, "param", None) or []:
        if "=" not in p:
            print(f"Invalid param format: {p} (expected key=value)")
            sys.exit(1)
        key, val = p.split("=", 1)
        # Try to convert to numeric
        try:
            params[key] = int(val)
        except ValueError:
            try:
                params[key] = float(val)
            except ValueError:
                params[key] = val

    return STRATEGIES[strategy_name](**params)


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Strategy Backtesting & Paper Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search for markets
  %(prog)s search "presidential election"

  # View market details
  %(prog)s market <condition_id>

  # View price history
  %(prog)s history <condition_id> --interval max --points 100

  # Backtest on a single market
  %(prog)s backtest --condition-id <id> --strategy threshold --param buy_below=0.3

  # Backtest across top markets
  %(prog)s backtest --strategy momentum --num-markets 20 --balance 5000

  # Paper trade with live data
  %(prog)s paper-trade --strategy mean_reversion --balance 1000 --condition-ids <id1> <id2>
""",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # ── search ──
    p_search = subparsers.add_parser("search", help="Search for markets")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--limit", type=int, default=20, help="Max results")
    p_search.add_argument("-v", "--verbose", action="store_true")
    p_search.set_defaults(func=cmd_search)

    # ── market ──
    p_market = subparsers.add_parser("market", help="View market details")
    p_market.add_argument("condition_id", help="Market condition ID")
    p_market.set_defaults(func=cmd_market)

    # ── history ──
    p_hist = subparsers.add_parser("history", help="View price history")
    p_hist.add_argument("condition_id", help="Market condition ID")
    p_hist.add_argument("--interval", default="max", help="Time range: 1h, 6h, 1d, 1w, max")
    p_hist.add_argument("--points", type=int, default=60, help="Number of data points")
    p_hist.set_defaults(func=cmd_history)

    # ── backtest ──
    p_bt = subparsers.add_parser("backtest", help="Run a strategy backtest")
    p_bt.add_argument("--condition-id", help="Single market condition ID (omit for multi-market)")
    p_bt.add_argument("--strategy", default="threshold", choices=STRATEGIES.keys())
    p_bt.add_argument("--param", action="append", help="Strategy param: key=value (repeatable)")
    p_bt.add_argument("--balance", type=float, default=1000.0, help="Starting balance (USDC)")
    p_bt.add_argument("--interval", default="max", help="Price history interval")
    p_bt.add_argument("--points", type=int, default=100, help="Price data points to fetch")
    p_bt.add_argument("--num-markets", type=int, default=20, help="Number of markets for multi-market")
    p_bt.add_argument("--include-closed", action="store_true", help="Include closed/resolved markets")
    p_bt.add_argument("-v", "--verbose", action="store_true")
    p_bt.set_defaults(func=cmd_backtest)

    # ── paper-trade ──
    p_pt = subparsers.add_parser("paper-trade", help="Start live paper trading")
    p_pt.add_argument("--strategy", default="threshold", choices=STRATEGIES.keys())
    p_pt.add_argument("--param", action="append", help="Strategy param: key=value (repeatable)")
    p_pt.add_argument("--balance", type=float, default=1000.0, help="Starting balance (USDC)")
    p_pt.add_argument("--condition-ids", nargs="+", help="Market condition IDs to watch")
    p_pt.add_argument("--num-markets", type=int, default=10, help="Top markets to watch if no IDs given")
    p_pt.add_argument("--poll-interval", type=int, default=60, help="Seconds between checks")
    p_pt.add_argument("--max-cycles", type=int, default=None, help="Max polling cycles (None=infinite)")
    p_pt.add_argument("--save-file", default=None, help="File to save portfolio state")
    p_pt.set_defaults(func=cmd_paper_trade)

    # ── strategies ──
    p_strat = subparsers.add_parser("strategies", help="List available strategies")
    p_strat.set_defaults(func=cmd_list_strategies)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
