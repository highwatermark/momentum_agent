"""
Database Module - SQLite for trade history, DQL training data, and performance metrics
"""
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get database connection, creating tables if needed"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_tables(conn)
    migrate_tables(conn)
    return conn


def migrate_tables(conn: sqlite3.Connection):
    """Add missing columns to existing tables"""
    cursor = conn.cursor()

    # Get existing columns in trades table
    cursor.execute("PRAGMA table_info(trades)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    # Columns to add if missing
    new_columns = [
        ("spy_price", "REAL"),
        ("spy_sma20", "REAL"),
        ("spy_trend", "TEXT"),
        ("vix_level", "REAL"),
        ("sector_etf", "TEXT"),
        ("sector_performance", "REAL"),
        ("sma7", "REAL"),
        ("sma20", "REAL"),
        ("sma30", "REAL"),
        ("rsi_14", "REAL"),
        ("atr_14", "REAL"),
        ("volume_ratio", "REAL"),
        ("close_position", "REAL"),
        ("roc_5", "REAL"),
        ("roc_10", "REAL"),
        ("distance_from_52w_high", "REAL"),
        ("portfolio_cash_pct", "REAL"),
        ("open_positions_count", "INTEGER"),
        ("total_exposure_pct", "REAL"),
        ("portfolio_drawdown", "REAL"),
        ("holding_days", "INTEGER"),
        ("max_gain_during_trade", "REAL"),
        ("max_drawdown_during_trade", "REAL"),
        ("state_vector", "TEXT"),
        ("reward", "REAL"),
    ]

    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
                print(f"Added column {col_name} to trades table")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    print(f"Error adding column {col_name}: {e}")

    conn.commit()


def init_tables(conn: sqlite3.Connection):
    """Initialize database tables"""
    cursor = conn.cursor()

    # Enhanced trades table with DQL fields
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            entry_order_id TEXT,
            stop_order_id TEXT,
            signals TEXT,  -- JSON
            exit_date TEXT,
            exit_price REAL,
            exit_reason TEXT,
            pnl_amount REAL,
            pnl_pct REAL,
            status TEXT DEFAULT 'open',

            -- Market context at entry (DQL)
            spy_price REAL,
            spy_sma20 REAL,
            spy_trend TEXT,
            vix_level REAL,
            sector_etf TEXT,
            sector_performance REAL,

            -- Entry signals (DQL)
            sma7 REAL,
            sma20 REAL,
            sma30 REAL,
            rsi_14 REAL,
            atr_14 REAL,
            volume_ratio REAL,
            close_position REAL,
            roc_5 REAL,
            roc_10 REAL,
            distance_from_52w_high REAL,

            -- Portfolio state at entry (DQL)
            portfolio_cash_pct REAL,
            open_positions_count INTEGER,
            total_exposure_pct REAL,
            portfolio_drawdown REAL,

            -- Trade tracking (DQL)
            holding_days INTEGER,
            max_gain_during_trade REAL,
            max_drawdown_during_trade REAL,

            -- DQL training data
            state_vector TEXT,  -- JSON array
            reward REAL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Market snapshots table - log market conditions at each scan
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            spy_price REAL,
            spy_change_1d REAL,
            spy_change_5d REAL,
            spy_sma20 REAL,
            spy_above_sma20 INTEGER,
            vix_level REAL,
            vix_change_1d REAL,
            market_breadth REAL,
            sector_leader TEXT,
            sector_laggard TEXT,
            total_candidates_found INTEGER,
            scan_type TEXT
        )
    """)

    # Candidate snapshots - log ALL candidates for DQL negative sampling
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS candidate_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            symbol TEXT,

            -- Price data
            price REAL,
            sma7 REAL,
            sma20 REAL,
            sma30 REAL,
            atr_14 REAL,

            -- Signals
            volume_ratio REAL,
            close_position REAL,
            roc_5 REAL,
            roc_10 REAL,
            rsi_14 REAL,
            distance_from_52w_high REAL,
            composite_score REAL,
            momentum_breakout INTEGER,

            -- Action taken
            action TEXT,
            skip_reason TEXT,

            -- Outcome tracking (filled in later by jobs.py)
            price_1d_later REAL,
            price_5d_later REAL,
            price_10d_later REAL,
            would_have_won INTEGER,

            FOREIGN KEY (scan_id) REFERENCES market_snapshots(id)
        )
    """)

    # Daily performance table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            starting_equity REAL,
            ending_equity REAL,
            daily_pnl REAL,
            daily_pnl_pct REAL,
            trades_opened INTEGER,
            trades_closed INTEGER,
            win_count INTEGER,
            loss_count INTEGER,
            max_drawdown REAL,
            positions_held INTEGER,
            cash_pct REAL,
            spy_change REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Scans table (for tracking what we scanned)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            candidates TEXT,  -- JSON
            decision TEXT,  -- JSON
            executed_symbol TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Signal performance table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_name TEXT NOT NULL,
            signal_value TEXT,
            trade_id INTEGER,
            outcome TEXT,  -- 'win' or 'loss'
            pnl_pct REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        )
    """)

    # Watchlist table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            added_date TEXT NOT NULL,
            score INTEGER,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Position checks table (for monitor)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS position_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            score INTEGER,
            signals TEXT,  -- JSON
            pnl_pct REAL,
            alert_sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Poor signals table (for self-learning loop)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS poor_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_id INTEGER,
            entry_date TEXT NOT NULL,
            exit_date TEXT NOT NULL,
            entry_price REAL,
            exit_price REAL,
            pnl_pct REAL,
            holding_days INTEGER,
            exit_reason TEXT,
            reversal_score INTEGER,
            reversal_signals TEXT,  -- JSON
            entry_signals TEXT,  -- JSON (signals that triggered the buy)
            composite_score INTEGER,
            notes TEXT,
            reviewed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        )
    """)

    # DQN experiences table for reinforcement learning
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dqn_experiences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER UNIQUE,
            symbol TEXT NOT NULL,

            -- State at entry (feature vector)
            state_vector TEXT NOT NULL,  -- JSON array of normalized features

            -- Action taken (0=skip, 1=buy)
            action INTEGER NOT NULL DEFAULT 1,

            -- Reward signal
            reward REAL NOT NULL,

            -- Next state (at exit or after holding period)
            next_state_vector TEXT,  -- JSON array

            -- Episode metadata
            done INTEGER DEFAULT 1,  -- 1 if trade completed
            entry_date TEXT,
            exit_date TEXT,
            holding_days INTEGER,
            pnl_pct REAL,

            -- Raw features for debugging/analysis
            price REAL,
            sma_7 REAL,
            sma_20 REAL,
            sma_30 REAL,
            rsi_14 REAL,
            atr_14 REAL,
            volume_surge REAL,
            roc_5d REAL,
            roc_10d REAL,
            pct_from_high REAL,
            composite_score REAL,
            momentum_breakout INTEGER,
            sma_aligned INTEGER,

            -- Market context
            spy_price REAL,
            vix_level REAL,
            market_breadth REAL,
            spy_trend TEXT,

            -- Portfolio context
            portfolio_cash_pct REAL,
            open_positions INTEGER,
            total_exposure REAL,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        )
    """)

    # Error log table for tracking all errors with context
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            error_type TEXT NOT NULL,  -- 'scan', 'trade', 'monitor', 'api', 'system'
            operation TEXT NOT NULL,   -- 'buy', 'sell', 'scan', 'close', etc.
            symbol TEXT,
            error_message TEXT NOT NULL,
            error_details TEXT,        -- Full traceback or additional context
            context TEXT,              -- JSON with relevant state (prices, signals, etc.)
            resolved INTEGER DEFAULT 0,
            resolution_notes TEXT
        )
    """)

    # Scan decisions table for tracking all agent decisions with reasoning
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            scan_type TEXT,            -- 'open', 'midday', 'close'
            cap_category TEXT,         -- 'large', 'mid', 'small'

            -- Candidates summary
            stage1_count INTEGER,
            stage2_count INTEGER,
            filtered_by_rsi INTEGER,   -- Count blocked by RSI
            filtered_by_breakout INTEGER,
            filtered_by_volume INTEGER,
            filtered_by_momentum INTEGER,

            -- Agent decision
            candidates_presented INTEGER,
            agent_buys TEXT,           -- JSON list of symbols
            agent_watches TEXT,        -- JSON list of symbols
            agent_skips TEXT,          -- JSON list of symbols
            agent_reasoning TEXT,      -- Market assessment from agent

            -- Execution results
            executed_buys TEXT,        -- JSON list of executed symbols
            failed_buys TEXT,          -- JSON list of failed symbols with reasons
            execution_errors TEXT,     -- JSON of any execution errors

            FOREIGN KEY (scan_id) REFERENCES market_snapshots(id)
        )
    """)

    # Flow signals table (options flow from Unusual Whales)
    cursor.execute("""
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
            raw_data TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Options trades table
    cursor.execute("""
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
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (flow_signal_id) REFERENCES flow_signals(id)
        )
    """)

    # Flow scan history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flow_scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT,
            filters TEXT,
            signals_found INTEGER,
            signals_analyzed INTEGER,
            buy_recommendations INTEGER,
            trades_executed INTEGER,
            top_signals TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()


