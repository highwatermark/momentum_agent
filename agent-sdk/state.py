"""
State persistence for AI-Native Options Flow Trading System.

Manages trading_state.json for context preservation across cycles and sessions.
"""
import json
import os
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict
from pathlib import Path
import logging

import pytz

ET = pytz.timezone("America/New_York")
logger = logging.getLogger(__name__)

# Default state file location
STATE_FILE = Path("/home/ubuntu/momentum-agent/data/trading_state.json")


@dataclass
class PositionState:
    """Tracked position state."""
    contract_symbol: str
    underlying: str
    option_type: str
    strike: float
    expiration: str
    qty: int
    entry_price: float
    entry_time: str
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    last_updated: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "PositionState":
        return cls(**data)


@dataclass
class SignalState:
    """Tracked signal state."""
    signal_id: str
    symbol: str
    option_type: str
    strike: float
    expiration: str
    premium: float
    score: int
    timestamp: str
    action_taken: str = "none"  # none, traded, skipped, rejected
    rejection_reason: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "SignalState":
        return cls(**data)


@dataclass
class TradeState:
    """Tracked trade state."""
    trade_id: str
    signal_id: str
    contract_symbol: str
    underlying: str
    action: str  # entry, exit, roll
    qty: int
    price: float
    timestamp: str
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "TradeState":
        return cls(**data)


