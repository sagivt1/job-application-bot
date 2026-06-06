"""Gemini calls plus the project's scoring IP.

This module owns two things:

* **The scoring rubric and analysis system instruction** — the core IP that tells
  Gemini how to extract a job posting's fields and assign a 1-100 CV-match score.
  It lives in ``prompts/analysis_system.md`` and is loaded here.
* **The candidate profile** — ``cv.md`` is loaded once at import time and injected
  into that instruction, so every analysis is scored against the user's real CV.

The Gemini-calling functions build on the constants defined here:
:func:`analyze` (extract + score) and :func:`write_cover_letter` (draft a
recruiter-facing letter). Supporting scaffolding also lives here:
:func:`load_cv`, :data:`CV_PROFILE`, :data:`ANALYSIS_SYSTEM`, and
:data:`_COVER_LETTER_SYSTEM`. The alert threshold is configuration
(``ALERT_THRESHOLD`` / ``config.Settings.alert_threshold``), not a constant here.
"""

import logging
from importlib.resources import files

from job_application_bot import PROJECT_ROOT
from job_application_bot.ai.model import get_model
from job_application_bot.schema import JobAnalysis

logger = logging.getLogger(__name__)

# The alert threshold is configurable via ``ALERT_THRESHOLD`` (see
# ``config.Settings.alert_threshold``, default 80). A job whose match_score is
# at or above it triggers cover-letter generation and a Telegram alert;
# everything below is logged to the CRM only. The pipeline owns the comparison.

# cv.md is user data (gitignored) kept at the repo root rather than shipped inside
# the package, so it is resolved via PROJECT_ROOT — independent of the process
# working directory.
_CV_PATH = PROJECT_ROOT / "cv.md"

# Prompt templates live as package resources under job_application_bot/prompts so
# they can be edited without touching code. Loaded via importlib.resources so they
# resolve regardless of how/where the package is installed.
_PROMPTS = files("job_application_bot.prompts")


def load_cv() -> str:
    """Load the candidate profile from ``cv.md``.

    The CV is the single source of truth for both scoring and cover-letter
    generation. A missing file is a hard startup error, so the underlying
    :class:`FileNotFoundError` is allowed to propagate rather than being masked.

    Returns:
        The full UTF-8 text of ``cv.md``.

    Raises:
        FileNotFoundError: If ``cv.md`` does not exist.
    """
    return _CV_PATH.read_text(encoding="utf-8")


# Loaded once at module import so every analyze() call reuses the same profile
# instead of re-reading the file per request.
CV_PROFILE = load_cv()
logger.debug("loaded CV profile from %s (%d chars)", _CV_PATH, len(CV_PROFILE))


# The scoring rubric + analysis instruction — the project's core IP, kept in
# prompts/analysis_system.md. The ``{{CV}}`` sentinel is replaced (not
# str.format-ed) so stray braces in the CV markdown can never break interpolation.
_ANALYSIS_SYSTEM_TEMPLATE = _PROMPTS.joinpath("analysis_system.md").read_text("utf-8")

# CV injected once at import. Passed as the system instruction by analyze().
ANALYSIS_SYSTEM = _ANALYSIS_SYSTEM_TEMPLATE.replace("{{CV}}", CV_PROFILE)


# The cover-letter system instruction, kept in prompts/cover_letter_system.md.
# Same ``{{CV}}`` sentinel-replace approach as ANALYSIS_SYSTEM so stray braces in
# the CV markdown can never break interpolation.
_COVER_LETTER_SYSTEM_TEMPLATE = _PROMPTS.joinpath("cover_letter_system.md").read_text(
    "utf-8"
)

# CV injected once at import, mirroring ANALYSIS_SYSTEM.
_COVER_LETTER_SYSTEM = _COVER_LETTER_SYSTEM_TEMPLATE.replace("{{CV}}", CV_PROFILE)


def analyze(job_text: str) -> dict:
    """Extract a job posting's fields and score it against the candidate's CV.

    The CV is already baked into :data:`ANALYSIS_SYSTEM`, so the user prompt carries
    only the job text. Gemini returns a structured response matching
    :class:`schema.JobAnalysis`.

    Args:
        job_text: The cleaned text of a single job posting.

    Returns:
        A dict with the keys of :class:`schema.JobAnalysis`: ``company``, ``role``,
        ``technologies``, ``years_required``, ``match_score``, and ``rationale``.

    Raises:
        ValueError: If the model returns an empty or unparseable response.
    """
    prompt = f"Analyze this job posting.\n\n<job>\n{job_text}\n</job>"
    logger.info("analyze: job_text_len=%d", len(job_text))
    return get_model().complete_json(prompt, schema=JobAnalysis, system=ANALYSIS_SYSTEM)


def write_cover_letter(job_text: str, analysis: dict) -> str:
    """Draft a short, recruiter-facing cover letter tailored to a job posting.

    The candidate's CV is baked into :data:`_COVER_LETTER_SYSTEM`; the *analysis*
    dict and raw *job_text* personalize the letter to this specific posting.

    The caller is responsible for only invoking this when
    ``analysis["match_score"]`` is at or above the configured
    ``alert_threshold`` — cover letters are generated solely for strong matches
    to save tokens. This function does not re-check the score itself.

    Args:
        job_text: The cleaned text of the job posting.
        analysis: The dict returned by :func:`analyze`, used to emphasize the right
            strengths (``company``, ``role``, ``rationale``, ``technologies``).

    Returns:
        The cover letter as plain text.

    Raises:
        ValueError: If the model returns an empty response.
    """
    prompt = (
        "Write the cover letter for this application.\n\n"
        f"Company: {analysis.get('company', '')}\n"
        f"Role: {analysis.get('role', '')}\n"
        f"Why this candidate is a strong match (emphasize these strengths): "
        f"{analysis.get('rationale', '')}\n"
        f"Key technologies in the posting: "
        f"{', '.join(analysis.get('technologies', []))}\n\n"
        f"<job>\n{job_text}\n</job>"
    )
    logger.info(
        "write_cover_letter: company=%r role=%r",
        analysis.get("company"),
        analysis.get("role"),
    )
    return get_model().complete_text(prompt, system=_COVER_LETTER_SYSTEM)
