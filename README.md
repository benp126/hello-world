# Polymarket Strategy Backtester & Paper Trader

A system for backtesting and paper trading strategies on [Polymarket](https://polymarket.com) prediction markets. Uses the public Polymarket API (no API key needed for read-only data).

## Setup

```bash
pip install -r requirements.txt
```

## Quick Start

### CLI Usage

```bash
# Search for markets
python -m polymarket_trader.cli search "presidential election"

# View market details
python -m polymarket_trader.cli market <condition_id>

# View price history
python -m polymarket_trader.cli history <condition_id> --interval max

# List available strategies
python -m polymarket_trader.cli strategies

# Backtest on a single market
python -m polymarket_trader.cli backtest \
    --condition-id <condition_id> \
    --strategy threshold \
    --param buy_below=0.30 \
    --param sell_above=0.70 \
    --balance 1000

# Backtest across top markets
python -m polymarket_trader.cli backtest \
    --strategy momentum \
    --num-markets 20 \
    --balance 5000 \
    -v

# Paper trade with live data
python -m polymarket_trader.cli paper-trade \
    --strategy mean_reversion \
    --balance 1000 \
    --condition-ids <id1> <id2> \
    --poll-interval 120
```

### Python API

```python
from polymarket_trader.api.client import PolymarketClient
from polymarket_trader.engine.backtester import Backtester
from polymarket_trader.strategies.threshold import ThresholdStrategy

client = PolymarketClient()

# Search markets
markets = client.search_markets("election", limit=10)
market = markets[0]

# Get price history
history = client.get_price_history(market.yes_token_id, interval="max", fidelity=100)

# Backtest
strategy = ThresholdStrategy(buy_below=0.30, sell_above=0.70)
backtester = Backtester(starting_balance=1000.0, verbose=True)
result = backtester.run_single_market(strategy, market, history)
print(result.summary())
```

## Built-in Strategies

| Strategy | Description | Key Parameters |
|----------|-------------|----------------|
| `threshold` | Buy when price drops below X, sell above Y | `buy_below`, `sell_above`, `position_size` |
| `momentum` | Follow trends via moving average crossover | `short_window`, `long_window`, `min_trend_strength` |
| `mean_reversion` | Bet on prices reverting to mean (z-score) | `window`, `entry_z`, `exit_z` |
| `diversified` | Spread bets across many underpriced markets | `max_price`, `min_price`, `max_positions`, `per_position_size` |

## Creating Custom Strategies

Subclass `Strategy` and implement `evaluate()`:

```python
from polymarket_trader.strategies.base import Strategy, Signal

class MyStrategy(Strategy):
    def evaluate(self, market, price_history, portfolio, current_time):
        signals = []
        # Your logic here - return Signal objects for BUY/SELL/HOLD
        if market.yes_price < 0.20:
            signals.append(Signal(
                action="BUY",
                token_id=market.yes_token_id,
                market_condition_id=market.condition_id,
                market_question=market.question,
                outcome="Yes",
                target_shares=100,
                reason="Cheap YES token",
            ))
        return signals
```

See `examples/custom_strategy.py` for a complete example.

## Project Structure

```
polymarket_trader/
├── api/
│   ├── client.py          # Polymarket API client (Gamma + CLOB)
│   └── models.py          # Data models (Market, Token, PricePoint, etc.)
├── strategies/
│   ├── base.py            # Strategy abstract base class + Signal
│   ├── threshold.py       # Threshold strategy
│   ├── momentum.py        # Moving average momentum
│   ├── mean_reversion.py  # Z-score mean reversion
│   └── multi_market.py    # Diversified value strategy
├── engine/
│   ├── backtester.py      # Historical backtesting engine
│   ├── paper_trader.py    # Live paper trading engine
│   └── portfolio.py       # Portfolio/position tracking + persistence
├── utils/
│   └── metrics.py         # Performance metrics (Sharpe, drawdown, etc.)
├── cli.py                 # CLI entry point
└── config.py              # Configuration constants
examples/
├── backtest_single_market.py
├── backtest_multi_market.py
├── paper_trade_live.py
└── custom_strategy.py
```

## Performance Metrics

The system tracks:
- Total P&L and return %
- Max drawdown
- Sharpe ratio (annualized)
- Win rate
- Trade volume and fees
- Portfolio snapshots over time

## API Notes

- Uses Polymarket's public **Gamma API** for market discovery and **CLOB API** for prices/order books
- No authentication required for read-only operations
- Rate limits: ~60 requests/minute (the client includes retry logic)
- All paper trades are simulated locally - no real money is involved
