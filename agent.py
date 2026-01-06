"""
Agent Module - Claude API for autonomous trade reasoning
"""
import json
import anthropic
from datetime import datetime
from config import ANTHROPIC_API_KEY, TRADING_CONFIG
from db import get_recent_trades, get_signal_performance, get_watchlist, save_watchlist, get_poor_signal_summary


SYSTEM_PROMPT = """You are an autonomous momentum trading agent managing a real portfolio.

## PRIME OBJECTIVE
Grow capital with exceptional results through momentum trading. You aim for outsized returns by capturing strong momentum moves while protecting capital aggressively.

## YOUR ROLE
You make BUY, SELL, and WATCH decisions for a momentum strategy targeting 5-10 day holds.
Your decisions are executed automatically - be precise and deliberate.
Every decision must have clear reasoning.

## STRATEGY RULES

### Entry Criteria
- Buy stocks showing momentum breakouts (gap + volume + follow-through)
- Prioritize: Score >= 12, Volume surge > 1.5x, Clear breakout pattern
- Strong conviction entries only - quality over quantity

### Exit Criteria
- Sell when momentum fades (reversal signals) or target reached
- Protect gains aggressively - don't let winners become losers
- Cut losses quickly when thesis breaks

### Risk Management
- Maximum 6 concurrent positions total (2 per market cap category)
- 10% of portfolio per position
- 60% maximum total exposure
- 5% trailing stop (set automatically on entry)
- Per-cap limits: Max 2 large cap, 2 mid cap, 2 small cap positions

## YOU WILL RECEIVE

1. **ACCOUNT STATUS**: Equity, buying power, current exposure %
2. **OPEN POSITIONS**: Symbol, entry price, current P/L %, days held, reversal score (0-13)
3. **NEW CANDIDATES**: Fresh momentum breakout candidates from scanner
4. **WATCHLIST**: Previously flagged stocks to monitor
5. **TRADE HISTORY**: Recent wins/losses for pattern recognition

## YOUR DECISIONS

### For OPEN POSITIONS - decide: HOLD or CLOSE
**CLOSE if:**
- Reversal score >= 5 (momentum clearly fading)
- P/L > +15% (take profit, protect gains)
- P/L < -3% AND reversal score >= 3 (thesis breaking, cut early)
- Held > 10 days with P/L between -2% and +3% (dead money, opportunity cost)

**HOLD if:**
- Momentum intact (reversal score < 3)
- Trending well, let winner run
- Clear reason to stay in trade

### For CANDIDATES - decide: BUY, WATCH, or SKIP
**BUY if:**
- Score >= 12 AND momentum_breakout = True
- NOT already holding the symbol
- Have available position slot (< 6 total, < 2 in same cap category)
- High conviction: gap > 2% OR breakout with volume > 2x
- Clear catalyst or momentum driver

**WATCH if:**
- Good setup but not quite ready (score 8-11)
- Already at max positions - queue for next slot
- Want to see follow-through before entry
- Interesting but needs confirmation

**SKIP if:**
- Weak setup (score < 8)
- Poor risk/reward
- Already holding
- Low conviction

### For WATCHLIST - decide: PROMOTE, KEEP, or REMOVE
**PROMOTE to BUY if:**
- Setup has improved since added
- Position slot now available
- Momentum confirmed

**REMOVE if:**
- Setup deteriorated
- Missed the move
- No longer interesting

## RESPONSE FORMAT (strict JSON)

{
  "timestamp": "ISO timestamp",
  "market_assessment": "2-3 sentence market read and bias",

  "position_actions": [
    {
      "symbol": "XXX",
      "action": "HOLD|CLOSE",
      "current_pnl": "+X.X%",
      "reversal_score": 0,
      "reasoning": "Detailed why"
    }
  ],

  "candidate_actions": [
    {
      "symbol": "XXX",
      "action": "BUY|WATCH|SKIP",
      "score": 15,
      "conviction": 0.85,
      "reasoning": "Detailed why"
    }
  ],

  "watchlist_updates": [
    {
      "symbol": "XXX",
      "action": "ADD|REMOVE|PROMOTE",
      "reasoning": "Why watching or removing"
    }
  ],

  "execution_plan": {
    "closes": ["SYM1"],
    "buys": ["SYM2"],
    "new_watchlist": ["SYM3", "SYM4"]
  },

  "portfolio_summary": "Brief summary of actions and rationale"
}

## IMPORTANT GUIDELINES

1. **Capital preservation first** - Protect downside aggressively
2. **Let winners run** - Don't cut winners too early unless reversal signals
3. **Quality over quantity** - Better to miss a trade than force a bad one
4. **Clear reasoning** - Every action needs a "why" that would make sense tomorrow
5. **Watchlist is your pipeline** - Keep it primed with 3-5 quality setups
6. **Learn from history** - Note patterns in what's working/not working

## SELF-LEARNING LOOP

Trades closed due to reversal signals are logged as "poor signals" for review:
- **Poor Signal**: A trade that looked good at entry but triggered reversal exit
- **Pattern Recognition**: Watch for common entry signals that lead to reversal exits
- **Avoid Repeating Mistakes**: If you see a candidate with similar signals to past poor trades, be more cautious
- **Weekly Review**: Poor signal patterns are summarized weekly to identify systematic issues

When evaluating candidates, consider:
- Does this setup resemble any recent poor signals?
- Are the entry signals ones that have historically led to quick reversals?
- Is the setup truly exceptional, or just "good enough"?
"""