# ============== MARKET SNAPSHOT FUNCTIONS ==============

def log_market_snapshot(spy_data: dict, vix_level: float, candidates_count: int,
                        scan_type: str = 'scheduled', market_breadth: float = None) -> int:
    """Log market conditions at scan time"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO market_snapshots (
            timestamp, spy_price, spy_change_1d, spy_change_5d, spy_sma20,
            spy_above_sma20, vix_level, vix_change_1d, market_breadth,
            sector_leader, sector_laggard, total_candidates_found, scan_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        spy_data.get('price'),
        spy_data.get('change_1d'),
        spy_data.get('change_5d'),
        spy_data.get('sma20'),
        1 if spy_data.get('above_sma20') else 0,
        vix_level,
        spy_data.get('vix_change_1d'),
        market_breadth,
        spy_data.get('sector_leader'),
        spy_data.get('sector_laggard'),
        candidates_count,
        scan_type
    ))

    scan_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return scan_id


def log_candidate(scan_id: int, symbol: str, signals: dict, action: str, skip_reason: str = None):
    """Log every candidate considered, whether traded or not"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO candidate_snapshots (
            scan_id, timestamp, symbol, price, sma7, sma20, sma30, atr_14,
            volume_ratio, close_position, roc_5, roc_10, rsi_14,
            distance_from_52w_high, composite_score, momentum_breakout,
            action, skip_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scan_id,
        datetime.now().isoformat(),
        symbol,
        signals.get('price'),
        signals.get('sma_7'),
        signals.get('sma_20'),
        signals.get('sma_30'),
        signals.get('atr_14'),
        signals.get('volume_surge'),
        signals.get('intraday_strength'),
        signals.get('roc_5d'),
        signals.get('roc_10d'),
        signals.get('rsi_14'),
        signals.get('pct_from_high'),
        signals.get('composite_score'),
        1 if signals.get('momentum_breakout') else 0,
        action,
        skip_reason
    ))

    conn.commit()
    conn.close()


def update_candidate_outcomes(data_client):
    """
    Fill in price_Xd_later for past candidates (called by jobs.py).

    Uses batched API calls and falls back to historical bars if snapshots fail.
    Should be run 1+ hour after market close for reliable data.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Get candidates that need outcome updates (1-15 days old, missing outcomes)
    cursor.execute("""
        SELECT id, symbol, timestamp, price FROM candidate_snapshots
        WHERE price_1d_later IS NULL
        AND timestamp < datetime('now', '-1 day')
        AND timestamp > datetime('now', '-15 day')
    """)

    candidates = cursor.fetchall()
    if not candidates:
        conn.close()
        return 0

    # Group candidates by symbol for efficient API calls
    symbols = list(set(c['symbol'] for c in candidates))
    print(f"  Fetching prices for {len(symbols)} unique symbols...")

    # Batch fetch snapshots (50 at a time)
    all_prices = {}
    batch_size = 50

    from alpaca.data.requests import StockSnapshotRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        try:
            request = StockSnapshotRequest(symbol_or_symbols=batch)
            snapshots = data_client.get_stock_snapshot(request)

            for symbol, snapshot in snapshots.items():
                if snapshot and snapshot.daily_bar:
                    all_prices[symbol] = snapshot.daily_bar.close
        except Exception as e:
            print(f"  Batch {i//batch_size + 1} snapshot failed: {e}")

    # Fallback: Fetch historical bars for symbols that failed
    missing_symbols = [s for s in symbols if s not in all_prices]
    if missing_symbols:
        print(f"  Falling back to historical bars for {len(missing_symbols)} symbols...")
        try:
            end = datetime.now()
            start = end - timedelta(days=5)

            for i in range(0, len(missing_symbols), batch_size):
                batch = missing_symbols[i:i + batch_size]
                try:
                    request = StockBarsRequest(
                        symbol_or_symbols=batch,
                        timeframe=TimeFrame.Day,
                        start=start,
                        end=end
                    )
                    bars = data_client.get_stock_bars(request)

                    for symbol in batch:
                        if symbol in bars.data and len(bars.data[symbol]) > 0:
                            # Get most recent bar
                            all_prices[symbol] = bars.data[symbol][-1].close
                except Exception as e:
                    print(f"  Historical bars batch failed: {e}")
        except Exception as e:
            print(f"  Historical bars fallback failed: {e}")

    print(f"  Got prices for {len(all_prices)}/{len(symbols)} symbols")

    # Update candidates with fetched prices
    updated = 0
    skipped_no_price = 0

    for candidate in candidates:
        symbol = candidate['symbol']
        entry_time = datetime.fromisoformat(candidate['timestamp'])
        entry_price = candidate['price']

        if symbol not in all_prices:
            skipped_no_price += 1
            continue

        current_price = all_prices[symbol]
        days_elapsed = (datetime.now() - entry_time).days

        try:
            # Update appropriate price field based on days elapsed
            if days_elapsed >= 1:
                cursor.execute(
                    "UPDATE candidate_snapshots SET price_1d_later = ? WHERE id = ?",
                    (current_price, candidate['id'])
                )
            if days_elapsed >= 5:
                cursor.execute(
                    "UPDATE candidate_snapshots SET price_5d_later = ? WHERE id = ?",
                    (current_price, candidate['id'])
                )
            if days_elapsed >= 10:
                # Calculate if it would have won (>3% gain)
                would_have_won = 1 if entry_price and (current_price - entry_price) / entry_price > 0.03 else 0
                cursor.execute(
                    "UPDATE candidate_snapshots SET price_10d_later = ?, would_have_won = ? WHERE id = ?",
                    (current_price, would_have_won, candidate['id'])
                )
            updated += 1
        except Exception as e:
            print(f"  Error updating {symbol}: {e}")
            continue

    conn.commit()
    conn.close()

    if skipped_no_price > 0:
        print(f"  Skipped {skipped_no_price} candidates (no price data)")

    return updated


# ============== DQL TRAINING FUNCTIONS ==============

def calculate_reward(trade: dict) -> float:
    """Calculate DQL reward for a closed trade"""
    if trade['status'] != 'closed' or trade['pnl_pct'] is None:
        return None

    pnl_pct = trade['pnl_pct'] / 100  # Convert from percentage
    holding_days = trade.get('holding_days') or 0
    max_dd = trade.get('max_drawdown_during_trade')

    # Handle missing max_dd - use a default based on pnl
    if max_dd is None or max_dd == 0:
        max_dd = abs(pnl_pct) if pnl_pct < 0 else 0.05

    # Reward components
    hold_penalty = -0.001 * holding_days  # Penalize long holds
    risk_adjusted = pnl_pct / abs(max_dd) if max_dd != 0 else pnl_pct

    # Final reward: PnL + hold penalty + risk adjustment bonus
    reward = pnl_pct + hold_penalty + (0.3 * risk_adjusted)

    return round(reward, 4)


def get_state_vector(symbol: str, signals: dict, portfolio_state: dict, market_state: dict) -> list:
    """Generate feature vector for DQL training"""
    # Encode trend: 1=up, 0=sideways, -1=down
    trend_map = {'up': 1, 'sideways': 0, 'down': -1}
    trend_encoded = trend_map.get(market_state.get('spy_trend', 'sideways'), 0)

    return [
        signals.get('price', 0),
        signals.get('sma_7', 0),
        signals.get('sma_20', 0),
        signals.get('sma_30', 0),
        signals.get('atr_14', 0),
        signals.get('volume_surge', 0),
        signals.get('intraday_strength', 0),
        signals.get('roc_5d', 0),
        signals.get('roc_10d', 0),
        signals.get('rsi_14', 0),
        signals.get('pct_from_high', 0),
        signals.get('composite_score', 0),
        1 if signals.get('momentum_breakout') else 0,
        1 if signals.get('sma_aligned') else 0,
        trend_encoded,
        market_state.get('vix_level', 15),
        market_state.get('market_breadth', 0.5),
        market_state.get('spy_change_1d', 0),
        portfolio_state.get('cash_pct', 1.0),
        portfolio_state.get('open_positions', 0),
        portfolio_state.get('total_exposure', 0),
        portfolio_state.get('current_drawdown', 0),
    ]


def update_trade_reward(trade_id: int):
    """Update reward for a closed trade"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cursor.fetchone()

    if row:
        trade = dict(row)
        reward = calculate_reward(trade)
        if reward is not None:
            cursor.execute("UPDATE trades SET reward = ? WHERE id = ?", (reward, trade_id))
            conn.commit()

    conn.close()


