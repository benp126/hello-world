"""Arbitrage scanner: find and exploit binary arb on short-duration Bitcoin markets.

Continuously scans Polymarket for Bitcoin-related binary markets that expire
within a short window (default <=15 min), checks order books for pricing
inefficiencies where YES_ask + NO_ask < $1.00, and paper-trades the arb.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from polymarket_trader.api.client import PolymarketClient, PolymarketAPIError
from polymarket_trader.api.models import Market, OrderBook
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.strategies.arbitrage import BinaryArbitrageStrategy
from polymarket_trader.utils.metrics import calculate_metrics, format_metrics


@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""
    market: Market
    yes_ask: float
    no_ask: float
    combined_cost: float
    edge: float  # $1.00 - combined_cost
    edge_cents: float
    max_pairs: float  # limited by order book depth
    timestamp: datetime


@dataclass
class ArbScanResult:
    """Summary of a single scan cycle."""
    timestamp: datetime
    markets_scanned: int
    bitcoin_markets_found: int
    short_duration_found: int
    opportunities: list[ArbOpportunity] = field(default_factory=list)
    trades_executed: int = 0
    errors: list[str] = field(default_factory=list)


# Keywords that identify Bitcoin price markets
BTC_KEYWORDS = [
    "bitcoin", "btc", "Bitcoin",
]

# Patterns for short-duration market questions
# e.g., "Will Bitcoin be above $100,000 at 4:15 PM ET on February 25?"
SHORT_DURATION_PATTERNS = [
    r"\d{1,2}:\d{2}\s*(AM|PM|am|pm)",  # has a specific time
    r"\d{1,2}(:\d{2})?\s*(AM|PM|am|pm)\s*(ET|PT|CT|UTC)",  # time with timezone
]


def is_bitcoin_market(market: Market) -> bool:
    """Check if a market is Bitcoin-related."""
    text = f"{market.question} {market.description} {market.slug}".lower()
    return any(kw.lower() in text for kw in BTC_KEYWORDS)


def is_short_duration(market: Market, max_minutes: int = 15) -> bool:
    """Check if a market expires within max_minutes from now.

    Uses end_date if available. Also checks the question text for time
    patterns that suggest short-duration markets (e.g., "at 4:15 PM ET").
    """
    # Check end_date field
    if market.end_date:
        try:
            end = datetime.fromisoformat(market.end_date.replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            remaining = end - now
            if timedelta(0) < remaining <= timedelta(minutes=max_minutes):
                return True
        except (ValueError, TypeError):
            pass

    # Check question text for time patterns (these markets are typically
    # short-duration candle markets)
    for pattern in SHORT_DURATION_PATTERNS:
        if re.search(pattern, market.question):
            return True

    return False


def minutes_until_close(market: Market) -> Optional[float]:
    """Get minutes until market closes, or None if unknown."""
    if market.end_date:
        try:
            end = datetime.fromisoformat(market.end_date.replace("Z", "+00:00"))
            now = datetime.now(tz=timezone.utc)
            remaining = (end - now).total_seconds() / 60
            return remaining if remaining > 0 else 0
        except (ValueError, TypeError):
            pass
    return None


class ArbScanner:
    """Scan for and execute binary arbitrage on short-duration Bitcoin markets.

    Workflow per cycle:
    1. Fetch active Bitcoin markets from Gamma API
    2. Filter to short-duration (<=15 min) markets
    3. For each, fetch both YES and NO order books
    4. Check if best YES ask + best NO ask < $1.00 (minus min edge)
    5. If arb exists, paper-buy equal shares of both sides
    6. Track positions until settlement

    Args:
        client: Polymarket API client.
        starting_balance: Paper trading balance.
        min_edge_cents: Minimum edge in cents (default 0.5 = half a cent).
        position_size: Dollar amount per arb opportunity.
        max_concurrent_arbs: Max simultaneous arb positions.
        max_minutes_to_expiry: Only trade markets expiring within this window.
        poll_interval: Seconds between scan cycles.
        save_path: File path to persist portfolio state.
    """

    def __init__(
        self,
        client: Optional[PolymarketClient] = None,
        starting_balance: float = 1000.0,
        min_edge_cents: float = 0.5,
        position_size: float = 100.0,
        max_concurrent_arbs: int = 20,
        max_minutes_to_expiry: int = 15,
        poll_interval: int = 10,
        save_path: Optional[str] = None,
    ):
        self.client = client or PolymarketClient()
        self.min_edge_cents = min_edge_cents
        self.min_edge = min_edge_cents / 100.0
        self.position_size = position_size
        self.max_minutes_to_expiry = max_minutes_to_expiry
        self.poll_interval = poll_interval
        self.save_path = save_path

        self.strategy = BinaryArbitrageStrategy(
            min_edge_cents=min_edge_cents,
            position_size=position_size,
            max_concurrent_arbs=max_concurrent_arbs,
            use_order_book=True,
        )
        self.portfolio = Portfolio(starting_balance=starting_balance)

        # Track arbed markets for settlement
        self._active_arbs: dict[str, ArbOpportunity] = {}  # condition_id -> opp
        self._settled_arbs: list[dict] = []  # settled arb records
        self._running = False
        self._total_scans = 0
        self._total_opps_found = 0
        self._total_arbs_executed = 0

    def scan_once(self) -> ArbScanResult:
        """Run a single scan cycle. Returns scan results."""
        now = datetime.now(tz=timezone.utc)
        result = ArbScanResult(
            timestamp=now,
            markets_scanned=0,
            bitcoin_markets_found=0,
            short_duration_found=0,
        )

        # 1. Fetch Bitcoin markets
        try:
            all_markets = self._fetch_bitcoin_markets()
        except PolymarketAPIError as e:
            result.errors.append(f"API error fetching markets: {e}")
            return result

        result.markets_scanned = len(all_markets)

        # 2. Filter to Bitcoin markets
        btc_markets = [m for m in all_markets if is_bitcoin_market(m)]
        result.bitcoin_markets_found = len(btc_markets)

        # 3. Filter to short-duration and active
        short_markets = [
            m for m in btc_markets
            if m.active and not m.closed and is_short_duration(m, self.max_minutes_to_expiry)
        ]
        result.short_duration_found = len(short_markets)

        # 4. Check each for arb opportunities
        for market in short_markets:
            if not market.yes_token_id or not market.no_token_id:
                continue

            # Skip if already arbed
            if market.condition_id in self._active_arbs:
                continue

            opp = self._check_arb(market, now)
            if opp:
                result.opportunities.append(opp)

        # 5. Execute arbs
        for opp in result.opportunities:
            executed = self._execute_arb(opp, now)
            if executed:
                result.trades_executed += 1

        # 6. Check for settled markets
        self._check_settlements(now)

        # 7. Save state
        self.portfolio.take_snapshot(timestamp=now)
        if self.save_path:
            try:
                self.portfolio.save(self.save_path)
            except Exception:
                pass

        self._total_scans += 1
        self._total_opps_found += len(result.opportunities)
        return result

    def run_loop(self, max_iterations: Optional[int] = None):
        """Run the scanner in a continuous loop."""
        print(f"\n{'=' * 65}")
        print(f"  BINARY ARBITRAGE SCANNER - Bitcoin Short-Duration Markets")
        print(f"{'=' * 65}")
        print(f"  Balance:        ${self.portfolio.starting_balance:,.2f}")
        print(f"  Min Edge:       {self.min_edge_cents:.1f} cents")
        print(f"  Position Size:  ${self.position_size:,.2f}")
        print(f"  Max Expiry:     {self.max_minutes_to_expiry} min")
        print(f"  Poll Interval:  {self.poll_interval}s")
        print(f"{'=' * 65}\n")

        self._running = True
        iteration = 0

        try:
            while self._running:
                if max_iterations is not None and iteration >= max_iterations:
                    break

                iteration += 1
                now = datetime.now(tz=timezone.utc)
                print(f"[{now.strftime('%H:%M:%S')}] Scan #{iteration}")

                result = self.scan_once()
                self._print_scan_result(result)

                pv = self.portfolio.total_value
                pnl = self.portfolio.total_pnl
                active = len(self._active_arbs)
                print(f"  Portfolio: ${pv:,.2f} | P&L: ${pnl:+,.2f} | Active arbs: {active}")
                print()

                if max_iterations is None or iteration < max_iterations:
                    time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            print("\nStopping scanner...")
        finally:
            self._running = False
            print(self.summary())

    def stop(self):
        """Stop the scan loop."""
        self._running = False

    def summary(self) -> str:
        """Get a summary of all arb activity."""
        metrics = calculate_metrics(self.portfolio)
        lines = [
            "",
            "=" * 65,
            "  ARBITRAGE SCANNER SUMMARY",
            "=" * 65,
            f"  Total scans:         {self._total_scans}",
            f"  Opportunities found: {self._total_opps_found}",
            f"  Arbs executed:       {self._total_arbs_executed}",
            f"  Arbs settled:        {len(self._settled_arbs)}",
            f"  Active arbs:         {len(self._active_arbs)}",
            "",
        ]

        if self._settled_arbs:
            lines.append("  Settled Arbs:")
            lines.append(f"  {'Market':<40} {'Cost':>8} {'Edge':>8} {'Profit':>8}")
            lines.append(f"  {'-'*40} {'-'*8} {'-'*8} {'-'*8}")
            for arb in self._settled_arbs:
                q = arb["question"][:38] + ".." if len(arb["question"]) > 38 else arb["question"]
                lines.append(
                    f"  {q:<40} ${arb['cost']:>7.2f} "
                    f"{arb['edge_cents']:>6.2f}c ${arb['profit']:>7.2f}"
                )
            total_settled_profit = sum(a["profit"] for a in self._settled_arbs)
            lines.append(f"  {'Total settled profit:':<40} {'':>8} {'':>8} ${total_settled_profit:>7.2f}")

        if self._active_arbs:
            lines.append("")
            lines.append("  Active Arbs (awaiting settlement):")
            for cid, opp in self._active_arbs.items():
                q = opp.market.question[:50] + ".." if len(opp.market.question) > 50 else opp.market.question
                mins = minutes_until_close(opp.market)
                time_str = f"{mins:.0f}m" if mins is not None else "?"
                lines.append(f"    [{time_str}] {q} (edge={opp.edge_cents:.2f}c)")

        lines.append("")
        lines.append(format_metrics(metrics))
        return "\n".join(lines)

    def _fetch_bitcoin_markets(self) -> list[Market]:
        """Fetch Bitcoin-related markets from the API."""
        # Search with multiple queries to catch different naming patterns
        markets = []
        seen_ids = set()

        for query in ["Bitcoin", "BTC"]:
            try:
                results = self.client.search_markets(query, limit=100)
                for m in results:
                    if m.condition_id not in seen_ids:
                        markets.append(m)
                        seen_ids.add(m.condition_id)
            except PolymarketAPIError:
                continue

        return markets

    def _check_arb(self, market: Market, now: datetime) -> Optional[ArbOpportunity]:
        """Check a market's order books for arb opportunity."""
        yes_token = market.yes_token_id
        no_token = market.no_token_id

        try:
            yes_book = self.client.get_order_book(yes_token)
            no_book = self.client.get_order_book(no_token)
        except PolymarketAPIError:
            return None

        # We need the best ask (cheapest price to buy) for each side
        yes_ask = yes_book.best_ask
        no_ask = no_book.best_ask

        if yes_ask is None or no_ask is None:
            return None
        if yes_ask <= 0 or no_ask <= 0:
            return None

        combined_cost = yes_ask + no_ask
        edge = 1.0 - combined_cost

        if edge < self.min_edge:
            return None

        # Check order book depth - how many pairs can we actually buy?
        # Limited by the smaller side's available size at best ask
        yes_size = yes_book.asks[0].size if yes_book.asks else 0
        no_size = no_book.asks[0].size if no_book.asks else 0
        max_pairs_by_depth = min(yes_size, no_size)

        # Also limit by our position size
        max_pairs_by_cash = self.position_size / combined_cost if combined_cost > 0 else 0
        max_pairs = min(max_pairs_by_depth, max_pairs_by_cash)

        if max_pairs < 1:
            return None

        return ArbOpportunity(
            market=market,
            yes_ask=yes_ask,
            no_ask=no_ask,
            combined_cost=combined_cost,
            edge=edge,
            edge_cents=edge * 100,
            max_pairs=max_pairs,
            timestamp=now,
        )

    def _execute_arb(self, opp: ArbOpportunity, now: datetime) -> bool:
        """Execute an arbitrage trade (buy both YES and NO)."""
        market = opp.market
        pairs = min(opp.max_pairs, self.position_size / opp.combined_cost)

        if pairs < 1:
            return False

        # Buy YES side
        yes_trade = self.portfolio.buy(
            token_id=market.yes_token_id,
            shares=pairs,
            price=opp.yes_ask,
            market_condition_id=market.condition_id,
            market_question=market.question,
            outcome="Yes",
            timestamp=now,
        )
        if not yes_trade:
            return False

        # Buy NO side
        no_trade = self.portfolio.buy(
            token_id=market.no_token_id,
            shares=pairs,
            price=opp.no_ask,
            market_condition_id=market.condition_id,
            market_question=market.question,
            outcome="No",
            timestamp=now,
        )
        if not no_trade:
            # Undo the YES buy if NO fails (insufficient cash)
            self.portfolio.sell(
                token_id=market.yes_token_id,
                shares=pairs,
                price=opp.yes_ask,
                timestamp=now,
            )
            return False

        # Track the active arb
        self._active_arbs[market.condition_id] = opp
        self._total_arbs_executed += 1

        return True

    def _check_settlements(self, now: datetime):
        """Check if any arbed markets have settled."""
        settled_ids = []

        for condition_id, opp in self._active_arbs.items():
            try:
                market = self.client.get_market(condition_id)
            except PolymarketAPIError:
                continue

            if not market.closed or not market.outcome:
                continue

            # Market has resolved - settle both sides
            yes_won = market.outcome.lower() == "yes"

            yes_token = market.yes_token_id
            no_token = market.no_token_id

            # Calculate profit before settling
            yes_cost = opp.yes_ask * opp.max_pairs
            no_cost = opp.no_ask * opp.max_pairs
            total_cost = yes_cost + no_cost
            # Payout is always max_pairs * $1.00 (one side wins)
            payout = opp.max_pairs * 1.0
            profit = payout - total_cost

            if yes_token and yes_token in self.portfolio.positions:
                self.portfolio.settle(yes_token, winning=yes_won, timestamp=now)
            if no_token and no_token in self.portfolio.positions:
                self.portfolio.settle(no_token, winning=not yes_won, timestamp=now)

            self.strategy.on_settled(condition_id)

            self._settled_arbs.append({
                "condition_id": condition_id,
                "question": opp.market.question,
                "yes_ask": opp.yes_ask,
                "no_ask": opp.no_ask,
                "cost": total_cost,
                "edge_cents": opp.edge_cents,
                "pairs": opp.max_pairs,
                "profit": profit,
                "outcome": market.outcome,
                "settled_at": now.isoformat(),
            })

            settled_ids.append(condition_id)

        for cid in settled_ids:
            del self._active_arbs[cid]

    def _print_scan_result(self, result: ArbScanResult):
        """Print a formatted scan result."""
        print(f"  Scanned: {result.markets_scanned} markets | "
              f"BTC: {result.bitcoin_markets_found} | "
              f"Short-dur: {result.short_duration_found}")

        if result.opportunities:
            for opp in result.opportunities:
                q = opp.market.question[:45]
                mins = minutes_until_close(opp.market)
                time_str = f"{mins:.0f}m" if mins is not None else "?"
                est_profit = opp.max_pairs * opp.edge
                print(
                    f"  >>> ARB FOUND [{time_str}]: {q}")
                print(
                    f"      YES@{opp.yes_ask:.4f} + NO@{opp.no_ask:.4f} = "
                    f"{opp.combined_cost:.4f} | "
                    f"edge={opp.edge_cents:.2f}c | "
                    f"pairs={opp.max_pairs:.0f} | "
                    f"est_profit=${est_profit:.2f}")
        elif result.short_duration_found > 0:
            print(f"  No arb opportunities (all markets priced efficiently)")

        if result.trades_executed:
            print(f"  Executed {result.trades_executed} arb trade(s)")

        for err in result.errors:
            print(f"  ERROR: {err}")
