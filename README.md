# Momentum Trading Agent

An automated momentum trading system that scans for high-momentum stocks, uses Claude AI for trade selection, executes trades via Alpaca, and monitors positions for reversal signals.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        TELEGRAM BOT                              │
│                    (momentum-agent.service)                      │
│         Commands: /status /scan /execute /close /positions      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   SCANNER     │ │    AGENT      │ │   EXECUTOR    │
│  scanner.py   │ │   agent.py    │ │  executor.py  │
│               │ │               │ │               │
│ - Fetch data  │ │ - Claude API  │ │ - Place orders│
│ - Calculate   │ │ - Reasoning   │ │ - Trailing    │
│   signals     │ │ - Decisions   │ │   stops       │
└───────────────┘ └───────────────┘ └───────────────┘
        │                                   │
        └───────────────┬───────────────────┘
                        ▼
              ┌───────────────────┐
              │     DATABASE      │
              │  data/trades.db   │
              └───────────────────┘
                        ▲
                        │
┌─────────────────────────────────────────────────────────────────┐
│                    POSITION MONITOR                              │
│               (position-monitor.timer)                           │
│            Runs every 30 min during market hours                 │
│         Detects reversals → Sends Telegram alerts                │
└─────────────────────────────────────────────────────────────────┘
```

## Components

| File | Description |
|------|-------------|
| `bot.py` | Telegram bot interface - all user interactions |
| `scanner.py` | Market scanner - fetches data & calculates momentum signals |
| `agent.py` | Claude AI integration for trade reasoning |
| `executor.py` | Alpaca order execution & position management |
| `monitor.py` | Position monitor for reversal signal detection |
| `db.py` | SQLite database for trade history, DQL training data, poor signals & metrics |
| `jobs.py` | Background jobs for DQL data collection & maintenance |
| `config.py` | Configuration parameters |
| `main.py` | CLI entry point for manual operations |
| `flow_scanner.py` | Unusual Whales API client for options flow signals |
| `flow_analyzer.py` | Signal enrichment and Claude thesis generation |
| `options_executor.py` | Alpaca options trading and position management |
| `options_agent.py` | AI agents for position review, sizing, and portfolio management |
| `flow_job.py` | Automated options flow job (exit checks, DTE alerts) |
| `flow_listener.py` | Real-time flow monitoring service (60s polling, Claude validation) |
| `options_monitor.py` | Real-time position monitoring with AI-driven exits (45s polling) |

## Services

### 1. Telegram Bot (`momentum-agent.service`)

**Status:** Always running
**Restart:** Automatic on failure

```bash
# Control commands
sudo systemctl status momentum-agent
sudo systemctl restart momentum-agent
sudo journalctl -u momentum-agent -f  # View logs
```

### 2. Flow Listener (`flow-listener.service`)

**Status:** Always running (active during market hours only)
**Polling:** Every 60 seconds during 9:30 AM - 4:00 PM ET
**Restart:** Automatic on failure

```bash
# Control commands
sudo systemctl status flow-listener
sudo systemctl restart flow-listener
sudo journalctl -u flow-listener -f  # View logs
tail -f logs/flow_listener.log       # View logs
```

### 3. Options Monitor (`options-monitor.service`)

**Status:** Always running (active during market hours only)
**Polling:** Every 45 seconds during 9:30 AM - 4:00 PM ET
**Restart:** Automatic on failure

```bash
# Control commands
sudo systemctl status options-monitor
sudo systemctl restart options-monitor
tail -f logs/options_monitor.log       # View logs
```

### 4. Position Monitor (`position-monitor.timer`)

**Schedule:** Every 30 minutes on weekdays
**Market Hours Check:** Built-in (9:30 AM - 4:00 PM ET)

```bash
# Control commands
sudo systemctl status position-monitor.timer
sudo systemctl list-timers position-monitor.timer
sudo systemctl restart position-monitor.timer

# Manual run (bypass market hours check)
./venv/bin/python monitor.py --force
```

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/status` | Account overview + position monitor status |
| `/scan` | Run momentum scan for candidates |
| `/candidates` | View last scan results |
| `/execute SYMBOL` | Execute trade for symbol |
| `/close SYMBOL` | Close position manually |
| `/positions` | View current positions |
| `/orders` | View open orders |
| `/history` | Trade history |
| `/performance` | Signal performance stats |
| `/metrics` | Baseline performance since inception |
| `/weekly` | Last 7 days report with SPY comparison |
| `/monthly` | Last 30 days with weekly breakdown |
| `/export` | Export trades & candidates to CSV |
| `/error` | Show recent errors from logs |

### Options Flow Commands

| Command | Description |
|---------|-------------|
| `/flow` | Scan options flow from Unusual Whales |
| `/analyze` | Analyze top signals with Claude thesis |
| `/options` | View options positions & performance |
| `/greeks` | View portfolio Greeks |
| `/expirations` | DTE alerts and roll suggestions |
| `/flowperf` | Signal factor performance stats |
| `/buyoption SYMBOL` | Execute options trade (requires confirm) |
| `/closeoption CONTRACT` | Close options position |
| `/reconcile` | Sync options DB with Alpaca positions |

### Options AI Agents Commands

| Command | Description |
|---------|-------------|
| `/optionsreview` | AI-powered review of each position with HOLD/CLOSE/ROLL/TRIM recommendations |
| `/portfolioreview` | AI portfolio risk assessment with risk scoring and rebalancing suggestions |
| `/optionsmonitor` | Run full AI monitoring cycle (positions + portfolio + exits + expirations) |

