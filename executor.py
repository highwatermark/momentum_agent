"""
Executor Module - Places and manages orders via Alpaca
"""
from datetime import datetime
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    TrailingStopOrderRequest,
    GetOrdersRequest
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderStatus, QueryOrderStatus
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, TRADING_CONFIG, get_cap_config
from db import log_trade, get_trade_by_symbol, update_trade_exit, log_poor_signal, log_error


def get_trading_client() -> TradingClient:
    """Initialize Alpaca trading client"""
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def get_account_info() -> dict:
    """Get account information"""
    client = get_trading_client()
    account = client.get_account()
    return {
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "positions_count": len(client.get_all_positions())
    }


def get_positions() -> list[dict]:
    """Get all current positions"""
    client = get_trading_client()
    positions = client.get_all_positions()
    return [
        {
            "symbol": p.symbol,
            "qty": float(p.qty),
            "market_value": float(p.market_value),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_pl": float(p.unrealized_pl),
            "unrealized_plpc": float(p.unrealized_plpc)
        }
        for p in positions
    ]


def calculate_position_size(account_equity: float, price: float) -> int:
    """
    Calculate position size based on config.
    Returns number of shares (integer).
    """
    position_value = account_equity * TRADING_CONFIG["position_size_pct"]
    shares = int(position_value / price)
    return max(1, shares)  # At least 1 share


def get_symbol_cap(symbol: str) -> str:
    """Get the market cap category for a symbol"""
    import json
    try:
        with open("data/universe.json", "r") as f:
            data = json.load(f)
        symbols = data.get("symbols", {})
        if isinstance(symbols, dict):
            for cap, cap_symbols in symbols.items():
                if symbol in cap_symbols:
                    return cap
    except Exception:
        pass
    return None


