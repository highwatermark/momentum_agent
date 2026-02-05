"""
Options Monitor Service - Real-time position monitoring and Greeks-aware exits

Polls positions every 45 seconds during market hours and monitors:
1. P/L thresholds (adaptive by DTE)
2. Greeks-based risk (gamma, theta, vega)
3. IV crush detection
4. Portfolio-level exposure limits

Three-layer monitoring:
1. Continuous rules-based checks (every 45s)
2. Daily AI review (10:00 AM ET)
3. Auto-exit on critical conditions
"""
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

import pytz
from dotenv import load_dotenv

load_dotenv()

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    OPTIONS_MONITOR_CONFIG,
    OPTIONS_CONFIG,
    OPTIONS_SAFETY,
)
from options_executor import (
    get_options_positions,
    get_portfolio_greeks,
    get_account_info,
    close_options_position,
    estimate_greeks,
    get_option_greeks,
    execute_roll,
    OptionsPosition,
)
from options_agent import (
    review_position,
    review_portfolio,
    PositionReviewInput,
    PortfolioReviewInput,
)
from db import (
    init_options_monitor_tables,
    get_options_monitor_state,
    update_options_monitor_state,
    increment_daily_exits_count,
    reset_options_monitor_daily,
    log_greeks_snapshot,
    get_entry_greeks,
    get_latest_greeks,
    log_monitor_alert,
    has_recent_alert,
    cleanup_old_greeks_history,
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/home/ubuntu/momentum-agent/logs/options_monitor.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")


# ============================================================================
# TELEGRAM NOTIFICATIONS
# ============================================================================

def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram Markdown"""
    if not text:
        return ""
    # Escape characters that have special meaning in Telegram Markdown
    for char in ['_', '*', '`', '[', ']', '(', ')']:
        text = text.replace(char, '\\' + char)
    return text


async def send_telegram(message: str, parse_mode: str = "Markdown"):
    """Send message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        logger.warning("Telegram not configured")
        return

    import aiohttp

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_ADMIN_ID,
        "text": message,
        "parse_mode": parse_mode,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Telegram error: {error_text}")
                    # Retry without parse mode if markdown failed
                    if "can't parse" in error_text.lower() or "parse" in error_text.lower():
                        payload["parse_mode"] = None
                        async with session.post(url, json=payload) as retry_resp:
                            if retry_resp.status != 200:
                                logger.error(f"Telegram retry also failed: {await retry_resp.text()}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


def send_telegram_sync(message: str, parse_mode: str = "Markdown"):
    """Synchronous wrapper for send_telegram"""
    import asyncio
    try:
        asyncio.run(send_telegram(message, parse_mode=parse_mode))
    except Exception as e:
        logger.error(f"Telegram sync send failed: {e}")


# ============================================================================
# CIRCUIT BREAKER
# ============================================================================

class CircuitBreaker:
    """Pause auto-execution after repeated errors"""

    def __init__(self):
        self.consecutive_errors = 0
        self.is_open = False
        self.last_error_time = None

    def record_error(self):
        self.consecutive_errors += 1
        self.last_error_time = datetime.now()
        max_errors = OPTIONS_MONITOR_CONFIG["max_consecutive_errors"]

        if self.consecutive_errors >= max_errors:
            self.is_open = True
            logger.error(f"Circuit breaker OPEN after {self.consecutive_errors} errors")
            send_telegram_sync(f"🔴 *Options Monitor Circuit Breaker OPEN*\nAuto-exits paused after {self.consecutive_errors} errors")
            update_options_monitor_state(circuit_breaker_open=1, consecutive_errors=self.consecutive_errors)

    def record_success(self):
        if self.consecutive_errors > 0:
            self.consecutive_errors = 0
            update_options_monitor_state(consecutive_errors=0)
        if self.is_open:
            self.is_open = False
            logger.info("Circuit breaker CLOSED - resuming normal operation")
            send_telegram_sync("🟢 *Options Monitor Circuit Breaker CLOSED*\nResuming normal operation")
            update_options_monitor_state(circuit_breaker_open=0)

    def can_execute(self) -> bool:
        if not self.is_open:
            return True

        # Check if cooldown has expired
        cooldown = OPTIONS_MONITOR_CONFIG["circuit_breaker_cooldown_seconds"]
        if self.last_error_time:
            elapsed = (datetime.now() - self.last_error_time).total_seconds()
            if elapsed > cooldown:
                self.is_open = False
                self.consecutive_errors = 0
                logger.info("Circuit breaker CLOSED - cooldown expired")
                update_options_monitor_state(circuit_breaker_open=0, consecutive_errors=0)
                return True

        return False


# ============================================================================
# MARKET HOURS CHECK
# ============================================================================

def is_market_hours() -> bool:
    """Check if current time is within market hours (ET)"""
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)

    # Skip weekends
    if now.weekday() >= 5:
        return False

    config = OPTIONS_MONITOR_CONFIG
    market_open = now.replace(
        hour=config["market_open_hour"],
        minute=config["market_open_minute"],
        second=0,
        microsecond=0
    )
    market_close = now.replace(
        hour=config["market_close_hour"],
        minute=config["market_close_minute"],
        second=0,
        microsecond=0
    )

    return market_open <= now <= market_close


def get_et_time() -> str:
    """Get current time in ET as formatted string"""
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)
    return now.strftime("%H:%M:%S ET")


def get_et_now() -> datetime:
    """Get current datetime in ET"""
    et = pytz.timezone('America/New_York')
    return datetime.now(et)


# ============================================================================
# ALERT DATACLASS
# ============================================================================

@dataclass
class Alert:
    """Monitoring alert"""
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    alert_type: str  # expiration, profit_target, stop_loss, gamma_risk, iv_crush, etc.
    message: str
    auto_exit: bool = False


# ============================================================================
# OPTIONS MONITOR CLASS
# ============================================================================

