"""Configuration for Polymarket trading system."""

# Polymarket API endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"

# Default pagination
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

# Default paper trading settings
DEFAULT_STARTING_BALANCE = 1000.0  # USDC
DEFAULT_FEE_RATE = 0.0  # Polymarket has no trading fees currently

# Price history resolution options
RESOLUTION_1H = "1h"
RESOLUTION_6H = "6h"
RESOLUTION_1D = "1d"