## Configuration

### Environment Variables (`.env`)

```bash
# Alpaca API (Paper Trading)
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Anthropic API (Claude)
ANTHROPIC_API_KEY=your_key

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_ADMIN_ID=your_telegram_user_id
```

### Trading Parameters (`config.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_price` | $5.00 | Minimum stock price |
| `min_avg_volume` | 500,000 | Minimum average daily volume |
| `min_volume_surge` | 1.3x | Today's volume vs 20D average |
| `min_sma_alignment` | True | Require 7 > 20 > 30 SMA |
| `min_close_position` | 0.6 | Where price closes in daily range |
| `min_roc_10d` | 3% | Minimum 10-day rate of change |
| `min_gap_up` | 1% | Minimum gap up for breakout |
| `max_positions` | 6 | Maximum total concurrent positions |
| `position_size_pct` | 10% | Portfolio allocation per trade |
| `max_portfolio_risk` | 60% | Maximum deployed capital |
| `trailing_stop_pct` | 5% | Trailing stop percentage |

### Per-Cap Configuration (`CAP_CONFIG`)

Different thresholds and limits per market cap category:

| Parameter | Large | Mid | Small |
|-----------|-------|-----|-------|
| `max_positions` | 2 | 2 | 2 |
| `max_buys_per_scan` | 2 | 2 | 2 |
| `min_volume_surge` | 1.3x | 1.3x | 1.5x |
| `min_gap_up` | 1% | 1% | 3% |
| `min_roc_10d` | 3% | 3% | 5% |

**Small caps require higher thresholds** to filter out noise and ensure stronger momentum signals.

### Universe Classification (`data/universe.json`)

| Category | Count | Market Cap |
|----------|-------|------------|
| Large | 108 | > $10B |
| Mid | 61 | $2B - $10B |
| Small | 42 | < $2B |

## Quick Filter (Stage 1)

The scanner uses a **two-stage filtering process**. Stage 1 uses real-time snapshots with time-normalized Relative Volume (RVOL):

| Filter | Threshold | Purpose |
|--------|-----------|---------|
| Price | > $5 | Liquidity requirement |
| Price Change | > 0.5% | Buyers in control |
| **RVOL** | >= 1.2x | Institutional interest |

**RVOL Calculation:**
```
projected_volume = current_volume / time_fraction
rvol = projected_volume / previous_day_volume
```

This normalizes volume by time-of-day, allowing early detection of unusual activity. A stock with 20M volume 1 hour into trading vs 80M full-day yesterday has RVOL = (20M / 0.15) / 80M = **1.67x** - strong institutional interest.

## Momentum Signals (Stage 2)

Candidates passing Stage 1 get deep analysis with 30-day OHLCV bars.

### Stage 2 Filter Requirements

Thresholds vary by market cap (see Per-Cap Configuration above):

| Filter | Large/Mid | Small | Description |
|--------|-----------|-------|-------------|
| **Gap Up** | >= 1% | >= 3% | Gap from yesterday's close |
| **Volume Surge** | >= 1.3x | >= 1.5x | vs 20-day average |
| **ROC 10D** | >= 3% | >= 5% | 10-day rate of change |

**Momentum Breakout** captures today's price action:
- **Gap + Follow-through**: Opened above threshold AND trading above today's open
- **5-Day Breakout**: Current price exceeding the 5-day high

### Composite Score (0-20)

| Signal | Points | Criteria |
|--------|--------|----------|
| SMA Aligned | +5 | 7-day > 20-day > 30-day SMA |
| Volume Surge | +2/+3 | >1.3x / >1.5x vs 20D avg |
| Momentum Breakout | +3 | Gap+follow-through OR 5D breakout |
| Intraday Strength | +1/+2 | >0.5 / >0.7 in today's range |
| 10D ROC | +2/+4 | >5% / >10% rate of change |
| Near 52W High | +3 | Within 5% of 52-week high |

## Reversal Signals (Monitor)

The monitor calculates a **reversal score** (0-13) for position alerts:

| Signal | Points | Criteria |
|--------|--------|----------|
| SMA Bearish Cross | +3 | 7-day < 20-day SMA |
| Close in Lower 30% | +2 | Price closes in lower 30% of daily range |
| Distribution Volume | +3 | Red day + volume > 1.5x average |
| RSI Breakdown | +2 | RSI was >70, now <60 |
| Failed Breakout | +3 | Hit 5-day high but closing red |

**Alert Thresholds & Actions:**

| Score | Severity | Action |
|-------|----------|--------|
| 0-2 | None | No action, logged only |
| 3-4 | Weak | Telegram alert sent, manual close recommended |
| **5+** | **Strong** | **AUTO-CLOSE** - Position automatically closed + logged as poor signal |

**Minimum Hold Protection** (added 2026-02-03):
- Positions held < 2 days are **protected from auto-close**
- Alerts still sent, but no automatic exit
- Allows positions to survive temporary whipsaws/pullbacks
- Reasoning: Same-day exits had 6.2% win rate vs 62.5% for 3+ day holds

**Winner Protection** (added 2026-02-03):
- Positions with P/L >= 5% are **protected from auto-close**
- Lets winners run through temporary pullbacks
- User can still manually close via `/close SYMBOL`

**RSI Enforcement** (added 2026-02-03):
- Trades blocked at execution if RSI >= 70
- Prevents entering overbought stocks even from watchlist
- Applied in `executor.py` as final safety check

The auto-close feature can be configured via Telegram bot commands (`/set autoclose on|off`, `/set threshold N`).

