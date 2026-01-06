"""
Scanner Module - Pulls market data and calculates momentum signals
Enhanced with DQL training data collection
"""
import json
from datetime import datetime, timedelta
from typing import Optional, Tuple
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_CONFIG, get_cap_config


def load_universe(cap: str = None) -> list[str]:
    """
    Load stock universe from JSON file.

    Args:
        cap: Market cap filter - 'large', 'mid', 'small', or None for all
    """
    with open("data/universe.json", "r") as f:
        data = json.load(f)

    symbols = data["symbols"]

    # Handle both old format (list) and new format (dict by cap)
    if isinstance(symbols, list):
        return symbols

    # New format: dict with large/mid/small keys
    if cap and cap in symbols:
        return symbols[cap]
    elif cap == 'all' or cap is None:
        # Return all symbols
        all_symbols = []
        for cap_type in ['large', 'mid', 'small']:
            if cap_type in symbols:
                all_symbols.extend(symbols[cap_type])
        return all_symbols
    else:
        print(f"Warning: Unknown cap '{cap}', returning all symbols")
        all_symbols = []
        for cap_type in ['large', 'mid', 'small']:
            if cap_type in symbols:
                all_symbols.extend(symbols[cap_type])
        return all_symbols


def get_data_client() -> StockHistoricalDataClient:
    """Initialize Alpaca data client"""
    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def get_snapshots(client: StockHistoricalDataClient, symbols: list[str]) -> dict:
    """
    Get latest snapshots for symbols (batched).
    Returns dict with latest quote, trade, and daily bars.
    """
    snapshots = {}
    batch_size = 100

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        try:
            request = StockSnapshotRequest(symbol_or_symbols=batch)
            batch_snapshots = client.get_stock_snapshot(request)
            snapshots.update(batch_snapshots)
        except Exception as e:
            print(f"Error fetching snapshots for batch {i}: {e}")

    return snapshots


def get_historical_bars(client: StockHistoricalDataClient, symbols: list[str], days: int = 30) -> dict:
    """
    Get historical daily bars for symbols.
    Returns dict of symbol -> list of bars.
    """
    end = datetime.now()
    start = end - timedelta(days=days + 20)  # Extra buffer for weekends/holidays

    try:
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Day,
            start=start,
            end=end
        )
        bars = client.get_stock_bars(request)
        return bars.data
    except Exception as e:
        print(f"Error fetching historical bars: {e}")
        return {}


def get_market_context(client: StockHistoricalDataClient) -> Tuple[dict, float]:
    """
    Get SPY and VIX data for market context.
    Returns (spy_data, vix_level)
    """
    spy_data = {
        'price': None,
        'change_1d': None,
        'change_5d': None,
        'sma20': None,
        'above_sma20': False,
        'spy_trend': 'sideways'
    }
    vix_level = 15.0  # Default

    try:
        # Get SPY data
        spy_bars = get_historical_bars(client, ['SPY'], days=30)
        if 'SPY' in spy_bars and len(spy_bars['SPY']) >= 20:
            bars = sorted(spy_bars['SPY'], key=lambda x: x.timestamp)
            closes = [b.close for b in bars]

            spy_data['price'] = closes[-1]
            spy_data['change_1d'] = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
            spy_data['change_5d'] = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else 0
            spy_data['sma20'] = round(sum(closes[-20:]) / 20, 2)
            spy_data['above_sma20'] = closes[-1] > spy_data['sma20']

            # Determine trend
            if closes[-1] > spy_data['sma20'] and spy_data['change_5d'] > 1:
                spy_data['spy_trend'] = 'up'
            elif closes[-1] < spy_data['sma20'] and spy_data['change_5d'] < -1:
                spy_data['spy_trend'] = 'down'
            else:
                spy_data['spy_trend'] = 'sideways'

        # Get VIX (UVXY as proxy since VIX not directly available)
        # Using a simple estimate based on market conditions
        vix_snapshot = get_snapshots(client, ['UVXY'])
        if 'UVXY' in vix_snapshot and vix_snapshot['UVXY'].daily_bar:
            # UVXY roughly correlates with VIX * 1.5
            uvxy_price = vix_snapshot['UVXY'].daily_bar.close
            vix_level = round(uvxy_price / 1.5, 1)

    except Exception as e:
        print(f"Error getting market context: {e}")

    return spy_data, vix_level


