"""
Configuration for AI-Native Options Flow Trading System

This module contains all configuration for the Claude Agent SDK implementation.
"""
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    name: str
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.7
    tools: List[str] = field(default_factory=list)


@dataclass
class OrchestratorConfig:
    """Configuration for the main orchestrator agent."""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192
    temperature: float = 0.5

    # Subagent configurations
    flow_scanner: AgentConfig = field(default_factory=lambda: AgentConfig(
        name="flow_scanner",
        tools=["uw_flow_scan", "stock_quote", "earnings_check", "iv_rank"]
    ))
    position_manager: AgentConfig = field(default_factory=lambda: AgentConfig(
        name="position_manager",
        tools=["get_positions", "get_quote", "calculate_dte", "estimate_greeks"]
    ))
    risk_manager: AgentConfig = field(default_factory=lambda: AgentConfig(
        name="risk_manager",
        tools=["portfolio_greeks", "sector_concentration", "account_info"]
    ))
    executor: AgentConfig = field(default_factory=lambda: AgentConfig(
        name="executor",
        model="claude-sonnet-4-20250514",  # Use capable model for execution
        tools=["find_contract", "check_liquidity", "place_order", "close_position", "execute_roll"]
    ))


@dataclass
class TradingConfig:
    """Trading rules and limits."""
    # Daily limits
    max_executions_per_day: int = 3
    max_position_size_dollars: float = 2000.0
    max_total_options_exposure: float = 8000.0
    max_positions: int = 4

    # Safety gates
    max_spread_pct: float = 0.15  # 15% max bid-ask spread
    min_volume: int = 100
    min_open_interest: int = 500
    min_bid_price: float = 0.10

    # Earnings blackout
    earnings_blackout_days: int = 2

    # Risk thresholds
    max_portfolio_risk_score: int = 50
    max_sector_concentration_pct: float = 0.50

    # Greeks limits
    max_portfolio_delta_per_100k: float = 150.0
    max_daily_theta_pct: float = 0.003
    max_vega_exposure_pct: float = 0.005

    # Profit/Loss targets
    base_profit_target_pct: float = 0.40
    base_stop_loss_pct: float = 0.50
    profit_targets_by_dte: Dict[int, float] = field(default_factory=lambda: {
        14: 0.50,  # DTE > 14: 50% profit target
        7: 0.40,   # DTE 7-14: 40%
        3: 0.30,   # DTE 3-7: 30%
        0: 0.20,   # DTE < 3: 20%
    })


@dataclass
class FlowScanConfig:
    """Configuration for flow scanning."""
    # Scan timing
    adaptive_scan_min_interval: int = 30  # Minimum seconds between scans
    adaptive_scan_max_interval: int = 180  # Maximum seconds between scans

    # Signal filtering
    min_premium: float = 50000.0  # $50K minimum premium
    min_score: int = 40  # Minimum signal score to consider

    # Conviction thresholds
    high_conviction_score: int = 70
    medium_conviction_score: int = 50

    # Volume requirements
    min_volume_oi_ratio: float = 0.5

    # Time filters
    exclude_weekly_on_thursday: bool = True
    exclude_0dte: bool = True
    min_dte: int = 7
    max_dte: int = 45


@dataclass
class MonitorConfig:
    """Configuration for position monitoring."""
    poll_interval_seconds: int = 45
    greeks_snapshot_interval_seconds: int = 300

    # AI evaluation triggers
    ai_trigger_loss_pct: float = 0.15
    ai_trigger_profit_pct: float = 0.30
    ai_trigger_dte: int = 7
    ai_review_cooldown_minutes: int = 10

    # Greeks triggers
    gamma_risk_threshold: float = 0.08
    iv_crush_threshold_pct: float = 20.0

    # Auto-exit
    enable_auto_exit: bool = True
    max_auto_exits_per_day: int = 5


@dataclass
class SessionConfig:
    """Configuration for session management."""
    # Persistence
    session_db_path: str = "data/agent_sessions.db"
    max_session_turns: int = 1000

    # Context management
    max_context_tokens: int = 150000
    compaction_threshold_tokens: int = 100000

    # Memory
    signal_history_window_hours: int = 24
    trade_history_window_days: int = 7

    # Resume/Fork
    enable_session_resume: bool = True
    enable_session_fork: bool = True


@dataclass
class Config:
    """Main configuration container."""
    # API Keys (from environment)
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    alpaca_api_key: str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY", ""))
    alpaca_secret_key: str = field(default_factory=lambda: os.getenv("ALPACA_SECRET_KEY", ""))
    uw_api_key: str = field(default_factory=lambda: os.getenv("UW_API_KEY", ""))
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Sub-configurations
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    flow_scan: FlowScanConfig = field(default_factory=FlowScanConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    session: SessionConfig = field(default_factory=SessionConfig)

    # Execution mode
    shadow_mode: bool = False  # If True, log decisions without executing
    paper_trading: bool = True  # If True, use paper trading API

    # Logging
    log_level: str = "INFO"
    log_dir: str = "logs"

    # Market hours (ET)
    market_open_hour: int = 9
    market_open_minute: int = 30
    market_close_hour: int = 16
    market_close_minute: int = 0

    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []

        if not self.anthropic_api_key:
            errors.append("ANTHROPIC_API_KEY not set")
        if not self.alpaca_api_key:
            errors.append("ALPACA_API_KEY not set")
        if not self.alpaca_secret_key:
            errors.append("ALPACA_SECRET_KEY not set")
        if not self.uw_api_key:
            errors.append("UW_API_KEY not set")
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN not set (optional but recommended)")

        return errors


# Global config instance
config = Config()