# ============== DAILY PERFORMANCE FUNCTIONS ==============

def log_daily_performance(date: str, starting_equity: float, ending_equity: float,
                          trades_opened: int, trades_closed: int, win_count: int,
                          loss_count: int, positions_held: int, cash_pct: float,
                          spy_change: float = None):
    """Log daily performance snapshot"""
    conn = get_connection()
    cursor = conn.cursor()

    daily_pnl = ending_equity - starting_equity
    daily_pnl_pct = (daily_pnl / starting_equity * 100) if starting_equity > 0 else 0

    cursor.execute("""
        INSERT OR REPLACE INTO daily_performance (
            date, starting_equity, ending_equity, daily_pnl, daily_pnl_pct,
            trades_opened, trades_closed, win_count, loss_count,
            positions_held, cash_pct, spy_change
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date, starting_equity, ending_equity, daily_pnl, daily_pnl_pct,
        trades_opened, trades_closed, win_count, loss_count,
        positions_held, cash_pct, spy_change
    ))

    conn.commit()
    conn.close()


# ============== METRICS FUNCTIONS ==============

def get_baseline_metrics() -> dict:
    """Get overall system metrics since inception"""
    conn = get_connection()
    cursor = conn.cursor()

    # Get all closed trades
    cursor.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE NULL END) as avg_win,
            AVG(CASE WHEN pnl_pct <= 0 THEN pnl_pct ELSE NULL END) as avg_loss,
            SUM(pnl_amount) as total_pnl,
            MIN(entry_date) as first_trade,
            MAX(exit_date) as last_trade
        FROM trades
        WHERE status = 'closed'
    """)

    row = cursor.fetchone()

    if not row or row['total_trades'] == 0:
        conn.close()
        return {
            'total_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'profit_factor': 0,
            'total_pnl': 0,
            'first_trade': None,
            'last_trade': None
        }

    wins = row['wins'] or 0
    losses = row['losses'] or 0
    avg_win = row['avg_win'] or 0
    avg_loss = abs(row['avg_loss'] or 0)

    win_rate = (wins / row['total_trades'] * 100) if row['total_trades'] > 0 else 0
    win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else avg_win
    profit_factor = (wins * avg_win) / (losses * avg_loss) if (losses * avg_loss) > 0 else wins * avg_win

    # Get max drawdown from daily performance
    cursor.execute("SELECT MIN(daily_pnl_pct) as max_dd FROM daily_performance")
    dd_row = cursor.fetchone()
    max_drawdown = abs(dd_row['max_dd']) if dd_row and dd_row['max_dd'] else 0

    # Get equity curve for Sharpe calculation
    cursor.execute("SELECT daily_pnl_pct FROM daily_performance ORDER BY date")
    daily_returns = [r['daily_pnl_pct'] for r in cursor.fetchall()]

    sharpe = 0
    if len(daily_returns) > 1:
        import statistics
        avg_return = statistics.mean(daily_returns)
        std_return = statistics.stdev(daily_returns)
        sharpe = (avg_return / std_return * (252 ** 0.5)) if std_return > 0 else 0

    conn.close()

    return {
        'total_trades': row['total_trades'],
        'wins': wins,
        'losses': losses,
        'win_rate': round(win_rate, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'win_loss_ratio': round(win_loss_ratio, 2),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown': round(max_drawdown, 2),
        'sharpe_ratio': round(sharpe, 2),
        'total_pnl': round(row['total_pnl'] or 0, 2),
        'first_trade': row['first_trade'],
        'last_trade': row['last_trade']
    }


def get_weekly_metrics(weeks_back: int = 1) -> dict:
    """Get performance metrics for past N weeks"""
    conn = get_connection()
    cursor = conn.cursor()

    start_date = (datetime.now() - timedelta(weeks=weeks_back)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')

    # Trades in period
    cursor.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_amount) as total_pnl,
            MAX(pnl_pct) as best_trade_pct,
            MIN(pnl_pct) as worst_trade_pct
        FROM trades
        WHERE status = 'closed'
        AND exit_date >= ?
    """, (start_date,))

    trades_row = cursor.fetchone()

    # Best and worst trades
    cursor.execute("""
        SELECT symbol, pnl_pct FROM trades
        WHERE status = 'closed' AND exit_date >= ?
        ORDER BY pnl_pct DESC LIMIT 1
    """, (start_date,))
    best = cursor.fetchone()

    cursor.execute("""
        SELECT symbol, pnl_pct FROM trades
        WHERE status = 'closed' AND exit_date >= ?
        ORDER BY pnl_pct ASC LIMIT 1
    """, (start_date,))
    worst = cursor.fetchone()

    # Daily performance in period
    cursor.execute("""
        SELECT SUM(daily_pnl) as period_pnl, AVG(daily_pnl_pct) as avg_daily,
               SUM(spy_change) as spy_total
        FROM daily_performance
        WHERE date >= ?
    """, (start_date,))
    daily_row = cursor.fetchone()

    # Scan stats
    cursor.execute("""
        SELECT COUNT(*) as scans, SUM(total_candidates_found) as candidates
        FROM market_snapshots
        WHERE timestamp >= ?
    """, (start_date,))
    scan_row = cursor.fetchone()

    conn.close()

    total_trades = trades_row['total_trades'] or 0
    wins = trades_row['wins'] or 0

    return {
        'period': f"{start_date} to {end_date}",
        'total_trades': total_trades,
        'wins': wins,
        'losses': total_trades - wins,
        'win_rate': round(wins / total_trades * 100, 1) if total_trades > 0 else 0,
        'total_pnl': round(trades_row['total_pnl'] or 0, 2),
        'best_trade': {'symbol': best['symbol'], 'pnl': round(best['pnl_pct'], 1)} if best else None,
        'worst_trade': {'symbol': worst['symbol'], 'pnl': round(worst['pnl_pct'], 1)} if worst else None,
        'scans_run': scan_row['scans'] or 0,
        'candidates_found': scan_row['candidates'] or 0,
        'spy_change': round(daily_row['spy_total'] or 0, 2),
        'beat_spy': (trades_row['total_pnl'] or 0) > (daily_row['spy_total'] or 0)
    }


def get_monthly_metrics(months_back: int = 1) -> dict:
    """Get performance metrics for past N months"""
    conn = get_connection()
    cursor = conn.cursor()

    start_date = (datetime.now() - timedelta(days=30 * months_back)).strftime('%Y-%m-%d')

    # Trades in period
    cursor.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_amount) as total_pnl,
            AVG(pnl_pct) as avg_pnl
        FROM trades
        WHERE status = 'closed' AND exit_date >= ?
    """, (start_date,))

    trades_row = cursor.fetchone()

    # Top winners and losers
    cursor.execute("""
        SELECT symbol, pnl_pct FROM trades
        WHERE status = 'closed' AND exit_date >= ?
        ORDER BY pnl_pct DESC LIMIT 3
    """, (start_date,))
    top_winners = [{'symbol': r['symbol'], 'pnl': round(r['pnl_pct'], 1)} for r in cursor.fetchall()]

    cursor.execute("""
        SELECT symbol, pnl_pct FROM trades
        WHERE status = 'closed' AND exit_date >= ?
        ORDER BY pnl_pct ASC LIMIT 3
    """, (start_date,))
    top_losers = [{'symbol': r['symbol'], 'pnl': round(r['pnl_pct'], 1)} for r in cursor.fetchall()]

    # Weekly breakdown
    cursor.execute("""
        SELECT
            strftime('%W', date) as week,
            SUM(daily_pnl_pct) as week_pnl
        FROM daily_performance
        WHERE date >= ?
        GROUP BY week
        ORDER BY week
    """, (start_date,))
    weekly_breakdown = [{'week': r['week'], 'pnl': round(r['week_pnl'] or 0, 2)} for r in cursor.fetchall()]

    # Max drawdown in period
    cursor.execute("""
        SELECT MIN(daily_pnl_pct) as max_dd
        FROM daily_performance
        WHERE date >= ?
    """, (start_date,))
    dd_row = cursor.fetchone()

    conn.close()

    total_trades = trades_row['total_trades'] or 0
    wins = trades_row['wins'] or 0

    return {
        'period': f"Last {months_back} month(s)",
        'total_trades': total_trades,
        'wins': wins,
        'losses': total_trades - wins,
        'win_rate': round(wins / total_trades * 100, 1) if total_trades > 0 else 0,
        'total_pnl': round(trades_row['total_pnl'] or 0, 2),
        'avg_trade': round(trades_row['avg_pnl'] or 0, 2),
        'max_drawdown': round(abs(dd_row['max_dd'] or 0), 2),
        'top_winners': top_winners,
        'top_losers': top_losers,
        'weekly_breakdown': weekly_breakdown
    }


