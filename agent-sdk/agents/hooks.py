"""
Safety hooks for AI-Native Options Flow Trading System.

These hooks enforce safety constraints that CANNOT be bypassed by agents.
They run before and after tool execution to ensure compliance.

Key Features:
- Shadow mode enforcement (blocks real trades when enabled)
- Daily execution limits
- Circuit breaker on consecutive losses
- Position count limits
"""
import logging
import os
from datetime import datetime, date
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

import pytz
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

# Shadow mode from environment or config
SHADOW_MODE = os.getenv("AGENT_SDK_SHADOW_MODE", "false").lower() in ("true", "1", "yes")


@dataclass
class ExecutionState:
    """Tracks execution state for safety enforcement."""
    executions_today: int = 0
    last_execution_date: Optional[date] = None
    positions_count: int = 0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    circuit_breaker_open: bool = False
    circuit_breaker_until: Optional[datetime] = None

    def reset_daily(self):
        """Reset daily counters at ET midnight (not UTC!)."""
        et = pytz.timezone('America/New_York')
        today_et = datetime.now(et).date()
        if self.last_execution_date != today_et:
            self.executions_today = 0
            self.daily_pnl = 0.0
            self.last_execution_date = today_et

    def record_execution(self):
        """Record a successful execution."""
        self.reset_daily()
        self.executions_today += 1
        et = pytz.timezone('America/New_York')
        self.last_execution_date = datetime.now(et).date()

    def record_loss(self, amount: float):
        """Record a loss for circuit breaker logic."""
        self.daily_pnl -= abs(amount)
        self.consecutive_losses += 1
        if self.consecutive_losses >= 3 or self.daily_pnl <= -1000:
            self.open_circuit_breaker()

    def record_win(self, amount: float):
        """Record a win."""
        self.daily_pnl += abs(amount)
        self.consecutive_losses = 0

    def open_circuit_breaker(self, duration_minutes: int = 60):
        """Open circuit breaker to pause trading."""
        self.circuit_breaker_open = True
        self.circuit_breaker_until = datetime.now(ET) + timedelta(minutes=duration_minutes)
        logger.warning(f"Circuit breaker opened until {self.circuit_breaker_until}")

    def check_circuit_breaker(self) -> bool:
        """Check if circuit breaker allows trading."""
        if not self.circuit_breaker_open:
            return True
        if datetime.now(ET) >= self.circuit_breaker_until:
            self.circuit_breaker_open = False
            self.circuit_breaker_until = None
            logger.info("Circuit breaker closed, trading resumed")
            return True
        return False


# Global execution state
from datetime import timedelta
execution_state = ExecutionState()


@dataclass
class HookResult:
    """Result from a hook execution."""
    allowed: bool
    reason: Optional[str] = None
    modified_params: Optional[Dict[str, Any]] = None


