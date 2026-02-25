"""Base strategy interface for Polymarket trading strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from polymarket_trader.api.models import Market, PricePoint
from polymarket_trader.engine.portfolio import Portfolio


@dataclass
class Signal:
    """A trading signal emitted by a strategy."""
    action: str  # "BUY", "SELL", "HOLD"
    token_id: str
    market_condition_id: str
    market_question: str
    outcome: str  # "Yes" or "No"
    target_shares: Optional[float] = None  # number of shares to trade
    target_allocation: Optional[float] = None  # fraction of portfolio (0-1)
    confidence: float = 1.0  # strategy's confidence level (0-1)
    reason: str = ""


class Strategy(ABC):
    """Abstract base class for trading strategies.

    Strategies receive market data and portfolio state, then produce
    trading signals (buy/sell/hold).
    """

    def __init__(self, name: str = ""):
        self.name = name or self.__class__.__name__

    @abstractmethod
    def evaluate(
        self,
        market: Market,
        price_history: list[PricePoint],
        portfolio: Portfolio,
        current_time: datetime,
    ) -> list[Signal]:
        """Evaluate a market and return trading signals.

        Args:
            market: Current market data with live prices.
            price_history: Historical price data for the market's tokens.
            portfolio: Current portfolio state.
            current_time: Current simulation time.

        Returns:
            List of Signal objects (can be empty for no action).
        """

    def on_start(self, portfolio: Portfolio):
        """Called when backtesting/trading starts. Override for setup."""
        pass

    def on_end(self, portfolio: Portfolio):
        """Called when backtesting/trading ends. Override for teardown."""
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
