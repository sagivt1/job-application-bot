"""Telegram alert formatting and sending."""

import html
import logging

from telegram import Bot

logger = logging.getLogger(__name__)

# Leave a buffer below Telegram's 4096-char limit to absorb HTML-escaping overhead.
_MAX_MSG = 4000


async def send_alert(
    bot: Bot, chat_id: int | str, analysis: dict, cover_letter: str
) -> None:
    """Send a match-score summary followed by the cover letter via Telegram.

    The summary and cover letter are sent as separate messages. Long cover
    letters are chunked into multiple messages so no single message exceeds
    Telegram's character limit.

    All dynamic text is HTML-escaped; messages use HTML parse mode.

    Args:
        bot: The Telegram Bot instance (available as ``context.bot`` in handlers).
        chat_id: Destination chat ID.
        analysis: Dict returned by ``brain.analyze()`` — uses ``match_score``,
            ``company``, ``role``, and ``rationale``.
        cover_letter: Plain-text cover letter from ``brain.write_cover_letter()``.
    """
    summary = (
        f"<b>Match: {analysis['match_score']}/100</b>  "
        f"{html.escape(analysis['company'])} — {html.escape(analysis['role'])}\n"
        f"{html.escape(analysis['rationale'])}"
    )
    logger.info(
        "Telegram alert: %s @ %s score=%s",
        analysis.get("role"),
        analysis.get("company"),
        analysis.get("match_score"),
    )
    await bot.send_message(chat_id, summary, parse_mode="HTML")

    for i in range(0, len(cover_letter), _MAX_MSG):
        chunk = html.escape(cover_letter[i : i + _MAX_MSG])
        await bot.send_message(chat_id, chunk, parse_mode="HTML")
