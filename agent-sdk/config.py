"""
Standalone Configuration for AI-Native Options Agent SDK

This config is self-contained and does NOT import from the parent system.
All settings are loaded from environment variables or defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment from parent directory
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

# API Keys
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

UW_API_KEY = os.getenv("UW_API_KEY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")

# Shadow Mode - when True, no actual trades are executed
SHADOW_MODE = os.getenv("AGENT_SDK_SHADOW_MODE", "false").lower() in ("true", "1", "yes")

# Options Trading Parameters - SWING TRADE STRATEGY (not scalping)
OPTIONS_CONFIG = {
    "max_options_positions": 4,
    "max_position_value": 2000,
    "position_size_pct": 0.02,            # 2% of portfolio per options trade
    "max_portfolio_risk_options": 0.10,   # Max 10% in options
    "default_contracts": 1,
    "max_contracts_per_trade": 5,         # Conservative limit
    "min_premium": 50,                    # Min $0.50 per contract
    "max_premium": 500,                   # Max $5.00 per contract (liquidity)
    "min_days_to_exp": 14,                # Min 14 DTE (avoid theta decay)
    "max_days_to_exp": 45,                # Max 45 DTE (sweet spot)
    "profit_target_pct": 0.50,            # 50% profit target - SIMPLE
    "stop_loss_pct": 0.50,                # 50% stop loss - SIMPLE

    # SWING TRADE - hold for days, not minutes
    "min_hold_days": 2,                   # Hold minimum 2 days
    "no_same_day_exit": True,             # Never exit same day as entry

    # ETF FILTER - Skip these (too much hedging noise)
    "excluded_etfs": ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "XLI", "GLD", "SLV", "TLT", "HYG", "EEM", "EFA", "VXX", "UVXY", "SQQQ", "TQQQ"],
}

# Safety Limits
OPTIONS_SAFETY = {
    "max_spread_pct": 15.0,               # Max 15% bid-ask spread
    "min_open_interest": 100,             # Minimum OI for liquidity
    "min_volume": 10,                     # Minimum daily volume
    "min_bid": 0.05,                      # Minimum bid price
    "min_bid_size": 10,                   # Minimum bid size
    "max_single_sector_pct": 50.0,        # Max 50% in one sector
    "max_single_underlying_pct": 30.0,    # Max 30% in one underlying
    "earnings_blackout_days": 2,          # Block trades 2 days before earnings
    "max_iv_rank_for_entry": 70,          # Don't buy when IV rank > 70%
}

# Flow Scanning Parameters - OPTIMIZED FOR SINGLE STOCKS
FLOW_CONFIG = {
    # API-level filters
    "min_premium": 100000,                # $100K minimum
    "min_vol_oi": 1.5,                    # Vol/OI > 1.5
    "all_opening": True,                  # Opening positions only (CRITICAL)
    "min_dte": 14,                        # Minimum DTE
    "max_dte": 45,                        # Maximum DTE
    "issue_types": ["Common Stock"],      # CRITICAL - filters OUT ETFs at API level
    "scan_limit": 30,                     # Raw alerts to fetch

    # Post-filter thresholds
    "min_score": 7,                       # Minimum conviction score (0-10 scale)
    "max_analyze": 10,                    # Max signals to analyze with Claude
    "min_conviction_execute": 85,         # Min conviction to auto-execute
    "min_conviction_alert": 70,           # Min conviction to alert

    # Quality checks
    "min_open_interest": 500,             # Minimum OI for liquidity
    "max_strike_distance_pct": 0.10,      # Max 10% from current price
}

# Excluded tickers - ETFs + meme/low quality stocks
EXCLUDED_TICKERS = {
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA",
    # Sector ETFs
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLU", "XLB", "XLC", "XLY", "XLP", "XLRE",
    # Commodities/Bonds
    "GLD", "SLV", "TLT", "HYG", "EEM", "EFA", "UNG",
    # Volatility products
    "VXX", "UVXY", "SVXY",
    # Leveraged ETFs
    "SQQQ", "TQQQ", "SPXU", "SPXL", "UPRO",
    # Meme/High manipulation risk
    "AMC", "GME", "BBBY", "MULN", "HYMC", "MMAT", "ATER", "DWAC",
    # Index options
    "SPXW", "SPX", "NDX", "XSP",
}

# Market Regime Thresholds
MARKET_REGIME = {
    "bullish_threshold": 0.02,            # SPY 5-day return > 2%
    "bearish_threshold": -0.02,           # SPY 5-day return < -2%
    "elevated_vix": 20,                   # VIX above this is elevated
    "high_vix": 25,                       # VIX above this is high
}

# =============================================================================
# RISK-BASED DECISION FRAMEWORK (replaces hard-coded limits)
# =============================================================================
# Claude decides based on risk capacity and conviction, not arbitrary counters.

RISK_FRAMEWORK = {
    # Portfolio Risk Limits
    "max_portfolio_delta_per_100k": 150,
    "max_portfolio_gamma_per_100k": 50,
    "max_portfolio_theta_daily_pct": 0.005,

    # Concentration Limits
    "max_sector_concentration": 0.40,
    "max_single_underlying_pct": 0.25,

    # Entry Risk Gates
    "min_conviction_for_entry": 80,
    "min_risk_capacity_pct": 0.20,
    "max_iv_rank_for_entry": 70,
    "exceptional_conviction_threshold": 90,

    # Exit Triggers
    "profit_target_pct": 0.50,
    "stop_loss_pct": 0.50,
    "conviction_exit_threshold": 50,
    "gamma_risk_dte_threshold": 5,
}

# Circuit breaker (keep for error handling, not trading limits)
CIRCUIT_BREAKER = {
    "max_consecutive_losses": 3,
    "max_daily_loss": -1000,
    "cooldown_minutes": 60,
}


def get_shadow_mode() -> bool:
    """Check if shadow mode is enabled."""
    return SHADOW_MODE


def validate_config() -> bool:
    """Validate that required config is present."""
    required = [
        ("ALPACA_API_KEY", ALPACA_API_KEY),
        ("ALPACA_SECRET_KEY", ALPACA_SECRET_KEY),
    ]

    missing = [name for name, value in required if not value]

    if missing:
        print(f"Missing required config: {', '.join(missing)}")
        return False

    return True