class PreToolUseHook:
    """
    Hook that runs BEFORE tool execution.

    Enforces safety constraints that agents cannot bypass.
    Includes shadow mode enforcement to block real trades.
    """

    # Tools that require safety checks
    EXECUTION_TOOLS = {"place_order", "close_position", "execute_roll"}
    POSITION_TOOLS = {"get_positions", "portfolio_greeks"}

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.max_executions_per_day = config.get("max_executions_per_day", 3)
        self.max_positions = config.get("max_positions", 4)
        self.max_spread_pct = config.get("max_spread_pct", 0.15)
        self.earnings_blackout_days = config.get("earnings_blackout_days", 2)
        # Shadow mode can be set via config or environment
        self.shadow_mode = config.get("shadow_mode", SHADOW_MODE)

    def __call__(self, tool_name: str, tool_params: Dict[str, Any]) -> HookResult:
        """
        Called before each tool execution.

        Returns HookResult indicating if the tool call should proceed.
        """
        execution_state.reset_daily()

        # SHADOW MODE CHECK - blocks all execution tools
        if self.shadow_mode and tool_name in self.EXECUTION_TOOLS:
            logger.warning(f"[SHADOW MODE] Blocked {tool_name}: {tool_params}")
            return HookResult(
                allowed=False,
                reason=f"SHADOW MODE: {tool_name} blocked. Would have executed: {tool_params.get('symbol', 'N/A')}"
            )

        # Check circuit breaker for any trading tool
        if tool_name in self.EXECUTION_TOOLS:
            if not execution_state.check_circuit_breaker():
                return HookResult(
                    allowed=False,
                    reason=f"Circuit breaker open until {execution_state.circuit_breaker_until}"
                )

        # Specific checks by tool
        if tool_name == "place_order":
            return self._check_place_order(tool_params)
        elif tool_name == "close_position":
            return self._check_close_position(tool_params)
        elif tool_name == "execute_roll":
            return self._check_execute_roll(tool_params)

        # Allow all other tools
        return HookResult(allowed=True)

    def is_shadow_mode(self) -> bool:
        """Check if shadow mode is currently enabled."""
        return self.shadow_mode

    def set_shadow_mode(self, enabled: bool):
        """Enable or disable shadow mode."""
        self.shadow_mode = enabled
        logger.info(f"Shadow mode {'ENABLED' if enabled else 'DISABLED'}")

    def _check_place_order(self, params: Dict[str, Any]) -> HookResult:
        """Validate place_order calls."""
        # Check daily execution limit
        if execution_state.executions_today >= self.max_executions_per_day:
            return HookResult(
                allowed=False,
                reason=f"Daily execution limit reached ({self.max_executions_per_day})"
            )

        # Check position count
        if execution_state.positions_count >= self.max_positions:
            return HookResult(
                allowed=False,
                reason=f"Maximum positions reached ({self.max_positions})"
            )

        # Check spread if provided
        spread_pct = params.get("spread_pct")
        if spread_pct and spread_pct > self.max_spread_pct:
            return HookResult(
                allowed=False,
                reason=f"Spread too wide ({spread_pct:.1%} > {self.max_spread_pct:.1%})"
            )

        # Check earnings blackout if data provided
        earnings_date = params.get("earnings_date")
        if earnings_date:
            days_to_earnings = (earnings_date - date.today()).days
            if 0 <= days_to_earnings <= self.earnings_blackout_days:
                return HookResult(
                    allowed=False,
                    reason=f"Earnings blackout ({days_to_earnings} days to earnings)"
                )

        # Force limit orders
        order_type = params.get("order_type", "").upper()
        if order_type == "MARKET":
            return HookResult(
                allowed=False,
                reason="Market orders not allowed for entries (use LIMIT)"
            )

        return HookResult(allowed=True)

    def _check_close_position(self, params: Dict[str, Any]) -> HookResult:
        """Validate close_position calls."""
        # Exits are generally allowed, but track for circuit breaker
        # Check spread for exits too
        spread_pct = params.get("spread_pct")
        if spread_pct and spread_pct > 0.25:  # More lenient for exits
            logger.warning(f"Wide spread on exit: {spread_pct:.1%}")

        return HookResult(allowed=True)

    def _check_execute_roll(self, params: Dict[str, Any]) -> HookResult:
        """Validate roll execution."""
        # Rolls count as an execution
        if execution_state.executions_today >= self.max_executions_per_day:
            return HookResult(
                allowed=False,
                reason=f"Daily execution limit reached ({self.max_executions_per_day})"
            )

        return HookResult(allowed=True)


