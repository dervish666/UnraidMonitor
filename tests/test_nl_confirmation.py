"""NL action confirmations through a real NLProcessor and tool executor.

The old buttons carried ``nl_confirm:<action>:<container>``, so any button
the client could send ran that action, a second tap ran it again, and the
model's prose could say "restart plex" over a button that stopped it.
"""

from unittest.mock import AsyncMock, MagicMock

from src.bot.nl_handler import (
    create_nl_cancel_callback,
    create_nl_confirm_callback,
    create_nl_handler,
)
from src.models import ContainerInfo
from src.services import nl_processor as nlp
from src.services.llm.provider import LLMResponse, ToolCall
from src.services.nl_processor import NLProcessor
from src.services.nl_tools import NLToolExecutor
from src.state import ContainerStateManager

CHAT = 555


def _state(*names: str) -> ContainerStateManager:
    state = ContainerStateManager()
    for n in names:
        state.update(ContainerInfo(name=n, status="running", health=None, image="x", started_at=None))
    return state


def _provider(*responses: LLMResponse) -> MagicMock:
    provider = MagicMock()
    provider.supports_tools = True
    provider.chat = AsyncMock(side_effect=list(responses))
    return provider


def _tool_turn(*calls: tuple[str, dict], text: str = "") -> LLMResponse:
    return LLMResponse(
        text=text, stop_reason="tool_use",
        tool_calls=[ToolCall(id=f"t{i}", name=n, input=a) for i, (n, a) in enumerate(calls)],
    )


def _end(text: str) -> LLMResponse:
    return LLMResponse(text=text, stop_reason="end", tool_calls=None)


def _processor(*responses: LLMResponse) -> NLProcessor:
    executor = NLToolExecutor(_state("vaultwarden", "plex", "radarr"), MagicMock())
    return NLProcessor(provider=_provider(*responses), tool_executor=executor)


class Chat:
    """Sends messages and remembers what came back, like a Telegram chat."""

    def __init__(self) -> None:
        self.sent: list[MagicMock] = []
        self._next_id = 100

    def incoming(self, text: str, user_id: int = 1) -> MagicMock:
        message = MagicMock()
        message.text = text
        message.from_user = MagicMock(id=user_id)
        message.answer = AsyncMock(side_effect=self._answer)
        return message

    async def _answer(self, text: str, **kwargs):
        self._next_id += 1
        sent = MagicMock()
        sent.text = text
        sent.reply_markup = kwargs.get("reply_markup")
        sent.chat = MagicMock(id=CHAT)
        sent.message_id = self._next_id
        sent.edit_text = AsyncMock()
        self.sent.append(sent)
        return sent

    def tap(self, sent: MagicMock, column: int, user_id: int = 1) -> MagicMock:
        button = sent.reply_markup.inline_keyboard[0][column]
        return self.tap_data(button.callback_data, sent, user_id)

    @staticmethod
    def tap_data(data: str, sent: MagicMock, user_id: int = 1) -> MagicMock:
        callback = MagicMock()
        callback.data = data
        callback.from_user = MagicMock(id=user_id)
        callback.message = sent
        callback.answer = AsyncMock()
        return callback


def _controller() -> MagicMock:
    controller = MagicMock()
    controller.is_protected.return_value = False
    for verb in ("restart", "stop", "start", "pull_and_recreate"):
        setattr(controller, verb, AsyncMock(return_value=f"done: {verb}"))
    return controller


async def _ask(processor: NLProcessor, chat: Chat, text: str) -> MagicMock:
    await create_nl_handler(processor)(chat.incoming(text))
    return chat.sent[-1]


async def test_button_and_bot_line_name_the_real_action_whatever_the_model_says():
    processor = _processor(
        _tool_turn(("stop_container", {"name": "vault"})),
        _end("Sure, I'll **restart** plex for you."),  # the model's prose lies
    )
    chat = Chat()
    sent = await _ask(processor, chat, "stop vaultwarden")

    confirm, cancel = sent.reply_markup.inline_keyboard[0]
    assert confirm.text == "✅ Stop vaultwarden"
    assert cancel.text == "❌ Cancel"
    assert "<b>Confirm:</b> stop <code>vaultwarden</code>" in sent.text
    # The button carries a token, not the action or the container.
    assert "vaultwarden" not in confirm.callback_data and "stop" not in confirm.callback_data
    assert len(confirm.callback_data.encode()) <= 64


async def test_confirm_runs_once_and_a_second_tap_does_nothing():
    processor = _processor(_tool_turn(("stop_container", {"name": "vaultwarden"})), _end("ok"))
    chat, controller = Chat(), _controller()
    sent = await _ask(processor, chat, "stop vaultwarden")
    confirm = create_nl_confirm_callback(processor, controller)

    first = chat.tap(sent, 0)
    await confirm(first)
    second = chat.tap(sent, 0)
    await confirm(second)

    controller.stop.assert_awaited_once_with("vaultwarden")
    assert "expired or was already used" in second.answer.call_args.args[0]