def place_entry_order(symbol: str, signals: dict, cap: str = None) -> dict:
    """
    Place entry order with trailing stop.

    1. Market order to enter
    2. Trailing stop order for exit (covers ENTIRE position, not just new shares)

    Args:
        symbol: Stock symbol
        signals: Signal data from scanner
        cap: Market cap category (large/mid/small) for position limit checks

    Returns order details.
    """
    client = get_trading_client()

    # Get account info
    account = client.get_account()
    equity = float(account.equity)

    # Check if we can open more positions
    positions = client.get_all_positions()

    # Check if we already have a position in this symbol
    existing_position = None
    existing_qty = 0
    for p in positions:
        if p.symbol == symbol:
            existing_position = p
            existing_qty = float(p.qty)
            break

    # Get cap config for position limits
    cap_config = get_cap_config(cap)
    max_total_positions = TRADING_CONFIG["max_positions"]
    max_cap_positions = cap_config["max_positions"]

    # Count positions by cap category
    unique_symbols = {p.symbol for p in positions}

    # Check total position limit
    if symbol not in unique_symbols and len(unique_symbols) >= max_total_positions:
        return {
            "success": False,
            "error": f"Max total positions ({max_total_positions}) reached"
        }

    # Check per-cap position limit
    if cap and symbol not in unique_symbols:
        cap_positions = sum(1 for p in positions if get_symbol_cap(p.symbol) == cap)
        if cap_positions >= max_cap_positions:
            return {
                "success": False,
                "error": f"Max {cap} cap positions ({max_cap_positions}) reached"
            }

    # Calculate position size
    price = signals["price"]
    qty = calculate_position_size(equity, price)

    # Check if this exceeds max portfolio risk
    current_exposure = sum(float(p.market_value) for p in positions)
    new_exposure = current_exposure + (qty * price)
    if new_exposure / equity > TRADING_CONFIG["max_portfolio_risk"]:
        return {
            "success": False,
            "error": f"Would exceed max portfolio risk ({TRADING_CONFIG['max_portfolio_risk']*100}%)"
        }

    try:
        # Cancel any existing trailing stops for this symbol before placing new order
        # This allows us to place a consolidated stop for the entire position
        existing_orders = client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=[symbol]
        ))
        for o in existing_orders:
            if o.type.value == 'trailing_stop':
                try:
                    client.cancel_order_by_id(o.id)
                    print(f"  Cancelled existing trailing stop for {symbol}")
                except Exception as cancel_err:
                    print(f"  Warning: Could not cancel existing stop: {cancel_err}")

        # Place market order
        market_order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY
        )
        entry_order = client.submit_order(market_order)

        # Wait for market order to fill before placing trailing stop
        import time
        max_wait = 30  # seconds
        wait_interval = 0.5
        elapsed = 0
        filled_qty = 0

        while elapsed < max_wait:
            order_status = client.get_order_by_id(entry_order.id)
            if order_status.status in [OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED]:
                filled_qty = float(order_status.filled_qty or 0)
                if filled_qty > 0:
                    break
            time.sleep(wait_interval)
            elapsed += wait_interval

        # Place trailing stop order for ENTIRE position (existing + new shares)
        stop_order = None
        stop_error = None
        if filled_qty > 0:
            total_position_qty = int(existing_qty + filled_qty)
            trail_pct = TRADING_CONFIG["trailing_stop_pct"] * 100  # Convert to percentage

            # Retry trailing stop placement up to 3 times
            for attempt in range(3):
                try:
                    trailing_stop = TrailingStopOrderRequest(
                        symbol=symbol,
                        qty=total_position_qty,
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.GTC,
                        trail_percent=trail_pct
                    )
                    stop_order = client.submit_order(trailing_stop)
                    if existing_qty > 0:
                        print(f"  Trailing stop now covers entire position: {total_position_qty} shares")
                    break  # Success, exit retry loop
                except Exception as stop_err:
                    stop_error = str(stop_err)
                    print(f"  Warning: Trailing stop attempt {attempt + 1} failed: {stop_err}")
                    if attempt < 2:
                        time.sleep(1)  # Wait before retry

            # Alert if trailing stop failed after all retries
            if stop_order is None and stop_error:
                print(f"  ⚠️ CRITICAL: Position {symbol} has NO trailing stop protection!")
                print(f"     Error: {stop_error}")
                print(f"     Manual intervention required: place trailing stop for {total_position_qty} shares")
        else:
            print(f"  Warning: Market order not filled after {max_wait}s, no trailing stop placed")
        
        # Log the trade
        trade_data = {
            "symbol": symbol,
            "entry_date": datetime.now().isoformat(),
            "entry_price": price,
            "quantity": int(filled_qty) if filled_qty > 0 else qty,
            "entry_order_id": str(entry_order.id),
            "stop_order_id": str(stop_order.id) if stop_order else None,
            "signals": signals,
            "status": "open"
        }
        log_trade(trade_data)

        trail_pct = TRADING_CONFIG["trailing_stop_pct"] * 100
        return {
            "success": True,
            "symbol": symbol,
            "qty": int(filled_qty) if filled_qty > 0 else qty,
            "estimated_cost": qty * price,
            "entry_order_id": str(entry_order.id),
            "stop_order_id": str(stop_order.id) if stop_order else None,
            "trailing_stop_pct": trail_pct if stop_order else None,
            "stop_placed": stop_order is not None,
            "stop_error": stop_error
        }
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        log_error(
            error_type='trade',
            operation='buy',
            error_message=str(e),
            symbol=symbol,
            error_details=error_details,
            context={
                'price': signals.get('price'),
                'qty': qty if 'qty' in dir() else None,
                'score': signals.get('composite_score'),
                'rsi': signals.get('rsi_14')
            }
        )
        return {
            "success": False,
            "error": str(e)
        }