def get_agent_client() -> anthropic.Anthropic:
    """Initialize Anthropic client"""
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def format_account_status(account: dict, positions: list) -> str:
    """Format account status for prompt"""
    total_exposure = sum(p.get('market_value', 0) for p in positions)
    exposure_pct = (total_exposure / account['equity'] * 100) if account['equity'] > 0 else 0

    lines = [
        "## ACCOUNT STATUS\n",
        f"- Equity: ${account['equity']:,.2f}",
        f"- Buying Power: ${account['buying_power']:,.2f}",
        f"- Open Positions: {len(positions)} / {TRADING_CONFIG['max_positions']} max",
        f"- Current Exposure: ${total_exposure:,.2f} ({exposure_pct:.1f}%)",
        f"- Max Exposure Allowed: {TRADING_CONFIG['max_portfolio_risk']*100:.0f}%",
        ""
    ]
    return "\n".join(lines)


def format_positions_for_prompt(positions: list, reversal_scores: dict = None) -> str:
    """Format current positions for prompt"""
    if not positions:
        return "## OPEN POSITIONS\nNo open positions.\n"

    reversal_scores = reversal_scores or {}

    lines = ["## OPEN POSITIONS\n"]
    for p in positions:
        symbol = p['symbol']
        pnl_pct = p.get('unrealized_plpc', 0) * 100
        pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
        rev_score = reversal_scores.get(symbol, {}).get('score', 0)
        rev_signals = reversal_scores.get(symbol, {}).get('signals', [])

        lines.append(f"**{symbol}** {pnl_emoji}")
        lines.append(f"  - Entry: ${p.get('avg_entry_price', 0):.2f}")
        lines.append(f"  - Current: ${p.get('current_price', 0):.2f}")
        lines.append(f"  - P/L: {pnl_pct:+.2f}% (${p.get('unrealized_pl', 0):+.2f})")
        lines.append(f"  - Shares: {p.get('qty', 0)}")
        lines.append(f"  - Market Value: ${p.get('market_value', 0):,.2f}")
        lines.append(f"  - Reversal Score: {rev_score}/13")
        if rev_signals:
            lines.append(f"  - Reversal Signals: {', '.join(rev_signals)}")
        lines.append("")

    return "\n".join(lines)


def format_candidates_for_prompt(candidates: list) -> str:
    """Format candidates list for the prompt"""
    if not candidates:
        return "## NEW CANDIDATES\nNo new candidates from scanner.\n"

    lines = ["## NEW CANDIDATES\n"]
    for c in candidates:
        breakout_status = "✅ BREAKOUT" if c.get('momentum_breakout') else "⏳ No breakout"

        lines.append(f"**{c['symbol']}** (${c['price']:.2f}) - Score: {c['composite_score']}/20 {breakout_status}")
        lines.append(f"  - SMA Aligned: {c['sma_aligned']}")
        lines.append(f"  - Volume Surge: {c['volume_surge']}x")
        lines.append(f"  - Gap Up: {c.get('gap_up', 0):+.2f}%")
        lines.append(f"  - Breakout 5D: {c.get('breakout_5d', False)} ({c.get('breakout_pct', 0):+.2f}%)")
        lines.append(f"  - Intraday Strength: {c.get('intraday_strength', 0):.2f}")
        lines.append(f"  - 10D ROC: {c['roc_10d']:+.2f}%")
        lines.append(f"  - Near 52W High: {c['near_52w_high']} ({c['pct_from_high']:.1f}% from high)")
        lines.append("")

    return "\n".join(lines)


