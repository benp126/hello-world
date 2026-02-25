"""Portfolio and position tracking for paper trading."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Position:
    """A position in a specific outcome token."""
    token_id: str
    market_condition_id: str
    market_question: str
    outcome: str  # "Yes" or "No"
    shares: float  # number of shares held
    avg_entry_price: float  # average cost basis per share
    current_price: float = 0.0

    @property
    def cost_basis(self) -> float:
        return self.shares * self.avg_entry_price

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.cost_basis == 0:
            return 0.0
        return (self.unrealized_pnl / self.cost_basis) * 100


@dataclass
class PaperTrade:
    """Record of a simulated trade."""
    trade_id: str
    timestamp: datetime
    token_id: str
    market_condition_id: str
    market_question: str
    outcome: str
    side: str  # "BUY" or "SELL"
    shares: float
    price: float
    cost: float  # total cost (shares * price)
    fee: float = 0.0

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "timestamp": self.timestamp.isoformat(),
            "token_id": self.token_id,
            "market_condition_id": self.market_condition_id,
            "market_question": self.market_question,
            "outcome": self.outcome,
            "side": self.side,
            "shares": self.shares,
            "price": self.price,
            "cost": self.cost,
            "fee": self.fee,
        }


@dataclass
class PortfolioSnapshot:
    """A snapshot of portfolio state at a point in time."""
    timestamp: datetime
    cash: float
    positions_value: float
    total_value: float
    num_positions: int


class Portfolio:
    """Manages paper trading positions and cash balance."""

    def __init__(self, starting_balance: float = 1000.0, fee_rate: float = 0.0):
        self.starting_balance = starting_balance
        self.cash = starting_balance
        self.fee_rate = fee_rate
        self.positions: dict[str, Position] = {}  # token_id -> Position
        self.trade_history: list[PaperTrade] = []
        self.snapshots: list[PortfolioSnapshot] = []
        self._trade_counter = 0

    def buy(
        self,
        token_id: str,
        shares: float,
        price: float,
        market_condition_id: str = "",
        market_question: str = "",
        outcome: str = "",
        timestamp: Optional[datetime] = None,
    ) -> Optional[PaperTrade]:
        """Buy shares of an outcome token.

        Returns the PaperTrade if successful, None if insufficient funds.
        """
        cost = shares * price
        fee = cost * self.fee_rate
        total_cost = cost + fee

        if total_cost > self.cash:
            return None

        self.cash -= total_cost

        if token_id in self.positions:
            pos = self.positions[token_id]
            total_shares = pos.shares + shares
            pos.avg_entry_price = (pos.cost_basis + cost) / total_shares
            pos.shares = total_shares
        else:
            self.positions[token_id] = Position(
                token_id=token_id,
                market_condition_id=market_condition_id,
                market_question=market_question,
                outcome=outcome,
                shares=shares,
                avg_entry_price=price,
                current_price=price,
            )

        self._trade_counter += 1
        ts = timestamp or datetime.now(tz=timezone.utc)
        trade = PaperTrade(
            trade_id=f"PT-{self._trade_counter:06d}",
            timestamp=ts,
            token_id=token_id,
            market_condition_id=market_condition_id,
            market_question=market_question,
            outcome=outcome,
            side="BUY",
            shares=shares,
            price=price,
            cost=cost,
            fee=fee,
        )
        self.trade_history.append(trade)
        return trade

    def sell(
        self,
        token_id: str,
        shares: float,
        price: float,
        timestamp: Optional[datetime] = None,
    ) -> Optional[PaperTrade]:
        """Sell shares of an outcome token.

        Returns the PaperTrade if successful, None if insufficient shares.
        """
        if token_id not in self.positions:
            return None

        pos = self.positions[token_id]
        if shares > pos.shares:
            return None

        proceeds = shares * price
        fee = proceeds * self.fee_rate
        net_proceeds = proceeds - fee

        self.cash += net_proceeds
        pos.shares -= shares

        if pos.shares < 1e-9:
            del self.positions[token_id]

        self._trade_counter += 1
        ts = timestamp or datetime.now(tz=timezone.utc)
        trade = PaperTrade(
            trade_id=f"PT-{self._trade_counter:06d}",
            timestamp=ts,
            token_id=token_id,
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            outcome=pos.outcome,
            side="SELL",
            shares=shares,
            price=price,
            cost=proceeds,
            fee=fee,
        )
        self.trade_history.append(trade)
        return trade

    def settle(self, token_id: str, winning: bool, timestamp: Optional[datetime] = None):
        """Settle a position when a market resolves.

        If winning=True, each share pays out $1. If False, shares are worth $0.
        """
        if token_id not in self.positions:
            return

        pos = self.positions[token_id]
        payout_price = 1.0 if winning else 0.0
        payout = pos.shares * payout_price
        self.cash += payout

        self._trade_counter += 1
        ts = timestamp or datetime.now(tz=timezone.utc)
        trade = PaperTrade(
            trade_id=f"PT-{self._trade_counter:06d}",
            timestamp=ts,
            token_id=token_id,
            market_condition_id=pos.market_condition_id,
            market_question=pos.market_question,
            outcome=pos.outcome,
            side="SETTLE",
            shares=pos.shares,
            price=payout_price,
            cost=payout,
        )
        self.trade_history.append(trade)
        del self.positions[token_id]

    def update_prices(self, prices: dict[str, float]):
        """Update current prices for held positions."""
        for token_id, price in prices.items():
            if token_id in self.positions:
                self.positions[token_id].current_price = price

    def take_snapshot(self, timestamp: Optional[datetime] = None):
        """Record a snapshot of current portfolio state."""
        ts = timestamp or datetime.now(tz=timezone.utc)
        positions_value = sum(p.market_value for p in self.positions.values())
        self.snapshots.append(
            PortfolioSnapshot(
                timestamp=ts,
                cash=self.cash,
                positions_value=positions_value,
                total_value=self.cash + positions_value,
                num_positions=len(self.positions),
            )
        )

    @property
    def total_value(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def total_pnl(self) -> float:
        return self.total_value - self.starting_balance

    @property
    def total_pnl_pct(self) -> float:
        if self.starting_balance == 0:
            return 0.0
        return (self.total_pnl / self.starting_balance) * 100

    @property
    def realized_pnl(self) -> float:
        """P&L from closed trades only (cash change minus still-open cost basis)."""
        open_cost = sum(p.cost_basis for p in self.positions.values())
        return (self.cash - self.starting_balance) + open_cost

    def save(self, filepath: str):
        """Save portfolio state to a JSON file."""
        data = {
            "starting_balance": self.starting_balance,
            "cash": self.cash,
            "fee_rate": self.fee_rate,
            "positions": {
                tid: {
                    "token_id": p.token_id,
                    "market_condition_id": p.market_condition_id,
                    "market_question": p.market_question,
                    "outcome": p.outcome,
                    "shares": p.shares,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                }
                for tid, p in self.positions.items()
            },
            "trade_history": [t.to_dict() for t in self.trade_history],
        }
        Path(filepath).write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, filepath: str) -> "Portfolio":
        """Load portfolio state from a JSON file."""
        data = json.loads(Path(filepath).read_text())
        port = cls(
            starting_balance=data["starting_balance"],
            fee_rate=data.get("fee_rate", 0.0),
        )
        port.cash = data["cash"]
        for tid, pdata in data.get("positions", {}).items():
            port.positions[tid] = Position(**pdata)
        for tdata in data.get("trade_history", []):
            tdata["timestamp"] = datetime.fromisoformat(tdata["timestamp"])
            port.trade_history.append(PaperTrade(**tdata))
            port._trade_counter += 1
        return port