def close_position(symbol: str, reason: str = "manual", reversal_signals: list = None) -> dict:
    """
    Close a position by symbol.

    Args:
        symbol: Stock symbol to close
        reason: Exit reason (for logging)
        reversal_signals: List of reversal signals detected (for poor signal logging)
    """
    client = get_trading_client()

    try:
        # Get position details before closing
        position = client.get_open_position(symbol)
        qty = float(position.qty)
        entry_price = float(position.avg_entry_price)
        exit_price = float(position.current_price)
        market_value = float(position.market_value)

        # FIRST: Cancel any open orders for this symbol (e.g., trailing stops)
        # This releases the shares so we can sell them
        orders = client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=[symbol]
        ))
        for o in orders:
            try:
                client.cancel_order_by_id(o.id)
                print(f"    Cancelled order {o.id} ({o.type.value})")
            except Exception as cancel_err:
                print(f"    Warning: Could not cancel order {o.id}: {cancel_err}")

        # Wait for cancellations to be processed and verify shares are available
        import time
        max_wait = 5  # seconds
        wait_interval = 0.5
        elapsed = 0

        while elapsed < max_wait:
            time.sleep(wait_interval)
            elapsed += wait_interval
            # Re-fetch position to check available qty
            try:
                updated_position = client.get_open_position(symbol)
                # If we can read position, shares should be available now
                break
            except Exception:
                # Position might be closed already or still locked
                continue

        # THEN: Place market sell order
        order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        result = client.submit_order(order)

        # Update trade record in database
        try:
            trade = get_trade_by_symbol(symbol, status="open")
            if trade:
                pnl_amount = (exit_price - entry_price) * qty
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100

                exit_data = {
                    "exit_date": datetime.now().isoformat(),
                    "exit_price": exit_price,
                    "exit_reason": reason,
                    "pnl_amount": pnl_amount,
                    "pnl_pct": pnl_pct,
                    "max_gain": trade.get("max_gain_during_trade"),
                    "max_drawdown": trade.get("max_drawdown_during_trade")
                }
                update_trade_exit(trade["id"], exit_data)
                print(f"    Trade record updated: PnL {pnl_pct:+.2f}%")

                # Log poor signal if closed due to reversal indicator
                if "reversal" in reason.lower():
                    # Parse reversal score from reason (e.g., "auto_reversal_score_7")
                    parsed_reversal_score = 0
                    try:
                        if "score_" in reason:
                            parsed_reversal_score = int(reason.split("score_")[-1].split("_")[0])
                    except Exception:
                        pass

                    trade_with_exit = {
                        **trade,
                        "exit_date": exit_data["exit_date"],
                        "exit_price": exit_price,
                        "exit_reason": reason,
                        "pnl_pct": pnl_pct
                    }
                    log_poor_signal(
                        trade=trade_with_exit,
                        reversal_score=parsed_reversal_score,
                        reversal_signals=reversal_signals or [],  # Use passed signals if available
                        notes=f"Closed due to reversal. Original entry signals may need review."
                    )
                    print(f"    ⚠️ Logged as poor signal for self-learning review")
            else:
                print(f"    Warning: No open trade record found for {symbol}")
        except Exception as db_err:
            print(f"    Warning: Could not update trade record: {db_err}")

        return {
            "success": True,
            "symbol": symbol,
            "qty": qty,
            "reason": reason,
            "order_id": str(result.id),
            "exit_price": exit_price,
            "pnl_pct": ((exit_price - entry_price) / entry_price) * 100
        }

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        log_error(
            error_type='trade',
            operation='close',
            error_message=str(e),
            symbol=symbol,
            error_details=error_details,
            context={
                'reason': reason,
                'reversal_signals': reversal_signals
            }
        )
        return {
            "success": False,
            "error": str(e)
        }


def get_open_orders() -> list[dict]:
    """Get all open orders"""
    client = get_trading_client()
    orders = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    return [
        {
            "id": str(o.id),
            "symbol": o.symbol,
            "side": o.side.value,
            "qty": float(o.qty),
            "type": o.type.value,
            "status": o.status.value
        }
        for o in orders
    ]


def execute_trade(symbol: str, signals: dict, decision: dict, cap: str = None) -> dict:
    """
    Execute a trade based on agent decision.

    Args:
        symbol: Stock symbol
        signals: Signal data from scanner
        decision: Decision data from agent
        cap: Market cap category for position limit checks

    Returns execution result.
    """
    print(f"[{datetime.now()}] Executing trade for {symbol}...")

    # RSI enforcement - block overbought entries
    rsi = signals.get('rsi_14', 50)
    if rsi >= 70:
        print(f"✗ RSI too high ({rsi:.0f} >= 70) - blocking entry")
        return {"success": False, "error": f"RSI too high ({rsi:.0f} >= 70)"}

    result = place_entry_order(symbol, signals, cap=cap)
    
    if result["success"]:
        print(f"✓ Entered {symbol}: {result['qty']} shares @ ~${signals['price']:.2f}")
        print(f"  Trailing stop: {result['trailing_stop_pct']}%")
    else:
        print(f"✗ Failed to enter {symbol}: {result['error']}")
    
    return result


if __name__ == "__main__":
    # Test account info
    info = get_account_info()
    print(f"Account equity: ${info['equity']:,.2f}")
    print(f"Positions: {info['positions_count']}")
    
    positions = get_positions()
    for p in positions:
        print(f"  {p['symbol']}: {p['qty']} shares, P/L: ${p['unrealized_pl']:.2f}")

