"""
Main Orchestrator for AI-Native Options Flow Trading System.

The orchestrator coordinates specialized subagents to:
- Scan for options flow
- Manage positions
- Assess risk
- Execute trades
"""
import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
import json

import pytz
from anthropic import Anthropic

from agents.definitions import (
    ORCHESTRATOR_PROMPT,
    FLOW_SCANNER_PROMPT,
    POSITION_MANAGER_PROMPT,
    RISK_MANAGER_PROMPT,
    EXECUTOR_PROMPT,
)
from agents.hooks import SafetyGateHook, get_execution_state
from agent_config import config, Config
from tools import TOOL_REGISTRY, TOOL_DESCRIPTIONS

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


@dataclass
class SessionState:
    """Persistent session state."""
    session_id: str
    started_at: datetime
    signals_seen_today: List[Dict] = field(default_factory=list)
    trades_today: List[Dict] = field(default_factory=list)
    decisions_log: List[Dict] = field(default_factory=list)
    last_scan_time: Optional[datetime] = None
    scan_interval_seconds: int = 60
    context_tokens_used: int = 0

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "signals_seen_today": self.signals_seen_today,
            "trades_today": self.trades_today,
            "decisions_count": len(self.decisions_log),
            "last_scan_time": self.last_scan_time.isoformat() if self.last_scan_time else None,
            "scan_interval_seconds": self.scan_interval_seconds,
        }

    def add_signal(self, signal: Dict):
        """Add a signal to today's history."""
        self.signals_seen_today.append({
            **signal,
            "seen_at": datetime.now(ET).isoformat(),
        })

    def add_trade(self, trade: Dict):
        """Add a trade to today's history."""
        self.trades_today.append({
            **trade,
            "executed_at": datetime.now(ET).isoformat(),
        })

    def log_decision(self, decision: Dict):
        """Log a decision for review."""
        self.decisions_log.append({
            **decision,
            "timestamp": datetime.now(ET).isoformat(),
        })


@dataclass
class SubagentResult:
    """Result from a subagent call."""
    agent_name: str
    success: bool
    output: str
    data: Optional[Dict] = None
    error: Optional[str] = None


