"""Data models for Polymarket API responses."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Token:
    """A binary outcome token (YES or NO) for a market."""
    token_id: str
    outcome: str  # "Yes" or "No"
    price: float = 0.0
    winner: Optional[bool] = None


@dataclass
class Market:
    """A Polymarket prediction market."""
    condition_id: str
    question: str
    slug: str
    tokens: list[Token] = field(default_factory=list)
    active: bool = True
    closed: bool = False
    end_date: Optional[str] = None
    description: str = ""
    category: str = ""
    volume: float = 0.0
    liquidity: float = 0.0
    outcome: Optional[str] = None  # resolved outcome

    @property
    def yes_price(self) -> float:
        for t in self.tokens:
            if t.outcome.lower() == "yes":
                return t.price
        return 0.0

    @property
    def no_price(self) -> float:
        for t in self.tokens:
            if t.outcome.lower() == "no":
                return t.price
        return 0.0

    @property
    def yes_token_id(self) -> Optional[str]:
        for t in self.tokens:
            if t.outcome.lower() == "yes":
                return t.token_id
        return None

    @property
    def no_token_id(self) -> Optional[str]:
        for t in self.tokens:
            if t.outcome.lower() == "no":
                return t.token_id
        return None


@dataclass
class PricePoint:
    """A single price observation at a point in time."""
    timestamp: datetime
    price: float


@dataclass
class Trade:
    """A trade that occurred on a market."""
    trade_id: str
    market: str
    token_id: str
    side: str  # "BUY" or "SELL"
    size: float
    price: float
    timestamp: datetime


@dataclass
class OrderBookLevel:
    """A single level in the order book."""
    price: float
    size: float


@dataclass
class OrderBook:
    """Order book for a token."""
    token_id: str
    bids: list[OrderBookLevel] = field(default_factory=list)
    asks: list[OrderBookLevel] = field(default_factory=list)

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return self.best_ask - self.best_bid
        return None
