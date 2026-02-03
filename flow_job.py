"""
Automated Options Flow Job - Runs flow scan, analysis, and execution
Sends Telegram notifications at each step
"""
import os
import sys
import asyncio
import logging
from datetime import datetime
from typing import List, Dict

from dotenv import load_dotenv

load_dotenv()

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/home/ubuntu/momentum-agent/logs/flow.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")


async def send_telegram(message: str, parse_mode: str = "Markdown"):
    """Send message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        logger.warning("Telegram not configured, skipping notification")
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
                    logger.error(f"Telegram error: {await resp.text()}")
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")


def send_telegram_sync(message: str):
    """Synchronous wrapper for send_telegram"""
    asyncio.run(send_telegram(message))


def run_flow_scan_job() -> List:
    """
    Step 1: Run options flow scan
    Returns list of FlowSignal objects
    """
    from flow_scanner import run_flow_scan, get_flow_summary

    logger.info("Starting flow scan...")

    signals = run_flow_scan(
        min_premium=100000,
        min_vol_oi=1.0,
        min_score=8,
        limit=50,
    )

    if not signals:
        return []

    summary = get_flow_summary(signals)
    return signals, summary


def analyze_signals_job(signals: List, max_analyze: int = 5) -> List:
    """
    Step 2: Analyze top signals with Claude
    Returns list of EnrichedFlowSignal objects
    """
    from flow_analyzer import analyze_flow_signals

    logger.info(f"Analyzing top {max_analyze} signals with Claude...")

    enriched = analyze_flow_signals(signals[:max_analyze], max_analyze=max_analyze)
    return enriched


def execute_trades_job(enriched_signals: List) -> List[Dict]:
    """
    Step 3: Execute BUY recommendations
    Returns list of execution results
    """
    from options_executor import execute_flow_trade

    results = []

    buy_signals = [e for e in enriched_signals if e.recommendation == "BUY"]

    if not buy_signals:
        logger.info("No BUY recommendations to execute")
        return results

    logger.info(f"Executing {len(buy_signals)} BUY recommendations...")

    for enriched in buy_signals:
        try:
            result = execute_flow_trade(enriched)
            result["symbol"] = enriched.signal.symbol
            results.append(result)

            if result.get("success"):
                logger.info(f"  Executed {enriched.signal.symbol}: {result.get('contract_symbol')}")
            else:
                logger.warning(f"  Failed {enriched.signal.symbol}: {result.get('error')}")

        except Exception as e:
            logger.error(f"  Error executing {enriched.signal.symbol}: {e}")
            results.append({
                "symbol": enriched.signal.symbol,
                "success": False,
                "error": str(e)
            })

    return results


def check_options_exits_job() -> List[Dict]:
    """
    Step 4: Check existing positions for exit conditions
    Returns list of closed positions
    """
    from options_executor import (
        get_options_positions,
        close_options_position,
        check_expiration_risk,
    )
    from config import OPTIONS_CONFIG

    closed = []
    positions = get_options_positions()

    if not positions:
        return closed

    profit_target = OPTIONS_CONFIG.get("profit_target_pct", 0.50)
    stop_loss = OPTIONS_CONFIG.get("stop_loss_pct", 0.50)

    logger.info(f"Checking {len(positions)} options positions for exits...")

    for pos in positions:
        pnl_pct = pos.unrealized_plpc
        reason = None

        # Check profit target
        if pnl_pct >= profit_target:
            reason = f"profit_target_{pnl_pct*100:.0f}pct"
            logger.info(f"  {pos.symbol}: Hit profit target +{pnl_pct*100:.1f}%")

        # Check stop loss
        elif pnl_pct <= -stop_loss:
            reason = f"stop_loss_{pnl_pct*100:.0f}pct"
            logger.info(f"  {pos.symbol}: Hit stop loss {pnl_pct*100:.1f}%")

        if reason:
            try:
                result = close_options_position(pos.contract_symbol, reason)
                result["symbol"] = pos.symbol
                closed.append(result)
            except Exception as e:
                logger.error(f"  Error closing {pos.symbol}: {e}")

    # Check DTE alerts for critical positions
    dte_alerts = check_expiration_risk()
    for alert in dte_alerts:
        if alert["severity"] == "CRITICAL" and alert["dte"] <= 0:
            pos = alert["position"]
            logger.info(f"  {pos.symbol}: EXPIRED - closing")
            try:
                result = close_options_position(pos.contract_symbol, "expired")
                result["symbol"] = pos.symbol
                closed.append(result)
            except Exception as e:
                logger.error(f"  Error closing expired {pos.symbol}: {e}")

    return closed


def run_full_flow_job():
    """
    Main job: Run complete flow scan, analysis, and execution cycle
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ========== STEP 0: START NOTIFICATION ==========
    send_telegram_sync(f"🔄 *Options Flow Job Started*\n`{timestamp}`")

    try:
        # ========== STEP 1: FLOW SCAN ==========
        logger.info("=" * 50)
        logger.info("STEP 1: Flow Scan")
        logger.info("=" * 50)

        result = run_flow_scan_job()

        if not result:
            send_telegram_sync("📭 *Flow Scan Complete*\nNo high-conviction signals found (score >= 8)")
            logger.info("No signals found, exiting")
            return

        signals, summary = result

        msg = f"📊 *Flow Scan Complete*\n\n"
        msg += f"Found *{summary['count']}* signals (score >= 8)\n"
        msg += f"├── Total Premium: ${summary['total_premium']:,.0f}\n"
        msg += f"├── Bullish: {summary['bullish_count']} | Bearish: {summary['bearish_count']}\n"
        msg += f"├── Sweeps: {summary['sweeps']} | Floor: {summary['floor_trades']}\n"
        msg += f"└── Avg Score: {summary['avg_score']:.1f}\n\n"
        msg += "*Top 5:*\n"

        for i, s in enumerate(signals[:5], 1):
            emoji = "📈" if s.sentiment == "bullish" else "📉"
            msg += f"{i}. {emoji} {s.symbol} {s.option_type.upper()} ${s.strike} | Score: {s.score}\n"

        send_telegram_sync(msg)

        # ========== STEP 2: CLAUDE ANALYSIS ==========
        logger.info("=" * 50)
        logger.info("STEP 2: Claude Analysis")
        logger.info("=" * 50)

        send_telegram_sync("🧠 *Analyzing signals with Claude...*")

        enriched = analyze_signals_job(signals, max_analyze=5)

        if not enriched:
            send_telegram_sync("❌ *Analysis Failed*\nCould not analyze signals")
            return

        # Build analysis summary
        msg = "🧠 *Claude Analysis Complete*\n\n"

        for e in enriched:
            emoji = "🟢" if e.recommendation == "BUY" else "🟡" if e.recommendation == "WATCH" else "🔴"
            msg += f"{emoji} *{e.signal.symbol}* - {e.recommendation}\n"
            msg += f"   Conviction: {e.conviction:.0%} | Score: {e.signal.score}\n"
            if e.thesis:
                # Truncate thesis
                thesis_short = e.thesis[:150] + "..." if len(e.thesis) > 150 else e.thesis
                msg += f"   _{thesis_short}_\n"
            msg += "\n"

        buy_count = sum(1 for e in enriched if e.recommendation == "BUY")
        msg += f"*BUY Recommendations: {buy_count}*"

        send_telegram_sync(msg)

        # ========== STEP 3: EXECUTE TRADES ==========
        logger.info("=" * 50)
        logger.info("STEP 3: Execute Trades")
        logger.info("=" * 50)

        if buy_count == 0:
            send_telegram_sync("⏸️ *No Trades to Execute*\nNo BUY recommendations from Claude")
        else:
            send_telegram_sync(f"⚡ *Executing {buy_count} trades...*")

            exec_results = execute_trades_job(enriched)

            # Build execution summary
            success_count = sum(1 for r in exec_results if r.get("success"))
            fail_count = len(exec_results) - success_count

            msg = f"💼 *Execution Complete*\n\n"
            msg += f"✅ Success: {success_count} | ❌ Failed: {fail_count}\n\n"

            for r in exec_results:
                if r.get("success"):
                    msg += f"✅ *{r['symbol']}*\n"
                    msg += f"   Contract: `{r.get('contract_symbol', 'N/A')}`\n"
                    msg += f"   Qty: {r.get('quantity', 0)} @ ${r.get('fill_price', 0):.2f}\n"
                    msg += f"   Cost: ${r.get('estimated_cost', 0):,.2f}\n"
                    if r.get('entry_greeks'):
                        g = r['entry_greeks']
                        msg += f"   Greeks: Δ={g.get('delta', 0):.2f} Θ=${g.get('theta', 0):.2f}\n"
                else:
                    msg += f"❌ *{r['symbol']}*: {r.get('error', 'Unknown error')}\n"
                msg += "\n"

            send_telegram_sync(msg)

        # ========== STEP 4: CHECK EXITS ==========
        logger.info("=" * 50)
        logger.info("STEP 4: Check Exits")
        logger.info("=" * 50)

        closed = check_options_exits_job()

        if closed:
            msg = f"🚪 *Positions Closed*\n\n"
            for c in closed:
                if c.get("success"):
                    emoji = "🟢" if c.get("pnl", 0) >= 0 else "🔴"
                    msg += f"{emoji} *{c['symbol']}*\n"
                    msg += f"   P/L: ${c.get('pnl', 0):,.2f} ({c.get('pnl_pct', 0)*100:.1f}%)\n"
                    msg += f"   Reason: {c.get('reason', 'N/A')}\n\n"
                else:
                    msg += f"❌ *{c.get('symbol', 'Unknown')}*: {c.get('error', 'Failed')}\n\n"

            send_telegram_sync(msg)

        # ========== STEP 5: FINAL SUMMARY ==========
        logger.info("=" * 50)
        logger.info("STEP 5: Final Summary")
        logger.info("=" * 50)

        from options_executor import get_options_summary

        summary = get_options_summary()

        msg = f"✅ *Flow Job Complete*\n`{datetime.now().strftime('%H:%M:%S')}`\n\n"
        msg += f"*Portfolio Status:*\n"
        msg += f"├── Positions: {summary['count']}\n"
        msg += f"├── Total Value: ${summary['total_value']:,.2f}\n"
        msg += f"├── Unrealized P/L: ${summary['total_pnl']:,.2f} ({summary['pnl_pct']:.1f}%)\n"
        msg += f"└── Options % of Portfolio: {summary['portfolio_pct']:.1f}%"

        send_telegram_sync(msg)

    except Exception as e:
        logger.exception(f"Flow job failed: {e}")
        send_telegram_sync(f"❌ *Flow Job Failed*\n\n`{str(e)}`")
        raise


