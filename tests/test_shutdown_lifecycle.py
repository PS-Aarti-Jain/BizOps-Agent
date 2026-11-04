import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from run import main
import ui.app as ui_app
from agent.agent import BizOpsAgent
from mcp.router import MCPRouterManager


@pytest.mark.asyncio
async def test_web_server_expected_shutdown_cancellation_handled(monkeypatch):
    """
    Verifies that when Uvicorn completes its graceful shutdown (server.should_exit is True)
    and cancellation occurs due to signal re-emission, main() completes cleanly without error.
    """
    monkeypatch.setattr("sys.argv", ["run.py", "--web"])
    monkeypatch.setattr("run.seed_database", MagicMock())

    class MockUvicornServer:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
            self.started = True

        async def serve(self):
            # Simulate Uvicorn signal capture setting should_exit=True,
            # followed by cancellation from signal re-emission.
            self.should_exit = True
            raise asyncio.CancelledError()

    with patch("uvicorn.Server", MockUvicornServer):
        # Should not raise CancelledError
        result = await main()
        assert result is None


@pytest.mark.asyncio
async def test_web_server_unexpected_cancellation_not_swallowed(monkeypatch):
    """
    Verifies that if task cancellation happens unexpectedly while server is running
    (server.should_exit is False), CancelledError is NOT swallowed and is re-raised.
    """
    monkeypatch.setattr("sys.argv", ["run.py", "--web"])
    monkeypatch.setattr("run.seed_database", MagicMock())

    class MockUvicornServerUnexpected:
        def __init__(self, config):
            self.config = config
            self.should_exit = False
            self.started = True

        async def serve(self):
            # Cancellation occurs unexpectedly without shutdown signal (should_exit remains False)
            self.should_exit = False
            raise asyncio.CancelledError("Unexpected task abort")

    with patch("uvicorn.Server", MockUvicornServerUnexpected):
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await main()
        assert "Unexpected task abort" in str(exc_info.value)


@pytest.mark.asyncio
async def test_web_server_keyboard_interrupt_handled(monkeypatch):
    """
    Verifies that if KeyboardInterrupt occurs during server.serve(), it is handled cleanly.
    """
    monkeypatch.setattr("sys.argv", ["run.py", "--web"])
    monkeypatch.setattr("run.seed_database", MagicMock())

    class MockUvicornServerSigint:
        def __init__(self, config):
            self.config = config
            self.should_exit = True

        async def serve(self):
            raise KeyboardInterrupt()

    with patch("uvicorn.Server", MockUvicornServerSigint):
        result = await main()
        assert result is None


@pytest.mark.asyncio
async def test_fastapi_lifespan_cleans_up_agent_and_mcp_router(monkeypatch):
    """
    Verifies that FastAPI lifespan cleanup calls _agent.close() exactly once,
    which in turn closes the router and LLM clients.
    """
    mock_agent = MagicMock()
    mock_agent.initialize = AsyncMock()
    mock_agent.close = AsyncMock()
    mock_agent.router = MagicMock()
    mock_agent.router.close = AsyncMock()

    monkeypatch.setattr("ui.app.seed_database", MagicMock())
    monkeypatch.setattr("ui.app.BizOpsAgent", MagicMock(return_value=mock_agent))

    async with ui_app.lifespan(ui_app.app):
        assert ui_app._agent is not None
        mock_agent.initialize.assert_awaited_once()

    # After lifespan exit:
    mock_agent.close.assert_awaited_once()
    assert ui_app._agent_ready is False


@pytest.mark.asyncio
async def test_fastapi_lifespan_cancels_running_background_tasks(monkeypatch):
    """
    Verifies that a normal server shutdown cancels any active query tasks in _session_jobs,
    leaving no orphaned tasks running in the event loop.
    """
    mock_agent = MagicMock()
    mock_agent.initialize = AsyncMock()
    mock_agent.close = AsyncMock()

    monkeypatch.setattr("ui.app.seed_database", MagicMock())
    monkeypatch.setattr("ui.app.BizOpsAgent", MagicMock(return_value=mock_agent))

    async def long_running_dummy_job():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    dummy_task = asyncio.create_task(long_running_dummy_job())
    ui_app._session_jobs["SESS-TEST-SHUTDOWN"] = {
        "session_id": "SESS-TEST-SHUTDOWN",
        "task": dummy_task,
        "status": "PROCESSING",
    }

    try:
        async with ui_app.lifespan(ui_app.app):
            assert not dummy_task.done()

        # After lifespan exits, task must be cancelled
        assert dummy_task.done()
        assert dummy_task.cancelled() or dummy_task.exception() is None
    finally:
        ui_app._session_jobs.pop("SESS-TEST-SHUTDOWN", None)


@pytest.mark.asyncio
async def test_agent_close_invokes_router_and_llm():
    """
    Verifies that BizOpsAgent.close() awaits both router.close() and llm.close().
    """
    mock_router = MagicMock()
    mock_router.close = AsyncMock()
    mock_llm = MagicMock()
    mock_llm.close = AsyncMock()

    agent = BizOpsAgent(
        user_id="USR-TEST",
        user_role="Manager",
        router=mock_router,
        llm=mock_llm,
    )

    await agent.close()

    mock_router.close.assert_awaited_once()
    mock_llm.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_router_close_calls_exit_stack():
    """
    Verifies that MCPRouterManager.close() awaits exit_stack.aclose() with timeout.
    """
    router = MCPRouterManager()
    router.exit_stack = MagicMock()
    router.exit_stack.aclose = AsyncMock()

    await router.close()

    router.exit_stack.aclose.assert_awaited_once()


def test_runpy_main_entrypoint_keyboard_interrupt():
    """
    Verifies that executing run.py as __main__ when asyncio.run raises KeyboardInterrupt
    completes cleanly without uncaught exception propagation.
    """
    import runpy

    def fake_asyncio_run(coro):
        coro.close()
        raise KeyboardInterrupt()

    with patch("asyncio.run", side_effect=fake_asyncio_run):
        result = runpy.run_path("run.py", run_name="__main__")
        assert result is not None
