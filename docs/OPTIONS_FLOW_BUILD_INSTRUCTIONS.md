# Options Flow Trading System - Build Instructions for Claude Code

## Overview

Build an options flow trading system that integrates with the existing momentum_agent codebase. The system should:
1. Pull unusual options flow from Unusual Whales API
2. Filter for high-conviction signals
3. Use Claude to synthesize trade theses
4. Execute options trades on Alpaca paper account
5. Track results in SQLite

## Environment Setup

Add to `.env`:
```
UW_API_KEY=your_unusual_whales_api_key
```

## Files to Create

### 1. `flow_scanner.py` - Fetch and Score UW Flow Signals

**Purpose**: Pull options flow alerts from Unusual Whales API and score them for conviction.

**Key Components**:

```python
# Configuration
UW_API_KEY = os.getenv("UW_API_KEY")
UW_BASE_URL = "https://api.unusualwhales.com/api"

# Scoring weights
FLOW_SCORING = {
    "sweep": 3,              # Intermarket sweep (urgency)
    "ask_side": 2,           # Bought at ask (bullish conviction)
    "high_premium": 3,       # $100K+ premium
    "very_high_premium": 2,  # $250K+ premium (bonus)
    "high_vol_oi": 2,        # Vol/OI > 1
    "very_high_vol_oi": 1,   # Vol/OI > 3 (bonus)
    "floor_trade": 2,        # Floor trade (institutional)
    "otm": 1,                # Out of the money
    "near_earnings": 1,      # Within 14 days of earnings
    "low_dte": 1,            # < 30 DTE
    "opening_trade": 2,      # Opening position
}

MIN_CONVICTION_SCORE = 8
```

**Classes**:
- `FlowSignal` dataclass with fields: id, symbol, strike, expiration, option_type, premium, size, volume, open_interest, vol_oi_ratio, is_sweep, is_ask_side, is_bid_side, is_floor, is_opening, is_otm, underlying_price, timestamp, sentiment, score, score_breakdown

- `UnusualWhalesClient` class with methods:
  - `get_flow_alerts(min_premium, is_sweep, is_ask_side, min_vol_oi_ratio, limit, ticker_symbol, is_otm, max_dte, min_dte, is_call, is_put, all_opening, newer_than, older_than)`
  - `get_stock_info(ticker)`
  - `get_earnings(ticker)`
  - `get_iv_rank(ticker)`
  - `get_greek_exposure(ticker)`
  - `get_max_pain(ticker)`

**Functions**:
- `parse_flow_alert(alert: Dict) -> FlowSignal` - Parse raw API response
- `score_flow_signal(signal: FlowSignal, earnings_data: Dict) -> FlowSignal` - Apply scoring
- `run_flow_scan(min_premium=100000, min_vol_oi=1.0, sweeps_only=False, ask_side_only=False, opening_only=False, min_score=8, limit=50, ticker=None, include_puts=True, max_dte=60) -> List[FlowSignal]`
- `get_flow_summary(signals: List[FlowSignal]) -> Dict`

**API Endpoints**:
- Flow alerts: `GET /api/option-trades/flow-alerts`
- Stock info: `GET /api/stock/{ticker}/info`
- Earnings: `GET /api/stock/{ticker}/earnings`
- IV Rank: `GET /api/stock/{ticker}/iv-rank`
- Max Pain: `GET /api/stock/{ticker}/max-pain`

**Headers**: `Authorization: Bearer {UW_API_KEY}`

---

### 2. `flow_analyzer.py` - Enrich with Context and Generate Thesis

**Purpose**: Enrich flow signals with Alpaca price data and UW options data, then use Claude to generate trade thesis.

**Classes**:
- `EnrichedFlowSignal` dataclass extending FlowSignal with:
  - Price context: current_price, price_change_1d, price_change_5d, volume_today, avg_volume_20d, relative_volume
  - Technical context: sma_20, sma_50, above_sma_20, above_sma_50, rsi_14, atr_14
  - Options context: iv_rank, iv_percentile, earnings_date, days_to_earnings, max_pain
  - Generated: thesis, recommendation (BUY/WATCH/SKIP), conviction (0-1), risk_factors