def export_trades_csv(filepath: str):
    """Export trades to CSV for analysis"""
    import csv

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM trades ORDER BY entry_date")
    trades = cursor.fetchall()

    if trades:
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(trades[0].keys())
            for trade in trades:
                writer.writerow(trade)

    conn.close()
    return len(trades)


def export_candidates_csv(filepath: str):
    """Export candidate snapshots to CSV for DQL training"""
    import csv

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.*, m.spy_price, m.vix_level, m.market_breadth
        FROM candidate_snapshots c
        LEFT JOIN market_snapshots m ON c.scan_id = m.id
        ORDER BY c.timestamp
    """)
    candidates = cursor.fetchall()

    if candidates:
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(candidates[0].keys())
            for c in candidates:
                writer.writerow(c)

    conn.close()
    return len(candidates)


# ============== ORIGINAL FUNCTIONS (maintained for compatibility) ==============

def log_trade(trade_data: dict) -> int:
    """Log a new trade entry"""
    conn = get_connection()
    cursor = conn.cursor()

    # Extract DQL fields if available
    signals = trade_data.get("signals", {})
    market_state = trade_data.get("market_state", {})
    portfolio_state = trade_data.get("portfolio_state", {})

    cursor.execute("""
        INSERT INTO trades (
            symbol, entry_date, entry_price, quantity,
            entry_order_id, stop_order_id, signals, status,
            spy_price, spy_sma20, spy_trend, vix_level,
            sma7, sma20, sma30, volume_ratio, roc_10,
            distance_from_52w_high, portfolio_cash_pct,
            open_positions_count, total_exposure_pct, state_vector
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade_data["symbol"],
        trade_data["entry_date"],
        trade_data["entry_price"],
        trade_data["quantity"],
        trade_data.get("entry_order_id"),
        trade_data.get("stop_order_id"),
        json.dumps(signals),
        trade_data.get("status", "open"),
        market_state.get("spy_price"),
        market_state.get("spy_sma20"),
        market_state.get("spy_trend"),
        market_state.get("vix_level"),
        signals.get("sma_7"),
        signals.get("sma_20"),
        signals.get("sma_30"),
        signals.get("volume_surge"),
        signals.get("roc_10d"),
        signals.get("pct_from_high"),
        portfolio_state.get("cash_pct"),
        portfolio_state.get("open_positions"),
        portfolio_state.get("total_exposure"),
        json.dumps(trade_data.get("state_vector", []))
    ))

    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return trade_id


def update_trade_exit(trade_id: int, exit_data: dict):
    """Update trade with exit information"""
    conn = get_connection()
    cursor = conn.cursor()

    # Calculate holding days
    cursor.execute("SELECT entry_date FROM trades WHERE id = ?", (trade_id,))
    row = cursor.fetchone()
    holding_days = 0
    if row:
        entry_date = datetime.fromisoformat(row['entry_date'][:10])
        exit_date = datetime.fromisoformat(exit_data["exit_date"][:10])
        holding_days = (exit_date - entry_date).days

    cursor.execute("""
        UPDATE trades SET
            exit_date = ?,
            exit_price = ?,
            exit_reason = ?,
            pnl_amount = ?,
            pnl_pct = ?,
            holding_days = ?,
            max_gain_during_trade = ?,
            max_drawdown_during_trade = ?,
            status = 'closed'
        WHERE id = ?
    """, (
        exit_data["exit_date"],
        exit_data["exit_price"],
        exit_data["exit_reason"],
        exit_data["pnl_amount"],
        exit_data["pnl_pct"],
        holding_days,
        exit_data.get("max_gain"),
        exit_data.get("max_drawdown"),
        trade_id
    ))

    conn.commit()
    conn.close()

    # Calculate and update reward
    update_trade_reward(trade_id)

    # Log signal performance
    log_signal_performance(trade_id, exit_data["pnl_pct"])


def log_signal_performance(trade_id: int, pnl_pct: float):
    """Log signal performance for a closed trade"""
    conn = get_connection()
    cursor = conn.cursor()

    # Get the trade's signals
    cursor.execute("SELECT signals FROM trades WHERE id = ?", (trade_id,))
    row = cursor.fetchone()
    if not row or not row["signals"]:
        conn.close()
        return

    signals = json.loads(row["signals"])
    outcome = "win" if pnl_pct > 0 else "loss"

    # Log each signal
    signal_mappings = [
        ("sma_aligned", str(signals.get("sma_aligned", False))),
        ("volume_surge_high", "True" if signals.get("volume_surge", 0) > 1.5 else "False"),
        ("momentum_breakout", str(signals.get("momentum_breakout", False))),
        ("near_52w_high", str(signals.get("near_52w_high", False))),
        ("roc_10d_strong", "True" if signals.get("roc_10d", 0) > 5 else "False"),
    ]

    for signal_name, signal_value in signal_mappings:
        cursor.execute("""
            INSERT INTO signal_performance (
                signal_name, signal_value, trade_id, outcome, pnl_pct
            ) VALUES (?, ?, ?, ?, ?)
        """, (signal_name, signal_value, trade_id, outcome, pnl_pct))

    conn.commit()
    conn.close()