@dataclass
class TradingState:
    """
    Complete trading state persisted to trading_state.json.

    This state is:
    - Loaded at orchestrator startup
    - Injected into prompt each cycle
    - Updated after each action
    - Saved between cycles
    """
    # Session info
    session_id: str = ""
    session_start: str = ""
    last_updated: str = ""

    # Daily counters (reset at market open)
    trading_date: str = ""
    executions_today: int = 0
    signals_seen_today: int = 0

    # Active positions (synced from Alpaca)
    positions: List[Dict] = field(default_factory=list)

    # Signals seen today (for context)
    signals: List[Dict] = field(default_factory=list)

    # Trades executed today
    trades: List[Dict] = field(default_factory=list)

    # Portfolio summary
    portfolio: Dict = field(default_factory=lambda: {
        "total_value": 0.0,
        "options_exposure": 0.0,
        "cash_available": 0.0,
        "net_delta": 0.0,
        "daily_theta": 0.0,
        "risk_score": 0,
    })

    # Market context
    market: Dict = field(default_factory=lambda: {
        "spy_price": 0.0,
        "spy_change_pct": 0.0,
        "vix": 0.0,
        "market_hours": False,
    })

    # Circuit breaker state
    circuit_breaker: Dict = field(default_factory=lambda: {
        "open": False,
        "reason": "",
        "until": "",
        "consecutive_errors": 0,
        "consecutive_losses": 0,
    })

    # Decision log (last N decisions for context)
    recent_decisions: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "TradingState":
        return cls(
            session_id=data.get("session_id", ""),
            session_start=data.get("session_start", ""),
            last_updated=data.get("last_updated", ""),
            trading_date=data.get("trading_date", ""),
            executions_today=data.get("executions_today", 0),
            signals_seen_today=data.get("signals_seen_today", 0),
            positions=data.get("positions", []),
            signals=data.get("signals", []),
            trades=data.get("trades", []),
            portfolio=data.get("portfolio", {}),
            market=data.get("market", {}),
            circuit_breaker=data.get("circuit_breaker", {}),
            recent_decisions=data.get("recent_decisions", []),
        )

    def to_prompt_context(self) -> str:
        """Format state for injection into orchestrator prompt."""
        lines = []
        lines.append("=" * 60)
        lines.append("CURRENT TRADING STATE")
        lines.append("=" * 60)
        lines.append(f"Session: {self.session_id}")
        lines.append(f"Date: {self.trading_date}")
        lines.append(f"Last Updated: {self.last_updated}")
        lines.append("")

        # Execution status
        lines.append("EXECUTION STATUS:")
        lines.append(f"  Trades Today: {self.executions_today}/3")
        lines.append(f"  Signals Seen: {self.signals_seen_today}")
        remaining = max(0, 3 - self.executions_today)
        lines.append(f"  Executions Remaining: {remaining}")
        lines.append("")

        # Portfolio summary
        lines.append("PORTFOLIO:")
        lines.append(f"  Total Value: ${self.portfolio.get('total_value', 0):,.2f}")
        lines.append(f"  Options Exposure: ${self.portfolio.get('options_exposure', 0):,.2f}")
        lines.append(f"  Cash Available: ${self.portfolio.get('cash_available', 0):,.2f}")
        lines.append(f"  Net Delta: {self.portfolio.get('net_delta', 0):.1f}")
        lines.append(f"  Daily Theta: ${self.portfolio.get('daily_theta', 0):.2f}")
        lines.append(f"  Risk Score: {self.portfolio.get('risk_score', 0)}/100")
        lines.append("")

        # Market context
        lines.append("MARKET:")
        lines.append(f"  SPY: ${self.market.get('spy_price', 0):.2f} ({self.market.get('spy_change_pct', 0):+.2f}%)")
        lines.append(f"  VIX: {self.market.get('vix', 0):.2f}")
        lines.append(f"  Market Hours: {'YES' if self.market.get('market_hours') else 'NO'}")
        lines.append("")

        # Active positions
        lines.append(f"ACTIVE POSITIONS ({len(self.positions)}):")
        if self.positions:
            for pos in self.positions:
                pnl_pct = pos.get('unrealized_pnl_pct', 0)
                pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
                lines.append(f"  {pnl_emoji} {pos.get('underlying', '?')} {pos.get('option_type', '?').upper()} ${pos.get('strike', 0)} exp {pos.get('expiration', '?')}")
                lines.append(f"     Entry: ${pos.get('entry_price', 0):.2f} | Current: ${pos.get('current_price', 0):.2f} | P/L: {pnl_pct:+.1%}")
        else:
            lines.append("  No active positions")
        lines.append("")

        # Recent signals (last 5)
        lines.append(f"RECENT SIGNALS (last 5 of {len(self.signals)}):")
        for sig in self.signals[-5:]:
            action = sig.get('action_taken', 'none')
            action_emoji = {"traded": "✅", "skipped": "⏭️", "rejected": "❌"}.get(action, "⬜")
            lines.append(f"  {action_emoji} {sig.get('symbol', '?')} {sig.get('option_type', '?').upper()} ${sig.get('strike', 0)} | Score: {sig.get('score', 0)} | {action}")
        if not self.signals:
            lines.append("  No signals seen today")
        lines.append("")

        # Circuit breaker
        if self.circuit_breaker.get("open"):
            lines.append("⚠️ CIRCUIT BREAKER OPEN:")
            lines.append(f"  Reason: {self.circuit_breaker.get('reason', 'unknown')}")
            lines.append(f"  Until: {self.circuit_breaker.get('until', 'unknown')}")
            lines.append("")

        # Recent decisions (last 3)
        lines.append("RECENT DECISIONS (last 3):")
        for dec in self.recent_decisions[-3:]:
            lines.append(f"  [{dec.get('timestamp', '?')}] {dec.get('action', '?')}: {dec.get('summary', '?')}")
        if not self.recent_decisions:
            lines.append("  No decisions logged")

        lines.append("=" * 60)
        return "\n".join(lines)


