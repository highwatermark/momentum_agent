"""
Flow Listener Service - Real-time options flow monitoring and execution

Polls Unusual Whales API every 60 seconds and uses Claude AI to validate
high-conviction signals for automatic execution.

Three-layer safety architecture:
1. Pre-filter (premium, dedupe)
2. Claude validation (profit-focused decision)
3. Safety gate (hard limits override Claude)
4. Options executor (existing safety + execution)
"""
import os
import sys
import time
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, asdict

import pytz
from dotenv import load_dotenv

load_dotenv()

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    FLOW_LISTENER_CONFIG,
    OPTIONS_CONFIG,
    ANTHROPIC_API_KEY,
)
from flow_scanner import UnusualWhalesClient, FlowSignal, parse_flow_alert, score_flow_signal
from options_agent import (
    FlowSignalInput,
    FlowValidationInput,
    FlowValidationResult,
    validate_flow_signals,
    review_portfolio,
    PortfolioReviewInput,
)
from options_executor import (
    get_options_positions,
    get_portfolio_greeks,
    get_account_info,
    execute_flow_trade,
    check_earnings_blackout,
    can_add_position,
)
from db import (
    init_flow_listener_tables,
    get_flow_listener_state,
    update_flow_listener_state,
    increment_daily_execution_count,
    reset_daily_execution_count,
    add_seen_signal_id,
    is_signal_seen,
    update_flow_signal_action,
    log_flow_signal,
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/home/ubuntu/momentum-agent/logs/flow_listener.log"),
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
        max_errors = FLOW_LISTENER_CONFIG["max_consecutive_errors"]

        if self.consecutive_errors >= max_errors:
            self.is_open = True
            logger.error(f"Circuit breaker OPEN after {self.consecutive_errors} errors")
            send_telegram_sync(f"🔴 *Circuit Breaker OPEN*\nAuto-execution paused after {self.consecutive_errors} errors")

    def record_success(self):
        if self.consecutive_errors > 0:
            self.consecutive_errors = 0
        if self.is_open:
            self.is_open = False
            logger.info("Circuit breaker CLOSED - resuming normal operation")
            send_telegram_sync("🟢 *Circuit Breaker CLOSED*\nResuming normal operation")

    def can_execute(self) -> bool:
        if not self.is_open:
            return True

        # Check if cooldown has expired
        cooldown = FLOW_LISTENER_CONFIG["circuit_breaker_cooldown_seconds"]
        if self.last_error_time:
            elapsed = (datetime.now() - self.last_error_time).total_seconds()
            if elapsed > cooldown:
                self.is_open = False
                self.consecutive_errors = 0
                logger.info("Circuit breaker CLOSED - cooldown expired")
                return True

        return False


# ============================================================================
# MARKET HOURS CHECK
# ============================================================================

def is_market_hours() -> bool:
    """Check if current time is within market hours (ET)"""
    et = pytz.timezone('America/New_York')
    now = datetime.now(et)  # Get current time in ET timezone

    # Skip weekends
    if now.weekday() >= 5:
        return False

    config = FLOW_LISTENER_CONFIG
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


# ============================================================================
# CONTEXT GATHERING
# ============================================================================

def get_market_context() -> Dict:
    """Get current market context (SPY, VIX, etc.)"""
    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

        client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

        # Get latest quotes for SPY
        quote_request = StockLatestQuoteRequest(symbol_or_symbols=["SPY"])
        quotes = client.get_stock_latest_quote(quote_request)

        spy_price = 0
        if "SPY" in quotes:
            q = quotes["SPY"]
            spy_price = (float(q.bid_price) + float(q.ask_price)) / 2

        # Get SPY daily change (from yesterday's close)
        bars_request = StockBarsRequest(
            symbol_or_symbols=["SPY"],
            timeframe=TimeFrame.Day,
            start=datetime.now() - timedelta(days=5),
            limit=2
        )
        bars = client.get_stock_bars(bars_request)

        spy_change_pct = 0
        spy_trend = "sideways"
        if "SPY" in bars and len(bars["SPY"]) >= 2:
            prev_close = float(bars["SPY"][-2].close)
            if spy_price > 0 and prev_close > 0:
                spy_change_pct = (spy_price - prev_close) / prev_close

            # Determine trend from recent bars
            if len(bars["SPY"]) >= 2:
                recent_closes = [float(b.close) for b in bars["SPY"]]
                if recent_closes[-1] > recent_closes[0]:
                    spy_trend = "uptrend"
                elif recent_closes[-1] < recent_closes[0]:
                    spy_trend = "downtrend"

        # VIX - try to get from UW API or use default
        vix_level = 18.0  # Default
        try:
            uw_client = UnusualWhalesClient()
            vix_data = uw_client.get_stock_info("VIX")
            if vix_data and "price" in vix_data:
                vix_level = float(vix_data["price"])
        except Exception:
            pass

        return {
            "spy_price": spy_price,
            "spy_change_pct": spy_change_pct,
            "spy_trend": spy_trend,
            "vix_level": vix_level,
            "sector_performance": {},  # Could be expanded
            "current_time": get_et_time(),
        }

    except Exception as e:
        logger.error(f"Error getting market context: {e}")
        return {
            "spy_price": 0,
            "spy_change_pct": 0,
            "spy_trend": "unknown",
            "vix_level": 20,
            "sector_performance": {},
            "current_time": get_et_time(),
        }


def get_portfolio_context() -> Dict:
    """Get current portfolio context"""
    try:
        account = get_account_info()
        positions = get_options_positions()
        greeks = get_portfolio_greeks()

        equity = account.get("equity", 100000)
        options_value = sum(getattr(pos, 'market_value', 0) for pos in positions)
        options_exposure_pct = (options_value / equity * 100) if equity > 0 else 0

        # Format positions for Claude
        position_dicts = []
        for pos in positions:
            position_dicts.append({
                "symbol": getattr(pos, 'symbol', 'N/A'),
                "contract_symbol": getattr(pos, 'contract_symbol', 'N/A'),
                "option_type": getattr(pos, 'option_type', 'unknown'),
                "strike": getattr(pos, 'strike', 0),
                "unrealized_plpc": getattr(pos, 'unrealized_plpc', 0),
                "delta": 0,  # Would need to fetch
            })

        # Get risk score
        risk_score = 0
        risk_assessment = "healthy"
        try:
            # Build portfolio review input for risk assessment
            from options_agent import PortfolioReviewInput
            cash_available = account.get("cash", equity - options_value)
            portfolio_input = PortfolioReviewInput(
                account_equity=equity,
                cash_available=cash_available,
                options_exposure=options_value,
                options_exposure_pct=options_exposure_pct,
                net_delta=greeks.get("net_delta", 0),
                total_gamma=greeks.get("total_gamma", 0),
                daily_theta=greeks.get("daily_theta", 0),
                total_vega=greeks.get("total_vega", 0),
                positions=position_dicts,
                sector_allocation={},
                spy_price=0,
                spy_change_1d=0,
                spy_change_5d=0,
                vix_level=20,
                max_single_position_pct=0,
                positions_expiring_soon=0
            )
            review_result = review_portfolio(portfolio_input, use_agent=False)
            risk_score = review_result.risk_score
            risk_assessment = review_result.overall_assessment
        except Exception as e:
            logger.warning(f"Could not get risk score: {e}")

        # Calculate available capital for new position
        available = equity * OPTIONS_CONFIG.get("position_size_pct", 0.02)

        return {
            "equity": equity,
            "options_positions": position_dicts,
            "position_count": len(positions),
            "max_positions": OPTIONS_CONFIG.get("max_options_positions", 4),
            "net_delta": greeks.get("net_delta", 0),
            "daily_theta": greeks.get("daily_theta", 0),
            "options_exposure_pct": options_exposure_pct,
            "risk_score": risk_score,
            "risk_assessment": risk_assessment,
            "available_capital": available,
            "underlying_symbols": [getattr(p, 'symbol', '') for p in positions],
        }

    except Exception as e:
        logger.error(f"Error getting portfolio context: {e}")
        return {
            "equity": 100000,
            "options_positions": [],
            "position_count": 0,
            "max_positions": 4,
            "net_delta": 0,
            "daily_theta": 0,
            "options_exposure_pct": 0,
            "risk_score": 0,
            "risk_assessment": "unknown",
            "available_capital": 2000,
            "underlying_symbols": [],
        }


# ============================================================================
# SAFETY GATE (Layer 3)
# ============================================================================

def safety_gate_check(signal: FlowSignal, portfolio: Dict) -> Tuple[bool, List[str]]:
    """
    Layer 3 safety checks - hard limits that override Claude.

    Returns (can_execute, list of block reasons)
    """
    block_reasons = []
    config = FLOW_LISTENER_CONFIG

    # 1. Master switch
    if not config["enable_auto_execute"]:
        block_reasons.append("Auto-execute disabled")

    # 2. Daily limit
    state = get_flow_listener_state()
    if state.get("daily_execution_count", 0) >= config["max_executions_per_day"]:
        block_reasons.append(f"Daily limit ({config['max_executions_per_day']}) reached")

    # 3. Position limits
    if portfolio["position_count"] >= portfolio["max_positions"]:
        block_reasons.append(f"Max positions ({portfolio['max_positions']}) reached")

    # 4. Delta limits
    equity = portfolio.get("equity", 100000)
    delta_per_100k = abs(portfolio.get("net_delta", 0)) / (equity / 100000) if equity > 0 else 0
    if delta_per_100k > config["max_delta_per_100k"]:
        block_reasons.append(f"Delta too high: {delta_per_100k:.0f} per $100K")

    # 5. Theta limits
    daily_theta_pct = abs(portfolio.get("daily_theta", 0)) / equity if equity > 0 else 0
    if daily_theta_pct > config["max_theta_pct"]:
        block_reasons.append(f"Theta decay too high: {daily_theta_pct:.2%}/day")

    # 6. Risk score
    if portfolio.get("risk_score", 0) > config["max_risk_score"]:
        block_reasons.append(f"Risk score {portfolio['risk_score']} > {config['max_risk_score']}")

    # 7. Options exposure
    if portfolio.get("options_exposure_pct", 0) >= OPTIONS_CONFIG.get("max_portfolio_risk_options", 0.10) * 100:
        block_reasons.append(f"Max options exposure reached")

    # 8. Duplicate position
    if signal.symbol in portfolio.get("underlying_symbols", []):
        block_reasons.append(f"Already hold {signal.symbol}")

    # 9. Earnings blackout
    blocked, earnings_date = check_earnings_blackout(signal.symbol)
    if blocked:
        block_reasons.append(f"Earnings blackout: {earnings_date}")

    # 10. Sector concentration (via can_add_position)
    can_add, reason = can_add_position(signal.symbol)
    if not can_add:
        block_reasons.append(f"Concentration: {reason}")

    return len(block_reasons) == 0, block_reasons


# ============================================================================
# FLOW LISTENER CLASS
# ============================================================================

class FlowListener:
    """Real-time flow monitoring and execution service"""

    def __init__(self):
        self.uw_client = UnusualWhalesClient()
        self.circuit_breaker = CircuitBreaker()
        self.seen_signal_ids: Set[str] = set()
        self.last_check_time = datetime.now(timezone.utc) - timedelta(seconds=60)
        self.daily_execution_count = 0
        self.last_reset_date = datetime.now().date()

        # Initialize database tables
        init_flow_listener_tables()

        # Load state from database
        self._load_state()

    def _load_state(self):
        """Load state from database"""
        state = get_flow_listener_state()
        if state.get("last_check_time"):
            try:
                self.last_check_time = datetime.fromisoformat(state["last_check_time"])
            except Exception:
                pass
        self.daily_execution_count = state.get("daily_execution_count", 0)
        if state.get("last_reset_date"):
            try:
                self.last_reset_date = datetime.fromisoformat(state["last_reset_date"]).date()
            except Exception:
                pass
        self.seen_signal_ids = state.get("seen_signal_ids", set())

    def _save_state(self):
        """Save state to database"""
        update_flow_listener_state(
            last_check_time=self.last_check_time.isoformat(),
            daily_execution_count=self.daily_execution_count,
            last_reset_date=self.last_reset_date.isoformat(),
            seen_signal_ids=self.seen_signal_ids,
        )

    def _check_daily_reset(self):
        """Reset daily counters at midnight"""
        today = datetime.now().date()
        if today > self.last_reset_date:
            logger.info(f"New day detected, resetting daily counters")
            self.daily_execution_count = 0
            self.last_reset_date = today
            self.seen_signal_ids.clear()
            reset_daily_execution_count(today.isoformat())

    def run(self):
        """Main run loop"""
        logger.info("=" * 60)
        logger.info("FLOW LISTENER SERVICE STARTING")
        logger.info("=" * 60)

        send_telegram_sync(
            f"🎯 *Flow Listener Started*\n"
            f"Polling every {FLOW_LISTENER_CONFIG['poll_interval_seconds']}s\n"
            f"Auto-execute: {'ON' if FLOW_LISTENER_CONFIG['enable_auto_execute'] else 'OFF'}\n"
            f"Max daily: {FLOW_LISTENER_CONFIG['max_executions_per_day']}"
        )

        while True:
            try:
                cycle_start = time.time()

                self._check_daily_reset()

                if is_market_hours():
                    self._poll_cycle()
                else:
                    logger.debug("Outside market hours, sleeping...")

                # Save state periodically
                self._save_state()

                # Calculate sleep time
                cycle_time = time.time() - cycle_start
                sleep_time = max(0, FLOW_LISTENER_CONFIG["poll_interval_seconds"] - cycle_time)

                if cycle_time > 5:  # Only log if cycle took significant time
                    logger.info(f"Cycle completed in {cycle_time:.1f}s, sleeping {sleep_time:.1f}s")

                time.sleep(sleep_time)

            except KeyboardInterrupt:
                logger.info("Shutting down...")
                self._save_state()
                send_telegram_sync("🛑 *Flow Listener Stopped*")
                break
            except Exception as e:
                logger.exception(f"Error in main loop: {e}")
                self.circuit_breaker.record_error()
                time.sleep(10)  # Brief pause on error

    def _poll_cycle(self):
        """Single poll cycle"""
        config = FLOW_LISTENER_CONFIG

        # Check circuit breaker
        if not self.circuit_breaker.can_execute():
            logger.warning("Circuit breaker open, skipping cycle")
            return

        # Phase 1: Pre-fetch context (parallel in real impl)
        logger.debug("Fetching context...")
        market_ctx = get_market_context()
        portfolio_ctx = get_portfolio_context()

        # Fetch new flow alerts
        newer_than = self.last_check_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.debug(f"Fetching alerts newer than {newer_than}")

        try:
            alerts = self.uw_client.get_flow_alerts(
                min_premium=config["min_premium"],
                newer_than=newer_than,
                limit=config["max_signals_per_cycle"] * 2,  # Fetch extra for filtering
            )
        except Exception as e:
            logger.error(f"UW API error: {e}")
            self.circuit_breaker.record_error()
            return

        if not alerts:
            logger.debug("No new alerts")
            self.last_check_time = datetime.now(timezone.utc)
            return

        logger.info(f"Fetched {len(alerts)} raw alerts")

        # Phase 2: Pre-filter and score
        filtered_signals = []
        for alert in alerts:
            signal = parse_flow_alert(alert)
            if not signal:
                continue

            # Score the signal based on flow factors
            signal = score_flow_signal(signal)

            # Skip excluded symbols (index options)
            if signal.symbol in config["excluded_symbols"]:
                continue

            # Premium filter
            if signal.premium < config["min_premium"]:
                continue

            # Dedupe
            if signal.id in self.seen_signal_ids or is_signal_seen(signal.id):
                continue

            filtered_signals.append(signal)

            if len(filtered_signals) >= config["max_signals_per_cycle"]:
                break

        if not filtered_signals:
            logger.debug("No signals passed pre-filter")
            self.last_check_time = datetime.now(timezone.utc)
            return

        logger.info(f"{len(filtered_signals)} signals passed pre-filter")

        # Mark signals as seen
        for sig in filtered_signals:
            self.seen_signal_ids.add(sig.id)
            add_seen_signal_id(sig.id)

        # Phase 3: Claude validation
        signal_inputs = [
            FlowSignalInput(
                signal_id=sig.id,
                symbol=sig.symbol,
                strike=sig.strike,
                expiration=sig.expiration,
                option_type=sig.option_type,
                premium=sig.premium,
                size=sig.size,
                vol_oi_ratio=sig.vol_oi_ratio,
                is_sweep=sig.is_sweep,
                is_ask_side=sig.is_ask_side,
                is_floor=sig.is_floor,
                is_opening=sig.is_opening,
                is_otm=sig.is_otm,
                underlying_price=sig.underlying_price,
                sentiment=sig.sentiment,
            )
            for sig in filtered_signals
        ]

        validation_input = FlowValidationInput(
            signals=signal_inputs,
            spy_price=market_ctx["spy_price"],
            spy_change_pct=market_ctx["spy_change_pct"],
            spy_trend=market_ctx["spy_trend"],
            vix_level=market_ctx["vix_level"],
            sector_performance=market_ctx["sector_performance"],
            current_time=market_ctx["current_time"],
            equity=portfolio_ctx["equity"],
            options_positions=portfolio_ctx["options_positions"],
            net_delta=portfolio_ctx["net_delta"],
            daily_theta=portfolio_ctx["daily_theta"],
            options_exposure_pct=portfolio_ctx["options_exposure_pct"],
            risk_score=portfolio_ctx["risk_score"],
            risk_assessment=portfolio_ctx["risk_assessment"],
            available_capital=portfolio_ctx["available_capital"],
            position_count=portfolio_ctx["position_count"],
            max_positions=portfolio_ctx["max_positions"],
        )

        logger.info("Calling Claude for validation...")
        validation_results = validate_flow_signals(validation_input)

        if not validation_results:
            logger.warning("Claude validation returned no results")
            self.last_check_time = datetime.now(timezone.utc)
            return

        # Phase 4: Process results
        for result in validation_results:
            # Find matching signal
            signal = next((s for s in filtered_signals if s.id == result.signal_id), None)
            if not signal:
                continue

            # Log to database and capture the database ID
            try:
                db_id = log_flow_signal({
                    "signal_id": signal.id,
                    "timestamp": signal.timestamp,
                    "symbol": signal.symbol,
                    "strike": signal.strike,
                    "expiration": signal.expiration,
                    "option_type": signal.option_type,
                    "premium": signal.premium,
                    "size": signal.size,
                    "volume": signal.volume,
                    "open_interest": signal.open_interest,
                    "vol_oi_ratio": signal.vol_oi_ratio,
                    "is_sweep": signal.is_sweep,
                    "is_ask_side": signal.is_ask_side,
                    "is_floor": signal.is_floor,
                    "is_opening": signal.is_opening,
                    "is_otm": signal.is_otm,
                    "underlying_price": signal.underlying_price,
                    "sentiment": signal.sentiment,
                    "score": result.conviction,
                })
                # Set the database ID on the signal for linking to trades
                signal.db_id = db_id
            except Exception as e:
                logger.error(f"Error logging signal: {e}")

            # Route based on recommendation
            if result.recommendation == "EXECUTE" and result.conviction >= config["min_conviction_execute"]:
                self._handle_execute(signal, result, portfolio_ctx)
            elif result.recommendation == "ALERT" or result.conviction >= config["min_conviction_alert"]:
                self._handle_alert(signal, result, blocked=False)
            else:
                self._handle_skip(signal, result)

        # Update checkpoint
        self.last_check_time = datetime.now(timezone.utc)
        self.circuit_breaker.record_success()

    def _handle_execute(self, signal: FlowSignal, result: FlowValidationResult, portfolio: Dict):
        """Handle EXECUTE recommendation"""
        logger.info(f"Processing EXECUTE for {signal.symbol}")

        # Safety gate check
        can_execute, block_reasons = safety_gate_check(signal, portfolio)

        if not can_execute:
            logger.warning(f"Safety gate blocked: {block_reasons}")
            self._handle_alert(signal, result, blocked=True, block_reasons=block_reasons)
            return

        # Create enriched signal for executor
        from flow_analyzer import EnrichedFlowSignal

        enriched = EnrichedFlowSignal(
            signal=signal,
            recommendation="BUY",
            conviction=result.conviction / 100.0,
            thesis=result.thesis,
        )

        # Execute trade
        logger.info(f"Executing trade for {signal.symbol}...")
        exec_result = execute_flow_trade(enriched)

        if exec_result.get("success"):
            self.daily_execution_count += 1
            increment_daily_execution_count()

            # Update signal action
            update_flow_signal_action(
                signal.id,
                "executed",
                json.dumps(asdict(result))
            )

            # Send notification
            self._send_execution_notification(signal, result, exec_result)

            logger.info(f"Successfully executed {signal.symbol}")

        else:
            error = exec_result.get("error", "Unknown error")
            logger.error(f"Execution failed: {error}")

            # Downgrade to alert
            self._handle_alert(
                signal, result,
                blocked=True,
                block_reasons=[f"Execution failed: {error}"]
            )

    def _handle_alert(
        self,
        signal: FlowSignal,
        result: FlowValidationResult,
        blocked: bool = False,
        block_reasons: List[str] = None
    ):
        """Handle ALERT recommendation or blocked EXECUTE"""
        logger.info(f"Sending alert for {signal.symbol} (blocked={blocked})")

        action = "blocked" if blocked else "alert_sent"
        update_flow_signal_action(signal.id, action, json.dumps(asdict(result)))

        # Build message
        emoji = "📈" if signal.sentiment == "bullish" else "📉"
        sweep_tag = " 🔥SWEEP" if signal.is_sweep else ""
        floor_tag = " 🏦FLOOR" if signal.is_floor else ""

        if blocked:
            header = f"⚠️ *BLOCKED* | {result.conviction}%"
        else:
            header = f"👀 *FLOW ALERT* | {result.conviction}%"

        thesis = escape_markdown(result.thesis[:150])
        msg = f"{header}\n\n"
        msg += f"{emoji} *{signal.symbol}* {signal.option_type.upper()} ${signal.strike}{sweep_tag}{floor_tag}\n"
        msg += f"├── Premium: ${signal.premium:,.0f}\n"
        msg += f"├── Vol/OI: {signal.vol_oi_ratio:.1f}x\n"
        msg += f"├── Exp: {signal.expiration[:10]}\n"
        msg += f"└── Thesis: {thesis}{'...' if len(result.thesis) > 150 else ''}\n"

        if blocked and block_reasons:
            reasons = ', '.join(escape_markdown(r) for r in block_reasons)
            msg += f"\nBlocked: {reasons}\n"

        msg += f"\n{get_et_time()}"

        send_telegram_sync(msg)

    def _handle_skip(self, signal: FlowSignal, result: FlowValidationResult):
        """Handle SKIP recommendation"""
        logger.debug(f"Skipping {signal.symbol}: {result.thesis[:100]}")
        update_flow_signal_action(signal.id, "skipped", json.dumps(asdict(result)))

    def _send_execution_notification(
        self,
        signal: FlowSignal,
        result: FlowValidationResult,
        exec_result: Dict
    ):
        """Send Telegram notification for successful execution"""
        emoji = "📈" if signal.sentiment == "bullish" else "📉"
        sweep_tag = " 🔥SWEEP" if signal.is_sweep else ""

        thesis = escape_markdown(result.thesis[:100])
        msg = f"🚀 *AUTO-EXECUTED* | {result.conviction}%\n\n"
        msg += f"{emoji} *{signal.symbol}* {signal.option_type.upper()} ${signal.strike}{sweep_tag}\n"
        msg += f"├── Premium: ${signal.premium:,.0f}\n"
        msg += f"├── Contract: {exec_result.get('contract_symbol', 'N/A')}\n"
        msg += f"├── Qty: {exec_result.get('quantity', 0)} @ ${exec_result.get('fill_price', 0):.2f}\n"
        msg += f"├── Cost: ${exec_result.get('estimated_cost', 0):,.2f}\n"
        msg += f"└── Thesis: {thesis}{'...' if len(result.thesis) > 100 else ''}\n"

        if exec_result.get('entry_greeks'):
            g = exec_result['entry_greeks']
            msg += f"\nGreeks: D={g.get('delta', 0):.2f} T=${g.get('theta', 0):.2f}"

        msg += f"\n\n{get_et_time()}"

        send_telegram_sync(msg)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    listener = FlowListener()
    listener.run()