def get_recent_trades(limit: int = 20) -> list[dict]:
    """Get recent trades"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM trades
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))

    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return trades


def get_open_trades() -> list[dict]:
    """Get all open trades"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM trades WHERE status = 'open'")
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return trades


def get_signal_performance() -> dict:
    """Get signal performance statistics"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            signal_name,
            signal_value,
            COUNT(*) as count,
            SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
            AVG(pnl_pct) as avg_pnl
        FROM signal_performance
        WHERE signal_value = 'True'
        GROUP BY signal_name, signal_value
    """)

    results = {}
    for row in cursor.fetchall():
        signal = row["signal_name"]
        count = row["count"]
        wins = row["wins"]
        results[signal] = {
            "count": count,
            "wins": wins,
            "win_rate": wins / count if count > 0 else 0,
            "avg_pnl": row["avg_pnl"] or 0
        }

    conn.close()
    return results


def log_scan(candidates: list, decision: dict, executed_symbol: str = None):
    """Log a scan for history"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO scans (scan_date, candidates, decision, executed_symbol)
        VALUES (?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        json.dumps(candidates),
        json.dumps(decision),
        executed_symbol
    ))

    conn.commit()
    conn.close()


def get_trade_by_symbol(symbol: str, status: str = "open") -> dict:
    """Get trade by symbol and status"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM trades WHERE symbol = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
        (symbol, status)
    )
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_watchlist() -> list[dict]:
    """Get current watchlist"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT symbol, added_date, score, reason
        FROM watchlist
        ORDER BY created_at DESC
    """)

    watchlist = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return watchlist


def save_watchlist(watchlist: list[dict]):
    """Save watchlist (replaces existing)"""
    conn = get_connection()
    cursor = conn.cursor()

    # Clear existing watchlist
    cursor.execute("DELETE FROM watchlist")

    # Insert new watchlist
    for item in watchlist:
        cursor.execute("""
            INSERT OR REPLACE INTO watchlist (symbol, added_date, score, reason)
            VALUES (?, ?, ?, ?)
        """, (
            item['symbol'],
            item.get('added_date', datetime.now().isoformat()[:10]),
            item.get('score'),
            item.get('reason')
        ))

    conn.commit()
    conn.close()


def add_to_watchlist(symbol: str, score: int = None, reason: str = None):
    """Add a single symbol to watchlist"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO watchlist (symbol, added_date, score, reason)
        VALUES (?, ?, ?, ?)
    """, (symbol, datetime.now().isoformat()[:10], score, reason))

    conn.commit()
    conn.close()


def remove_from_watchlist(symbol: str):
    """Remove a symbol from watchlist"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))

    conn.commit()
    conn.close()


def log_position_check(symbol: str, score: int, signals: list, pnl_pct: float, alert_sent: bool = False):
    """Log a position check from monitor"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO position_checks (check_time, symbol, score, signals, pnl_pct, alert_sent)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        symbol,
        score,
        json.dumps(signals),
        pnl_pct,
        1 if alert_sent else 0
    ))

    conn.commit()
    conn.close()


def update_position_tracking(symbol: str, current_price: float, entry_price: float):
    """Update max gain/drawdown tracking for open position"""
    conn = get_connection()
    cursor = conn.cursor()

    current_pnl_pct = (current_price - entry_price) / entry_price * 100

    cursor.execute("""
        SELECT id, max_gain_during_trade, max_drawdown_during_trade
        FROM trades WHERE symbol = ? AND status = 'open'
        ORDER BY created_at DESC LIMIT 1
    """, (symbol,))

    row = cursor.fetchone()
    if row:
        max_gain = max(row['max_gain_during_trade'] or 0, current_pnl_pct)
        max_dd = min(row['max_drawdown_during_trade'] or 0, current_pnl_pct)

        cursor.execute("""
            UPDATE trades SET max_gain_during_trade = ?, max_drawdown_during_trade = ?
            WHERE id = ?
        """, (max_gain, max_dd, row['id']))

        conn.commit()

    conn.close()


# ============== POOR SIGNALS FUNCTIONS ==============

def log_poor_signal(trade: dict, reversal_score: int, reversal_signals: list, notes: str = None):
    """
    Log a trade that was closed due to reversal indicator as a poor signal.
    Used for self-learning loop to identify patterns in bad trades.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Calculate holding days
    holding_days = 0
    if trade.get('entry_date') and trade.get('exit_date'):
        try:
            entry = datetime.fromisoformat(trade['entry_date'][:10])
            exit_dt = datetime.fromisoformat(trade['exit_date'][:10])
            holding_days = (exit_dt - entry).days
        except Exception:
            pass

    cursor.execute("""
        INSERT INTO poor_signals (
            symbol, trade_id, entry_date, exit_date, entry_price, exit_price,
            pnl_pct, holding_days, exit_reason, reversal_score, reversal_signals,
            entry_signals, composite_score, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade.get('symbol'),
        trade.get('id'),
        trade.get('entry_date'),
        trade.get('exit_date', datetime.now().isoformat()),
        trade.get('entry_price'),
        trade.get('exit_price'),
        trade.get('pnl_pct'),
        holding_days,
        trade.get('exit_reason'),
        reversal_score,
        json.dumps(reversal_signals) if reversal_signals else None,
        json.dumps(trade.get('signals')) if trade.get('signals') else None,
        trade.get('composite_score'),
        notes
    ))

    conn.commit()
    conn.close()