class StateManager:
    """
    Manages trading state persistence.

    Usage:
        state_mgr = StateManager()
        state = state_mgr.load()

        # ... do work, modify state ...

        state_mgr.save(state)
    """

    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = state_file
        self._ensure_directory()

    def _ensure_directory(self):
        """Ensure state file directory exists."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def load(self, session_id: Optional[str] = None) -> TradingState:
        """
        Load state from file.

        Args:
            session_id: If provided, validates session matches or creates new

        Returns:
            TradingState object
        """
        if not self.state_file.exists():
            logger.info("No existing state file, creating new state")
            return self._create_new_state(session_id)

        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)

            state = TradingState.from_dict(data)

            # Check if we need to reset for new day
            today = datetime.now(ET).strftime("%Y-%m-%d")
            if state.trading_date != today:
                logger.info(f"New trading day, resetting daily counters (was {state.trading_date})")
                state = self._reset_for_new_day(state, session_id)

            # Update session if provided
            if session_id and state.session_id != session_id:
                logger.info(f"New session {session_id}, preserving state from {state.session_id}")
                state.session_id = session_id
                state.session_start = datetime.now(ET).isoformat()

            logger.info(f"Loaded state: session={state.session_id}, positions={len(state.positions)}, signals={len(state.signals)}")
            return state

        except Exception as e:
            logger.error(f"Error loading state: {e}, creating new state")
            return self._create_new_state(session_id)

    def save(self, state: TradingState) -> bool:
        """
        Save state to file.

        Args:
            state: TradingState to save

        Returns:
            True if successful
        """
        try:
            state.last_updated = datetime.now(ET).isoformat()

            with open(self.state_file, 'w') as f:
                json.dump(state.to_dict(), f, indent=2)

            logger.debug(f"Saved state: {len(state.positions)} positions, {len(state.signals)} signals")
            return True

        except Exception as e:
            logger.error(f"Error saving state: {e}")
            return False

    def _create_new_state(self, session_id: Optional[str] = None) -> TradingState:
        """Create a fresh state."""
        now = datetime.now(ET)
        return TradingState(
            session_id=session_id or f"session-{now.strftime('%Y%m%d-%H%M%S')}",
            session_start=now.isoformat(),
            last_updated=now.isoformat(),
            trading_date=now.strftime("%Y-%m-%d"),
        )

    def _reset_for_new_day(self, state: TradingState, session_id: Optional[str] = None) -> TradingState:
        """Reset daily counters while preserving positions."""
        now = datetime.now(ET)

        # Keep positions but reset daily counters
        state.trading_date = now.strftime("%Y-%m-%d")
        state.executions_today = 0
        state.signals_seen_today = 0
        state.signals = []  # Clear signals from previous day
        state.trades = []   # Clear trades from previous day
        state.recent_decisions = []  # Clear decisions

        # Update session
        if session_id:
            state.session_id = session_id
        state.session_start = now.isoformat()
        state.last_updated = now.isoformat()

        return state

    # =========================================================================
    # State update helpers
    # =========================================================================

    def add_signal(self, state: TradingState, signal: Dict) -> TradingState:
        """Add a signal to state."""
        state.signals.append(signal)
        state.signals_seen_today += 1
        # Keep only last 50 signals
        if len(state.signals) > 50:
            state.signals = state.signals[-50:]
        return state

    def add_trade(self, state: TradingState, trade: Dict) -> TradingState:
        """Add a trade to state."""
        state.trades.append(trade)
        if trade.get("action") == "entry":
            state.executions_today += 1
        return state

    def add_decision(self, state: TradingState, decision: Dict) -> TradingState:
        """Add a decision to state."""
        decision["timestamp"] = datetime.now(ET).strftime("%H:%M:%S")
        state.recent_decisions.append(decision)
        # Keep only last 20 decisions
        if len(state.recent_decisions) > 20:
            state.recent_decisions = state.recent_decisions[-20:]
        return state

    def update_positions(self, state: TradingState, positions: List[Dict]) -> TradingState:
        """Update positions from Alpaca."""
        state.positions = positions
        return state

    def update_portfolio(self, state: TradingState, portfolio: Dict) -> TradingState:
        """Update portfolio summary."""
        state.portfolio.update(portfolio)
        return state

    def update_market(self, state: TradingState, market: Dict) -> TradingState:
        """Update market context."""
        state.market.update(market)
        return state

    def open_circuit_breaker(self, state: TradingState, reason: str, duration_minutes: int = 60) -> TradingState:
        """Open circuit breaker."""
        until = datetime.now(ET) + timedelta(minutes=duration_minutes)
        state.circuit_breaker = {
            "open": True,
            "reason": reason,
            "until": until.isoformat(),
            "consecutive_errors": state.circuit_breaker.get("consecutive_errors", 0) + 1,
            "consecutive_losses": state.circuit_breaker.get("consecutive_losses", 0),
        }
        return state

    def close_circuit_breaker(self, state: TradingState) -> TradingState:
        """Close circuit breaker."""
        state.circuit_breaker["open"] = False
        state.circuit_breaker["reason"] = ""
        state.circuit_breaker["until"] = ""
        return state


# Import timedelta for circuit breaker
from datetime import timedelta


# Convenience functions
def load_state(session_id: Optional[str] = None) -> TradingState:
    """Load trading state."""
    return StateManager().load(session_id)


def save_state(state: TradingState) -> bool:
    """Save trading state."""
    return StateManager().save(state)


def get_state_context(session_id: Optional[str] = None) -> str:
    """Get formatted state for prompt injection."""
    state = load_state(session_id)
    return state.to_prompt_context()