## Self-Learning Loop

The system tracks "poor signals" - trades that looked good at entry but triggered reversal exits - to improve signal quality over time.

### How It Works

1. **Detection**: When a position is auto-closed due to reversal (score >= 5), it's logged as a poor signal
2. **Pattern Analysis**: Entry signals that led to the poor trade are recorded for pattern recognition
3. **Agent Awareness**: The agent receives poor signal patterns when making decisions
4. **Weekly Review**: Poor signal summaries included in weekly/monthly reports

### Poor Signals Table

| Column | Description |
|--------|-------------|
| symbol | Stock symbol |
| entry_signals | JSON of original entry signals |
| exit_reason | Why position was closed |
| reversal_score | Score that triggered the close |
| reversal_signals | Which reversal signals fired |
| pnl_pct | Profit/loss at exit |
| holding_days | How long position was held |

### Skip-Buy Logic

When all open positions are "healthy" (reversal score < threshold), the system:
- **Logs scan results** for record-keeping
- **Skips new buys** to let winners run without interference
- Configurable via `skip_buys_when_healthy` and `healthy_threshold` in config

This prevents over-trading during strong momentum runs.

## Logs

| Log File | Description |
|----------|-------------|
| `logs/monitor.log` | Position monitor output |
| `journalctl -u momentum-agent` | Bot service logs |
| `journalctl -u position-monitor` | Monitor service logs |

View logs:
```bash
# Bot logs (live)
sudo journalctl -u momentum-agent -f

# Monitor logs
tail -f logs/monitor.log

# Last monitor run
sudo journalctl -u position-monitor -n 50
```

## Database Schema

Located at `data/trades.db`:

### trades
| Column | Type | Description |
|--------|------|-------------|
| symbol | TEXT | Stock symbol |
| entry_date | TEXT | Entry timestamp |
| entry_price | REAL | Entry price |
| quantity | INTEGER | Number of shares |
| signals | TEXT | JSON of entry signals |
| exit_date | TEXT | Exit timestamp |
| exit_price | REAL | Exit price |
| exit_reason | TEXT | Why position was closed |
| pnl_amount | REAL | Profit/loss in dollars |
| pnl_pct | REAL | Profit/loss percentage |
| status | TEXT | 'open' or 'closed' |

### scans
| Column | Type | Description |
|--------|------|-------------|
| scan_date | TEXT | When scan was run |
| candidates | TEXT | JSON of candidates found |
| decision | TEXT | JSON of agent decision |
| executed_symbol | TEXT | Symbol that was traded |

### position_checks
| Column | Type | Description |
|--------|------|-------------|
| check_time | TEXT | When check was run |
| symbol | TEXT | Stock symbol |
| score | INTEGER | Reversal score (0-13) |
| signals | TEXT | JSON of detected signals |
| pnl_pct | REAL | Current P/L percentage |
| alert_sent | INTEGER | 1 if alert was sent |

### market_snapshots (DQL Training)
| Column | Type | Description |
|--------|------|-------------|
| scan_id | INTEGER | Links to candidate_snapshots |
| timestamp | TEXT | When snapshot was taken |
| spy_price | REAL | SPY price at scan time |
| spy_change_1d | REAL | SPY daily change % |
| spy_trend | TEXT | up/down/sideways |
| vix_level | REAL | VIX proxy level |
| market_breadth | REAL | % advancing stocks |
| scan_type | TEXT | open/midday/close |

### candidate_snapshots (DQL Training)
| Column | Type | Description |
|--------|------|-------------|
| scan_id | INTEGER | Links to market_snapshots |
| symbol | TEXT | Stock symbol |
| price | REAL | Price at scan time |
| composite_score | INTEGER | Momentum score (0-20) |
| roc_5/roc_10 | REAL | Rate of change |
| rsi_14 | REAL | RSI indicator |
| atr_14 | REAL | Average true range |
| action | TEXT | candidate/filtered_out/skipped |
| price_Xd_later | REAL | Outcome prices (1/5/10 day) |

### daily_performance
| Column | Type | Description |
|--------|------|-------------|
| date | TEXT | Trading date |
| starting_equity | REAL | Equity at start |
| ending_equity | REAL | Equity at end |
| trades_opened | INTEGER | Trades opened today |
| trades_closed | INTEGER | Trades closed today |
| win_count | INTEGER | Winning trades |
| loss_count | INTEGER | Losing trades |
| spy_change | REAL | SPY daily change % |

### poor_signals (Self-Learning)
| Column | Type | Description |
|--------|------|-------------|
| symbol | TEXT | Stock symbol |
| trade_id | INTEGER | Links to trades table |
| entry_date | TEXT | Original entry date |
| exit_date | TEXT | When closed due to reversal |
| entry_price | REAL | Entry price |
| exit_price | REAL | Exit price |
| pnl_pct | REAL | Profit/loss percentage |
| holding_days | INTEGER | Days held before reversal |
| reversal_score | INTEGER | Score that triggered close |
| reversal_signals | TEXT | JSON of reversal signals |
| entry_signals | TEXT | JSON of original entry signals |
| reviewed | INTEGER | 0=unreviewed, 1=reviewed |

## Stock Universe

Located at `data/universe.json`:
- 211 liquid US stocks
- Sources: S&P 500, NASDAQ 100, high-volume mid-caps
- All symbols validated against Alpaca API
- Updated as needed

## Manual CLI Usage

