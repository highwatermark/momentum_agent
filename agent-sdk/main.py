#!/usr/bin/env python3
"""
AI-Native Options Flow Trading System - Entry Point

This is the main entry point for running the Claude Agent SDK-based
options flow trading system.

Usage:
    python main.py                  # Run in live mode
    python main.py --shadow         # Run in shadow mode (log only, no execution)
    python main.py --paper          # Run in paper trading mode
    python main.py --single-cycle   # Run single cycle and exit
"""
import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

import pytz

# Add current directory first (for local config), then parent (for existing modules)
_current_dir = str(Path(__file__).parent)
_parent_dir = str(Path(__file__).parent.parent)
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)
if _parent_dir not in sys.path:
    sys.path.append(_parent_dir)  # Append parent, not insert - local takes priority

from agent_config import config, Config
from agents import OptionsOrchestrator

ET = pytz.timezone("America/New_York")

# Setup logging
def setup_logging(log_level: str = "INFO", log_dir: str = "logs"):
    """Configure logging for the application."""
    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    log_file = log_path / f"agent_{datetime.now(ET).strftime('%Y%m%d')}.log"

    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level.upper()))
    console_handler.setFormatter(console_formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)

    return logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="AI-Native Options Flow Trading System"
    )
    parser.add_argument(
        "--shadow",
        action="store_true",
        help="Shadow mode: log decisions without executing trades"
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Paper trading mode: use paper trading API"
    )
    parser.add_argument(
        "--single-cycle",
        action="store_true",
        help="Run single cycle and exit (useful for testing)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=60,
        help="Seconds between flow scans (default: 60)"
    )
    return parser.parse_args()


async def run_single_cycle(orchestrator: OptionsOrchestrator, logger):
    """Run a single orchestration cycle."""
    logger.info("Running single cycle...")

    # Run flow scan
    scan_result = await orchestrator.run_scan_cycle()
    if scan_result:
        logger.info(f"Scan result: {scan_result.get('action', 'unknown')}")

    # Run position check
    position_result = await orchestrator.run_position_check()
    if position_result:
        logger.info(f"Position check: {position_result.get('action', 'unknown')}")

    # Print session summary
    summary = orchestrator.get_session_summary()
    logger.info(f"Session summary: {summary}")

    return summary


async def run_continuous(orchestrator: OptionsOrchestrator, logger):
    """Run continuous orchestration loop."""
    logger.info("Starting continuous orchestration...")
    logger.info(f"Session ID: {orchestrator.session.session_id}")
    logger.info(f"Shadow mode: {config.shadow_mode}")
    logger.info(f"Paper trading: {config.paper_trading}")

    # Setup graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Run until shutdown
        while not shutdown_event.is_set():
            try:
                # Run scan cycle
                await orchestrator.run_scan_cycle()

                # Check positions
                await orchestrator.run_position_check()

                # Wait for next cycle or shutdown
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=orchestrator.session.scan_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue loop

            except Exception as e:
                logger.error(f"Cycle error: {e}")
                await asyncio.sleep(60)

    finally:
        # Cleanup
        logger.info("Shutting down orchestrator...")
        summary = orchestrator.get_session_summary()
        logger.info(f"Final session summary: {summary}")

        # Send shutdown notification
        try:
            from tools.telegram_mcp import send_notification
            send_notification(
                message=f"🔴 Agent SDK shutdown\n\nSession: {orchestrator.session.session_id}\nSignals seen: {len(orchestrator.session.signals_seen_today)}\nTrades: {len(orchestrator.session.trades_today)}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not send shutdown notification: {e}")


def validate_config(logger) -> bool:
    """Validate configuration before starting."""
    errors = config.validate()

    if errors:
        for error in errors:
            logger.error(f"Config error: {error}")
        return False

    return True


def main():
    """Main entry point."""
    args = parse_args()

    # Setup logging
    logger = setup_logging(args.log_level, config.log_dir)
    logger.info("=" * 60)
    logger.info("AI-Native Options Flow Trading System")
    logger.info("=" * 60)

    # Apply CLI arguments to config
    if args.shadow:
        config.shadow_mode = True
        logger.info("Shadow mode ENABLED - no trades will be executed")

    if args.paper:
        config.paper_trading = True
        logger.info("Paper trading mode ENABLED")

    # Validate configuration
    if not validate_config(logger):
        logger.error("Configuration validation failed, exiting")
        sys.exit(1)

    # Create orchestrator
    orchestrator = OptionsOrchestrator(config)

    # Apply scan interval from args
    if args.scan_interval:
        orchestrator.session.scan_interval_seconds = args.scan_interval
        logger.info(f"Scan interval set to {args.scan_interval} seconds")

    # Send startup notification
    try:
        from tools.telegram_mcp import send_notification
        mode = "SHADOW" if config.shadow_mode else ("PAPER" if config.paper_trading else "LIVE")
        send_notification(
            message=f"🟢 Agent SDK started\n\nMode: {mode}\nSession: {orchestrator.session.session_id}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.warning(f"Could not send startup notification: {e}")

    # Run
    if args.single_cycle:
        # Single cycle mode
        result = asyncio.run(run_single_cycle(orchestrator, logger))
        print(f"\nResult: {result}")
    else:
        # Continuous mode
        asyncio.run(run_continuous(orchestrator, logger))

    logger.info("Agent SDK exited")


if __name__ == "__main__":
    main()