class PostToolUseHook:
    """
    Hook that runs AFTER tool execution.

    Updates state and triggers notifications.
    """

    def __init__(self, config: Dict[str, Any], telegram_notifier=None):
        self.config = config
        self.telegram = telegram_notifier

    def __call__(self, tool_name: str, tool_params: Dict[str, Any],
                 tool_result: Any) -> None:
        """
        Called after each tool execution.

        Updates state based on tool results.
        """
        if tool_name == "place_order":
            self._handle_order_result(tool_params, tool_result)
        elif tool_name == "close_position":
            self._handle_close_result(tool_params, tool_result)
        elif tool_name == "get_positions":
            self._handle_positions_result(tool_result)

    def _handle_order_result(self, params: Dict[str, Any], result: Any):
        """Handle place_order result."""
        if result.get("status") == "filled":
            execution_state.record_execution()
            execution_state.positions_count += 1
            logger.info(f"Order filled: {params.get('symbol')} - Executions today: {execution_state.executions_today}")

            if self.telegram:
                self._send_entry_notification(params, result)

    def _handle_close_result(self, params: Dict[str, Any], result: Any):
        """Handle close_position result."""
        if result.get("status") == "filled":
            execution_state.positions_count = max(0, execution_state.positions_count - 1)

            pnl = result.get("realized_pnl", 0)
            if pnl < 0:
                execution_state.record_loss(abs(pnl))
            else:
                execution_state.record_win(pnl)

            logger.info(f"Position closed: {params.get('symbol')} P/L: ${pnl:.2f}")

            if self.telegram:
                self._send_exit_notification(params, result)

    def _handle_positions_result(self, result: Any):
        """Update position count from positions query."""
        if isinstance(result, list):
            execution_state.positions_count = len(result)

    def _send_entry_notification(self, params: Dict[str, Any], result: Any):
        """Send Telegram notification for entry."""
        msg = f"📥 *ENTRY* | {params.get('underlying', 'N/A')}\n\n"
        msg += f"Contract: {params.get('symbol', 'N/A')}\n"
        msg += f"├── Type: {params.get('option_type', 'N/A')}\n"
        msg += f"├── Strike: ${params.get('strike', 0):.2f}\n"
        msg += f"├── Expiry: {params.get('expiry', 'N/A')}\n"
        msg += f"├── Fill: ${result.get('fill_price', 0):.2f}\n"
        msg += f"└── Cost: ${result.get('total_cost', 0):.2f}\n"
        msg += f"\nExecutions today: {execution_state.executions_today}/3"

        self.telegram.send_sync(msg, parse_mode="Markdown")

    def _send_exit_notification(self, params: Dict[str, Any], result: Any):
        """Send Telegram notification for exit."""
        pnl = result.get("realized_pnl", 0)
        pnl_pct = result.get("pnl_pct", 0)
        emoji = "📈" if pnl >= 0 else "📉"

        msg = f"{emoji} *EXIT* | {params.get('underlying', 'N/A')}\n\n"
        msg += f"Contract: {params.get('symbol', 'N/A')}\n"
        msg += f"├── Exit Price: ${result.get('fill_price', 0):.2f}\n"
        msg += f"├── P/L: ${pnl:.2f} ({pnl_pct:.1%})\n"
        msg += f"└── Reason: {params.get('reason', 'N/A')}\n"
        msg += f"\nDaily P/L: ${execution_state.daily_pnl:.2f}"

        self.telegram.send_sync(msg, parse_mode="Markdown")


class SafetyGateHook:
    """
    Combined hook for comprehensive safety enforcement.

    This is the main hook to use - it combines pre and post hooks.
    Includes shadow mode support for testing without real execution.
    """

    def __init__(self, config: Dict[str, Any], telegram_notifier=None):
        self.pre_hook = PreToolUseHook(config)
        self.post_hook = PostToolUseHook(config, telegram_notifier)
        self.config = config

    def pre_tool_use(self, tool_name: str, tool_params: Dict[str, Any]) -> HookResult:
        """Called before tool execution."""
        return self.pre_hook(tool_name, tool_params)

    def post_tool_use(self, tool_name: str, tool_params: Dict[str, Any],
                      tool_result: Any) -> None:
        """Called after tool execution."""
        self.post_hook(tool_name, tool_params, tool_result)

    def get_state(self) -> Dict[str, Any]:
        """Get current execution state for agent context."""
        return {
            "executions_today": execution_state.executions_today,
            "executions_remaining": max(0, 3 - execution_state.executions_today),
            "positions_count": execution_state.positions_count,
            "daily_pnl": execution_state.daily_pnl,
            "circuit_breaker_open": execution_state.circuit_breaker_open,
            "shadow_mode": self.pre_hook.is_shadow_mode(),
        }

    def is_shadow_mode(self) -> bool:
        """Check if shadow mode is enabled."""
        return self.pre_hook.is_shadow_mode()

    def set_shadow_mode(self, enabled: bool):
        """Enable or disable shadow mode."""
        self.pre_hook.set_shadow_mode(enabled)


def reset_daily_state():
    """Reset execution state for new day."""
    execution_state.reset_daily()
    logger.info("Daily execution state reset")


def get_execution_state() -> ExecutionState:
    """Get current execution state."""
    return execution_state