```bash
# Run momentum scan (dry run - no trades)
./venv/bin/python main.py scan --dry-run

# Run momentum scan (live execution)
./venv/bin/python main.py scan

# Check positions
./venv/bin/python main.py positions

# View trade history
./venv/bin/python main.py history

# Run position monitor manually
./venv/bin/python monitor.py --force
```

## Installation

```bash
# Clone and setup
cd /home/ubuntu/momentum-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Install systemd services
sudo cp momentum-agent.service /etc/systemd/system/
sudo cp position-monitor.service /etc/systemd/system/
sudo cp position-monitor.timer /etc/systemd/system/

# Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable momentum-agent
sudo systemctl start momentum-agent
sudo systemctl enable position-monitor.timer
sudo systemctl start position-monitor.timer
```

## Workflow

### Entry Flow
1. User triggers `/scan` or scheduled scan runs
2. Scanner fetches snapshots for ~150 stocks
3. Quick filter: price >$5, up >1%, volume increasing
4. Deep analysis: 30-day bars, calculate all signals
5. Filter by minimum criteria, return top 10
6. Claude AI analyzes candidates with trade history
7. Returns TRADE/SKIP/WATCH decisions with reasoning
8. User executes with `/execute SYMBOL`
9. Market order + trailing stop placed via Alpaca

### Exit Flow
1. **Trailing Stop:** 5% trailing stop triggers automatically (covers entire position)
2. **Auto-Close:** Monitor detects strong reversal (score >= 5), auto-closes position + logs as poor signal
3. **Alert-based:** Monitor detects weak reversal (score 3-4), user decides
4. **Manual:** User sends `/close SYMBOL`

### Self-Learning Flow
1. Position closed due to reversal → logged to `poor_signals` table
2. Entry signals that led to reversal are recorded
3. Agent receives poor signal patterns in next scan prompt
4. Weekly reports summarize poor signals with recommendations
5. User reviews and adjusts agent prompt if patterns emerge

### Trailing Stop Behavior
- When adding to an existing position, any existing trailing stops are cancelled
- A new trailing stop is placed covering the **entire position** (old + new shares)
- This ensures consistent protection across the full holding

### Monitoring Flow (every 30 min on weekdays)
1. Timer triggers `position-monitor.service`
2. Script checks if within market hours (9:30 AM - 4:00 PM ET)
3. Fetches all stock positions (excludes options)
4. For each position:
   - Fetch 30-day historical bars
   - Calculate reversal signals (score 0-13)
   - Log to database
   - **Score >= 5:** Auto-close position, log as poor signal, send notification
   - **Score 3-4:** Send Telegram alert, user decides
5. User can respond with `/close SYMBOL` for manual exit

### Scan Locking
- File-based lock prevents concurrent scans from interfering
- Only one scan can run at a time across all processes
- Lock automatically released when scan completes

### Skip-Buy Mode
When `skip_buys_when_healthy` is enabled and all positions have reversal score < `healthy_threshold`:
1. Scan runs normally, candidates found
2. Results logged to database for tracking
3. **No new buys executed** - lets winners run
4. Telegram summary indicates "SKIP MODE"

## Options Flow Trading (Added 2026-02-03)

Options flow trading system integrated with Unusual Whales API for institutional-grade options signals.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                               │
│    /flow /analyze /options /greeks /expirations /flowperf       │
│         /buyoption /closeoption /reconcile                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  FLOW_SCANNER   │ │  FLOW_ANALYZER  │ │OPTIONS_EXECUTOR │
│  flow_scanner.py│ │ flow_analyzer.py│ │options_executor │
│                 │ │                 │ │                 │
│ - UW API client │ │ - Alpaca data   │ │ - Find contracts│
│ - Flow alerts   │ │ - Claude thesis │ │ - Position size │
│ - Signal scoring│ │ - Recommendations│ │ - Greeks calc   │
│                 │ │                 │ │ - Smart orders  │
└─────────────────┘ └─────────────────┘ └─────────────────┘
          │                 │                   │
          └─────────────────┴───────────────────┘
                            │
                            ▼
              ┌───────────────────────┐
              │  DATABASE (db.py)     │
              │  - flow_signals       │
              │  - options_trades     │
              │  - flow_signal_outcomes│
              └───────────────────────┘