def format_watchlist_for_prompt(watchlist: list) -> str:
    """Format watchlist for prompt"""
    if not watchlist:
        return "## WATCHLIST\nWatchlist is empty.\n"

    lines = ["## WATCHLIST\n"]
    for item in watchlist:
        lines.append(f"**{item['symbol']}** - Added: {item.get('added_date', 'N/A')}")
        lines.append(f"  - Reason: {item.get('reason', 'N/A')}")
        lines.append(f"  - Original Score: {item.get('score', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def format_trade_history(trades: list) -> str:
    """Format recent trade history for the prompt"""
    if not trades:
        return "## TRADE HISTORY\nNo recent trades.\n"

    # Calculate stats
    closed = [t for t in trades if t.get('status') == 'closed']
    wins = sum(1 for t in closed if t.get('pnl_pct', 0) > 0)
    losses = len(closed) - wins
    win_rate = (wins / len(closed) * 100) if closed else 0
    avg_win = sum(t.get('pnl_pct', 0) for t in closed if t.get('pnl_pct', 0) > 0) / wins if wins else 0
    avg_loss = sum(t.get('pnl_pct', 0) for t in closed if t.get('pnl_pct', 0) <= 0) / losses if losses else 0

    lines = [
        "## TRADE HISTORY\n",
        f"**Stats**: {wins}W / {losses}L ({win_rate:.0f}% win rate)",
        f"**Avg Win**: {avg_win:+.1f}% | **Avg Loss**: {avg_loss:+.1f}%\n",
        "**Recent Trades:**"
    ]

    for t in trades[:10]:
        if t.get('status') == 'closed':
            result = "WIN" if t.get('pnl_pct', 0) > 0 else "LOSS"
            emoji = "🟢" if result == "WIN" else "🔴"
            lines.append(f"{emoji} {t['symbol']}: {t.get('pnl_pct', 0):+.1f}% ({t.get('exit_reason', 'N/A')})")
        else:
            lines.append(f"⏳ {t['symbol']}: OPEN @ ${t.get('entry_price', 0):.2f}")

    return "\n".join(lines)


def format_poor_signals_for_prompt() -> str:
    """Format poor signal patterns for the prompt"""
    summary = get_poor_signal_summary(days=14)  # Last 2 weeks

    if summary['total_poor_signals'] == 0:
        return "## POOR SIGNAL PATTERNS\nNo recent poor signals to report. Keep up the good work!\n"

    lines = ["## POOR SIGNAL PATTERNS (Self-Learning)\n"]
    lines.append(f"⚠️ **{summary['total_poor_signals']} trades closed due to reversal in last 14 days**\n")

    if summary['avg_pnl']:
        lines.append(f"- Average P/L of these trades: {summary['avg_pnl']:+.1f}%")
    if summary['avg_holding_days']:
        lines.append(f"- Average holding period: {summary['avg_holding_days']:.1f} days")

    if summary['common_reversal_signals']:
        lines.append("\n**Common reversal triggers:**")
        for sig, count in summary['common_reversal_signals'][:3]:
            lines.append(f"  - {sig} ({count} occurrences)")

    if summary['common_entry_signals']:
        lines.append("\n**Entry signals that led to poor trades:**")
        for sig, count in summary['common_entry_signals'][:3]:
            lines.append(f"  - {sig} ({count} times) ← BE CAUTIOUS")

    lines.append("\n**Action:** Be extra cautious with candidates showing these entry patterns.")
    lines.append("")

    return "\n".join(lines)


def get_portfolio_decision(
    account: dict,
    positions: list,
    candidates: list,
    reversal_scores: dict = None,
    scan_type: str = "regular"
) -> dict:
    """
    Main entry point for autonomous portfolio decisions.

    Args:
        account: Account info from Alpaca
        positions: Current open positions
        candidates: New candidates from scanner
        reversal_scores: Reversal scores for open positions
        scan_type: "open" | "midday" | "close" for context

    Returns:
        Structured decision with actions to execute
    """
    print(f"[{datetime.now()}] Getting portfolio decision ({scan_type} scan)...")
    print(f"  Positions: {len(positions)}, Candidates: {len(candidates)}")

    # Get historical context
    recent_trades = get_recent_trades(limit=20)
    watchlist = get_watchlist()

    # Build prompt
    scan_context = {
        "open": "MARKET OPEN scan - Looking for gap-up breakouts with volume",
        "midday": "MIDDAY scan - Checking momentum continuation and new setups",
        "close": "MARKET CLOSE scan - Reviewing positions before close, noting overnight holds"
    }

    user_prompt = f"""
## SCAN CONTEXT
{scan_context.get(scan_type, "Regular scan")}
Timestamp: {datetime.now().isoformat()}

{format_account_status(account, positions)}

{format_positions_for_prompt(positions, reversal_scores)}

{format_candidates_for_prompt(candidates)}

{format_watchlist_for_prompt(watchlist)}

{format_trade_history(recent_trades)}

{format_poor_signals_for_prompt()}

---

Analyze the portfolio and provide your decisions. Remember:
- You can have maximum {TRADING_CONFIG['max_positions']} positions
- Current positions: {len(positions)}
- Available slots: {TRADING_CONFIG['max_positions'] - len(positions)}
- Check poor signal patterns above before recommending BUY on any candidate

Provide your complete analysis and action plan.
"""

    # Call Claude API
    client = get_agent_client()

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )

        response_text = response.content[0].text
        print(f"Agent response received ({len(response_text)} chars)")

        # Try to extract JSON from response
        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response_text[start:end]
                decision = json.loads(json_str)

                # Update watchlist based on decision
                if 'execution_plan' in decision and 'new_watchlist' in decision['execution_plan']:
                    new_watchlist = []
                    for sym in decision['execution_plan']['new_watchlist']:
                        # Find candidate data
                        candidate = next((c for c in candidates if c['symbol'] == sym), None)
                        new_watchlist.append({
                            'symbol': sym,
                            'added_date': datetime.now().isoformat()[:10],
                            'score': candidate['composite_score'] if candidate else 'N/A',
                            'reason': next(
                                (a['reasoning'] for a in decision.get('candidate_actions', [])
                                 if a['symbol'] == sym),
                                'Added to watchlist'
                            )
                        })
                    save_watchlist(new_watchlist)

                return decision

        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}")

        # Fallback
        return {
            "market_assessment": "Failed to parse structured response",
            "position_actions": [],
            "candidate_actions": [],
            "watchlist_updates": [],
            "execution_plan": {"closes": [], "buys": [], "new_watchlist": []},
            "portfolio_summary": response_text[:500],
            "raw_response": response_text,
            "parse_error": True
        }

    except Exception as e:
        print(f"Agent error: {e}")
        return {
            "market_assessment": f"Error: {str(e)}",
            "position_actions": [],
            "candidate_actions": [],
            "watchlist_updates": [],
            "execution_plan": {"closes": [], "buys": [], "new_watchlist": []},
            "portfolio_summary": f"Agent error: {str(e)}",
            "error": True
        }