def get_poor_signals(days: int = 7, reviewed_only: bool = False) -> list[dict]:
    """Get poor signals for review"""
    conn = get_connection()
    cursor = conn.cursor()

    since_date = (datetime.now() - timedelta(days=days)).isoformat()

    if reviewed_only:
        cursor.execute("""
            SELECT * FROM poor_signals
            WHERE exit_date >= ? AND reviewed = 1
            ORDER BY exit_date DESC
        """, (since_date,))
    else:
        cursor.execute("""
            SELECT * FROM poor_signals
            WHERE exit_date >= ?
            ORDER BY exit_date DESC
        """, (since_date,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_poor_signal_summary(days: int = 30) -> dict:
    """Get summary of poor signals for reporting"""
    conn = get_connection()
    cursor = conn.cursor()

    since_date = (datetime.now() - timedelta(days=days)).isoformat()

    # Count by exit reason
    cursor.execute("""
        SELECT exit_reason, COUNT(*) as count, AVG(pnl_pct) as avg_pnl,
               AVG(holding_days) as avg_holding_days, AVG(reversal_score) as avg_reversal_score
        FROM poor_signals
        WHERE exit_date >= ?
        GROUP BY exit_reason
        ORDER BY count DESC
    """, (since_date,))
    by_reason = [dict(row) for row in cursor.fetchall()]

    # Most common reversal signals
    cursor.execute("""
        SELECT reversal_signals FROM poor_signals
        WHERE exit_date >= ? AND reversal_signals IS NOT NULL
    """, (since_date,))

    signal_counts = {}
    for row in cursor.fetchall():
        try:
            signals = json.loads(row['reversal_signals'])
            for sig in signals:
                signal_counts[sig] = signal_counts.get(sig, 0) + 1
        except Exception:
            pass

    # Most common entry signals that led to poor trades
    cursor.execute("""
        SELECT entry_signals FROM poor_signals
        WHERE exit_date >= ? AND entry_signals IS NOT NULL
    """, (since_date,))

    entry_signal_counts = {}
    for row in cursor.fetchall():
        try:
            signals = json.loads(row['entry_signals'])
            if isinstance(signals, dict):
                for key, value in signals.items():
                    if value:  # Only count truthy signals
                        entry_signal_counts[key] = entry_signal_counts.get(key, 0) + 1
        except Exception:
            pass

    # Total stats
    cursor.execute("""
        SELECT COUNT(*) as total, AVG(pnl_pct) as avg_pnl,
               SUM(CASE WHEN pnl_pct < 0 THEN 1 ELSE 0 END) as losses,
               AVG(holding_days) as avg_holding
        FROM poor_signals
        WHERE exit_date >= ?
    """, (since_date,))
    totals = dict(cursor.fetchone())

    conn.close()

    return {
        "period_days": days,
        "total_poor_signals": totals.get('total', 0),
        "avg_pnl": totals.get('avg_pnl'),
        "loss_count": totals.get('losses', 0),
        "avg_holding_days": totals.get('avg_holding'),
        "by_exit_reason": by_reason,
        "common_reversal_signals": sorted(signal_counts.items(), key=lambda x: x[1], reverse=True)[:5],
        "common_entry_signals": sorted(entry_signal_counts.items(), key=lambda x: x[1], reverse=True)[:5],
    }


def mark_poor_signal_reviewed(signal_id: int):
    """Mark a poor signal as reviewed"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE poor_signals SET reviewed = 1 WHERE id = ?", (signal_id,))
    conn.commit()
    conn.close()


# ============== ERROR LOGGING FUNCTIONS ==============

def log_error(error_type: str, operation: str, error_message: str,
              symbol: str = None, error_details: str = None, context: dict = None):
    """
    Log an error to the database for tracking and analysis.

    Args:
        error_type: Category of error ('scan', 'trade', 'monitor', 'api', 'system')
        operation: What was being attempted ('buy', 'sell', 'scan', 'close', etc.)
        error_message: Short error description
        symbol: Related symbol if applicable
        error_details: Full traceback or detailed error info
        context: Dict with relevant state (will be JSON encoded)
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO error_log (
            timestamp, error_type, operation, symbol,
            error_message, error_details, context
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        error_type,
        operation,
        symbol,
        error_message,
        error_details,
        json.dumps(context) if context else None
    ))

    conn.commit()
    conn.close()


def get_recent_errors(limit: int = 20, error_type: str = None,
                      include_resolved: bool = False) -> list:
    """
    Get recent errors from the database.

    Args:
        limit: Maximum number of errors to return
        error_type: Filter by error type (None for all)
        include_resolved: Include resolved errors

    Returns:
        List of error dicts
    """
    conn = get_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM error_log WHERE 1=1"
    params = []

    if not include_resolved:
        query += " AND resolved = 0"
    if error_type:
        query += " AND error_type = ?"
        params.append(error_type)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    errors = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return errors


def get_error_summary(days: int = 7) -> dict:
    """Get summary of errors for reporting"""
    conn = get_connection()
    cursor = conn.cursor()

    since_date = (datetime.now() - timedelta(days=days)).isoformat()

    # Count by type
    cursor.execute("""
        SELECT error_type, COUNT(*) as count
        FROM error_log
        WHERE timestamp >= ?
        GROUP BY error_type
        ORDER BY count DESC
    """, (since_date,))
    by_type = {row['error_type']: row['count'] for row in cursor.fetchall()}

    # Count by operation
    cursor.execute("""
        SELECT operation, COUNT(*) as count
        FROM error_log
        WHERE timestamp >= ?
        GROUP BY operation
        ORDER BY count DESC
    """, (since_date,))
    by_operation = {row['operation']: row['count'] for row in cursor.fetchall()}

    # Most common error messages
    cursor.execute("""
        SELECT error_message, COUNT(*) as count
        FROM error_log
        WHERE timestamp >= ?
        GROUP BY error_message
        ORDER BY count DESC
        LIMIT 5
    """, (since_date,))
    common_errors = [(row['error_message'], row['count']) for row in cursor.fetchall()]

    # Total counts
    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as unresolved
        FROM error_log
        WHERE timestamp >= ?
    """, (since_date,))
    totals = cursor.fetchone()

    conn.close()

    return {
        'period_days': days,
        'total_errors': totals['total'] or 0,
        'unresolved': totals['unresolved'] or 0,
        'by_type': by_type,
        'by_operation': by_operation,
        'common_errors': common_errors
    }


def resolve_error(error_id: int, notes: str = None):
    """Mark an error as resolved"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE error_log SET resolved = 1, resolution_notes = ? WHERE id = ?",
        (notes, error_id)
    )
    conn.commit()
    conn.close()


# ============== SCAN DECISION LOGGING ==============

def log_scan_decision(scan_id: int, scan_type: str, cap_category: str,
                      filter_stats: dict, agent_decision: dict,
                      execution_results: dict = None):
    """
    Log a complete scan decision with all context.

    Args:
        scan_id: Reference to market_snapshots.id
        scan_type: 'open', 'midday', 'close'
        cap_category: 'large', 'mid', 'small'
        filter_stats: Dict with stage1_count, stage2_count, filtered_by_*
        agent_decision: Dict with buys, watches, skips, reasoning
        execution_results: Dict with executed_buys, failed_buys, errors
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO scan_decisions (
            scan_id, timestamp, scan_type, cap_category,
            stage1_count, stage2_count,
            filtered_by_rsi, filtered_by_breakout,
            filtered_by_volume, filtered_by_momentum,
            candidates_presented, agent_buys, agent_watches, agent_skips,
            agent_reasoning, executed_buys, failed_buys, execution_errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scan_id,
        datetime.now().isoformat(),
        scan_type,
        cap_category,
        filter_stats.get('stage1_count', 0),
        filter_stats.get('stage2_count', 0),
        filter_stats.get('filtered_by_rsi', 0),
        filter_stats.get('filtered_by_breakout', 0),
        filter_stats.get('filtered_by_volume', 0),
        filter_stats.get('filtered_by_momentum', 0),
        filter_stats.get('candidates_presented', 0),
        json.dumps(agent_decision.get('buys', [])),
        json.dumps(agent_decision.get('watches', [])),
        json.dumps(agent_decision.get('skips', [])),
        agent_decision.get('reasoning', ''),
        json.dumps(execution_results.get('executed', [])) if execution_results else None,
        json.dumps(execution_results.get('failed', [])) if execution_results else None,
        json.dumps(execution_results.get('errors', [])) if execution_results else None
    ))

    conn.commit()
    conn.close()


def get_recent_scan_decisions(limit: int = 10) -> list:
    """Get recent scan decisions for review"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM scan_decisions
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))

    decisions = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return decisions


# ============== DQN EXPERIENCE BACKFILL FUNCTIONS ==============