```

### Options Commands

| Command | Description |
|---------|-------------|
| `/flow` | Scan options flow from Unusual Whales |
| `/analyze` | Analyze top signals with Claude thesis |
| `/options` | View options positions & performance |
| `/greeks` | Portfolio Greeks with sector allocation |
| `/expirations` | DTE alerts with roll suggestions |
| `/flowperf` | Signal factor performance analysis |
| `/buyoption SYMBOL` | Execute options trade (requires confirm) |
| `/closeoption CONTRACT` | Close options position |
| `/reconcile` | Sync options DB with Alpaca positions |

### Flow Signal Scoring (0-20)

| Signal | Points | Criteria |
|--------|--------|----------|
| Sweep | +3 | Intermarket sweep (urgency) |
| Ask Side | +2 | Bought at ask (conviction) |
| Premium $100K+ | +3 | Serious money |
| Premium $250K+ | +2 | Very high premium (bonus) |
| Vol/OI > 1 | +2 | Unusual activity |
| Vol/OI > 3 | +1 | Very high ratio (bonus) |
| Floor Trade | +2 | Institutional origin |
| Opening Trade | +2 | New position |
| OTM | +1 | Out of the money |
| Near Earnings | +1 | Within 14 days |
| Low DTE | +1 | Under 30 days to expiration |

Minimum score for consideration: **8/20**

### Options Safety Features

| Safety Check | Threshold | Action |
|--------------|-----------|--------|
| Bid-Ask Spread | > 15% | Block trade |
| Minimum Bid | < $0.05 | Block trade |
| Bid Size | < 10 | Block trade |
| Order Type | - | Limit orders at mid + 2% |
| Sector Concentration | > 50% | Block new position |
| Single Underlying | > 30% | Block new position |
| Earnings Blackout | 2 days before | Block trade |

### Greeks Tracking

The system calculates and logs Greeks at trade entry and exit for learning:

| Greek | Description | Use |
|-------|-------------|-----|
| Delta | Price sensitivity | Directional exposure |
| Gamma | Delta sensitivity | Risk acceleration |
| Theta | Time decay | Daily cost of holding |
| Vega | IV sensitivity | Volatility exposure |
| IV | Implied volatility | Market expectation |

**Portfolio Greeks** (`/greeks`):
- **Net Delta**: Aggregate directional exposure (shares equivalent)
- **Total Theta**: Daily portfolio decay ($)
- **Sector Allocation**: % exposure by sector with concentration warnings

### DTE Alerts & Roll Suggestions

The `/expirations` command monitors positions approaching expiration:

| DTE | Severity | Message |
|-----|----------|---------|
| <= 0 | CRITICAL | "EXPIRED - Close immediately" |
| <= 3 | HIGH | "Expiring - Consider closing or rolling" |
| <= 7 | MEDIUM | "Monitor theta decay" |

For HIGH severity, the system suggests rolls:
- Same strike, 3-4 weeks out
- Shows roll cost (debit/credit)
- ITM positions flagged for assignment risk

### Sector Concentration

Positions are categorized by sector to prevent over-concentration:

| Sector | Example Symbols |
|--------|-----------------|
| tech | AAPL, MSFT, NVDA, AMD, META |
| finance | JPM, BAC, GS, V, MA |
| healthcare | UNH, JNJ, PFE, LLY, MRK |
| energy | XOM, CVX, COP, SLB |
| consumer | AMZN, TSLA, HD, NKE, WMT |
| industrial | CAT, BA, HON, UPS, GE |
| index | SPY, QQQ, IWM, DIA |

**Limits**:
- Max single sector: 50% of options portfolio
- Max single underlying: 30% of options portfolio

### Signal Outcome Learning

Closed trades are logged to `flow_signal_outcomes` for factor analysis:

```
/flowperf shows:
- Win rate by score tier (elite 15+, high 12-14, medium 10-11, low 8-9)
- Win rate by factor (sweep, ask side, floor, opening)
- Win rate by premium tier (very_high, high, medium, low)
- Avg P/L and holding period per tier
```

This enables data-driven refinement of signal weights.

### Options Workflow

1. **Scan**: `/flow` fetches signals from Unusual Whales
2. **Filter**: Signals scored (min 8 points) and ranked
3. **Analyze**: `/analyze` enriches with:
   - Alpaca price data and technicals
   - Claude thesis with conviction score
   - BUY/SKIP/WATCH recommendation
4. **Pre-Trade Checks**:
   - Earnings blackout (2 days before)
   - Sector concentration limits
   - Liquidity check (spread < 15%, bid > $0.05)
5. **Execute**: `/buyoption SYMBOL confirm` places limit order at mid + 2%
6. **Greeks Logged**: Entry delta, gamma, theta, vega, IV, DTE
7. **Monitor**:
   - `/greeks` for portfolio exposure
   - `/expirations` for DTE alerts
   - `/options` for P/L tracking
8. **Exit**: `/closeoption CONTRACT` with exit Greeks logged
9. **Learn**: Signal outcome recorded to `flow_signal_outcomes`

### Options Automation Schedule

The options flow system runs via a real-time listener service and cron jobs:

**Real-Time Flow Listener (`flow-listener.service`)**

| Service | Schedule | Description |
|---------|----------|-------------|
| `flow_listener.py` | Every 60s during market hours | Poll UW API, Claude validation, auto-execute |

The flow listener replaces the previous cron-based `flow_job.py full` jobs with real-time monitoring:
- Polls Unusual Whales API every 60 seconds
- Pre-filters signals (premium >= $100K, dedupe, exclude index options)
- Sends to Claude for validation with profit-focused prompt
- Auto-executes if conviction >= 75%, alerts if >= 50%
- Safety gate enforces hard limits (daily cap, Greeks, concentration)

**Cron Jobs (Exit Checks & Alerts)**

| Job | Schedule (ET) | UTC | Description |
|-----|---------------|-----|-------------|
| `flow_job.py exits` | Every 30 min | 14:30-20:30 | Check profit target (50%) / stop loss (50%) |
| `flow_job.py dte` | 9:30 AM | 14:30 | DTE alerts for expiring positions |

**Telegram Notifications:**
- Auto-execution: Contract, Greeks, cost, thesis
- Alerts: Signal details, conviction, thesis
- Blocked trades: Safety gate reason
- Exit checks: P/L, exit reason
- DTE alerts: Expiring positions with roll suggestions

**Manual Override:** You can still use `/flow`, `/analyze`, `/buyoption` for manual control.

### Flow Listener Configuration (Added 2026-02-04)

The flow listener service provides real-time options flow monitoring with Claude AI validation.

**Three-Layer Safety Architecture:**

```
┌─────────────────────────────────────────────────────────────────┐
│                    FLOW LISTENER SERVICE                         │
│                   (flow-listener.service)                        │
│              Polls every 60s during market hours                 │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────────────┐
          ▼               ▼                       ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐
│   LAYER 1       │ │    LAYER 2      │ │       LAYER 3           │
│   Pre-Filter    │ │ Claude Validate │ │     Safety Gate         │
│                 │ │                 │ │                         │
│ - Premium≥$100K │ │ - Profit focus  │ │ - Daily limit (3)       │
│ - Dedupe        │ │ - Conviction %  │ │ - Position limits       │
│ - Exclude index │ │ - Thesis        │ │ - Delta/Theta limits    │
│                 │ │ - Risk factors  │ │ - Sector concentration  │
└─────────────────┘ └─────────────────┘ └─────────────────────────┘
          │                 │                       │
          └─────────────────┼───────────────────────┘
                            ▼
              ┌───────────────────────┐
              │      LAYER 4          │
              │  Options Executor     │
              │                       │
              │ - Liquidity check     │
              │ - Limit orders        │
              │ - Greeks logging      │
              └───────────────────────┘
```

**Flow Listener Config (`FLOW_LISTENER_CONFIG`):**

| Parameter | Value | Description |
|-----------|-------|-------------|
| `poll_interval_seconds` | 60 | Poll frequency |
| `min_premium` | 100000 | $100K minimum filter |
| `max_signals_per_cycle` | 10 | Max signals to Claude |
| `excluded_symbols` | SPX, SPXW, NDX, XSP | Index options excluded |
| `min_conviction_execute` | 75 | Auto-execute threshold |
| `min_conviction_alert` | 50 | Alert-only threshold |
| `max_executions_per_day` | 3 | Daily execution cap |
| `max_delta_per_100k` | 150 | Max delta exposure |
| `max_theta_pct` | 0.003 | Max 0.3% daily theta |
| `enable_auto_execute` | True | Master switch |

**Circuit Breaker:**
- Opens after 5 consecutive errors
- 5-minute cooldown before retry
- Telegram notification on open/close

**Service Commands:**
```bash
sudo systemctl status flow-listener    # Check status
sudo systemctl restart flow-listener   # Restart
tail -f logs/flow_listener.log         # View logs
```

### Options Configuration

```python
OPTIONS_CONFIG = {
    "max_options_positions": 4,
    "max_position_value": 2000,
    "position_size_pct": 0.02,        # 2% of portfolio
    "profit_target_pct": 0.50,        # 50% profit target
    "stop_loss_pct": 0.50,            # 50% stop loss
    "min_days_to_exp": 14,            # Minimum DTE
    "max_days_to_exp": 60,            # Maximum DTE
    "max_portfolio_risk_options": 0.10, # Max 10% in options
}

OPTIONS_SAFETY = {
    "max_spread_pct": 15.0,           # Block if spread > 15%
    "min_bid": 0.05,                  # Block penny options
    "min_bid_size": 10,               # Minimum liquidity
    "max_single_sector_pct": 50.0,    # Concentration limit
    "max_single_underlying_pct": 30.0,
    "earnings_blackout_days": 2,
    "roll_alert_dte": 7,              # Alert at 7 DTE
    "critical_dte": 3,                # Critical at 3 DTE
    "use_limit_orders": True,         # No market orders
    "limit_price_buffer_pct": 2.0,    # Mid + 2% for buys
}
```

### Options Database Tables

**options_trades**:
| Column | Description |
|--------|-------------|
| contract_symbol | OCC symbol (e.g., AAPL260220C00175000) |
| underlying | Stock symbol |
| option_type | call/put |
| strike, expiration | Contract details |
| entry_price, exit_price | Trade prices |
| entry_delta, entry_theta, entry_iv | Greeks at open |
| exit_delta, exit_theta, exit_iv | Greeks at close |
| entry_dte, exit_dte | Days to expiration |
| signal_score, thesis | Signal data |

**flow_signal_outcomes**:
| Column | Description |
|--------|-------------|
| was_sweep, was_ask_side | Signal factors |
| was_floor, was_opening | Institutional indicators |
| premium_tier, vol_oi_tier | Size tiers |
| entry_delta, entry_theta, entry_iv | Entry Greeks |
| max_gain_pct, max_loss_pct | Trade extremes |
| actual_pnl_pct, holding_days | Final outcome |
| was_winner, hit_target, hit_stop | Result flags |

## Options AI Agents (Added 2026-02-03)

Three Claude-powered AI agents for intelligent options position management with rules-based fallbacks.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM BOT                               │
│     /optionsreview  /portfolioreview  /optionsmonitor           │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│    POSITION     │ │    POSITION     │ │   PORTFOLIO     │
│    REVIEWER     │ │     SIZER       │ │    MANAGER      │
│                 │ │                 │ │                 │
│ - HOLD/CLOSE/   │ │ - Contract qty  │ │ - Risk scoring  │
│   ROLL/TRIM     │ │ - Greeks impact │ │ - Rebalancing   │
│ - Urgency level │ │ - Sector check  │ │ - Roll suggests │
│ - Risk factors  │ │ - Conviction    │ │ - Concentration │
└─────────────────┘ └─────────────────┘ └─────────────────┘
          │                 │                   │
          └─────────────────┴───────────────────┘
                            │
              ┌─────────────┴─────────────┐
              │     options_agent.py      │
              │  Claude Agent + Fallback  │
              └───────────────────────────┘
```

### Agent 1: Options Position Reviewer

Reviews individual positions and recommends actions.