**Functions**:
- `get_price_context(client: StockHistoricalDataClient, symbol: str) -> Dict` - Get Alpaca price/technical data
- `get_options_context(uw_client: UnusualWhalesClient, symbol: str) -> Dict` - Get UW options data
- `enrich_flow_signal(signal, alpaca_client, uw_client) -> EnrichedFlowSignal`
- `generate_thesis(enriched: EnrichedFlowSignal) -> EnrichedFlowSignal` - Call Claude API
- `analyze_flow_signals(signals: List[FlowSignal], max_analyze=10) -> List[EnrichedFlowSignal]`
- `get_buy_recommendations(enriched_signals) -> List[EnrichedFlowSignal]`
- `format_flow_analysis_for_telegram(enriched: EnrichedFlowSignal) -> str`

**Claude System Prompt** (for thesis generation):
```
You are an expert options flow analyst. Analyze unusual options flow signals and generate actionable trade theses.

Signal Quality Factors (Positive):
- Sweeps: Urgency - willing to pay across exchanges
- Ask-side: Paying up = conviction
- High premium ($100K+): Serious money
- Vol/OI > 1: Unusual activity
- Floor trades: Institutional
- Opening trades: New positions

Risk Factors (Negative):
- High IV: Premium expensive
- IV rank > 50%: Volatility priced in
- Near earnings: Binary risk
- Short DTE + OTM: Theta decay
- Against trend: Fighting momentum

Output JSON:
{
    "thesis": "2-3 sentence thesis",
    "recommendation": "BUY|WATCH|SKIP",
    "conviction": 0.0-1.0,
    "entry_strategy": "...",
    "target_exit": "...",
    "stop_loss": "...",
    "risk_factors": ["..."],
    "reasoning": "..."
}
```

---

### 3. `options_executor.py` - Place Options Orders via Alpaca

**Purpose**: Find contracts, size positions, execute trades, manage exits.

**Configuration**:
```python
OPTIONS_CONFIG = {
    "max_options_positions": 4,
    "max_position_value": 2000,
    "position_size_pct": 0.02,        # 2% of portfolio
    "max_portfolio_risk_options": 0.10,
    "default_contracts": 1,
    "max_contracts_per_trade": 10,
    "min_premium": 50,
    "max_premium": 1000,
    "min_days_to_exp": 7,
    "max_days_to_exp": 60,
    "profit_target_pct": 0.50,        # 50% profit target
    "stop_loss_pct": 0.50,            # 50% stop loss
}
```

**Classes**:
- `OptionsPosition` dataclass: symbol, contract_symbol, option_type, strike, expiration, quantity, avg_entry_price, current_price, market_value, unrealized_pl, unrealized_plpc

**Functions**:
- `get_trading_client() -> TradingClient`
- `get_account_info() -> Dict`
- `get_options_positions() -> List[OptionsPosition]`
- `find_option_contract(underlying, option_type, target_strike, target_expiration, min_dte, max_dte, otm_pct) -> Dict`
- `calculate_position_size(account, option_price, enriched_signal) -> int`
- `place_options_order(contract_symbol, quantity, side, order_type, limit_price, signal_data) -> Dict`
- `execute_flow_trade(enriched_signal: EnrichedFlowSignal) -> Dict`
- `close_options_position(contract_symbol, reason, quantity) -> Dict`
- `check_options_exits() -> List[Dict]` - Check profit target/stop loss
- `get_options_summary() -> Dict`

**Alpaca Options API**:
- Use `GetOptionContractsRequest` to find contracts
- Use `MarketOrderRequest` or `LimitOrderRequest` for execution
- Filter by: underlying_symbols, status, type (CALL/PUT), expiration_date_gte/lte, strike_price_gte/lte

---

### 4. Update `db.py` - Add Options Tables

**New Tables**:

```sql
-- Flow signals table
CREATE TABLE IF NOT EXISTS flow_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT UNIQUE,
    timestamp TEXT,
    symbol TEXT,
    strike REAL,
    expiration TEXT,
    option_type TEXT,
    premium REAL,
    size INTEGER,
    volume INTEGER,
    open_interest INTEGER,
    vol_oi_ratio REAL,
    is_sweep INTEGER,
    is_ask_side INTEGER,
    is_floor INTEGER,
    is_opening INTEGER,
    is_otm INTEGER,
    underlying_price REAL,
    sentiment TEXT,
    score INTEGER,
    score_breakdown TEXT,
    analyzed INTEGER DEFAULT 0,
    recommendation TEXT,
    conviction REAL,
    thesis TEXT,
    executed INTEGER DEFAULT 0,
    raw_data TEXT
);

-- Options trades table
CREATE TABLE IF NOT EXISTS options_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_symbol TEXT,
    underlying TEXT,
    option_type TEXT,
    strike REAL,
    expiration TEXT,
    entry_date TEXT,
    entry_price REAL,
    quantity INTEGER,
    signal_score INTEGER,
    signal_data TEXT,
    thesis TEXT,
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl_amount REAL,
    pnl_pct REAL,
    status TEXT DEFAULT 'open',
    flow_signal_id INTEGER,
    FOREIGN KEY (flow_signal_id) REFERENCES flow_signals(id)
);

-- Flow scan history
CREATE TABLE IF NOT EXISTS flow_scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time TEXT,
    filters TEXT,
    signals_found INTEGER,
    signals_analyzed INTEGER,
    buy_recommendations INTEGER,
    trades_executed INTEGER,
    top_signals TEXT
);
```

