"""
Position Monitor - Detects early reversal signals and alerts via Telegram
"""
import os
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, get_runtime_config
from executor import get_positions, close_position
from db import get_trade_by_symbol
import pytz

load_dotenv()

# Market hours (Eastern Time)
MARKET_TZ = pytz.timezone("US/Eastern")
MARKET_OPEN = 9  # 9:30 AM, but we check from 10:00 AM
MARKET_CLOSE = 16  # 4:00 PM


def is_market_hours() -> bool:
    """Check if current time is during US market hours (Mon-Fri 9:30 AM - 4:00 PM ET)"""
    now_et = datetime.now(MARKET_TZ)
    # Weekday check (0=Monday, 4=Friday)
    if now_et.weekday() > 4:
        return False
    # Hour check (9:30 AM to 4:00 PM)
    if now_et.hour < MARKET_OPEN or now_et.hour >= MARKET_CLOSE:
        return False
    if now_et.hour == MARKET_OPEN and now_et.minute < 30:
        return False
    return True


# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")


def get_data_client() -> StockHistoricalDataClient:
    """Initialize Alpaca data client"""
    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def get_historical_bars(client: StockHistoricalDataClient, symbol: str, days: int = 30) -> list:
    """Fetch historical daily bars for a symbol"""
    end = datetime.now()
    start = end - timedelta(days=days + 10)  # Buffer for weekends/holidays

    try:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end
        )
        bars = client.get_stock_bars(request)
        return bars.data.get(symbol, [])
    except Exception as e:
        print(f"Error fetching bars for {symbol}: {e}")
        return []


def calculate_rsi(closes: list, period: int = 14) -> float:
    """Calculate RSI"""
    if len(closes) < period + 1:
        return 50.0  # Default neutral

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    # Use last 'period' values
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_reversal_signals(symbol: str, bars: list, position: dict) -> dict:
    """
    Calculate reversal signals for a position.

    Returns dict with:
    - signals: list of detected signals
    - score: total reversal score
    - details: signal details
    """
    if len(bars) < 21:
        return {"signals": [], "score": 0, "details": {}}

    # Sort bars by timestamp
    bars = sorted(bars, key=lambda x: x.timestamp)

    # Extract arrays
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    opens = [b.open for b in bars]

    signals = []
    score = 0
    details = {}

    # Current values
    current_close = closes[-1]
    current_high = highs[-1]
    current_low = lows[-1]
    current_volume = volumes[-1]
    current_open = opens[-1]

    # 1. SMA Bearish Cross (7 < 20) - Score: 3
    sma_7 = sum(closes[-7:]) / 7
    sma_20 = sum(closes[-20:]) / 20
    details["sma_7"] = round(sma_7, 2)
    details["sma_20"] = round(sma_20, 2)

    if sma_7 < sma_20:
        signals.append("SMA bearish cross (7 < 20)")
        score += 3

    # 2. Close in lower 30% of range - Score: 2
    daily_range = current_high - current_low
    if daily_range > 0:
        close_position = (current_close - current_low) / daily_range
        details["close_position"] = round(close_position, 2)

        if close_position < 0.3:
            signals.append(f"Close in lower 30% ({close_position:.0%})")
            score += 2

    # 3. Distribution volume (red day + volume > 1.5x avg) - Score: 3
    avg_volume_20 = sum(volumes[-21:-1]) / 20
    volume_ratio = current_volume / avg_volume_20 if avg_volume_20 > 0 else 0
    is_red_day = current_close < current_open
    details["volume_ratio"] = round(volume_ratio, 2)
    details["is_red_day"] = is_red_day

    if is_red_day and volume_ratio > 1.5:
        signals.append(f"Distribution volume ({volume_ratio:.1f}x on red day)")
        score += 3

    # 4. RSI breakdown (was >70, now dropping below 60) - Score: 2
    rsi_current = calculate_rsi(closes)
    rsi_prev = calculate_rsi(closes[:-1]) if len(closes) > 15 else 50
    details["rsi"] = round(rsi_current, 1)
    details["rsi_prev"] = round(rsi_prev, 1)

    if rsi_prev > 70 and rsi_current < 60:
        signals.append(f"RSI breakdown ({rsi_prev:.0f} -> {rsi_current:.0f})")
        score += 2

    # 5. Failed breakout (hit 5-day high but closing red) - Score: 3
    high_5d = max(highs[-6:-1])  # Previous 5 days' high
    details["high_5d"] = round(high_5d, 2)

    if current_high > high_5d and current_close < current_open:
        signals.append(f"Failed breakout (hit ${current_high:.2f}, closing red)")
        score += 3

    return {
        "signals": signals,
        "score": score,
        "details": details
    }


def send_telegram_alert(symbol: str, score: int, signals: list, pnl_pct: float, auto_closed: bool = False):
    """Send reversal alert to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        print("Telegram not configured, skipping alert")
        return

    config = get_runtime_config()
    auto_close_threshold = config.get('auto_close_threshold', 5)

    severity = "STRONG" if score >= auto_close_threshold else "WEAK"
    emoji = "🚨" if score >= auto_close_threshold else "⚠️"

    signal_list = "\n".join(f"  • {s}" for s in signals)
    pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"

    if auto_closed:
        action_text = f"*AUTO-CLOSED* - Position exited automatically"
    else:
        action_text = f"Action: `/close {symbol}` to exit"

    message = f"""{emoji} *REVERSAL ALERT: {symbol}*

Score: {score}/13 ({severity})

Signals detected:
{signal_list}