def calculate_market_breadth(snapshots: dict) -> float:
    """
    Calculate market breadth: % of stocks above their 20-day average.
    This is a simplified version using daily price change as proxy.
    """
    above_count = 0
    total_count = 0

    for symbol, snapshot in snapshots.items():
        try:
            if snapshot.daily_bar and snapshot.previous_daily_bar:
                total_count += 1
                if snapshot.daily_bar.close > snapshot.previous_daily_bar.close:
                    above_count += 1
        except:
            continue

    return round(above_count / total_count, 2) if total_count > 0 else 0.5


def calculate_signals(symbol: str, bars: list) -> Optional[dict]:
    """
    Calculate momentum signals for a stock.

    Returns dict with:
    - sma_7, sma_20, sma_30
    - sma_aligned (bool)
    - volume_surge (time-normalized today vs 20D avg)
    - gap_up (gap from yesterday's close)
    - follow_through (trading above today's open)
    - breakout_5d (breaking above 5-day high)
    - momentum_breakout (confirmed breakout with volume)
    - roc_10d (10-day rate of change)
    - near_52w_high (bool)
    - composite_score
    """
    if len(bars) < 30:
        return None

    # Sort bars by timestamp (oldest first)
    bars = sorted(bars, key=lambda x: x.timestamp)

    # Today and yesterday bars
    today = bars[-1]
    yesterday = bars[-2]

    # Extract arrays
    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    opens = [b.open for b in bars]

    # SMAs (using last N days)
    sma_7 = sum(closes[-7:]) / 7
    sma_20 = sum(closes[-20:]) / 20
    sma_30 = sum(closes[-30:]) / 30

    # SMA Alignment: 7 > 20 > 30 and 7-day slope positive
    sma_aligned = (sma_7 > sma_20 > sma_30) and (closes[-1] > closes[-7])

    # Volume surge: time-normalized today's volume vs 20-day average
    time_fraction = calculate_time_fraction()
    avg_volume_20 = sum(volumes[-21:-1]) / 20 if len(volumes) > 20 else sum(volumes[:-1]) / len(volumes[:-1])
    projected_today_volume = volumes[-1] / time_fraction
    volume_surge = projected_today_volume / avg_volume_20 if avg_volume_20 > 0 else 0

    # Gap up: today's open vs yesterday's close
    gap_up = (today.open - yesterday.close) / yesterday.close

    # Follow-through: current price above today's open
    follow_through = today.close > today.open

    # Breakout: current price above 5-day high (of completed days)
    five_day_high = max(highs[-6:-1])
    breakout_5d = today.close > five_day_high
    breakout_pct = (today.close - five_day_high) / five_day_high

    # Intraday strength: where current price is in today's range
    today_range = today.high - today.low
    intraday_strength = (today.close - today.low) / today_range if today_range > 0 else 0.5

    # Momentum breakout: price breakout + volume confirmation
    # Price breakout = gap up >1% with follow-through OR breaking 5-day high
    price_breakout = (gap_up > 0.01 and follow_through) or breakout_5d
    momentum_breakout = price_breakout and volume_surge >= 1.3

    # 5-day and 10-day rate of change
    roc_5d = (closes[-1] - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] > 0 else 0
    roc_10d = (closes[-1] - closes[-11]) / closes[-11] if closes[-11] > 0 else 0

    # Near 52-week high (within 5%)
    high_52w = max(highs[-min(252, len(highs)):])
    pct_from_high = (high_52w - closes[-1]) / high_52w
    near_52w_high = pct_from_high <= 0.05

    # ATR (14-day)
    true_ranges = []
    for i in range(-14, 0):
        if i == -14:
            tr = highs[i] - lows[i]
        else:
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
        true_ranges.append(tr)
    atr_14 = sum(true_ranges) / 14

    # RSI (14-day)
    gains = []
    losses = []
    for i in range(-14, 0):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        rsi_14 = 100
    else:
        rs = avg_gain / avg_loss
        rsi_14 = 100 - (100 / (1 + rs))

    # Composite score
    score = 0
    if sma_aligned:
        score += 5
    if volume_surge > 1.5:
        score += 3
    elif volume_surge > 1.3:
        score += 2
    if momentum_breakout:
        score += 3
    if intraday_strength > 0.7:
        score += 2
    elif intraday_strength > 0.5:
        score += 1
    if roc_10d > 0.10:
        score += 4
    elif roc_10d > 0.05:
        score += 2
    if near_52w_high:
        score += 3

    return {
        "symbol": symbol,
        "price": closes[-1],
        "sma_7": round(sma_7, 2),
        "sma_20": round(sma_20, 2),
        "sma_30": round(sma_30, 2),
        "sma_aligned": sma_aligned,
        "volume_surge": round(volume_surge, 2),
        "gap_up": round(gap_up * 100, 2),  # As percentage
        "follow_through": follow_through,
        "breakout_5d": breakout_5d,
        "breakout_pct": round(breakout_pct * 100, 2),
        "intraday_strength": round(intraday_strength, 2),
        "momentum_breakout": momentum_breakout,
        "roc_5d": round(roc_5d * 100, 2),  # As percentage
        "roc_10d": round(roc_10d * 100, 2),  # As percentage
        "near_52w_high": near_52w_high,
        "pct_from_high": round(pct_from_high * 100, 2),
        "atr_14": round(atr_14, 2),
        "rsi_14": round(rsi_14, 1),
        "composite_score": score
    }


