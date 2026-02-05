"""
Agent definitions and orchestration for AI-Native Options Flow Trading.
"""
from .definitions import (
    ORCHESTRATOR_PROMPT,
    FLOW_SCANNER_PROMPT,
    POSITION_MANAGER_PROMPT,
    RISK_MANAGER_PROMPT,
    EXECUTOR_PROMPT,
)
from .orchestrator import OptionsOrchestrator
from .hooks import PreToolUseHook, PostToolUseHook, SafetyGateHook

__all__ = [
    "ORCHESTRATOR_PROMPT",
    "FLOW_SCANNER_PROMPT",
    "POSITION_MANAGER_PROMPT",
    "RISK_MANAGER_PROMPT",
    "EXECUTOR_PROMPT",
    "OptionsOrchestrator",
    "PreToolUseHook",
    "PostToolUseHook",
    "SafetyGateHook",
]