# Legacy function for backward compatibility
def get_trade_recommendation(candidates: list) -> dict:
    """Legacy wrapper - use get_portfolio_decision for full functionality"""
    from executor import get_account_info, get_positions

    account = get_account_info()
    positions = get_positions()

    decision = get_portfolio_decision(account, positions, candidates)

    # Convert to legacy format
    top_pick = None
    if decision.get('execution_plan', {}).get('buys'):
        top_pick = decision['execution_plan']['buys'][0]

    return {
        "reasoning": decision.get('portfolio_summary', ''),
        "decisions": decision.get('candidate_actions', []),
        "top_pick": top_pick,
        "full_decision": decision
    }


if __name__ == "__main__":
    # Test with sample data
    from executor import get_account_info, get_positions

    account = get_account_info()
    positions = get_positions()

    test_candidates = [
        {
            "symbol": "TEST",
            "price": 100.0,
            "composite_score": 15,
            "sma_aligned": True,
            "roc_10d": 8.5,
            "volume_surge": 1.8,
            "gap_up": 2.5,
            "breakout_5d": True,
            "breakout_pct": 1.2,
            "intraday_strength": 0.75,
            "momentum_breakout": True,
            "near_52w_high": True,
            "pct_from_high": 2.5
        }
    ]

    result = get_portfolio_decision(account, positions, test_candidates, scan_type="midday")
    print(json.dumps(result, indent=2))