def calculate_time_fraction() -> float:
    """
    Calculate what fraction of the trading day has elapsed.
    Returns value between 0.0 and 1.0.
    """
    import pytz
    et = pytz.timezone('America/New_York')
    now_et = datetime.now(et)

    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    total_minutes = 390  # 6.5 hours

    elapsed_minutes = (now_et - market_open).total_seconds() / 60
    elapsed_minutes = max(0, min(total_minutes, elapsed_minutes))

    # Minimum 10% to avoid division issues early in day
    return max(0.10, elapsed_minutes / total_minutes)


def quick_filter_snapshots(snapshots: dict) -> list[str]:
    """
    Quick filter using snapshots with time-normalized RVOL.
    Returns symbols that pass basic momentum criteria.

    Uses projected full-day volume based on time elapsed to calculate
    Relative Volume (RVOL) - captures institutional interest early in day.
    """
    candidates = []
    time_fraction = calculate_time_fraction()

    for symbol, snapshot in snapshots.items():
        try:
            if not snapshot.daily_bar or not snapshot.previous_daily_bar:
                continue

            # Price change from previous close
            prev_close = snapshot.previous_daily_bar.close
            curr_close = snapshot.daily_bar.close
            price_change = (curr_close - prev_close) / prev_close

            # Time-normalized Relative Volume (RVOL)
            # Project current volume to full-day estimate, compare to prev day
            curr_volume = snapshot.daily_bar.volume
            prev_volume = snapshot.previous_daily_bar.volume

            if prev_volume > 0:
                projected_volume = curr_volume / time_fraction
                rvol = projected_volume / prev_volume
            else:
                rvol = 0

            # Quick filters:
            # 1. Price > $5 (liquidity)
            # 2. Up at least 0.5% from previous close (buyers in control)
            # 3. RVOL >= 1.2 (volume pace 20%+ above normal - institutional interest)
            if curr_close > 5 and price_change > 0.005 and rvol >= 1.2:
                candidates.append(symbol)
        except Exception as e:
            continue

    return candidates