**New Functions to Add**:
- `init_options_tables(conn)`
- `log_flow_signal(...)` - Log raw flow signal
- `update_flow_signal_analysis(signal_id, recommendation, conviction, thesis)`
- `mark_flow_signal_executed(signal_id)`
- `log_options_trade(contract_symbol, underlying, option_type, strike, expiration, quantity, entry_price, signal_score, signal_data, thesis, flow_signal_id)`
- `update_options_trade_exit(contract_symbol, trade_id, exit_price, exit_reason)`
- `get_options_trade_by_id(trade_id)`
- `get_options_trade_by_contract(contract_symbol, status)`
- `get_open_options_trades()`
- `get_recent_options_trades(limit)`
- `get_options_performance()` - Return win rate, avg win/loss, total P/L

---

### 5. Update `bot.py` - Add Telegram Commands

**New Commands**:

#### `/flow` - Run Flow Scan
```python
@admin_only
async def cmd_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run options flow scan"""
    await update.message.reply_text("⏳ Scanning options flow...")

    from flow_scanner import run_flow_scan, get_flow_summary

    signals = run_flow_scan(
        min_premium=100000,
        min_vol_oi=1.0,
        min_score=8,
        limit=50,
    )

    if not signals:
        await update.message.reply_text("📭 No high-conviction flow signals found.")
        return

    # Store for later execution
    global last_flow_results
    last_flow_results = {"timestamp": datetime.now().isoformat(), "signals": signals}

    summary = get_flow_summary(signals)

    msg = f"✅ *Flow Scan Complete*\n"
    msg += f"Found {summary['count']} signals (score >= 8)\n\n"
    msg += f"*Summary:*\n"
    msg += f"├── Total Premium: ${summary['total_premium']:,.0f}\n"
    msg += f"├── Bullish: {summary['bullish_count']} | Bearish: {summary['bearish_count']}\n"
    msg += f"├── Sweeps: {summary['sweeps']} | Floor: {summary['floor_trades']}\n"
    msg += f"└── Avg Score: {summary['avg_score']:.1f}\n\n"

    msg += "*Top 5 Signals:*\n"
    for i, s in enumerate(signals[:5], 1):
        emoji = "📈" if s.sentiment == "bullish" else "📉"
        msg += f"\n{i}. {emoji} *{s.symbol}* {s.option_type.upper()} ${s.strike}\n"
        msg += f"   ${s.premium:,.0f} | Score: {s.score} | Vol/OI: {s.vol_oi_ratio}x\n"

    msg += f"\n\nUse `/analyze` to generate theses or `/buyoption SYMBOL` to trade"

    await update.message.reply_text(msg, parse_mode="Markdown")
```

#### `/analyze` - Analyze Flow Signals with Claude
```python
@admin_only
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyze top flow signals with Claude"""
    global last_flow_results

    if not last_flow_results or not last_flow_results.get("signals"):
        await update.message.reply_text("📭 No flow signals. Run /flow first.")
        return

    await update.message.reply_text("⏳ Analyzing signals with Claude... (30-60 sec)")

    from flow_analyzer import analyze_flow_signals, format_flow_analysis_for_telegram

    signals = last_flow_results["signals"][:5]  # Top 5
    enriched = analyze_flow_signals(signals, max_analyze=5)

    last_flow_results["analyzed"] = enriched

    for e in enriched:
        msg = format_flow_analysis_for_telegram(e)
        await update.message.reply_text(msg, parse_mode="Markdown")

    buys = [e for e in enriched if e.recommendation == "BUY"]
    if buys:
        symbols = ", ".join(e.signal.symbol for e in buys)
        await update.message.reply_text(
            f"🟢 *BUY Recommendations:* {symbols}\n\nUse `/buyoption SYMBOL confirm` to execute",
            parse_mode="Markdown"
        )
```

