from __future__ import annotations

import logging
import sys
import warnings
from pathlib import Path
from typing import NoReturn

# Suppression of third-party framework reflection warnings
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    message=r".*<built-in function any> is not a Python type.*",
)
warnings.filterwarnings(
    "ignore",
    message=r".*allowed_objects.*",
)

# Deterministic resolution of sibling modules within the src directory
CURRENT_DIR: Path = Path(__file__).resolve().parent
SRC_PATH: str = str(CURRENT_DIR)
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from langgraph.graph.state import CompiledStateGraph  # noqa: E402

from orchestrator_graph import build_orchestrator_graph  # noqa: E402

logger = logging.getLogger("mvp.studio")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)


class StudioGraphCompilationError(Exception):
    """Raised when the orchestrator workflow fails compilation for studio execution."""

    pass


def load_orchestrator_graph() -> CompiledStateGraph:
    """Instantiates and compiles the TARGET_PROJECT orchestrator graph.

    Returns:
        CompiledStateGraph: The compiled state graph ready for studio visualization.

    Raises:
        StudioGraphCompilationError: If compilation or node resolution fails.
    """
    try:
        logger.info("Instantiating StateGraph builder for DEBUG-AGENT-MVP...")
        compiled_graph: CompiledStateGraph = build_orchestrator_graph()
        logger.info("StateGraph successfully compiled for LangGraph Studio.")
        return compiled_graph
    except Exception as exc:
        error_message = f"Orchestrator graph compilation aborted: {str(exc)}"
        logger.critical(error_message, exc_info=True)
        raise StudioGraphCompilationError(error_message) from exc


def handle_fatal_exception(exc: BaseException) -> NoReturn:
    """Transmits fatal compilation diagnostics to standard error and halts execution."""
    sys.stderr.write(f"[FATAL] Studio graph failed to load: {type(exc).__name__}: {str(exc)}\n")
    sys.stderr.flush()
    sys.exit(1)


try:
    graph: CompiledStateGraph = load_orchestrator_graph()
except StudioGraphCompilationError as err:
    handle_fatal_exception(err)