def backfill_dqn_experiences():
    """
    Backfill dqn_experiences table from closed trades.
    This creates training data for the DQN model from historical trades.
    """
    conn = get_connection()
    cursor = conn.cursor()

    # Get all closed trades that aren't already in dqn_experiences
    cursor.execute("""
        SELECT t.* FROM trades t
        LEFT JOIN dqn_experiences d ON t.id = d.trade_id
        WHERE t.status = 'closed' AND d.id IS NULL
        ORDER BY t.entry_date
    """)

    trades = [dict(row) for row in cursor.fetchall()]
    print(f"Found {len(trades)} closed trades to backfill")

    backfilled = 0
    skipped = 0

    for trade in trades:
        try:
            # Parse signals JSON
            signals = {}
            if trade.get('signals'):
                signals = json.loads(trade['signals'])

            # Calculate reward if missing
            reward = trade.get('reward')
            if reward is None:
                reward = calculate_reward(trade)
                if reward is not None:
                    cursor.execute("UPDATE trades SET reward = ? WHERE id = ?", (reward, trade['id']))

            if reward is None:
                print(f"  Skipping {trade['symbol']} (id={trade['id']}): cannot calculate reward")
                skipped += 1
                continue

            # Try to get market context from closest market_snapshot
            market_context = _get_closest_market_snapshot(cursor, trade['entry_date'])

            # Build state vector from available data
            state_vector = _build_state_vector_from_trade(trade, signals, market_context)

            # Build next state (simplified - just at exit)
            next_state_vector = state_vector.copy()  # Same features, different context

            # Insert into dqn_experiences
            cursor.execute("""
                INSERT INTO dqn_experiences (
                    trade_id, symbol, state_vector, action, reward, next_state_vector,
                    done, entry_date, exit_date, holding_days, pnl_pct,
                    price, sma_7, sma_20, sma_30, rsi_14, atr_14,
                    volume_surge, roc_5d, roc_10d, pct_from_high,
                    composite_score, momentum_breakout, sma_aligned,
                    spy_price, vix_level, market_breadth, spy_trend,
                    portfolio_cash_pct, open_positions, total_exposure
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade['id'],
                trade['symbol'],
                json.dumps(state_vector),
                1,  # action = BUY (all trades in history are buys)
                reward,
                json.dumps(next_state_vector),
                1,  # done = True (trade completed)
                trade['entry_date'],
                trade['exit_date'],
                trade.get('holding_days', 0),
                trade.get('pnl_pct', 0),
                signals.get('price', trade.get('entry_price')),
                signals.get('sma_7', trade.get('sma7')),
                signals.get('sma_20', trade.get('sma20')),
                signals.get('sma_30', trade.get('sma30')),
                signals.get('rsi_14', trade.get('rsi_14')),
                signals.get('atr_14', trade.get('atr_14')),
                signals.get('volume_surge', trade.get('volume_ratio')),
                signals.get('roc_5d', trade.get('roc_5')),
                signals.get('roc_10d', trade.get('roc_10')),
                signals.get('pct_from_high', trade.get('distance_from_52w_high')),
                signals.get('composite_score'),
                1 if signals.get('momentum_breakout') else 0,
                1 if signals.get('sma_aligned') else 0,
                market_context.get('spy_price', trade.get('spy_price')),
                market_context.get('vix_level', trade.get('vix_level')),
                market_context.get('market_breadth'),
                market_context.get('spy_trend', trade.get('spy_trend')),
                trade.get('portfolio_cash_pct'),
                trade.get('open_positions_count'),
                trade.get('total_exposure_pct')
            ))

            # Update state_vector in trades table too
            cursor.execute(
                "UPDATE trades SET state_vector = ? WHERE id = ?",
                (json.dumps(state_vector), trade['id'])
            )

            backfilled += 1
            print(f"  Backfilled {trade['symbol']} (id={trade['id']}): reward={reward:.4f}, pnl={trade.get('pnl_pct', 0):.2f}%")

        except Exception as e:
            print(f"  Error backfilling {trade.get('symbol', 'unknown')} (id={trade.get('id')}): {e}")
            skipped += 1

    conn.commit()
    conn.close()

    print(f"\nBackfill complete: {backfilled} experiences added, {skipped} skipped")
    return backfilled, skipped


def _get_closest_market_snapshot(cursor, entry_date: str) -> dict:
    """Get the market snapshot closest to the entry date"""
    cursor.execute("""
        SELECT spy_price, vix_level, market_breadth,
               CASE WHEN spy_price > spy_sma20 THEN 'up' ELSE 'down' END as spy_trend
        FROM market_snapshots
        WHERE timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (entry_date,))

    row = cursor.fetchone()
    if row:
        return dict(row)

    # Try getting any snapshot
    cursor.execute("""
        SELECT spy_price, vix_level, market_breadth,
               CASE WHEN spy_price > spy_sma20 THEN 'up' ELSE 'down' END as spy_trend
        FROM market_snapshots
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    return dict(row) if row else {}


def _build_state_vector_from_trade(trade: dict, signals: dict, market: dict) -> list:
    """Build normalized state vector from trade data"""
    # Feature extraction with defaults
    price = signals.get('price', trade.get('entry_price', 0))
    sma_7 = signals.get('sma_7', trade.get('sma7', 0))
    sma_20 = signals.get('sma_20', trade.get('sma20', 0))
    sma_30 = signals.get('sma_30', trade.get('sma30', 0))

    # Normalize price features relative to price
    price_norm = 1.0
    sma7_norm = (sma_7 / price) if price > 0 else 0
    sma20_norm = (sma_20 / price) if price > 0 else 0
    sma30_norm = (sma_30 / price) if price > 0 else 0

    # Other features (already normalized or bounded)
    rsi = signals.get('rsi_14', trade.get('rsi_14', 50)) or 50
    rsi_norm = rsi / 100.0

    volume_surge = signals.get('volume_surge', trade.get('volume_ratio', 1)) or 1
    volume_norm = min(volume_surge / 5.0, 1.0)  # Cap at 5x

    roc_5 = signals.get('roc_5d', trade.get('roc_5', 0)) or 0
    roc_10 = signals.get('roc_10d', trade.get('roc_10', 0)) or 0
    roc5_norm = max(min(roc_5 / 50.0, 1.0), -1.0)  # Cap at +/-50%
    roc10_norm = max(min(roc_10 / 50.0, 1.0), -1.0)

    pct_from_high = signals.get('pct_from_high', trade.get('distance_from_52w_high', 0)) or 0
    pct_high_norm = max(min(pct_from_high / 100.0, 0.0), -1.0)  # Negative means below high

    composite = signals.get('composite_score', 0) or 0
    composite_norm = composite / 20.0

    momentum_breakout = 1.0 if signals.get('momentum_breakout') else 0.0
    sma_aligned = 1.0 if signals.get('sma_aligned') else 0.0

    # Market features
    vix = market.get('vix_level', trade.get('vix_level', 20)) or 20
    vix_norm = min(vix / 50.0, 1.0)

    breadth = market.get('market_breadth', 0.5) or 0.5

    spy_trend_val = 1.0 if market.get('spy_trend') == 'up' else (
        -1.0 if market.get('spy_trend') == 'down' else 0.0
    )

    # Portfolio features (if available)
    cash_pct = trade.get('portfolio_cash_pct', 0.5) or 0.5
    positions = trade.get('open_positions_count', 0) or 0
    positions_norm = min(positions / 10.0, 1.0)
    exposure = trade.get('total_exposure_pct', 0.5) or 0.5

    return [
        price_norm,          # 0: price (normalized to 1)
        sma7_norm,           # 1: SMA7 relative to price
        sma20_norm,          # 2: SMA20 relative to price
        sma30_norm,          # 3: SMA30 relative to price
        rsi_norm,            # 4: RSI (0-1)
        volume_norm,         # 5: volume surge (0-1, capped)
        roc5_norm,           # 6: 5-day ROC (-1 to 1)
        roc10_norm,          # 7: 10-day ROC (-1 to 1)
        pct_high_norm,       # 8: % from 52w high (-1 to 0)
        composite_norm,      # 9: composite score (0-1)
        momentum_breakout,   # 10: momentum breakout flag
        sma_aligned,         # 11: SMA aligned flag
        vix_norm,            # 12: VIX level (0-1)
        breadth,             # 13: market breadth (0-1)
        spy_trend_val,       # 14: SPY trend (-1, 0, 1)
        cash_pct,            # 15: portfolio cash %
        positions_norm,      # 16: open positions (0-1)
        exposure,            # 17: total exposure %
    ]


def get_dqn_training_data(min_trades: int = 10) -> list:
    """Get DQN experiences for training"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT state_vector, action, reward, next_state_vector, done
        FROM dqn_experiences
        ORDER BY entry_date
    """)

    experiences = []
    for row in cursor.fetchall():
        try:
            experiences.append({
                'state': json.loads(row['state_vector']),
                'action': row['action'],
                'reward': row['reward'],
                'next_state': json.loads(row['next_state_vector']) if row['next_state_vector'] else None,
                'done': bool(row['done'])
            })
        except Exception:
            continue

    conn.close()
    return experiences if len(experiences) >= min_trades else []


def get_dqn_stats() -> dict:
    """Get statistics about DQN experiences"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total,
            AVG(reward) as avg_reward,
            MIN(reward) as min_reward,
            MAX(reward) as max_reward,
            SUM(CASE WHEN reward > 0 THEN 1 ELSE 0 END) as positive_rewards,
            AVG(pnl_pct) as avg_pnl,
            AVG(holding_days) as avg_holding
        FROM dqn_experiences
    """)

    row = cursor.fetchone()
    conn.close()

    if not row or row['total'] == 0:
        return {'total': 0}

    return {
        'total': row['total'],
        'avg_reward': round(row['avg_reward'] or 0, 4),
        'min_reward': round(row['min_reward'] or 0, 4),
        'max_reward': round(row['max_reward'] or 0, 4),
        'positive_rewards': row['positive_rewards'] or 0,
        'win_rate': round((row['positive_rewards'] or 0) / row['total'] * 100, 1),
        'avg_pnl': round(row['avg_pnl'] or 0, 2),
        'avg_holding_days': round(row['avg_holding'] or 0, 1)
    }


# ============== OPTIONS FLOW FUNCTIONS ==============

def log_flow_signal(signal_data: dict) -> int:
    """Log a flow signal from Unusual Whales"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO flow_signals (
            signal_id, timestamp, symbol, strike, expiration, option_type,
            premium, size, volume, open_interest, vol_oi_ratio,
            is_sweep, is_ask_side, is_floor, is_opening, is_otm,
            underlying_price, sentiment, score, score_breakdown, raw_data
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal_data.get('id'),
        signal_data.get('timestamp'),
        signal_data.get('symbol'),
        signal_data.get('strike'),
        signal_data.get('expiration'),
        signal_data.get('option_type'),
        signal_data.get('premium'),
        signal_data.get('size'),
        signal_data.get('volume'),
        signal_data.get('open_interest'),
        signal_data.get('vol_oi_ratio'),
        1 if signal_data.get('is_sweep') else 0,
        1 if signal_data.get('is_ask_side') else 0,
        1 if signal_data.get('is_floor') else 0,
        1 if signal_data.get('is_opening') else 0,
        1 if signal_data.get('is_otm') else 0,
        signal_data.get('underlying_price'),
        signal_data.get('sentiment'),
        signal_data.get('score'),
        json.dumps(signal_data.get('score_breakdown', {})),
        json.dumps(signal_data.get('raw_data', {})),
    ))

    signal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return signal_id


