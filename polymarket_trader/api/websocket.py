"""WebSocket client for real-time Polymarket order book streaming.

Connects to Polymarket's CLOB WebSocket at:
    wss://ws-subscriptions-clob.polymarket.com/ws/market

Subscribes to order book updates for specific token IDs and pushes
changes to a callback function in real-time (sub-second latency).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import websockets

from polymarket_trader.api.models import OrderBook, OrderBookLevel

logger = logging.getLogger(__name__)

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class BookState:
    """Maintains a local order book state updated by WebSocket deltas."""

    def __init__(self, token_id: str):
        self.token_id = token_id
        self.bids: dict[float, float] = {}  # price -> size
        self.asks: dict[float, float] = {}  # price -> size
        self.last_updated: Optional[datetime] = None

    def apply_snapshot(self, bids: list[dict], asks: list[dict]):
        """Replace the full book with a snapshot."""
        self.bids.clear()
        self.asks.clear()
        for b in bids:
            price = float(b.get("price", 0))
            size = float(b.get("size", 0))
            if size > 0:
                self.bids[price] = size
        for a in asks:
            price = float(a.get("price", 0))
            size = float(a.get("size", 0))
            if size > 0:
                self.asks[price] = size
        self.last_updated = datetime.now(tz=timezone.utc)

    def apply_delta(self, bids: list[dict], asks: list[dict]):
        """Apply incremental updates. Size=0 means remove the level."""
        for b in bids:
            price = float(b.get("price", 0))
            size = float(b.get("size", 0))
            if size > 0:
                self.bids[price] = size
            else:
                self.bids.pop(price, None)
        for a in asks:
            price = float(a.get("price", 0))
            size = float(a.get("size", 0))
            if size > 0:
                self.asks[price] = size
            else:
                self.asks.pop(price, None)
        self.last_updated = datetime.now(tz=timezone.utc)

    @property
    def best_bid(self) -> Optional[float]:
        return max(self.bids.keys()) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return min(self.asks.keys()) if self.asks else None

    @property
    def best_bid_size(self) -> float:
        if self.best_bid is not None:
            return self.bids[self.best_bid]
        return 0.0

    @property
    def best_ask_size(self) -> float:
        if self.best_ask is not None:
            return self.asks[self.best_ask]
        return 0.0

    def to_order_book(self) -> OrderBook:
        """Convert to an OrderBook model."""
        sorted_bids = sorted(self.bids.items(), key=lambda x: -x[0])
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])
        return OrderBook(
            token_id=self.token_id,
            bids=[OrderBookLevel(price=p, size=s) for p, s in sorted_bids],
            asks=[OrderBookLevel(price=p, size=s) for p, s in sorted_asks],
        )


# Type for the callback that fires on every book update
BookUpdateCallback = Callable[[str, BookState], None]


class PolymarketWebSocket:
    """Manages a WebSocket connection to Polymarket's CLOB market stream.

    Subscribes to order book updates for a set of token IDs and invokes
    a callback on every update with sub-second latency.

    Usage:
        ws = PolymarketWebSocket(on_book_update=my_callback)
        ws.subscribe(["token_id_1", "token_id_2"])
        await ws.connect()  # blocks, processing messages
    """

    def __init__(
        self,
        on_book_update: Optional[BookUpdateCallback] = None,
        url: str = WS_MARKET_URL,
    ):
        self.url = url
        self.on_book_update = on_book_update
        self._subscribed_assets: set[str] = set()
        self._books: dict[str, BookState] = {}  # token_id -> BookState
        self._ws = None
        self._running = False

    @property
    def books(self) -> dict[str, BookState]:
        return self._books

    def subscribe(self, token_ids: list[str]):
        """Add token IDs to subscribe to. Can be called before or during connection."""
        for tid in token_ids:
            self._subscribed_assets.add(tid)
            if tid not in self._books:
                self._books[tid] = BookState(tid)

    def unsubscribe(self, token_ids: list[str]):
        """Remove token IDs from subscription."""
        for tid in token_ids:
            self._subscribed_assets.discard(tid)

    async def connect(self):
        """Connect to the WebSocket and process messages until stopped."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    logger.info("WebSocket connected to %s", self.url)

                    # Send subscription for all assets
                    if self._subscribed_assets:
                        await self._send_subscribe(ws, list(self._subscribed_assets))

                    # Message loop
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            self._handle_message(raw_msg)
                        except Exception as e:
                            logger.warning("Error handling WS message: %s", e)

            except websockets.exceptions.ConnectionClosed as e:
                if self._running:
                    logger.warning("WebSocket disconnected (%s), reconnecting in 1s...", e)
                    await asyncio.sleep(1)
            except Exception as e:
                if self._running:
                    logger.warning("WebSocket error (%s), reconnecting in 2s...", e)
                    await asyncio.sleep(2)

        self._ws = None

    async def disconnect(self):
        """Stop the connection."""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _send_subscribe(self, ws, asset_ids: list[str]):
        """Send a subscription message to the WebSocket."""
        # Polymarket expects subscription messages in this format
        msg = {
            "type": "subscribe",
            "channel": "book",
            "assets_ids": asset_ids,
        }
        await ws.send(json.dumps(msg))
        logger.info("Subscribed to %d assets", len(asset_ids))

    def _handle_message(self, raw_msg: str):
        """Parse and process an incoming WebSocket message."""
        try:
            data = json.loads(raw_msg)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type", data.get("event_type", ""))
        asset_id = data.get("asset_id", data.get("market", ""))

        # Handle different message types
        if msg_type in ("book", "book_snapshot"):
            self._handle_book_update(asset_id, data, is_snapshot=True)
        elif msg_type in ("book_delta", "book_update"):
            self._handle_book_update(asset_id, data, is_snapshot=False)
        elif msg_type == "price_change":
            # price_change events also carry book info sometimes
            if "bids" in data or "asks" in data:
                self._handle_book_update(asset_id, data, is_snapshot=False)
        # Silently ignore other message types (heartbeats, etc.)

    def _handle_book_update(self, asset_id: str, data: dict, is_snapshot: bool):
        """Process a book snapshot or delta update."""
        if not asset_id:
            # Some messages nest the asset_id inside a 'market' field
            asset_id = data.get("market", "")
        if not asset_id:
            return

        if asset_id not in self._books:
            self._books[asset_id] = BookState(asset_id)

        book = self._books[asset_id]
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        if is_snapshot:
            book.apply_snapshot(bids, asks)
        else:
            book.apply_delta(bids, asks)

        # Fire callback
        if self.on_book_update:
            try:
                self.on_book_update(asset_id, book)
            except Exception as e:
                logger.warning("Book update callback error: %s", e)