{pnl_emoji} P/L at alert: {pnl_pct:+.1f}%

{action_text}"""

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_ADMIN_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"Alert sent for {symbol}")
        else:
            print(f"Failed to send alert: {response.text}")
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")


def log_position_check(symbol: str, score: int, signals: list, details: dict, pnl_pct: float):
    """Log position check to database"""
    import sqlite3
    from config import DB_PATH

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Create table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS position_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                score INTEGER NOT NULL,
                signals TEXT,
                details TEXT,
                pnl_pct REAL,
                alert_sent INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            INSERT INTO position_checks (check_time, symbol, score, signals, details, pnl_pct, alert_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            symbol,
            score,
            json.dumps(signals),
            json.dumps(details),
            pnl_pct,
            1 if score >= 3 else 0
        ))

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging position check: {e}")


def run_monitor():
    """Main monitoring function"""
    print(f"[{datetime.now()}] Running position monitor...")

    # Get open positions
    positions = get_positions()

    # Filter out options (symbols with numbers like SPY260106C00695000)
    stock_positions = [p for p in positions if not any(c.isdigit() for c in p["symbol"])]

    if not stock_positions:
        print("No stock positions to monitor")
        return []

    print(f"Monitoring {len(stock_positions)} positions: {[p['symbol'] for p in stock_positions]}")

    # Initialize data client
    client = get_data_client()

    results = []

    for position in stock_positions:
        symbol = position["symbol"]
        pnl_pct = position["unrealized_plpc"] * 100

        print(f"\nChecking {symbol} (P/L: {pnl_pct:+.1f}%)...")

        # Get historical bars
        bars = get_historical_bars(client, symbol)

        if len(bars) < 21:
            print(f"  Insufficient data ({len(bars)} bars)")
            continue

        # Calculate reversal signals
        result = calculate_reversal_signals(symbol, bars, position)
        result["symbol"] = symbol
        result["pnl_pct"] = pnl_pct

        print(f"  Score: {result['score']}/13")
        if result["signals"]:
            print(f"  Signals: {', '.join(result['signals'])}")

        # Log to database
        log_position_check(
            symbol,
            result["score"],
            result["signals"],
            result["details"],
            pnl_pct
        )

        # Get current config settings
        config = get_runtime_config()
        auto_close_enabled = config.get('auto_close_enabled', True)
        auto_close_threshold = config.get('auto_close_threshold', 5)
        alert_threshold = config.get('alert_threshold', 3)

        # Handle based on score
        if result["score"] >= auto_close_threshold and auto_close_enabled:
            # Calculate days held before auto-closing
            trade = get_trade_by_symbol(symbol, "open")
            days_held = 0
            if trade and trade.get('entry_date'):
                try:
                    entry_date = datetime.fromisoformat(trade['entry_date'][:10])
                    days_held = (datetime.now() - entry_date).days
                except Exception:
                    pass

            # Skip auto-close if held < 2 days (let positions develop)
            if days_held < 2:
                print(f"  ⏳ MIN HOLD PROTECTION: Skipping auto-close (held {days_held} days, need 2+)")
                send_telegram_alert(symbol, result["score"], result["signals"], pnl_pct, auto_closed=False)
                result["auto_closed"] = False
                results.append(result)
                continue

            # Strong reversal with sufficient hold time - auto-close position
            print(f"  🚨 STRONG REVERSAL (score >= {auto_close_threshold}, held {days_held} days) - AUTO-CLOSING!")
            close_result = close_position(
                symbol,
                reason=f"auto_reversal_score_{result['score']}",
                reversal_signals=result.get("signals", [])
            )
            if close_result.get("success"):
                print(f"  ✓ Position closed: {close_result['qty']} shares")
                send_telegram_alert(symbol, result["score"], result["signals"], pnl_pct, auto_closed=True)
                result["auto_closed"] = True
            else:
                print(f"  ✗ Failed to close: {close_result.get('error')}")
                send_telegram_alert(symbol, result["score"], result["signals"], pnl_pct, auto_closed=False)
                result["auto_closed"] = False
        elif result["score"] >= alert_threshold:
            # Weak reversal - alert only
            print(f"  ⚠️ ALERT TRIGGERED (score >= {alert_threshold})!")
            send_telegram_alert(symbol, result["score"], result["signals"], pnl_pct, auto_closed=False)
            result["auto_closed"] = False

        results.append(result)

    print(f"\n[{datetime.now()}] Monitor complete. Checked {len(results)} positions.")
    return results


if __name__ == "__main__":
    import sys

    # Check for --force flag to skip market hours check
    force_run = "--force" in sys.argv

    if not force_run and not is_market_hours():
        now_et = datetime.now(MARKET_TZ)
        print(f"[{now_et}] Outside market hours. Use --force to run anyway.")
        sys.exit(0)

    # Show current config
    config = get_runtime_config()
    print(f"\nConfig: auto_close={'ON' if config.get('auto_close_enabled', True) else 'OFF'}, "
          f"threshold={config.get('auto_close_threshold', 5)}, "
          f"alert_at={config.get('alert_threshold', 3)}")

    results = run_monitor()

    if results:
        print("\n=== SUMMARY ===")
        for r in results:
            if r.get("auto_closed"):
                status = "🚨 AUTO-CLOSED"
            elif r["score"] >= config.get('alert_threshold', 3):
                status = "⚠️ ALERT"
            else:
                status = "✓ OK"
            print(f"{status} {r['symbol']}: Score={r['score']}, P/L={r['pnl_pct']:+.1f}%")