class OptionsMonitor:
    """Real-time options position monitoring service"""

    def __init__(self):
        self.circuit_breaker = CircuitBreaker()
        self.last_greeks_snapshot = None
        self.last_ai_review = None
        self.daily_exits_count = 0
        self.last_reset_date = datetime.now().date()

        # Initialize database tables
        init_options_monitor_tables()

        # Load state from database
        self._load_state()

    def _load_state(self):
        """Load state from database"""
        state = get_options_monitor_state()

        if state.get("last_ai_review_time"):
            try:
                self.last_ai_review = datetime.fromisoformat(state["last_ai_review_time"])
            except Exception:
                pass

        self.daily_exits_count = state.get("daily_exits_count", 0)

        if state.get("last_reset_date"):
            try:
                self.last_reset_date = datetime.fromisoformat(state["last_reset_date"]).date()
            except Exception:
                pass

        # Restore circuit breaker state
        if state.get("circuit_breaker_open"):
            self.circuit_breaker.is_open = True
            self.circuit_breaker.consecutive_errors = state.get("consecutive_errors", 0)

    def _save_state(self):
        """Save state to database"""
        update_options_monitor_state(
            last_check_time=datetime.now().isoformat(),
            daily_exits_count=self.daily_exits_count,
            last_reset_date=self.last_reset_date.isoformat(),
        )

    def _check_daily_reset(self):
        """Reset daily counters at midnight"""
        today = datetime.now().date()
        if today > self.last_reset_date:
            logger.info("New day detected, resetting daily counters")
            self.daily_exits_count = 0
            self.last_reset_date = today
            reset_options_monitor_daily(today.isoformat())

            # Cleanup old Greeks history
            deleted = cleanup_old_greeks_history(days=30)
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old Greeks history records")

    def run(self):
        """Main run loop"""
        logger.info("=" * 60)
        logger.info("OPTIONS MONITOR SERVICE STARTING")
        logger.info("=" * 60)

        config = OPTIONS_MONITOR_CONFIG
        send_telegram_sync(
            f"👁️ *Options Monitor Started*\n"
            f"Polling every {config['poll_interval_seconds']}s\n"
            f"Auto-exit: {'ON' if config['enable_auto_exit'] else 'OFF'}\n"
            f"Max daily exits: {config['max_auto_exits_per_day']}"
        )

        while True:
            try:
                cycle_start = time.time()

                self._check_daily_reset()

                if is_market_hours():
                    self._monitor_cycle()
                else:
                    logger.debug("Outside market hours, sleeping...")

                # Save state periodically
                self._save_state()

                # Calculate sleep time
                cycle_time = time.time() - cycle_start
                sleep_time = max(0, config["poll_interval_seconds"] - cycle_time)

                if cycle_time > 5:
                    logger.info(f"Cycle completed in {cycle_time:.1f}s, sleeping {sleep_time:.1f}s")

                time.sleep(sleep_time)

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                self._save_state()
                send_telegram_sync("🛑 *Options Monitor Stopped*")
                break
            except Exception as e:
                logger.exception(f"Error in main loop: {e}")
                self.circuit_breaker.record_error()
                time.sleep(10)

    def _monitor_cycle(self):
        """Single monitoring cycle - continuous AI-driven monitoring"""
        config = OPTIONS_MONITOR_CONFIG

        # Check circuit breaker
        if not self.circuit_breaker.can_execute():
            logger.warning("Circuit breaker open, skipping cycle")
            return

        # Get positions
        try:
            positions = get_options_positions()
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            self.circuit_breaker.record_error()
            return

        if not positions:
            logger.info("No open positions to monitor")
            self.circuit_breaker.record_success()
            return

        logger.info(f"Monitoring {len(positions)} positions")

        # Get portfolio Greeks
        try:
            portfolio_greeks = get_portfolio_greeks()
        except Exception as e:
            logger.error(f"Error fetching portfolio Greeks: {e}")
            portfolio_greeks = {"net_delta": 0, "total_gamma": 0, "daily_theta": 0, "total_vega": 0}

        # Get account info
        try:
            account = get_account_info()
            equity = account.get("equity", 100000)
        except Exception as e:
            logger.error(f"Error fetching account: {e}")
            equity = 100000

        # Check each position - if concerning, call Claude immediately
        for pos in positions:
            try:
                self._evaluate_position(pos, portfolio_greeks, equity)
            except Exception as e:
                logger.error(f"Error evaluating position {getattr(pos, 'symbol', 'unknown')}: {e}")

        # Snapshot Greeks periodically
        if self._should_snapshot_greeks():
            self._snapshot_greeks(positions)

        self.circuit_breaker.record_success()

    def _evaluate_position(self, pos, portfolio_greeks: Dict, equity: float):
        """
        Evaluate a position and call Claude if action may be needed.
        This is the core of continuous monitoring - when conditions warrant,
        we immediately ask Claude what to do and execute the decision.
        """
        # Get contract symbol and underlying - handle both OptionsPosition and raw objects
        contract_symbol = getattr(pos, 'contract_symbol', None) or getattr(pos, 'symbol', 'UNKNOWN')
        underlying = getattr(pos, 'symbol', contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol)

        # If underlying looks like a contract symbol, extract the actual underlying
        if len(underlying) > 6:
            underlying = underlying[:4].rstrip('0123456789')

        expiration = getattr(pos, 'expiration_date', '') or getattr(pos, 'expiration', '')
        unrealized_plpc = float(getattr(pos, 'unrealized_plpc', 0) or 0)
        current_price = float(getattr(pos, 'current_price', 0) or 0)

        dte = self._calculate_dte(expiration)

        # Get current Greeks
        try:
            greeks = get_option_greeks(contract_symbol, current_price)
            gamma = greeks.gamma if greeks else 0
        except Exception:
            gamma = 0

        # =====================================================================
        # DETERMINE IF THIS POSITION NEEDS AI EVALUATION
        # We call Claude when any concerning condition is detected
        # =====================================================================
        config = OPTIONS_MONITOR_CONFIG

        needs_ai_review = False
        trigger_reason = None

        # 1. Position is losing money
        loss_threshold = config.get("ai_trigger_loss_pct", 0.15)
        if unrealized_plpc <= -loss_threshold:
            needs_ai_review = True
            trigger_reason = f"losing_money_{abs(unrealized_plpc):.0%}"

        # 2. Expiration approaching
        dte_threshold = config.get("ai_trigger_dte", 7)
        if not needs_ai_review and dte <= dte_threshold:
            needs_ai_review = True
            trigger_reason = f"expiration_approaching_dte_{dte}"

        # 3. Significant profit - should we take profits?
        profit_threshold = config.get("ai_trigger_profit_pct", 0.30)
        if not needs_ai_review and unrealized_plpc >= profit_threshold:
            needs_ai_review = True
            trigger_reason = f"profit_opportunity_{unrealized_plpc:.0%}"

        # 4. High gamma risk (ATM near expiry)
        if not needs_ai_review and dte <= 10 and abs(gamma) > config["gamma_risk_threshold"]:
            needs_ai_review = True
            trigger_reason = f"high_gamma_risk_{gamma:.3f}"

        # 5. IV crush detected
        if not needs_ai_review:
            entry_greeks = get_entry_greeks(contract_symbol)
            if entry_greeks and entry_greeks.get('iv') and greeks and greeks.iv > 0:
                entry_iv = entry_greeks['iv']
                iv_change_pct = ((greeks.iv - entry_iv) / entry_iv) * 100 if entry_iv > 0 else 0
                if iv_change_pct <= -config["iv_crush_threshold_pct"]:
                    needs_ai_review = True
                    trigger_reason = f"iv_crush_{iv_change_pct:.0f}pct"

        # =====================================================================
        # IF AI REVIEW NEEDED, CHECK RATE LIMIT THEN CALL CLAUDE
        # =====================================================================

        if needs_ai_review:
            # Rate limit: don't call Claude for same position too frequently
            cooldown = config.get("ai_review_cooldown_minutes", 10)
            if has_recent_alert(contract_symbol, f"ai_review_{trigger_reason}", minutes=cooldown):
                logger.debug(f"[{underlying}] Skipping AI review - recently evaluated for {trigger_reason}")
                return

            logger.info(f"[{underlying}] Triggering AI review: {trigger_reason}")

            # Call Claude to evaluate and decide
            self._ai_evaluate_and_act(pos, trigger_reason, dte, unrealized_plpc, greeks, equity)

    def _ai_evaluate_and_act(self, pos, trigger_reason: str, dte: int, unrealized_plpc: float, greeks, equity: float):
        """Call Claude to evaluate position and immediately execute the decision"""
        # Get contract symbol and underlying properly
        contract_symbol = getattr(pos, 'contract_symbol', None) or getattr(pos, 'symbol', 'UNKNOWN')
        underlying = getattr(pos, 'symbol', contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol)
        if len(underlying) > 6:
            underlying = underlying[:4].rstrip('0123456789')

        expiration = getattr(pos, 'expiration_date', '') or getattr(pos, 'expiration', '')
        current_price = float(getattr(pos, 'current_price', 0) or 0)

        # Build review input
        review_input = PositionReviewInput(
            contract_symbol=contract_symbol,
            underlying=underlying,
            option_type=getattr(pos, 'option_type', 'unknown'),
            strike=getattr(pos, 'strike_price', 0) or getattr(pos, 'strike', 0),
            expiration=expiration,
            quantity=int(getattr(pos, 'qty', 0) or 0),
            avg_entry_price=float(getattr(pos, 'avg_entry_price', 0) or 0),
            current_price=current_price,
            unrealized_pl=float(getattr(pos, 'unrealized_pl', 0) or 0),
            unrealized_plpc=unrealized_plpc,
            delta=greeks.delta if greeks else 0,
            gamma=greeks.gamma if greeks else 0,
            theta=greeks.theta if greeks else 0,
            vega=greeks.vega if greeks else 0,
            iv=greeks.iv if greeks else 0,
            underlying_price=0,
            days_to_expiry=dte,
        )

        # Call Claude for decision
        try:
            result = review_position(review_input, use_agent=True)
            logger.info(f"[{underlying}] Claude says: {result.recommendation} ({result.urgency}) - {result.reasoning[:80]}...")

            # Log that we did an AI review
            log_monitor_alert(
                contract_symbol=contract_symbol,
                alert_type=f"ai_review_{trigger_reason}",
                severity=result.urgency,
                message=f"{result.recommendation}: {result.reasoning[:200]}",
                action_taken=None
            )

            # Execute the decision based on recommendation
            action_taken = self._execute_ai_decision(pos, result, trigger_reason)

            # Update the alert with action taken
            if action_taken:
                log_monitor_alert(
                    contract_symbol=contract_symbol,
                    alert_type=f"action_executed",
                    severity=result.urgency,
                    message=f"Executed {action_taken} based on AI recommendation",
                    action_taken=action_taken
                )

        except Exception as e:
            logger.error(f"[{underlying}] Error in AI evaluation: {e}")
            # Fall back to rules-based decision
            self._rules_based_fallback(pos, trigger_reason, dte, unrealized_plpc)

    def _execute_ai_decision(self, pos, result, trigger_reason: str) -> Optional[str]:
        """Execute Claude's decision immediately"""
        contract_symbol = getattr(pos, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol

        # CLOSE - Exit the position
        if result.recommendation == 'CLOSE':
            if self._can_auto_exit():
                success = self._execute_exit(pos, f"ai_{trigger_reason}")
                if success:
                    return "CLOSED"
            else:
                self._send_telegram_ai_action_blocked(underlying, 'CLOSE', result)
                return None

        # ROLL - Close and reopen at later expiration
        elif result.recommendation == 'ROLL':
            if self._can_auto_exit():
                roll_result = self._execute_roll_for_position(pos, result, trigger_reason)
                if roll_result:
                    return "ROLLED"
            else:
                self._send_telegram_ai_action_blocked(underlying, 'ROLL', result)
                return None

        # TRIM - Reduce position size (close partial)
        elif result.recommendation == 'TRIM':
            # For now, treat TRIM as an alert - partial closes are more complex
            self._send_telegram_ai_recommendation(underlying, result)
            return None

        # HOLD - Do nothing, position is fine
        elif result.recommendation == 'HOLD':
            logger.info(f"[{underlying}] Holding position per AI recommendation")
            return None

        return None

    def _execute_roll_for_position(self, pos, result, trigger_reason: str) -> bool:
        """Execute a roll for a position"""
        contract_symbol = getattr(pos, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol
        expiration = getattr(pos, 'expiration_date', '') or getattr(pos, 'expiration', '')

        logger.info(f"[{underlying}] Executing roll based on AI recommendation")

        try:
            # Create OptionsPosition object for the roll function
            options_pos = OptionsPosition(
                symbol=underlying,
                contract_symbol=contract_symbol,
                option_type=getattr(pos, 'option_type', 'unknown'),
                strike=getattr(pos, 'strike_price', 0) or getattr(pos, 'strike', 0),
                expiration=expiration,
                quantity=int(getattr(pos, 'qty', 0) or 0),
                avg_entry_price=float(getattr(pos, 'avg_entry_price', 0) or 0),
                current_price=float(getattr(pos, 'current_price', 0) or 0),
                market_value=float(getattr(pos, 'market_value', 0) or 0),
                unrealized_pl=float(getattr(pos, 'unrealized_pl', 0) or 0),
                unrealized_plpc=float(getattr(pos, 'unrealized_plpc', 0) or 0),
            )

            roll_result = execute_roll(options_pos, reason=f"ai_{trigger_reason}")

            if roll_result.get("success"):
                self.daily_exits_count += 1
                increment_daily_exits_count()
                self._send_roll_notification(underlying, roll_result)
                logger.info(f"[{underlying}] Successfully rolled to {roll_result.get('new_contract')}")
                return True
            else:
                logger.error(f"[{underlying}] Roll failed: {roll_result.get('error')}")
                return False

        except Exception as e:
            logger.exception(f"[{underlying}] Error executing roll: {e}")
            return False

    def _rules_based_fallback(self, pos, trigger_reason: str, dte: int, unrealized_plpc: float):
        """Fallback to rules-based decision if Claude unavailable"""
        contract_symbol = getattr(pos, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol

        logger.warning(f"[{underlying}] Using rules-based fallback for {trigger_reason}")

        # Simple rules-based logic
        should_exit = False
        reason = None

        # Hard stop loss at 50%
        if unrealized_plpc <= -0.50:
            should_exit = True
            reason = "stop_loss_50pct"

        # Expiring tomorrow or today
        elif dte <= 1:
            should_exit = True
            reason = f"expiring_dte_{dte}"

        # Take profit at 50%+ with DTE < 5
        elif unrealized_plpc >= 0.50 and dte <= 5:
            should_exit = True
            reason = "profit_target_near_expiry"

        if should_exit and self._can_auto_exit():
            success = self._execute_exit(pos, f"rules_{reason}")
            if success:
                log_monitor_alert(
                    contract_symbol=contract_symbol,
                    alert_type="rules_based_exit",
                    severity="high",
                    message=f"Rules-based exit: {reason}",
                    action_taken="CLOSED"
                )

    def _check_position(self, pos, portfolio_greeks: Dict, equity: float) -> List[Alert]:
        """Check single position for exit triggers"""
        alerts = []
        config = OPTIONS_MONITOR_CONFIG

        # Get position attributes
        symbol = getattr(pos, 'symbol', 'UNKNOWN')
        contract_symbol = getattr(pos, 'symbol', '')  # Alpaca uses 'symbol' for contract
        option_type = getattr(pos, 'option_type', 'unknown')
        strike = getattr(pos, 'strike_price', 0) or getattr(pos, 'strike', 0)
        expiration = getattr(pos, 'expiration_date', '') or getattr(pos, 'expiration', '')
        quantity = int(getattr(pos, 'qty', 0) or getattr(pos, 'quantity', 0))
        avg_entry = float(getattr(pos, 'avg_entry_price', 0) or 0)
        current_price = float(getattr(pos, 'current_price', 0) or 0)
        market_value = float(getattr(pos, 'market_value', 0) or 0)
        unrealized_pl = float(getattr(pos, 'unrealized_pl', 0) or 0)
        unrealized_plpc = float(getattr(pos, 'unrealized_plpc', 0) or 0)

        # Calculate DTE
        dte = self._calculate_dte(expiration)

        # Get current Greeks
        try:
            greeks = get_option_greeks(contract_symbol, current_price)
            delta = greeks.delta if greeks else 0
            gamma = greeks.gamma if greeks else 0
            theta = greeks.theta if greeks else 0
            vega = greeks.vega if greeks else 0
            iv = greeks.iv if greeks else 0
        except Exception as e:
            logger.warning(f"Could not get Greeks for {symbol}: {e}")
            delta = gamma = theta = vega = iv = 0

        # Extract underlying symbol from contract
        underlying = symbol[:4].rstrip('0123456789') if len(symbol) > 10 else symbol

        # ===== CHECK 1: DTE-based checks =====
        if dte <= 1:
            alerts.append(Alert(
                severity="CRITICAL",
                alert_type="expiration",
                message=f"Expiring {'today' if dte <= 0 else 'tomorrow'}",
                auto_exit=True
            ))
        elif dte <= 3:
            alerts.append(Alert(
                severity="HIGH",
                alert_type="expiration",
                message=f"DTE={dte}, critical expiration window"
            ))

        # ===== CHECK 2: P/L with adaptive thresholds =====
        profit_target = self._get_profit_target(dte)
        stop_loss = self._get_stop_loss(dte)

        if unrealized_plpc >= profit_target:
            alerts.append(Alert(
                severity="HIGH",
                alert_type="profit_target",
                message=f"Profit target hit: {unrealized_plpc:.1%} >= {profit_target:.0%}",
                auto_exit=True if dte <= 3 else False
            ))

        if unrealized_plpc <= -stop_loss:
            alerts.append(Alert(
                severity="CRITICAL",
                alert_type="stop_loss",
                message=f"Stop loss hit: {unrealized_plpc:.1%}",
                auto_exit=True
            ))

        # ===== CHECK 3: Gamma risk (ATM near expiry) =====
        if dte <= config["gamma_critical_dte"] and abs(gamma) > config["gamma_risk_threshold"]:
            alerts.append(Alert(
                severity="HIGH",
                alert_type="gamma_risk",
                message=f"High gamma risk: γ={gamma:.3f}, DTE={dte}"
            ))

        # ===== CHECK 4: IV crush detection =====
        entry_greeks = get_entry_greeks(contract_symbol)
        if entry_greeks and entry_greeks.get('iv') and iv > 0:
            entry_iv = entry_greeks['iv']
            iv_change_pct = ((iv - entry_iv) / entry_iv) * 100 if entry_iv > 0 else 0

            if iv_change_pct <= -config["iv_crush_threshold_pct"]:
                alerts.append(Alert(
                    severity="MEDIUM",
                    alert_type="iv_crush",
                    message=f"IV crush: {iv_change_pct:.0f}% from entry ({entry_iv:.1f}% → {iv:.1f}%)"
                ))

        # ===== CHECK 5: Theta acceleration =====
        if dte <= 7 and current_price > 0:
            daily_decay_pct = abs(theta) / current_price
            if daily_decay_pct > config["theta_acceleration_threshold"]:
                alerts.append(Alert(
                    severity="MEDIUM",
                    alert_type="theta_acceleration",
                    message=f"Theta acceleration: {daily_decay_pct:.1%}/day decay"
                ))

        return alerts

    def _check_portfolio(self, portfolio_greeks: Dict, equity: float) -> List[Alert]:
        """Check portfolio-level risk metrics"""
        alerts = []
        config = OPTIONS_MONITOR_CONFIG

        # Delta exposure
        net_delta = portfolio_greeks.get("net_delta", 0)
        delta_per_100k = abs(net_delta) / (equity / 100000) if equity > 0 else 0

        if delta_per_100k > config["max_portfolio_delta_per_100k"]:
            alerts.append(Alert(
                severity="HIGH",
                alert_type="portfolio_delta",
                message=f"Portfolio delta too high: {delta_per_100k:.0f} per $100K (max: {config['max_portfolio_delta_per_100k']})"
            ))

        # Theta decay
        daily_theta = portfolio_greeks.get("daily_theta", 0)
        daily_theta_pct = abs(daily_theta) / equity if equity > 0 else 0

        if daily_theta_pct > config["max_daily_theta_pct"]:
            alerts.append(Alert(
                severity="MEDIUM",
                alert_type="portfolio_theta",
                message=f"Daily theta: {daily_theta_pct:.2%} of portfolio (max: {config['max_daily_theta_pct']:.2%})"
            ))

        # Vega exposure
        total_vega = portfolio_greeks.get("total_vega", 0)
        vega_pct = abs(total_vega) / equity if equity > 0 else 0

        if vega_pct > config["max_vega_exposure_pct"]:
            alerts.append(Alert(
                severity="MEDIUM",
                alert_type="portfolio_vega",
                message=f"High vega exposure: {vega_pct:.2%} (max: {config['max_vega_exposure_pct']:.2%})"
            ))

        return alerts

    def _process_alerts(self, alerts: List[Alert], position):
        """Process alerts - execute exits or send notifications"""
        config = OPTIONS_MONITOR_CONFIG
        contract_symbol = getattr(position, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol

        for alert in alerts:
            # Deduplication check
            if has_recent_alert(contract_symbol, alert.alert_type, config["alert_cooldown_minutes"]):
                continue

            # Log alert to database
            action_taken = None

            if alert.severity == "CRITICAL" and alert.auto_exit:
                if self._can_auto_exit():
                    result = self._execute_exit(position, alert.message)
                    action_taken = "auto_exit" if result else "exit_failed"
                else:
                    action_taken = "auto_exit_blocked"
                    self._send_telegram_critical(position, alert)
            elif alert.severity in ["CRITICAL", "HIGH"]:
                self._send_telegram_warning(position, alert)
                action_taken = "alert_sent"
            elif alert.severity == "MEDIUM":
                logger.info(f"[{underlying}] {alert.alert_type}: {alert.message}")
                action_taken = "logged"

            # Record alert
            log_monitor_alert(
                contract_symbol=contract_symbol,
                alert_type=alert.alert_type,
                severity=alert.severity,
                message=alert.message,
                action_taken=action_taken
            )

    def _process_portfolio_alerts(self, alerts: List[Alert]):
        """Process portfolio-level alerts"""
        config = OPTIONS_MONITOR_CONFIG

        for alert in alerts:
            # Deduplication
            if has_recent_alert("PORTFOLIO", alert.alert_type, config["alert_cooldown_minutes"]):
                continue

            if alert.severity in ["CRITICAL", "HIGH"]:
                self._send_telegram_portfolio_warning(alert)

            log_monitor_alert(
                contract_symbol="PORTFOLIO",
                alert_type=alert.alert_type,
                severity=alert.severity,
                message=alert.message,
                action_taken="alert_sent"
            )

    def _can_auto_exit(self) -> bool:
        """Check if auto-exit is allowed"""
        config = OPTIONS_MONITOR_CONFIG

        if not config["enable_auto_exit"]:
            return False

        if self.daily_exits_count >= config["max_auto_exits_per_day"]:
            logger.warning(f"Daily auto-exit limit reached: {self.daily_exits_count}")
            return False

        return True

    def _execute_exit(self, position, reason: str) -> bool:
        """Execute an auto-exit"""
        # Get the full contract symbol - try contract_symbol first, then symbol
        contract_symbol = getattr(position, 'contract_symbol', None) or getattr(position, 'symbol', 'UNKNOWN')
        underlying = getattr(position, 'symbol', contract_symbol[:4].rstrip('0123456789'))

        # If contract_symbol looks like just an underlying (< 10 chars), it's wrong
        if len(contract_symbol) < 10:
            logger.error(f"Invalid contract symbol '{contract_symbol}' - need full OCC symbol")
            return False

        logger.info(f"Auto-exiting {underlying} ({contract_symbol}): {reason}")

        try:
            result = close_options_position(contract_symbol, reason=f"monitor_{reason}")

            if result.get("success"):
                self.daily_exits_count += 1
                increment_daily_exits_count()

                self._send_exit_notification(position, reason, result)
                logger.info(f"Successfully exited {underlying} ({contract_symbol})")
                return True
            else:
                logger.error(f"Exit failed for {contract_symbol}: {result.get('error')}")
                return False

        except Exception as e:
            logger.exception(f"Error executing exit for {contract_symbol}: {e}")
            return False

    def _get_profit_target(self, dte: int) -> float:
        """Get adaptive profit target based on DTE"""
        targets = OPTIONS_MONITOR_CONFIG["profit_targets_by_dte"]

        for dte_threshold in sorted(targets.keys(), reverse=True):
            if dte > dte_threshold:
                return targets[dte_threshold]

        return targets[0]  # Lowest threshold for DTE < 3

    def _get_stop_loss(self, dte: int) -> float:
        """Get stop loss threshold"""
        # Could be enhanced to consider position conviction
        return OPTIONS_MONITOR_CONFIG["base_stop_loss_pct"]

    def _calculate_dte(self, expiration: str) -> int:
        """Calculate days to expiration"""
        if not expiration:
            return 999

        try:
            if isinstance(expiration, str):
                # Handle different date formats
                if 'T' in expiration:
                    exp_date = datetime.fromisoformat(expiration.replace('Z', '+00:00')).date()
                else:
                    exp_date = datetime.strptime(expiration[:10], "%Y-%m-%d").date()
            else:
                exp_date = expiration

            today = datetime.now().date()
            return (exp_date - today).days
        except Exception:
            return 999

    def _should_snapshot_greeks(self) -> bool:
        """Check if it's time for a Greeks snapshot"""
        if self.last_greeks_snapshot is None:
            return True

        elapsed = (datetime.now() - self.last_greeks_snapshot).total_seconds()
        return elapsed >= OPTIONS_MONITOR_CONFIG["greeks_snapshot_interval_seconds"]

    def _snapshot_greeks(self, positions):
        """Take a Greeks snapshot for all positions"""
        logger.debug("Taking Greeks snapshot")

        for pos in positions:
            try:
                contract_symbol = getattr(pos, 'symbol', '')
                current_price = float(getattr(pos, 'current_price', 0) or 0)
                expiration = getattr(pos, 'expiration_date', '') or getattr(pos, 'expiration', '')

                greeks = get_option_greeks(contract_symbol, current_price)
                if greeks:
                    dte = self._calculate_dte(expiration)
                    log_greeks_snapshot(
                        contract_symbol=contract_symbol,
                        delta=greeks.delta,
                        gamma=greeks.gamma,
                        theta=greeks.theta,
                        vega=greeks.vega,
                        iv=greeks.iv,
                        underlying_price=0,  # Would need to fetch
                        option_price=current_price,
                        dte=dte
                    )
            except Exception as e:
                logger.warning(f"Could not snapshot Greeks for {getattr(pos, 'symbol', 'unknown')}: {e}")

        self.last_greeks_snapshot = datetime.now()

    def _should_run_ai_review(self) -> bool:
        """Check if it's time for the daily AI review"""
        config = OPTIONS_MONITOR_CONFIG
        et_now = get_et_now()

        # Only run once per day at the scheduled time
        if self.last_ai_review:
            if self.last_ai_review.date() >= et_now.date():
                return False

        # Check if we're at or past the scheduled time
        scheduled_time = et_now.replace(
            hour=config["ai_review_hour"],
            minute=config["ai_review_minute"],
            second=0,
            microsecond=0
        )

        return et_now >= scheduled_time

    def _run_ai_review(self, positions, portfolio_greeks: Dict, equity: float):
        """Run daily AI review of all positions and act on recommendations"""
        logger.info("Running daily AI review")

        try:
            # Build position data for AI
            position_dicts = []
            position_map = {}  # Map contract_symbol to position object

            for pos in positions:
                contract_symbol = getattr(pos, 'symbol', '')
                current_price = float(getattr(pos, 'current_price', 0) or 0)
                expiration = getattr(pos, 'expiration_date', '') or getattr(pos, 'expiration', '')

                greeks = get_option_greeks(contract_symbol, current_price)
                dte = self._calculate_dte(expiration)

                position_dicts.append({
                    'contract_symbol': contract_symbol,
                    'symbol': contract_symbol[:4].rstrip('0123456789'),
                    'option_type': getattr(pos, 'option_type', 'unknown'),
                    'strike': getattr(pos, 'strike_price', 0) or getattr(pos, 'strike', 0),
                    'expiration': expiration,
                    'quantity': int(getattr(pos, 'qty', 0) or 0),
                    'avg_entry_price': float(getattr(pos, 'avg_entry_price', 0) or 0),
                    'current_price': current_price,
                    'unrealized_pl': float(getattr(pos, 'unrealized_pl', 0) or 0),
                    'unrealized_plpc': float(getattr(pos, 'unrealized_plpc', 0) or 0),
                    'delta': greeks.delta if greeks else 0,
                    'gamma': greeks.gamma if greeks else 0,
                    'theta': greeks.theta if greeks else 0,
                    'vega': greeks.vega if greeks else 0,
                    'iv': greeks.iv if greeks else 0,
                    'days_to_expiry': dte,
                })
                position_map[contract_symbol] = pos

            # Review each position individually with AI
            actions_taken = []
            for pos_dict in position_dicts:
                try:
                    action = self._review_and_act_on_position(pos_dict, position_map, equity)
                    if action:
                        actions_taken.append(action)
                except Exception as e:
                    logger.error(f"Error reviewing {pos_dict['symbol']}: {e}")

            # Also run portfolio-level review
            options_exposure = sum(p['current_price'] * p['quantity'] * 100 for p in position_dicts)
            options_exposure_pct = (options_exposure / equity * 100) if equity > 0 else 0

            portfolio_input = PortfolioReviewInput(
                account_equity=equity,
                cash_available=equity - options_exposure,
                options_exposure=options_exposure,
                options_exposure_pct=options_exposure_pct,
                net_delta=portfolio_greeks.get("net_delta", 0),
                total_gamma=portfolio_greeks.get("total_gamma", 0),
                daily_theta=portfolio_greeks.get("daily_theta", 0),
                total_vega=portfolio_greeks.get("total_vega", 0),
                positions=position_dicts,
                sector_allocation={},
                spy_price=0,
                spy_change_1d=0,
                spy_change_5d=0,
                vix_level=20,
                max_single_position_pct=0,
                positions_expiring_soon=sum(1 for p in position_dicts if p['days_to_expiry'] <= 7)
            )

            review_result = review_portfolio(portfolio_input, use_agent=True)

            # Send summary with actions taken
            self._send_ai_review_summary(review_result, position_dicts, actions_taken)

            # Update last AI review time
            self.last_ai_review = datetime.now()
            update_options_monitor_state(last_ai_review_time=self.last_ai_review.isoformat())

            logger.info(f"AI review complete: {review_result.overall_assessment} (risk score: {review_result.risk_score}), actions: {len(actions_taken)}")

        except Exception as e:
            logger.exception(f"Error in AI review: {e}")

    def _review_and_act_on_position(self, pos_dict: Dict, position_map: Dict, equity: float) -> Optional[Dict]:
        """Review a single position with AI and act on the recommendation"""
        contract_symbol = pos_dict['contract_symbol']
        underlying = pos_dict['symbol']

        # Build input for position review
        review_input = PositionReviewInput(
            contract_symbol=contract_symbol,
            underlying=underlying,
            option_type=pos_dict['option_type'],
            strike=pos_dict['strike'],
            expiration=pos_dict['expiration'],
            quantity=pos_dict['quantity'],
            avg_entry_price=pos_dict['avg_entry_price'],
            current_price=pos_dict['current_price'],
            unrealized_pl=pos_dict['unrealized_pl'],
            unrealized_plpc=pos_dict['unrealized_plpc'],
            delta=pos_dict['delta'],
            gamma=pos_dict['gamma'],
            theta=pos_dict['theta'],
            vega=pos_dict['vega'],
            iv=pos_dict['iv'],
            underlying_price=0,  # Would need to fetch
            days_to_expiry=pos_dict['days_to_expiry'],
        )

        # Get AI recommendation
        result = review_position(review_input, use_agent=True)

        logger.info(f"[{underlying}] AI recommends: {result.recommendation} ({result.urgency}) - {result.reasoning[:100]}")

        # Act on recommendations with high/critical urgency
        if result.urgency in ['critical', 'high']:
            if result.recommendation == 'CLOSE':
                if self._can_auto_exit():
                    pos = position_map.get(contract_symbol)
                    if pos:
                        success = self._execute_exit(pos, f"ai_review_{result.urgency}")
                        if success:
                            return {
                                'action': 'CLOSED',
                                'symbol': underlying,
                                'contract': contract_symbol,
                                'reason': result.reasoning[:100],
                                'urgency': result.urgency
                            }
                else:
                    # Alert that we wanted to close but hit limit
                    self._send_telegram_ai_action_blocked(underlying, 'CLOSE', result)

            elif result.recommendation == 'ROLL':
                pos = position_map.get(contract_symbol)
                if pos and self._can_auto_exit():  # Rolling counts as an exit
                    roll_result = self._execute_roll_action(pos, result)
                    if roll_result:
                        return roll_result
                else:
                    self._send_telegram_ai_action_blocked(underlying, 'ROLL', result)

        elif result.urgency == 'medium' and result.recommendation in ['CLOSE', 'ROLL']:
            # For medium urgency, just alert - don't auto-execute
            self._send_telegram_ai_recommendation(underlying, result)

        return None

    def _execute_roll_action(self, position, review_result) -> Optional[Dict]:
        """Execute a roll based on AI recommendation"""
        contract_symbol = getattr(position, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol

        logger.info(f"Executing roll for {underlying}")

        try:
            # Create OptionsPosition object for the roll function
            options_pos = OptionsPosition(
                symbol=underlying,
                contract_symbol=contract_symbol,
                option_type=getattr(position, 'option_type', 'unknown'),
                strike=getattr(position, 'strike_price', 0) or getattr(position, 'strike', 0),
                expiration=getattr(position, 'expiration_date', '') or getattr(position, 'expiration', ''),
                quantity=int(getattr(position, 'qty', 0) or 0),
                avg_entry_price=float(getattr(position, 'avg_entry_price', 0) or 0),
                current_price=float(getattr(position, 'current_price', 0) or 0),
                market_value=float(getattr(position, 'market_value', 0) or 0),
                unrealized_pl=float(getattr(position, 'unrealized_pl', 0) or 0),
                unrealized_plpc=float(getattr(position, 'unrealized_plpc', 0) or 0),
            )

            result = execute_roll(options_pos, reason=f"ai_review_{review_result.urgency}")

            if result.get("success"):
                self.daily_exits_count += 1
                increment_daily_exits_count()

                self._send_roll_notification(underlying, result)
                logger.info(f"Successfully rolled {underlying} to {result.get('new_contract')}")

                return {
                    'action': 'ROLLED',
                    'symbol': underlying,
                    'old_contract': contract_symbol,
                    'new_contract': result.get('new_contract'),
                    'new_expiration': result.get('new_expiration'),
                    'roll_cost': result.get('roll_cost'),
                    'urgency': review_result.urgency
                }
            else:
                logger.error(f"Roll failed for {underlying}: {result.get('error')}")
                # If roll failed, consider just closing
                if result.get('partial'):
                    logger.warning(f"Partial roll - old position closed but new not opened")
                return None

        except Exception as e:
            logger.exception(f"Error executing roll for {underlying}: {e}")
            return None

    def _send_telegram_ai_action_blocked(self, symbol: str, action: str, result):
        """Notify that an AI-recommended action was blocked"""
        reasoning = escape_markdown(result.reasoning[:100])
        msg = f"⚠️ *AI Action Blocked* | {symbol}\n\n"
        msg += f"├── Recommended: {action}\n"
        msg += f"├── Urgency: {result.urgency}\n"
        msg += f"├── Reason: Daily limit reached\n"
        msg += f"└── AI: {reasoning}{'...' if len(result.reasoning) > 100 else ''}\n"
        msg += f"\n{get_et_time()}"
        send_telegram_sync(msg)

    def _send_telegram_ai_recommendation(self, symbol: str, result):
        """Send AI recommendation for medium urgency actions"""
        reasoning = escape_markdown(result.reasoning[:150])
        msg = f"🤖 *AI Recommendation* | {symbol}\n\n"
        msg += f"├── Action: {result.recommendation}\n"
        msg += f"├── Urgency: {result.urgency}\n"
        msg += f"└── Reason: {reasoning}{'...' if len(result.reasoning) > 150 else ''}\n"
        msg += f"\n{get_et_time()}"
        send_telegram_sync(msg)

    def _send_roll_notification(self, symbol: str, result: Dict):
        """Send roll confirmation to Telegram"""
        roll_cost = result.get('roll_cost', 0)
        cost_type = "DEBIT" if roll_cost > 0 else "CREDIT"

        msg = f"🔄 *AUTO-ROLL* | {symbol}\n\n"
        msg += f"├── Old: {result.get('old_contract', 'N/A')}\n"
        msg += f"├── New: {result.get('new_contract', 'N/A')}\n"
        msg += f"├── New Exp: {result.get('new_expiration', 'N/A')}\n"
        msg += f"├── Roll Cost: ${abs(roll_cost):.2f} {cost_type}\n"
        msg += f"└── P/L from Close: ${result.get('pnl_from_close', 0):.2f}\n"
        msg += f"\n{get_et_time()}"
        send_telegram_sync(msg)

    # =========================================================================
    # TELEGRAM NOTIFICATION HELPERS
    # =========================================================================

    def _send_telegram_critical(self, position, alert: Alert):
        """Send critical alert to Telegram"""
        contract_symbol = getattr(position, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol
        unrealized_plpc = float(getattr(position, 'unrealized_plpc', 0) or 0)
        expiration = getattr(position, 'expiration_date', '') or getattr(position, 'expiration', '')
        dte = self._calculate_dte(expiration)
        alert_msg = escape_markdown(alert.message)

        msg = f"🚨 *CRITICAL* | {underlying}\n\n"
        msg += f"Contract: {contract_symbol}\n"
        msg += f"├── Alert: {alert_msg}\n"
        msg += f"├── P/L: {unrealized_plpc:.1%}\n"
        msg += f"├── DTE: {dte}\n"
        msg += f"└── Action: Manual review needed\n"
        msg += f"\n{get_et_time()}"

        send_telegram_sync(msg)

    def _send_telegram_warning(self, position, alert: Alert):
        """Send warning alert to Telegram"""
        contract_symbol = getattr(position, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol
        unrealized_plpc = float(getattr(position, 'unrealized_plpc', 0) or 0)
        alert_msg = escape_markdown(alert.message)

        emoji = "⚠️" if alert.severity == "HIGH" else "📋"
        msg = f"{emoji} *{alert.severity}* | {underlying}\n\n"
        msg += f"├── {alert.alert_type}: {alert_msg}\n"
        msg += f"└── P/L: {unrealized_plpc:.1%}\n"
        msg += f"\n{get_et_time()}"

        send_telegram_sync(msg)

    def _send_telegram_portfolio_warning(self, alert: Alert):
        """Send portfolio-level warning"""
        alert_msg = escape_markdown(alert.message)
        emoji = "🚨" if alert.severity == "CRITICAL" else "⚠️"
        msg = f"{emoji} *Portfolio {alert.severity}*\n\n"
        msg += f"├── {alert.alert_type}\n"
        msg += f"└── {alert_msg}\n"
        msg += f"\n{get_et_time()}"

        send_telegram_sync(msg)

    def _send_exit_notification(self, position, reason: str, result: Dict):
        """Send exit confirmation to Telegram"""
        contract_symbol = getattr(position, 'symbol', 'UNKNOWN')
        underlying = contract_symbol[:4].rstrip('0123456789') if len(contract_symbol) > 10 else contract_symbol
        pnl = result.get('pnl', 0)
        pnl_pct = result.get('pnl_pct', 0)
        reason_escaped = escape_markdown(reason)

        emoji = "📈" if pnl > 0 else "📉"
        msg = f"{emoji} *AUTO-EXIT* | {underlying}\n\n"
        msg += f"├── Reason: {reason_escaped}\n"
        msg += f"├── P/L: ${pnl:,.2f} ({pnl_pct:.1%})\n"
        msg += f"├── Exit Price: ${result.get('exit_price', 0):.2f}\n"
        msg += f"└── Contract: {contract_symbol}\n"
        msg += f"\n{get_et_time()}"

        send_telegram_sync(msg)

    def _send_ai_review_summary(self, review_result, positions, actions_taken=None):
        """Send AI review summary to Telegram"""
        assessment = escape_markdown(review_result.overall_assessment.upper())
        msg = f"🤖 *Daily AI Review*\n\n"
        msg += f"├── Assessment: {assessment}\n"
        msg += f"├── Risk Score: {review_result.risk_score}/100\n"
        msg += f"└── Positions: {len(positions)}\n"

        if actions_taken:
            msg += f"\nActions Executed:\n"
            for action in actions_taken:
                if action['action'] == 'CLOSED':
                    msg += f"• CLOSED {action['symbol']} ({action['urgency']})\n"
                elif action['action'] == 'ROLLED':
                    new_exp = action.get('new_expiration', 'later')
                    msg += f"• ROLLED {action['symbol']} to {new_exp}\n"

        if review_result.recommendations:
            msg += f"\nPending Recommendations:\n"
            for rec in review_result.recommendations[:3]:
                msg += f"• {escape_markdown(rec)}\n"

        if review_result.risk_factors:
            msg += f"\nRisk Factors:\n"
            for factor in review_result.risk_factors[:3]:
                msg += f"• {escape_markdown(factor)}\n"

        msg += f"\n{get_et_time()}"

        send_telegram_sync(msg)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    monitor = OptionsMonitor()
    monitor.run()
