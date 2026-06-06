"""Unit tests for crm.create(), crm.find_by_link(), and crm.update().

The pyairtable Table is fully mocked via monkeypatch; no network calls occur.
"""

from unittest.mock import MagicMock

import pytest

from job_application_bot.integrations import crm


@pytest.fixture(autouse=True)
def mock_table(monkeypatch):
    """Replace _get_table with a fresh MagicMock for each test.

    monkeypatch restores the original lru_cached function after each test,
    so the cache is implicitly cleared between runs.
    """
    table = MagicMock()
    monkeypatch.setattr(crm, "_get_table", lambda: table)
    return table


def _analysis(**overrides) -> dict:
    """Return a minimal valid analysis dict, optionally overriding fields."""
    base = {
        "company": "Acme",
        "role": "Backend Engineer",
        "technologies": ["Python", "FastAPI"],
        "years_required": 3,
        "match_score": 88,
        "rationale": "Strong backend fit; gap in Kubernetes.",
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# find_by_link
# ---------------------------------------------------------------------------


def test_find_by_link_returns_none_when_absent(mock_table):
    mock_table.first.return_value = None
    assert crm.find_by_link("https://example.com/job") is None


def test_find_by_link_returns_record_when_found(mock_table):
    record = {
        "id": "recXXX",
        "fields": {"Company": "Acme"},
        "createdTime": "2026-01-01",
    }
    mock_table.first.return_value = record
    assert crm.find_by_link("https://example.com/job") == record


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_with_link_includes_link_field(mock_table):
    mock_table.create.return_value = {
        "id": "rec1",
        "fields": {},
        "createdTime": "2026-01-01",
    }
    crm.create(_analysis(), link="https://example.com/job")

    sent = mock_table.create.call_args[0][0]
    assert sent["Link"] == "https://example.com/job"


def test_create_without_link_omits_link_field(mock_table):
    mock_table.create.return_value = {
        "id": "rec1",
        "fields": {},
        "createdTime": "2026-01-01",
    }
    crm.create(_analysis())

    sent = mock_table.create.call_args[0][0]
    assert "Link" not in sent


def test_create_always_sets_status_new(mock_table):
    mock_table.create.return_value = {
        "id": "rec1",
        "fields": {},
        "createdTime": "2026-01-01",
    }
    crm.create(_analysis())

    sent = mock_table.create.call_args[0][0]
    assert sent["Status"] == crm.STATUS_NEW


def test_create_never_sets_cover_letter(mock_table):
    mock_table.create.return_value = {
        "id": "rec1",
        "fields": {},
        "createdTime": "2026-01-01",
    }
    crm.create(_analysis())

    sent = mock_table.create.call_args[0][0]
    assert "CoverLetter" not in sent


def test_create_joins_technologies_as_csv(mock_table):
    mock_table.create.return_value = {
        "id": "rec1",
        "fields": {},
        "createdTime": "2026-01-01",
    }
    crm.create(_analysis(technologies=["Python", "FastAPI", "Docker"]))

    sent = mock_table.create.call_args[0][0]
    assert sent["Tech"] == "Python, FastAPI, Docker"


def test_create_maps_all_analysis_fields(mock_table):
    mock_table.create.return_value = {
        "id": "rec1",
        "fields": {},
        "createdTime": "2026-01-01",
    }
    a = _analysis()
    crm.create(a)

    sent = mock_table.create.call_args[0][0]
    assert sent["Company"] == a["company"]
    assert sent["Role"] == a["role"]
    assert sent["YearsRequired"] == a["years_required"]
    assert sent["MatchScore"] == a["match_score"]
    assert sent["Rationale"] == a["rationale"]


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_update_delegates_to_table(mock_table):
    mock_table.update.return_value = {
        "id": "recXXX",
        "fields": {"Status": "Alerted"},
        "createdTime": "2026-01-01",
    }
    result = crm.update(
        "recXXX", {"Status": crm.STATUS_ALERTED, "CoverLetter": "Dear Acme..."}
    )

    mock_table.update.assert_called_once_with(
        "recXXX", {"Status": crm.STATUS_ALERTED, "CoverLetter": "Dear Acme..."}
    )
    assert result["fields"]["Status"] == "Alerted"