def run_scan(scan_type: str = 'scheduled', log_candidates: bool = True, cap: str = None) -> list[dict]:
    """
    Main scan function with DQL data collection.

    Stage 1: Quick filter using snapshots
    Stage 2: Deep analysis on candidates using 30D bars
    Stage 3: Log all candidates for DQL training

    Args:
        scan_type: Type of scan ('scheduled', 'manual', etc.)
        log_candidates: Whether to log candidates for DQL training
        cap: Market cap filter - 'large', 'mid', 'small', or None for all

    Returns sorted list of candidates with signals.
    """
    from db import log_market_snapshot, log_candidate

    print(f"[{datetime.now()}] Starting scan...")

    # Load universe (filtered by cap if specified)
    universe = load_universe(cap=cap)
    cap_label = cap.upper() if cap else "ALL"
    print(f"Universe: {len(universe)} stocks ({cap_label} cap)")

    # Initialize client
    client = get_data_client()

    # Get market context for DQL
    print("Fetching market context (SPY/VIX)...")
    spy_data, vix_level = get_market_context(client)
    print(f"  SPY: ${spy_data['price']} ({spy_data['change_1d']:+.1f}%), Trend: {spy_data['spy_trend']}")
    print(f"  VIX: {vix_level}")

    # Stage 1: Quick filter with snapshots
    print("Stage 1: Fetching snapshots...")
    snapshots = get_snapshots(client, universe)
    print(f"Got snapshots for {len(snapshots)} stocks")

    # Calculate market breadth
    market_breadth = calculate_market_breadth(snapshots)
    print(f"  Market breadth: {market_breadth*100:.0f}% advancing")

    stage1_candidates = quick_filter_snapshots(snapshots)
    print(f"Stage 1 candidates: {len(stage1_candidates)} stocks passed quick filter")

    # Log market snapshot for DQL
    scan_id = None
    if log_candidates:
        scan_id = log_market_snapshot(
            spy_data=spy_data,
            vix_level=vix_level,
            candidates_count=len(stage1_candidates),
            scan_type=scan_type,
            market_breadth=market_breadth
        )
        print(f"  Logged market snapshot (scan_id: {scan_id})")

    if not stage1_candidates:
        print("No candidates found in Stage 1")
        return []

    # Limit candidates for Stage 2
    candidates_for_analysis = stage1_candidates[:50]  # Max 50 for deep analysis

    # Stage 2: Deep analysis with historical bars
    print(f"Stage 2: Fetching 30D bars for {len(candidates_for_analysis)} candidates...")
    bars_data = get_historical_bars(client, candidates_for_analysis, days=30)

    # Calculate signals for each candidate
    all_results = []
    for symbol in candidates_for_analysis:
        if symbol in bars_data:
            signals = calculate_signals(symbol, bars_data[symbol])
            if signals:
                all_results.append(signals)

    # Get cap-specific thresholds
    cap_config = get_cap_config(cap)
    min_volume_surge = cap_config["min_volume_surge"]
    min_gap_up = cap_config["min_gap_up"] * 100  # Convert to percentage
    min_roc_10d = cap_config["min_roc_10d"] * 100  # Convert to percentage

    print(f"  Thresholds: gap>={min_gap_up:.1f}%, vol>={min_volume_surge}x, roc>={min_roc_10d:.1f}%")

    # Filter by minimum criteria
    # Momentum breakout: gap+follow-through OR 5D breakout, with volume confirmation
    filtered = []
    for r in all_results:
        # Use cap-specific gap threshold for breakout detection
        gap_breakout = r["gap_up"] >= min_gap_up and r["follow_through"]
        breakout = gap_breakout or r["breakout_5d"]
        volume_ok = r["volume_surge"] >= min_volume_surge
        momentum_ok = r["roc_10d"] >= min_roc_10d

        passes_filter = breakout and volume_ok and momentum_ok

        if passes_filter:
            filtered.append(r)

        # Log ALL candidates for DQL training (not just filtered ones)
        if log_candidates and scan_id:
            if passes_filter:
                action = 'candidate'  # Will be updated to 'bought' or 'skipped' by executor
            else:
                action = 'filtered_out'
                skip_reason = []
                if not breakout:
                    skip_reason.append(f"no_breakout(gap={r['gap_up']:.1f}%)")
                if not volume_ok:
                    skip_reason.append(f"low_volume({r['volume_surge']:.1f}x)")
                if not momentum_ok:
                    skip_reason.append(f"weak_momentum({r['roc_10d']:.1f}%)")

            log_candidate(
                scan_id=scan_id,
                symbol=r["symbol"],
                signals=r,
                action=action,
                skip_reason=','.join(skip_reason) if action == 'filtered_out' else None
            )

    # Sort by composite score (descending)
    filtered.sort(key=lambda x: x["composite_score"], reverse=True)

    print(f"Stage 2 results: {len(filtered)} stocks passed all filters")
    if log_candidates:
        print(f"  Logged {len(all_results)} candidates for DQL training")

    return filtered[:10]  # Return top 10


def run_scan_simple() -> list[dict]:
    """
    Simple scan without DQL logging (for manual/telegram scans).
    """
    return run_scan(scan_type='manual', log_candidates=False)


if __name__ == "__main__":
    candidates = run_scan(scan_type='manual', log_candidates=True)
    print("\n=== TOP CANDIDATES ===")
    for c in candidates:
        print(f"{c['symbol']}: Score={c['composite_score']}, ROC={c['roc_10d']}%, "
              f"VolSurge={c['volume_surge']}x, Gap={c['gap_up']}%, Breakout={c['breakout_5d']}")
