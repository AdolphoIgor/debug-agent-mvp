from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock, patch

import pytest
from langgraph.graph.state import CompiledStateGraph

import src.studio_entrypoint as se
from src.studio_entrypoint import (
    StudioGraphCompilationError,
    handle_fatal_exception,
    load_orchestrator_graph,
)


def test_load_orchestrator_graph_success() -> None:
    with patch("src.studio_entrypoint.build_orchestrator_graph") as mock_build:
        mock_graph = MagicMock(spec=CompiledStateGraph)
        mock_build.return_value = mock_graph

        compiled = load_orchestrator_graph()

        assert compiled is mock_graph
        mock_build.assert_called_once()


def test_load_orchestrator_graph_raises_compilation_error() -> None:
    with patch(
        "src.studio_entrypoint.build_orchestrator_graph",
        side_effect=ValueError("Graph cyclic error"),
    ):
        with pytest.raises(
            StudioGraphCompilationError,
            match="Orchestrator graph compilation aborted: Graph cyclic error",
        ):
            load_orchestrator_graph()


def test_handle_fatal_exception_writes_stderr_and_exits() -> None:
    captured_stderr = io.StringIO()
    test_exception = RuntimeError("Fatal hardware initialization failed")

    with patch.object(sys, "stderr", captured_stderr), pytest.raises(SystemExit) as exit_ctx:
        handle_fatal_exception(test_exception)

    assert exit_ctx.value.code == 1
    assert (
        "[FATAL] Studio graph failed to load: RuntimeError: Fatal hardware initialization failed"
        in captured_stderr.getvalue()
    )


def test_src_path_in_sys_path() -> None:
    assert se.SRC_PATH in sys.path
