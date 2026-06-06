"""Unit tests for the Telegram transport in ``job_application_bot.bot``.

:func:`bot.handle_job` is the thin entry point: it enforces the owner guard and
buffers the fragments Telegram splits long pastes into, flushing them as one job
after a quiet window. The pipeline core it ultimately calls
(:func:`pipeline.process_job`) is mocked here so these tests focus solely on
buffering and the guard.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_application_bot import bot, pipeline

_OWNER_CHAT = "123"


@pytest.fixture(autouse=True)
def owner_settings(monkeypatch):
    """Point the owner guard at a known chat id and clear buffers per test."""
    settings = SimpleNamespace(chat_id=_OWNER_CHAT)
    monkeypatch.setattr(bot, "get_settings", lambda: settings)

    bot._buffers.clear()
    bot._flush_tasks.clear()


def _make_bot():
    """Build a mock Telegram Bot with an async ``send_message``."""
    telegram_bot = MagicMock(name="bot")
    telegram_bot.send_message = AsyncMock(name="send_message")
    return telegram_bot


def _make_update(text: str = "anything", chat_id: str = _OWNER_CHAT):
    """Build a mock Telegram Update carrying the given text and chat id."""
    update = MagicMock(name="update")
    update.effective_chat.id = chat_id
    update.message.text = text
    return update


def _make_context(telegram_bot=None):
    """Build a mock handler context exposing ``context.bot``."""
    context = MagicMock(name="context")
    context.bot = telegram_bot or _make_bot()
    return context


async def test_ignores_non_owner_chat():
    update = _make_update(chat_id="999")

    await bot.handle_job(update, _make_context())

    assert not bot._buffers
    assert not bot._flush_tasks


async def test_fragmented_messages_are_joined_into_one_job(monkeypatch):
    # Flush almost immediately and intercept the pipeline core so we only assert
    # on how fragments are buffered + concatenated.
    monkeypatch.setattr(bot, "_BUFFER_QUIET_SECONDS", 0)
    process = AsyncMock(name="process_job")
    monkeypatch.setattr(pipeline, "process_job", process)

    context = _make_context()
    await bot.handle_job(_make_update(text="part one"), context)
    await bot.handle_job(_make_update(text="part two"), context)

    # The latest scheduled flush is the one that fires; await it.
    await bot._flush_tasks[_OWNER_CHAT]

    process.assert_awaited_once_with("part one\npart two", _OWNER_CHAT, context.bot)