def run_exit_check_job():
    """
    Standalone job to check exits only (run more frequently)
    """
    timestamp = datetime.now().strftime("%H:%M")

    try:
        from options_executor import get_options_positions

        positions = get_options_positions()

        if not positions:
            logger.info("No options positions to check")
            return

        logger.info(f"Checking {len(positions)} positions for exits...")

        closed = check_options_exits_job()

        if closed:
            msg = f"🚪 *Auto-Exit Triggered* `{timestamp}`\n\n"
            for c in closed:
                if c.get("success"):
                    emoji = "🟢" if c.get("pnl", 0) >= 0 else "🔴"
                    msg += f"{emoji} *{c['symbol']}*\n"
                    msg += f"   P/L: ${c.get('pnl', 0):,.2f} ({c.get('pnl_pct', 0)*100:.1f}%)\n"
                    msg += f"   Reason: {c.get('reason', 'N/A')}\n\n"

            send_telegram_sync(msg)

    except Exception as e:
        logger.error(f"Exit check failed: {e}")


def run_dte_alert_job():
    """
    Standalone job to send DTE alerts (run daily)
    """
    try:
        from options_executor import check_expiration_risk, suggest_roll

        alerts = check_expiration_risk()

        if not alerts:
            logger.info("No DTE alerts")
            return

        # Only send alerts for HIGH and CRITICAL
        urgent = [a for a in alerts if a["severity"] in ["HIGH", "CRITICAL"]]

        if not urgent:
            return

        msg = "⏰ *DTE Alert*\n\n"

        for alert in urgent:
            pos = alert["position"]
            severity_emoji = "🔴" if alert["severity"] == "CRITICAL" else "🟠"

            msg += f"{severity_emoji} *{pos.symbol}* {pos.option_type.upper()} ${pos.strike}\n"
            msg += f"   DTE: {alert['dte']} | {alert['message']}\n"

            if alert["action"] in ["close_or_roll", "close"]:
                roll = suggest_roll(pos)
                if roll.get("can_roll"):
                    cost = roll['roll_cost']
                    cost_str = f"${cost:.2f} debit" if cost > 0 else f"${abs(cost):.2f} credit"
                    msg += f"   Roll suggestion: {roll['new_expiration']} ({cost_str})\n"

            msg += "\n"

        msg += "Use `/closeoption CONTRACT` or `/expirations` for details"

        send_telegram_sync(msg)

    except Exception as e:
        logger.error(f"DTE alert job failed: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python flow_job.py [full|exits|dte]")
        print("  full  - Run complete flow scan, analysis, and execution")
        print("  exits - Check existing positions for exit conditions")
        print("  dte   - Send DTE alerts for expiring positions")
        sys.exit(1)

    job_type = sys.argv[1].lower()

    if job_type == "full":
        run_full_flow_job()
    elif job_type == "exits":
        run_exit_check_job()
    elif job_type == "dte":
        run_dte_alert_job()
    else:
        print(f"Unknown job type: {job_type}")
        sys.exit(1)