**Decisions:**
| Decision | When |
|----------|------|
| **HOLD** | Thesis intact, DTE > 10, theta acceptable |
| **CLOSE** | DTE <= 3 and profitable/OTM, loss > 50%, high gamma risk |
| **ROLL** | DTE <= 7 and want to maintain exposure |
| **TRIM** | Position too large, lock in partial profits |

**Urgency Levels:**
| Level | Criteria | Action |
|-------|----------|--------|
| `critical` | Expiring today/tomorrow, large loss | Act immediately |
| `high` | DTE <= 3, significant risk | Act within hours |
| `medium` | DTE <= 7, moderate risk | Act within 1-2 days |
| `low` | Normal parameters | Monitor only |

**Risk Factors Assessed:**
- Theta Risk (daily decay vs potential gain)
- Gamma Risk (delta swings near expiry)
- Vega Risk (IV changes impact)
- Directional Risk (delta vs market conditions)
- Time Risk (DTE and theta acceleration)
- Liquidity Risk (ability to exit at fair price)

### Agent 2: Options Position Sizer

Calculates optimal contract quantity for new trades.

**Base Rules:**
- Never risk > 2% of portfolio on single options trade
- Maximum 10% total portfolio in options
- Consider existing Greeks exposure
- Account for sector concentration

**Size Adjustments:**

| Factor | Effect | Example |
|--------|--------|---------|
| Signal score >= 15 | +50% size | High conviction |
| Signal score >= 12 | +25% size | Good signal |
| Signal score < 10 | -50% size | Weak signal |
| IV rank < 30% | +25% size | Cheap premium |
| IV rank > 50% | -25% size | Expensive premium |
| Sector > 35% | -50% size | Concentration risk |
| Short DTE < 14 | -25% size | Theta risk |

**Maximum Constraints:**
- Single underlying: Max 30% of options allocation
- Single sector: Max 50% of options allocation
- Max contracts per trade: 10

**Greeks Impact Assessment:**
The agent calculates expected portfolio changes:
- Delta impact (directional shift)
- Theta impact (daily decay change)
- Gamma concentration
- Vega exposure vs IV environment

### Agent 3: Options Portfolio Manager

Reviews overall portfolio and provides strategic recommendations.

**Health Levels:**
| Level | Risk Score | Action |
|-------|------------|--------|
| `healthy` | 0-25 | No action needed |
| `moderate_risk` | 26-50 | Monitor closely |
| `high_risk` | 51-75 | Action recommended |
| `critical` | 76-100 | Immediate action required |

**Risk Scoring (0-100):**
| Component | Points | Criteria |
|-----------|--------|----------|
| Theta decay | 0-20 | Daily decay as % of portfolio |
| Gamma concentration | 0-20 | High gamma positions near expiry |
| Delta imbalance | 0-20 | Net delta per $100K equity |
| Concentration risk | 0-20 | Single sector/position exposure |
| Expiration risk | 0-20 | Multiple positions same week |

**Key Metrics Monitored:**
- **Net Delta**: Healthy < |50| per $100K, Concerning > |100|
- **Daily Theta**: Healthy < 0.1% portfolio/day, Concerning > 0.2%
- **Sector Concentration**: Max 50% single sector

**Rebalancing Triggers:**
- Net delta > |100| per $100K equity
- Single sector > 50% of options
- Daily theta > 0.2% of portfolio
- Multiple positions DTE < 7

### Commands

| Command | Description |
|---------|-------------|
| `/optionsreview` | AI review of each position with urgency ratings |
| `/portfolioreview` | AI portfolio risk assessment with recommendations |
| `/optionsmonitor` | Full monitoring cycle (positions + portfolio + exits) |

### Fallback Logic

When Claude agent is unavailable, the system falls back to rules-based decisions:

**Position Reviewer Fallback:**
```
DTE <= 1 → CLOSE (critical)
DTE <= 3 + profit > 30% → CLOSE
DTE <= 3 + loss > 40% → CLOSE
DTE <= 7 + profit > 50% → CLOSE
Loss > 50% → CLOSE
Otherwise → HOLD
```

**Position Sizer Fallback:**
```
Base = 2% of equity / contract cost
Signal >= 15 → ×1.5
Signal >= 12 → ×1.25
Signal < 8 → ×0.5
Sector > 35% → ×0.5
Cap at max_contracts (10)
```

**Portfolio Manager Fallback:**
- Calculates risk score from thresholds
- Identifies positions needing roll (DTE < 7)
- Checks sector concentration limits

### Sample Output

**Position Review:**
```
🔍 Options Position Review

🔴 CRITICAL - Act Now:
• AAPL240315C00175000
  CLOSE: Expiring in 2 days with 31% profit - lock gains

🟡 MEDIUM - Monitor:
• NVDA240419C00500000: HOLD

🟢 LOW - Healthy: 1 position

Agent used: 2/3 reviews
```

**Portfolio Review:**
```
🟡 Options Portfolio Review

Assessment: Moderate Risk
Risk Score: 42/100

Summary: Portfolio has moderate risk with 65% tech concentration

Risk Factors:
• High tech sector concentration
• Elevated theta decay ($45/day)

Roll Suggestions:
• AAPL240315C175: Roll to 2024-04-19 (DTE=5)

Analysis: AI Agent (confidence: 82%)
```

### Logging

**File Logging:**
```
logs/options_agent.log
```

**Database Table:**
```sql
CREATE TABLE options_agent_logs (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    agent_name TEXT NOT NULL,
    input_data TEXT,      -- JSON
    output_data TEXT,     -- JSON
    agent_used INTEGER,   -- 1 if Claude, 0 if fallback
    fallback_reason TEXT,
    execution_time_ms REAL,
    confidence REAL
)
```