class OptionsOrchestrator:
    """
    Main orchestrator agent that coordinates the options trading system.

    Uses Claude Agent SDK patterns:
    - Persistent context through session state
    - Subagent delegation for specialized tasks
    - Tool use with safety hooks
    - Adaptive behavior based on context
    """

    def __init__(self, config: Config = config):
        self.config = config
        self.client = Anthropic(api_key=config.anthropic_api_key)
        self.safety_hook = SafetyGateHook(
            config=asdict(config.trading),
            telegram_notifier=None,  # Set up separately
        )

        # Session state
        self.session = SessionState(
            session_id=self._generate_session_id(),
            started_at=datetime.now(ET),
        )

        # Conversation history for context
        self.messages: List[Dict] = []

        # Subagent definitions
        self.subagents = {
            "flow_scanner": {
                "prompt": FLOW_SCANNER_PROMPT,
                "tools": ["uw_flow_scan", "stock_quote", "earnings_check", "iv_rank"],
            },
            "position_manager": {
                "prompt": POSITION_MANAGER_PROMPT,
                "tools": ["get_positions", "get_quote", "estimate_greeks"],
            },
            "risk_manager": {
                "prompt": RISK_MANAGER_PROMPT,
                "tools": ["portfolio_greeks", "get_account_info"],
            },
            "executor": {
                "prompt": EXECUTOR_PROMPT,
                "tools": ["find_contract", "check_liquidity", "place_order", "close_position", "execute_roll"],
            },
        }

        logger.info(f"Orchestrator initialized, session: {self.session.session_id}")

    def _generate_session_id(self) -> str:
        """Generate unique session ID."""
        import uuid
        return f"orch-{datetime.now(ET).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    def _is_market_hours(self) -> bool:
        """Check if currently market hours."""
        now = datetime.now(ET)
        market_open = now.replace(
            hour=self.config.market_open_hour,
            minute=self.config.market_open_minute,
            second=0,
        )
        market_close = now.replace(
            hour=self.config.market_close_hour,
            minute=self.config.market_close_minute,
            second=0,
        )

        # Check if weekday
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False

        return market_open <= now <= market_close

    def _build_context(self) -> str:
        """Build current context for the orchestrator."""
        exec_state = get_execution_state()

        context = f"""
CURRENT STATE
=============
Time: {datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')}
Market Hours: {'YES' if self._is_market_hours() else 'NO'}

Execution State:
- Executions Today: {exec_state.executions_today}/3
- Positions: {exec_state.positions_count}/4
- Daily P/L: ${exec_state.daily_pnl:.2f}
- Circuit Breaker: {'OPEN' if exec_state.circuit_breaker_open else 'CLOSED'}

Session Activity:
- Signals Seen Today: {len(self.session.signals_seen_today)}
- Trades Today: {len(self.session.trades_today)}
- Last Scan: {self.session.last_scan_time.strftime('%H:%M:%S') if self.session.last_scan_time else 'Never'}

Recent Signals (last 5):
"""
        for signal in self.session.signals_seen_today[-5:]:
            context += f"- {signal.get('symbol')} {signal.get('option_type')} ${signal.get('strike')} Score:{signal.get('score')}\n"

        context += "\nRecent Trades (today):\n"
        for trade in self.session.trades_today:
            context += f"- {trade.get('symbol')} {trade.get('action')} P/L: ${trade.get('pnl', 0):.2f}\n"

        return context

    def _build_tool_schema(self, tool_names: List[str]) -> List[Dict]:
        """Build Claude tool schema for specified tools."""
        schemas = []

        tool_schemas = {
            "uw_flow_scan": {
                "name": "uw_flow_scan",
                "description": TOOL_DESCRIPTIONS["uw_flow_scan"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "min_premium": {"type": "number", "description": "Minimum premium filter"},
                        "min_score": {"type": "integer", "description": "Minimum signal score"},
                        "limit": {"type": "integer", "description": "Max signals to return"},
                    },
                },
            },
            "get_positions": {
                "name": "get_positions",
                "description": TOOL_DESCRIPTIONS["get_positions"],
                "input_schema": {"type": "object", "properties": {}},
            },
            "portfolio_greeks": {
                "name": "portfolio_greeks",
                "description": TOOL_DESCRIPTIONS["portfolio_greeks"],
                "input_schema": {"type": "object", "properties": {}},
            },
            "get_account_info": {
                "name": "get_account_info",
                "description": TOOL_DESCRIPTIONS["get_account_info"],
                "input_schema": {"type": "object", "properties": {}},
            },
            "get_quote": {
                "name": "get_quote",
                "description": TOOL_DESCRIPTIONS["get_quote"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol to quote"},
                        "is_option": {"type": "boolean", "description": "Whether this is an option"},
                    },
                    "required": ["symbol"],
                },
            },
            "estimate_greeks": {
                "name": "estimate_greeks",
                "description": TOOL_DESCRIPTIONS["estimate_greeks"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Option symbol"},
                    },
                    "required": ["symbol"],
                },
            },
            "check_liquidity": {
                "name": "check_liquidity",
                "description": TOOL_DESCRIPTIONS["check_liquidity"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Option symbol"},
                    },
                    "required": ["symbol"],
                },
            },
            "find_contract": {
                "name": "find_contract",
                "description": TOOL_DESCRIPTIONS["find_contract"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "underlying": {"type": "string"},
                        "expiration": {"type": "string"},
                        "strike": {"type": "number"},
                        "option_type": {"type": "string"},
                    },
                    "required": ["underlying", "expiration", "strike", "option_type"],
                },
            },
            "place_order": {
                "name": "place_order",
                "description": TOOL_DESCRIPTIONS["place_order"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "qty": {"type": "integer"},
                        "limit_price": {"type": "number"},
                        "underlying": {"type": "string"},
                        "option_type": {"type": "string"},
                        "strike": {"type": "number"},
                        "expiration": {"type": "string"},
                        "signal_score": {"type": "integer"},
                        "signal_id": {"type": "integer"},
                    },
                    "required": ["symbol", "qty", "limit_price", "underlying", "option_type", "strike", "expiration"],
                },
            },
            "close_position": {
                "name": "close_position",
                "description": TOOL_DESCRIPTIONS["close_position"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "qty": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["symbol"],
                },
            },
            "execute_roll": {
                "name": "execute_roll",
                "description": TOOL_DESCRIPTIONS["execute_roll"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "new_expiration": {"type": "string"},
                        "new_strike": {"type": "number"},
                    },
                    "required": ["symbol", "new_expiration"],
                },
            },
            "earnings_check": {
                "name": "earnings_check",
                "description": TOOL_DESCRIPTIONS["earnings_check"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "blackout_days": {"type": "integer"},
                    },
                    "required": ["symbol"],
                },
            },
            "iv_rank": {
                "name": "iv_rank",
                "description": TOOL_DESCRIPTIONS["iv_rank"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                    },
                    "required": ["symbol"],
                },
            },
            "stock_quote": {
                "name": "stock_quote",
                "description": "Get current stock quote for underlying",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                    },
                    "required": ["symbol"],
                },
            },
            "send_notification": {
                "name": "send_notification",
                "description": TOOL_DESCRIPTIONS["send_notification"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "parse_mode": {"type": "string"},
                    },
                    "required": ["message"],
                },
            },
        }

        for name in tool_names:
            if name in tool_schemas:
                schemas.append(tool_schemas[name])

        return schemas

    def _execute_tool(self, tool_name: str, tool_params: Dict) -> Dict:
        """Execute a tool with safety hooks."""
        # Pre-execution check
        hook_result = self.safety_hook.pre_tool_use(tool_name, tool_params)
        if not hook_result.allowed:
            logger.warning(f"Tool {tool_name} blocked: {hook_result.reason}")
            return {"success": False, "error": f"Blocked: {hook_result.reason}"}

        # Execute tool
        tool_fn = TOOL_REGISTRY.get(tool_name)
        if not tool_fn:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        try:
            result = tool_fn(**tool_params)

            # Post-execution hook
            self.safety_hook.post_tool_use(tool_name, tool_params, result)

            return result
        except Exception as e:
            logger.error(f"Tool {tool_name} error: {e}")
            return {"success": False, "error": str(e)}

    async def call_subagent(
        self,
        agent_name: str,
        task: str,
        context: Optional[str] = None,
    ) -> SubagentResult:
        """
        Call a specialized subagent for a specific task.

        Args:
            agent_name: Name of subagent (flow_scanner, position_manager, etc.)
            task: Task description for the subagent
            context: Additional context to provide

        Returns:
            SubagentResult with the subagent's output
        """
        if agent_name not in self.subagents:
            return SubagentResult(
                agent_name=agent_name,
                success=False,
                output="",
                error=f"Unknown subagent: {agent_name}"
            )

        subagent_config = self.subagents[agent_name]
        tools = self._build_tool_schema(subagent_config["tools"])

        # Build subagent prompt
        system_prompt = subagent_config["prompt"]
        if context:
            system_prompt += f"\n\nADDITIONAL CONTEXT:\n{context}"

        messages = [{"role": "user", "content": task}]

        try:
            # Run subagent conversation loop
            max_turns = 10
            for turn in range(max_turns):
                response = self.client.messages.create(
                    model=self.config.orchestrator.flow_scanner.model,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=tools,
                    messages=messages,
                )

                # Check for tool use
                if response.stop_reason == "tool_use":
                    # Process tool calls
                    tool_results = []
                    for content in response.content:
                        if content.type == "tool_use":
                            tool_name = content.name
                            tool_params = content.input

                            logger.info(f"[{agent_name}] Calling tool: {tool_name}")
                            result = self._execute_tool(tool_name, tool_params)

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": content.id,
                                "content": json.dumps(result),
                            })

                    # Add assistant response and tool results
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})

                else:
                    # Agent is done
                    output = ""
                    for content in response.content:
                        if hasattr(content, "text"):
                            output += content.text

                    return SubagentResult(
                        agent_name=agent_name,
                        success=True,
                        output=output,
                    )

            # Max turns reached
            return SubagentResult(
                agent_name=agent_name,
                success=False,
                output="",
                error="Max turns reached"
            )

        except Exception as e:
            logger.error(f"Subagent {agent_name} error: {e}")
            return SubagentResult(
                agent_name=agent_name,
                success=False,
                output="",
                error=str(e)
            )

    async def run_scan_cycle(self) -> Optional[Dict]:
        """
        Run a single flow scanning cycle.

        Returns:
            Dict with scan results or None if no action needed
        """
        if not self._is_market_hours():
            logger.debug("Outside market hours, skipping scan")
            return None

        # Check if enough time has passed since last scan
        if self.session.last_scan_time:
            elapsed = (datetime.now(ET) - self.session.last_scan_time).total_seconds()
            if elapsed < self.session.scan_interval_seconds:
                return None

        logger.info("Starting flow scan cycle")
        self.session.last_scan_time = datetime.now(ET)

        # Build context for scan
        context = self._build_context()

        # Call flow scanner subagent
        scan_result = await self.call_subagent(
            "flow_scanner",
            task="Scan for new options flow signals. Return top opportunities ranked by score.",
            context=context,
        )

        if not scan_result.success:
            logger.warning(f"Flow scan failed: {scan_result.error}")
            return {"action": "none", "reason": "scan_failed"}

        # Parse scan output for signals
        # (In production, would use structured output)
        logger.info(f"Flow scan complete: {scan_result.output[:200]}...")

        # Log decision
        self.session.log_decision({
            "type": "flow_scan",
            "result": scan_result.output[:500],
        })

        return {"action": "scan_complete", "output": scan_result.output}

    async def run_position_check(self) -> Optional[Dict]:
        """
        Check existing positions for exit triggers.

        Returns:
            Dict with position review results
        """
        exec_state = get_execution_state()
        if exec_state.positions_count == 0:
            return None

        logger.info("Running position check")
        context = self._build_context()

        # Call position manager
        position_result = await self.call_subagent(
            "position_manager",
            task="Review all current positions. Check for exit triggers (profit target, stop loss, expiration). Report any positions needing action.",
            context=context,
        )

        if not position_result.success:
            logger.warning(f"Position check failed: {position_result.error}")
            return {"action": "none", "reason": "check_failed"}

        logger.info(f"Position check complete: {position_result.output[:200]}...")

        self.session.log_decision({
            "type": "position_check",
            "result": position_result.output[:500],
        })

        return {"action": "check_complete", "output": position_result.output}

    async def run_orchestration_loop(self):
        """
        Main orchestration loop.

        Runs continuously during market hours, coordinating subagents.
        """
        logger.info("Starting orchestration loop")

        while True:
            try:
                if self._is_market_hours():
                    # Run scan cycle
                    await self.run_scan_cycle()

                    # Check positions
                    await self.run_position_check()

                    # Adaptive sleep based on activity
                    await asyncio.sleep(self.session.scan_interval_seconds)
                else:
                    # Outside market hours - longer sleep
                    logger.debug("Outside market hours, sleeping 5 minutes")
                    await asyncio.sleep(300)

            except KeyboardInterrupt:
                logger.info("Orchestration loop interrupted")
                break
            except Exception as e:
                logger.error(f"Orchestration loop error: {e}")
                await asyncio.sleep(60)

    def get_session_summary(self) -> Dict:
        """Get summary of current session."""
        return {
            **self.session.to_dict(),
            "execution_state": asdict(get_execution_state()) if get_execution_state() else {},
        }


async def main():
    """Entry point for standalone orchestrator."""
    logging.basicConfig(level=logging.INFO)

    orchestrator = OptionsOrchestrator()
    await orchestrator.run_orchestration_loop()


if __name__ == "__main__":
    asyncio.run(main())
