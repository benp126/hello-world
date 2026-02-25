"""Polymarket API client for fetching market data."""

import time
from datetime import datetime, timezone
from typing import Optional

import requests

from polymarket_trader.api.models import (
    Market,
    OrderBook,
    OrderBookLevel,
    PricePoint,
    Token,
    Trade,
)
from polymarket_trader.config import (
    CLOB_API_BASE,
    DEFAULT_LIMIT,
    GAMMA_API_BASE,
)


class PolymarketAPIError(Exception):
    """Raised when an API request fails."""


class PolymarketClient:
    """Client for reading data from Polymarket's public APIs.

    Uses two APIs:
    - Gamma API: market metadata, search, events
    - CLOB API: prices, order books, trade history
    """

    def __init__(self, gamma_base: str = GAMMA_API_BASE, clob_base: str = CLOB_API_BASE):
        self.gamma_base = gamma_base.rstrip("/")
        self.clob_base = clob_base.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, url: str, params: Optional[dict] = None) -> dict | list:
        """Make a GET request with basic retry logic."""
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    raise PolymarketAPIError(f"Request failed after 3 attempts: {e}") from e
                time.sleep(1 * (attempt + 1))

    # ── Gamma API (market metadata) ──────────────────────────────────

    def get_markets(
        self,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
        active: Optional[bool] = None,
        closed: Optional[bool] = None,
        order: str = "volume",
        ascending: bool = False,
    ) -> list[Market]:
        """Fetch a list of markets from the Gamma API."""
        params = {"limit": limit, "offset": offset, "order": order, "ascending": ascending}
        if active is not None:
            params["active"] = str(active).lower()
        if closed is not None:
            params["closed"] = str(closed).lower()

        data = self._get(f"{self.gamma_base}/markets", params=params)
        return [self._parse_gamma_market(m) for m in data]

    def get_market(self, condition_id: str) -> Market:
        """Fetch a single market by condition ID."""
        data = self._get(f"{self.gamma_base}/markets/{condition_id}")
        return self._parse_gamma_market(data)

    def search_markets(self, query: str, limit: int = DEFAULT_LIMIT) -> list[Market]:
        """Search markets by keyword."""
        params = {"limit": limit, "_q": query}
        data = self._get(f"{self.gamma_base}/markets", params=params)
        return [self._parse_gamma_market(m) for m in data]

    def _parse_gamma_market(self, raw: dict) -> Market:
        """Parse a raw Gamma API market response into a Market model."""
        import json as _json

        tokens = []
        clob_token_ids = raw.get("clobTokenIds") or []
        outcomes = raw.get("outcomes") or []
        outcome_prices = raw.get("outcomePrices") or []

        # Gamma API returns these fields as stringified JSON arrays - parse them
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = _json.loads(clob_token_ids)
            except (ValueError, TypeError):
                clob_token_ids = []
        if isinstance(outcomes, str):
            try:
                outcomes = _json.loads(outcomes)
            except (ValueError, TypeError):
                outcomes = []
        if isinstance(outcome_prices, str):
            try:
                outcome_prices = _json.loads(outcome_prices)
            except (ValueError, TypeError):
                outcome_prices = []

        for i, token_id in enumerate(clob_token_ids):
            outcome_name = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
            price = 0.0
            if i < len(outcome_prices):
                try:
                    price = float(outcome_prices[i])
                except (ValueError, TypeError):
                    pass
            tokens.append(Token(token_id=str(token_id), outcome=str(outcome_name), price=price))

        return Market(
            condition_id=str(raw.get("conditionId", raw.get("id", ""))),
            question=raw.get("question", ""),
            slug=raw.get("slug", ""),
            tokens=tokens,
            active=bool(raw.get("active", True)),
            closed=bool(raw.get("closed", False)),
            end_date=raw.get("endDate"),
            description=raw.get("description", ""),
            category=raw.get("category", ""),
            volume=float(raw.get("volume", 0) or 0),
            liquidity=float(raw.get("liquidity", 0) or 0),
            outcome=raw.get("outcome"),
        )

    # ── CLOB API (prices, order books, trades) ───────────────────────

    def get_midpoint(self, token_id: str) -> float:
        """Get current midpoint price for a token."""
        data = self._get(f"{self.clob_base}/midpoint", params={"token_id": token_id})
        return float(data.get("mid", 0))

    def get_price(self, token_id: str) -> float:
        """Get current best prices for a token. Returns average of best bid/ask."""
        data = self._get(f"{self.clob_base}/price", params={"token_id": token_id})
        # Response: { token_id: { "BUY": "0.65", "SELL": "0.64" } } or { "price": X }
        if isinstance(data, dict):
            if token_id in data:
                inner = data[token_id]
                buy = float(inner.get("BUY", 0))
                sell = float(inner.get("SELL", 0))
                if buy > 0 and sell > 0:
                    return (buy + sell) / 2
                return buy or sell
            if "price" in data:
                return float(data["price"])
            if "mid" in data:
                return float(data["mid"])
        return 0.0

    def get_prices(self, token_ids: list[str]) -> dict[str, float]:
        """Get current prices for multiple tokens. Returns midpoint per token."""
        result = {}
        data = self._get(
            f"{self.clob_base}/midpoints",
            params={"token_ids": ",".join(token_ids)},
        )
        if isinstance(data, dict):
            for tid, val in data.items():
                try:
                    if isinstance(val, dict):
                        result[tid] = float(val.get("mid", 0))
                    else:
                        result[tid] = float(val)
                except (ValueError, TypeError):
                    result[tid] = 0.0
        return result

    def get_order_book(self, token_id: str) -> OrderBook:
        """Get the order book for a token."""
        data = self._get(f"{self.clob_base}/book", params={"token_id": token_id})
        bids = [
            OrderBookLevel(price=float(b.get("price", 0)), size=float(b.get("size", 0)))
            for b in data.get("bids", [])
        ]
        asks = [
            OrderBookLevel(price=float(a.get("price", 0)), size=float(a.get("size", 0)))
            for a in data.get("asks", [])
        ]
        return OrderBook(token_id=token_id, bids=bids, asks=asks)

    def get_trades(
        self,
        token_id: Optional[str] = None,
        limit: int = DEFAULT_LIMIT,
        before: Optional[str] = None,
        after: Optional[str] = None,
    ) -> list[Trade]:
        """Fetch recent trades, optionally filtered by token."""
        params = {"limit": limit}
        if token_id:
            params["asset_id"] = token_id
        if before:
            params["before"] = before
        if after:
            params["after"] = after

        data = self._get(f"{self.clob_base}/trades", params=params)
        trades = []
        for t in data if isinstance(data, list) else data.get("data", data.get("trades", [])):
            try:
                ts = t.get("timestamp") or t.get("matchTime") or t.get("createdAt", "")
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                dt = datetime.now(tz=timezone.utc)
            trades.append(
                Trade(
                    trade_id=str(t.get("id", "")),
                    market=str(t.get("market", "")),
                    token_id=str(t.get("asset_id", t.get("tokenId", ""))),
                    side=str(t.get("side", "BUY")).upper(),
                    size=float(t.get("size", 0)),
                    price=float(t.get("price", 0)),
                    timestamp=dt,
                )
            )
        return trades

    def get_price_history(
        self,
        token_id: str,
        interval: str = "1d",
        fidelity: int = 60,
    ) -> list[PricePoint]:
        """Fetch price history for a token from the CLOB timeseries endpoint.

        Args:
            token_id: The CLOB token ID.
            interval: Time range - "1h", "6h", "1d", "1w", "max".
            fidelity: Number of data points to return (approx).
        """
        params = {"market": token_id, "interval": interval, "fidelity": fidelity}
        data = self._get(f"{self.clob_base}/prices-history", params=params)

        points = []
        history = data if isinstance(data, list) else data.get("history", [])
        for pt in history:
            try:
                t = pt.get("t", pt.get("timestamp", 0))
                p = pt.get("p", pt.get("price", 0))
                if isinstance(t, (int, float)):
                    dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
                points.append(PricePoint(timestamp=dt, price=float(p)))
            except (ValueError, TypeError):
                continue
        return sorted(points, key=lambda x: x.timestamp)

    def get_market_trades_history(
        self,
        condition_id: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[Trade]:
        """Get trade history for a specific market via Gamma API."""
        params = {"limit": limit, "market": condition_id}
        data = self._get(f"{self.gamma_base}/trades", params=params)
        trades = []
        items = data if isinstance(data, list) else data.get("data", [])
        for t in items:
            try:
                ts = t.get("timestamp") or t.get("createdAt", "")
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                dt = datetime.now(tz=timezone.utc)
            trades.append(
                Trade(
                    trade_id=str(t.get("id", "")),
                    market=condition_id,
                    token_id=str(t.get("asset_id", t.get("tokenId", ""))),
                    side=str(t.get("side", "BUY")).upper(),
                    size=float(t.get("size", 0)),
                    price=float(t.get("price", 0)),
                    timestamp=dt,
                )
            )
        return trades