#### `/options` - View Options Positions
```python
@admin_only
async def cmd_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show options positions and performance"""
    from options_executor import get_options_positions, get_options_summary
    from db import get_options_performance

    summary = get_options_summary()
    perf = get_options_performance()

    msg = "📊 *Options Positions*\n\n"

    if summary["count"] == 0:
        msg += "No open options positions.\n\n"
    else:
        msg += f"*Open Positions ({summary['count']}):*\n"
        for pos in summary["positions"]:
            emoji = "🟢" if pos["unrealized_pl"] >= 0 else "🔴"
            msg += f"\n{emoji} *{pos['symbol']}* {pos['option_type'].upper()} ${pos['strike']}\n"
            msg += f"   {pos['quantity']}x @ ${pos['avg_entry_price']:.2f}\n"
            msg += f"   P/L: ${pos['unrealized_pl']:.2f} ({pos['unrealized_plpc']*100:.1f}%)\n"

        msg += f"\n*Portfolio:*\n"
        msg += f"├── Total Value: ${summary['total_value']:,.2f}\n"
        msg += f"├── Total P/L: ${summary['total_pnl']:,.2f} ({summary['pnl_pct']:.1f}%)\n"
        msg += f"└── % of Portfolio: {summary['portfolio_pct']:.1f}%\n"

    msg += f"\n*All-Time Performance:*\n"
    msg += f"├── Trades: {perf['total_trades']} ({perf['open_trades']} open)\n"
    msg += f"├── Win Rate: {perf['win_rate']}%\n"
    msg += f"├── Avg Win: +{perf['avg_win']:.1f}% | Avg Loss: {perf['avg_loss']:.1f}%\n"
    msg += f"└── Total P/L: ${perf['total_pnl']:,.2f}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")
```

#### `/buyoption SYMBOL` - Execute Options Trade
```python
@admin_only
async def cmd_buyoption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute options trade from analyzed signals"""
    global last_flow_results

    if not context.args:
        await update.message.reply_text("Usage: `/buyoption SYMBOL [confirm]`", parse_mode="Markdown")
        return

    symbol = context.args[0].upper()

    # Find in analyzed signals
    analyzed = last_flow_results.get("analyzed", [])
    enriched = next((e for e in analyzed if e.signal.symbol == symbol), None)

    if not enriched:
        await update.message.reply_text(
            f"⚠️ {symbol} not in analyzed signals.\nRun /flow then /analyze first."
        )
        return

    if enriched.recommendation != "BUY":
        await update.message.reply_text(
            f"⚠️ {symbol} recommendation is {enriched.recommendation}, not BUY.\n"
            f"Conviction: {enriched.conviction:.0%}"
        )
        return

    # Confirmation
    if len(context.args) < 2 or context.args[1].lower() != "confirm":
        signal = enriched.signal
        msg = f"⚠️ *Confirm Options Trade*\n\n"
        msg += f"Symbol: *{symbol}*\n"
        msg += f"Contract: {signal.option_type.upper()} ${signal.strike} exp {signal.expiration[:10]}\n"
        msg += f"Signal Score: {signal.score}/20\n"
        msg += f"Conviction: {enriched.conviction:.0%}\n\n"
        msg += f"Send `/buyoption {symbol} confirm` to execute"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # Execute
    await update.message.reply_text(f"⏳ Executing options trade for {symbol}...")

    from options_executor import execute_flow_trade
    result = execute_flow_trade(enriched)

    if result.get("success"):
        msg = f"✅ *Options Trade Executed*\n\n"
        msg += f"Contract: {result['contract_symbol']}\n"
        msg += f"Quantity: {result['quantity']}\n"
        msg += f"Est. Cost: ${result.get('estimated_cost', 0):,.2f}\n"
        msg += f"Strike: ${result['strike']} | Exp: {result['expiration']}\n\n"
        msg += f"*Thesis:* {result.get('thesis', 'N/A')[:200]}..."
    else:
        msg = f"❌ Trade failed: {result.get('error')}"

    await update.message.reply_text(msg, parse_mode="Markdown")
```

#### `/closeoption CONTRACT` - Close Options Position
```python
@admin_only
async def cmd_closeoption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close an options position"""
    if not context.args:
        await update.message.reply_text("Usage: `/closeoption CONTRACT_SYMBOL`", parse_mode="Markdown")
        return

    contract = context.args[0].upper()
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "manual"

    from options_executor import close_options_position
    result = close_options_position(contract, reason)

    if result.get("success"):
        msg = f"✅ Closed {result.get('quantity', 1)}x {contract}\n"
        if "pnl" in result:
            emoji = "🟢" if result["pnl"] >= 0 else "🔴"
            msg += f"{emoji} P/L: ${result['pnl']:.2f} ({result['pnl_pct']*100:.1f}%)"
    else:
        msg = f"❌ Failed: {result.get('error')}"

    await update.message.reply_text(msg, parse_mode="Markdown")
```