### Testing

```bash
# Run agent tests
./venv/bin/python options_agent.py

# Tests included:
# 1. Position Review (with/without agent)
# 2. Position Sizing (with/without agent)
# 3. Portfolio Review (with/without agent)
```

### Options Monitor Service (Added 2026-02-04)

Real-time options position monitoring with event-driven AI evaluation for intelligent exit decisions.

**Architecture**:
```
┌─────────────────────────────────────────────────────────────────┐
│                    OPTIONS MONITOR SERVICE                       │
│                  (options-monitor.service)                       │
│              Polls every 45s during market hours                 │
└─────────────────────────┬───────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────────────┐
          ▼               ▼                       ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────────────┐
│  CONTINUOUS     │ │  EVENT-DRIVEN   │ │     AUTO-EXIT           │
│  MONITORING     │ │  AI EVALUATION  │ │                         │
│                 │ │                 │ │                         │
│ - P/L tracking  │ │ - Loss > 15%    │ │ - Stop loss hit         │
│ - Greeks calc   │ │ - DTE <= 7      │ │ - Expiring tomorrow     │
│ - IV tracking   │ │ - Profit > 30%  │ │ - AI says CLOSE         │
│ - Portfolio     │ │ - High gamma    │ │ - Daily limit: 5        │
└─────────────────┘ └─────────────────┘ └─────────────────────────┘
```

**Key Difference from Flow Listener**: The monitor uses **event-driven AI calls** - when a position shows concerning conditions (losing money, approaching expiration, etc.), Claude is called immediately to decide what to do. This is NOT a scheduled daily review.

**AI Evaluation Triggers**:
| Condition | Trigger | Cooldown |
|-----------|---------|----------|
| Unrealized P/L <= -15% | `losing_money_X%` | 10 min |
| DTE <= 7 | `expiration_approaching` | 10 min |
| Unrealized P/L >= 30% | `profit_opportunity` | 10 min |
| High gamma near expiry | `high_gamma_risk` | 10 min |
| IV crush > 20% from entry | `iv_crush` | 10 min |

**AI Actions Executed**:
| Recommendation | Action |
|----------------|--------|
| CLOSE | Auto-exit position |
| ROLL | Close and reopen at later expiration |
| TRIM | Alert only (manual action) |
| HOLD | No action, position is fine |

**Adaptive Profit Targets by DTE**:
| DTE | Profit Target |
|-----|---------------|
| > 14 days | 50% |
| 7-14 days | 40% |
| 3-7 days | 30% |
| < 3 days | 20% |

**Config (`OPTIONS_MONITOR_CONFIG`)**:
| Parameter | Value | Description |
|-----------|-------|-------------|
| `poll_interval_seconds` | 45 | Check positions every 45s |
| `greeks_snapshot_interval_seconds` | 300 | Snapshot Greeks every 5 min |
| `ai_trigger_loss_pct` | 0.15 | Trigger AI if losing > 15% |
| `ai_trigger_profit_pct` | 0.30 | Trigger AI if profit > 30% |
| `ai_trigger_dte` | 7 | Trigger AI if DTE <= 7 |
| `ai_review_cooldown_minutes` | 10 | Don't re-evaluate same trigger |
| `gamma_risk_threshold` | 0.08 | High gamma warning |
| `iv_crush_threshold_pct` | 20 | IV drop % to trigger |
| `enable_auto_exit` | True | Master switch for auto-exits |
| `max_auto_exits_per_day` | 5 | Daily auto-exit limit |

**Database Tables**:
```sql
-- Service state persistence
options_monitor_state (
    last_check_time, last_ai_review_time,
    daily_exits_count, circuit_breaker_open
)

-- Greeks time-series for IV crush detection
position_greeks_history (
    contract_symbol, timestamp,
    delta, gamma, theta, vega, iv,
    underlying_price, option_price, dte
)

-- Alert audit trail
monitor_alerts (
    contract_symbol, alert_type, severity,
    message, action_taken
)
```

**Service Commands**:
```bash
sudo systemctl status options-monitor    # Check status
sudo systemctl restart options-monitor   # Restart
tail -f logs/options_monitor.log         # View logs
```

**Telegram Notifications**:
- Critical alerts (expiring, stop loss hit)
- AI recommendations with reasoning
- Auto-exit confirmations with P/L
- Roll confirmations
- Circuit breaker open/close

---

## Background Jobs (DQL Training)

Background jobs run via cron to collect training data for future DQL model development.

### Job Schedule (UTC)

| Job | Schedule | Time (ET) | Purpose |
|-----|----------|-----------|---------|
| `daily_snapshot` | 21:05 M-F | 4:05 PM | Log daily performance & equity |
| `update_outcomes` | 22:30 M-F | 5:30 PM | Fill in price_Xd_later for candidates |
| `backfill_dqn` | 22:45 M-F | 5:45 PM | Add closed trades to DQN experiences (added 2026-02-03) |
| `update_tracking` | */30 14-21 M-F | Every 30min | Track max gain/drawdown for open positions |
| `cleanup` | 0:00 Sunday | Midnight | Remove data older than 90 days |

### Running Jobs Manually

```bash
# Run all daily jobs
./venv/bin/python jobs.py all

# Run individual jobs
./venv/bin/python jobs.py daily_snapshot
./venv/bin/python jobs.py update_outcomes
./venv/bin/python jobs.py backfill_dqn
./venv/bin/python jobs.py update_tracking
./venv/bin/python jobs.py cleanup
```
