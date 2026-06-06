"""Unit tests for the pipeline core in ``job_application_bot.pipeline``.

:func:`pipeline.process_job` is the pipeline core: intake outcomes, dedupe, the
non-job gate, the score >= threshold alert branch, the broad error guard that
keeps the bot alive on a bad message, and the in-flight concurrency gauge. All
collaborators (intake, crm, brain, notify) are mocked, so no network, Telegram,
Gemini, or Airtable calls occur. Replies go through ``bot.send_message``.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from job_application_bot import pipeline
from job_application_bot.ai import brain
from job_application_bot.config import get_settings
from job_application_bot.integrations import crm, intake, notify
from job_application_bot.integrations.intake import IntakeResult, IntakeStatus

_OWNER_CHAT = "123"
_URL = "https://example.com/jobs/123"
# The pipeline alerts at or above this score; tests track the same configured
# value so the boundary stays in sync with config.Settings.alert_threshold.
_THRESHOLD = get_settings().alert_threshold


@pytest.fixture(autouse=True)
def mock_collaborators(monkeypatch):
    """Replace every external collaborator with a mock for each test.

    Returns a namespace of the individual mocks so tests can set return values
    and assert call patterns. Also resets the in-flight gauge so state never
    leaks between tests.
    """
    pipeline._active_jobs = 0

    extract_job_text = MagicMock(name="extract_job_text")
    find_by_link = MagicMock(name="find_by_link", return_value=None)
    create = MagicMock(name="create", return_value={"id": "rec1", "fields": {}})
    update_record = MagicMock(name="update")
    analyze = MagicMock(name="analyze")
    write_cover_letter = MagicMock(name="write_cover_letter", return_value="Dear...")
    send_alert = AsyncMock(name="send_alert")

    monkeypatch.setattr(intake, "extract_job_text", extract_job_text)
    monkeypatch.setattr(crm, "find_by_link", find_by_link)
    monkeypatch.setattr(crm, "create", create)
    monkeypatch.setattr(crm, "update", update_record)
    monkeypatch.setattr(brain, "analyze", analyze)
    monkeypatch.setattr(brain, "write_cover_letter", write_cover_letter)
    monkeypatch.setattr(notify, "send_alert", send_alert)

    return SimpleNamespace(
        extract_job_text=extract_job_text,
        find_by_link=find_by_link,
        create=create,
        update=update_record,
        analyze=analyze,
        write_cover_letter=write_cover_letter,
        send_alert=send_alert,
    )


def _make_bot():
    """Build a mock Telegram Bot with an async ``send_message``."""
    bot = MagicMock(name="bot")
    bot.send_message = AsyncMock(name="send_message")
    return bot


def _analysis(score: int, is_job_posting: bool = True) -> dict:
    """Return a minimal analysis dict with the given score and job flag."""
    return {
        "is_job_posting": is_job_posting,
        "company": "Acme",
        "role": "Backend Engineer",
        "technologies": ["Python", "FastAPI"],
        "years_required": 3,
        "match_score": score,
        "rationale": "Strong backend fit; gap in Kubernetes.",
    }


# --- process_job: pipeline core -------------------------------------------


async def test_needs_paste_replies_and_stops(mock_collaborators):
    mock_collaborators.extract_job_text.return_value = IntakeResult(
        IntakeStatus.NEEDS_PASTE, text=None, link=_URL
    )
    bot = _make_bot()

    await pipeline.process_job("job text", _OWNER_CHAT, bot)

    bot.send_message.assert_awaited_once()
    mock_collaborators.find_by_link.assert_not_called()
    mock_collaborators.analyze.assert_not_called()


async def test_needs_link_replies_and_stops(mock_collaborators):
    mock_collaborators.extract_job_text.return_value = IntakeResult(
        IntakeStatus.NEEDS_LINK, text=None, link=None
    )
    bot = _make_bot()

    await pipeline.process_job("job text", _OWNER_CHAT, bot)

    bot.send_message.assert_awaited_once()
    mock_collaborators.analyze.assert_not_called()


async def test_duplicate_link_replies_and_skips_analysis(mock_collaborators):
    mock_collaborators.extract_job_text.return_value = IntakeResult(
        IntakeStatus.OK, text="job text", link=_URL
    )
    mock_collaborators.find_by_link.return_value = {
        "id": "rec0",
        "fields": {"Company": "Acme", "Role": "Backend Engineer", "MatchScore": 90},
    }
    bot = _make_bot()

    await pipeline.process_job("job text", _OWNER_CHAT, bot)

    bot.send_message.assert_awaited_once()
    assert "Already logged" in bot.send_message.await_args[0][1]
    mock_collaborators.analyze.assert_not_called()
    mock_collaborators.create.assert_not_called()


async def test_non_job_is_not_logged(mock_collaborators):
    mock_collaborators.extract_job_text.return_value = IntakeResult(
        IntakeStatus.OK, text="company homepage text", link=_URL
    )
    mock_collaborators.analyze.return_value = _analysis(1, is_job_posting=False)
    bot = _make_bot()

    await pipeline.process_job("company homepage text", _OWNER_CHAT, bot)

    mock_collaborators.create.assert_not_called()
    mock_collaborators.write_cover_letter.assert_not_called()
    mock_collaborators.send_alert.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    assert "doesn't look like a job posting" in bot.send_message.await_args[0][1]


async def test_low_score_logs_without_alert(mock_collaborators):
    mock_collaborators.extract_job_text.return_value = IntakeResult(
        IntakeStatus.OK, text="job text", link=_URL
    )
    mock_collaborators.analyze.return_value = _analysis(_THRESHOLD - 1)
    bot = _make_bot()

    await pipeline.process_job("job text", _OWNER_CHAT, bot)

    mock_collaborators.create.assert_called_once()
    mock_collaborators.write_cover_letter.assert_not_called()
    mock_collaborators.update.assert_not_called()
    mock_collaborators.send_alert.assert_not_awaited()
    bot.send_message.assert_awaited_once()


async def test_threshold_boundary_triggers_alert(mock_collaborators):
    mock_collaborators.extract_job_text.return_value = IntakeResult(
        IntakeStatus.OK, text="job text", link=_URL
    )
    mock_collaborators.analyze.return_value = _analysis(_THRESHOLD)
    bot = _make_bot()

    await pipeline.process_job("job text", _OWNER_CHAT, bot)

    mock_collaborators.write_cover_letter.assert_called_once()
    mock_collaborators.update.assert_called_once()
    sent_fields = mock_collaborators.update.call_args[0][1]
    assert sent_fields["Status"] == crm.STATUS_ALERTED
    assert sent_fields["CoverLetter"] == "Dear..."
    mock_collaborators.send_alert.assert_awaited_once()
    # The alert summary comes solely from send_alert; the pipeline must NOT also
    # send its own trailing summary, or the user gets a duplicate (regression).
    bot.send_message.assert_not_awaited()


async def test_error_is_caught_and_user_gets_reply(mock_collaborators):
    mock_collaborators.extract_job_text.return_value = IntakeResult(
        IntakeStatus.OK, text="job text", link=_URL
    )
    mock_collaborators.analyze.side_effect = RuntimeError("Gemini exploded")
    bot = _make_bot()

    # Must not raise — the handler swallows the error and replies.
    await pipeline.process_job("job text", _OWNER_CHAT, bot)

    bot.send_message.assert_awaited_once()
    assert "went wrong" in bot.send_message.await_args[0][1]


# --- process_job: concurrency gauge ---------------------------------------


async def test_active_jobs_gauge_tracks_concurrency(mock_collaborators):
    # Block intake inside its worker thread so two jobs sit in the pipeline at
    # once, then assert the gauge counts both and returns to zero when they finish.
    release = threading.Event()

    def blocking_extract(_text):
        release.wait(timeout=5)
        return IntakeResult(IntakeStatus.OK, text="job text", link=None)

    mock_collaborators.extract_job_text.side_effect = blocking_extract
    mock_collaborators.analyze.return_value = _analysis(_THRESHOLD - 1)
    bot = _make_bot()

    t1 = asyncio.create_task(pipeline.process_job("a", _OWNER_CHAT, bot))
    t2 = asyncio.create_task(pipeline.process_job("b", _OWNER_CHAT, bot))

    # Both tasks increment the gauge before parking in the blocking extract.
    for _ in range(500):
        if pipeline._active_jobs == 2:
            break
        await asyncio.sleep(0.01)
    assert pipeline._active_jobs == 2

    release.set()
    await asyncio.gather(t1, t2)
    assert pipeline._active_jobs == 0


async def test_job_timeout_abandons_and_restores_gauge(mock_collaborators, monkeypatch):
    # Squeeze the wall-clock cap to near-zero and block intake long enough to
    # overrun it: the job must be abandoned with a user-facing message, the gauge
    # restored to zero, and no analysis attempted. The cap now comes from
    # settings, so stub get_settings to return a near-zero timeout.
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: SimpleNamespace(job_timeout_seconds=0.05)
    )
    release = threading.Event()

    def slow_extract(_text):
        release.wait(timeout=5)
        return IntakeResult(IntakeStatus.OK, text="job text", link=None)

    mock_collaborators.extract_job_text.side_effect = slow_extract
    bot = _make_bot()

    await pipeline.process_job("job text", _OWNER_CHAT, bot)
    release.set()

    bot.send_message.assert_awaited_once()
    assert "gave up" in bot.send_message.await_args[0][1]
    mock_collaborators.analyze.assert_not_called()
    assert pipeline._active_jobs == 0