**Register Handlers** (add to `main()` in bot.py):
```python
app.add_handler(CommandHandler("flow", cmd_flow))
app.add_handler(CommandHandler("analyze", cmd_analyze))
app.add_handler(CommandHandler("options", cmd_options))
app.add_handler(CommandHandler("buyoption", cmd_buyoption))
app.add_handler(CommandHandler("closeoption", cmd_closeoption))
```

**Update `/start` help message** to include new commands.

---

### 6. Update `config.py` - Add Options Config

```python
# Unusual Whales API
UW_API_KEY = os.getenv("UW_API_KEY")

# Options Trading Parameters
OPTIONS_CONFIG = {
    "max_options_positions": 4,
    "max_position_value": 2000,
    "position_size_pct": 0.02,
    "max_portfolio_risk_options": 0.10,
    "default_contracts": 1,
    "max_contracts_per_trade": 10,
    "min_premium": 50,
    "max_premium": 1000,
    "min_days_to_exp": 7,
    "max_days_to_exp": 60,
    "profit_target_pct": 0.50,
    "stop_loss_pct": 0.50,
}

# Flow Scanning Parameters
FLOW_CONFIG = {
    "min_premium": 100000,      # $100K minimum
    "min_vol_oi": 1.0,          # Vol/OI > 1
    "min_score": 8,             # Minimum conviction score
    "max_analyze": 10,          # Max signals to analyze with Claude
    "scan_limit": 50,           # Raw alerts to fetch
}
```

---

## Testing Checklist

1. **Flow Scanner**:
   - [ ] API connection works with UW_API_KEY
   - [ ] Filters applied correctly (premium, vol/oi, sweeps)
   - [ ] Scoring produces expected results
   - [ ] Run: `python flow_scanner.py`

2. **Flow Analyzer**:
   - [ ] Alpaca price data fetched correctly
   - [ ] UW options data (IV rank, earnings) fetched
   - [ ] Claude generates valid JSON thesis
   - [ ] Run: `python flow_analyzer.py`

3. **Options Executor**:
   - [ ] Contract lookup finds correct options
   - [ ] Position sizing respects limits
   - [ ] Orders submit successfully (paper)
   - [ ] Run: `python options_executor.py`

4. **Database**:
   - [ ] Tables created on first run
   - [ ] Flow signals logged correctly
   - [ ] Options trades tracked
   - [ ] Run: `python -c "from db import init_options_tables; init_options_tables()"`

5. **Bot Commands**:
   - [ ] `/flow` returns formatted results
   - [ ] `/analyze` calls Claude successfully
   - [ ] `/options` shows positions
   - [ ] `/buyoption` executes with confirmation
   - [ ] `/closeoption` closes positions

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                               │
│  Commands: /flow /analyze /options /buyoption /closeoption      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  FLOW_SCANNER   │ │  FLOW_ANALYZER  │ │OPTIONS_EXECUTOR │
│  flow_scanner.py│ │  flow_analyzer.py│ │options_executor │
│                 │ │                 │ │                 │
│ - UW API client │ │ - Alpaca data   │ │ - Find contracts│
│ - Flow alerts   │ │ - UW options    │ │ - Position size │
│ - Signal scoring│ │ - Claude thesis │ │ - Place orders  │
│                 │ │ - Recommendations│ │ - Exit mgmt    │
└─────────────────┘ └─────────────────┘ └─────────────────┘
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                ┌───────────────────┐
                │     DATABASE      │
                │   data/trades.db  │
                │                   │
                │ - flow_signals    │
                │ - options_trades  │
                │ - flow_scan_hist  │
                └───────────────────┘
```

---

## Key Dependencies

Add to `requirements.txt`:
```
requests>=2.28.0
```

Existing dependencies should cover:
- alpaca-py
- anthropic
- python-telegram-bot
- python-dotenv

---

## Notes for Claude Code

1. **Follow existing patterns** - Match the style of scanner.py, agent.py, executor.py
2. **Error handling** - Wrap API calls in try/except, return error dicts
3. **Logging** - Use print() with timestamps like existing code
4. **Type hints** - Add type hints to all functions
5. **Database** - Call init_options_tables() in get_connection() or migrate_tables()
6. **Global state** - Use module-level `last_flow_results` dict for bot state (same pattern as `last_scan_results`)
