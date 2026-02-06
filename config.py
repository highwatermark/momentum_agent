"""
Configuration for Momentum Trading Agent
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Runtime config file (persists changes made via bot)
RUNTIME_CONFIG_PATH = "data/runtime_config.json"

# Alpaca API (Paper Trading)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# Anthropic API (Claude)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Unusual Whales API
UW_API_KEY = os.getenv("UW_API_KEY")

# Trading Parameters
TRADING_CONFIG = {
    # Universe filters
    "min_price": 5.0,
    "min_avg_volume": 500_000,

    # Entry filters (defaults - can be overridden per cap)
    "min_volume_surge": 1.3,        # Today volume vs 20D avg
    "min_sma_alignment": True,       # 7 > 20 > 30
    "min_close_position": 0.6,       # Where price closes in daily range
    "min_roc_10d": 0.03,             # 3% min 10-day rate of change
    "min_gap_up": 0.01,              # 1% min gap up for breakout

    # Position sizing
    "max_positions": 6,              # Max total concurrent positions
    "position_size_pct": 0.10,       # 10% of portfolio per position
    "max_portfolio_risk": 0.60,      # Max 60% deployed

    # Exit rules
    "trailing_stop_pct": 0.05,       # 5% trailing stop
}

# Per-cap position limits and thresholds
CAP_CONFIG = {
    "large": {
        "max_positions": 2,          # Max positions for large cap
        "max_buys_per_scan": 2,      # Max buys per scan
        "min_volume_surge": 1.3,     # Volume threshold
        "min_gap_up": 0.01,          # 1% gap up
        "min_roc_10d": 0.03,         # 3% ROC
    },
    "mid": {
        "max_positions": 2,          # Max positions for mid cap
        "max_buys_per_scan": 2,      # Max buys per scan
        "min_volume_surge": 1.3,     # Volume threshold
        "min_gap_up": 0.01,          # 1% gap up
        "min_roc_10d": 0.03,         # 3% ROC
    },
    "small": {
        "max_positions": 2,          # Max positions for small cap
        "max_buys_per_scan": 2,      # Max buys per scan
        "min_volume_surge": 1.5,     # Higher volume for small caps
        "min_gap_up": 0.03,          # 3% gap up (higher for small caps)
        "min_roc_10d": 0.05,         # 5% ROC (higher momentum required)
    },
}


def get_cap_config(cap: str) -> dict:
    """Get configuration for a specific market cap category"""
    if cap and cap in CAP_CONFIG:
        return CAP_CONFIG[cap]
    # Return defaults if no cap specified
    return {
        "max_positions": TRADING_CONFIG["max_positions"],
        "max_buys_per_scan": 3,
        "min_volume_surge": TRADING_CONFIG["min_volume_surge"],
        "min_gap_up": TRADING_CONFIG["min_gap_up"],
        "min_roc_10d": TRADING_CONFIG["min_roc_10d"],
    }

# Schedule (ET timezone)
SCHEDULE = {
    "scan_day": "weekdays",
    "scan_time": "09:35",            # 5 min after open
    "position_check": "15:55",       # Before close
}

# Database
DB_PATH = "data/trades.db"

# Monitor Settings (defaults - can be overridden via bot)
MONITOR_CONFIG = {
    "auto_close_enabled": True,      # Auto-close on strong reversal
    "auto_close_threshold": 5,       # Reversal score to trigger auto-close (0-13)
    "alert_threshold": 3,            # Reversal score to send alert
    "skip_buys_when_healthy": True,  # Skip new buys when all positions healthy
    "healthy_threshold": 3,          # Reversal score below which position is "healthy"
    "min_positions_for_skip": 4,     # Minimum positions required before skip-buy mode activates
}


def get_runtime_config() -> dict:
    """Get runtime config, merging defaults with any saved overrides"""
    config = MONITOR_CONFIG.copy()

    config_path = Path(RUNTIME_CONFIG_PATH)
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                saved = json.load(f)
                config.update(saved)
        except Exception as e:
            print(f"Warning: Could not load runtime config: {e}")

    return config


def set_runtime_config(key: str, value) -> bool:
    """Set a runtime config value and persist to file"""
    config_path = Path(RUNTIME_CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config or start fresh
    config = {}
    if config_path.exists():
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except Exception:
            config = {}

    # Update and save
    config[key] = value
    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving runtime config: {e}")
        return False


def get_monitor_setting(key: str):
    """Get a specific monitor setting"""
    config = get_runtime_config()
    return config.get(key, MONITOR_CONFIG.get(key))


# Options Trading Parameters
OPTIONS_CONFIG = {
    "max_options_positions": 4,
    "max_position_value": 2000,
    "position_size_pct": 0.02,        # 2% of portfolio per options trade
    "max_portfolio_risk_options": 0.10,  # Max 10% in options
    "default_contracts": 1,
    "max_contracts_per_trade": 10,
    "min_premium": 50,                # Min $0.50 per contract
    "max_premium": 500,               # Max $5.00 per contract (reduced for liquidity)
    "min_days_to_exp": 14,            # Minimum 14 DTE (avoid fast theta decay)
    "max_days_to_exp": 45,            # Max 45 DTE (sweet spot)
    "profit_target_pct": 0.50,        # 50% profit target - SIMPLE
    "stop_loss_pct": 0.50,            # 50% stop loss - SIMPLE

    # SWING TRADE STRATEGY (not scalping)
    "min_hold_days": 2,               # Hold minimum 2 days
    "max_hold_days": 5,               # Target 2-5 day holds
    "no_same_day_exit": True,         # Never exit same day as entry

    # ETF FILTER - Skip these (too much hedging noise, low signal-to-noise)
    "excluded_etfs": ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "XLI", "GLD", "SLV", "TLT", "HYG", "EEM", "EFA", "VXX", "UVXY", "SQQQ", "TQQQ"],
}

# Flow Scanning Parameters - OPTIMIZED FOR SINGLE STOCKS
FLOW_CONFIG = {
    # API-level filters (applied at UW API call)
    "min_premium": 100000,            # $100K minimum flow premium
    "min_vol_oi": 1.5,                # Vol/OI > 1.5
    "all_opening": True,              # Only opening positions (CRITICAL - not closing/adjusting)
    "min_dte": 14,                    # Minimum DTE filter (avoid theta decay)
    "max_dte": 45,                    # Max DTE (avoid low gamma)
    "issue_types": ["Common Stock"],  # CRITICAL - filters OUT ETFs at API level
    "scan_limit": 30,                 # Raw alerts to fetch

    # Post-filter thresholds
    "min_score": 7,                   # Minimum conviction score (0-10 scale, only 7+)
    "max_analyze": 10,                # Max signals to analyze with Claude

    # Quality checks
    "min_open_interest": 500,         # Minimum OI for liquidity
    "max_strike_distance_pct": 0.10,  # Max 10% from current price
}

# Excluded tickers - ETFs + meme/low quality stocks (hedging noise, manipulation risk)
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
    # Low quality/penny territory risk
    "WISH", "PLTR",  # Note: PLTR may be removed once it stabilizes
}

# Flow Signal Scoring Weights
FLOW_SCORING = {
    "sweep": 3,                       # Intermarket sweep (urgency)
    "ask_side": 2,                    # Bought at ask (bullish conviction)
    "high_premium": 3,                # $100K+ premium
    "very_high_premium": 2,           # $250K+ premium (bonus)
    "high_vol_oi": 2,                 # Vol/OI > 1
    "very_high_vol_oi": 1,            # Vol/OI > 3 (bonus)
    "floor_trade": 2,                 # Floor trade (institutional)
    "otm": 1,                         # Out of the money
    "near_earnings": 1,               # Within 14 days of earnings
    "low_dte": 1,                     # < 30 DTE
    "opening_trade": 2,               # Opening position
}

# Options Safety Limits
OPTIONS_SAFETY = {
    # Liquidity
    "max_spread_pct": 15.0,           # Max 15% bid-ask spread
    "min_open_interest": 100,         # Minimum OI for liquidity
    "min_volume": 10,                 # Minimum daily volume
    "min_bid": 0.05,                  # Minimum bid price (avoid pennies)
    "min_bid_size": 10,               # Minimum bid size

    # Concentration
    "max_single_sector_pct": 50.0,    # Max 50% in one sector
    "max_single_underlying_pct": 30.0,  # Max 30% in one underlying

    # Time
    "earnings_blackout_days": 2,      # Block trades 2 days before earnings
    "roll_alert_dte": 7,              # Alert when DTE <= 7
    "critical_dte": 3,                # Critical when DTE <= 3

    # Execution
    "use_limit_orders": True,         # Use limit orders by default
    "limit_price_buffer_pct": 2.0,    # % above mid for buys
}

# Flow Listener Configuration (Real-time flow monitoring)
FLOW_LISTENER_CONFIG = {
    # Polling
    "poll_interval_seconds": 60,      # Poll every 60 seconds

    # Pre-filter thresholds (Layer 1 - before Claude)
    "min_premium": 150000,            # $150K minimum premium (was $100K)
    "max_signals_per_cycle": 10,      # Max signals to send to Claude
    # Index options always excluded (use EXCLUDED_TICKERS from config for full list)
    "excluded_index_options": ["SPXW", "SPX", "NDX", "XSP"],

    # Claude decision thresholds (Layer 2)
    "min_conviction_execute": 75,     # Auto-execute if conviction >= 75%
    "min_conviction_alert": 50,       # Send alert if conviction >= 50%

    # Market hours (ET timezone)
    "market_open_hour": 9,
    "market_open_minute": 30,
    "market_close_hour": 16,
    "market_close_minute": 0,

    # Safety limits (Layer 3 - hard limits override Claude)
    "max_executions_per_day": 3,      # Max auto-executions per day
    "max_delta_per_100k": 150,        # Max |delta| per $100K equity
    "max_theta_pct": 0.003,           # Max daily theta as % of portfolio (0.3%)
    "max_risk_score": 50,             # Max portfolio risk score (0-100)
    "max_sector_concentration": 0.50, # Max 50% in single sector

    # Operational controls
    "enable_auto_execute": True,      # Master switch for auto-execution
    "max_cycle_time_seconds": 55,     # Hard timeout per cycle

    # Circuit breaker
    "max_consecutive_errors": 5,      # Errors before circuit breaker opens
    "circuit_breaker_cooldown_seconds": 300,  # 5 min cooldown

    # Symbol context cache TTL (seconds)
    "cache_ttl_earnings": 86400,      # 24 hours
    "cache_ttl_iv_rank": 3600,        # 1 hour
}

# Options Monitor Configuration (Real-time position monitoring)
OPTIONS_MONITOR_CONFIG = {
    # Polling
    "poll_interval_seconds": 45,              # Check positions every 45 seconds
    "greeks_snapshot_interval_seconds": 300,  # Snapshot Greeks every 5 minutes

    # AI Review Schedule (ET timezone)
    "ai_review_hour": 10,             # Daily AI review at 10:00 AM ET
    "ai_review_minute": 0,

    # Market hours (same as flow listener)
    "market_open_hour": 9,
    "market_open_minute": 30,
    "market_close_hour": 16,
    "market_close_minute": 0,

    # Greeks-based exit triggers
    "gamma_risk_threshold": 0.08,     # High gamma warning threshold
    "gamma_critical_dte": 5,          # DTE when gamma becomes critical
    "iv_crush_threshold_pct": 20,     # IV drop % from entry to trigger alert
    "max_portfolio_delta_per_100k": 150,  # Max |delta| per $100K equity
    "max_daily_theta_pct": 0.003,     # Max daily theta as % of portfolio (0.3%)
    "max_vega_exposure_pct": 0.005,   # Max vega as % of portfolio (0.5%)

    # Adaptive profit targets by DTE (DTE threshold: profit target %)
    "profit_targets_by_dte": {
        14: 0.50,   # DTE > 14: 50% profit target
        7: 0.40,    # DTE 7-14: 40% profit target
        3: 0.30,    # DTE 3-7: 30% profit target
        0: 0.20,    # DTE < 3: 20% profit target
    },

    # Stop loss (adaptive by conviction)
    "base_stop_loss_pct": 0.50,           # Default 50% stop loss
    "high_conviction_stop_loss_pct": 0.60,  # 60% for high-conviction trades

    # Auto-exit controls
    "enable_auto_exit": True,         # Master switch for auto-exits
    "max_auto_exits_per_day": 5,      # Max auto-exits per day
    "require_liquidity_check": True,  # Check liquidity before exit
    "min_bid_for_exit": 0.05,         # Minimum bid to allow exit

    # Alert deduplication
    "alert_cooldown_minutes": 30,     # Don't repeat same alert within 30 min

    # Circuit breaker
    "max_consecutive_errors": 5,
    "circuit_breaker_cooldown_seconds": 300,

    # Theta acceleration warning
    "theta_acceleration_threshold": 0.05,  # Daily decay > 5% of premium

    # AI trigger thresholds (when to call Claude for evaluation)
    "ai_trigger_loss_pct": 0.35,          # Losing > 35% triggers AI review (was 0.15 - too aggressive)
    "ai_trigger_profit_pct": 0.30,        # Profit > 30% triggers AI review
    "ai_trigger_dte": 7,                  # DTE <= 7 triggers AI review
    "ai_review_cooldown_minutes": 10,     # Don't re-evaluate same trigger within 10 min

    # AI advisory mode (Claude recommends, but risk framework decides)
    "ai_advisory_only": True,             # AI recommendations feed into risk framework
}

# =============================================================================
# RISK-BASED DECISION FRAMEWORK
# =============================================================================
# Replaces hard-coded counters with dynamic risk assessment.
# Claude validates decisions based on portfolio state, conviction, and risk.

RISK_FRAMEWORK = {
    # -------------------------------------------------------------------------
    # PORTFOLIO RISK LIMITS (caps on total exposure)
    # -------------------------------------------------------------------------
    "max_portfolio_delta_per_100k": 150,      # Max net |delta| per $100K equity
    "max_portfolio_gamma_per_100k": 50,       # Max gamma concentration
    "max_portfolio_theta_daily_pct": 0.005,   # Max daily theta burn (0.5% of portfolio)
    "max_portfolio_vega_pct": 0.01,           # Max vega exposure (1% of portfolio)

    # -------------------------------------------------------------------------
    # CONCENTRATION LIMITS
    # -------------------------------------------------------------------------
    "max_sector_concentration": 0.40,         # Max 40% in one sector
    "max_single_underlying_pct": 0.25,        # Max 25% in one ticker
    "max_correlated_exposure": 0.50,          # Max 50% in correlated positions

    # -------------------------------------------------------------------------
    # ENTRY RISK GATES (must pass ALL to enter)
    # -------------------------------------------------------------------------
    "min_conviction_for_entry": 80,           # Signal conviction must be >= 80%
    "min_risk_capacity_pct": 0.20,            # Need 20% of risk budget available
    "max_iv_rank_for_entry": 70,              # Don't buy expensive premium (IV rank < 70)
    "require_trend_alignment": True,          # Position must align with market trend
    "min_dte_for_entry": 14,                  # Minimum 14 DTE for new positions
    "max_premium_per_contract": 500,          # Max $5.00 per contract (liquidity)

    # -------------------------------------------------------------------------
    # EXIT TRIGGERS (Claude evaluates these, any can trigger exit recommendation)
    # -------------------------------------------------------------------------
    # P&L based (hard stops)
    "profit_target_pct": 0.50,                # Take profit at +50%
    "stop_loss_pct": 0.50,                    # Stop loss at -50%

    # Thesis-based (Claude validates)
    "exit_on_thesis_invalidation": True,      # Exit if original thesis no longer valid
    "thesis_invalidation_triggers": [
        "trend_reversal",                     # Market trend flipped against position
        "conviction_drop",                    # Conviction dropped significantly
        "catalyst_passed",                    # Earnings/event has passed
        "sector_rotation",                    # Money flowing out of sector
    ],

    # Risk-based (dynamic)
    "exit_on_gamma_risk": True,               # Exit if gamma risk too high near expiry
    "gamma_risk_dte_threshold": 5,            # DTE below which gamma risk elevated
    "exit_on_concentration_breach": True,     # Exit if position causes concentration breach

    # Conviction-based
    "conviction_exit_threshold": 50,          # Exit if conviction drops below 50%
    "conviction_hold_threshold": 65,          # Hold if conviction between 50-65%

    # -------------------------------------------------------------------------
    # CLAUDE DECISION WEIGHTS (how much each factor matters)
    # -------------------------------------------------------------------------
    "decision_weights": {
        "conviction_score": 0.30,             # 30% weight on signal conviction
        "risk_capacity": 0.25,                # 25% weight on available risk budget
        "thesis_validity": 0.25,              # 25% weight on thesis still valid
        "technical_alignment": 0.20,          # 20% weight on technicals
    },

    # -------------------------------------------------------------------------
    # OVERRIDE CONDITIONS (bypass normal limits)
    # -------------------------------------------------------------------------
    "exceptional_conviction_threshold": 90,   # Allow override if conviction >= 90%
    "exceptional_risk_allowance": 1.25,       # Can use 125% of normal risk budget
}

# Risk scoring thresholds for portfolio health
RISK_SCORE_THRESHOLDS = {
    "healthy": 30,          # Risk score 0-30: healthy, can add positions
    "cautious": 50,         # Risk score 31-50: cautious, selective entries only
    "elevated": 70,         # Risk score 51-70: elevated, no new entries
    "critical": 100,        # Risk score 71+: critical, consider reducing
}