async def test_cancel_then_confirm_does_nothing():
    processor = _processor(_tool_turn(("restart_container", {"name": "plex"})), _end("ok"))
    chat, controller = Chat(), _controller()
    sent = await _ask(processor, chat, "restart plex")

    await create_nl_cancel_callback(processor)(chat.tap(sent, 1))
    await create_nl_confirm_callback(processor, controller)(chat.tap(sent, 0))

    controller.restart.assert_not_awaited()


async def test_a_newer_message_retires_the_old_buttons():
    processor = _processor(
        _tool_turn(("restart_container", {"name": "plex"})), _end("ok"),
        _end("All quiet."),
    )
    chat, controller = Chat(), _controller()
    old = await _ask(processor, chat, "restart plex")
    await _ask(processor, chat, "how's the server?")

    await create_nl_confirm_callback(processor, controller)(chat.tap(old, 0))

    controller.restart.assert_not_awaited()


async def test_forged_old_style_callback_data_does_nothing():
    processor = _processor(_tool_turn(("restart_container", {"name": "plex"})), _end("ok"))
    chat, controller = Chat(), _controller()
    sent = await _ask(processor, chat, "restart plex")

    forged = Chat.tap_data("nl_confirm:stop:vaultwarden", sent)
    await create_nl_confirm_callback(processor, controller)(forged)

    controller.stop.assert_not_awaited()
    controller.restart.assert_not_awaited()


async def test_token_from_another_user_or_message_does_nothing():
    processor = _processor(_tool_turn(("restart_container", {"name": "plex"})), _end("ok"))
    chat, controller = Chat(), _controller()
    sent = await _ask(processor, chat, "restart plex")
    confirm = create_nl_confirm_callback(processor, controller)
    data = sent.reply_markup.inline_keyboard[0][0].callback_data

    await confirm(Chat.tap_data(data, sent, user_id=2))
    controller.restart.assert_not_awaited()

    other_message = MagicMock(chat=MagicMock(id=CHAT), message_id=9999)
    await confirm(Chat.tap_data(data, other_message))
    controller.restart.assert_not_awaited()


async def test_stale_button_does_nothing(monkeypatch):
    processor = _processor(_tool_turn(("restart_container", {"name": "plex"})), _end("ok"))
    chat, controller = Chat(), _controller()
    sent = await _ask(processor, chat, "restart plex")

    monkeypatch.setattr(nlp, "NL_ACTION_TTL_SECONDS", -1)
    await create_nl_confirm_callback(processor, controller)(chat.tap(sent, 0))

    controller.restart.assert_not_awaited()


async def test_second_action_in_one_turn_is_refused_to_the_model():
    provider = _provider(
        _tool_turn(("restart_container", {"name": "plex"}), ("stop_container", {"name": "radarr"})),
        _end("ok"),
    )
    executor = NLToolExecutor(_state("plex", "radarr"), MagicMock())
    processor = NLProcessor(provider=provider, tool_executor=executor)

    result = await processor.process(user_id=1, message="restart plex and stop radarr")

    assert result.pending_action is not None
    assert (result.pending_action.action, result.pending_action.container) == ("restart", "plex")
    tool_results = [m for m in provider.chat.call_args.kwargs["messages"] if m["role"] == "tool_result"]
    assert "Refused" in tool_results[1]["content"]
    assert "Nothing has happened yet" in tool_results[0]["content"]


# -- tool argument validation ----------------------------------------------------


async def test_malformed_tool_args_come_back_as_tool_errors():
    executor = NLToolExecutor(_state("plex"), MagicMock())

    assert (await executor.execute("get_container_status", "plex")).startswith("Tool error:")
    assert (await executor.execute("get_container_status", {"name": 7})).startswith("Tool error:")
    assert (await executor.execute("get_container_status", {})).startswith("Tool error:")
    assert (await executor.execute("get_container_logs", {"name": "plex", "lines": "lots"})).startswith(
        "Tool error:"
    )
    assert (await executor.execute("no_such_tool", {})).startswith("Tool error:")


async def test_integer_args_are_coerced():
    docker_client = MagicMock()
    docker_client.containers.get.return_value.logs.return_value = b"hello\n"
    executor = NLToolExecutor(_state("plex"), docker_client)

    result = await executor.execute("get_container_logs", {"name": "plex", "lines": "20"})

    assert "hello" in result
    assert docker_client.containers.get.return_value.logs.call_args.kwargs["tail"] == 20


async def test_a_tool_that_raises_becomes_a_tool_error_not_a_dead_reply():
    state = _state("plex")
    state.find_by_name = MagicMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    provider = _provider(_tool_turn(("get_container_status", {"name": "plex"})), _end("Sorry."))
    processor = NLProcessor(provider=provider, tool_executor=NLToolExecutor(state, MagicMock()))

    result = await processor.process(user_id=1, message="how's plex")

    assert result.response == "Sorry."
    tool_result = [m for m in provider.chat.call_args.kwargs["messages"] if m["role"] == "tool_result"][0]
    assert tool_result["content"].startswith("Tool error:")
