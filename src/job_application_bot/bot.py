"""Telegram transport: owner guard, fragment buffering, and the message handler.

This layer is deliberately thin — it owns nothing about scoring or the CRM. Its
sole job is to take raw Telegram updates, enforce the owner guard, reassemble the
fragments Telegram splits long pastes into, and hand the combined text to
:func:`job_application_bot.pipeline.process_job`.
"""

import asyncio
import logging

from telegram import Bot, Update
from telegram.ext import ContextTypes

from job_application_bot import pipeline
from job_application_bot.config import get_settings

logger = logging.getLogger(__name__)

# Telegram clients split any outgoing message past the 4096-char limit into
# several separate messages, so a long pasted JD arrives as multiple updates. We
# buffer incoming messages per chat and only run the pipeline once the user has
# been quiet for this many seconds, concatenating the fragments back into one
# posting. Split fragments arrive within tens of milliseconds, so this window is
# comfortably long enough while adding only minor latency to a normal send.
_BUFFER_QUIET_SECONDS = 2.5

# Per-chat accumulated message fragments, awaiting a quiet-window flush.
_buffers: dict[int, list[str]] = {}
# Per-chat pending flush task, cancelled and rescheduled on each new fragment.
_flush_tasks: dict[int, asyncio.Task] = {}


async def handle_job(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Buffer an incoming Telegram message and (re)arm the quiet-window flush.

    This is the ``MessageHandler`` entry point. It does not process the message
    directly: because Telegram splits long pastes across several messages, we
    accumulate each chat's messages and defer the pipeline to :func:`_flush`,
    which fires after :data:`_BUFFER_QUIET_SECONDS` of silence. Every new message
    cancels the previously scheduled flush and schedules a fresh one, so a burst
    of fragments is processed exactly once on the concatenated text.

    The owner guard runs here (before buffering) so messages from other chats are
    never accumulated or processed.

    Args:
        update: Incoming Telegram update.
        context: Handler context (provides ``context.bot``).
    """
    s = get_settings()

    # Only the configured owner may use this bot.
    if str(update.effective_chat.id) != s.chat_id:
        logger.warning(
            "Ignoring message from unknown chat %s", update.effective_chat.id
        )
        return

    chat_id = update.effective_chat.id
    _buffers.setdefault(chat_id, []).append(update.message.text or "")

    # Cancel any pending flush for this chat and re-arm — the quiet window restarts
    # on every fragment so we only flush once the burst has settled.
    pending = _flush_tasks.get(chat_id)
    if pending is not None:
        pending.cancel()
    _flush_tasks[chat_id] = asyncio.create_task(_flush(chat_id, context.bot))


async def _flush(chat_id: int, bot: Bot) -> None:
    """Wait out the quiet window, then process the chat's buffered fragments.

    Sleeps :data:`_BUFFER_QUIET_SECONDS`; if a newer message arrives first this
    task is cancelled (the sleep raises ``CancelledError``) and the buffer is left
    intact for the rescheduled flush. Only the flush that actually fires drains
    the buffer and runs the pipeline on the joined text.

    Args:
        chat_id: The chat whose buffered fragments to process.
        bot: The Telegram Bot instance used to reply.
    """
    await asyncio.sleep(_BUFFER_QUIET_SECONDS)
    chunks = _buffers.pop(chat_id, [])
    _flush_tasks.pop(chat_id, None)
    combined = "\n".join(chunks).strip()
    if combined:
        await pipeline.process_job(combined, chat_id, bot)
