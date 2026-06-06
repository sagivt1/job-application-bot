"""Airtable CRM access: create, read, and update job records."""

import logging
from functools import lru_cache

from pyairtable import Api, Table
from pyairtable.formulas import match

from job_application_bot.config import get_settings

logger = logging.getLogger(__name__)

STATUS_NEW = "New"
STATUS_ALERTED = "Alerted"


@lru_cache(maxsize=1)
def _get_table() -> Table:
    """Lazily initialise and cache the pyairtable Table.

    Called by every public function on first use so that settings (and the
    network) are not touched at import time.

    Returns:
        A pyairtable Table bound to the Airtable base and table from settings.
    """
    s = get_settings()
    logger.debug(
        "Initialising Airtable table: base=%s table=%s",
        s.airtable_base,
        s.airtable_table,
    )
    return Api(s.airtable_token).table(s.airtable_base, s.airtable_table)


def find_by_link(link: str) -> dict | None:
    """Return the first Airtable record whose Link field equals *link*, or None.

    Uses ``pyairtable.formulas.match`` so the value is properly quoted and
    escaped in the Airtable formula string.

    Args:
        link: The job URL to look up.

    Returns:
        The Airtable record dict (with ``id``, ``createdTime``, ``fields`` keys)
        if a match is found, otherwise ``None``.
    """
    logger.debug("CRM dedupe lookup for link: %s", link)
    record = _get_table().first(formula=match({"Link": link}))
    logger.info(
        "CRM dedupe: %s for %s",
        "already logged" if record else "no existing record",
        link,
    )
    return record


def create(analysis: dict, link: str | None = None) -> dict:
    """Write a new job record to Airtable with Status=New.

    ``CoverLetter`` is intentionally omitted here; it is added later via
    :func:`update` once the cover letter has been generated for high-score jobs.
    ``Added`` is an Airtable created-time field set automatically by Airtable.

    Args:
        analysis: Dict returned by ``brain.analyze()`` — must contain
            ``company``, ``role``, ``technologies``, ``years_required``,
            ``match_score``, and ``rationale``.
        link: The job URL. Omit for raw-text pastes (no ``Link`` field is set).

    Returns:
        The newly created Airtable record dict.
    """
    fields: dict = {
        "Company": analysis["company"],
        "Role": analysis["role"],
        "Tech": ", ".join(analysis.get("technologies", [])),
        "YearsRequired": analysis["years_required"],
        "MatchScore": analysis["match_score"],
        "Rationale": analysis["rationale"],
        "Status": STATUS_NEW,
    }
    if link:
        fields["Link"] = link
    logger.info(
        "Airtable create: %s @ %s (score=%s)",
        analysis["role"],
        analysis["company"],
        analysis["match_score"],
    )
    return _get_table().create(fields)


def update(record_id: str, fields: dict) -> dict:
    """Patch an existing Airtable record by ID.

    Used after cover-letter generation to attach the letter and flip the status
    to Alerted: ``crm.update(record["id"], {"CoverLetter": letter, ...})``.

    Args:
        record_id: The Airtable record ID (``rec...`` string).
        fields: Dict of field names → new values to write.

    Returns:
        The updated Airtable record dict.
    """
    logger.info("Airtable update record %s: fields=%s", record_id, list(fields.keys()))
    return _get_table().update(record_id, fields)
