import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.agent import GaladrielAgent, _is_request_too_large_error

def test_is_request_too_large_error():
    assert _is_request_too_large_error(Exception("413 Request Entity Too Large"))
    assert _is_request_too_large_error(RuntimeError("Anthropic error: Error code: 413 - {'error': {'type': 'request_too_large'}}"))
    assert _is_request_too_large_error(Exception("Request exceeds the maximum size"))
    assert not _is_request_too_large_error(Exception("Rate limit exceeded"))

def test_agent_recovers_from_413_by_trimming():
    async def _run():
        agent = GaladrielAgent.__new__(GaladrielAgent)
        agent.conversations = {}
        agent.journal = MagicMock()
        agent.memory = MagicMock()
        agent.memory.build_system_blocks.return_value = []
        agent.memory.config_dir = MagicMock()
        agent.model = "test-model"
        agent.max_tokens = 1000
        agent.tools = []
        agent.thinking_budget = 0
        agent.context_warning_callback = None
        agent.context_window = 200000
        agent.history_token_budget = 150000
        agent.history_max_messages = 1000
        agent._output_ceiling_streak = {}
        agent._post_recovery_archive_tag = {}
        agent._thinking_param = lambda: None
        agent._log_usage = lambda resp: None
        agent._maybe_warn_context = AsyncMock()
        agent._maybe_warn_output_ceiling = AsyncMock()

        fake_response = MagicMock()
        fake_response.stop_reason = "end_turn"
        fake_block = MagicMock()
        fake_block.model_dump = lambda **kw: {"type": "text", "text": "Recovered response"}
        fake_response.content = [fake_block]

        agent.provider = MagicMock()
        agent.provider.complete = AsyncMock(side_effect=[
            RuntimeError("Anthropic error: Error code: 413 - {'error': {'type': 'request_too_large'}}"),
            fake_response
        ])

        channel = "test_chan"
        agent.conversations[channel] = [{"role": "user", "content": f"msg {i}"} for i in range(60)]

        # Call whatever the respond method is named (_respond_inner on body, _respond_unlocked on galadriel)
        respond_fn = getattr(agent, "_respond_inner", getattr(agent, "_respond_unlocked", None))
        res = await respond_fn("latest question", channel_id=channel)
        assert res == "Recovered response"
        assert agent.provider.complete.call_count == 2
        assert len(agent.conversations[channel]) <= 42

    asyncio.run(_run())
