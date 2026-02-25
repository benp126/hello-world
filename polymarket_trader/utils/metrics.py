"""Performance metrics for strategy evaluation."""

import math
from typing import Optional

from polymarket_trader.engine.portfolio import Portfolio, PortfolioSnapshot


def calculate_metrics(portfolio: Portfolio) -> dict:
    """Calculate comprehensive performance metrics for a portfolio.

    Returns a dict with keys like total_return, sharpe_ratio, max_drawdown, etc.
    """
    snapshots = portfolio.snapshots
    metrics = {
        "starting_balance": portfolio.starting_balance,
        "ending_value": portfolio.total_value,
        "cash": portfolio.cash,
        "num_open_positions": len(portfolio.positions),
        "total_trades": len(portfolio.trade_history),
        "total_pnl": portfolio.total_pnl,
        "total_return_pct": portfolio.total_pnl_pct,
    }

    # Trade-level stats
    buys = [t for t in portfolio.trade_history if t.side == "BUY"]
    sells = [t for t in portfolio.trade_history if t.side == "SELL"]
    settles = [t for t in portfolio.trade_history if t.side == "SETTLE"]
    metrics["num_buys"] = len(buys)
    metrics["num_sells"] = len(sells)
    metrics["num_settlements"] = len(settles)
    metrics["total_volume"] = sum(t.cost for t in portfolio.trade_history if t.side in ("BUY", "SELL"))
    metrics["total_fees"] = sum(t.fee for t in portfolio.trade_history)

    if len(snapshots) < 2:
        metrics["max_drawdown_pct"] = 0.0
        metrics["sharpe_ratio"] = None
        metrics["volatility"] = None
        metrics["win_rate"] = None
        return metrics

    # Returns series from snapshots
    values = [s.total_value for s in snapshots]
    returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            returns.append((values[i] - values[i - 1]) / values[i - 1])

    # Max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values[1:]:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    metrics["max_drawdown_pct"] = max_dd * 100

    # Volatility & Sharpe (annualized assuming daily snapshots)
    if returns:
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_return = math.sqrt(variance)
        metrics["volatility"] = std_return * math.sqrt(365)  # annualized
        if std_return > 0:
            metrics["sharpe_ratio"] = (mean_return / std_return) * math.sqrt(365)
        else:
            metrics["sharpe_ratio"] = None
    else:
        metrics["volatility"] = 0.0
        metrics["sharpe_ratio"] = None

    # Win rate from completed round-trip trades (sell/settle after buy)
    wins, losses = _compute_win_loss(portfolio)
    total_closed = wins + losses
    if total_closed > 0:
        metrics["win_rate"] = (wins / total_closed) * 100
        metrics["wins"] = wins
        metrics["losses"] = losses
    else:
        metrics["win_rate"] = None

    return metrics


def _compute_win_loss(portfolio: Portfolio) -> tuple[int, int]:
    """Count winning and losing closed trades.

    A winning trade is a sell or settlement where proceeds > cost basis.
    """
    # Track cost basis per token from buys
    cost_basis: dict[str, list[float]] = {}  # token_id -> list of entry prices

    wins = 0
    losses = 0

    for trade in portfolio.trade_history:
        if trade.side == "BUY":
            cost_basis.setdefault(trade.token_id, [])
            for _ in range(int(trade.shares)):
                cost_basis[trade.token_id].append(trade.price)
            remainder = trade.shares - int(trade.shares)
            if remainder > 0.001:
                cost_basis[trade.token_id].append(trade.price)

        elif trade.side in ("SELL", "SETTLE"):
            entries = cost_basis.get(trade.token_id, [])
            if entries:
                avg_entry = sum(entries) / len(entries)
                if trade.price > avg_entry:
                    wins += 1
                else:
                    losses += 1
                # Remove used entries
                shares_to_remove = trade.shares
                while shares_to_remove > 0 and entries:
                    entries.pop(0)
                    shares_to_remove -= 1

    return wins, losses


def format_metrics(metrics: dict) -> str:
    """Format metrics dict into a readable string."""
    lines = [
        "=" * 55,
        "  PORTFOLIO PERFORMANCE SUMMARY",
        "=" * 55,
        f"  Starting Balance:   ${metrics['starting_balance']:>12,.2f}",
        f"  Ending Value:       ${metrics['ending_value']:>12,.2f}",
        f"  Cash Remaining:     ${metrics['cash']:>12,.2f}",
        f"  Total P&L:          ${metrics['total_pnl']:>+12,.2f} ({metrics['total_return_pct']:+.2f}%)",
        "-" * 55,
        f"  Total Trades:       {metrics['total_trades']:>12d}",
        f"    Buys:             {metrics['num_buys']:>12d}",
        f"    Sells:            {metrics['num_sells']:>12d}",
        f"    Settlements:      {metrics['num_settlements']:>12d}",
        f"  Total Volume:       ${metrics['total_volume']:>12,.2f}",
        f"  Total Fees:         ${metrics['total_fees']:>12,.2f}",
        f"  Open Positions:     {metrics['num_open_positions']:>12d}",
        "-" * 55,
        f"  Max Drawdown:       {metrics['max_drawdown_pct']:>11.2f}%",
    ]

    if metrics.get("sharpe_ratio") is not None:
        lines.append(f"  Sharpe Ratio:       {metrics['sharpe_ratio']:>12.3f}")
    else:
        lines.append(f"  Sharpe Ratio:              N/A")

    if metrics.get("volatility") is not None:
        lines.append(f"  Volatility (ann.):  {metrics['volatility']:>11.4f}")

    if metrics.get("win_rate") is not None:
        lines.append(f"  Win Rate:           {metrics['win_rate']:>11.1f}% ({metrics['wins']}W / {metrics['losses']}L)")
    else:
        lines.append(f"  Win Rate:                  N/A")

    lines.append("=" * 55)
    return "\n".join(lines)
