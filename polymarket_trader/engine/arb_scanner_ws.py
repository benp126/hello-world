"""Real-time WebSocket-based arbitrage scanner.

Instead of polling REST every N seconds, this connects to Polymarket's
WebSocket stream and reacts to order book changes in real-time (sub-second).

Flow:
1. Discover Bitcoin short-duration markets via REST (periodic refresh)
2. Subscribe to all YES+NO token order books via WebSocket
3. On every book update, instantly check if YES_ask + NO_ask < $1.00
4. Execute paper arb immediately when edge appears
5. Periodically check for market settlements via REST
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from polymarket_trader.api.client import PolymarketClient, PolymarketAPIError
from polymarket_trader.api.websocket import BookState, PolymarketWebSocket
from polymarket_trader.engine.arb_scanner import (
    ArbOpportunity,
    is_bitcoin_market,
    is_short_duration,
    minutes_until_close,
)
from polymarket_trader.engine.portfolio import Portfolio
from polymarket_trader.utils.metrics import calculate_metrics, format_metrics

logger = logging.getLogger(__name__)


class RealtimeArbScanner:
    """WebSocket-powered arb scanner with sub-second reaction time.

    Uses REST API only for:
    - Market discovery (every market_refresh_interval seconds)
    - Settlement checks (every settlement_check_interval seconds)

    Uses WebSocket for:
    - Real-time order book updates (fires callback on every change)

    Args:
        client: REST API client for market discovery.
        starting_balance: Paper trading balance.
        min_edge_cents: Minimum arb edge in cents (default 0.5).
        position_size: Dollars per arb opportunity.
        max_concurrent_arbs: Max simultaneous arb positions.
        max_minutes_to_expiry: Only trade markets expiring within this window.
        market_refresh_interval: Seconds between REST market discovery scans.
        settlement_check_interval: Seconds between settlement checks.
    """

    def __init__(
        self,
        client: Optional[PolymarketClient] = None,
        starting_balance: float = 1000.0,
        min_edge_cents: float = 0.5,
        position_size: float = 100.0,
        max_concurrent_arbs: int = 20,
        max_minutes_to_expiry: int = 15,
        market_refresh_interval: int = 30,
        settlement_check_interval: int = 15,
        save_path: Optional[str] = None,
    ):
        self.client = client or PolymarketClient()
        self.min_edge = min_edge_cents / 100.0
        self.min_edge_cents = min_edge_cents
        self.position_size = position_size
        self.max_concurrent_arbs = max_concurrent_arbs
        self.max_minutes_to_expiry = max_minutes_to_expiry
        self.market_refresh_interval = market_refresh_interval
        self.settlement_check_interval = settlement_check_interval
        self.save_path = save_path

        self.portfolio = Portfolio(starting_balance=starting_balance)

        # Token -> market mapping so we can look up market info from book updates
        self._token_to_market: dict[str, dict] = {}
        # condition_id -> (yes_token_id, no_token_id)
        self._market_tokens: dict[str, tuple[str, str]] = {}

        # Active arbs
        self._active_arbs: dict[str, ArbOpportunity] = {}
        self._settled_arbs: list[dict] = []
        self._arbed_condition_ids: set[str] = set()

        # Stats
        self._total_checks = 0
        self._total_opps_found = 0
        self._total_arbs_executed = 0
        self._start_time: Optional[datetime] = None

        # WebSocket
        self._ws: Optional[PolymarketWebSocket] = None
        self._running = False

    def _on_book_update(self, token_id: str, book: BookState):
        """Called on every WebSocket order book update. This is the hot path."""
        self._total_checks += 1
        info = self._token_to_market.get(token_id)
        if not info:
            return

        condition_id = info["condition_id"]
        yes_token = info["yes_token"]
        no_token = info["no_token"]

        # Skip if already arbed
        if condition_id in self._arbed_condition_ids:
            return

        # Skip if at max arbs
        if len(self._active_arbs) >= self.max_concurrent_arbs:
            return

        # We need both books to be populated
        if not self._ws:
            return
        yes_book = self._ws.books.get(yes_token)
        no_book = self._ws.books.get(no_token)
        if not yes_book or not no_book:
            return

        yes_ask = yes_book.best_ask
        no_ask = no_book.best_ask
        if yes_ask is None or no_ask is None:
            return
        if yes_ask <= 0 or no_ask <= 0:
            return

        combined = yes_ask + no_ask
        edge = 1.0 - combined

        if edge < self.min_edge:
            return

        # Check depth
        yes_size = yes_book.best_ask_size
        no_size = no_book.best_ask_size
        max_pairs_by_depth = min(yes_size, no_size)
        max_pairs_by_cash = self.position_size / combined if combined > 0 else 0
        max_pairs = min(max_pairs_by_depth, max_pairs_by_cash)

        if max_pairs < 1:
            return

        # ARB FOUND - execute immediately
        now = datetime.now(tz=timezone.utc)
        opp = ArbOpportunity(
            market=info["market_obj"],
            yes_ask=yes_ask,
            no_ask=no_ask,
            combined_cost=combined,
            edge=edge,
            edge_cents=edge * 100,
            max_pairs=max_pairs,
            timestamp=now,
        )

        self._total_opps_found += 1
        executed = self._execute_arb(opp, now)

        q = info["question"][:50]
        latency_ms = (datetime.now(tz=timezone.utc) - now).total_seconds() * 1000
        if executed:
            est_profit = max_pairs * edge
            print(
                f"  >>> ARB EXECUTED [{latency_ms:.0f}ms]: {q}\n"
                f"      YES@{yes_ask:.4f} + NO@{no_ask:.4f} = {combined:.4f} | "
                f"edge={edge*100:.2f}c | pairs={max_pairs:.0f} | "
                f"est_profit=${est_profit:.2f}"
            )
        else:
            print(f"  >>> ARB FOUND but execution failed: {q} (edge={edge*100:.2f}c)")

    def _execute_arb(self, opp: ArbOpportunity, now: datetime) -> bool:
        """Execute paired buy."""
        market = opp.market
        pairs = min(opp.max_pairs, self.position_size / opp.combined_cost)
        if pairs < 1:
            return False

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
            self.portfolio.sell(
                token_id=market.yes_token_id,
                shares=pairs,
                price=opp.yes_ask,
                timestamp=now,
            )
            return False

        self._active_arbs[market.condition_id] = opp
        self._arbed_condition_ids.add(market.condition_id)
        self._total_arbs_executed += 1
        return True

    async def _refresh_markets(self):
        """Periodically discover new Bitcoin short-duration markets and subscribe."""
        while self._running:
            try:
                markets = []
                seen = set()
                for query in ["Bitcoin", "BTC"]:
                    try:
                        results = self.client.search_markets(query, limit=100)
                        for m in results:
                            if m.condition_id not in seen and is_bitcoin_market(m):
                                markets.append(m)
                                seen.add(m.condition_id)
                    except PolymarketAPIError:
                        continue

                short = [
                    m for m in markets
                    if m.active and not m.closed
                    and is_short_duration(m, self.max_minutes_to_expiry)
                    and m.yes_token_id and m.no_token_id
                ]

                new_tokens = []
                for m in short:
                    yes_tid = m.yes_token_id
                    no_tid = m.no_token_id

                    if yes_tid not in self._token_to_market:
                        info = {
                            "condition_id": m.condition_id,
                            "question": m.question,
                            "yes_token": yes_tid,
                            "no_token": no_tid,
                            "market_obj": m,
                        }
                        self._token_to_market[yes_tid] = info
                        self._token_to_market[no_tid] = info
                        self._market_tokens[m.condition_id] = (yes_tid, no_tid)
                        new_tokens.extend([yes_tid, no_tid])

                if new_tokens and self._ws:
                    self._ws.subscribe(new_tokens)
                    print(
                        f"  [REFRESH] Found {len(short)} short-dur BTC markets, "
                        f"subscribed to {len(new_tokens)} new tokens "
                        f"(total tracked: {len(self._token_to_market) // 2})"
                    )

                # Prune expired markets from tracking
                self._prune_expired()

            except Exception as e:
                logger.warning("Market refresh error: %s", e)

            await asyncio.sleep(self.market_refresh_interval)

    async def _check_settlements(self):
        """Periodically check if arbed markets have settled."""
        while self._running:
            await asyncio.sleep(self.settlement_check_interval)

            settled_ids = []
            now = datetime.now(tz=timezone.utc)

            for condition_id, opp in list(self._active_arbs.items()):
                try:
                    market = self.client.get_market(condition_id)
                except PolymarketAPIError:
                    continue

                if not market.closed or not market.outcome:
                    continue

                yes_won = market.outcome.lower() == "yes"
                yes_token = market.yes_token_id
                no_token = market.no_token_id

                yes_cost = opp.yes_ask * opp.max_pairs
                no_cost = opp.no_ask * opp.max_pairs
                total_cost = yes_cost + no_cost
                payout = opp.max_pairs * 1.0
                profit = payout - total_cost

                if yes_token and yes_token in self.portfolio.positions:
                    self.portfolio.settle(yes_token, winning=yes_won, timestamp=now)
                if no_token and no_token in self.portfolio.positions:
                    self.portfolio.settle(no_token, winning=not yes_won, timestamp=now)

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

                q = opp.market.question[:45]
                print(
                    f"  [SETTLED] {q} -> {market.outcome} | "
                    f"profit=${profit:.2f} (edge={opp.edge_cents:.2f}c x {opp.max_pairs:.0f} pairs)"
                )

            for cid in settled_ids:
                del self._active_arbs[cid]
                self._arbed_condition_ids.discard(cid)

            # Save portfolio
            if self.save_path:
                try:
                    self.portfolio.save(self.save_path)
                except Exception:
                    pass

    async def _status_printer(self):
        """Print periodic status updates."""
        while self._running:
            await asyncio.sleep(10)
            now = datetime.now(tz=timezone.utc)
            elapsed = (now - self._start_time).total_seconds() if self._start_time else 0
            pv = self.portfolio.total_value
            pnl = self.portfolio.total_pnl
            checks_sec = self._total_checks / elapsed if elapsed > 0 else 0
            tracked = len(self._token_to_market) // 2
            print(
                f"  [{now.strftime('%H:%M:%S')}] "
                f"Book updates: {self._total_checks} ({checks_sec:.1f}/s) | "
                f"Markets: {tracked} | "
                f"Opps: {self._total_opps_found} | "
                f"Arbs: {self._total_arbs_executed} | "
                f"Active: {len(self._active_arbs)} | "
                f"P&L: ${pnl:+,.2f} | "
                f"Value: ${pv:,.2f}"
            )

    def _prune_expired(self):
        """Remove tokens for markets that have expired from tracking."""
        expired_conditions = []
        for cid, (yes_tid, no_tid) in self._market_tokens.items():
            info = self._token_to_market.get(yes_tid)
            if not info:
                continue
            market = info["market_obj"]
            mins = minutes_until_close(market)
            if mins is not None and mins <= 0 and cid not in self._active_arbs:
                expired_conditions.append(cid)

        for cid in expired_conditions:
            yes_tid, no_tid = self._market_tokens.pop(cid)
            self._token_to_market.pop(yes_tid, None)
            self._token_to_market.pop(no_tid, None)
            if self._ws:
                self._ws.unsubscribe([yes_tid, no_tid])

    async def run(self):
        """Start the real-time arb scanner. Blocks until stopped."""
        self._running = True
        self._start_time = datetime.now(tz=timezone.utc)

        print(f"\n{'=' * 70}")
        print(f"  REAL-TIME BINARY ARBITRAGE SCANNER (WebSocket)")
        print(f"{'=' * 70}")
        print(f"  Mode:           WebSocket (sub-second reaction)")
        print(f"  Balance:        ${self.portfolio.starting_balance:,.2f}")
        print(f"  Min Edge:       {self.min_edge_cents:.1f} cents")
        print(f"  Position Size:  ${self.position_size:,.2f}")
        print(f"  Max Expiry:     {self.max_minutes_to_expiry} min")
        print(f"  Market Refresh: every {self.market_refresh_interval}s")
        print(f"  Settle Check:   every {self.settlement_check_interval}s")
        print(f"{'=' * 70}\n")

        self._ws = PolymarketWebSocket(on_book_update=self._on_book_update)

        try:
            await asyncio.gather(
                self._ws.connect(),           # WebSocket message loop
                self._refresh_markets(),       # Periodic market discovery
                self._check_settlements(),     # Periodic settlement checks
                self._status_printer(),        # Periodic status line
            )
        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            if self._ws:
                await self._ws.disconnect()
            print(self.summary())

    def run_sync(self, max_duration: Optional[int] = None):
        """Synchronous entry point. Runs the async scanner.

        Args:
            max_duration: Stop after this many seconds (None = run forever).
        """
        async def _run_with_timeout():
            if max_duration:
                try:
                    await asyncio.wait_for(self.run(), timeout=max_duration)
                except asyncio.TimeoutError:
                    self._running = False
                    if self._ws:
                        await self._ws.disconnect()
                    print(self.summary())
            else:
                await self.run()

        try:
            asyncio.run(_run_with_timeout())
        except KeyboardInterrupt:
            print("\nStopping...")

    def summary(self) -> str:
        """Full summary of scanner activity."""
        elapsed = 0
        if self._start_time:
            elapsed = (datetime.now(tz=timezone.utc) - self._start_time).total_seconds()

        metrics = calculate_metrics(self.portfolio)
        lines = [
            "",
            "=" * 70,
            "  REAL-TIME ARBITRAGE SCANNER SUMMARY",
            "=" * 70,
            f"  Runtime:              {elapsed:.0f}s ({elapsed/60:.1f} min)",
            f"  Book updates checked: {self._total_checks}",
            f"  Check rate:           {self._total_checks/elapsed:.1f}/s" if elapsed > 0 else "  Check rate:           N/A",
            f"  Opportunities found:  {self._total_opps_found}",
            f"  Arbs executed:        {self._total_arbs_executed}",
            f"  Arbs settled:         {len(self._settled_arbs)}",
            f"  Active arbs:          {len(self._active_arbs)}",
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
            total_profit = sum(a["profit"] for a in self._settled_arbs)
            lines.append(f"  {'Total settled profit:':<40} {'':>8} {'':>8} ${total_profit:>7.2f}")

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