def update_flow_signal_analysis(signal_id: str, recommendation: str, conviction: float, thesis: str):
    """Update flow signal with analysis results"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE flow_signals
        SET analyzed = 1, recommendation = ?, conviction = ?, thesis = ?
        WHERE signal_id = ?
    """, (recommendation, conviction, thesis, signal_id))

    conn.commit()
    conn.close()


def mark_flow_signal_executed(signal_id: str):
    """Mark flow signal as executed"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE flow_signals SET executed = 1 WHERE signal_id = ?
    """, (signal_id,))

    conn.commit()
    conn.close()


def log_options_trade(
    contract_symbol: str,
    underlying: str,
    option_type: str,
    strike: float,
    expiration: str,
    quantity: int,
    entry_price: float,
    signal_score: int = None,
    signal_data: dict = None,
    thesis: str = None,
    flow_signal_id: int = None
) -> int:
    """Log an options trade"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO options_trades (
            contract_symbol, underlying, option_type, strike, expiration,
            entry_date, entry_price, quantity, signal_score, signal_data,
            thesis, status, flow_signal_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
    """, (
        contract_symbol,
        underlying,
        option_type,
        strike,
        expiration,
        datetime.now().isoformat(),
        entry_price,
        quantity,
        signal_score,
        json.dumps(signal_data) if signal_data else None,
        thesis,
        flow_signal_id,
    ))

    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_options_trade_exit(trade_id: int, exit_price: float, exit_reason: str):
    """Update options trade with exit info"""
    conn = get_connection()
    cursor = conn.cursor()

    # Get entry info for P/L calculation
    cursor.execute("SELECT entry_price, quantity FROM options_trades WHERE id = ?", (trade_id,))
    row = cursor.fetchone()

    if row:
        entry_price = row['entry_price']
        quantity = row['quantity']
        # Options P/L: (exit - entry) * quantity * 100 (100 shares per contract)
        pnl_amount = (exit_price - entry_price) * quantity * 100
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0

        cursor.execute("""
            UPDATE options_trades
            SET exit_date = ?, exit_price = ?, exit_reason = ?,
                pnl_amount = ?, pnl_pct = ?, status = 'closed'
            WHERE id = ?
        """, (
            datetime.now().isoformat(),
            exit_price,
            exit_reason,
            pnl_amount,
            pnl_pct,
            trade_id,
        ))

    conn.commit()
    conn.close()


def get_options_trade_by_id(trade_id: int) -> dict:
    """Get options trade by ID"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM options_trades WHERE id = ?", (trade_id,))
    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_options_trade_by_contract(contract_symbol: str, status: str = 'open') -> dict:
    """Get options trade by contract symbol"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM options_trades
        WHERE contract_symbol = ? AND status = ?
        ORDER BY entry_date DESC LIMIT 1
    """, (contract_symbol, status))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def get_open_options_trades() -> list:
    """Get all open options trades"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM options_trades WHERE status = 'open'")
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return trades


def get_recent_options_trades(limit: int = 20) -> list:
    """Get recent options trades"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM options_trades
        ORDER BY entry_date DESC
        LIMIT ?
    """, (limit,))

    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return trades


def get_options_performance() -> dict:
    """Get options trading performance statistics"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_trades,
            SUM(CASE WHEN status = 'closed' AND pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN status = 'closed' AND pnl_pct <= 0 THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN status = 'closed' AND pnl_pct > 0 THEN pnl_pct ELSE NULL END) as avg_win,
            AVG(CASE WHEN status = 'closed' AND pnl_pct <= 0 THEN pnl_pct ELSE NULL END) as avg_loss,
            SUM(CASE WHEN status = 'closed' THEN pnl_amount ELSE 0 END) as total_pnl
        FROM options_trades
    """)

    row = cursor.fetchone()
    conn.close()

    if not row or row['total_trades'] == 0:
        return {
            'total_trades': 0,
            'open_trades': 0,
            'win_rate': 0,
            'avg_win': 0,
            'avg_loss': 0,
            'total_pnl': 0,
        }

    closed_trades = (row['wins'] or 0) + (row['losses'] or 0)
    win_rate = round((row['wins'] or 0) / closed_trades * 100, 1) if closed_trades > 0 else 0

    return {
        'total_trades': row['total_trades'],
        'open_trades': row['open_trades'] or 0,
        'win_rate': win_rate,
        'avg_win': round(row['avg_win'] or 0, 1),
        'avg_loss': round(row['avg_loss'] or 0, 1),
        'total_pnl': round(row['total_pnl'] or 0, 2),
    }


def log_flow_scan_history(
    filters: dict,
    signals_found: int,
    signals_analyzed: int,
    buy_recommendations: int,
    trades_executed: int,
    top_signals: list
):
    """Log flow scan history"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO flow_scan_history (
            scan_time, filters, signals_found, signals_analyzed,
            buy_recommendations, trades_executed, top_signals
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(),
        json.dumps(filters),
        signals_found,
        signals_analyzed,
        buy_recommendations,
        trades_executed,
        json.dumps(top_signals[:10]) if top_signals else '[]',
    ))

    conn.commit()
    conn.close()


if __name__ == "__main__":
    # Test database
    conn = get_connection()
    print("Database initialized successfully")

    # Show tables
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"Tables: {[t['name'] for t in tables]}")
    conn.close()
