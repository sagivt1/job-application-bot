"""Pipeline orchestration: turn one message's text into a logged, scored job.

This is the business core of the bot, deliberately kept independent of the
Telegram transport layer (buffering, owner guard) in :mod:`job_application_bot.bot`. It
exposes a single entry point, :func:`process_job`, which the transport calls once
a chat's fragments have settled into one combined message.
"""

import asyncio
import html
import logging

from telegram import Bot

from job_application_bot.ai import brain
from job_application_bot.config import get_settings
from job_application_bot.integrations import crm, intake, notify

logger = logging.getLogger(__name__)

# Number of jobs currently in the pipeline. Mutated only on the event loop thread
# (never inside the to_thread workers), so a plain int needs no lock. Logged on
# each start/finish so slow providers — e.g. the free NVIDIA models, which can
# take many seconds per analysis — make it obvious how many jobs overlap.
_active_jobs = 0


async def process_job(text: str, chat_id: int, bot: Bot) -> None:
    """Route the combined message text through the full pipeline.

    Wraps :func:`_run_pipeline` with two guards so the handler (and the bot) can
    never be taken down or wedged by a single message:

    * a wall-clock timeout (``JOB_TIMEOUT_SECONDS``, default 300s) — a wedged
      provider (a slow local/free model, a stalled fetch, a hung Airtable call)
      must never pin an ``_active_jobs`` slot indefinitely. If the pipeline
      overruns the cap, the job is abandoned and the user is asked to retry; the
      orphaned worker thread is left to finish on its own and its result
      discarded, and
    * a broad ``except`` — any other error (API failure, junk posting) is logged
      and answered with a short, friendly reply.

    Either way the ``finally`` restores the in-flight gauge, so a stuck or failing
    job always frees its slot.

    Args:
        text: The combined (de-fragmented) message text to process.
        chat_id: The originating chat, used as the reply destination.
        bot: The Telegram Bot instance used to reply.
    """
    global _active_jobs
    timeout = get_settings().job_timeout_seconds
    _active_jobs += 1
    logger.info("Job started for chat %s — %d in progress", chat_id, _active_jobs)
    try:
        await asyncio.wait_for(_run_pipeline(text, chat_id, bot), timeout=timeout)
    except TimeoutError:
        # The pipeline outran its wall-clock cap. wait_for has already cancelled
        # the inner coroutine; the underlying worker thread keeps running but its
        # result is discarded. Tell the user rather than leaving them hanging.
        logger.warning(
            "process_job timed out after %ss for chat %s",
            timeout,
            chat_id,
        )
        await bot.send_message(
            chat_id,
            "That job took too long, so I gave up on it — please try again.",
        )
    except Exception:
        # Never let one bad message take the handler (or bot) down. Log with
        # context and reply a short, friendly error.
        logger.exception("process_job failed for chat %s", chat_id)
        await bot.send_message(
            chat_id, "Something went wrong processing that — please try again."
        )
    finally:
        _active_jobs -= 1
        logger.info("Job finished for chat %s — %d in progress", chat_id, _active_jobs)


async def _run_pipeline(text: str, chat_id: int, bot: Bot) -> None:
    """Run the intake → dedupe → analyze → log → alert sequence for one message.

    Steps:
    1. Intake: extract clean job text from the message (URL fetch or raw paste).
    2. Dedupe: skip URLs already in the CRM.
    3. Analyze: call Gemini to extract fields and score the job.
    4. Reject: if the text is not a job posting, reply and stop — nothing is
       written to the CRM.
    5. Log: create an Airtable record with Status=New.
    6. Alert: if score >= threshold, generate a cover letter, update the record
       (CoverLetter + Status=Alerted), and send a Telegram alert.
    7. Confirm: reply with the analysis summary.

    Every blocking step (intake fetch, Gemini, Airtable) is offloaded to a worker
    thread via :func:`asyncio.to_thread` so the bot's event loop stays responsive
    while one job is processing. Error handling and the wall-clock timeout live in
    the caller, :func:`process_job`.

    Args:
        text: The combined (de-fragmented) message text to process.
        chat_id: The originating chat, used as the reply destination.
        bot: The Telegram Bot instance used to reply.
    """
    result = await asyncio.to_thread(intake.extract_job_text, text)

    if result.status == intake.IntakeStatus.NEEDS_LINK:
        await bot.send_message(
            chat_id,
            "Send a job URL — or paste the URL followed by the full job description.",
        )
        return

    if result.status == intake.IntakeStatus.NEEDS_PASTE:
        await bot.send_message(
            chat_id,
            "Couldn't extract text from that URL. Paste the full job description.",
        )
        return

    # Deduplicate by URL. Raw-text pastes (no link) can't be deduped — each
    # paste creates a new row, which is expected behaviour.
    if result.link:
        existing = await asyncio.to_thread(crm.find_by_link, result.link)
        if existing:
            f = existing["fields"]
            await bot.send_message(
                chat_id,
                f"Already logged: <b>{html.escape(f.get('Company', '?'))}</b> — "
                f"{html.escape(f.get('Role', '?'))} "
                f"(score {f.get('MatchScore', '?')}).",
                parse_mode="HTML",
            )
            return

    analysis = await asyncio.to_thread(brain.analyze, result.text)

    # Non-postings (homepages, articles, login walls) are never written to the
    # CRM — reply and stop before any Airtable write or cover letter.
    if not analysis.get("is_job_posting", True):
        logger.info("Skipping non-job content for chat %s", chat_id)
        await bot.send_message(
            chat_id,
            "That doesn't look like a job posting — nothing was logged.",
        )
        return

    record = await asyncio.to_thread(crm.create, analysis, result.link)

    if analysis["match_score"] >= get_settings().alert_threshold:
        cover = await asyncio.to_thread(brain.write_cover_letter, result.text, analysis)
        await asyncio.to_thread(
            crm.update,
            record["id"],
            {"CoverLetter": cover, "Status": crm.STATUS_ALERTED},
        )
        await notify.send_alert(bot, chat_id, analysis, cover)
    else:
        # Sub-threshold jobs don't alert, so send a plain confirmation summary.
        # Alerted jobs already get their summary from notify.send_alert above —
        # sending one here too would double-message the user.
        summary = (
            f"<b>{html.escape(analysis['company'])} — "
            f"{html.escape(analysis['role'])}</b>\n"
            f"Score: {analysis['match_score']}/100\n"
            f"{html.escape(analysis['rationale'])}"
        )
        await bot.send_message(chat_id, summary, parse_mode="HTML")
